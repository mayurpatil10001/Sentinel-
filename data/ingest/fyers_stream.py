"""
Fyers Market Data Adapter for Sentinel
========================================
Wraps the Fyers API v3 to provide live market data for the surveillance pipeline.

ARCHITECTURE NOTE
-----------------
Data scope (what this module provides):
  ✅ Live quotes — LTP, bid/ask, volume, OI for any NSE/BSE symbol
  ✅ Market depth — 5-level bid/ask ladder (from depth() API)
  ✅ Option chain snapshots — strikes, IV, OI across expiries
  ✅ Historical OHLCV — minute/day bars for backtesting
  ✅ Account order book — YOUR orders only (via get_orders/orderbook)
  ✅ Positions & holdings — YOUR account only

  ❌ Market-wide order book — unavailable via broker APIs
  ❌ Other participants' orders — exchange/SEBI-level data only
  ❌ OMS-level spoofing detection — only YOUR cancel-history is available

THRESHOLDS
----------
All pattern thresholds are labeled:
  HEURISTIC    — author judgment, not empirically validated
  SEBI CASE    — informed by published SEBI adjudication orders
  EMPIRICAL    — derived from backtested Sentinel case library

USAGE
-----
    from data.ingest.fyers_stream import FyersMarketDataAdapter
    adapter = FyersMarketDataAdapter()
    # Quotes for a list of symbols:
    quotes = adapter.get_quotes(["NSE:RELIANCE-EQ", "NSE:NIFTY50-INDEX"])
    # Market depth:
    depth = adapter.get_market_depth("NSE:RELIANCE-EQ")
    # Option chain:
    chain = adapter.get_option_chain("NSE:NIFTY50-INDEX", strike_count=10)
    # Historical bars:
    history = adapter.get_history("NSE:RELIANCE-EQ", resolution="D", days=30)
"""

import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from data.ingest.errors import BrokerAuthError, BrokerStreamError
from data.ingest.fyers_auth import FyersAuthError, FyersAuthManager
from data.ingest.resilience import CircuitBreaker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# HEURISTIC: Max symbols in a single quotes request (Fyers docs: up to ~50)
MAX_SYMBOLS_PER_QUOTE_CALL = 50

# Default exchange prefix for NSE symbols
NSE_PREFIX = "NSE"
BSE_PREFIX = "BSE"

# Fyers symbol suffix for equity cash segment
EQ_SUFFIX = "-EQ"
INDEX_SUFFIX = "-INDEX"


