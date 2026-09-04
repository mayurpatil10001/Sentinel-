"""
Historical Data Puller — Phase 8 Backtest
==========================================

Pulls real NSE bhavcopy data for the scrips and date ranges specified
in sebi_case_catalog.py, using the Phase 6 resilient ingestion modules.

HARD RULES (inherited from the main ingestion layer):
  - Never synthesize data and present it as real historical data.
  - If a fetch fails (403, 404, 500), record the failure explicitly per
    date — do NOT fall back to interpolated or estimated values.
  - 404 on a specific date = non-trading day (weekend/holiday). This is
    expected and noted, not counted as a failure.
  - 500 or 403 = genuine fetch failure. Recorded as FETCH_ERROR.

Output
------
For each (scrip, date_range) request, returns a dict:
  {
    "symbol": str,
    "trading_days_attempted": int,
    "trading_days_fetched": int,
    "non_trading_days": int,
    "fetch_errors": list[{"date": date, "error": str}],
    "data": pd.DataFrame or None,   # OHLCV rows for this symbol across dates
  }
"""

import logging
import time
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from data.ingest.errors import BhavcopyFetchError, BhavcopyParseError, IngestError
from data.ingest.nse_bhavcopy import fetch_bhavcopy

logger = logging.getLogger(__name__)


# Delay between successive bhavcopy fetches to avoid hammering NSE archives.
# The Phase 6 retry-with-backoff layer adds its own delays on top of this.
_INTER_FETCH_DELAY_SECONDS = 1.5


def _business_days(start: date, end: date) -> list[date]:
    """Generate business days (Mon-Fri) between start and end inclusive."""
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:   # 0=Mon, 4=Fri
            days.append(current)
        current += timedelta(days=1)
    return days


def pull_bhavcopy_for_symbol(
    symbol: str,
    start: date,
    end: date,
    series: str = "EQ",
) -> dict:
    """
    Pull NSE bhavcopy rows for a single symbol across a date range.

    Returns a results dict documenting every date attempted, which
    succeeded, which were non-trading days (404), and which had genuine
    fetch errors.

    Parameters
    ----------
    symbol : str
        NSE symbol (e.g. "RELIANCE", "MAURIUDYOG"). Case-insensitive.
    start : date
        First date of the range (inclusive).
    end : date
        Last date of the range (inclusive).
    series : str
        NSE series code (usually "EQ" for equities; "BE" for T+2
        trade-to-trade stocks, common for small-caps under surveillance).

    Returns
    -------
    dict with keys:
        symbol, trading_days_attempted, trading_days_fetched,
        non_trading_days (404s), fetch_errors (list), data (DataFrame | None)
    """
    symbol_upper = symbol.upper()
    attempted = _business_days(start, end)
    rows: list[dict] = []
    fetch_errors: list[dict] = []
    non_trading_days: int = 0
    days_fetched: int = 0

    logger.info(
        f"Pulling bhavcopy for {symbol_upper} from {start} to {end} "
        f"({len(attempted)} business days)"
    )

    for trading_date in attempted:
        try:
            df = fetch_bhavcopy(trading_date)
            # fetch_bhavcopy returns lowercase renamed columns:
            # symbol, series, isin, open, high, low, close, volume, turnover, date, exchange
            mask = df["symbol"].str.upper() == symbol_upper
            if series:
                mask &= df["series"].str.upper() == series.upper()
            symbol_rows = df[mask]

            if symbol_rows.empty:
                # The bhavcopy fetched fine but this symbol wasn't in it.
                # Could mean: wrong series, scrip delisted, or scrip name changed.
                logger.debug(
                    f"{symbol_upper}: not found in bhavcopy for {trading_date} "
                    f"(series={series}). Trying all series."
                )
                # Retry without series filter to see if it's a series mismatch
                symbol_rows = df[df["symbol"].str.upper() == symbol_upper]
                if symbol_rows.empty:
                    fetch_errors.append({
                        "date": trading_date,
                        "error": (
                            f"Symbol {symbol_upper!r} not found in bhavcopy "
                            f"for {trading_date}. May be delisted, renamed, "
                            f"or listed under a different exchange segment."
                        ),
                    })
                    continue
                # Found under different series — log but use it
                found_series = symbol_rows["series"].iloc[0]
                logger.info(
                    f"{symbol_upper}: found under series {found_series!r} "
                    f"(not {series!r}) for {trading_date}"
                )

            row = symbol_rows.iloc[0].to_dict()
            row["DATE"] = trading_date
            rows.append(row)
            days_fetched += 1

        except BhavcopyFetchError as e:
            if e.status_code == 404:
                # Expected: NSE returns 404 for non-trading days (weekends,
                # holidays). Not a failure — just note it.
                non_trading_days += 1
                logger.debug(f"Non-trading day: {trading_date} (404)")
            else:
                logger.warning(
                    f"Fetch error for {trading_date}: HTTP {e.status_code}"
                )
                fetch_errors.append({
                    "date": trading_date,
                    "error": f"HTTP {e.status_code}: {e}",
                })

        except IngestError as e:
            # MaxRetriesExceededError, CircuitBreakerOpenError, parse errors
            logger.warning(f"Ingest error for {trading_date}: {e}")
            fetch_errors.append({
                "date": trading_date,
                "error": str(e)[:200],
            })

        except Exception as e:
            logger.error(f"Unexpected error for {trading_date}: {e}", exc_info=True)
            fetch_errors.append({
                "date": trading_date,
                "error": f"Unexpected: {e}",
            })

        finally:
            time.sleep(_INTER_FETCH_DELAY_SECONDS)

    data_df = None
    if rows:
        data_df = pd.DataFrame(rows)
        # Normalise column types (lowercase column names from fetch_bhavcopy)
        for col in ("open", "high", "low", "close", "volume", "turnover"):
            if col in data_df.columns:
                data_df[col] = pd.to_numeric(data_df[col], errors="coerce")
        # Add uppercase aliases for backward compat with runner
        data_df["SYMBOL"] = data_df["symbol"]
        data_df["OPEN"]   = data_df["open"]
        data_df["HIGH"]   = data_df["high"]
        data_df["LOW"]    = data_df["low"]
        data_df["CLOSE"]  = data_df["close"]
        data_df["TOTTRDQTY"] = data_df["volume"]
        data_df["TOTTRDVAL"] = data_df["turnover"]
        data_df["DATE"]   = data_df.get("DATE", data_df.get("date"))
        data_df = data_df.sort_values("date").reset_index(drop=True)

    return {
        "symbol": symbol_upper,
        "start": start,
        "end": end,
        "trading_days_attempted": len(attempted),
        "trading_days_fetched": days_fetched,
        "non_trading_days": non_trading_days,
        "fetch_errors": fetch_errors,
        "data": data_df,
    }


