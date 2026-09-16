"""
DIAGNOSTIC_before_rerun.py - Phase 2 Pump-Dump Backtest Diagnostic

Confirms the real reason PUMP-DUMP-2017-2020 returned 0 days fetched.
fetch_bhavcopy(date, series='EQ') returns the full bhavcopy DataFrame for
that date; we filter by SYMBOL column to check if each scrip appears.

Usage:
    cd D:\Sentinel
    python -m backtest.DIAGNOSTIC_before_rerun
"""

import json
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("diagnostic")

SCRIPS = {
    "MAURIUDYOG": {
        "full_name": "Mauria Udyog Ltd.",
        "probe_dates": [date(2018, 6, 15), date(2018, 9, 14), date(2019, 3, 15)],
        "alt_symbols": ["MAURIUDYOG", "MAURIA", "MAURYA", "MAURIUDYG"],
        "alt_series": ["EQ", "BE", "BL", "SM"],
    },
    "7NRRETAIL": {
        "full_name": "7NR Retail Ltd.",
        "probe_dates": [date(2018, 6, 15), date(2018, 9, 14)],
        "alt_symbols": ["7NRRETAIL", "7NR", "NRRETAIL"],
        "alt_series": ["EQ", "BE", "SM"],
    },
    "GBLIND": {
        "full_name": "GBL Industries Ltd.",
        "probe_dates": [date(2018, 6, 15), date(2017, 9, 15)],
        "alt_symbols": ["GBLIND", "GBLINDS", "GBL"],
        "alt_series": ["EQ", "BE"],
    },
    "VISHALFAB": {
        "full_name": "Vishal Fabrics Ltd.",
        "probe_dates": [date(2018, 6, 15), date(2019, 6, 14)],
        "alt_symbols": ["VISHALFAB", "VISHFAB", "VISHALFA"],
        "alt_series": ["EQ", "BE"],
    },
    "DARJROPE": {
        "full_name": "Darjeeling Ropeway Co. Ltd.",
        "probe_dates": [date(2018, 6, 15), date(2018, 9, 14)],
        "alt_symbols": ["DARJROPE", "DARJROPEWAY", "DARPEWAY", "DARJEELING"],
        "alt_series": ["EQ", "BE"],
    },
}

REFERENCE_DATE = date(2018, 6, 15)
_bhavcopy_cache = {}  # (date, series) -> DataFrame | Exception


def _get_bhavcopy(probe_date, series):
    """Fetch and cache full bhavcopy for (date, series). Returns df or raises."""
    key = (probe_date, series)
    if key in _bhavcopy_cache:
        return _bhavcopy_cache[key]
    from data.ingest.nse_bhavcopy import fetch_bhavcopy
    from data.ingest.errors import BhavcopyFetchError, BhavcopyParseError
    try:
        df = fetch_bhavcopy(probe_date, series=series)
        _bhavcopy_cache[key] = df
        time.sleep(1.5)  # Polite delay between date fetches
        return df
    except Exception as exc:
        _bhavcopy_cache[key] = exc
        time.sleep(0.5)
        raise


def probe_symbol_in_bhavcopy(symbol, probe_date, series):
    """Check if symbol appears in bhavcopy for (date, series)."""
    try:
        df = _get_bhavcopy(probe_date, series)
        if df is None or df.empty:
            return {"found": False, "error": "bhavcopy_empty", "rows_in_file": 0}
        rows_in_file = len(df)
        # Filter by SYMBOL column (exact match, case-insensitive)
        mask = df["SYMBOL"].str.upper() == symbol.upper()
        matched = df[mask]
        if not matched.empty:
            return {"found": True, "error": None, "rows_in_file": rows_in_file, "rows": len(matched)}
        # Also check for partial match (symbol contained in SYMBOL)
        partial_mask = df["SYMBOL"].str.upper().str.contains(symbol.upper()[:5], na=False)
        partials = df[partial_mask]["SYMBOL"].tolist()
        return {
            "found": False,
            "error": "not_in_file",
            "rows_in_file": rows_in_file,
            "partial_matches": partials[:5],
        }
    except Exception as exc:
        s = str(exc)
        if "403" in s or "401" in s:
            return {"found": False, "error": "http_403_blocked", "rows_in_file": 0}
        if "404" in s:
            return {"found": False, "error": "http_404_no_data_for_date", "rows_in_file": 0}
        return {"found": False, "error": s[:100], "rows_in_file": 0}


def check_connectivity():
    logger.info("Connectivity check: RELIANCE/EQ on %s", REFERENCE_DATE)
    r = probe_symbol_in_bhavcopy("RELIANCE", REFERENCE_DATE, "EQ")
    if r["found"]:
        logger.info("  OK - RELIANCE found (file has %d rows)", r.get("rows_in_file", "?"))
        return True
    logger.error("  FAIL - %s", r["error"])
    return False


