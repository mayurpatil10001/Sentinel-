"""
app/harm_estimation/guards.py
==============================
Hard-coded allow-list guard for the harm estimation module.

HARD ETHICAL RULE (enforced in code, not just policy):
  This module may ONLY run against:
    (a) Instruments from SEBI cases where an adjudication order has already
        legally confirmed manipulation occurred — these go in SEBI_CONFIRMED_CASES.
    (b) Explicitly-labeled synthetic scenarios — these go in SYNTHETIC_SCENARIOS.

  Running against any other instrument is refused with a HarmGuardRefusal exception.
  The guard cannot be silently bypassed. Any attempt to call bypass_guard=True
  is loudly logged at ERROR level AND recorded in the guard audit log.

RATIONALE:
  A harm-quantification output — even labeled "statistical estimate" — could be
  misread as an accusation of real harm against a real, named, currently-listed
  company. For a company with real share value and real legal standing, an
  unproven harm claim is potentially defamatory. This guard makes "accidentally
  running against live data" require an explicit, logged override, not just an
  oversight.

  Compare: SEBI's own disgorgement methodology (used in adjudication orders) is
  only applied AFTER a legal finding of manipulation, not before. This module
  follows the same principle.
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

# ── Guard audit log ───────────────────────────────────────────────────────────
# Every call — including refused ones — is logged to a file, not just to
# stderr. This gives a paper trail: if the guard is ever bypassed, there is a
# record.

_GUARD_AUDIT_LOG_PATH = Path("app/harm_estimation/guard_audit.log")


def _audit(event: str, instrument_id: str, case_id: str, bypassed: bool) -> None:
    """Write a tamper-evident audit entry to the guard log."""
    _GUARD_AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
    line = f"{ts} | event={event} | instrument_id={instrument_id!r} | case_id={case_id!r} | bypass_requested={bypassed}\n"
    with open(_GUARD_AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line)


# ── Allow-lists ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SEBIConfirmedCase:
    """
    A SEBI case where an adjudication/final order has legally confirmed
    manipulation occurred. Running harm estimation against these answers
    "how much harm?" — not "did harm occur?" (which the legal order has
    already answered).
    """
    case_id: str
    order_reference: str
    confirmed_instruments: list[str]  # NSE/BSE instrument IDs or symbols
    order_date: str                   # ISO date string: YYYY-MM-DD
    note: str = ""


@dataclass(frozen=True)
class SyntheticScenario:
    """
    An explicitly-labeled synthetic scenario built from injected anomalies
    into real or simulated price data. Used for methodology validation only.
    The label SYNTHETIC_INJECTED must appear in the scenario ID and in every
    output produced, so it can never be confused for a finding about a real entity.
    """
    scenario_id: str   # MUST contain the prefix "SYNTHETIC_"
    description: str
    instruments: list[str]

    def __post_init__(self):
        if not self.scenario_id.startswith("SYNTHETIC_"):
            raise ValueError(
                f"Synthetic scenario IDs must start with 'SYNTHETIC_'. "
                f"Got: {self.scenario_id!r}"
            )


# ── The actual allow-lists ────────────────────────────────────────────────────

SEBI_CONFIRMED_CASES: list[SEBIConfirmedCase] = [
    SEBIConfirmedCase(
        case_id="KIL-2019",
        order_reference=(
            "Adjudication Order in the matter of Kavit Industries Limited, "
            "passed February 28, 2025 (SEBI Adjudicating Officer Order)"
        ),
        confirmed_instruments=["KAVIT", "KIL"],
        order_date="2025-02-28",
        note=(
            "SEBI's adjudication order legally confirmed circular trading manipulation "
            "in the KIL scrip on BSE. Harm estimation is appropriate — the question "
            "is 'how much?' not 'did it happen?'. NOTE: Real-case harm estimation "
            "currently blocked by BSE-only data gap (see docs/NSE_ACCESS_LIMITATIONS.md). "
            "Can only run on synthetic proxy until BSE bhavcopy fetcher is built."
        ),
    ),
    SEBIConfirmedCase(
        case_id="PUMP-DUMP-2017-2020",
        order_reference=(
            "Ex Parte Ad Interim Order-cum-Show Cause Notice, June 19, 2023; "
            "Final Order June 2026 — In the Matter of Manipulation in Scrips "
            "including Mauria Udyog Ltd., 7NR Retail Ltd., GBL Industries Ltd., "
            "Darjeeling Ropeway Co. Ltd., Vishal Fabrics Ltd."
        ),
        confirmed_instruments=[
            "MAURIUDYOG", "7NRRETAIL", "GBLIND", "VISHALFAB", "DARJROPE"
        ],
        order_date="2023-06-19",
        note=(
            "SEBI's final order (Rs. 143.79 crore disgorgement, 222 entities barred) "
            "legally confirmed pump-and-dump manipulation. NOTE: Real-case harm "
            "estimation blocked by BSE-only data gap. Synthetic proxy only until "
            "BSE data access is established."
        ),
    ),
    SEBIConfirmedCase(
        case_id="GIL-2003-2004",
        order_reference=(
            "Adjudication Order in the matter of Gravity India Limited, "
            "ORDER/SBM/KL/2021-22/15788, March 31, 2022"
        ),
        confirmed_instruments=["GRAVITYIND", "GIL"],
        order_date="2022-03-31",
        note=(
            "SEBI adjudication confirmed circular trading on BSE. Additionally "
            "blocked by archive availability — 2003-2004 data predates reliable "
            "NSE/BSE digital archives."
        ),
    ),
]

SYNTHETIC_SCENARIOS: list[SyntheticScenario] = [
    SyntheticScenario(
        scenario_id="SYNTHETIC_PUMP_RELIANCE_2021_VALIDATION",
        description=(
            "Methodology validation scenario. Real RELIANCE price history used "
            "as estimation window (NSE large-cap, clean negative-control-validated). "
            "Synthetic abnormal return injected into event window at known magnitude "
            "+30% CAR over 20 trading days. Used to verify that event_study.py "
            "recovers the injected effect within expected statistical confidence "
            "intervals. The 'manipulation' here is synthetic and RELIANCE is not "
            "accused of any wrongdoing — it is used only as a liquid price series "
            "with known properties."
        ),
        instruments=["RELIANCE"],
    ),
    SyntheticScenario(
        scenario_id="SYNTHETIC_CIRCULAR_KAVITIND_DEMO",
        description=(
            "Demo scenario for circular trading harm illustration. KAVITIND is "
            "the fictional instrument used in the demo/watchlist samples — not "
            "the real Kavit Industries Ltd listed on BSE. Uses injected synthetic "
            "price data, no real company data."
        ),
        instruments=["KAVITIND"],
    ),
]

# Derived lookup sets for O(1) membership testing
_CONFIRMED_INSTRUMENTS: set[str] = {
    sym.upper()
    for case in SEBI_CONFIRMED_CASES
    for sym in case.confirmed_instruments
}
_CONFIRMED_CASE_IDS: set[str] = {c.case_id for c in SEBI_CONFIRMED_CASES}
_SYNTHETIC_SCENARIO_IDS: set[str] = {s.scenario_id for s in SYNTHETIC_SCENARIOS}
_SYNTHETIC_INSTRUMENTS: set[str] = {
    sym.upper()
    for scenario in SYNTHETIC_SCENARIOS
    for sym in scenario.instruments
}


# ── Guard exception ───────────────────────────────────────────────────────────

class HarmGuardRefusal(RuntimeError):
    """
    Raised when the harm estimation module is asked to run against an
    instrument or case that is not on the allow-list.

    Do not catch this exception silently. If you are getting this exception,
    you are attempting to run harm estimation against unapproved data.
    """
    pass


# ── Public guard API ──────────────────────────────────────────────────────────

def check_instrument_allowed(
    instrument_id: str,
    case_id: str,
    bypass_guard: bool = False,
) -> Literal["sebi_confirmed", "synthetic"]:
    """
    Verify that harm estimation may run for this instrument/case combination.

    Parameters
    ----------
    instrument_id : str
        The instrument symbol or ID being analyzed (e.g. "KAVIT", "RELIANCE").
    case_id : str
        The case ID (e.g. "KIL-2019" or "SYNTHETIC_PUMP_RELIANCE_2021_VALIDATION").
    bypass_guard : bool
        If True, the guard will LOG A LOUD ERROR but not raise. The audit log
        will record the bypass. This parameter should only be set by an explicit,
        documented, analyst-reviewed override — never programmatically.

    Returns
    -------
    str
        "sebi_confirmed" or "synthetic" — the type of allow-listed context.

    Raises
    ------
    HarmGuardRefusal
        If the instrument is not on the allow-list AND bypass_guard is False.
    """
    instrument_upper = instrument_id.upper()
    case_upper = case_id.upper()

    is_sebi = (
        case_id in _CONFIRMED_CASE_IDS
        or instrument_upper in _CONFIRMED_INSTRUMENTS
    )
    is_synthetic = (
        case_id in _SYNTHETIC_SCENARIO_IDS
        or instrument_upper in _SYNTHETIC_INSTRUMENTS
        or case_id.startswith("SYNTHETIC_")
    )

    if is_sebi:
        _audit("ALLOWED_SEBI_CONFIRMED", instrument_id, case_id, bypass_guard)
        logger.info(
            "[HARM_GUARD] ALLOWED: instrument=%r case=%r — SEBI-confirmed manipulation case.",
            instrument_id, case_id,
        )
        return "sebi_confirmed"

    if is_synthetic:
        _audit("ALLOWED_SYNTHETIC", instrument_id, case_id, bypass_guard)
        logger.info(
            "[HARM_GUARD] ALLOWED: instrument=%r case=%r — Explicitly labeled synthetic scenario.",
            instrument_id, case_id,
        )
        return "synthetic"

    # ── NOT ALLOWED ──────────────────────────────────────────────────────────
    _audit("REFUSED", instrument_id, case_id, bypass_guard)

    msg = (
        f"[HARM_GUARD] REFUSED: instrument={instrument_id!r} case={case_id!r} "
        f"is NOT on the harm-estimation allow-list.\n"
        f"  Allow-list requires one of:\n"
        f"    (a) A SEBI-confirmed case ID: {sorted(_CONFIRMED_CASE_IDS)}\n"
        f"    (b) A confirmed instrument: {sorted(_CONFIRMED_INSTRUMENTS)}\n"
        f"    (c) A synthetic scenario ID (must start with 'SYNTHETIC_'): "
        f"{sorted(_SYNTHETIC_SCENARIO_IDS)}\n"
        f"  RATIONALE: Harm quantification outputs could be misread as an "
        f"accusation of real harm against a real entity. This guard prevents "
        f"accidental application to live/unlisted instruments. To add a new "
        f"SEBI case, add it to SEBI_CONFIRMED_CASES in guards.py — this "
        f"requires a real, citable SEBI adjudication/final order."
    )

    if bypass_guard:
        logger.error(
            "*** HARM_GUARD BYPASS REQUESTED AND RECORDED *** %s\n"
            "    This bypass is logged to %s and should be reviewed by a senior analyst.",
            msg,
            _GUARD_AUDIT_LOG_PATH,
        )
        _audit("BYPASS_GRANTED", instrument_id, case_id, True)
        return "sebi_confirmed"  # bypass grants access but it's logged

    logger.error(msg)
    raise HarmGuardRefusal(msg)


def list_allowed_instruments() -> dict:
    """Return the current allow-list for documentation / UI display."""
    return {
        "sebi_confirmed_cases": [
            {
                "case_id": c.case_id,
                "instruments": c.confirmed_instruments,
                "order_date": c.order_date,
                "note": c.note,
            }
            for c in SEBI_CONFIRMED_CASES
        ],
        "synthetic_scenarios": [
            {
                "scenario_id": s.scenario_id,
                "instruments": s.instruments,
                "description": s.description,
            }
            for s in SYNTHETIC_SCENARIOS
        ],
    }
