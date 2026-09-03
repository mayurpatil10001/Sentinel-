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
