"""
Evidence log builder.

This is the piece that answers the original ask directly: when a
pattern is flagged, produce the raw, independently-verifiable log —
exact time, quantity, price, exchange, session, account — so SEBI or
the exchange can check it against their own records rather than
trusting a score.

Deliberately separate from any "narrative" report (PDF, dashboard
card, etc). This is meant to be machine-readable and exact.
"""

from dataclasses import dataclass, asdict
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import Order, Alert


@dataclass
class EvidenceRow:
    order_id: str
    exchange_order_id: str
    account_id: str
    side: str
    status: str
    price: float
    quantity: int
    filled_quantity: int
    session: str | None
    exchange: str
    timestamp: str  # ISO 8601, exact to the millisecond


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
    rows: list  # list[EvidenceRow]
    generated_at: str
    disclaimer: str = (
        "This log contains the exact order-level records that produced this "
        "alert. It is provided for independent verification by exchanges and "
        "regulators against their own systems of record. The composite score "
        "is a detection signal, not a legal finding."
    )


def build_evidence_log(db: Session, alert: Alert) -> EvidenceLog:
    orders = (
        db.query(Order)
        .filter(Order.instrument_id == alert.instrument_id)
        .filter(Order.timestamp >= alert.window_start)
        .filter(Order.timestamp <= alert.window_end)
        .filter(Order.account_id.in_(alert.accounts_involved))
        .order_by(Order.timestamp.asc())
        .all()
    )

    rows = [
        EvidenceRow(
            order_id=o.id,
            exchange_order_id=o.exchange_order_id,
            account_id=o.account_id,
            side=o.side.value if hasattr(o.side, "value") else str(o.side),
            status=o.status.value if hasattr(o.status, "value") else str(o.status),
            price=o.price,
            quantity=o.quantity,
            filled_quantity=o.filled_quantity or 0,
            session=o.session,
            exchange=o.exchange,
            timestamp=o.timestamp.isoformat(),
        )
        for o in orders
    ]

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
    )