class FyersMarketDataAdapter:
    """
    Live market data adapter using the Fyers API v3.

    Provides real-time quotes, market depth, option chain, and historical
    OHLCV data for Sentinel's surveillance pipeline.

    Args:
        auth_manager: Optional custom FyersAuthManager. Defaults to global instance.
        log_path: Directory for Fyers SDK logs.
    """

    def __init__(
        self,
        auth_manager: Optional[FyersAuthManager] = None,
        log_path: str = "logs/",
    ):
        self.auth_manager = auth_manager or FyersAuthManager()
        self.log_path = log_path
        self._client = None
        self._client_lock = threading.Lock()
        self._circuit_breaker = CircuitBreaker(
            source_name="fyers_api",
            failure_threshold=5,
            cooldown_seconds=30,
        )

    # ------------------------------------------------------------------
    # Client management
    # ------------------------------------------------------------------
    def _get_client(self):
        """Return an authenticated FyersModel client, refreshing if needed."""
        with self._client_lock:
            if self._client is None:
                try:
                    self._client = self.auth_manager.get_fyers_client(
                        log_path=self.log_path
                    )
                    logger.info("Fyers client initialized successfully")
                except FyersAuthError as exc:
                    raise BrokerAuthError(
                        f"Fyers authentication required: {exc}"
                    ) from exc
            return self._client

    def _call(self, method_name: str, **kwargs) -> Dict[str, Any]:
        """
        Call a Fyers API method with circuit breaker protection.

        Args:
            method_name: Name of the FyersModel method to call.
            **kwargs: Arguments to pass to the method.

        Returns:
            dict: API response payload.

        Raises:
            BrokerAuthError: If authentication fails.
            BrokerStreamError: If the API call fails or circuit is open.
        """
        self._circuit_breaker.before_request()  # raises if OPEN
        try:
            client = self._get_client()
            method = getattr(client, method_name)
            response = method(kwargs)
            if response.get("s") == "error":
                # API returned an error — count as failure for circuit breaker
                self._circuit_breaker.on_failure()
                raise BrokerStreamError(
                    f"Fyers API error ({method_name}): {response.get('message', response)}"
                )
            self._circuit_breaker.on_success()
            return response
        except BrokerAuthError:
            raise
        except BrokerStreamError:
            raise
        except Exception as exc:
            self._circuit_breaker.on_failure()
            raise BrokerStreamError(
                f"Fyers API call failed ({method_name}): {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Live Quotes
    # ------------------------------------------------------------------
    def get_quotes(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """
        Fetch live quotes for a list of Fyers-format symbols.

        Args:
            symbols: List of Fyers symbols e.g. ["NSE:RELIANCE-EQ", "NSE:NIFTY50-INDEX"]
                     Max 50 symbols per call.

        Returns:
            List of quote dicts with fields: symbol, ltp, open, high, low, close,
            volume, bid, ask, oi, prev_close_price, spread, change, chp.

        Raises:
            BrokerStreamError: If the API call fails.
        """
        if not symbols:
            return []

        # Chunk into MAX_SYMBOLS_PER_QUOTE_CALL batches
        results = []
        for i in range(0, len(symbols), MAX_SYMBOLS_PER_QUOTE_CALL):
            chunk = symbols[i : i + MAX_SYMBOLS_PER_QUOTE_CALL]
            symbols_str = ",".join(chunk)
            response = self._call("quotes", symbols=symbols_str)
            quotes_data = response.get("d", [])
            for item in quotes_data:
                q = item.get("v", {})
                q["symbol"] = item.get("n", "")
                results.append(q)

        logger.debug("Fetched %d quotes", len(results))
        return results

    def get_quotes_normalized(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """
        Like get_quotes but returns a normalized, flattened list suitable
        for the Sentinel dashboard and detection pipeline.

        Returns list of dicts with keys:
            symbol, ltp, open_price, high_price, low_price, close_price,
            volume, bid, ask, oi, prev_close, change_pct, spread_pct
        """
        raw = self.get_quotes(symbols)
        normalized = []
        for q in raw:
            ltp = q.get("lp", 0) or 0
            prev = q.get("prev_close_price") or q.get("prev_close") or 0
            bid = q.get("bid", 0) or 0
            ask = q.get("ask", 0) or 0
            spread_pct = ((ask - bid) / ltp * 100) if ltp > 0 else 0
            change_pct = q.get("chp", 0) or 0

            normalized.append(
                {
                    "symbol": q.get("symbol", ""),
                    "ltp": ltp,
                    "open_price": q.get("o", 0),
                    "high_price": q.get("h", 0),
                    "low_price": q.get("l", 0),
                    "close_price": q.get("c", 0),
                    "volume": q.get("v", 0),
                    "bid": bid,
                    "ask": ask,
                    "oi": q.get("oi", 0),
                    "prev_close": prev,
                    "change_pct": change_pct,
                    "spread_pct": round(spread_pct, 4),
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                }
            )
        return normalized

    # ------------------------------------------------------------------
    # Market Depth
    # ------------------------------------------------------------------
    def get_market_depth(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch 5-level market depth (bid/ask ladder) for a symbol.

        Args:
            symbol: Fyers symbol string, e.g. "NSE:RELIANCE-EQ"

        Returns:
            dict with keys: symbol, bids, asks, ltp, volume
            bids/asks are lists of {price, quantity, orders} dicts.

        Raises:
            BrokerStreamError: If the API call fails.
        """
        response = self._call("depth", symbol=symbol, ohlcv_flag=1)
        depth_data = response.get("d", {})
        if isinstance(depth_data, dict):
            depth_data = depth_data.get(symbol, {})

        bids_raw = depth_data.get("bids", [])
        asks_raw = depth_data.get("ask", depth_data.get("asks", []))

        def normalize_level(level):
            if isinstance(level, dict):
                return {
                    "price": level.get("price", 0),
                    "quantity": level.get("quantity", 0),
                    "orders": level.get("ord", level.get("orders", 0)),
                }
            return {"price": 0, "quantity": 0, "orders": 0}

        return {
            "symbol": symbol,
            "bids": [normalize_level(b) for b in bids_raw],
            "asks": [normalize_level(a) for a in asks_raw],
            "ltp": depth_data.get("ltp", 0),
            "volume": depth_data.get("volume", 0),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    # ------------------------------------------------------------------
    # Option Chain
    # ------------------------------------------------------------------
    def get_option_chain(
        self,
        underlying_symbol: str,
        strike_count: int = 10,
    ) -> Dict[str, Any]:
        """
        Fetch the option chain for an underlying symbol.

        Args:
            underlying_symbol: Fyers symbol for the underlying, e.g. "NSE:NIFTY50-INDEX"
            strike_count: Number of strikes on each side of ATM to fetch.

        Returns:
            dict with keys: underlying, expiry, strikes (list of option strike dicts),
            atm_strike, timestamp

        Raises:
            BrokerStreamError: If the API call fails.
        """
        response = self._call(
            "optionchain",
            symbol=underlying_symbol,
            strikecount=strike_count,
        )
        return {
            "underlying": underlying_symbol,
            "raw": response.get("d", {}),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    # ------------------------------------------------------------------
    # Historical OHLCV
    # ------------------------------------------------------------------
    def get_history(
        self,
        symbol: str,
        resolution: str = "D",
        days: int = 30,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch historical OHLCV data for a symbol.

        Args:
            symbol: Fyers symbol, e.g. "NSE:RELIANCE-EQ"
            resolution: "1" "2" "3" "5" "10" "15" "20" "30" "60" "120" "240" "D" "W" "M"
            days: Number of calendar days of history (ignored if from_date/to_date given).
            from_date: Optional start date as "YYYY-MM-DD".
            to_date: Optional end date as "YYYY-MM-DD".

        Returns:
            List of OHLCV dicts with keys: timestamp, open, high, low, close, volume.

        Raises:
            BrokerStreamError: If the API call fails.
        """
        if to_date is None:
            to_date = date.today().isoformat()
        if from_date is None:
            from_date = (date.today() - timedelta(days=days)).isoformat()

        # Fyers expects epoch timestamps
        def to_epoch(d: str) -> int:
            return int(datetime.strptime(d, "%Y-%m-%d").timestamp())

        response = self._call(
            "history",
            symbol=symbol,
            resolution=resolution,
            date_format="0",  # epoch
            range_from=str(to_epoch(from_date)),
            range_to=str(to_epoch(to_date)),
            cont_flag="1",
        )

        candles = response.get("candles", [])
        result = []
        for c in candles:
            if len(c) >= 6:
                result.append(
                    {
                        "timestamp": c[0],
                        "open": c[1],
                        "high": c[2],
                        "low": c[3],
                        "close": c[4],
                        "volume": c[5],
                        "symbol": symbol,
                    }
                )
        return result

    # ------------------------------------------------------------------
    # Account Data (single-account only)
    # ------------------------------------------------------------------
    def get_account_orders(self) -> List[Dict[str, Any]]:
        """
        Fetch your account's order book (TODAY only, single account).

        NOTE: This is YOUR orders only — not market-wide. [ARCHITECTURE LIMIT]

        Returns:
            List of order dicts from Fyers.
        """
        response = self._call("orderbook")
        orders = response.get("orderBook", [])
        logger.debug("Fetched %d account orders", len(orders))
        return orders

    def get_positions(self) -> List[Dict[str, Any]]:
        """
        Fetch your open positions (single account only). [ARCHITECTURE LIMIT]

        Returns:
            List of position dicts from Fyers.
        """
        response = self._call("positions")
        return response.get("netPositions", [])

    def get_holdings(self) -> List[Dict[str, Any]]:
        """
        Fetch your equity holdings (single account only). [ARCHITECTURE LIMIT]

        Returns:
            List of holding dicts from Fyers.
        """
        response = self._call("holdings")
        return response.get("holdings", [])

    # ------------------------------------------------------------------
    # Connection / health
    # ------------------------------------------------------------------
    def is_connected(self) -> bool:
        """Check if Fyers token is valid and API is reachable."""
        if not self.auth_manager.is_token_valid():
            return False
        try:
            client = self._get_client()
            resp = client.get_profile()
            return resp.get("s") == "ok"
        except Exception:
            return False

    def get_connection_status(self) -> Dict[str, Any]:
        """
        Return a status dict for the Sentinel system health endpoint.

        Returns:
            dict with keys: connected, token_valid, token_date, circuit_state
        """
        token_valid = self.auth_manager.is_token_valid()
        circuit_state = self._circuit_breaker.state  # "CLOSED" | "OPEN" | "HALF_OPEN"
        return {
            "source": "Fyers API v3",
            "connected": token_valid,
            "token_valid": token_valid,
            "token_date": self._get_token_date(),
            "circuit_state": circuit_state,
            "circuit_failures": self._circuit_breaker.consecutive_failures,
            # Return only a masked prefix — never the full app_id or account ID
            "app_id_prefix": self.auth_manager.get_token_info().get("app_id_prefix", "***"),
        }

    def _get_token_date(self) -> Optional[str]:
        try:
            import json

            if self.auth_manager.token_file.exists():
                payload = json.loads(self.auth_manager.token_file.read_text())
                return payload.get("date")
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Module-level singleton (lazy-initialized)
# ---------------------------------------------------------------------------
_adapter_instance: Optional[FyersMarketDataAdapter] = None
_adapter_lock = threading.Lock()


def get_fyers_adapter() -> FyersMarketDataAdapter:
    """Return the module-level FyersMarketDataAdapter singleton."""
    global _adapter_instance
    with _adapter_lock:
        if _adapter_instance is None:
            _adapter_instance = FyersMarketDataAdapter()
    return _adapter_instance

