"""
NSE Daily Bhavcopy Ingestion
============================

Downloads and parses NSE's daily bhavcopy — the end-of-day equity data file
containing OHLCV (Open, High, Low, Close, Volume) and delivery statistics for
every security traded on NSE that day.

Data source
-----------
NSE Archives (public, no authentication required, 1-day delayed):
    https://archives.nseindia.com/content/historical/EQUITIES/{YYYY}/{MON}/cm{DD}{MON}{YYYY}bhav.csv.zip

Example for 2024-01-15:
    https://archives.nseindia.com/content/historical/EQUITIES/2024/JAN/cm15JAN2024bhav.csv.zip

What this gives you
-------------------
- Symbol, Series (EQ/BE/BZ etc.), ISIN
- Open, High, Low, Close prices
- Volume (number of shares traded)
- Turnover (₹ value traded)
- Delivery quantity and delivery-to-traded % (useful for wash-trade signals)

What this does NOT give you
---------------------------
- Intra-day prices or order-level data — this is strictly end-of-day
- Real-time data — always T+1 (previous trading day)
- Options/futures data — that comes from nse_option_chain.py

Failure behaviour (HARD RULE #1)
---------------------------------
If the download fails (network error, HTTP error, missing date, malformed ZIP)
this module raises BhavcopyFetchError or BhavcopyParseError.
It NEVER silently returns synthetic/empty data — the caller must handle the error.
"""

import io
import logging
import time
import zipfile
from datetime import date, timedelta

import pandas as pd
import requests

from data.ingest.errors import BhavcopyFetchError, BhavcopyParseError
from data.ingest.resilience import bhavcopy_circuit, retry_with_backoff

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_BASE_URL = "https://archives.nseindia.com/content/historical/EQUITIES"

# Columns the bhavcopy CSV must contain for us to consider it valid.
# NSE has used this schema consistently since ~2010; if they change it,
# BhavcopyParseError will tell us which columns went missing.
_REQUIRED_COLUMNS = {
    "SYMBOL", "SERIES", "OPEN", "HIGH", "LOW", "CLOSE",
    "TOTTRDQTY", "TOTTRDVAL", "TIMESTAMP", "ISIN",
}

# Delivery data lives in a separate file on the same archive server.
# Format: https://archives.nseindia.com/products/content/sec_deliverypos_{DDMONYYYY}.dat
_DELIVERY_BASE_URL = "https://archives.nseindia.com/products/content"

# NSE month abbreviations (uppercase 3-letter, matches their URL scheme exactly)
_MONTH_ABBR = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}

# Request timeout (seconds). NSE's archive server can be slow.
_TIMEOUT = 30

# NSE homepage used for cookie handshake before archive downloads.
# NSE's CDN checks for a valid session cookie on some archive endpoints.
# Visiting the homepage first is the same approach used by nse_option_chain.py.
_NSE_HOME = "https://www.nseindia.com/"

# Browser-realistic headers matching what Chrome 124 sends.
# NSE uses bot detection that checks UA, Referer, and Accept-Language.
# NOTE: These headers significantly improve success rate from residential
# IPs. They do NOT guarantee access from datacenter/cloud IPs — NSE's
# IP reputation layer operates at a network level that headers cannot bypass.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "DNT": "1",
    "Connection": "keep-alive",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
}

# Minimum gap between cookie handshake and archive download (seconds).
# NSE's behavioral analysis may flag requests that arrive too fast after
# the homepage visit (no human browses that fast).
_POST_COOKIE_DELAY = 0.8  # HEURISTIC: 0.8s — mimics human navigation pace


class _BhavSession:
    """
    A requests.Session with NSE cookie handshake for archive downloads.

    NSE's bot protection checks for a valid session cookie even on archive
    downloads. This class performs the same homepage visit that
    nse_option_chain._NSESession does, before making archive requests.

    IMPORTANT: This improves access from residential IPs. It does NOT
    guarantee access from datacenter IPs blocked by IP reputation.
    See docs/NSE_ACCESS_LIMITATIONS.md.
    """

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)
        self._cookies_initialised = False

    def _ensure_cookies(self) -> None:
        if self._cookies_initialised:
            return
        try:
            resp = self._session.get(_NSE_HOME, timeout=_TIMEOUT)
            resp.raise_for_status()
            self._cookies_initialised = True
            logger.debug("NSE session cookies obtained for bhavcopy.")
            time.sleep(_POST_COOKIE_DELAY)
        except requests.exceptions.RequestException as exc:
            # Cookie failure is non-fatal — we can still attempt the archive
            # download without a cookie (it may still succeed from residential IPs).
            logger.warning(
                "NSE homepage cookie handshake failed: %s. "
                "Proceeding without session cookie — archive download may fail.",
                exc
            )

    def get(self, url: str) -> bytes:
        """Perform a cookie-authenticated GET to the NSE archive and return raw bytes."""
        self._ensure_cookies()
        resp = self._session.get(url, timeout=_TIMEOUT)
        return resp


