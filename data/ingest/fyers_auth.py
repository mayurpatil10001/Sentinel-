"""
Fyers API v3 Authentication Helper
====================================
Manages the OAuth2 token lifecycle for the Fyers broker API.

ARCHITECTURE NOTE
-----------------
Fyers provides single-account visibility only:
  - Live intraday quotes for any NSE/BSE symbol  ← NEW via Fyers
  - Your account's orders, positions, holdings   ← Account-level only
  - Market depth / best bid-ask (5 levels)       ← NEW via Fyers
  - Option chain snapshots                       ← NEW via Fyers
  - Historical OHLCV (day/intraday)              ← Supplementary to NSE bhavcopy

It does NOT provide:
  - Other participants' orders (market-wide order book)
  - Exchange surveillance feeds (SEBI-only access)

TOKEN REFRESH
-------------
Fyers access tokens expire daily. You must run auth flow once per trading day
(before 09:00 IST) using `generate_auth_url()` → browser → `exchange_code_for_token()`.

USAGE
-----
    from data.ingest.fyers_auth import FyersAuthManager
    auth = FyersAuthManager()
    # First time / daily refresh:
    url = auth.generate_auth_url()
    # Open URL in browser, login, copy the full redirect URL
    token = auth.exchange_code_for_token(auth_code="<paste code from redirect URL>")
    # Subsequent calls:
    fyers_client = auth.get_fyers_client()
"""

import hashlib
import json
import logging
import os
from datetime import date, datetime
from pathlib import Path

from fyers_apiv3 import fyersModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — read from environment variables first, fall back to defaults
# ---------------------------------------------------------------------------
APP_ID = os.environ.get("FYERS_APP_ID", "S08D2750W7-100")
SECRET_ID = os.environ.get("FYERS_SECRET_ID", "O06AUI9GFX")
REDIRECT_URI = os.environ.get(
    "FYERS_REDIRECT_URI",
    "https://trade.fyers.in/api-login/redirect-uri/index.html",
)

# Token stored locally (never committed — add to .gitignore)
_TOKEN_FILE = Path(__file__).parent.parent.parent / ".fyers_token.json"


class FyersAuthError(Exception):
    """Raised when Fyers authentication fails or token is missing/expired."""


