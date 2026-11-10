"""
app/api/live_detect.py  —  Real-time manipulation detection endpoints.

GET  /api/live/scan          — scan top watchlist, return fresh detections
POST /api/live/scan/{symbol} — single symbol on-demand
GET  /api/live/scan/status   — last scan summary (backed by scan_runs table)
GET  /api/live/scan/runs     — recent scan_runs rows
GET  /api/live/heatmap       — anomaly scores for all instruments

Dedup rule (Phase 1):
  Alerts are keyed on (instrument_id, pattern_type, event_date).
  event_date = the trading date the anomaly refers to (IST date, closed session).
  On re-detection: UPDATE last_seen_at and occurrences; never INSERT duplicate.
  window_start / window_end = 09:15–15:30 IST of event_date (kept for compatibility).

Rate-limit strategy (Fyers Standard plan = 5 req/sec):
  • ONE batch quotes call for all symbols  (1 request total)
  • Per-symbol history calls with 0.25 s sleep (≤4/sec)
  • Circuit-breaker in FyersMarketDataAdapter absorbs bursts
"""

import logging
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pytz
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.db.session import get_db
from app.db.models import Alert, Instrument, InstrumentType, ScanRun
from app.workers.detection_worker import run_detection_for_symbol, build_fyers_symbol

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/live", tags=["live-detect"])

