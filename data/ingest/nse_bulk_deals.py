"""
NSE Bulk & Block Deal Disclosure Ingestion
==========================================

Downloads and parses NSE's bulk deal and block deal disclosure files.

What are bulk/block deals?
--------------------------
- **Bulk deal**: Any trade where total quantity traded in a security exceeds
  0.5% of the equity shares of the company in a single day. The broker must
  report these to the exchange.
- **Block deal**: Large trades executed in a separate trading window
  (8:45–9:00 AM IST) with a minimum order size of ₹10 crore. The exchange
  then discloses these publicly.

Data sources (public, no authentication, 1-day delayed)
--------------------------------------------------------
Bulk deals (current year + historical):
    https://archives.nseindia.com/content/equities/bulk.csv
    https://archives.nseindia.com/archives/bulk_deals/bulk_{YYYY}.csv  (prior years)

Block deals (current year):
    https://archives.nseindia.com/content/equities/block_deal.csv

Data columns
------------
  Date | Symbol | Security Name | Client Name | Buy/Sell | Quantity | Price (Weighted Avg)

IMPORTANT — Label quality warning
----------------------------------
# WARNING: Bulk and block deal records are NOT confirmed manipulation.
# They are large trades the exchange REQUIRES disclosure of — many are
# legitimate institutional trades (mutual funds, FIIs, proprietary desks).
# These records are WEAK POSITIVE LABELS AT BEST for any downstream
# model training or pattern detection.
# DO NOT treat them as ground truth for manipulation. Use them as:
#   1. Context when a flagged account also appears in bulk/block data
#   2. Volume anomaly context (sudden large single-trade disclosures)
#   3. Candidate enrichment for backtesting against SEBI enforcement orders

Failure behaviour (HARD RULE #1)
---------------------------------
HTTP failures raise BulkDealFetchError. Parse failures raise BulkDealParseError.
This module NEVER silently returns empty/synthetic data on failure.
"""

import logging
from datetime import date

import pandas as pd
import requests

from data.ingest.errors import BulkDealFetchError, BulkDealParseError

logger = logging.getLogger(__name__)

# ── URLs ─────────────────────────────────────────────────────────────────────

_BULK_DEALS_URL = (
    "https://archives.nseindia.com/content/equities/bulk.csv"
)
_BLOCK_DEALS_URL = (
    "https://archives.nseindia.com/content/equities/block_deal.csv"
)
_BULK_HISTORICAL_URL = (
    "https://archives.nseindia.com/archives/bulk_deals/bulk_{year}.csv"
)

_TIMEOUT = 30
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.nseindia.com/",
    "Accept": "text/html,application/xhtml+xml,*/*",
}