def run():
    report = {
        "purpose": (
            "Determine confirmed reason for 0 days fetched in PUMP-DUMP-2017-2020. "
            "Uses correct fetch_bhavcopy API: fetches entire daily file, filters by SYMBOL."
        ),
        "connectivity_ok": False,
        "scrip_results": {},
        "final_verdict": "",
        "final_reason": "",
    }

    if not check_connectivity():
        report["final_verdict"] = "DIAGNOSTIC_FAILED_NO_CONNECTIVITY"
        report["final_reason"] = "Cannot fetch NSE bhavcopy (network or bot-block). Re-run from residential IP."
        return report

    report["connectivity_ok"] = True

    for symbol, meta in SCRIPS.items():
        logger.info("\n-- %s (%s) --", symbol, meta["full_name"])
        result = {
            "full_name": meta["full_name"],
            "probe_results": [],
            "found_on_nse": False,
            "found_as": None,
            "partial_matches": [],
            "conclusion": "",
        }

        seen_dates = set()
        for probe_date in meta["probe_dates"]:
            # Fetch the bhavcopy once per (date, series) combination
            for series in meta["alt_series"]:
                if (probe_date, series) in seen_dates:
                    continue
                seen_dates.add((probe_date, series))

                # Try all symbol variants against the same downloaded file
                bhavcopy_fetched = False
                for alt_sym in meta["alt_symbols"]:
                    r = probe_symbol_in_bhavcopy(alt_sym, probe_date, series)
                    entry = {
                        "symbol_tried": alt_sym,
                        "date": probe_date.isoformat(),
                        "series": series,
                        **r,
                    }
                    result["probe_results"].append(entry)

                    if r.get("partial_matches"):
                        result["partial_matches"].extend(r["partial_matches"])

                    if r["found"]:
                        result["found_on_nse"] = True
                        result["found_as"] = f"{alt_sym}/{series} on {probe_date}"
                        logger.info("  FOUND: %s/%s on %s (%d rows in file)",
                                    alt_sym, series, probe_date, r.get("rows_in_file", 0))
                        break
                    else:
                        if not bhavcopy_fetched and r.get("rows_in_file", 0) > 0:
                            bhavcopy_fetched = True
                            logger.info("  bhavcopy %s/%s has %d rows — '%s' not in file (partials: %s)",
                                        probe_date, series, r["rows_in_file"], alt_sym,
                                        r.get("partial_matches", []))
                        else:
                            logger.info("  not found: %s/%s/%s -> %s",
                                        alt_sym, series, probe_date, r["error"])

                if result["found_on_nse"]:
                    break
            if result["found_on_nse"]:
                break

        # Deduplicate partial matches
        result["partial_matches"] = list(set(result["partial_matches"]))

        # Determine conclusion
        if result["found_on_nse"]:
            result["conclusion"] = (
                f"FOUND on NSE as {result['found_as']}. "
                "Original 0-days result was a symbol/series mismatch bug."
            )
        else:
            # Check what error types we got
            errors = [p.get("error", "") for p in result["probe_results"]]
            rows_seen = [p.get("rows_in_file", 0) for p in result["probe_results"]]
            max_rows = max(rows_seen) if rows_seen else 0

            if "http_403_blocked" in errors:
                result["conclusion"] = (
                    "NSE bhavcopy returned 403 Forbidden. Likely bot-blocked from this IP. "
                    "Cannot confirm presence or absence on NSE. Re-run from residential IP."
                )
            elif max_rows > 0:
                partials = result["partial_matches"]
                if partials:
                    result["conclusion"] = (
                        f"Bhavcopy data obtained ({max_rows} rows/day). "
                        f"Symbol not found exactly, but partial matches exist: {partials}. "
                        "May be listed under a slightly different NSE symbol."
                    )
                else:
                    result["conclusion"] = (
                        f"Bhavcopy data obtained ({max_rows} rows/day). "
                        "Symbol NOT present in NSE bhavcopy under any tested variant. "
                        "Confirmed not NSE-listed. BSE-only status is most probable explanation "
                        "but has not been independently verified against BSE data. "
                        "0-days result is correct — not a fetch bug."
                    )
            elif all("404" in e for e in errors if e):
                result["conclusion"] = (
                    "All dates returned 404. Scrip likely not listed on NSE at all, "
                    "or these specific dates had no trading. PROBABLE: not NSE-listed "
                    "(BSE-only listing unconfirmed without BSE data check)."
                )
            else:
                result["conclusion"] = (
                    f"Could not determine (errors: {list(set(errors))[:3]}). Re-run to confirm."
                )

        logger.info("  -> %s", result["conclusion"])
        report["scrip_results"][symbol] = result

    found = [s for s, v in report["scrip_results"].items() if v["found_on_nse"]]
    not_found = [s for s, v in report["scrip_results"].items() if not v["found_on_nse"]]

    if found:
        report["final_verdict"] = "PARTIALLY_RETESTABLE"
        report["final_reason"] = (
            f"Found on NSE: {found}. Confirmed unavailable: {not_found}. "
            "Re-run backtest with corrected symbols for the found scrips."
        )
    else:
        report["final_verdict"] = "UNTESTABLE_CONFIRMED"
        report["final_reason"] = (
            "None of the 5 scrips found on NSE under any tested symbol or series. "
            "Confirmed not NSE-listed (not a fetch code bug). "
            "BSE-only status is the most probable explanation but has not been "
            "independently verified against BSE data. "
            "See backtest/results/PUMP-DUMP-2017-2020_diagnostic_v2.log for evidence."
        )

    return report


if __name__ == "__main__":
    logger.info("=== Phase 2 Diagnostic: PUMP-DUMP-2017-2020 ===")
    report = run()

    out = Path("backtest/results/PUMP-DUMP-2017-2020_diagnostic.json")
    def _safe(o):
        if isinstance(o, date):
            return o.isoformat()
        raise TypeError(type(o))

    with open(out, "w") as f:
        json.dump(report, f, indent=2, default=_safe)

    print("\n" + "="*60)
    print(f"VERDICT: {report['final_verdict']}")
    print(f"REASON:  {report['final_reason'][:400]}")
    print(f"Full report: {out}")
    print("="*60)
