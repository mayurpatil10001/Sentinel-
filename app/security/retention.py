"""
Data Retention — Purge Expired Orders and Trades
==================================================

Enforces a configurable retention window: deletes Order and Trade rows
older than `retention_days`, EXCEPT any rows referenced by an open or
escalated Alert. Evidence tied to an active investigation must NEVER be
purged regardless of age.

RETENTION PERIOD DEFAULT — IS IT DEFENSIBLE?
---------------------------------------------
Default: RETENTION_DAYS_DEFAULT = 2555 (approximately 7 years).

Honest answer: 7 years is a REASONED ESTIMATE, not a confirmed legal
requirement. Here is the reasoning and the uncertainty:

  Known:
    - SEBI Regulation 20 (SEBI Act 1992) gives SEBI inspection powers
      going back an unspecified period. In practice, SEBI enforcement
      orders have cited conduct 5-7 years prior.
    - The Companies Act 2013 requires companies to maintain books of
      account for 8 years (Section 128). This is not directly applicable
      to broker order records but is a useful reference point.
    - The Limitation Act 1963 (Section 37, Arbitration) sets a 3-year
      limitation for most civil claims, but SEBI enforcement is not a
      civil claim.
    - SEBI (Stock Brokers and Sub-Brokers) Regulations 1992, Regulation 17:
      brokers must preserve records for a minimum of 5 years.

  Conclusion:
    5 years is the known minimum for broker records. 7 years provides a
    safety margin. 8 years would match Companies Act. This is a PROJECT
    DEFAULT — it must be validated against the actual regulatory posture
    of the deploying entity (exchange, broker, regulator) before any
    production deployment. It is explicitly NOT a legal opinion.

WHAT IS PURGED vs PRESERVED
------------------------------
  PURGED: Order and Trade rows older than retention_days AND not
          referenced by any Alert in status "open" or "investigating"
          or "escalated".

  PRESERVED: Any Order/Trade row where:
    a) Its timestamp is within retention_days of today, OR
    b) It falls within the window of an Alert in status
       "open", "investigating", or "escalated" — i.e., any
       Alert that has NOT been closed.

  Note: "closed" alerts do NOT protect rows from purging. The rationale
  is that once an analyst closes an alert (no case proceeding), the
  underlying raw data reverts to its normal retention schedule.
  If an Alert has been "escalated" (to SEBI), its evidence must be
  preserved indefinitely until the case is formally resolved — but
  this function uses retention_days as the floor, not an indefinite hold.
  A compliance process should close or archive escalated alerts before
  the data is allowed to be purged.

HARD RULE #1 COMPLIANCE
-------------------------
This module does NOT modify any existing column. It only DELETEs rows.
It does not touch instruments, alerts, or access logs.
Migration path: none required — this is a DELETE operation, not a schema
change.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from app.db.models import Order, Trade, Alert

logger = logging.getLogger(__name__)

# ── Default retention ─────────────────────────────────────────────────────────
# Label: REASONED ESTIMATE — NOT a confirmed legal requirement.
# See module docstring for the reasoning. Validate with compliance before
# deploying to production with real account data.
RETENTION_DAYS_DEFAULT: int = 2555   # ≈ 7 years

# Alert statuses that PROTECT their evidence from purging.
# "closed" deliberately excluded — see docstring for rationale.
_ACTIVE_ALERT_STATUSES = {"open", "investigating", "escalated"}


def purge_expired_orders(
    db: Session,
    retention_days: int = RETENTION_DAYS_DEFAULT,
    dry_run: bool = False,
) -> dict:
    """
    Delete Order and Trade rows outside the retention window that are not
    protected by an active Alert.

    Parameters
    ----------
    db
        Active SQLAlchemy session.
    retention_days
        Number of days to retain data. Rows older than this are candidates
        for deletion. Default is RETENTION_DAYS_DEFAULT (≈7 years).
        MUST be validated against actual regulatory requirements before use.
    dry_run
        If True, counts what WOULD be deleted without committing any
        changes. Use for testing and audit preview.

    Returns
    -------
    dict with keys:
        orders_deleted     int  — rows deleted (or would-be if dry_run)
        trades_deleted     int  — rows deleted (or would-be if dry_run)
        cutoff_date        str  — ISO8601 cutoff date used
        protected_by_alert int  — how many rows were skipped due to active Alert
        dry_run            bool — whether this was a real deletion

    HARD RULE #1: raises TypeError if db is None.
    HARD RULE #1: raises ValueError if retention_days < 1.
    """
    if db is None:
        raise TypeError(
            "purge_expired_orders: db is None. Pass an active SQLAlchemy session."
        )
    if retention_days < 1:
        raise ValueError(
            f"purge_expired_orders: retention_days must be >= 1, got {retention_days}."
        )

    cutoff = datetime.utcnow() - timedelta(days=retention_days)

    # ── Find instrument_ids + windows of ACTIVE alerts ────────────────────────
    # We protect any Order/Trade that falls WITHIN the window of an open alert.
    # Using window-based protection (not individual order IDs) because:
    # 1. The Alert stores the window boundaries, not individual order IDs.
    # 2. ALL orders in the window may be needed for context even if only
    #    some were in accounts_involved.
    active_alerts = (
        db.query(Alert)
        .filter(Alert.status.in_(_ACTIVE_ALERT_STATUSES))
        .all()
    )

    # Build a set of (instrument_id, window_start, window_end) tuples
    protected_windows = [
        (a.instrument_id, a.window_start, a.window_end)
        for a in active_alerts
    ]

    # ── Query expired orders ──────────────────────────────────────────────────
    expired_orders_q = db.query(Order).filter(Order.timestamp < cutoff)
    expired_order_ids = [o.id for o in expired_orders_q.all()]

    # Filter out orders protected by active alert windows
    orders_to_delete = []
    orders_protected = 0
    for order in expired_orders_q.all():
        is_protected = any(
            order.instrument_id == instr_id
            and window_start <= order.timestamp <= window_end
            for instr_id, window_start, window_end in protected_windows
        )
        if is_protected:
            orders_protected += 1
        else:
            orders_to_delete.append(order)

    # ── Query expired trades ───────────────────────────────────────────────────
    expired_trades_q = db.query(Trade).filter(Trade.timestamp < cutoff)
    trades_to_delete = []
    trades_protected = 0
    for trade in expired_trades_q.all():
        is_protected = any(
            trade.instrument_id == instr_id
            and window_start <= trade.timestamp <= window_end
            for instr_id, window_start, window_end in protected_windows
        )
        if is_protected:
            trades_protected += 1
        else:
            trades_to_delete.append(trade)

    orders_deleted = len(orders_to_delete)
    trades_deleted = len(trades_to_delete)
    total_protected = orders_protected + trades_protected

    if not dry_run:
        for order in orders_to_delete:
            db.delete(order)
        for trade in trades_to_delete:
            db.delete(trade)
        db.commit()
        logger.info(
            "Retention purge completed: %d orders, %d trades deleted. "
            "%d rows protected by active alerts. Cutoff: %s",
            orders_deleted, trades_deleted, total_protected, cutoff.isoformat()
        )
    else:
        logger.info(
            "Retention purge DRY RUN: would delete %d orders, %d trades. "
            "%d rows protected by active alerts. Cutoff: %s",
            orders_deleted, trades_deleted, total_protected, cutoff.isoformat()
        )

    return {
        "orders_deleted": orders_deleted,
        "trades_deleted": trades_deleted,
        "cutoff_date": cutoff.isoformat(),
        "protected_by_alert": total_protected,
        "dry_run": dry_run,
        "retention_days": retention_days,
    }


def purge_cli_main():
    """
    Entry point for cron/CLI invocation.

    Usage:
        python -m app.security.retention [--retention-days N] [--dry-run]

    Reads RETENTION_DAYS from the SENTINEL_RETENTION_DAYS environment
    variable, falling back to RETENTION_DAYS_DEFAULT.
    Reads the database URL from DATABASE_URL environment variable.
    """
    import argparse
    import os
    from app.db.session import get_db

    parser = argparse.ArgumentParser(
        description="Sentinel data retention purge — removes expired order/trade data."
    )
    parser.add_argument(
        "--retention-days", type=int,
        default=int(os.environ.get("SENTINEL_RETENTION_DAYS", RETENTION_DAYS_DEFAULT)),
        help=f"Retention window in days. Default: {RETENTION_DAYS_DEFAULT} (≈7 years). "
             "VALIDATE AGAINST SEBI/COMPLIANCE REQUIREMENTS BEFORE USE."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be deleted without actually deleting anything."
    )
    args = parser.parse_args()

    print(f"Sentinel Retention Purge")
    print(f"  retention_days : {args.retention_days}")
    print(f"  dry_run        : {args.dry_run}")
    print()

    for db in get_db():
        result = purge_expired_orders(
            db,
            retention_days=args.retention_days,
            dry_run=args.dry_run,
        )
        print(f"Result: {result}")


if __name__ == "__main__":
    purge_cli_main()
