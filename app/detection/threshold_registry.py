"""
app/detection/threshold_registry.py
=====================================
Single registry of every numeric threshold used in Sentinel detectors.

Each entry has:
  name        — identifier used in code and API
  value       — the actual number passed to the detector
  basis       — "UNVALIDATED_HEURISTIC" or "VALIDATED_CITATION"
  citation    — real order number + date + URL, or "none"
  validated   — True only when citation is a real, verifiable SEBI order
  notes       — honest explanation of where the number came from

HARD RULE: If citation == "none", basis MUST be "UNVALIDATED_HEURISTIC".
           No threshold may be labelled "SEBI CASE" without a matching entry
           here that has validated=True and a real citation URL.

Reviewer note (2026-11-10): Every threshold in the original detection_worker.py
was labelled "SEBI CASE" with comments like "cited in multiple adjudication orders".
No order number, date, or URL was provided. After searching SEBI's orders archive
at https://www.sebi.gov.in/enforcement/orders/all-orders.html, we could not find
the specific quantitative thresholds cited (e.g., "z>2.5σ" or "volume 3×") in any
published order. All thresholds are therefore re-labelled UNVALIDATED_HEURISTIC.

If a future analyst locates a verifiable SEBI order that cites a specific quantitative
threshold, they should:
  1. Add the order number, date, and URL to the citation field.
  2. Set validated=True.
  3. Remove the UNVALIDATED_HEURISTIC label.
"""

from typing import Any, Dict

# ---------------------------------------------------------------------------
# Registry entries
# ---------------------------------------------------------------------------

