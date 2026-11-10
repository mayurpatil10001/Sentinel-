"""
SEBI Suspicious Activity Report (SAR) Formatter
=================================================

Generates a structured draft SAR in the format expected by SEBI's
Integrated Surveillance Department (ISD), based on SEBI Circular
SEBI/HO/ISD/ISD_OAED/P/CIR/2022/155 on Surveillance System Guidelines.

IMPORTANT DISCLAIMERS
----------------------
1. This module produces a DRAFT for analyst review. It does NOT
   automatically file anything with SEBI. The analyst must:
   a) Review the draft for factual accuracy.
   b) Attach supporting evidence (order-level logs, screenshots).
   c) Submit through SEBI's SCORES portal or the designated exchange channel.

2. The format follows publicly available SEBI guidance as of 2024. SEBI
   may update reporting requirements — verify against the current circular
   before submission.

3. Threshold labels ("UNVALIDATED GUESS", "HEURISTIC") from the detectors
   are carried through into the SAR so the analyst and SEBI can assess
   the reliability of each finding independently.

SAR sections (per SEBI ISD guidance)
--------------------------------------
1. Header         — Case reference, reporting entity, date
2. Subject        — Instrument, exchange, time period
3. Suspected pattern — Pattern type and SEBI regulation potentially violated
4. Evidence summary — Human-readable signal description + key metrics
5. Accounts involved — List of implicated account IDs
6. Detection methodology — How the system detected this (important for
   SEBI to assess evidentiary weight)
7. Limitations    — Known false-positive risks and data gaps
8. Recommended action — What the analyst should do next

Output formats: dict (machine-readable), plain-text string (for filing).
"""

import uuid
from datetime import datetime, date
from dataclasses import dataclass, field
from typing import Optional

from app.db.models import Alert, Instrument


# ── SEBI regulation mapping ───────────────────────────────────────────────────
# Pattern type → suspected SEBI regulation violated.
# These are informational hints for the analyst — not legal conclusions.
# Source: SEBI (Prohibition of Fraudulent and Unfair Trade Practices) Regulations 2003
# and SEBI (Prohibition of Insider Trading) Regulations 2015.
_REGULATION_MAP = {
    "circular_trading": (
        "SEBI PFUTP Regulations 2003, Regulation 4(2)(a): "
        "Creating artificial volume / false or misleading appearance of trading."
    ),
    "coordinated_pump": (
        "SEBI PFUTP Regulations 2003, Regulation 4(2)(a)/(e): "
        "Creating artificial demand / price manipulation through coordinated buying."
    ),
    "oi_concentration": (
        "SEBI PFUTP Regulations 2003, Regulation 4(2)(e): "
        "Manipulating the price of a security through derivatives positioning."
    ),
    "oi_iv_decoupling": (
        "SEBI PFUTP Regulations 2003, Regulation 4(2)(e): "
        "Structural positioning in derivatives inconsistent with normal market behaviour."
    ),
    "basis_distortion": (
        "SEBI PFUTP Regulations 2003, Regulation 4(2)(e): "
        "Futures price manipulation affecting spot-futures relationship."
    ),
    "option_pinning": (
        "SEBI PFUTP Regulations 2003, Regulation 4(2)(a)/(e): "
        "Manipulating spot price near option strike to minimise option payout."
    ),
    "spoofing_layering": (
        "SEBI PFUTP Regulations 2003, Regulation 4(2)(a): "
        "Placing and cancelling orders to create false impression of demand/supply."
    ),
}

_PATTERN_DISPLAY = {
    "circular_trading": "Circular Trading / Wash Trading",
    "coordinated_pump": "Coordinated Pump (Multi-Account Buy-Side Manipulation)",
    "oi_concentration": "Abnormal Open Interest Concentration",
    "oi_iv_decoupling": "Open Interest – Implied Volatility Decoupling",
    "basis_distortion": "Futures Basis Distortion",
    "option_pinning": "Option Pinning / Max-Pain Manipulation",
    "spoofing_layering": "Spoofing / Layering (Order Cancellation Pattern)",
}


@dataclass
class SEBIDraftSAR:
    """
    A draft Suspicious Activity Report for SEBI submission.

    All fields are strings so the document can be serialised to any format
    (plain text, JSON, PDF) without further transformation.
    """
    case_reference: str            # auto-generated, format: SNTNL-YYYYMMDD-XXXX
    alert_id: str
    generated_at: str              # ISO8601

    # Section 1: Header
    reporting_entity: str = "Sentinel Automated Surveillance System"
    report_date: str = ""
    analyst_name: str = "[ANALYST TO COMPLETE]"

    # Section 2: Subject
    instrument_symbol: str = ""
    exchange: str = ""
    pattern_type_display: str = ""
    detection_window_start: str = ""
    detection_window_end: str = ""

    # Section 3: Suspected pattern
    suspected_regulation: str = ""
    pattern_description: str = ""

    # Section 4: Evidence summary
    anomaly_score: str = ""
    severity: str = ""
    evidence_narrative: str = ""    # the detector's explanation string

    # Section 5: Accounts
    accounts_involved: list[str] = field(default_factory=list)
    account_count: int = 0

    # Section 6: Detection methodology
    methodology_note: str = ""

    # Section 7: Limitations
    limitations: str = ""

    # Section 8: Recommended action
    recommended_action: str = ""

    # Filing status
    is_draft: bool = True
    filed_at: Optional[str] = None


