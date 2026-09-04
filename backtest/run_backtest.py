"""
Phase 8 Backtest — Main Execution Script
=========================================

Run this script to execute the full Phase 8 backtest:
  1. Pull real bhavcopy data for SEBI case scrips
  2. Run the anomaly adapter on each case
  3. Pull negative control data (large-cap stocks, clean days)
  4. Compute false positive rate
  5. Write raw results to backtest/results/ (JSON)
  6. Print a summary to stdout (REPORT.md is written separately)

Usage:
    cd D:\\Sentinel
    python -m backtest.run_backtest

This script does NOT modify the database. It is purely analytical.

Runtime estimate: 20-60 minutes depending on NSE archive response times
and the number of trading days across all cases. The retry-with-backoff
layer (Phase 6) adds latency on transient failures.
"""

import json
import logging
import os
import sys
from datetime import date, datetime

# Ensure the repo root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.sebi_case_catalog import CASES, NEGATIVE_CONTROL_STOCKS, NEGATIVE_CONTROL_DATES
from backtest.historical_data_puller import pull_bhavcopy_for_symbol, pull_negative_controls
from backtest.case_backtest_runner import run_case
from backtest.negative_control_runner import run_negative_controls, summarize_negative_controls

# ── Output directory — must exist before logging setup ───────────────────────

os.makedirs("backtest/results", exist_ok=True)

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("backtest/results/run.log", mode="w"),
    ],
)
logger = logging.getLogger(__name__)


def _json_serializable(obj):
    """Make datetimes and dates JSON-serializable."""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def run_cases() -> dict:
    """Run anomaly adapter for all SEBI cases. Return raw results."""
    all_case_results = {}

    for case in CASES:
        logger.info(f"\n{'='*70}")
        logger.info(f"CASE: {case.case_id}")
        logger.info(f"Order: {case.order_reference}")
        logger.info(f"Period: {case.investigation_start} to {case.investigation_end}")
        logger.info(f"Verdict (pre-run): {case.testability_verdict}")
        logger.info(f"{'='*70}")

        if case.testability_verdict == "UNTESTABLE":
            logger.info(f"Skipping {case.case_id}: {case.untestable_reason}")
            case_result = run_case(case, {})
            all_case_results[case.case_id] = _serialize_case_result(case, case_result)
            continue

        # Determine which scrips to pull (use nse_symbol if available,
        # otherwise we note it's untestable via this pipeline)
        scrips_to_pull: dict[str, dict] = {}

        if case.case_id == "KIL-2019":
            # Kavit Industries traded on BSE, not NSE.
            # We attempt to find it on NSE anyway to be thorough.
            # It is expected to not be found (BSE-only scrip).
            logger.info(
                "KIL-2019: Kavit Industries is a BSE-listed scrip. "
                "Attempting NSE lookup — expected to fail (will be recorded). "
                "A BSE bhavcopy fetcher would be needed for proper testing."
            )
            pull = pull_bhavcopy_for_symbol(
                symbol="KAVIT",  # NSE symbol if it existed; likely not listed
                start=case.investigation_start,
                end=case.investigation_end,
            )
            scrips_to_pull["KAVIT"] = pull

        elif case.case_id == "PUMP-DUMP-2017-2020":
            # Attempt each scrip in the pump-dump cluster.
            # NSE symbols for these illiquid small-caps need verification.
            # Some may be delisted — we record which ones fail.
            scrip_candidates = {
                "MAURIUDYOG": ("Mauria Udyog Ltd.", date(2018, 1, 1), date(2019, 12, 31)),
                "7NRRETAIL": ("7NR Retail Ltd.", date(2018, 1, 1), date(2019, 12, 31)),
                "GBLIND": ("GBL Industries Ltd.", date(2017, 6, 1), date(2019, 6, 30)),
                "VISHALFAB": ("Vishal Fabrics Ltd.", date(2018, 1, 1), date(2020, 3, 31)),
                # Darjeeling Ropeway likely not on NSE — try anyway
                "DARJROPE": ("Darjeeling Ropeway Co.", date(2018, 1, 1), date(2019, 12, 31)),
            }

            # Use the case's overall investigation period for all scrips
            # (the scrip-specific sub-periods above are approximate;
            #  the SEBI order spans the full 2017-2020 window overall)
            for symbol, (name, s, e) in scrip_candidates.items():
                logger.info(f"Pulling {symbol} ({name}): {s} to {e}")
                pull = pull_bhavcopy_for_symbol(
                    symbol=symbol,
                    start=s,
                    end=e,
                )
                scrips_to_pull[symbol] = pull

        case_result = run_case(case, scrips_to_pull)
        all_case_results[case.case_id] = _serialize_case_result(case, case_result)

        # Save raw per-case results
        out_path = f"backtest/results/{case.case_id}_result.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(all_case_results[case.case_id], f, default=_json_serializable, indent=2)
        logger.info(f"Saved case results to {out_path}")

    return all_case_results