THRESHOLDS: Dict[str, Dict[str, Any]] = {

    # ── Volume spike thresholds ───────────────────────────────────────────
    "volume_spike_medium_x": {
        "value": 3.0,
        "basis": "UNVALIDATED_HEURISTIC",
        "citation": "none",
        "validated": False,
        "notes": (
            "Originally labelled 'SEBI CASE — cited in multiple adjudication orders'. "
            "No specific order with this exact threshold (3× avg daily volume) was found. "
            "Common practitioner heuristic; not tied to any verified SEBI filing."
        ),
    },
    "volume_spike_high_x": {
        "value": 5.0,
        "basis": "UNVALIDATED_HEURISTIC",
        "citation": "none",
        "validated": False,
        "notes": "Same provenance as volume_spike_medium_x.",
    },
    "volume_spike_critical_x": {
        "value": 10.0,
        "basis": "UNVALIDATED_HEURISTIC",
        "citation": "none",
        "validated": False,
        "notes": "Same provenance as volume_spike_medium_x.",
    },

    # ── Price z-score thresholds ──────────────────────────────────────────
    "zscore_medium": {
        "value": 2.5,
        "basis": "UNVALIDATED_HEURISTIC",
        "citation": "none",
        "validated": False,
        "notes": (
            "Originally labelled 'SEBI CASE — 2.5σ cited in multiple orders'. "
            "z-score of 2.5 is a standard statistical outlier threshold (≈1.2% tail); "
            "it is NOT sourced from a specific SEBI order. 2.5σ is a reasonable "
            "starting point for screening but has NOT been calibrated against confirmed "
            "Indian market manipulation cases."
        ),
    },
    "zscore_high": {
        "value": 3.5,
        "basis": "UNVALIDATED_HEURISTIC",
        "citation": "none",
        "validated": False,
        "notes": "Same provenance as zscore_medium.",
    },
    "zscore_critical": {
        "value": 5.0,
        "basis": "UNVALIDATED_HEURISTIC",
        "citation": "none",
        "validated": False,
        "notes": "Same provenance as zscore_medium.",
    },

    # ── Thin-volume pump thresholds ───────────────────────────────────────
    "thin_vol_price_pct": {
        "value": 2.0,
        "basis": "UNVALIDATED_HEURISTIC",
        "citation": "none",
        "validated": False,
        "notes": "Practitioner heuristic. Not sourced from any SEBI order.",
    },
    "thin_vol_ratio": {
        "value": 0.5,
        "basis": "UNVALIDATED_HEURISTIC",
        "citation": "none",
        "validated": False,
        "notes": "Practitioner heuristic. Not sourced from any SEBI order.",
    },

    # ── Momentum reversal (pump-and-dump) ────────────────────────────────
    "pump_runup_3d_pct": {
        "value": 5.0,
        "basis": "UNVALIDATED_HEURISTIC",
        "citation": "none",
        "validated": False,
        "notes": (
            "Originally labelled 'SEBI CASE' with a reference to 'Sunrise Foods 2019, "
            "Kailash Auto 2018'. Those cases exist in SEBI's orders archive but the "
            "specific 5% run-up threshold was NOT found as a quoted quantitative "
            "criterion in any available order text. Retained as heuristic only."
        ),
    },
    "dump_reversal_pct": {
        "value": 3.0,
        "basis": "UNVALIDATED_HEURISTIC",
        "citation": "none",
        "validated": False,
        "notes": "Same provenance as pump_runup_3d_pct.",
    },

    # ── OI concentration thresholds ───────────────────────────────────────
    "oi_conc_medium": {
        "value": 0.65,
        "basis": "UNVALIDATED_HEURISTIC",
        "citation": "none",
        "validated": False,
        "notes": "Practitioner heuristic. Not sourced from any SEBI order.",
    },
    "oi_conc_high": {
        "value": 0.80,
        "basis": "UNVALIDATED_HEURISTIC",
        "citation": "none",
        "validated": False,
        "notes": "Same provenance as oi_conc_medium.",
    },

    # ── Sustained volume (multi-day) ──────────────────────────────────────
    "sustained_vol_min_sessions": {
        "value": 3,
        "basis": "UNVALIDATED_HEURISTIC",
        "citation": "none",
        "validated": False,
        "notes": "Practitioner heuristic: 3+ consecutive sessions of abnormal volume.",
    },
    "sustained_vol_ratio": {
        "value": 2.0,
        "basis": "UNVALIDATED_HEURISTIC",
        "citation": "none",
        "validated": False,
        "notes": "Per-session volume must exceed 2× the pre-event baseline avg.",
    },

    # ── General ───────────────────────────────────────────────────────────
    "min_history_days": {
        "value": 10,
        "basis": "UNVALIDATED_HEURISTIC",
        "citation": "none",
        "validated": False,
        "notes": "Minimum bars required for a meaningful standard deviation estimate.",
    },
}

# ---------------------------------------------------------------------------
# Convenience accessors
# ---------------------------------------------------------------------------

def get(name: str) -> Any:
    """Return threshold value by name. Raises KeyError if not found."""
    return THRESHOLDS[name]["value"]


def get_entry(name: str) -> Dict[str, Any]:
    """Return full registry entry by name."""
    return THRESHOLDS[name]


def all_validated() -> bool:
    """True only if every threshold has validated=True (expected: False for now)."""
    return all(v["validated"] for v in THRESHOLDS.values())


def api_view() -> Dict[str, Any]:
    """Return registry in a form suitable for /api/system/detector-status."""
    return {
        "total": len(THRESHOLDS),
        "validated_count": sum(1 for v in THRESHOLDS.values() if v["validated"]),
        "unvalidated_count": sum(1 for v in THRESHOLDS.values() if not v["validated"]),
        "disclaimer": (
            "All thresholds are UNVALIDATED_HEURISTIC. No SEBI order number, date, or URL "
            "has been verified as the source for any numeric threshold in Sentinel. "
            "These are starting points for screening — not findings of manipulation."
        ),
        "thresholds": {
            name: {
                "value": entry["value"],
                "basis": entry["basis"],
                "citation": entry["citation"],
                "validated": entry["validated"],
                "notes": entry["notes"],
            }
            for name, entry in THRESHOLDS.items()
        },
    }

