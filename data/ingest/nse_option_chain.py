"""
NSE Option Chain Ingestion
==========================

Fetches NSE's public option chain data — open interest (OI), implied
volatility (IV), volume, and last traded price per strike per expiry.

Data source (public, no authentication required, ~real-time during market hours)
---------------------------------------------------------------------------------
Indices (Nifty, BankNifty, FinNifty, etc.):
    https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY

Individual equities:
    https://www.nseindia.com/api/option-chain-equities?symbol={SYMBOL}

Important: NSE's API rejects bare HTTP requests — it requires a valid
browser session cookie (obtained by first hitting the homepage). This
module handles the cookie handshake automatically.

Data fields returned per strike
--------------------------------
  symbol, expiry, strike, option_type (CE/PE),
  oi, change_in_oi, iv (implied volatility),
  volume, ltp (last traded price), underlying_value, fetch_time

What this does NOT give you
---------------------------
- Historical option chain snapshots (only the current state)
- Order-level data (only aggregate OI/volume per strike)
- Live tick-by-tick streaming (poll this endpoint for time-series)

Rate limiting
-------------
NSE's public API has undocumented rate limits. As a courtesy and to avoid
IP blocks, this module enforces a minimum 1-second gap between requests.
For time-series collection, use the provided ``OptionChainPoller`` class.

Failure behaviour (HARD RULE #1)
---------------------------------
If the session setup or API call fails, raises OptionChainFetchError or
OptionChainParseError. NEVER silently returns synthetic/mock data.

REAL-DATA STATUS (as of 2024): Fully wired to live NSE API.
The NSE response format has changed 3+ times since 2020; if parsing breaks,
OptionChainParseError will tell you which keys are missing.
"""

import logging
import time
from datetime import datetime
from typing import Iterator

import pandas as pd
import requests

from data.ingest.errors import OptionChainFetchError, OptionChainParseError
from data.ingest.resilience import option_chain_circuit, retry_with_backoff

logger = logging.getLogger(__name__)

# ── URLs ─────────────────────────────────────────────────────────────────────

_NSE_HOME = "https://www.nseindia.com/"
_OC_INDICES_URL = "https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
_OC_EQUITIES_URL = "https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"

# Indices that use the /indices endpoint (not the /equities endpoint)
_INDEX_SYMBOLS = {
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX",
    "BANKEX", "NIFTY NEXT 50",
}

_TIMEOUT = 20
_SESSION_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "application/json, text/plain, */*"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
    "DNT": "1",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

_MIN_REQUEST_GAP_SECONDS = 1.0  # courtesy rate limit
# Delay between NSE homepage cookie handshake and first API call.
# Real browsers take some time to navigate before hitting the API endpoint.
# Too fast = behavioral fingerprinting may flag as a bot.
_POST_COOKIE_DELAY = 1.0  # HEURISTIC: 1.0s — slightly longer than bhavcopy
                           # because the option chain API is more aggressively guarded


# ── Session management ────────────────────────────────────────────────────────

