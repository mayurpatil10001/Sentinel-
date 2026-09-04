"""
Core schema.

Design decision: `orders` is the primary surveillance unit, not `trades`.
Spoofing, layering, and quote-stuffing are defined by order behavior
(placed -> cancelled without execution), so if you only store executed
trades you structurally cannot detect them before impact.

All asset classes (equity, index, future, option) share one table with
optional derivative-specific columns, rather than separate tables per
asset class, so cross-asset patterns (e.g. spot-vs-future manipulation)
can be queried in one place.
"""

# ── PII Configuration ─────────────────────────────────────────────────────────
# Controls whether evidence.py and sebi_report.py return raw account IDs or
# their SHA-256 hashes when generating exportable logs.
#
# Default: True (hashed IDs). Override to False only when:
#   1. An analyst explicitly needs the raw ID to cross-verify against
#      exchange records (SEBI / NSE / BSE verification workflow), AND
#   2. That access is being logged (see app/security/access_log.py).
#
# DESIGN INTENT: The raw account_id column is preserved in the database
# because SEBI/exchange counterpart verification genuinely requires the
# real ID. The point of this flag is to make outputting raw IDs a
# DELIBERATE, LOGGED choice rather than an uncontrolled default leak.
# Anyone calling evidence.py with raw IDs enabled must have a logged
# justification in EvidenceAccessLog.
#
# Label: HEURISTIC default — the right default for most deployments.
# A SEBI investigation unit with direct exchange feed integration may
# legitimately set this to False for their workflow.
EVIDENCE_LOG_USE_HASHED_ID: bool = True

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def gen_uuid() -> str:
    return str(uuid.uuid4())


class InstrumentType(str, enum.Enum):
    EQUITY = "equity"
    PENNY_STOCK = "penny_stock"  # flagged separately so liquidity baselines differ
    INDEX = "index"
    FUTURE = "future"
    OPTION = "option"


class OrderStatus(str, enum.Enum):
    PLACED = "placed"
    MODIFIED = "modified"
    CANCELLED = "cancelled"
    EXECUTED = "executed"
    PARTIALLY_EXECUTED = "partially_executed"
    REJECTED = "rejected"


class OrderSide(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"


class Instrument(Base):
    """
    One row per tradable instrument. Keeps derivative-specific fields
    (strike, expiry, option_type, underlying) as first-class columns
    instead of stuffing them into a generic metadata blob.
    """

    __tablename__ = "instruments"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    symbol = Column(String(64), nullable=False, index=True)
    exchange = Column(String(16), nullable=False)  # NSE / BSE / MCX
    instrument_type = Column(Enum(InstrumentType), nullable=False)

    # Derivative-specific (null for plain equity)
    underlying_symbol = Column(String(64), nullable=True, index=True)
    strike_price = Column(Float, nullable=True)
    expiry_date = Column(DateTime, nullable=True)
    option_type = Column(String(2), nullable=True)  # CE / PE

    # Liquidity baseline, used to normalize detection thresholds per
    # instrument instead of applying one fixed threshold to everything.
    avg_daily_volume_30d = Column(Float, nullable=True)
    avg_order_size_30d = Column(Float, nullable=True)
    avg_daily_turnover_30d = Column(Float, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)


class Order(Base):
    """
    Every order lifecycle event: placed, modified, cancelled, executed.
    This is what makes early (pre-impact) detection possible.
    """

    __tablename__ = "orders"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    exchange_order_id = Column(String(64), nullable=False, index=True)
    account_id = Column(String(64), nullable=False, index=True)
    # account_id_hash: salted SHA-256 of account_id. Nullable to allow
    # rows ingested before this column was added (migration-safe additive
    # column). Populated by the ingest layer via pii.hash_account_identifier().
    # HARD RULE #1 compliance: account_id is NOT removed — it is still
    # required for SEBI/exchange counterpart verification.
    account_id_hash = Column(String(64), nullable=True, index=True)
    instrument_id = Column(String(36), ForeignKey("instruments.id"), nullable=False)

    side = Column(Enum(OrderSide), nullable=False)
    status = Column(Enum(OrderStatus), nullable=False, index=True)
    price = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False)
    filled_quantity = Column(Integer, default=0)

    session = Column(String(16), nullable=True)  # e.g. "pre-open", "normal", "closing"
    timestamp = Column(DateTime, nullable=False, index=True)
    exchange = Column(String(16), nullable=False)

    instrument = relationship("Instrument")


class Trade(Base):
    """
    Executed trades — derived from matched orders. Kept for
    completed-trade analytics (pump & dump, circular trading) but is
    NOT the primary detection input for spoofing/layering.
    """

    __tablename__ = "trades"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    buy_order_id = Column(String(36), ForeignKey("orders.id"), nullable=True)
    sell_order_id = Column(String(36), ForeignKey("orders.id"), nullable=True)
    instrument_id = Column(String(36), ForeignKey("instruments.id"), nullable=False)
    # account_id_hash: same convention as Order.account_id_hash — nullable
    # for migration safety. Trades derived from Orders inherit the hash.
    account_id_hash = Column(String(64), nullable=True, index=True)

    price = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    exchange = Column(String(16), nullable=False)

    instrument = relationship("Instrument")


class Alert(Base):
    """
    A detection result. Deliberately separates the *score* from the
    *evidence* — evidence_log_ref points to the raw order/trade slice
    that a regulator can independently verify, rather than asking them
    to trust the score alone.
    """

    __tablename__ = "alerts"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    instrument_id = Column(String(36), ForeignKey("instruments.id"), nullable=False)
    pattern_type = Column(String(64), nullable=False, index=True)  # e.g. "spoofing_layering"
    severity = Column(String(16), nullable=False)  # low/medium/high/critical
    score = Column(Float, nullable=False)  # 0-1, normalized

    accounts_involved = Column(JSON, nullable=False)  # list of account_ids
    window_start = Column(DateTime, nullable=False)
    window_end = Column(DateTime, nullable=False)

    explanation = Column(String(2048), nullable=False)  # human-readable reasoning
    detected_at = Column(DateTime, default=datetime.utcnow)

    status = Column(String(16), default="open")  # open/investigating/escalated/closed
    escalated_to_sebi = Column(Boolean, default=False)

    instrument = relationship("Instrument")

    # Prevents duplicate alerts when concurrent detection runs both fire on
    # the same (instrument, pattern, window). Confirmed via
    # tests/stress/test_concurrent_access.py that without this, two
    # concurrent detection runs can both pass the "does this alert already
    # exist" check before either commits (classic TOCTOU race), producing
    # two identical alerts pointing at the same evidence — a real integrity
    # problem for anything referenced in a SEBI filing. With this
    # constraint, the second concurrent INSERT raises IntegrityError, which
    # calling code must catch and treat as "alert already exists," not as
    # a failure.
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "pattern_type",
            "window_start",
            name="uq_alert_instrument_pattern_window",
        ),
    )
