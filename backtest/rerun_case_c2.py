"""
Re-run Case C2 (PUMP-DUMP-2017-2020) only, with the delivery-circuit fix applied.

The original Phase 8 run logged 0 trading days fetched for all 5 scrips due to
two compounding bugs in nse_bhavcopy.py / resilience.py (see commit eae832c).
This script re-runs that case alone so we can get a valid result.

Usage:
    cd D:\\Sentinel
    python -m backtest.rerun_case_c2
"""

import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date

os.makedirs("backtest/results", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("backtest/results/rerun_c2.log", mode="w"),
    ],
)
logger = logging.getLogger(__name__)

from backtest.sebi_case_catalog import CASES
from backtest.historical_data_puller import pull_bhavcopy_for_symbol
from backtest.case_backtest_runner import run_case

CASE_C2 = next(c for c in CASES if c.case_id == "PUMP-DUMP-2017-2020")

SCRIPS = {
    "MAURIUDYOG": ("Mauria Udyog Ltd.",    date(2018, 1, 1), date(2019, 12, 31)),
    "7NRRETAIL":  ("7NR Retail Ltd.",      date(2018, 1, 1), date(2019, 12, 31)),
    "GBLIND":     ("GBL Industries Ltd.",  date(2017, 6, 1), date(2019, 6, 30)),
    "VISHALFAB":  ("Vishal Fabrics Ltd.",  date(2018, 1, 1), date(2020, 3, 31)),
    "DARJROPE":   ("Darjeeling Ropeway.", date(2018, 1, 1), date(2019, 12, 31)),
}


def _json_safe(obj):
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(type(obj))


if __name__ == "__main__":
    logger.info("Re-running Case C2: PUMP-DUMP-2017-2020 (fix: delivery_circuit isolation)")
    logger.info("Scrips: %s", list(SCRIPS.keys()))

    scrips_to_pull = {}
    for symbol, (name, s, e) in SCRIPS.items():
        logger.info("Pulling %s (%s): %s → %s", symbol, name, s, e)
        pull = pull_bhavcopy_for_symbol(symbol=symbol, start=s, end=e)
        scrips_to_pull[symbol] = pull
        fetched = pull.get("trading_days_fetched", 0)
        errors  = len(pull.get("fetch_errors", []))
        logger.info(
            "  %s: %d days fetched, %d errors",
            symbol, fetched, errors
        )

    result = run_case(CASE_C2, scrips_to_pull)

    # Serialise
    scrip_summary = {}
    for symbol, daily_list in result.scrip_results.items():
        in_window = [
            r for r in daily_list
            if CASE_C2.investigation_start <= r.date <= CASE_C2.investigation_end
        ]
        flagged = [r for r in in_window if r.is_flagged]
        scrip_summary[symbol] = {
            "days_in_window": len(in_window),
            "days_flagged": len(flagged),
            "flagged_detail": [
                {
                    "date": r.date.isoformat(),
                    "close": r.close,
                    "volume": r.volume,
                    "price_change_pct": r.price_change_pct,
                    "volume_multiple": r.volume_multiple,
                    "explanation": r.explanation,
                }
                for r in flagged
            ],
        }

    output = {
        "rerun_reason": (
            "Original run invalid due to delivery-circuit bug (commit eae832c). "
            "This result replaces PUMP-DUMP-2017-2020_result.json."
        ),
        "case_id": CASE_C2.case_id,
        "run_verdict": result.verdict,
        "summary": result.summary,
        "data_quality_note": result.data_quality_note,
        "scrip_results": scrip_summary,
    }

    out_path = "backtest/results/PUMP-DUMP-2017-2020_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, default=_json_safe, indent=2)

    logger.info("Saved to %s", out_path)
    logger.info("")
    logger.info("=== CASE C2 RE-RUN RESULT ===")
    logger.info("Verdict: %s", result.verdict)
    logger.info("Summary: %s", result.summary)
    logger.info("")
    for sym, stats in scrip_summary.items():
        logger.info(
            "  %s: %d days in window, %d flagged",
            sym, stats["days_in_window"], stats["days_flagged"]
        )
    logger.info("=============================")
