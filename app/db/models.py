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

Schema changelog
----------------
Phase 1 (2026-11-10):
  - Alert: added event_date, last_seen_at, occurrences, source columns.
  - Alert: UniqueConstraint changed from (instrument_id, pattern_type, window_start)
           to (instrument_id, pattern_type, event_date) so dedup works across scans.
  - Instrument: added source column.
  - ScanRun: new table — single source of truth for scheduler activity.
  - OptionChainSnapshot: new table for multi-day OI delta (Phase 3).
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
from datetime import datetime, date

from sqlalchemy import (
    Boolean,
    Column,
    Date,
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

    # Provenance: how this instrument entered the DB.
    # live_fyers   = imported from live Fyers scan
    # synthetic_demo = seeded for demo/testing (never shown by default in live mode)
    # backtest     = imported as part of a backtest run
    source = Column(String(32), nullable=False, server_default="live_fyers")


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

    Dedup key: (instrument_id, pattern_type, event_date).
    event_date is the trading date the anomaly refers to (date only,
    Asia/Kolkata convention), NOT the wall-clock time the scan ran.
    This prevents duplicate rows when the same day is re-scanned.

    On re-detection of an existing alert, callers must UPDATE:
        last_seen_at = now
        occurrences  += 1
        severity/score only if they increased
    Never insert a new row for the same (instrument, pattern, event_date).
    """

    __tablename__ = "alerts"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    instrument_id = Column(String(36), ForeignKey("instruments.id"), nullable=False)
    pattern_type = Column(String(64), nullable=False, index=True)  # e.g. "volume_spike"
    severity = Column(String(16), nullable=False)  # low/medium/high/critical
    score = Column(Float, nullable=False)  # 0-1, heuristic score from the triggering detector
    # ML anomaly score from OHLCVScorer Isolation Forest (None if model not yet trained).
    # This is a distributional anomalousness score, NOT a manipulation probability.
    # See app/ml/ohlcv_scorer.py for full provenance and honest description.
    ml_anomaly_score = Column(Float, nullable=True)   # 0-1, None = heuristic-only mode
    score_method = Column(String(32), nullable=True)  # 'heuristic' | 'heuristic+if'

    accounts_involved = Column(JSON, nullable=False)  # list of account_ids
    window_start = Column(DateTime, nullable=False)   # 09:15 IST of event_date (kept for compat)
    window_end = Column(DateTime, nullable=False)     # 15:30 IST of event_date

    # Dedup key: the trading date this anomaly refers to (IST, Date only).
    # NOT NULL — every alert must have a date.  The ingest layer derives this
    # from window_start converted to IST; demo generators use the trade date.
    # Migration: existing rows with NULL are back-filled by init_db() below.
    event_date = Column(Date, nullable=False, index=True, server_default="2000-01-01")

    # Phase 1: re-detection tracking — updated on every scan that fires for
    # the same (instrument, pattern, event_date) rather than inserting a new row.
    last_seen_at = Column(DateTime, nullable=True)  # UTC, updated on every re-detection
    occurrences = Column(Integer, nullable=False, server_default="1", default=1)

    explanation = Column(String(4096), nullable=False)  # human-readable reasoning

    # Phase 4: provenance — where this alert came from.
    # live_fyers   = real Fyers scan
    # eod_history  = end-of-day history scan
    # synthetic_demo = seeded demo data
    # backtest     = generated by backtest run
    source = Column(String(32), nullable=False, server_default="live_fyers")

    detected_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(16), default="open")  # open/investigating/escalated/closed
    escalated_to_sebi = Column(Boolean, default=False)

    instrument = relationship("Instrument")

    # Dedup constraint keyed on event_date (the trading session), not window_start
    # (the wall-clock time). Without this, every 5-minute scan inserts a new row
    # for the same event because window_start = now - 6h never repeats.
    # With this, the second INSERT raises IntegrityError → caller treats as
    # "already exists, increment occurrences".
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "pattern_type",
            "event_date",
            name="uq_alert_instrument_pattern_eventdate",
        ),
    )


# ---------------------------------------------------------------------------
# ScanRun — single source of truth for "is processing happening?"
# ---------------------------------------------------------------------------

class ScanRun(Base):
    """
    One row per scheduler invocation (or manual trigger). Written at the END
    of every scan, including skipped runs (e.g. token missing, outside hours).

    This is the only honest answer to "when did detection last run, and what
    happened?" — the dashboard must read this table, not an in-memory dict.
    """

    __tablename__ = "scan_runs"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    # "intraday" = scheduled during market hours
    # "eod"      = end-of-day scan triggered after 15:30 IST
    # "manual"   = triggered via API endpoint
    mode = Column(String(16), nullable=False)  # intraday|eod|manual
    symbols_scanned = Column(Integer, nullable=False, default=0)
    symbols_failed = Column(Integer, nullable=False, default=0)
    detections = Column(Integer, nullable=False, default=0)   # raw detector hits
    alerts_created = Column(Integer, nullable=False, default=0)  # new rows inserted
    alerts_updated = Column(Integer, nullable=False, default=0)  # occurrences incremented
    # "ok"      = completed normally
    # "skipped" = not authenticated, outside hours, or holiday
    # "error"   = unexpected exception
    status = Column(String(16), nullable=False)
    error_summary = Column(String(1024), nullable=True)
    # Last bar timestamp used for detection (None if skipped before data fetch)
    data_as_of = Column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# OptionChainSnapshot — raw option chain stored on every scan
# ---------------------------------------------------------------------------

class OptionChainSnapshot(Base):
    """
    One row per strike/type per scan. Fyers provides only the CURRENT OI at
    the moment of the call; there is no historical OI endpoint. By storing
    snapshots on every scan we can compute OI delta between scans.

    NOTE: OI change computed from these snapshots is an approximation —
    it reflects change between our scan intervals, not tick-level OI changes.
    The UI must say so explicitly.
    """

    __tablename__ = "option_chain_snapshots"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    scan_run_id = Column(String(36), ForeignKey("scan_runs.id"), nullable=True)
    snapshot_at = Column(DateTime, nullable=False, index=True)  # UTC
    underlying = Column(String(32), nullable=False, index=True)  # e.g. "NIFTY50"
    expiry = Column(Date, nullable=False)
    strike = Column(Float, nullable=False)
    option_type = Column(String(2), nullable=False)  # CE or PE
    oi = Column(Integer, nullable=True)
    volume = Column(Integer, nullable=True)
    ltp = Column(Float, nullable=True)
    iv = Column(Float, nullable=True)  # implied volatility, if returned by broker