def generate_draft_sar(
    alert: Alert,
    instrument: Instrument,
    analyst_name: str = "[ANALYST TO COMPLETE]",
) -> SEBIDraftSAR:
    """
    Generate a draft SEBI SAR from a persisted Alert.

    Parameters
    ----------
    alert
        The Alert DB row (status should be "open" or "escalated").
    instrument
        The related Instrument row.
    analyst_name
        Name of the reviewing analyst. Defaults to a placeholder.

    Returns
    -------
    SEBIDraftSAR
        A draft report ready for analyst review and editing.

    HARD RULE #1: raises ValueError if alert or instrument is None.
    HARD RULE #3: the SAR always includes the detector's explanation string
    unmodified, so the analyst sees exactly what the system found.
    """
    if alert is None:
        raise ValueError("generate_draft_sar: alert is None.")
    if instrument is None:
        raise ValueError("generate_draft_sar: instrument is None.")

    now = datetime.utcnow()
    case_ref = (
        f"SNTNL-{now.strftime('%Y%m%d')}-"
        f"{str(uuid.uuid4())[:8].upper()}"
    )
    pattern = alert.pattern_type
    accounts = alert.accounts_involved or []

    methodology = (
        "Sentinel automated surveillance system detected this pattern using "
        "rule-based signal detectors (Phases 1–3) combined with an unsupervised "
        "Isolation Forest anomaly scorer (Phase 4). "
        "Detection thresholds are documented inline and labelled as "
        "'HEURISTIC' (informed by SEBI case review or market convention) or "
        "'UNVALIDATED GUESS' (starting values requiring backtesting). "
        "The composite anomaly score is NOT a manipulation probability — "
        "it is a relative anomaly rank within the observation baseline. "
        "This report is a draft that requires analyst verification before submission."
    )

    limitations = _build_limitations(pattern, alert.score)

    recommended = _build_recommended_action(alert.score, alert.severity, pattern)

    return SEBIDraftSAR(
        case_reference=case_ref,
        alert_id=str(alert.id),
        generated_at=now.isoformat() + "Z",
        report_date=now.strftime("%d %B %Y"),
        analyst_name=analyst_name,
        instrument_symbol=instrument.symbol,
        exchange=instrument.exchange,
        pattern_type_display=_PATTERN_DISPLAY.get(pattern, pattern),
        detection_window_start=(
            alert.window_start.isoformat() if alert.window_start else ""
        ),
        detection_window_end=(
            alert.window_end.isoformat() if alert.window_end else ""
        ),
        suspected_regulation=_REGULATION_MAP.get(
            pattern,
            "SEBI PFUTP Regulations 2003 (specific regulation to be determined by analyst)"
        ),
        pattern_description=_PATTERN_DISPLAY.get(pattern, pattern),
        anomaly_score=f"{alert.score:.4f}",
        severity=alert.severity.upper(),
        evidence_narrative=alert.explanation or "[No explanation generated — investigate]",
        accounts_involved=accounts,
        account_count=len(accounts),
        methodology_note=methodology,
        limitations=limitations,
        recommended_action=recommended,
        is_draft=True,
    )


def _build_limitations(pattern_type: str, score: float) -> str:
    """Build a pattern-specific limitations section."""
    common = (
        "1. Detection thresholds have not been formally backtested against "
        "confirmed SEBI enforcement orders. False positive rates are unknown.\n"
        "2. The anomaly score is relative to the system's observation baseline, "
        "which may not capture long-running manipulation campaigns that appear "
        "'normal' within the training window.\n"
        "3. Order-level counterparty data is unavailable for most NSE instruments "
        "at EOD — detectors relying on counterparty identification use only "
        "broker-level data from the account under surveillance."
    )

    specific = {
        "circular_trading": (
            "\n4. Circular trading detector requires counterparty IDs to build "
            "the trade graph. Without EOD counterparty data (not publicly available), "
            "edges are inferred from timing — this may produce false positives "
            "in fast, liquid markets where coincident trades are common."
        ),
        "option_pinning": (
            "\n4. Option pinning is indistinguishable from legitimate gamma exposure "
            "by market makers. Market makers with large options books naturally "
            "hedge near high-OI strikes on expiry — this is NOT manipulation. "
            "This case requires confirmation that the accounts involved are NOT "
            "registered market makers for this instrument."
        ),
        "basis_distortion": (
            "\n4. The basis distortion model omits dividend yield, which causes "
            "false positives for stocks with ex-dividend dates near the detection window. "
            "Check the ex-dividend calendar before escalating."
        ),
    }.get(pattern_type, "")

    return common + specific


