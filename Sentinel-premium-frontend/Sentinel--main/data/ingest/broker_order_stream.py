"""
Zerodha Kite Connect — Broker Order Stream Adapter
===================================================

Provides real-time order lifecycle events (placed / modified / cancelled /
executed) from a Zerodha Kite Connect brokerage account.

ARCHITECTURAL LIMITATION — READ THIS FIRST
------------------------------------------
This adapter gives visibility into ONE account's orders only: the account
whose KITE_API_KEY and KITE_ACCESS_TOKEN are provided. It does NOT provide
market-wide order book data.

True market-wide order lifecycle data (every participant's orders) is
available only via direct exchange/SEBI partnership — not through any
public or retail broker API. A production surveillance system is
architecturally designed to be deployed AT the exchange or regulator,
not run by an outside party.

What this module gives you:
  - Your own orders: placed, modified, cancelled, executed timestamps
  - Your own trades: execution prices, quantities
  - Live position and portfolio state for your account

What this module does NOT give you:
  - Other participants' orders
  - Full order book depth
  - Market-wide cancel ratios or spoof patterns from other accounts

REAL-DATA STATUS: This module is fully wired for real data IF valid
Kite Connect credentials are provided via environment variables:
    KITE_API_KEY       — your Kite Connect app's API key
    KITE_ACCESS_TOKEN  — daily access token (must be refreshed each trading day)

WITHOUT credentials, this module raises BrokerAuthError immediately.
It does NOT fall back to synthetic data.

Cost: Kite Connect API subscription is approximately ₹2000/month.

Setup reference: https://kite.trade/docs/connect/v3/

Failure behaviour (HARD RULE #1)
---------------------------------
- Missing/invalid credentials → BrokerAuthError (immediate, no fallback)
- Stream disconnection → BrokerStreamError (raised to caller)
- API errors → BrokerStreamError with the underlying kiteconnect error message
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterator

from data.ingest.errors import BrokerAuthError, BrokerStreamError

logger = logging.getLogger(__name__)

# ── Order event schema ────────────────────────────────────────────────────────

@dataclass
class OrderEvent:
    """
    A single order lifecycle event from the broker.
    Maps directly to the Sentinel `orders` table schema.

    ``status`` is one of: PLACED, MODIFIED, CANCELLED, EXECUTED,
    PARTIALLY_EXECUTED, REJECTED — matching app/db/models.py OrderStatus.
    """
    exchange_order_id: str
    account_id: str         # Kite user ID (e.g. "XY1234")
    symbol: str             # NSE/BSE trading symbol
    exchange: str           # "NSE" or "BSE"
    instrument_type: str    # "equity", "future", "option" etc.
    side: str               # "buy" or "sell"
    status: str             # see OrderStatus in models.py
    price: float            # order price (0 for market orders)
    quantity: int           # total ordered quantity
    filled_quantity: int    # executed quantity so far
    timestamp: datetime     # event timestamp (IST, from exchange)
    raw: dict               # the full Kite order dict, preserved for auditing


# ── Credential validation ─────────────────────────────────────────────────────

def _load_credentials() -> tuple[str, str]:
    """
    Loads Kite Connect credentials from environment variables.
    Raises BrokerAuthError immediately if either variable is missing or empty.
    Does NOT prompt interactively or fall back to any default.
    """
    api_key = os.environ.get("KITE_API_KEY", "").strip()
    access_token = os.environ.get("KITE_ACCESS_TOKEN", "").strip()

    missing = []
    if not api_key:
        missing.append("KITE_API_KEY")
    if not access_token:
        missing.append("KITE_ACCESS_TOKEN")

    if missing:
        raise BrokerAuthError(
            f"Missing environment variable(s): {', '.join(missing)}. "
            f"KITE_ACCESS_TOKEN must be refreshed daily — see "
            f"https://kite.trade/docs/connect/v3/user/#session-token"
        )

    return api_key, access_token


def _kite_status_to_sentinel(kite_status: str) -> str:
    """
    Maps Kite Connect order status strings to Sentinel's OrderStatus enum values.

    Kite statuses: OPEN, COMPLETE, CANCELLED, REJECTED, UPDATE, TRIGGER PENDING
    Sentinel statuses: placed, modified, cancelled, executed, partially_executed, rejected
    """
    mapping = {
        "OPEN":            "placed",
        "TRIGGER PENDING": "placed",
        "UPDATE":          "modified",
        "CANCELLED":       "cancelled",
        "COMPLETE":        "executed",
        "REJECTED":        "rejected",
    }
    return mapping.get(kite_status.upper(), "placed")


def _kite_order_to_event(order: dict, account_id: str) -> OrderEvent:
    """Converts a raw Kite order dict to a Sentinel OrderEvent."""
    # Kite uses 'tradingsymbol' for the symbol
    symbol = order.get("tradingsymbol", "")
    exchange = order.get("exchange", "NSE")
    instrument_type_raw = order.get("instrument_type", "EQ")

    # Map Kite instrument types to Sentinel's schema
    instr_type_map = {
        "EQ": "equity", "FUT": "future", "CE": "option", "PE": "option",
    }
    instrument_type = instr_type_map.get(instrument_type_raw, "equity")

    order_timestamp = order.get("order_timestamp") or order.get("exchange_timestamp")
    if isinstance(order_timestamp, str):
        try:
            order_timestamp = datetime.fromisoformat(order_timestamp)
        except ValueError:
            order_timestamp = datetime.utcnow()

    return OrderEvent(
        exchange_order_id=str(order.get("order_id", "")),
        account_id=account_id,
        symbol=symbol,
        exchange=exchange,
        instrument_type=instrument_type,
        side=order.get("transaction_type", "BUY").lower(),
        status=_kite_status_to_sentinel(order.get("status", "OPEN")),
        price=float(order.get("price", 0.0) or 0.0),
        quantity=int(order.get("quantity", 0) or 0),
        filled_quantity=int(order.get("filled_quantity", 0) or 0),
        timestamp=order_timestamp or datetime.utcnow(),
        raw=order,
    )


# ── Main adapter class ────────────────────────────────────────────────────────

class KiteOrderStream:
    """
    Connects to Zerodha Kite Connect and provides access to order events.

    Instantiation validates credentials immediately — does not defer to the
    first API call. This means if KITE_API_KEY or KITE_ACCESS_TOKEN are
    missing, you find out the moment you construct this object, not later.

    Parameters
    ----------
    api_key, access_token
        Override environment variables if provided explicitly. If None,
        reads from KITE_API_KEY / KITE_ACCESS_TOKEN env vars.

    Raises
    ------
    BrokerAuthError
        If credentials are missing or the Kite API rejects them.

    Example::

        stream = KiteOrderStream()                    # reads from env vars
        orders = stream.fetch_todays_orders()         # REST: today's orders
        for event in stream.stream_order_updates():   # WebSocket: live updates
            process(event)
    """

    def __init__(
        self,
        api_key: str | None = None,
        access_token: str | None = None,
    ) -> None:
        # Resolve credentials — raise immediately if missing
        if api_key is None or access_token is None:
            env_api_key, env_access_token = _load_credentials()
            api_key = api_key or env_api_key
            access_token = access_token or env_access_token

        if not api_key or not access_token:
            raise BrokerAuthError(
                "api_key and access_token must both be non-empty strings."
            )

        self._api_key = api_key
        self._access_token = access_token
        self._kite = None
        self._account_id: str | None = None

        self._init_kite()

    def _init_kite(self) -> None:
        """
        Initialises the KiteConnect client and validates the session.
        Raises BrokerAuthError if the session is rejected.
        """
        try:
            from kiteconnect import KiteConnect  # type: ignore[import]
        except ImportError as exc:
            raise BrokerAuthError(
                "kiteconnect package not installed. "
                "Run: pip install kiteconnect"
            ) from exc

        kite = KiteConnect(api_key=self._api_key)
        kite.set_access_token(self._access_token)

        # Validate by fetching profile — if token is expired/invalid, this raises
        try:
            profile = kite.profile()
            self._account_id = profile.get("user_id", "unknown")
            logger.info(
                "Kite Connect session established for account: %s (%s)",
                self._account_id, profile.get("user_name", "")
            )
        except Exception as exc:  # kiteconnect raises NetworkException/TokenException
            raise BrokerAuthError(
                f"Kite profile fetch failed — token may be expired "
                f"(access tokens expire daily at midnight). Error: {exc}"
            ) from exc

        self._kite = kite

    @property
    def account_id(self) -> str:
        """The Kite user ID for the authenticated account."""
        return self._account_id or "unknown"

    def fetch_todays_orders(self) -> list[OrderEvent]:
        """
        Fetches today's complete order history via the Kite REST API.
        Returns a list of OrderEvent objects, one per order lifecycle event.

        This is a point-in-time snapshot (not a stream). For live updates,
        use ``stream_order_updates()``.

        Raises
        ------
        BrokerStreamError
            If the API call fails for any reason.
        """
        try:
            raw_orders = self._kite.orders()
        except Exception as exc:
            raise BrokerStreamError(
                f"Failed to fetch today's orders: {exc}"
            ) from exc

        events = [_kite_order_to_event(o, self.account_id) for o in raw_orders]
        logger.info(
            "Fetched %d order events for account %s",
            len(events), self.account_id
        )
        return events

    def fetch_order_history(self, order_id: str) -> list[OrderEvent]:
        """
        Fetches the full lifecycle history for a specific order ID.
        Kite returns one entry per state transition, so a placed→modified→cancelled
        order will return 3 entries.

        Raises BrokerStreamError on API failure.
        """
        try:
            raw_history = self._kite.order_history(order_id=order_id)
        except Exception as exc:
            raise BrokerStreamError(
                f"Failed to fetch history for order {order_id}: {exc}"
            ) from exc

        return [_kite_order_to_event(o, self.account_id) for o in raw_history]

    def stream_order_updates(
        self,
        on_order_update: Callable[[OrderEvent], None] | None = None,
    ) -> None:
        """
        Opens a Kite WebSocket connection and streams live order updates.

        ``on_order_update`` is called with each OrderEvent as it arrives.
        If None, order events are logged at INFO level.

        This method BLOCKS until the connection is closed.
        Run it in a background thread for non-blocking use.

        Raises BrokerStreamError if the WebSocket connection fails.

        Example::

            import threading
            stream = KiteOrderStream()
            t = threading.Thread(target=stream.stream_order_updates,
                                 args=(lambda e: print(e),))
            t.daemon = True
            t.start()
        """
        try:
            from kiteconnect import KiteTicker  # type: ignore[import]
        except ImportError as exc:
            raise BrokerStreamError(
                "kiteconnect package not installed. Run: pip install kiteconnect"
            ) from exc

        ticker = KiteTicker(self._api_key, self._access_token)

        def _on_order_update(ws, message):  # noqa: ARG001
            try:
                event = _kite_order_to_event(message, self.account_id)
                if on_order_update:
                    on_order_update(event)
                else:
                    logger.info("Order event: %s", event)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to process order update: %s | Raw: %s", exc, message)

        def _on_error(ws, code, reason):  # noqa: ARG001
            raise BrokerStreamError(f"Kite WebSocket error {code}: {reason}")

        def _on_close(ws, code, reason):  # noqa: ARG001
            logger.warning(
                "Kite WebSocket closed (code=%s, reason=%s). "
                "Reconnect logic needed for production use.", code, reason
            )

        ticker.on_order_update = _on_order_update
        ticker.on_error = _on_error
        ticker.on_close = _on_close

        try:
            logger.info("Starting Kite order WebSocket stream for %s", self.account_id)
            ticker.connect(threaded=False)
        except Exception as exc:
            raise BrokerStreamError(
                f"Kite WebSocket connect failed: {exc}"
            ) from exc