class _NSESession:
    """
    Maintains a browser-like requests.Session with NSE cookies.

    NSE's API returns 401/403 for requests without a valid session cookie
    obtained by first visiting the homepage. This class handles that handshake.
    """

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(_SESSION_HEADERS)
        self._last_request_at: float = 0.0
        self._cookies_initialised = False

    def _ensure_cookies(self) -> None:
        if self._cookies_initialised:
            return
        try:
            resp = self._session.get(_NSE_HOME, timeout=_TIMEOUT)
            resp.raise_for_status()
            self._cookies_initialised = True
            logger.debug("NSE session cookies obtained.")
            # Pause after homepage: real browsers take time to navigate
            time.sleep(_POST_COOKIE_DELAY)
        except requests.exceptions.HTTPError as exc:
            # raise_for_status() throws HTTPError for 4xx/5xx.
            # Preserve the status code so retry_with_backoff can correctly
            # classify 403 as NON-RETRYABLE (without this, status_code=None
            # and the retry misclassifies it as a network-level failure).
            status_code = exc.response.status_code if exc.response is not None else None
            raise OptionChainFetchError(
                _NSE_HOME, status_code,
                f"NSE homepage returned HTTP {status_code}. "
                f"For 403: IP reputation block from this environment — "
                f"see docs/NSE_ACCESS_LIMITATIONS.md. Error: {exc}"
            ) from exc
        except requests.exceptions.RequestException as exc:
            # Pure network failure (timeout, connection refused) — no HTTP response
            raise OptionChainFetchError(
                _NSE_HOME, None,
                f"Could not establish NSE session (homepage fetch failed): {exc}"
            ) from exc


    def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < _MIN_REQUEST_GAP_SECONDS:
            time.sleep(_MIN_REQUEST_GAP_SECONDS - elapsed)

    @retry_with_backoff(
        max_retries=2,
        base_delay=2.0,
        max_delay=20.0,
        retryable_exceptions=(OptionChainFetchError,),
    )
    def get(self, url: str) -> dict:
        """
        Makes a rate-limited, cookie-authenticated GET request to NSE's API.
        Returns the parsed JSON response dict.
        Raises OptionChainFetchError on HTTP/network failure.

        Non-retryable failures (403, 404) propagate immediately.
        Retryable failures (5xx, network) are retried up to 2 times.
        Circuit breaker prevents hammering after repeated failures.
        """
        self._ensure_cookies()
        self._rate_limit()

        option_chain_circuit.before_request()
        try:
            resp = self._session.get(url, timeout=_TIMEOUT)
            self._last_request_at = time.monotonic()
        except requests.exceptions.RequestException as exc:
            option_chain_circuit.on_failure()
            raise OptionChainFetchError(url, None, str(exc)) from exc

        if resp.status_code != 200:
            if resp.status_code in (500, 502, 503, 504, 429):
                option_chain_circuit.on_failure()
            raise OptionChainFetchError(
                url, resp.status_code,
                f"NSE API returned HTTP {resp.status_code}. "
                f"For 403: IP/bot block — see docs/NSE_ACCESS_LIMITATIONS.md. "
                f"For 401/403: session may have expired — reinstantiate session."
            )
        option_chain_circuit.on_success()

        # Check we actually got JSON, not an HTML block page
        content_type = resp.headers.get("Content-Type", "")
        if "html" in content_type.lower():
            raise OptionChainFetchError(
                url, resp.status_code,
                "NSE returned an HTML page instead of JSON. "
                "This usually means the session was rejected or the option chain "
                "is only available during market hours (09:15–15:30 IST)."
            )

        try:
            return resp.json()
        except ValueError as exc:
            raise OptionChainFetchError(
                url, resp.status_code,
                f"NSE response was not valid JSON: {exc}"
            ) from exc


# Module-level shared session (avoids re-doing cookie handshake on every call)
_shared_session = _NSESession()


# ── Parsers ───────────────────────────────────────────────────────────────────