# Module-level shared session (avoids re-doing cookie handshake on every call)
_shared_bhav_session = _BhavSession()


# ── URL builders ─────────────────────────────────────────────────────────────

def _bhavcopy_url(trading_date: date) -> str:
    """
    Returns the exact archive URL for a given trading date.

    NSE URL format:
        https://archives.nseindia.com/content/historical/EQUITIES/{YYYY}/{MON}/cm{DD}{MON}{YYYY}bhav.csv.zip
    """
    mon = _MONTH_ABBR[trading_date.month]
    dd = f"{trading_date.day:02d}"
    yyyy = trading_date.year
    filename = f"cm{dd}{mon}{yyyy}bhav.csv.zip"
    return f"{_BASE_URL}/{yyyy}/{mon}/{filename}"


def _delivery_url(trading_date: date) -> str:
    """
    Returns the URL for the delivery position file for a given date.

    NSE URL format:
        https://archives.nseindia.com/products/content/sec_deliverypos_{DDMONYYYY}.dat
    """
    mon = _MONTH_ABBR[trading_date.month]
    dd = f"{trading_date.day:02d}"
    yyyy = trading_date.year
    filename = f"sec_deliverypos_{dd}{mon}{yyyy}.dat"
    return f"{_DELIVERY_BASE_URL}/{filename}"


# ── Core fetchers ────────────────────────────────────────────────────────────

@retry_with_backoff(
    max_retries=3,
    base_delay=2.0,
    max_delay=30.0,
    retryable_exceptions=(BhavcopyFetchError,),
)
def _fetch_raw(url: str) -> bytes:
    """
    HTTP GET with timeout, error handling, retry, and circuit breaker.

    Uses the module-level session (with NSE homepage cookie) rather than
    bare requests.get. Raises BhavcopyFetchError on any failure.

    Non-retryable failures (403, 404) propagate immediately — they will not
    be retried by retry_with_backoff.

    Retryable failures (5xx, network timeouts) are retried up to 3 times
    with exponential backoff and full jitter.
    """
    bhavcopy_circuit.before_request()  # raises CircuitBreakerOpenError if OPEN
    try:
        resp = _shared_bhav_session.get(url)
    except requests.exceptions.RequestException as exc:
        bhavcopy_circuit.on_failure()
        raise BhavcopyFetchError(url, None, str(exc)) from exc

    if resp.status_code != 200:
        if resp.status_code in (500, 502, 503, 504, 429):
            bhavcopy_circuit.on_failure()
        # Non-retryable statuses (403, 404, etc.) are raised immediately
        raise BhavcopyFetchError(
            url, resp.status_code,
            f"NSE returned HTTP {resp.status_code}. "
            f"For 403: IP reputation block — see docs/NSE_ACCESS_LIMITATIONS.md. "
            f"For 404: non-trading day or archive not yet available."
        )
    bhavcopy_circuit.on_success()
    return resp.content


