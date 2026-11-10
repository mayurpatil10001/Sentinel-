"""
Negative Control Runner — Phase 8
===================================

Runs the same price/volume anomaly adapter used in case_backtest_runner.py
against large, liquid, uncontroversial NSE stocks on normal trading days.

Purpose: establish the FALSE POSITIVE RATE of the anomaly adapter.
A detection rate without a false positive rate is not evidence the system
works — it might just flag everything.

What "false positive" means here:
  The anomaly adapter fires (volume ≥ 5× 30-day avg AND price change ≥ 3%)
  on a day for a large-cap stock where there is no known SEBI enforcement
  action. This could be a legitimate institutional block trade, index
  rebalancing, earnings-related volume, or a genuine (non-manipulative)
  news catalyst.

IMPORTANT CAVEAT documented in REPORT.md:
  A "false positive" in a negative control does NOT mean the detector is
  broken — it means the threshold alone is not sufficient to distinguish
  manipulation from legitimate high-volume events. The full Sentinel
  system adds account-level coordination signals on top of the volume
  signal, which would eliminate most of these false positives in production.
  We cannot run the account-level layer here because the data doesn't exist.
  What we CAN say honestly: on large-cap stocks, the daily OHLCV adapter
  alone fires [N]% of the time on normal days — that baseline matters.
"""

import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd

from backtest.case_backtest_runner import (
    DailyAnomalyResult,
    compute_daily_anomalies,
    VOLUME_SPIKE_MULTIPLE,
    PRICE_CHANGE_PCT_THRESHOLD,
)

logger = logging.getLogger(__name__)


@dataclass
class NegativeControlResult:
    """
    False-positive rate summary for one symbol across all control dates.
    """
    symbol: str
    dates_tested: int
    dates_flagged: int
    false_positive_rate: float   # flagged / tested
    flagged_dates: list[date]
    daily_detail: list[DailyAnomalyResult]
    data_quality_note: str = ""


def run_negative_controls(pull_results: dict[str, dict]) -> dict[str, NegativeControlResult]:
    """
    Run anomaly adapter on negative control stocks.

    Parameters
    ----------
    pull_results : dict
        keyed by symbol, values are pull result dicts from
        historical_data_puller.pull_negative_controls()

    Returns
    -------
    dict keyed by symbol → NegativeControlResult
    """
    results: dict[str, NegativeControlResult] = {}

    for symbol, pull in pull_results.items():
        df = pull.get("data")
        dates_requested = pull.get("dates_requested", [])
        n_errors = len(pull.get("fetch_errors", []))
        note = ""

        if df is None or df.empty:
            note = f"No data fetched for {symbol}. {n_errors} fetch errors."
            results[symbol] = NegativeControlResult(
                symbol=symbol,
                dates_tested=0,
                dates_flagged=0,
                false_positive_rate=0.0,
                flagged_dates=[],
                daily_detail=[],
                data_quality_note=note,
            )
            continue

        if n_errors > 0:
            note = f"{n_errors} fetch errors out of {len(dates_requested)} dates requested."

        # Use a dummy wide range for the manipulation window so that
        # ALL control dates count as "in window" (everything is a potential FP)
        all_dates = sorted(df["DATE"].tolist())
        if all_dates:
            window_start = min(all_dates)
            window_end = max(all_dates)
        else:
            results[symbol] = NegativeControlResult(
                symbol=symbol,
                dates_tested=0,
                dates_flagged=0,
                false_positive_rate=0.0,
                flagged_dates=[],
                daily_detail=[],
                data_quality_note="No dates in data.",
            )
            continue

        daily = compute_daily_anomalies(
            df=df,
            symbol=symbol,
            manipulation_start=window_start,
            manipulation_end=window_end,
        )

        # Only evaluate the specific control dates (not every date in the
        # wider rolling window used for baseline computation)
        control_dates_set = set(dates_requested)
        evaluated = [r for r in daily if r.date in control_dates_set]
        flagged = [r for r in evaluated if r.is_flagged]
        flagged_dates = [r.date for r in flagged]

        fp_rate = len(flagged) / len(evaluated) if evaluated else 0.0

        results[symbol] = NegativeControlResult(
            symbol=symbol,
            dates_tested=len(evaluated),
            dates_flagged=len(flagged),
            false_positive_rate=fp_rate,
            flagged_dates=flagged_dates,
            daily_detail=evaluated,
            data_quality_note=note,
        )

        logger.info(
            f"[NEGATIVE CONTROL] {symbol}: {len(flagged)} of {len(evaluated)} "
            f"control days flagged ({fp_rate:.1%} false positive rate)"
        )

    return results


def summarize_negative_controls(
    results: dict[str, NegativeControlResult]
) -> dict:
    """
    Aggregate false positive statistics across all control symbols.

    Returns a summary dict with:
        overall_fp_rate, total_days_tested, total_days_flagged,
        per_symbol (dict), worst_symbol, best_symbol
    """
    total_tested = sum(r.dates_tested for r in results.values())
    total_flagged = sum(r.dates_flagged for r in results.values())
    overall_fp_rate = total_flagged / total_tested if total_tested else 0.0

    per_symbol = {
        sym: {
            "dates_tested": r.dates_tested,
            "dates_flagged": r.dates_flagged,
            "fp_rate": r.false_positive_rate,
            "flagged_dates": r.flagged_dates,
            "data_quality_note": r.data_quality_note,
        }
        for sym, r in results.items()
    }

    valid = {
        sym: r for sym, r in results.items() if r.dates_tested > 0
    }
    worst = max(valid, key=lambda s: valid[s].false_positive_rate) if valid else None
    best = min(valid, key=lambda s: valid[s].false_positive_rate) if valid else None

    return {
        "total_days_tested": total_tested,
        "total_days_flagged": total_flagged,
        "overall_fp_rate": overall_fp_rate,
        "per_symbol": per_symbol,
        "worst_symbol": worst,
        "best_symbol": best,
    }
