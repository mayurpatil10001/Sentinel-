"""
get_token.py
=============
Sentinel Fyers token exchange helper.

Credentials are read from .env (via python-dotenv) or environment variables.
No credentials are hardcoded in this file.

Usage:
    python get_token.py <auth_code_or_full_redirect_url>

To get an auth_code:
  1. Run:  python get_token.py --url
     This prints the login URL.
  2. Open the URL in your browser and log in with your Fyers credentials.
  3. After login your browser shows:
       https://your-redirect/?auth_code=eyJ0...&state=sentinel_auth
     Copy the auth_code value.
  4. Run:  python get_token.py eyJ0...
"""

import pathlib
import sys

# Load .env before importing anything that reads env vars
try:
    from dotenv import load_dotenv
    load_dotenv(pathlib.Path(__file__).parent / ".env", override=False)
except ImportError:
    pass  # python-dotenv not installed; env vars must be set externally

from data.ingest.fyers_auth import FyersAuthManager, FyersAuthError


def main():
    auth = FyersAuthManager()

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        print("Login URL:")
        try:
            print(" ", auth.generate_auth_url())
        except FyersAuthError as e:
            print(f"  Cannot generate URL: {e}")
        sys.exit(0)

    if sys.argv[1] == "--url":
        try:
            print(auth.generate_auth_url())
        except FyersAuthError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    raw = sys.argv[1].strip()
    # If the user pasted the full redirect URL, extract the auth_code
    if "auth_code=" in raw:
        raw = raw.split("auth_code=")[1].split("&")[0]
        print(f"Extracted auth_code from URL: {raw[:20]}...")

    print(f"Exchanging auth_code: {raw[:20]}...")
    try:
        token = auth.exchange_code_for_token(raw)
        print(f"✅ Token saved. First 20 chars: {token[:20]}...")
        print("\nNow test live data:")
        _test_connection(auth)
        print("\n✅ Done! Restart the server:")
        print("   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload")
    except FyersAuthError as e:
        print(f"❌ Auth error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


def _test_connection(auth: FyersAuthManager):
    """Quick connection test — shows profile name only, not account IDs."""
    try:
        client = auth.get_fyers_client()
        profile = client.get_profile()
        if isinstance(profile, dict) and profile.get("s") == "ok":
            name = profile.get("data", {}).get("name", "unknown")
            print(f"   Connected as: {name}")
        else:
            print(f"   Profile response: {profile}")
    except Exception as e:
        print(f"   Connection test failed: {e}")


if __name__ == "__main__":
    main()
