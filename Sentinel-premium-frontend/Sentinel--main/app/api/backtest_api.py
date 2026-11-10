"""
app/api/backtest_api.py
=======================
GET /api/backtest/results  — real content from backtest/results/*.json
                             and key sections of backtest/REPORT.md
"""
import json
import pathlib
import re
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

_BACKTEST_RESULTS = pathlib.Path(__file__).parent.parent.parent / "backtest" / "results"
_BACKTEST_REPORT  = pathlib.Path(__file__).parent.parent.parent / "backtest" / "REPORT.md"


def _read_result_files() -> list[dict[str, Any]]:
    """Read all *_result.json files in backtest/results/."""
    results = []
    for f in sorted(_BACKTEST_RESULTS.glob("*_result.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data["_source_file"] = f.name
            results.append(data)
        except Exception as exc:
            results.append({"_source_file": f.name, "_read_error": str(exc)})
    return results


def _read_negative_controls() -> dict[str, Any]:
    nc_file = _BACKTEST_RESULTS / "negative_controls.json"
    if not nc_file.exists():
        return {"error": "negative_controls.json not found"}
    try:
        return json.loads(nc_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": str(exc)}


def _extract_report_sections(md: str) -> dict[str, str]:
    """
    Extract key sections from REPORT.md by header name.
    Returns a dict: section_title -> section_text.
    Preserves full granularity — does NOT summarize.
    """
    sections = {}
    current_title = None
    current_lines: list[str] = []

    for line in md.splitlines():
        m = re.match(r'^#{1,3}\s+(.+)$', line)
        if m:
            if current_title is not None:
                sections[current_title] = "\n".join(current_lines).strip()
            current_title = m.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_title:
        sections[current_title] = "\n".join(current_lines).strip()

    return sections


@router.get("/results")
def get_backtest_results():
    """
    Real content from backtest/results/*.json and backtest/REPORT.md.
    Presented per-case, per-detector, hit/miss/untestable with reasons.
    No summarization into a single score — honest granularity preserved.
    """
    case_results = _read_result_files()
    negative_controls = _read_negative_controls()

    # Extract REPORT.md sections
    report_sections: dict[str, str] = {}
    if _BACKTEST_REPORT.exists():
        try:
            md = _BACKTEST_REPORT.read_text(encoding="utf-8")
            report_sections = _extract_report_sections(md)
        except Exception as exc:
            report_sections = {"_read_error": str(exc)}
    else:
        report_sections = {"_error": "REPORT.md not found"}

    # Compute totals from actual data
    total_cases = len(case_results)
    untestable = sum(1 for r in case_results if r.get("run_verdict") == "UNTESTABLE")
    partially_testable = sum(1 for r in case_results if r.get("run_verdict") == "PARTIALLY_TESTABLE")

    return {
        "summary": {
            "total_cases": total_cases,
            "untestable": untestable,
            "partially_testable": partially_testable,
            "testable": total_cases - untestable - partially_testable,
            "negative_control_days": negative_controls.get("summary", {}).get("total_days_tested"),
            "negative_control_fp_rate": negative_controls.get("summary", {}).get("overall_fp_rate"),
            "honest_note": (
                "0 of 6 detectors have been validated against a confirmed real manipulation case. "
                "All SEBI cases in the test set were structurally untestable due to public data limitations. "
                "See per-case details for exact reasons."
            ),
        },
        "cases": case_results,
        "negative_controls": negative_controls,
        "report_sections": report_sections,
        "report_file": str(_BACKTEST_REPORT.resolve()) if _BACKTEST_REPORT.exists() else None,
    }
