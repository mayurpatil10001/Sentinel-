"""
data/ingest/fyers_auth.py
==========================
Fyers API v3 Authentication Helper.

CREDENTIAL LOADING
------------------
All credentials are read exclusively from environment variables or a .env
file at the repo root. There are NO hardcoded fallback values. If a required
variable is missing, FyersAuthError is raised at the first API call so the
failure is explicit and logged, rather than silently using a stale value.

Required env vars:
  FYERS_APP_ID       — your Fyers API app ID (from myapi.fyers.in)
  FYERS_SECRET_ID    — your Fyers API secret (rotate immediately if leaked)
  FYERS_REDIRECT_URI — the redirect URI registered in your Fyers app

TOKEN REFRESH
-------------
Fyers access tokens expire daily. Run auth flow once per trading day
(before 09:00 IST): generate_auth_url() → browser → exchange_code_for_token().
"""

import hashlib
import json
import logging
import os
from datetime import date, datetime
from pathlib import Path

from fyers_apiv3 import fyersModel

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent / ".env", override=False)
except ImportError:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — read from environment ONLY. No hardcoded fallbacks.
# ---------------------------------------------------------------------------
# python-dotenv is loaded by app/main.py at startup. For scripts run outside
# the FastAPI context, callers should load_dotenv() themselves.

def _require_env(name: str) -> str:
    """Return env var value or raise FyersAuthError if missing."""
    val = os.environ.get(name, "").strip()
    if not val:
        raise FyersAuthError(
            f"Environment variable {name!r} is not set. "
            f"Copy .env.example to .env and fill in your Fyers credentials. "
            f"See README for setup instructions."
        )
    return val


# Token file location
# ---------------------------------------------------------------------------
# The token is stored as plain JSON (NOT encrypted).
# Default location: ~/.sentinel/fyers_token.json  (outside the repo).
# Override with the SENTINEL_TOKEN_PATH environment variable.
#
# The file is gitignored under every name it can have (.fyers_token.json,
# data/fyers_token.json, **/fyers_token.json). Do not place it inside
# the repository. The file is chmod 0600 on POSIX systems; on Windows a
# best-effort icacls restriction is applied.
_DEFAULT_TOKEN_FILE = Path.home() / ".sentinel" / "fyers_token.json"
_TOKEN_FILE = Path(
    os.environ.get("SENTINEL_TOKEN_PATH", str(_DEFAULT_TOKEN_FILE))
)


class FyersAuthError(Exception):
    """Raised when Fyers authentication fails or token is missing/expired."""