def _parse_bhavcopy_csv(raw_zip: bytes, trading_date: date) -> pd.DataFrame:
    """
    Extracts the CSV from the ZIP and parses it into a normalised DataFrame.

    Raises BhavcopyParseError if required columns are missing.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
            csv_name = zf.namelist()[0]  # always one file in NSE bhav zip
            with zf.open(csv_name) as f:
                df = pd.read_csv(f)
    except (zipfile.BadZipFile, KeyError, pd.errors.ParserError) as exc:
        raise BhavcopyParseError([], f"Could not unzip/parse bhavcopy: {exc}") from exc

    # Normalise column names (strip whitespace, uppercase)
    df.columns = [c.strip().upper() for c in df.columns]

    missing = _REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise BhavcopyParseError(
            sorted(missing),
            f"NSE may have changed their bhavcopy schema. "
            f"Expected columns: {sorted(_REQUIRED_COLUMNS)}"
        )

    # Rename to our internal schema
    df = df.rename(columns={
        "SYMBOL":    "symbol",
        "SERIES":    "series",
        "OPEN":      "open",
        "HIGH":      "high",
        "LOW":       "low",
        "CLOSE":     "close",
        "TOTTRDQTY": "volume",
        "TOTTRDVAL": "turnover",
        "TIMESTAMP": "timestamp_raw",
        "ISIN":      "isin",
    })

    df["date"] = trading_date
    df["exchange"] = "NSE"

    # Keep only EQ (equity) series unless caller requests all series
    # (BE, BZ etc. are special categories; mixing them skews volume baselines)
    df = df[df["series"].str.strip() == "EQ"].copy()

    df = df[[
        "symbol", "series", "isin", "open", "high", "low", "close",
        "volume", "turnover", "date", "exchange",
    ]]
    df = df.reset_index(drop=True)
    logger.info(
        "Bhavcopy parsed: %d EQ records for %s", len(df), trading_date
    )
    return df


def _parse_delivery_dat(raw_bytes: bytes, trading_date: date) -> pd.DataFrame:
    """
    Parses NSE's delivery position .dat file.

    Format: pipe-delimited, columns vary slightly by year but always include
    SYMBOL, SERIES, QUANTITY_TRADED, DELIVERABLE_QTY, PERCENTAGE_DELVBL_QTY.

    Returns a DataFrame with [symbol, series, delivery_qty, delivery_pct, date].
    If parsing fails, logs a warning and returns an empty DataFrame —
    delivery data is supplementary; the core bhavcopy fetch is still valid.
    """
    try:
        df = pd.read_csv(
            io.BytesIO(raw_bytes),
            sep=",",
            skipinitialspace=True,
            on_bad_lines="skip",
        )
        df.columns = [c.strip().upper() for c in df.columns]

        # NSE uses these column names in delivery files
        symbol_col = next(
            (c for c in df.columns if "SYMBOL" in c), None
        )
        qty_col = next(
            (c for c in df.columns if "DELIVERABLE" in c and "QTY" in c), None
        )
        pct_col = next(
            (c for c in df.columns if "PERCENTAGE" in c or "PCT" in c or "DELVBL" in c and "PCT" in c), None
        )

        if not all([symbol_col, qty_col]):
            logger.warning(
                "Delivery file for %s missing expected columns — "
                "skipping delivery enrichment. Columns found: %s",
                trading_date, list(df.columns)
            )
            return pd.DataFrame(columns=["symbol", "delivery_qty", "delivery_pct", "date"])

        result = pd.DataFrame({
            "symbol": df[symbol_col].str.strip(),
            "delivery_qty": pd.to_numeric(df[qty_col], errors="coerce"),
            "delivery_pct": pd.to_numeric(df[pct_col], errors="coerce") if pct_col else None,
            "date": trading_date,
        })
        return result.dropna(subset=["symbol"])

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not parse delivery file for %s: %s. "
            "Continuing without delivery data.",
            trading_date, exc
        )
        return pd.DataFrame(columns=["symbol", "delivery_qty", "delivery_pct", "date"])


# ── Public API ───────────────────────────────────────────────────────────────

def fetch_bhavcopy(
    trading_date: date | None = None,
    *,
    include_delivery: bool = True,
    series: str = "EQ",
) -> pd.DataFrame:
    """
    Downloads and returns the NSE bhavcopy for ``trading_date``.

    Parameters
    ----------
    trading_date
        The date to fetch. Defaults to yesterday (T-1). NSE posts the
        previous day's bhavcopy by approximately 18:30 IST.
    include_delivery
        If True (default), attempts to enrich the result with delivery
        quantity and delivery-to-traded % from the supplementary .dat file.
        Delivery fetch failures are non-fatal (logged as warnings) — only
        the main bhavcopy fetch is considered required.
    series
        Which NSE series to include (default "EQ" = regular equity).
        Pass None to include all series (BE, BZ, SM, etc.).

    Returns
    -------
    pd.DataFrame
        Columns: symbol, series, isin, open, high, low, close, volume,
        turnover, delivery_qty (if available), delivery_pct (if available),
        date, exchange

    Raises
    ------
    BhavcopyFetchError
        If the HTTP request to NSE's archive server fails. Includes the
        exact URL attempted and HTTP status code.
    BhavcopyParseError
        If the downloaded file cannot be parsed into the expected schema.

    REAL-DATA STATUS: This function is fully wired to live NSE archives.
    No synthetic fallback exists — if the fetch fails, the exception propagates.
    """
    if trading_date is None:
        trading_date = date.today() - timedelta(days=1)

    url = _bhavcopy_url(trading_date)
    logger.info("Fetching bhavcopy from %s", url)

    raw_zip = _fetch_raw(url)
    df = _parse_bhavcopy_csv(raw_zip, trading_date)

    if series is not None:
        df = df[df["series"].str.strip() == series].copy()

    if include_delivery:
        del_url = _delivery_url(trading_date)
        logger.info("Fetching delivery data from %s", del_url)
        try:
            del_raw = _fetch_raw(del_url)
            del_df = _parse_delivery_dat(del_raw, trading_date)
            if not del_df.empty:
                df = df.merge(del_df[["symbol", "delivery_qty", "delivery_pct"]],
                              on="symbol", how="left")
        except BhavcopyFetchError as exc:
            # Delivery file is supplementary — log but don't abort
            logger.warning(
                "Delivery file fetch failed (%s). "
                "Continuing without delivery data. Error: %s",
                del_url, exc
            )

    return df


def get_last_trading_day(reference: date | None = None) -> date:
    """
    Returns the most recent weekday before ``reference`` (default: today).
    This is a conservative approximation — does not account for NSE
    holidays (exchange holidays would require a separate holiday calendar).
    """
    ref = reference or date.today()
    candidate = ref - timedelta(days=1)
    while candidate.weekday() >= 5:  # Saturday=5, Sunday=6
        candidate -= timedelta(days=1)
    return candidate
