"""
app/api/live_market.py
======================
Live market data endpoints powered by the Fyers API v3.

Routes
------
GET  /api/live/status           — Fyers connection health + token validity
GET  /api/live/quotes           — Live LTP + OHLCV for a list of symbols
GET  /api/live/depth/{symbol}   — 5-level market depth for one symbol
GET  /api/live/option-chain     — Option chain snapshot for an underlying
GET  /api/live/history          — Historical OHLCV bars
GET  /api/live/watchlist        — Default Sentinel watchlist with live prices
POST /api/live/authenticate     — Step 1: return Fyers auth URL for browser login
POST /api/live/token            — Step 2: exchange auth_code for access token

NOTE: All live endpoints return 503 with actionable message if Fyers token
      is not yet obtained for today. Use /api/live/authenticate → /api/live/token
      to complete the OAuth flow.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from data.ingest.errors import BrokerAuthError, BrokerStreamError
from data.ingest.fyers_auth import FyersAuthManager, FyersAuthError
from data.ingest.fyers_stream import get_fyers_adapter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/live", tags=["live-market"])

# Default Sentinel watchlist — covers key NSE instruments for surveillance
DEFAULT_WATCHLIST = [
    "NSE:NIFTY50-INDEX",
    "NSE:NIFTYBANK-INDEX",
    "NSE:RELIANCE-EQ",
    "NSE:TCS-EQ",
    "NSE:HDFCBANK-EQ",
    "NSE:INFY-EQ",
    "NSE:ICICIBANK-EQ",
    "NSE:AXISBANK-EQ",
    "NSE:KOTAKBANK-EQ",
    "NSE:SBIN-EQ",
    "NSE:WIPRO-EQ",
    "NSE:LT-EQ",
    "NSE:BAJFINANCE-EQ",
    "NSE:MARUTI-EQ",
    "NSE:TATASTEEL-EQ",
]


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------
def _require_connection():
    """Raise 503 if Fyers token is not valid for today."""
    adapter = get_fyers_adapter()
    status = adapter.get_connection_status()
    if not status["token_valid"]:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "fyers_not_authenticated",
                "message": (
                    "Fyers API token is missing or expired. "
                    "Call POST /api/live/authenticate to get the login URL, "
                    "open it in your browser, then call POST /api/live/token "
                    "with the auth_code from the redirect URL."
                ),
                "authenticate_endpoint": "/api/live/authenticate",
                "token_endpoint": "/api/live/token",
            },
        )
    return adapter


# ---------------------------------------------------------------------------
# Auth flow endpoints
# ---------------------------------------------------------------------------
class TokenRequest(BaseModel):
    auth_code: str


@router.post("/authenticate", summary="Generate Fyers login URL (Step 1 of auth)")
def generate_auth_url():
    """
    Returns the Fyers OAuth2 login URL.

    **Flow:**
    1. Call this endpoint → get `auth_url`
    2. Open `auth_url` in a browser and login with Fyers credentials
    3. After redirect, copy the `auth_code` from the redirect URL query string
    4. Call `POST /api/live/token` with `{"auth_code": "<code>"}`
    """
    try:
        auth = FyersAuthManager()
        url = auth.generate_auth_url()
        return {
            "auth_url": url,
            "instructions": (
                "Open auth_url in your browser. After Fyers login and redirect, "
                "copy the auth_code query parameter from the redirect URL and "
                "POST it to /api/live/token."
            ),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/token", summary="Exchange auth_code for access token (Step 2 of auth)")
def exchange_token(body: TokenRequest):
    """
    Exchange the Fyers auth_code for a session access token.

    After this, all `/api/live/*` endpoints will return real data until midnight.
    You must re-authenticate daily.
    """
    try:
        auth = FyersAuthManager()
        token = auth.exchange_code_for_token(body.auth_code)
        # Invalidate the cached client so it picks up the new token
        from data.ingest import fyers_stream as _fs
        import threading
        with _fs._adapter_lock:
            _fs._adapter_instance = None
        return {
            "status": "ok",
            "message": "Fyers access token obtained and stored. Live data endpoints are now active.",
            "token_preview": token[:8] + "..." if len(token) > 8 else token,
        }
    except FyersAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
@router.get("/status", summary="Fyers connection health")
def get_live_status():
    """
    Returns Fyers API connection status and token validity.

    Does NOT require authentication — always safe to call.
    """
    adapter = get_fyers_adapter()
    status = adapter.get_connection_status()
    return {
        **status,
        "default_watchlist": DEFAULT_WATCHLIST,
        "auth_instructions": (
            "If token_valid is false, call POST /api/live/authenticate "
            "then POST /api/live/token with the auth_code."
        ),
    }


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------
@router.get("/quotes", summary="Live quotes for a list of symbols")
def get_live_quotes(
    symbols: Optional[str] = Query(
        None,
        description="Comma-separated Fyers symbols e.g. NSE:RELIANCE-EQ,NSE:TCS-EQ",
    )
):
    """
    Fetch real-time LTP, OHLCV, bid/ask, volume and OI for Fyers symbols.

    Returns normalized quote objects ready for the Sentinel dashboard.
    If `symbols` is omitted, returns quotes for the default Sentinel watchlist.
    """
    adapter = _require_connection()

    symbol_list = (
        [s.strip() for s in symbols.split(",") if s.strip()]
        if symbols
        else DEFAULT_WATCHLIST
    )

    try:
        quotes = adapter.get_quotes_normalized(symbol_list)
        return {
            "status": "ok",
            "count": len(quotes),
            "quotes": quotes,
            "source": "Fyers API v3",
        }
    except BrokerAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except BrokerStreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ---------------------------------------------------------------------------
# Market Depth
# ---------------------------------------------------------------------------
@router.get("/depth/{symbol:path}", summary="5-level market depth for one symbol")
def get_market_depth(symbol: str):
    """
    Returns the 5-level bid/ask ladder for a Fyers symbol.

    Example: `/api/live/depth/NSE:RELIANCE-EQ`
    """
    adapter = _require_connection()
    try:
        depth = adapter.get_market_depth(symbol)
        return {"status": "ok", "depth": depth, "source": "Fyers API v3"}
    except BrokerAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except BrokerStreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ---------------------------------------------------------------------------
# Option Chain
# ---------------------------------------------------------------------------
@router.get("/option-chain", summary="Option chain snapshot")
def get_option_chain(
    underlying: str = Query(
        "NSE:NIFTY50-INDEX",
        description="Fyers underlying symbol e.g. NSE:NIFTY50-INDEX",
    ),
    strikes: int = Query(10, description="Number of strikes each side of ATM"),
):
    """
    Returns the option chain (CE + PE) around ATM for the given underlying.
    """
    adapter = _require_connection()
    try:
        chain = adapter.get_option_chain(underlying, strike_count=strikes)
        return {"status": "ok", "option_chain": chain, "source": "Fyers API v3"}
    except BrokerAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except BrokerStreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ---------------------------------------------------------------------------
# Historical OHLCV
# ---------------------------------------------------------------------------
@router.get("/history", summary="Historical OHLCV bars")
def get_history(
    symbol: str = Query(..., description="Fyers symbol e.g. NSE:RELIANCE-EQ"),
    resolution: str = Query("D", description="Bar resolution: 1 5 15 60 D W M"),
    days: int = Query(30, description="Number of calendar days of history"),
    from_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
):
    """
    Fetches historical OHLCV candles for a symbol.
    Use for backtesting ingest, chart rendering, or technical analysis.
    """
    adapter = _require_connection()
    try:
        bars = adapter.get_history(
            symbol=symbol,
            resolution=resolution,
            days=days,
            from_date=from_date,
            to_date=to_date,
        )
        return {
            "status": "ok",
            "symbol": symbol,
            "resolution": resolution,
            "count": len(bars),
            "bars": bars,
            "source": "Fyers API v3",
        }
    except BrokerAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except BrokerStreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ---------------------------------------------------------------------------
# Watchlist (enriched)
# ---------------------------------------------------------------------------
@router.get("/watchlist", summary="Default Sentinel watchlist with live prices")
def get_watchlist():
    """
    Returns live quotes for all instruments in the Sentinel default watchlist.
    Identical to `GET /api/live/quotes` without the symbols parameter.

    Dashboard calls this endpoint to populate the left-side watchlist panel.
    If Fyers is not authenticated, returns the watchlist structure with
    null prices rather than 503, so the dashboard can show placeholder rows.
    """
    adapter = get_fyers_adapter()
    status = adapter.get_connection_status()

    if not status["token_valid"]:
        # Return placeholder structure so dashboard doesn't crash
        placeholders = [
            {
                "symbol": s,
                "ltp": None,
                "change_pct": None,
                "volume": None,
                "oi": None,
                "source": "not_connected",
            }
            for s in DEFAULT_WATCHLIST
        ]
        return {
            "status": "not_connected",
            "message": "Fyers not authenticated. Call POST /api/live/authenticate to connect.",
            "count": len(placeholders),
            "quotes": placeholders,
        }

    try:
        quotes = adapter.get_quotes_normalized(DEFAULT_WATCHLIST)
        return {
            "status": "ok",
            "count": len(quotes),
            "quotes": quotes,
            "source": "Fyers API v3",
        }
    except (BrokerAuthError, BrokerStreamError) as exc:
        return {
            "status": "error",
            "message": str(exc),
            "count": 0,
            "quotes": [],
        }