IST = pytz.timezone("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Scan state  (in-process, single-worker; source-of-truth is scan_runs table)
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_state: Dict[str, Any] = {
    "timestamp": None, "symbols_scanned": 0, "alerts_generated": 0,
    "detections": [], "running": False, "error": None,
}

# Prioritised scan watchlist — market-cap weighted, indices first
SCAN_WATCHLIST = [
    ("NIFTY50",    "INDEX"),  ("BANKNIFTY",  "INDEX"),  ("FINNIFTY",   "INDEX"),
    ("RELIANCE",   "EQUITY"), ("TCS",        "EQUITY"), ("HDFCBANK",   "EQUITY"),
    ("ICICIBANK",  "EQUITY"), ("INFY",       "EQUITY"), ("HINDUNILVR", "EQUITY"),
    ("ITC",        "EQUITY"), ("SBIN",       "EQUITY"), ("BHARTIARTL", "EQUITY"),
    ("KOTAKBANK",  "EQUITY"), ("LT",         "EQUITY"), ("AXISBANK",   "EQUITY"),
    ("BAJFINANCE", "EQUITY"), ("HCLTECH",    "EQUITY"), ("ASIANPAINT", "EQUITY"),
    ("MARUTI",     "EQUITY"), ("YESBANK",    "EQUITY"), ("ADANIENT",   "EQUITY"),
    ("TATASTEEL",  "EQUITY"), ("JSWSTEEL",   "EQUITY"), ("VEDL",       "EQUITY"),
    ("ZOMATO",     "EQUITY"), ("TATAMOTORS", "EQUITY"),
]


def _get_adapter():
    try:
        from data.ingest.fyers_stream import FyersMarketDataAdapter
        return FyersMarketDataAdapter()
    except Exception as e:
        logger.error("Fyers adapter init failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Alert persistence with proper dedup
# ---------------------------------------------------------------------------

def _event_window(event_date: date) -> Tuple[datetime, datetime]:
    """Return (window_start, window_end) = 09:15–15:30 IST for the given date."""
    ist_start = IST.localize(datetime(event_date.year, event_date.month, event_date.day, 9, 15))
    ist_end   = IST.localize(datetime(event_date.year, event_date.month, event_date.day, 15, 30))
    return ist_start.astimezone(pytz.utc).replace(tzinfo=None), \
           ist_end.astimezone(pytz.utc).replace(tzinfo=None)


def _persist_or_update(
    db: Session,
    instrument: Instrument,
    det: Dict,
    source: str = "live_fyers",
) -> Tuple[Optional[Alert], bool]:
    """
    Insert a new Alert or update an existing one for the same event_date.

    Returns (alert_object, is_new):
        is_new=True  → new row was inserted
        is_new=False → existing row was updated (occurrences incremented)
    """
    event_date: date = det.get("event_date") or date.today()
    w_start, w_end   = _event_window(event_date)
    now_utc           = datetime.utcnow()

    # Try to find an existing alert for this (instrument, pattern, event_date)
    existing = (
        db.query(Alert)
        .filter(
            Alert.instrument_id == instrument.id,
            Alert.pattern_type  == det["pattern_type"],
            Alert.event_date    == event_date,
        )
        .first()
    )

    if existing:
        # Re-detection: update metadata, never insert duplicate
        existing.last_seen_at = now_utc
        existing.occurrences  = (existing.occurrences or 1) + 1
        # Only upgrade severity/score, never downgrade
        sev_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        if det["score"] > existing.score:
            existing.score    = det["score"]
            existing.severity = det["severity"]
        try:
            db.commit()
            db.refresh(existing)
        except Exception as e:
            db.rollback()
            logger.error("Alert update failed: %s", e)
        return existing, False

    # Insert new alert
    try:
        a = Alert(
            id=str(uuid.uuid4()),
            instrument_id=instrument.id,
            pattern_type=det["pattern_type"],
            severity=det["severity"],
            score=det["score"],
            accounts_involved=[],
            window_start=w_start,
            window_end=w_end,
            event_date=event_date,
            last_seen_at=now_utc,
            occurrences=1,
            explanation=det["explanation"],
            status="open",
            source=source,
        )
        db.add(a)
        db.commit()
        db.refresh(a)
        return a, True
    except IntegrityError:
        # Race condition: another concurrent scan inserted the same row
        db.rollback()
        # Fetch and update it
        existing = (
            db.query(Alert)
            .filter(
                Alert.instrument_id == instrument.id,
                Alert.pattern_type  == det["pattern_type"],
                Alert.event_date    == event_date,
            )
            .first()
        )
        if existing:
            existing.last_seen_at = now_utc
            existing.occurrences  = (existing.occurrences or 1) + 1
            try:
                db.commit()
            except Exception:
                db.rollback()
        return existing, False
    except Exception as e:
        db.rollback()
        logger.error("Alert persist failed: %s", e)
        return None, False


# ---------------------------------------------------------------------------
# Core scan runner
# ---------------------------------------------------------------------------

def _run_scan(
    db: Session,
    symbols: List[tuple],
    mode: str = "manual",
    source: str = "live_fyers",
) -> Dict:
    """
    Run detection for a list of symbols.  Writes a ScanRun row to the DB.

    Returns summary dict. Never raises — catches all errors and records them.
    """
    scan_id    = str(uuid.uuid4())
    started_at = datetime.utcnow()
    scan_run   = ScanRun(
        id=scan_id,
        started_at=started_at,
        mode=mode,
        status="running",
        symbols_scanned=0,
        symbols_failed=0,
        detections=0,
        alerts_created=0,
        alerts_updated=0,
    )
    db.add(scan_run)
    try:
        db.commit()
    except Exception:
        db.rollback()

    adapter = _get_adapter()
    if not adapter:
        _finish_scan(db, scan_run, "skipped", "Fyers adapter unavailable — check token", started_at)
        return {
            "scan_id": scan_id,
            "error": "Fyers adapter unavailable — check token",
            "detections": [],
            "symbols_scanned": 0,
            "symbols_failed": 0,
            "alerts_created": 0,
            "alerts_updated": 0,
            "status": "skipped",
        }

    summaries   : List[Dict] = []
    skipped     : List[str]  = []
    alerts_n    = 0
    alerts_upd  = 0
    scanned     = 0
    failed      = 0
    data_as_of  = None

    # ── Step 1: Batch quotes — ONE call for all non-FUTURE symbols ────────
    # FUTURE symbols require expiry resolution (not implemented here);
    # they are logged as skipped rather than silently broken.
    tradeable = []
    for sym, itype in symbols:
        fsym = build_fyers_symbol(sym, itype)
        if not fsym:
            skipped.append(f"{sym}: skipped — FUTURE contract expiry not resolved (see detection_worker.py)")
            logger.warning("FUTURE contract not resolved for %s — skipped", sym)
            continue
        tradeable.append((sym, itype, fsym))

    fyers_syms = [fsym for _, _, fsym in tradeable]
    quote_map: Dict[str, Dict] = {}
    if fyers_syms:
        try:
            all_quotes = adapter.get_quotes_normalized(fyers_syms)
            quote_map  = {q.get("symbol", ""): q for q in all_quotes}
        except Exception as e:
            logger.error("Batch quotes failed: %s", e)

    # ── Step 2: Per-symbol history + detection ────────────────────────────
    for sym, itype, fsym in tradeable:
        inst = db.query(Instrument).filter(
            Instrument.symbol == sym, Instrument.exchange == "NSE"
        ).first()
        if not inst:
            skipped.append(f"{sym}: not in DB")
            continue

        try:
            dets, skip_reason = run_detection_for_symbol(
                sym, fsym, adapter, itype,
                cached_quote=quote_map.get(fsym),
            )
            scanned += 1

            if skip_reason:
                skipped.append(f"{sym}: {skip_reason}")
                logger.info("Skipped %s: %s", sym, skip_reason)
                continue

            for d in dets:
                a, is_new = _persist_or_update(db, inst, d, source=source)
                if is_new:
                    alerts_n += 1
                else:
                    alerts_upd += 1
                if a and d.get("event_date"):
                    if data_as_of is None or d["event_date"] > (data_as_of.date() if hasattr(data_as_of, 'date') else data_as_of):
                        data_as_of = datetime.combine(d["event_date"], datetime.min.time())
                summaries.append({
                    "symbol": sym,
                    "pattern": d["pattern_type"],
                    "severity": d["severity"],
                    "score": d["score"],
                    "event_date": str(d.get("event_date", "")),
                    "new_alert": is_new,
                    "occurrences": a.occurrences if a else 1,
                    "explanation_short": d["explanation"][:300],
                })

        except Exception as e:
            failed += 1
            logger.error("Scan error %s: %s", sym, e)
            skipped.append(f"{sym}: unexpected error — {e}")

        # Rate-limit: ≤4 history calls/sec on Standard plan
        time.sleep(0.25)

    # ── Step 3: Derivative detectors (Phase 3) ─────────────────────────────
    # Run OI concentration, OI-IV decoupling, option pinning, basis distortion
    # for NIFTY50/BANKNIFTY/FINNIFTY using the full app/detection/ detector set.
    try:
        from app.detection.fyers_adapters import run_derivative_detectors
        deriv_dets = run_derivative_detectors(adapter, db, scan_run_id=scan_id)
        for d in deriv_dets:
            # Derivative detections are keyed to the index instrument in DB
            sym_key = d.get("underlying", "")
            inst = db.query(Instrument).filter(
                Instrument.symbol == sym_key, Instrument.exchange == "NSE"
            ).first()
            if not inst:
                skipped.append(f"{sym_key}: instrument not in DB (derivative detection)")
                continue
            a, is_new = _persist_or_update(db, inst, d, source=source)
            if is_new:
                alerts_n += 1
            else:
                alerts_upd += 1
            summaries.append({
                "symbol": sym_key,
                "pattern": d["pattern_type"],
                "severity": d["severity"],
                "score": d["score"],
                "event_date": str(d.get("event_date", "")),
                "new_alert": is_new,
                "occurrences": a.occurrences if a else 1,
                "explanation_short": d["explanation"][:300],
                "detector_type": "derivative",
            })
    except Exception as e:
        logger.error("Derivative detector step failed: %s", e, exc_info=True)
        skipped.append(f"derivative_detectors: error — {e}")

    _finish_scan(
        db, scan_run, "ok", None, started_at,
        symbols_scanned=scanned,
        symbols_failed=failed,
        detections=len(summaries),
        alerts_created=alerts_n,
        alerts_updated=alerts_upd,
        data_as_of=data_as_of,
    )

    return {
        "scan_id": scan_id,
        "symbols_scanned": scanned,
        "symbols_failed": failed,
        "alerts_created": alerts_n,
        "alerts_updated": alerts_upd,
        "detections": summaries,
        "skipped": skipped,
        "error": None,
        "status": "ok",
    }


def _finish_scan(
    db: Session,
    scan_run: ScanRun,
    status: str,
    error_summary: Optional[str],
    started_at: datetime,
    **kwargs,
) -> None:
    """Update the ScanRun row with final results."""
    try:
        scan_run.finished_at     = datetime.utcnow()
        scan_run.status          = status
        scan_run.error_summary   = error_summary
        for k, v in kwargs.items():
            if hasattr(scan_run, k):
                setattr(scan_run, k, v)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error("ScanRun update failed: %s", e)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/scan/status")
def scan_status(db: Session = Depends(get_db)):
    """
    Last scan summary, backed by the scan_runs table.
    Returns the most recent ScanRun row (any mode).
    Also returns in-process running flag from memory.
    """
    last_run = (
        db.query(ScanRun)
        .order_by(ScanRun.started_at.desc())
        .first()
    )
    with _lock:
        running = _state["running"]

    if last_run is None:
        return {
            "running": running,
            "last_scan": None,
            "message": "No scan has been run yet. Trigger /api/live/scan or wait for scheduler.",
        }

    return {
        "running": running,
        "scan_id": last_run.id,
        "started_at": last_run.started_at.isoformat() + "Z" if last_run.started_at else None,
        "finished_at": last_run.finished_at.isoformat() + "Z" if last_run.finished_at else None,
        "mode": last_run.mode,
        "status": last_run.status,
        "symbols_scanned": last_run.symbols_scanned,
        "symbols_failed": last_run.symbols_failed,
        "detections": last_run.detections,
        "alerts_created": last_run.alerts_created,
        "alerts_updated": last_run.alerts_updated,
        "error_summary": last_run.error_summary,
        "data_as_of": last_run.data_as_of.isoformat() + "Z" if last_run.data_as_of else None,
    }


@router.get("/scan/runs")
def scan_runs(limit: int = Query(default=20, le=100), db: Session = Depends(get_db)):
    """Recent scan_runs rows — the full scheduler activity log."""
    runs = (
        db.query(ScanRun)
        .order_by(ScanRun.started_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "total": len(runs),
        "runs": [
            {
                "id": r.id,
                "started_at": r.started_at.isoformat() + "Z" if r.started_at else None,
                "finished_at": r.finished_at.isoformat() + "Z" if r.finished_at else None,
                "mode": r.mode,
                "status": r.status,
                "symbols_scanned": r.symbols_scanned,
                "symbols_failed": r.symbols_failed,
                "detections": r.detections,
                "alerts_created": r.alerts_created,
                "alerts_updated": r.alerts_updated,
                "error_summary": r.error_summary,
                "data_as_of": r.data_as_of.isoformat() + "Z" if r.data_as_of else None,
            }
            for r in runs
        ],
    }


@router.get("/scan")
def run_scan(
    max_symbols: int = Query(default=10, le=26, description="Max symbols to scan"),
    db: Session = Depends(get_db),
):
    """Run real-time manipulation scan on live/last-session market data."""
    with _lock:
        if _state["running"]:
            return {"status": "already_running", "last_scan": _state["timestamp"]}
        _state["running"] = True
        _state["error"]   = None

    try:
        result = _run_scan(db, SCAN_WATCHLIST[:max_symbols], mode="manual")
        with _lock:
            _state.update({
                "timestamp":        datetime.utcnow().isoformat() + "Z",
                "symbols_scanned":  result["symbols_scanned"],
                "alerts_generated": result["alerts_created"],
                "detections":       result["detections"],
                "error":            result.get("error"),
                "running":          False,
            })
        return {
            "status": "complete",
            "scan_id": result["scan_id"],
            "timestamp": _state["timestamp"],
            "symbols_scanned": result["symbols_scanned"],
            "alerts_created": result["alerts_created"],
            "alerts_updated": result["alerts_updated"],
            "detections": result["detections"],
            "skipped": result.get("skipped", []),
            "data_note": (
                "Signals derived from Fyers daily OHLCV (last N sessions). "
                "Only completed (closed) sessions are evaluated — no partial intraday bars. "
                "Dedup key: (instrument, pattern, event_date). "
                "All thresholds are UNVALIDATED_HEURISTIC — see /api/system/detector-status."
            ),
        }
    except Exception as e:
        with _lock:
            _state["running"] = False
            _state["error"]   = str(e)
        logger.error("Scan top-level error: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}


@router.post("/scan/{symbol}")
def scan_symbol(symbol: str, db: Session = Depends(get_db)):
    """On-demand scan for a single symbol. Example: POST /api/live/scan/RELIANCE"""
    symbol = symbol.upper().strip()
    inst = db.query(Instrument).filter(
        Instrument.symbol == symbol, Instrument.exchange == "NSE"
    ).first()
    if not inst:
        return {"error": f"{symbol} not found in DB"}

    itype  = inst.instrument_type.value.upper()
    fsym   = build_fyers_symbol(symbol, itype)
    if not fsym:
        return {
            "error": f"{symbol} is a FUTURE — expiry resolution not implemented. "
                     "Skipped rather than using an expired contract."
        }

    adapter = _get_adapter()
    if not adapter:
        return {"error": "Fyers adapter unavailable"}

    try:
        dets, skip_reason = run_detection_for_symbol(symbol, fsym, adapter, itype)
        if skip_reason:
            return {"symbol": symbol, "skipped": True, "reason": skip_reason}

        created = updated = 0
        result_dets = []
        for d in dets:
            a, is_new = _persist_or_update(db, inst, d)
            if is_new:
                created += 1
            else:
                updated += 1
            result_dets.append({
                **d,
                "event_date": str(d.get("event_date", "")),
                "new_alert": is_new,
                "occurrences": a.occurrences if a else 1,
            })

        return {
            "symbol": symbol,
            "fyers_symbol": fsym,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "detections_found": len(dets),
            "alerts_created": created,
            "alerts_updated": updated,
            "detections": result_dets,
        }
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


@router.get("/heatmap")
def heatmap(db: Session = Depends(get_db)):
    """Anomaly scores for all instruments — used by dashboard heatmap."""
    insts = db.query(Instrument).filter(
        Instrument.instrument_type.in_([InstrumentType.EQUITY, InstrumentType.INDEX])
    ).all()

    rows = []
    for inst in insts:
        latest = (
            db.query(Alert)
            .filter(Alert.instrument_id == inst.id)
            .order_by(Alert.detected_at.desc())
            .first()
        )
        rows.append({
            "symbol": inst.symbol,
            "exchange": inst.exchange,
            "instrument_type": inst.instrument_type.value,
            "anomaly_score": round(latest.score, 3) if latest else 0.0,
            "severity": latest.severity if latest else "none",
            "pattern": latest.pattern_type if latest else None,
            "alert_id": latest.id if latest else None,
            "last_detected": (latest.detected_at.isoformat() + "Z") if latest else None,
            "event_date": str(latest.event_date) if latest and latest.event_date else None,
            "occurrences": latest.occurrences if latest else 0,
            "source": latest.source if latest else None,
        })

    rows.sort(key=lambda x: x["anomaly_score"], reverse=True)
    return {
        "instruments": rows,
        "total": len(rows),
        "flagged": sum(1 for r in rows if r["anomaly_score"] > 0),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