# Minimum columns we need to parse a bulk/block deal record
_BULK_REQUIRED_COLS = {"symbol", "client_name", "buy_sell", "quantity", "price"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fetch_csv(url: str, deal_type: str) -> pd.DataFrame:
    """
    Fetches a CSV from `url` and returns a raw DataFrame.
    Raises BulkDealFetchError on HTTP errors, BulkDealParseError on
    CSV parse errors.
    """
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    except requests.exceptions.RequestException as exc:
        raise BulkDealFetchError(url, None, str(exc)) from exc

    if resp.status_code != 200:
        raise BulkDealFetchError(
            url, resp.status_code,
            f"NSE returned non-200 for {deal_type} deals. "
            f"The file may not yet be available for today."
        )

    try:
        # NSE's bulk CSV uses various encodings; latin-1 handles most edge cases
        df = pd.read_csv(
            pd.io.common.StringIO(resp.content.decode("latin-1")),
            skipinitialspace=True,
            on_bad_lines="skip",
        )
    except Exception as exc:  # noqa: BLE001
        raise BulkDealParseError(
            f"CSV parse failed for {deal_type} deals at {url}: {exc}"
        ) from exc

    return df


def _normalise_bulk(df: pd.DataFrame, deal_type: str) -> pd.DataFrame:
    """
    Normalises raw bulk/block deal CSV columns to our internal schema.
    NSE's bulk.csv uses slightly different column names from block_deal.csv,
    and historical files vary — this handles the most common variants.
    """
    # Normalise column names
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Common NSE column name aliases
    renames = {
        # Date variants
        "date": "date",
        "deal_date": "date",
        # Symbol variants
        "symbol": "symbol",
        "scrip_code": "symbol",
        # Client name variants
        "client_name": "client_name",
        "name_of_client": "client_name",
        "party_name": "client_name",
        # Buy/sell variants
        "buy/sell": "buy_sell",
        "buy_/_sell": "buy_sell",
        "buy_sell": "buy_sell",
        "transaction_type": "buy_sell",
        # Quantity variants
        "quantity_traded": "quantity",
        "quantity": "quantity",
        "no._of_shares": "quantity",
        # Price variants — NSE uses several spellings across years/file types
        "trade_price/wght._avg._price": "price",
        "trade_price_/_wght._avg._price": "price",  # after spaces→underscore normalisation
        "weighted_average_trade_price": "price",
        "price": "price",
        "wgt._avg._price": "price",
        "wgt._avg_price": "price",
        # Security name (informational, not required)
        "security_name": "security_name",
        "security name": "security_name",
    }
    df = df.rename(columns={k: v for k, v in renames.items() if k in df.columns})

    missing = _BULK_REQUIRED_COLS - set(df.columns)
    if missing:
        raise BulkDealParseError(
            f"After normalising columns, still missing: {sorted(missing)}. "
            f"NSE may have changed their {deal_type} deal format. "
            f"Columns available: {sorted(df.columns)}"
        )

    # Parse date
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], dayfirst=True, errors="coerce")

    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["deal_type"] = deal_type
    df["exchange"] = "NSE"

    df = df[
        ["date", "symbol", "client_name", "buy_sell", "quantity", "price",
         "deal_type", "exchange"]
        + [c for c in df.columns if c not in {
            "date", "symbol", "client_name", "buy_sell", "quantity", "price",
            "deal_type", "exchange"
        }]
    ]

    return df.dropna(subset=["symbol", "quantity"]).reset_index(drop=True)


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_bulk_deals(year: int | None = None) -> pd.DataFrame:
    """
    Fetches NSE bulk deal disclosures.

    Parameters
    ----------
    year
        If None (default), fetches the current-year running file at:
            https://archives.nseindia.com/content/equities/bulk.csv
        If an integer year is provided, fetches the historical annual file at:
            https://archives.nseindia.com/archives/bulk_deals/bulk_{year}.csv

    Returns
    -------
    pd.DataFrame
        Columns: date, symbol, client_name, buy_sell, quantity, price,
        deal_type ('bulk'), exchange ('NSE')

    Raises
    ------
    BulkDealFetchError
        If the HTTP request fails (network error or non-200 response).
    BulkDealParseError
        If the CSV cannot be parsed into the expected schema.

    REAL-DATA STATUS: Fully wired to live NSE archives. No synthetic fallback.

    LABEL WARNING: See module docstring. Bulk deals ≠ confirmed manipulation.
    """
    if year is None:
        url = _BULK_DEALS_URL
    else:
        url = _BULK_HISTORICAL_URL.format(year=year)

    logger.info("Fetching bulk deals from %s", url)
    raw = _fetch_csv(url, "bulk")
    return _normalise_bulk(raw, "bulk")


def fetch_block_deals() -> pd.DataFrame:
    """
    Fetches NSE block deal disclosures (current year running file).

    Source URL:
        https://archives.nseindia.com/content/equities/block_deal.csv

    Returns
    -------
    pd.DataFrame
        Columns: date, symbol, client_name, buy_sell, quantity, price,
        deal_type ('block'), exchange ('NSE')

    Raises
    ------
    BulkDealFetchError
        If the HTTP request fails.
    BulkDealParseError
        If the CSV cannot be parsed into the expected schema.

    REAL-DATA STATUS: Fully wired to live NSE archives. No synthetic fallback.

    LABEL WARNING: See module docstring. Block deals ≠ confirmed manipulation.
    """
    logger.info("Fetching block deals from %s", _BLOCK_DEALS_URL)
    raw = _fetch_csv(_BLOCK_DEALS_URL, "block")
    return _normalise_bulk(raw, "block")


def fetch_all_deals(year: int | None = None) -> pd.DataFrame:
    """
    Fetches both bulk and block deals and concatenates them.
    If either source fails, raises immediately — does not partially return.
    """
    bulk = fetch_bulk_deals(year=year)
    block = fetch_block_deals()
    return pd.concat([bulk, block], ignore_index=True)


def filter_by_date(df: pd.DataFrame, from_date: date, to_date: date) -> pd.DataFrame:
    """
    Filters a bulk/block deals DataFrame to a date range (inclusive).
    """
    mask = (df["date"].dt.date >= from_date) & (df["date"].dt.date <= to_date)
    return df[mask].copy()


def filter_by_symbol(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    Filters a bulk/block deals DataFrame to a specific NSE symbol.
    """
    return df[df["symbol"].str.upper() == symbol.upper()].copy()
