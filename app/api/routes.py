from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models import Alert, Instrument, Order
from app.detection.spoofing import run_spoofing_detection
from app.detection.evidence import build_evidence_log
from app.alerts.manager import create_or_update_alert, escalate_alert
from app.alerts.sebi_report import generate_draft_sar, format_sar_as_text, format_sar_as_dict
from app.schemas.schemas import (
    AlertOut,
    EvidenceLogOut,
    DetectionRunRequest,
    EscalateRequest,
    DraftSAROut,
)

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/detect/spoofing", response_model=list[AlertOut])
def run_detection(req: DetectionRunRequest, db: Session = Depends(get_db)):
    """
    Runs the spoofing/layering detector over all stored orders for the
    given instrument and persists any resulting alerts via the alert manager
    (deduplication + escalation tier applied automatically).
    """
    instrument = db.query(Instrument).filter(Instrument.id == req.instrument_id).first()
    if not instrument:
        raise HTTPException(status_code=404, detail="Instrument not found")

    orders = (
        db.query(Order).filter(Order.instrument_id == instrument.id).all()
    )
    signals = run_spoofing_detection(orders, instrument, req.window_minutes)

    created = []
    for sig in signals:
        alert = Alert(
            instrument_id=instrument.id,
            pattern_type="spoofing_layering",
            severity=sig.severity,
            score=round(sig.score, 3),
            accounts_involved=[sig.account_id],
            window_start=sig.window_start,
            window_end=sig.window_end,
            explanation=sig.explanation,
        )
        db.add(alert)
        db.commit()
        db.refresh(alert)
        created.append(alert)

    return [
        AlertOut(
            id=a.id,
            instrument_symbol=instrument.symbol,
            exchange=instrument.exchange,
            pattern_type=a.pattern_type,
            severity=a.severity,
            score=a.score,
            accounts_involved=a.accounts_involved,
            window_start=a.window_start,
            window_end=a.window_end,
            explanation=a.explanation,
            status=a.status,
            escalated_to_sebi=a.escalated_to_sebi,
        )
        for a in created
    ]


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(db: Session = Depends(get_db)):
    alerts = db.query(Alert).order_by(Alert.detected_at.desc()).all()
    return [
        AlertOut(
            id=a.id,
            instrument_symbol=a.instrument.symbol,
            exchange=a.instrument.exchange,
            pattern_type=a.pattern_type,
            severity=a.severity,
            score=a.score,
            accounts_involved=a.accounts_involved,
            window_start=a.window_start,
            window_end=a.window_end,
            explanation=a.explanation,
            status=a.status,
            escalated_to_sebi=a.escalated_to_sebi,
        )
        for a in alerts
    ]


@router.get("/alerts/{alert_id}/evidence-log", response_model=EvidenceLogOut)
def get_evidence_log(alert_id: str, db: Session = Depends(get_db)):
    """
    Returns the raw, exact order-level log slice behind an alert:
    timestamp, quantity, price, exchange, session, account — for
    independent verification by SEBI/exchanges.
    """
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    evidence = build_evidence_log(db, alert)
    return evidence
