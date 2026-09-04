"""
Case Backtest Runner — Phase 8
================================

Runs applicable detection logic against real SEBI case bhavcopy data.

IMPORTANT: This runner does NOT run the full Sentinel detectors against
bhavcopy data, because those detectors require account-level Order objects
that do not exist in public historical data. See the plan for full reasoning.

Instead, it runs a PRICE/VOLUME ANOMALY ADAPTER — a simplified signal that
asks:
  "On this scrip's manipulation days, did daily OHLCV show the kind of
   price × volume anomaly that the coordinated_pump.py detector was designed
   to catch at the intraday level?"

The adapter uses the same thresholds as coordinated_pump.py:
  - VOLUME_SPIKE_MULTIPLE = 5.0 (volume must be 5× the 30-day rolling average)
  - Price change >= 3% on the same day (consistent with "price pushed up")

This is documented as a METHODOLOGICAL APPROXIMATION in the report. It does
NOT test the full detector logic (which requires account-level data). It tests
only whether the price/volume surface of the manipulation was anomalous enough
to be visible in daily OHLCV — a necessary but not sufficient condition for
the full detector to fire.

What a HIT means: The anomaly adapter would have flagged this day as suspicious.
What a MISS means: The price/volume signals on manipulation days were not
  distinguishable from normal trading in this scrip.
What UNTESTABLE means: Data not available (wrong exchange, scrip delisted, etc.)
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# Thresholds (match coordinated_pump.py constants)
VOLUME_SPIKE_MULTIPLE: float = 5.0
PRICE_CHANGE_PCT_THRESHOLD: float = 3.0  # % daily price change to count

# Minimum rolling window for baseline computation
MIN_BASELINE_DAYS: int = 10


@dataclass
class DailyAnomalyResult:
    """Result of the price/volume anomaly adapter for one day."""
    date: date
    symbol: str
    close: float
    volume: float
    price_change_pct: Optional[float]
    volume_multiple: Optional[float]   # volume / rolling_avg_volume
    is_volume_spike: bool
    is_price_move: bool
    is_flagged: bool   # True if BOTH volume spike AND price move
    explanation: str


@dataclass
class CaseResult:
    """Full result for one case (all scrips, all days)."""
    case_id: str
    verdict: str   # TESTABLE_HIT | TESTABLE_MISS | PARTIALLY_TESTABLE | UNTESTABLE
    scrip_results: dict = field(default_factory=dict)  # symbol → list[DailyAnomalyResult]
    flagged_days: dict = field(default_factory=dict)   # symbol → list[date]
    data_quality_note: str = ""
    summary: str = ""


def compute_daily_anomalies(
    df: pd.DataFrame,
    symbol: str,
    manipulation_start: date,
    manipulation_end: date,
) -> list[DailyAnomalyResult]:
    """
    Run the price/volume anomaly adapter against bhavcopy OHLCV data.

    Parameters
    ----------
    df : DataFrame
        Bhavcopy data for the symbol, sorted by DATE.
        Must have columns: DATE, OPEN, HIGH, LOW, CLOSE, TOTTRDQTY.
    symbol : str
        Scrip symbol for logging.
    manipulation_start, manipulation_end : date
        The SEBI-documented investigation period. Days within this range
        are the "manipulation window" — we look for anomalies here.

    Returns
    -------
    list[DailyAnomalyResult] for every row in df.
    """
    if df is None or df.empty:
        return []

    df = df.copy().sort_values("DATE").reset_index(drop=True)

    # Rolling 30-day average volume (use all available data for baseline,
    # not just the manipulation period — we want the pre-manipulation baseline)
    df["volume_rolling_30d"] = (
        df["TOTTRDQTY"]
        .rolling(window=30, min_periods=MIN_BASELINE_DAYS)
        .mean()
        .shift(1)   # shift(1) = use PRIOR days' average, not including today
    )

    # Daily price change % relative to previous close
    df["prev_close"] = df["CLOSE"].shift(1)
    df["price_change_pct"] = (
        (df["CLOSE"] - df["prev_close"]) / df["prev_close"].abs() * 100
    )

    results: list[DailyAnomalyResult] = []
    for _, row in df.iterrows():
        d = row["DATE"]
        is_manipulation_window = (
            manipulation_start <= d <= manipulation_end
        )

        vol = row["TOTTRDQTY"]
        baseline_vol = row["volume_rolling_30d"]
        price_chg = row.get("price_change_pct")

        # Volume multiple
        if pd.isna(baseline_vol) or baseline_vol == 0:
            volume_multiple = None
            is_volume_spike = False
        else:
            volume_multiple = vol / baseline_vol
            is_volume_spike = volume_multiple >= VOLUME_SPIKE_MULTIPLE

        # Price move
        if pd.isna(price_chg):
            is_price_move = False
            price_chg = None
        else:
            is_price_move = abs(price_chg) >= PRICE_CHANGE_PCT_THRESHOLD

        is_flagged = is_volume_spike and is_price_move

        # Build explanation
        parts = []
        if volume_multiple is not None:
            parts.append(
                f"Volume {volume_multiple:.1f}× 30-day avg "
                f"({'SPIKE' if is_volume_spike else 'normal'})"
            )
        else:
            parts.append("Volume baseline insufficient (< 10 days data)")
        if price_chg is not None:
            parts.append(
                f"Price change {price_chg:+.2f}% "
                f"({'MOVE' if is_price_move else 'normal'})"
            )

        flag_label = "FLAGGED" if is_flagged else (
            "IN_MANIPULATION_WINDOW_NOT_FLAGGED"
            if is_manipulation_window else "clean"
        )
        explanation = f"[{flag_label}] " + "; ".join(parts)

        results.append(DailyAnomalyResult(
            date=d,
            symbol=symbol,
            close=row["CLOSE"],
            volume=vol,
            price_change_pct=price_chg,
            volume_multiple=volume_multiple,
            is_volume_spike=is_volume_spike,
            is_price_move=is_price_move,
            is_flagged=is_flagged,
            explanation=explanation,
        ))

    return results


def run_case(case, pull_results: dict[str, dict]) -> CaseResult:
    """
    Run the anomaly adapter for a single SEBI case across its scrips.

    Parameters
    ----------
    case : SEBICase from sebi_case_catalog.py
    pull_results : dict mapping scrip symbol → pull result dict
        (as returned by historical_data_puller.pull_bhavcopy_for_symbol)

    Returns
    -------
    CaseResult
    """
    if case.testability_verdict == "UNTESTABLE":
        return CaseResult(
            case_id=case.case_id,
            verdict="UNTESTABLE",
            summary=case.untestable_reason,
        )

    scrip_results: dict[str, list[DailyAnomalyResult]] = {}
    flagged_days: dict[str, list[date]] = {}
    data_quality_notes: list[str] = []

    for symbol, pull in pull_results.items():
        df = pull.get("data")
        n_fetched = pull.get("trading_days_fetched", 0)
        n_errors = len(pull.get("fetch_errors", []))

        if df is None or df.empty:
            data_quality_notes.append(
                f"{symbol}: 0 trading days fetched "
                f"({n_errors} fetch errors). UNTESTABLE for this scrip."
            )
            scrip_results[symbol] = []
            flagged_days[symbol] = []
            continue

        if n_errors > 0:
            data_quality_notes.append(
                f"{symbol}: {n_fetched} days fetched, "
                f"{n_errors} fetch errors (logged in pull_results)."
            )

        results = compute_daily_anomalies(
            df=df,
            symbol=symbol,
            manipulation_start=case.investigation_start,
            manipulation_end=case.investigation_end,
        )
        scrip_results[symbol] = results

        # Separate flagged days within the manipulation window
        flagged = [
            r.date for r in results
            if r.is_flagged
            and case.investigation_start <= r.date <= case.investigation_end
        ]
        flagged_days[symbol] = flagged

    # Determine overall verdict
    any_flagged = any(len(v) > 0 for v in flagged_days.values())
    all_empty = all(len(v) == 0 for v in scrip_results.values())

    if all_empty:
        verdict = "UNTESTABLE"
        summary_verdict = (
            "No data could be fetched for any scrip in this case. "
            "Likely delisted or wrong exchange."
        )
    elif any_flagged:
        # Count total flagged days vs manipulation window days
        total_manipulation_days = sum(
            len([r for r in results
                 if case.investigation_start <= r.date <= case.investigation_end])
            for results in scrip_results.values()
        )
        total_flagged = sum(len(v) for v in flagged_days.values())
        verdict = "TESTABLE_HIT"
        summary_verdict = (
            f"Price/volume anomaly adapter FLAGGED {total_flagged} of "
            f"{total_manipulation_days} manipulation-window trading days "
            f"across {len([s for s, v in flagged_days.items() if v])} scrip(s). "
            f"See per-day detail in scrip_results."
        )
    else:
        total_manipulation_days = sum(
            len([r for r in results
                 if case.investigation_start <= r.date <= case.investigation_end])
            for results in scrip_results.values()
        )
        verdict = "TESTABLE_MISS"
        summary_verdict = (
            f"Price/volume anomaly adapter did NOT flag any days in the "
            f"manipulation window ({total_manipulation_days} days tested). "
            f"This does NOT mean manipulation did not occur — SEBI's finding "
            f"stands. It means the daily OHLCV signal was not anomalous enough "
            f"to cross the volume spike + price move thresholds."
        )

    return CaseResult(
        case_id=case.case_id,
        verdict=verdict,
        scrip_results=scrip_results,
        flagged_days=flagged_days,
        data_quality_note="\n".join(data_quality_notes) if data_quality_notes else "",
        summary=summary_verdict,
    )