class FyersAuthManager:
    """Manages Fyers OAuth2 token generation and storage."""

    def __init__(
        self,
        app_id: str | None = None,
        secret_id: str | None = None,
        redirect_uri: str | None = None,
        token_file: Path = _TOKEN_FILE,
    ):
        # Credentials: use constructor args (for testing) or env vars
        self._app_id       = app_id       or _require_env("FYERS_APP_ID")
        self._secret_id    = secret_id    or _require_env("FYERS_SECRET_ID")
        self._redirect_uri = redirect_uri or _require_env("FYERS_REDIRECT_URI")
        self._token_file   = token_file

    # ------------------------------------------------------------------
    # Auth URL generation
    # ------------------------------------------------------------------
    def generate_auth_url(self) -> str:
        """Return the Fyers OAuth2 login URL for the user to open in a browser."""
        from urllib.parse import quote
        base = "https://api-t1.fyers.in/api/v3/generate-authcode"
        return (
            f"{base}?client_id={self._app_id}"
            f"&redirect_uri={quote(self._redirect_uri, safe='')}"
            f"&response_type=code&state=sentinel_auth"
        )

    # ------------------------------------------------------------------
    # Token exchange
    # ------------------------------------------------------------------
    def exchange_code_for_token(self, auth_code: str) -> str:
        """Exchange the OAuth2 auth code for an access token and persist it."""
        auth_code = auth_code.strip()
        if "auth_code=" in auth_code:
            auth_code = auth_code.split("auth_code=")[1].split("&")[0]

        session = fyersModel.SessionModel(
            client_id=self._app_id,
            secret_key=self._secret_id,
            redirect_uri=self._redirect_uri,
            response_type="code",
            grant_type="authorization_code",
        )
        session.set_token(auth_code)
        response = session.generate_token()
        if response.get("s") != "ok" or "access_token" not in response:
            raise FyersAuthError(f"Token exchange failed: {response}")
        token = response["access_token"]
        self._save_token(token)
        logger.info("Fyers token exchanged and saved successfully.")
        return token

    # ------------------------------------------------------------------
    # Token persistence
    # ------------------------------------------------------------------
    def _save_token(self, access_token: str) -> None:
        payload = {
            "access_token": access_token,
            "date": date.today().isoformat(),
            # Store app_id so we can detect token/app_id mismatch on load
            "app_id": self._app_id,
        }
        self._token_file.parent.mkdir(parents=True, exist_ok=True)
        self._token_file.write_text(json.dumps(payload, indent=2))
        # Restrict file permissions: owner read/write only.
        # On POSIX this sets mode 0600. On Windows we attempt icacls; failure
        # is logged but not fatal (Windows ACLs are managed differently).
        try:
            import stat
            self._token_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except (AttributeError, NotImplementedError):
            # Windows: attempt icacls as best-effort
            try:
                import subprocess, getpass
                subprocess.run(
                    ["icacls", str(self._token_file), "/inheritance:r",
                     "/grant:r", f"{getpass.getuser()}:(R,W)"],
                    capture_output=True, check=False
                )
            except Exception:
                pass
        logger.debug("Token saved (plain JSON, NOT encrypted) to %s", self._token_file)

    def _load_token(self) -> dict | None:
        if not self._token_file.exists():
            return None
        try:
            data = json.loads(self._token_file.read_text())
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load token file: %s", e)
            return None

    def is_token_valid(self) -> bool:
        """Return True if a token exists and was issued today."""
        data = self._load_token()
        if not data:
            return False
        try:
            token_date = date.fromisoformat(data["date"])
            return token_date == date.today()
        except (KeyError, ValueError):
            return False

    def get_access_token(self) -> str:
        """Return the access token or raise FyersAuthError."""
        data = self._load_token()
        if not data:
            raise FyersAuthError(
                "No Fyers token found. Run the auth flow: "
                "open the login URL in a browser, then run get_token.py."
            )
        token_date = data.get("date", "")
        if token_date != date.today().isoformat():
            raise FyersAuthError(
                f"Fyers token is from {token_date}, not today ({date.today()}). "
                "Re-run the auth flow to get a fresh token."
            )
        return data["access_token"]

    def get_fyers_client(self, log_path: str = "logs/") -> fyersModel.FyersModel:
        """Return an authenticated FyersModel client."""
        import pathlib
        pathlib.Path(log_path).mkdir(exist_ok=True)
        token = self.get_access_token()
        return fyersModel.FyersModel(
            client_id=self._app_id,
            token=token,
            log_path=log_path,
            log_level="ERROR",
        )

    def get_token_info(self) -> dict:
        """Return token metadata for the /api/live/status endpoint."""
        data = self._load_token()
        if not data:
            return {"token_valid": False, "token_date": None, "app_id_prefix": None}
        today = date.today().isoformat()
        app_id = data.get("app_id", "")
        return {
            "token_valid": data.get("date") == today,
            "token_date": data.get("date"),
            # Only expose a masked prefix, never the full app_id
            "app_id_prefix": app_id[:6] + "***" if len(app_id) >= 6 else "***",
        }

    # ------------------------------------------------------------------
    # Legacy alias kept for backward compatibility
    # ------------------------------------------------------------------
    def check_token(self) -> bool:
        return self.is_token_valid()