def run_controls() -> dict:
    """Run negative control for large-cap stocks."""
    logger.info(f"\n{'='*70}")
    logger.info("NEGATIVE CONTROLS")
    logger.info(f"Symbols: {NEGATIVE_CONTROL_STOCKS}")
    logger.info(f"Dates: {len(NEGATIVE_CONTROL_DATES)} trading days")
    logger.info(f"{'='*70}")

    pull_results = pull_negative_controls(NEGATIVE_CONTROL_STOCKS, NEGATIVE_CONTROL_DATES)
    control_results = run_negative_controls(pull_results)
    summary = summarize_negative_controls(control_results)

    # Serialize
    serialized = {
        "summary": summary,
        "per_symbol": {
            sym: {
                "dates_tested": r.dates_tested,
                "dates_flagged": r.dates_flagged,
                "false_positive_rate": r.false_positive_rate,
                "flagged_dates": [d.isoformat() for d in r.flagged_dates],
                "data_quality_note": r.data_quality_note,
            }
            for sym, r in control_results.items()
        },
    }

    out_path = "backtest/results/negative_controls.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(serialized, f, default=_json_serializable, indent=2)
    logger.info(f"Saved control results to {out_path}")

    return serialized


def _serialize_case_result(case, result) -> dict:
    """Convert a CaseResult to a JSON-serializable dict."""
    scrip_summary = {}
    for symbol, daily_list in result.scrip_results.items():
        flagged_in_window = [
            {
                "date": r.date.isoformat(),
                "close": r.close,
                "volume": r.volume,
                "price_change_pct": r.price_change_pct,
                "volume_multiple": r.volume_multiple,
                "explanation": r.explanation,
            }
            for r in daily_list
            if r.is_flagged
            and case.investigation_start <= r.date <= case.investigation_end
        ]
        all_window_days = [
            r for r in daily_list
            if case.investigation_start <= r.date <= case.investigation_end
        ]
        scrip_summary[symbol] = {
            "days_in_manipulation_window": len(all_window_days),
            "days_flagged": len(flagged_in_window),
            "flagged_days_detail": flagged_in_window,
        }

    return {
        "case_id": case.case_id,
        "order_reference": case.order_reference,
        "order_date": case.order_date.isoformat(),
        "alleged_pattern": case.alleged_pattern,
        "investigation_period": {
            "start": case.investigation_start.isoformat(),
            "end": case.investigation_end.isoformat(),
        },
        "testability_verdict_pre_run": case.testability_verdict,
        "run_verdict": result.verdict,
        "summary": result.summary,
        "data_quality_note": result.data_quality_note,
        "inapplicable_detectors": case.inapplicable_detectors,
        "scrip_results": scrip_summary,
    }


def print_summary(case_results: dict, control_results: dict) -> None:
    """Print a human-readable summary to stdout."""
    print("\n" + "="*70)
    print("PHASE 8 BACKTEST — RESULTS SUMMARY")
    print("="*70)
    print(f"Cases attempted: {len(case_results)}")
    print()

    for case_id, result in case_results.items():
        print(f"  [{result['run_verdict']}] {case_id}")
        print(f"    Order: {result['order_reference'][:80]}...")
        print(f"    Period: {result['investigation_period']['start']} to "
              f"{result['investigation_period']['end']}")
        print(f"    Summary: {result['summary'][:120]}...")
        if result["data_quality_note"]:
            print(f"    Data quality: {result['data_quality_note'][:80]}...")
        print()

    ctrl = control_results.get("summary", {})
    print("NEGATIVE CONTROLS:")
    print(f"  Total days tested: {ctrl.get('total_days_tested', 'N/A')}")
    print(f"  Days flagged: {ctrl.get('total_days_flagged', 'N/A')}")
    print(f"  Overall false positive rate: "
          f"{ctrl.get('overall_fp_rate', 0):.1%}")
    print()

    per_sym = ctrl.get("per_symbol", {})
    for sym, stats in per_sym.items():
        if isinstance(stats, dict):
            print(f"  {sym}: {stats.get('dates_flagged', 0)} of "
                  f"{stats.get('dates_tested', 0)} days flagged "
                  f"({stats.get('fp_rate', 0):.1%})")
    print("="*70)


if __name__ == "__main__":
    logger.info("Phase 8 Backtest starting")
    logger.info(f"Cases to process: {[c.case_id for c in CASES]}")
    logger.info(
        f"Negative control: {NEGATIVE_CONTROL_STOCKS}, "
        f"{len(NEGATIVE_CONTROL_DATES)} dates"
    )

    case_results = run_cases()
    control_results = run_controls()
    print_summary(case_results, control_results)

    logger.info("Phase 8 Backtest complete. See backtest/results/ for raw output.")
    logger.info("Run backtest/generate_report.py next to produce REPORT.md.")
