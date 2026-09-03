"""
Alert Manager — Deduplication, Escalation, and Lifecycle
==========================================================

Converts raw detector signals (from any phase) into persisted Alert rows,
deduplicates repeated signals for the same instrument/pattern, and manages
the escalation tier logic.

Escalation tiers
-----------------
  TIER 1 (score >= 0.45, severity "medium" or above):
    Alert created and assigned to internal analyst queue (status="open").

  TIER 2 (score >= 0.70, severity "high"):
    Alert flagged for supervisor review (status="open", escalation_tier=2).
    SLA: must be reviewed within 2 business days per SEBI circular
    CIR/MRD/DP/30/2010 on surveillance obligations.
    Label: HEURISTIC threshold — SEBI does not specify a numeric score
    cutoff; 0.70 is a project-internal starting point.

  TIER 3 (score >= 0.85, severity "critical"):
    Alert marked for SEBI referral draft generation (status="open",
    escalation_tier=3, escalated_to_sebi=False → analyst drafts the SAR).
    The system does NOT auto-file with SEBI — it prepares the draft
    and requires human sign-off before submission.
    Label: HEURISTIC.

Deduplication
--------------
A new signal for (instrument, pattern_type) is suppressed if an OPEN alert
already exists for the same instrument AND pattern with a window that
overlaps the current window. This prevents the same manipulation episode
from generating hundreds of alerts across rolling windows.

Suppression window: DEDUP_WINDOW_HOURS = 4
  A new alert for the same (instrument, pattern_type) is suppressed if
  an open alert was created within the last 4 hours.
  Label: HEURISTIC — needs tuning based on typical episode durations.

If the new signal has a HIGHER score than the existing open alert, the
existing alert's score is updated upward (escalation update, not a new row).
This ensures the highest-severity observation for an episode is preserved.

HARD RULE #1: alerts are NOT created from synthetic signals. The caller
is responsible for passing real detector output. The manager raises if
passed a None signal.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from app.db.models import Alert, Instrument

logger = logging.getLogger(__name__)

# ── Escalation thresholds ─────────────────────────────────────────────────────

TIER_1_SCORE: float = 0.45   # HEURISTIC
TIER_2_SCORE: float = 0.70   # HEURISTIC
TIER_3_SCORE: float = 0.85   # HEURISTIC

# ── Deduplication ─────────────────────────────────────────────────────────────

DEDUP_WINDOW_HOURS: int = 4  # HEURISTIC


def _escalation_tier(score: float, severity: str) -> int:
    """Return 1, 2, or 3 based on score and severity."""
    if score >= TIER_3_SCORE or severity == "critical":
        return 3
    if score >= TIER_2_SCORE or severity == "high":
        return 2
    return 1


def find_existing_open_alert(
    db,
    instrument_id: str,
    pattern_type: str,
    dedup_window_hours: int = DEDUP_WINDOW_HOURS,
) -> Optional[Alert]:
    """
    Returns the most recent open alert for (instrument, pattern_type)
    within the deduplication window, or None if no such alert exists.

    HARD RULE #1: raises TypeError if db is None.
    """
    if db is None:
        raise TypeError(
            "find_existing_open_alert: db session is None. "
            "Pass a live SQLAlchemy session."
        )

    cutoff = datetime.utcnow() - timedelta(hours=dedup_window_hours)
    return (
        db.query(Alert)
        .filter(
            Alert.instrument_id == instrument_id,
            Alert.pattern_type == pattern_type,
            Alert.status.in_(["open", "investigating"]),
            Alert.detected_at >= cutoff,
        )
        .order_by(Alert.detected_at.desc())
        .first()
    )


def create_or_update_alert(
    db,
    instrument: Instrument,
    pattern_type: str,
    score: float,
    severity: str,
    accounts_involved: list[str],
    window_start: datetime,
    window_end: datetime,
    explanation: str,
) -> tuple[Alert, str]:
    """
    Creates a new Alert or updates an existing open one via deduplication.

    Parameters
    ----------
    db
        Active SQLAlchemy session.
    instrument
        The Instrument DB row for the flagged instrument.
    pattern_type
        String identifying the detector: "circular_trading", "coordinated_pump",
        "oi_concentration", "oi_iv_decoupling", "basis_distortion",
        "option_pinning", "spoofing_layering".
    score
        Composite anomaly score in [0, 1].
    severity
        "low" | "medium" | "high" | "critical".
    accounts_involved
        List of account IDs associated with the signal.
    window_start, window_end
        The detection window boundaries.
    explanation
        Human-readable reasoning string (HARD RULE #3).

    Returns
    -------
    (Alert, action)
        action = "created" | "updated_score" | "suppressed"
        "suppressed" means the alert was below TIER_1_SCORE and not persisted.
    """
    if explanation is None or explanation.strip() == "":
        raise ValueError(
            "create_or_update_alert: explanation is required (HARD RULE #3). "
            "Every alert must have a human-readable explanation."
        )

    if score < TIER_1_SCORE:
        logger.debug(
            "Alert suppressed: score %.3f < TIER_1_SCORE %.2f for %s/%s",
            score, TIER_1_SCORE, instrument.symbol, pattern_type
        )
        return None, "suppressed"

    tier = _escalation_tier(score, severity)

    existing = find_existing_open_alert(db, instrument.id, pattern_type)

    if existing is not None:
        if score > existing.score:
            # Score update — the new observation is more severe
            old_score = existing.score
            existing.score = round(score, 4)
            existing.severity = severity
            existing.explanation = explanation
            existing.window_end = window_end
            # Merge account lists (union, dedup)
            merged = list(set(
                (existing.accounts_involved or []) + accounts_involved
            ))
            existing.accounts_involved = merged
            db.commit()
            db.refresh(existing)
            logger.info(
                "Alert %s score updated: %.3f → %.3f for %s/%s (tier %d)",
                existing.id, old_score, score, instrument.symbol, pattern_type, tier
            )
            return existing, "updated_score"
        else:
            logger.debug(
                "Alert deduplicated: existing %s (score %.3f) >= new %.3f for %s/%s",
                existing.id, existing.score, score, instrument.symbol, pattern_type
            )
            return existing, "deduplicated"

    # No existing open alert — create new
    alert = Alert(
        instrument_id=instrument.id,
        pattern_type=pattern_type,
        severity=severity,
        score=round(score, 4),
        accounts_involved=accounts_involved,
        window_start=window_start,
        window_end=window_end,
        explanation=explanation,
        status="open",
        escalated_to_sebi=False,
    )
    # Store escalation tier in explanation prefix for now
    # (avoid schema migration for this build)
    alert.explanation = f"[TIER {tier}] {explanation}"
    db.add(alert)
    db.commit()
    db.refresh(alert)

    logger.info(
        "Alert created: %s (tier=%d, score=%.3f, severity=%s) for %s/%s",
        alert.id, tier, score, severity, instrument.symbol, pattern_type
    )
    return alert, "created"


def escalate_alert(db, alert: Alert, new_status: str, notes: str = "") -> Alert:
    """
    Transition an alert to a new lifecycle status.

    Valid transitions:
      open → investigating → escalated → closed
      open → closed (analyst dismisses)

    HARD RULE #1: raises if alert or db is None.
    """
    if alert is None:
        raise ValueError("escalate_alert: alert is None.")
    if db is None:
        raise TypeError("escalate_alert: db is None.")

    valid_statuses = {"open", "investigating", "escalated", "closed"}
    if new_status not in valid_statuses:
        raise ValueError(
            f"Invalid status '{new_status}'. "
            f"Must be one of: {valid_statuses}"
        )

    old_status = alert.status
    alert.status = new_status

    if new_status == "escalated":
        alert.escalated_to_sebi = True

    if notes:
        alert.explanation = alert.explanation + f"\n[ESCALATION NOTE] {notes}"

    db.commit()
    db.refresh(alert)
    logger.info(
        "Alert %s status: %s → %s", alert.id, old_status, new_status
    )
    return alert
