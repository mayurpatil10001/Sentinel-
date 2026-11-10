"""
app/scheduler.py
=================
Background detection scheduler for Sentinel.

Schedule:
  Market hours (Mon–Fri, 09:15–15:30 IST): scan every SCAN_INTERVAL_MINUTES (default 5).
  After 15:30 IST: one EOD scan (once per day).
  Outside hours / weekends: idle, writes a "skipped: outside market hours" ScanRun.

Exchange holidays:
  We do not maintain a holiday list. If the market is a holiday, Fyers returns
  no live data (volume=0, ltp unchanged from prior session). The scheduler still
  runs and writes a ScanRun; detection_worker will find only closed historical bars
  and report them normally (no live data to pollute results).
  The ScanRun.error_summary will note "holidays not handled; market data may be stale".

Token expiry:
  Fyers tokens expire at ~09:00 IST the next day. If the adapter fails to init,
  the scan is logged as "skipped: not authenticated" — never fake data.

Config env vars:
  SCAN_INTERVAL_MINUTES  — how often to scan during market hours (default 5)
  LOOKBACK_SESSIONS      — passed through to detection_worker (default 5)
"""

import logging
import os
import uuid
from datetime import datetime, timedelta, date

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.db.models import ScanRun

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

SCAN_INTERVAL_MINUTES: int = int(os.environ.get("SCAN_INTERVAL_MINUTES", "5"))

# Track whether we've run the EOD scan for the current date
_eod_done_for: date = None


def _now_ist() -> datetime:
    return datetime.now(IST)


def _is_market_open(now_ist: datetime) -> bool:
    """True during Mon–Fri 09:15–15:30 IST."""
    if now_ist.weekday() >= 5:   # Saturday=5, Sunday=6
        return False
    market_open  = now_ist.replace(hour=9,  minute=15, second=0, microsecond=0)
    market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now_ist <= market_close


def _is_eod(now_ist: datetime) -> bool:
    """True in the 30-minute window after market close (15:30–16:00) on weekdays."""
    if now_ist.weekday() >= 5:
        return False
    eod_start = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    eod_end   = now_ist.replace(hour=16, minute=0,  second=0, microsecond=0)
    return eod_start <= now_ist < eod_end


def _write_skipped_run(db: Session, reason: str, mode: str) -> None:
    """Write a ScanRun row for a scan that was skipped (no data fetched)."""
    now = datetime.utcnow()
    run = ScanRun(
        id=str(uuid.uuid4()),
        started_at=now,
        finished_at=now,
        mode=mode,
        status="skipped",
        symbols_scanned=0,
        symbols_failed=0,
        detections=0,
        alerts_created=0,
        alerts_updated=0,
        error_summary=reason,
    )
    db.add(run)
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error("Could not write skipped ScanRun: %s", e)


async def scheduled_scan() -> None:
    """
    Called by APScheduler every SCAN_INTERVAL_MINUTES.
    Decides whether to run intraday scan, EOD scan, or skip.
    """
    global _eod_done_for
    now = _now_ist()
    today = now.date()

    db: Session = SessionLocal()
    try:
        if _is_market_open(now):
            # Intraday scan
            logger.info("[scheduler] Market open — running intraday scan at %s IST", now.strftime("%H:%M"))
            await _do_scan(db, mode="intraday")

        elif _is_eod(now) and _eod_done_for != today:
            # EOD scan — only once per day
            logger.info("[scheduler] EOD window — running end-of-day scan at %s IST", now.strftime("%H:%M"))
            await _do_scan(db, mode="eod")
            _eod_done_for = today

        else:
            reason = (
                "outside market hours (09:15–15:30 IST Mon–Fri)"
                if now.weekday() < 5
                else "weekend"
            )
            if _eod_done_for == today:
                reason = "EOD scan already completed for today"
            logger.debug("[scheduler] Skipping scan: %s", reason)
            # Write a skipped row only once per 30 minutes to avoid flooding the table
            # (we do this only if no run exists in the last 30 minutes)
            last_run = db.query(ScanRun).order_by(ScanRun.started_at.desc()).first()
            cutoff = datetime.utcnow() - timedelta(minutes=30)
            if last_run is None or last_run.started_at < cutoff:
                _write_skipped_run(db, reason, mode="intraday")

    except Exception as e:
        logger.error("[scheduler] Unhandled error in scheduled_scan: %s", e, exc_info=True)
    finally:
        db.close()


async def _do_scan(db: Session, mode: str) -> None:
    """
    Perform an actual detection scan.  Checks token validity first.
    Records a "skipped: not authenticated" ScanRun if token is missing/stale.
    """
    from app.api.live_detect import SCAN_WATCHLIST, _run_scan

    # Token check — never fake data if token is absent
    try:
        from data.ingest.fyers_stream import FyersMarketDataAdapter
        adapter = FyersMarketDataAdapter()
        status  = adapter.get_connection_status()
        if not status.get("token_valid", False):
            reason = "not authenticated — Fyers token missing or expired. Re-run get_token.py."
            logger.warning("[scheduler] %s", reason)
            _write_skipped_run(db, reason, mode=mode)
            return
        # Holidays: we cannot know for certain but log it
        logger.info(
            "[scheduler] Token valid (prefix: %s). Note: exchange holidays are not handled — "
            "if today is a holiday, market data will be stale (last session).",
            status.get("app_id_prefix", "?"),
        )
    except Exception as e:
        reason = f"Fyers adapter init error: {e}"
        logger.error("[scheduler] %s", reason)
        _write_skipped_run(db, reason, mode=mode)
        return

    # Run detection (this writes its own ScanRun row)
    try:
        source = "eod_history" if mode == "eod" else "live_fyers"
        result = _run_scan(db, SCAN_WATCHLIST, mode=mode, source=source)
        logger.info(
            "[scheduler] %s scan complete: %d scanned, %d detections, "
            "%d alerts_created, %d alerts_updated, %d failed",
            mode,
            result.get("symbols_scanned", 0),
            len(result.get("detections", [])),
            result.get("alerts_created", 0),
            result.get("alerts_updated", 0),
            result.get("symbols_failed", 0),
        )
    except Exception as e:
        logger.error("[scheduler] Scan error during %s scan: %s", mode, e, exc_info=True)


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------

_scheduler: AsyncIOScheduler = None


def start_scheduler() -> AsyncIOScheduler:
    """Create and start the APScheduler instance. Called from FastAPI lifespan."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        logger.warning("Scheduler already running — not starting again.")
        return _scheduler

    _scheduler = AsyncIOScheduler(timezone=str(IST))

    # Fire every SCAN_INTERVAL_MINUTES during and around market hours.
    # We run it continuously; the scheduled_scan() function decides whether
    # to actually scan or skip based on the current IST time.
    _scheduler.add_job(
        scheduled_scan,
        CronTrigger(
            minute=f"*/{SCAN_INTERVAL_MINUTES}",
            timezone=str(IST),
        ),
        id="sentinel_scan",
        name="Sentinel scheduled scan",
        max_instances=1,          # never run two scans concurrently
        misfire_grace_time=60,    # if a scan fires late, skip it rather than pile up
    )

    _scheduler.start()
    logger.info(
        "[scheduler] Started. Interval: every %d min IST. "
        "Market hours: 09:15–15:30 IST Mon–Fri.",
        SCAN_INTERVAL_MINUTES,
    )
    return _scheduler


def stop_scheduler() -> None:
    """Shut down the scheduler. Called from FastAPI lifespan shutdown."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[scheduler] Stopped.")

