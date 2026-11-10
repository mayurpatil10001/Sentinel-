"""
app/api/live_detect.py  —  Real-time manipulation detection endpoints.

GET  /api/live/scan          — scan top watchlist, return fresh detections
POST /api/live/scan/{symbol} — single symbol on-demand
GET  /api/live/scan/status   — last scan summary
GET  /api/live/heatmap       — anomaly scores for all instruments

Rate-limit strategy (Fyers Standard plan = 5 req/sec):
  • ONE batch quotes call for all symbols  (1 request total)
  • Per-symbol history calls with 0.25 s sleep (≤4/sec)
  • Circuit-breaker in FyersMarketDataAdapter absorbs bursts
"""

import logging
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.db.session import get_db
from app.db.models import Alert, Instrument, InstrumentType
from app.workers.detection_worker import run_detection_for_symbol, build_fyers_symbol

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/live", tags=["live-detect"])

# ---------------------------------------------------------------------------
# Scan state  (in-process, single-worker)
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


def _persist(db, instrument, det, w_start, w_end) -> Optional[Alert]:
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
            explanation=det["explanation"],
            status="open",
        )
        db.add(a)
        db.commit()
        db.refresh(a)
        return a
    except IntegrityError:
        db.rollback()
        return None
    except Exception as e:
        db.rollback()
        logger.error("Alert persist failed: %s", e)
        return None


def _run_scan(db: Session, symbols: List[tuple]) -> Dict:
    adapter = _get_adapter()
    if not adapter:
        return {"error": "Fyers adapter unavailable — check token", "detections": [],
                "symbols_scanned": 0, "alerts_generated": 0}

    now = datetime.utcnow()
    w_start = now - timedelta(hours=6)
    summaries, alerts_n, scanned = [], 0, 0

    # ── Step 1: Batch quotes — ONE call for all symbols ───────────────────────
    fyers_syms = [build_fyers_symbol(sym, itype) for sym, itype in symbols]
    try:
        all_quotes = adapter.get_quotes_normalized(fyers_syms)
        quote_map  = {q.get("symbol", ""): q for q in all_quotes}
    except Exception as e:
        logger.error("Batch quotes failed: %s", e)
        quote_map = {}

    # ── Step 2: Per-symbol history + detection ────────────────────────────────
    for sym, itype in symbols:
        fsym = build_fyers_symbol(sym, itype)
        inst = db.query(Instrument).filter(
            Instrument.symbol == sym, Instrument.exchange == "NSE"
        ).first()
        if not inst:
            continue

        try:
            dets = run_detection_for_symbol(
                sym, fsym, adapter, itype,
                cached_quote=quote_map.get(fsym),
            )
            scanned += 1
            for d in dets:
                a = _persist(db, inst, d, w_start, now)
                if a:
                    alerts_n += 1
                summaries.append({
                    "symbol": sym, "pattern": d["pattern_type"],
                    "severity": d["severity"], "score": d["score"],
                    "new_alert": a is not None,
                    "explanation_short": d["explanation"][:300],
                })
        except Exception as e:
            logger.error("Scan error %s: %s", sym, e)

        # Rate-limit: ≤4 history calls/sec on Standard plan
        time.sleep(0.25)

    return {
        "symbols_scanned": scanned, "alerts_generated": alerts_n,
        "detections": summaries, "error": None,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/scan/status")
def scan_status():
    with _lock:
        return {
            "last_scan": _state["timestamp"],
            "symbols_scanned": _state["symbols_scanned"],
            "alerts_generated": _state["alerts_generated"],
            "running": _state["running"],
            "error": _state["error"],
            "recent_detections": _state["detections"][-20:],
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
        result = _run_scan(db, SCAN_WATCHLIST[:max_symbols])
        with _lock:
            _state.update({
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "symbols_scanned": result["symbols_scanned"],
                "alerts_generated": result["alerts_generated"],
                "detections": result["detections"],
                "error": result.get("error"),
                "running": False,
            })
        return {
            "status": "complete",
            "timestamp": _state["timestamp"],
            "symbols_scanned": result["symbols_scanned"],
            "alerts_generated": result["alerts_generated"],
            "detections": result["detections"],
            "data_note": (
                "Signals derived from Fyers live quotes + 30-day daily OHLCV. "
                "When markets are closed, last-session data is used. "
                "Thresholds: SEBI CASE or HEURISTIC — see detection_worker.py."
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
    adapter = _get_adapter()
    if not adapter:
        return {"error": "Fyers adapter unavailable"}

    now     = datetime.utcnow()
    w_start = now - timedelta(hours=6)
    try:
        dets = run_detection_for_symbol(symbol, fsym, adapter, itype)
        created = 0
        for d in dets:
            if _persist(db, inst, d, w_start, now):
                created += 1
        return {
            "symbol": symbol, "fyers_symbol": fsym,
            "timestamp": now.isoformat() + "Z",
            "detections_found": len(dets),
            "alerts_created": created,
            "detections": dets,
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
        })

    rows.sort(key=lambda x: x["anomaly_score"], reverse=True)
    return {
        "instruments": rows,
        "total": len(rows),
        "flagged": sum(1 for r in rows if r["anomaly_score"] > 0),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