def pull_for_case(case, scrip_symbol: str) -> dict:
    """
    Pull bhavcopy for a single scrip across a case's investigation period.
    Wraps pull_bhavcopy_for_symbol with case metadata for logging.
    """
    logger.info(
        f"[{case.case_id}] Pulling data for scrip {scrip_symbol!r} "
        f"({case.investigation_start} to {case.investigation_end})"
    )
    result = pull_bhavcopy_for_symbol(
        symbol=scrip_symbol,
        start=case.investigation_start,
        end=case.investigation_end,
    )
    result["case_id"] = case.case_id
    return result


def pull_negative_controls(symbols: list[str], dates: list[date]) -> dict[str, dict]:
    """
    Pull bhavcopy for each control stock on each control date.

    Returns a dict keyed by symbol, where each value is the pull result
    for that symbol across the negative control dates.

    We compress the date list into ranges for efficiency (pull full
    bhavcopy per date and filter — same cost whether we request 1 or 500
    symbols per date).
    """
    if not dates:
        return {}

    # To minimise NSE fetches, group all control dates into one range and
    # pull the full bhavcopy once per date, then filter per symbol.
    min_date = min(dates)
    max_date = max(dates)

    logger.info(
        f"Pulling negative control bhavcopy for {len(symbols)} symbols "
        f"across {len(dates)} dates ({min_date} to {max_date})"
    )

    # Fetch full bhavcopy for every date in the range, cache in memory
    full_bhavcopies: dict[date, pd.DataFrame] = {}
    control_dates_set = set(dates)

    for d in _business_days(min_date, max_date):
        if d not in control_dates_set:
            continue
        try:
            df = fetch_bhavcopy(d)
            full_bhavcopies[d] = df
            logger.debug(f"Fetched full bhavcopy for {d} ({len(df)} rows)")
        except BhavcopyFetchError as e:
            if e.status_code == 404:
                logger.debug(f"Non-trading day: {d} (404)")
            else:
                logger.warning(f"Fetch error for {d}: {e}")
        except IngestError as e:
            logger.warning(f"Ingest error for {d}: {e}")
        finally:
            time.sleep(_INTER_FETCH_DELAY_SECONDS)

    # Now extract per-symbol data
    results: dict[str, dict] = {}
    for symbol in symbols:
        symbol_upper = symbol.upper()
        rows = []
        fetch_errors = []
        for d, df in full_bhavcopies.items():
            mask = df["symbol"].str.upper() == symbol_upper
            symbol_rows = df[mask]
            if symbol_rows.empty:
                fetch_errors.append({
                    "date": d,
                    "error": f"{symbol_upper} not found in bhavcopy for {d}",
                })
            else:
                row = symbol_rows.iloc[0].to_dict()
                row["DATE"] = d
                rows.append(row)

        data_df = None
        if rows:
            data_df = pd.DataFrame(rows)
            for col in ("open", "high", "low", "close", "volume", "turnover"):
                if col in data_df.columns:
                    data_df[col] = pd.to_numeric(data_df[col], errors="coerce")
            # Uppercase aliases for the runner
            data_df["SYMBOL"] = data_df["symbol"]
            data_df["OPEN"]   = data_df["open"]
            data_df["HIGH"]   = data_df["high"]
            data_df["LOW"]    = data_df["low"]
            data_df["CLOSE"]  = data_df["close"]
            data_df["TOTTRDQTY"] = data_df["volume"]
            data_df["TOTTRDVAL"] = data_df["turnover"]
            data_df["DATE"]   = data_df.get("DATE", data_df.get("date"))
            data_df = data_df.sort_values("DATE").reset_index(drop=True)

        results[symbol_upper] = {
            "symbol": symbol_upper,
            "dates_requested": sorted(dates),
            "dates_fetched": len(rows),
            "fetch_errors": fetch_errors,
            "data": data_df,
        }

    return results