def _build_recommended_action(
    score: float, severity: str, pattern_type: str
) -> str:
    """Build a score/severity-appropriate recommended action."""
    if score >= 0.85 or severity == "critical":
        return (
            "CRITICAL: Recommend immediate escalation to Compliance Officer. "
            "1. Retrieve complete order-level audit trail for all implicated accounts "
            "from the exchange (NSE/BSE ENIT or broker surveillance feed). "
            "2. Cross-reference accounts against known related-party lists. "
            "3. If independent review confirms the pattern, prepare formal SAR "
            "for submission to SEBI ISD via SCORES portal. "
            "4. Freeze analyst review SLA: 1 business day."
        )
    if score >= 0.70 or severity == "high":
        return (
            "HIGH: Supervisor review required within 2 business days. "
            "1. Pull broker account KYC for all implicated accounts. "
            "2. Check for related-party links (common directors, addresses, etc.). "
            "3. If pattern is confirmed, elevate to CRITICAL tier. "
            "4. Document the review decision and rationale in this case file."
        )
    return (
        "MEDIUM: Internal analyst review within 5 business days. "
        "1. Verify the signal against raw order data. "
        "2. Check if the pattern has an innocent explanation "
        "(institutional block trade, news event, legitimate hedging). "
        "3. Close the alert with a documented rationale if no manipulation found."
    )


def format_sar_as_text(sar: SEBIDraftSAR) -> str:
    """
    Render the SAR as a plain-text document suitable for filing
    or pasting into a word processor.
    """
    lines = [
        "=" * 70,
        "SENTINEL — DRAFT SUSPICIOUS ACTIVITY REPORT",
        "FOR ANALYST REVIEW ONLY — NOT YET FILED WITH SEBI",
        "=" * 70,
        "",
        f"Case Reference : {sar.case_reference}",
        f"Alert ID       : {sar.alert_id}",
        f"Report Date    : {sar.report_date}",
        f"Generated At   : {sar.generated_at}",
        f"Analyst        : {sar.analyst_name}",
        f"Reporting Entity: {sar.reporting_entity}",
        "",
        "─" * 70,
        "SECTION 1 — SUBJECT INSTRUMENT",
        "─" * 70,
        f"Instrument     : {sar.instrument_symbol}",
        f"Exchange       : {sar.exchange}",
        f"Detection Window: {sar.detection_window_start} → {sar.detection_window_end}",
        "",
        "─" * 70,
        "SECTION 2 — SUSPECTED PATTERN",
        "─" * 70,
        f"Pattern        : {sar.pattern_type_display}",
        f"Regulation     : {sar.suspected_regulation}",
        "",
        "─" * 70,
        "SECTION 3 — EVIDENCE SUMMARY",
        "─" * 70,
        f"Anomaly Score  : {sar.anomaly_score}  (range 0–1; NOT a probability)",
        f"Severity Tier  : {sar.severity}",
        "",
        "System finding:",
        sar.evidence_narrative,
        "",
        "─" * 70,
        "SECTION 4 — ACCOUNTS INVOLVED",
        "─" * 70,
        f"Total accounts : {sar.account_count}",
        "Account IDs    :",
    ]
    for acct in sar.accounts_involved:
        lines.append(f"  • {acct}")

    lines += [
        "",
        "─" * 70,
        "SECTION 5 — DETECTION METHODOLOGY",
        "─" * 70,
        sar.methodology_note,
        "",
        "─" * 70,
        "SECTION 6 — LIMITATIONS AND CAVEATS",
        "─" * 70,
        sar.limitations,
        "",
        "─" * 70,
        "SECTION 7 — RECOMMENDED ACTION",
        "─" * 70,
        sar.recommended_action,
        "",
        "=" * 70,
        "END OF DRAFT SAR",
        f"This document is a DRAFT. Status: {'FILED' if sar.filed_at else 'PENDING ANALYST REVIEW'}",
        "=" * 70,
    ]
    return "\n".join(lines)


def format_sar_as_dict(sar: SEBIDraftSAR) -> dict:
    """Render the SAR as a JSON-serialisable dict for API responses."""
    return {
        "case_reference": sar.case_reference,
        "alert_id": sar.alert_id,
        "generated_at": sar.generated_at,
        "is_draft": sar.is_draft,
        "filed_at": sar.filed_at,
        "header": {
            "reporting_entity": sar.reporting_entity,
            "report_date": sar.report_date,
            "analyst_name": sar.analyst_name,
        },
        "subject": {
            "instrument_symbol": sar.instrument_symbol,
            "exchange": sar.exchange,
            "detection_window_start": sar.detection_window_start,
            "detection_window_end": sar.detection_window_end,
        },
        "suspected_pattern": {
            "display_name": sar.pattern_type_display,
            "suspected_regulation": sar.suspected_regulation,
        },
        "evidence": {
            "anomaly_score": sar.anomaly_score,
            "severity": sar.severity,
            "narrative": sar.evidence_narrative,
        },
        "accounts_involved": sar.accounts_involved,
        "methodology": sar.methodology_note,
        "limitations": sar.limitations,
        "recommended_action": sar.recommended_action,
    }
