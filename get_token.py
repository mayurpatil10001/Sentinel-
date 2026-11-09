"""
Sentinel Fyers Token Exchange
==============================
Run this script after you get the auth_code from the Fyers login redirect.

Usage:
    python get_token.py <auth_code>

Example:
    python get_token.py eyJ0eXAi...

The auth_code is found in the URL bar of your browser after Fyers login,
in the redirect URL like:
    https://mayurbpatil.in?auth_code=eyJ0...&state=sentinel_auth
                                                                         ^^^^^^^^^^^^
                                                                         COPY THIS PART
"""

import sys
import json
import pathlib
from datetime import date
from fyers_apiv3 import fyersModel

CLIENT_ID  = "S08D2750W7-100"
SECRET_KEY = "O06AUI9GFX"
REDIRECT   = "https://mayurbpatil.in"
TOKEN_FILE = pathlib.Path(".fyers_token.json")

def get_token(auth_code: str) -> str:
    session = fyersModel.SessionModel(
        client_id=CLIENT_ID,
        secret_key=SECRET_KEY,
        redirect_uri=REDIRECT,
        response_type="code",
        grant_type="authorization_code",
    )
    session.set_token(auth_code)
    response = session.generate_token()
    print("Raw response:", response)
    if response.get("s") != "ok" or "access_token" not in response:
        raise RuntimeError(f"Token exchange failed: {response}")
    return response["access_token"]

def save_token(token: str):
    payload = {"access_token": token, "date": date.today().isoformat(), "app_id": CLIENT_ID}
    TOKEN_FILE.write_text(json.dumps(payload, indent=2))
    print(f"\n✅ Token saved to {TOKEN_FILE.absolute()}")

def test_quotes(token: str):
    client = fyersModel.FyersModel(
        client_id=CLIENT_ID, token=token, log_path="logs/", log_level="ERROR"
    )
    # Test profile
    profile = client.get_profile({})
    if profile.get("s") == "ok":
        d = profile.get("data", {})
        print(f"✅ Logged in as: {d.get('name','?')} | FY_ID: {d.get('fy_id','?')}")

    # Test live quotes
    resp = client.quotes({"symbols": "NSE:NIFTY50-INDEX,NSE:RELIANCE-EQ,NSE:TCS-EQ"})
    if resp.get("s") == "ok":
        print("\n📊 Live Quotes:")
        for item in resp.get("d", []):
            q = item.get("v", {})
            sym = item.get("n", "")
            ltp  = q.get("lp", "N/A")
            high = q.get("h", "N/A")
            low  = q.get("l", "N/A")
            vol  = q.get("v", "N/A")
            chg  = q.get("chp", "N/A")
            print(f"   {sym:<30} LTP={ltp}  H={high}  L={low}  Vol={vol}  Chg={chg}%")
    else:
        print("Quote response:", resp)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python get_token.py <auth_code>")
        print("\nTo get the auth_code:")
        print("1. Open this URL in your browser:")
        print(f"   https://api-t1.fyers.in/api/v3/generate-authcode?client_id={CLIENT_ID}&redirect_uri=https%3A%2F%2Ftrade.fyers.in%2Fapi-login%2Fredirect-uri%2Findex.html&response_type=code&state=sentinel_auth")
        print("\n2. Login with your Fyers credentials")
        print("\n3. After login, your browser address bar will show:")
        print("   https://mayurbpatil.in?auth_code=eyJ0...&state=sentinel_auth")
        print("   Copy ONLY the auth_code value (after auth_code= and before &state)")
        print("\n4. Run: python get_token.py <that auth_code>")
        sys.exit(0)

    auth_code = sys.argv[1].strip()
    # If user pasted the full URL, extract auth_code from it
    if "auth_code=" in auth_code:
        auth_code = auth_code.split("auth_code=")[1].split("&")[0]
        print(f"Extracted auth_code from URL: {auth_code[:20]}...")

    print(f"\nExchanging auth_code: {auth_code[:20]}...")
    try:
        token = get_token(auth_code)
        save_token(token)
        print(f"\n✅ Token: {token[:20]}...")
        print("\nTesting live data...\n")
        test_quotes(token)
        print("\n✅ Done! Now restart the server and check the dashboard:")
        print("   http://127.0.0.1:8000/dashboard/")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)

