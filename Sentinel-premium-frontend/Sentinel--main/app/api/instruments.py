"""
app/api/instruments.py
======================
GET /api/instruments       — list all instruments with last-alert status
GET /api/instruments/{id}  — single instrument detail
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models import Alert, Instrument, InstrumentType

router = APIRouter(prefix="/api/instruments", tags=["instruments"])


# ── Schemas ────────────────────────────────────────────────────────────────────

class InstrumentOut(BaseModel):
    id: str
    symbol: str
    exchange: str
    instrument_type: str
    underlying_symbol: Optional[str] = None
    strike_price: Optional[float] = None
    expiry_date: Optional[str] = None
    option_type: Optional[str] = None
    avg_daily_volume_30d: Optional[float] = None
    avg_order_size_30d: Optional[float] = None
    avg_daily_turnover_30d: Optional[float] = None
    # Computed from recent alerts
    last_alert_severity: Optional[str] = None   # max severity in last 24 h, or None
    last_alert_pattern: Optional[str] = None
    open_alert_count: int = 0

    class Config:
        from_attributes = True


class InstrumentDetailOut(InstrumentOut):
    """Same as InstrumentOut — kept separate so we can add fields later."""
    pass


# ── Helpers ────────────────────────────────────────────────────────────────────

_SEV_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}

def _last_alert_info(db: Session, instrument_id: str) -> dict:
    """Return the highest-severity open alert for this instrument in the last 24 h."""
    cutoff = datetime.utcnow() - timedelta(hours=24)
    alerts = (
        db.query(Alert)
        .filter(
            Alert.instrument_id == instrument_id,
            Alert.status.in_(["open", "investigating"]),
            Alert.detected_at >= cutoff,
        )
        .all()
    )
    if not alerts:
        return {"last_alert_severity": None, "last_alert_pattern": None, "open_alert_count": 0}

    top = max(alerts, key=lambda a: _SEV_RANK.get(a.severity, 0))
    return {
        "last_alert_severity": top.severity,
        "last_alert_pattern": top.pattern_type,
        "open_alert_count": len(alerts),
    }


def _instrument_to_out(inst: Instrument, db: Session) -> InstrumentOut:
    info = _last_alert_info(db, inst.id)
    return InstrumentOut(
        id=inst.id,
        symbol=inst.symbol,
        exchange=inst.exchange,
        instrument_type=inst.instrument_type.value if hasattr(inst.instrument_type, "value") else str(inst.instrument_type),
        underlying_symbol=inst.underlying_symbol,
        strike_price=inst.strike_price,
        expiry_date=inst.expiry_date.isoformat() if inst.expiry_date else None,
        option_type=inst.option_type,
        avg_daily_volume_30d=inst.avg_daily_volume_30d,
        avg_order_size_30d=inst.avg_order_size_30d,
        avg_daily_turnover_30d=inst.avg_daily_turnover_30d,
        **info,
    )


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("", response_model=list[InstrumentOut])
def list_instruments(
    exchange: Optional[str] = None,
    instrument_type: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    List all instruments. Optional filters: exchange, instrument_type.
    Each instrument includes computed last_alert_severity from the
    most recent open alert in the last 24 hours (or None if clean).
    """
    q = db.query(Instrument)
    if exchange:
        q = q.filter(Instrument.exchange == exchange.upper())
    if instrument_type:
        q = q.filter(Instrument.instrument_type == instrument_type)
    instruments = q.order_by(Instrument.symbol).all()
    return [_instrument_to_out(i, db) for i in instruments]


@router.get("/{instrument_id}", response_model=InstrumentDetailOut)
def get_instrument(instrument_id: str, db: Session = Depends(get_db)):
    """Full detail for one instrument by ID."""
    inst = db.query(Instrument).filter(Instrument.id == instrument_id).first()
    if not inst:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return _instrument_to_out(inst, db)