def _parse_option_chain_response(
    data: dict,
    symbol: str,
    fetch_time: datetime,
) -> pd.DataFrame:
    """
    Parses NSE's option chain JSON response into a flat DataFrame.

    NSE's response structure (as of 2024):
    {
      "records": {
        "expiryDates": [...],
        "data": [
          {
            "strikePrice": float,
            "expiryDate": str,
            "CE": { "openInterest": ..., "changeinOpenInterest": ...,
                    "impliedVolatility": ..., "totalTradedVolume": ...,
                    "lastPrice": ..., "underlyingValue": ... },
            "PE": { ... same keys ... }
          },
          ...
        ],
        "underlyingValue": float
      }
    }

    Raises OptionChainParseError if the expected top-level structure is absent.
    """
    try:
        records = data["records"]
        raw_data = records["data"]
        underlying_value = records.get("underlyingValue", None)
    except (KeyError, TypeError) as exc:
        raise OptionChainParseError(
            f"NSE option chain response missing expected keys. "
            f"Top-level keys found: {list(data.keys())}. "
            f"NSE may have changed their API response format. Error: {exc}"
        ) from exc

    rows = []
    for entry in raw_data:
        strike = entry.get("strikePrice")
        expiry = entry.get("expiryDate")

        for opt_type in ("CE", "PE"):
            oc = entry.get(opt_type)
            if oc is None:
                continue  # not all strikes have both CE and PE (deep ITM/OTM)

            rows.append({
                "symbol":          symbol.upper(),
                "expiry":          expiry,
                "strike":          strike,
                "option_type":     opt_type,
                "oi":              oc.get("openInterest"),
                "change_in_oi":    oc.get("changeinOpenInterest"),
                "iv":              oc.get("impliedVolatility"),
                "volume":          oc.get("totalTradedVolume"),
                "ltp":             oc.get("lastPrice"),
                "bid":             oc.get("bidprice"),
                "ask":             oc.get("askPrice"),
                "underlying_value": underlying_value or oc.get("underlyingValue"),
                "fetch_time":      fetch_time,
            })

    if not rows:
        raise OptionChainParseError(
            f"No option chain rows parsed for {symbol}. "
            f"The symbol may be invalid, or market may be closed "
            f"(option chain data is only available during trading hours)."
        )

    df = pd.DataFrame(rows)
    df["expiry"] = pd.to_datetime(df["expiry"], dayfirst=True, errors="coerce")
    df["oi"] = pd.to_numeric(df["oi"], errors="coerce")
    df["change_in_oi"] = pd.to_numeric(df["change_in_oi"], errors="coerce")
    df["iv"] = pd.to_numeric(df["iv"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    df["ltp"] = pd.to_numeric(df["ltp"], errors="coerce")
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")

    return df.reset_index(drop=True)


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_option_chain(
    symbol: str,
    *,
    session: _NSESession | None = None,
) -> pd.DataFrame:
    """
    Fetches the current NSE option chain for ``symbol``.

    Automatically selects the correct endpoint:
    - Index symbols (NIFTY, BANKNIFTY, FINNIFTY, etc.) → /option-chain-indices
    - Equity symbols → /option-chain-equities

    Parameters
    ----------
    symbol
        NSE trading symbol, e.g. 'NIFTY', 'BANKNIFTY', 'RELIANCE', 'TCS'.
        Case-insensitive.
    session
        Optional pre-built _NSESession. If None, uses the module-level
        shared session (recommended to avoid repeated cookie handshakes).

    Returns
    -------
    pd.DataFrame
        Columns: symbol, expiry, strike, option_type, oi, change_in_oi,
        iv, volume, ltp, bid, ask, underlying_value, fetch_time

    Raises
    ------
    OptionChainFetchError
        On HTTP failure, non-JSON response, or NSE returning an HTML block page.
    OptionChainParseError
        If the JSON response cannot be parsed into the expected schema.

    REAL-DATA STATUS: Fully wired to live NSE API. No synthetic fallback.
    Only available during NSE market hours (09:15–15:30 IST on trading days).
    Outside market hours, NSE may return stale data or an empty response.
    """
    sym_upper = symbol.upper()
    if sym_upper in _INDEX_SYMBOLS:
        url = _OC_INDICES_URL.format(symbol=sym_upper)
    else:
        url = _OC_EQUITIES_URL.format(symbol=sym_upper)

    logger.info("Fetching option chain for %s from %s", sym_upper, url)
    sess = session or _shared_session
    data = sess.get(url)
    return _parse_option_chain_response(data, sym_upper, datetime.utcnow())


def fetch_multiple_option_chains(
    symbols: list[str],
    *,
    session: _NSESession | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Fetches option chains for multiple symbols sequentially (rate-limited).
    Returns a dict mapping symbol → DataFrame.
    If any individual fetch fails, its OptionChainFetchError propagates
    immediately — partial results are NOT silently returned.
    """
    sess = session or _NSESession()
    results: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        results[symbol.upper()] = fetch_option_chain(symbol, session=sess)
    return results


class OptionChainPoller:
    """
    Polls the NSE option chain on a fixed interval and yields DataFrames.

    Usage::

        poller = OptionChainPoller("NIFTY", interval_seconds=60)
        for df in poller.poll():
            # process snapshot
            store_to_db(df)

    Raises OptionChainFetchError / OptionChainParseError on failure —
    does NOT silently skip a failed poll cycle.
    """

    def __init__(self, symbol: str, interval_seconds: float = 60.0) -> None:
        if interval_seconds < _MIN_REQUEST_GAP_SECONDS:
            raise ValueError(
                f"interval_seconds must be >= {_MIN_REQUEST_GAP_SECONDS} "
                f"to respect NSE's rate limits."
            )
        self.symbol = symbol
        self.interval_seconds = interval_seconds
        self._session = _NSESession()

    def poll(self, max_snapshots: int | None = None) -> Iterator[pd.DataFrame]:
        """
        Generator. Yields one DataFrame per poll interval.
        Call ``next()`` or iterate in a loop.
        ``max_snapshots=None`` means poll indefinitely (until interrupted).
        """
        count = 0
        while max_snapshots is None or count < max_snapshots:
            start = time.monotonic()
            yield fetch_option_chain(self.symbol, session=self._session)
            count += 1
            elapsed = time.monotonic() - start
            sleep_time = max(0.0, self.interval_seconds - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)
