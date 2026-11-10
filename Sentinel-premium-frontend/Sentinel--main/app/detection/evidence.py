"""
Evidence log builder.

This is the piece that answers the original ask directly: when a
pattern is flagged, produce the raw, independently-verifiable log —
exact time, quantity, price, exchange, session, account — so SEBI or
the exchange can check it against their own records rather than
trusting a score.

Deliberately separate from any "narrative" report (PDF, dashboard
card, etc). This is meant to be machine-readable and exact.

PII PROTECTION — PHASE 5b
---------------------------
account_id is no longer always returned in plaintext. The behaviour is
controlled by the EVIDENCE_LOG_USE_HASHED_ID flag in models.py:

  True (default): returns account_id_hash (SHA-256 + salt).
    - Use for most internal analytics, dashboards, and automated reports.
    - Protects against accidental leakage of raw IDs.

  False: returns the raw account_id.
    - Use ONLY when an analyst needs to cross-verify against exchange
      records where the real ID is required.
    - MUST be logged in EvidenceAccessLog (enforced automatically here).

The choice is DELIBERATE AND LOGGED. The point is not to prevent
legitimate access to raw IDs (that would break the verification workflow).
The point is to make raw-ID access a visible, searchable, auditable event.

SECURITY POSTURE:
  - Hashed-ID mode: protects against accidental leakage.
  - Raw-ID mode + access log: documents that access happened.
  - Neither mode prevents an insider with direct DB access from reading
    raw IDs — this is an application-layer control only.
"""

from dataclasses import dataclass, asdict
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import Order, Alert, EVIDENCE_LOG_USE_HASHED_ID
from app.security.access_log import log_evidence_access


@dataclass
class EvidenceRow:
    order_id: str
    exchange_order_id: str
    account_id: str          # raw OR hashed depending on use_raw_ids
    side: str
    status: str
    price: float
    quantity: int
    filled_quantity: int
    session: str | None
    exchange: str
    timestamp: str           # ISO 8601, exact to the millisecond


@dataclass
class EvidenceLog:
    alert_id: str
    pattern_type: str
    instrument_symbol: str
    exchange: str
    window_start: str
    window_end: str
    accounts_involved: list
    severity: str
    score: float
    explanation: str
    rows: list               # list[EvidenceRow]
    generated_at: str
    used_raw_ids: bool       # NEW: tells the consumer which ID mode was used
    disclaimer: str = (
        "This log contains the exact order-level records that produced this "
        "alert. It is provided for independent verification by exchanges and "
        "regulators against their own systems of record. The composite score "
        "is a detection signal, not a legal finding."
    )


def build_evidence_log(
    db: Session,
    alert: Alert,
    accessed_by: str = "system",
    source_ip: str | None = None,
    use_raw_ids: bool | None = None,
) -> EvidenceLog:
    """
    Build a structured evidence log for a given alert.

    Parameters
    ----------
    db
        Active SQLAlchemy session.
    alert
        The Alert DB row whose evidence to retrieve.
    accessed_by
        Identity of the caller. Used in EvidenceAccessLog.
        For API calls, pass the authenticated user ID or API key ID.
        For system jobs, pass the job name.
        Default: "system" — change this for API-initiated calls.
    source_ip
        Optional IP address of the requesting client (for API calls).
    use_raw_ids
        If None: uses EVIDENCE_LOG_USE_HASHED_ID from models.py (default).
        If True: returns raw account_id regardless of global flag.
        If False: returns account_id_hash regardless of global flag.
        Explicit override is allowed so that the verification workflow
        can request raw IDs without changing the global config.

    Returns
    -------
    EvidenceLog
        The evidence log. `used_raw_ids` field tells the consumer which
        ID mode was applied. Access is always logged in EvidenceAccessLog.

    IMPORTANT: every call to this function creates an EvidenceAccessLog row.
    This is non-optional and enforced here, not in the API route.
    """
    effective_use_raw = (
        use_raw_ids if use_raw_ids is not None else not EVIDENCE_LOG_USE_HASHED_ID
    )

    orders = (
        db.query(Order)
        .filter(Order.instrument_id == alert.instrument_id)
        .filter(Order.timestamp >= alert.window_start)
        .filter(Order.timestamp <= alert.window_end)
        .filter(Order.account_id.in_(alert.accounts_involved))
        .order_by(Order.timestamp.asc())
        .all()
    )

    rows = []
    for o in orders:
        if effective_use_raw:
            id_value = o.account_id
        else:
            # Use pre-computed hash if available; fall back to raw with a warning
            # if hash column is null (e.g. legacy row ingested before Phase 5b).
            if o.account_id_hash:
                id_value = o.account_id_hash
            else:
                # This row predates the hash column — fall back to raw and log it
                id_value = o.account_id
                effective_use_raw = True  # mark that we actually returned raw

        rows.append(EvidenceRow(
            order_id=o.id,
            exchange_order_id=o.exchange_order_id,
            account_id=id_value,
            side=o.side.value if hasattr(o.side, "value") else str(o.side),
            status=o.status.value if hasattr(o.status, "value") else str(o.status),
            price=o.price,
            quantity=o.quantity,
            filled_quantity=o.filled_quantity or 0,
            session=o.session,
            exchange=o.exchange,
            timestamp=o.timestamp.isoformat(),
        ))

    # Log the access — non-optional, enforced here not in the route
    log_evidence_access(
        db=db,
        alert_id=str(alert.id),
        accessed_by=accessed_by,
        used_raw_ids=effective_use_raw,
        evidence_row_count=len(rows),
        source_ip=source_ip,
    )

    return EvidenceLog(
        alert_id=alert.id,
        pattern_type=alert.pattern_type,
        instrument_symbol=alert.instrument.symbol,
        exchange=alert.instrument.exchange,
        window_start=alert.window_start.isoformat(),
        window_end=alert.window_end.isoformat(),
        accounts_involved=alert.accounts_involved,
        severity=alert.severity,
        score=alert.score,
        explanation=alert.explanation,
        rows=[asdict(r) for r in rows],
        generated_at=datetime.utcnow().isoformat(),
        used_raw_ids=effective_use_raw,
    )