class FyersAuthManager:
    """Manages Fyers OAuth2 token generation and storage."""

    def __init__(
        self,
        app_id: str = APP_ID,
        secret_id: str = SECRET_ID,
        redirect_uri: str = REDIRECT_URI,
        token_file: Path = _TOKEN_FILE,
    ):
        self.app_id = app_id
        self.secret_id = secret_id
        self.redirect_uri = redirect_uri
        self.token_file = token_file

    # ------------------------------------------------------------------
    # Auth URL generation
    # ------------------------------------------------------------------
    def generate_auth_url(self) -> str:
        """
        Generate the Fyers OAuth2 login URL.

        Returns:
            str: URL to open in a browser for user login.

        Steps after getting the URL:
            1. Open URL in browser.
            2. Login with your Fyers credentials.
            3. After redirect, copy the `auth_code` query parameter from the URL.
            4. Pass it to `exchange_code_for_token(auth_code=...)`.
        """
        session = fyersModel.SessionModel(
            client_id=self.app_id,
            redirect_uri=self.redirect_uri,
            response_type="code",
            state="sentinel_auth",
            secret_key=self.secret_id,
            grant_type="authorization_code",
        )
        auth_url: str = session.generate_authcode()
        logger.info("Fyers auth URL generated. Open in browser to authenticate.")
        return auth_url

    # ------------------------------------------------------------------
    # Token exchange
    # ------------------------------------------------------------------
    def exchange_code_for_token(self, auth_code: str) -> str:
        """
        Exchange an auth code (from the redirect URL) for an access token.

        Args:
            auth_code: The `auth_code` query parameter from the Fyers redirect URL.

        Returns:
            str: The access token string.

        Raises:
            FyersAuthError: If the token exchange fails.
        """
        session = fyersModel.SessionModel(
            client_id=self.app_id,
            redirect_uri=self.redirect_uri,
            response_type="code",
            state="sentinel_auth",
            secret_key=self.secret_id,
            grant_type="authorization_code",
        )
        session.set_token(auth_code)
        response = session.generate_token()
        if response.get("s") != "ok" or "access_token" not in response:
            raise FyersAuthError(
                f"Token exchange failed: {response.get('message', response)}"
            )
        access_token: str = response["access_token"]
        self._save_token(access_token)
        logger.info("Fyers access token obtained and saved to %s", self.token_file)
        return access_token

    # ------------------------------------------------------------------
    # Token persistence
    # ------------------------------------------------------------------
    def _save_token(self, access_token: str) -> None:
        payload = {
            "access_token": access_token,
            "date": date.today().isoformat(),
            "app_id": self.app_id,
        }
        self.token_file.write_text(json.dumps(payload, indent=2))
        logger.debug("Token saved to %s", self.token_file)

    def load_token(self) -> str:
        """
        Load a previously saved access token.

        Returns:
            str: The saved access token.

        Raises:
            FyersAuthError: If token file is missing or stale (not today's date).
        """
        if not self.token_file.exists():
            raise FyersAuthError(
                f"No token file found at {self.token_file}. "
                "Run generate_auth_url() and exchange_code_for_token() first."
            )
        try:
            payload = json.loads(self.token_file.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise FyersAuthError(f"Cannot read token file: {exc}") from exc

        token_date = payload.get("date", "")
        today = date.today().isoformat()
        if token_date != today:
            raise FyersAuthError(
                f"Fyers token is stale (dated {token_date}, today is {today}). "
                "Re-authenticate: run generate_auth_url() and exchange_code_for_token()."
            )
        return payload["access_token"]

    def is_token_valid(self) -> bool:
        """Return True if a fresh (today's) token exists."""
        try:
            self.load_token()
            return True
        except FyersAuthError:
            return False

    # ------------------------------------------------------------------
    # FyersModel client
    # ------------------------------------------------------------------
    def get_fyers_client(self, log_path: str = "logs/") -> fyersModel.FyersModel:
        """
        Return an authenticated FyersModel client using the stored token.

        Args:
            log_path: Directory for Fyers SDK logs.

        Returns:
            fyersModel.FyersModel: Authenticated client ready for API calls.

        Raises:
            FyersAuthError: If token is missing or stale.
        """
        access_token = self.load_token()
        client = fyersModel.FyersModel(
            client_id=self.app_id,
            token=access_token,
            log_path=log_path,
            log_level="ERROR",
        )
        return client


# ---------------------------------------------------------------------------
# CLI helper — run this file directly to perform auth flow interactively
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    auth = FyersAuthManager()
    print("\n=== Sentinel × Fyers Authentication ===\n")

    if "--check" in sys.argv:
        if auth.is_token_valid():
            print("✅ Token is valid for today.")
        else:
            print("❌ Token missing or stale. Re-run without --check to authenticate.")
        sys.exit(0)

    url = auth.generate_auth_url()
    print(f"1. Open this URL in your browser:\n\n   {url}\n")
    print("2. Login with your Fyers credentials and approve access.")
    print("3. After redirect, copy the FULL redirect URL and paste below.\n")
    redirect_url = input("Paste redirect URL here: ").strip()

    # Extract auth_code from redirect URL
    if "auth_code=" in redirect_url:
        auth_code = redirect_url.split("auth_code=")[1].split("&")[0]
    elif "code=" in redirect_url:
        auth_code = redirect_url.split("code=")[1].split("&")[0]
    else:
        auth_code = redirect_url  # Maybe they pasted just the code

    try:
        token = auth.exchange_code_for_token(auth_code)
        print(f"\n✅ Token obtained and saved to {auth.token_file}")
        print("   The Sentinel Fyers adapter will use this token automatically.")
    except FyersAuthError as e:
        print(f"\n❌ Authentication failed: {e}")
        sys.exit(1)

