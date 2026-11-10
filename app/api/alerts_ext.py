"""
app/api/alerts_ext.py
=====================
Extended alert endpoints:

  GET    /api/alerts                    — filterable, paginated
  GET    /api/alerts/{id}               — single alert detail
  PATCH  /api/alerts/{id}/status        — lifecycle transition via alert manager
  POST   /api/alerts/{id}/escalate-to-sebi — generate real SEBI SAR draft

Phase 4: provenance filtering
  - ?source=live_fyers|eod_history|synthetic_demo|backtest filters by source.
  - By default, synthetic_demo rows are EXCLUDED (they are seeded demo data,
    not real detections). Pass ?source=synthetic_demo or ?include_demo=true to
    include them. Every synthetic_demo row is marked with is_sample_data=True.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models import Alert, Instrument
from app.alerts.manager import escalate_alert
from app.alerts.sebi_report import generate_draft_sar, format_sar_as_dict
from app.schemas.schemas import AlertOut, DraftSAROut

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


# ── Helper ─────────────────────────────────────────────────────────────────────

def _alert_to_out(a: Alert) -> AlertOut:
    source = getattr(a, "source", "live_fyers") or "live_fyers"
    event_date_str = None
    if getattr(a, "event_date", None) is not None:
        try:
            event_date_str = a.event_date.isoformat()
        except Exception:
            pass
    return AlertOut(
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
        # Phase 4 provenance fields
        source=source,
        event_date=event_date_str,
        occurrences=getattr(a, "occurrences", 1) or 1,
        last_seen_at=getattr(a, "last_seen_at", None),
        is_sample_data=(source == "synthetic_demo"),
    )


# ── GET /api/alerts ────────────────────────────────────────────────────────────

# Valid source values (Phase 4 provenance)
_VALID_SOURCES = {"live_fyers", "eod_history", "synthetic_demo", "backtest"}

@router.get("", response_model=dict)
def list_alerts_filtered(
    severity: Optional[str] = Query(None, description="low|medium|high|critical"),
    pattern_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="open|investigating|escalated|closed"),
    instrument_id: Optional[str] = Query(None),
    from_dt: Optional[datetime] = Query(None, description="ISO8601 datetime"),
    to_dt: Optional[datetime] = Query(None, description="ISO8601 datetime"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    # Phase 4: source filter. Default excludes synthetic_demo.
    # Pass ?source=synthetic_demo to show ONLY demo data.
    # Pass ?include_demo=true to include synthetic_demo alongside live data.
    source: Optional[str] = Query(
        None,
        description=(
            "Filter by data provenance: live_fyers|eod_history|synthetic_demo|backtest. "
            "By default, synthetic_demo rows are excluded (they are seeded demo data, "
            "not real detections). Pass ?source=synthetic_demo to see only demo data, "
            "or ?include_demo=true to mix demo with real."
        ),
    ),
    include_demo: bool = Query(
        False,
        description="Include synthetic_demo rows alongside real data. Default false.",
    ),
    db: Session = Depends(get_db),
):
    """
    Filterable, paginated alert listing. All filters are optional.

    Phase 4 provenance:
    - By default, synthetic_demo rows are EXCLUDED. These are seeded demo
      rows not produced by real scans. The UI must label every row that has
      is_sample_data=True with a visible 'SAMPLE DATA' badge.
    - Pass ?include_demo=true to include synthetic_demo alongside real data.
    - Pass ?source=synthetic_demo to show ONLY synthetic_demo rows.

    Returns: { total, page, page_size, source_filter, items: [AlertOut] }
    Every field traces back to a real Alert DB row.
    """
    if source and source not in _VALID_SOURCES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid source '{source}'. Must be one of: {sorted(_VALID_SOURCES)}",
        )

    q = db.query(Alert).join(Alert.instrument)

    # Phase 4: provenance filter
    if source:
        # Explicit source requested — show only that source
        q = q.filter(Alert.source == source)
    elif not include_demo:
        # Default: exclude synthetic_demo
        q = q.filter(Alert.source != "synthetic_demo")
    # else include_demo=True: no filter, return all sources

    if severity:
        q = q.filter(Alert.severity == severity)
    if pattern_type:
        q = q.filter(Alert.pattern_type == pattern_type)
    if status:
        q = q.filter(Alert.status == status)
    if instrument_id:
        q = q.filter(Alert.instrument_id == instrument_id)
    if from_dt:
        q = q.filter(Alert.detected_at >= from_dt)
    if to_dt:
        q = q.filter(Alert.detected_at <= to_dt)

    total = q.count()
    alerts = (
        q.order_by(Alert.detected_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        # Phase 4: tell the client exactly what filter was applied
        "source_filter": source or ("all_except_demo" if not include_demo else "all"),
        "demo_included": include_demo or (source == "synthetic_demo"),
        "items": [_alert_to_out(a).model_dump() for a in alerts],
    }


# ── GET /api/alerts/{id} ───────────────────────────────────────────────────────

@router.get("/{alert_id}", response_model=AlertOut)
def get_alert(alert_id: str, db: Session = Depends(get_db)):
    """Single alert detail by ID."""
    a = db.query(Alert).filter(Alert.id == alert_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Alert not found")
    return _alert_to_out(a)


# ── PATCH /api/alerts/{id}/status ─────────────────────────────────────────────

class StatusUpdate(BaseModel):
    new_status: str   # open | investigating | escalated | closed
    notes: str = ""


@router.patch("/{alert_id}/status", response_model=AlertOut)
def update_alert_status(
    alert_id: str,
    body: StatusUpdate,
    db: Session = Depends(get_db),
):
    """
    Transition an alert's lifecycle status.
    Calls escalate_alert() from app.alerts.manager — real dedup/lifecycle logic.
    NOT a raw DB write.

    Valid transitions: open → investigating → escalated → closed
                       open → closed (direct dismiss)
    """
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    try:
        updated = escalate_alert(db, alert, body.new_status, notes=body.notes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return _alert_to_out(updated)


# ── POST /api/alerts/{id}/escalate-to-sebi ────────────────────────────────────

@router.post("/{alert_id}/escalate-to-sebi", response_model=DraftSAROut)
def escalate_to_sebi(alert_id: str, db: Session = Depends(get_db)):
    """
    Generate a real SEBI Suspicious Activity Report draft for this alert.
    Calls generate_draft_sar() from app.alerts.sebi_report — returns the
    full draft including PFUTP citations, methodology, limitations, and
    a plain-text document for filing.

    This does NOT auto-file with SEBI. The analyst must review and submit
    through SEBI SCORES.
    """
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    instrument = db.query(Instrument).filter(Instrument.id == alert.instrument_id).first()
    if not instrument:
        raise HTTPException(status_code=500, detail="Alert instrument record missing")

    try:
        sar = generate_draft_sar(alert, instrument)
        sar_dict = format_sar_as_dict(sar)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"SAR generation failed: {exc}")

    # Also transition status to escalated via real manager (records the fact)
    try:
        escalate_alert(
            db, alert, "escalated",
            notes=f"SEBI SAR draft generated: case_reference={sar_dict['case_reference']}"
        )
    except ValueError:
        pass  # Already escalated — idempotent

    return DraftSAROut(**sar_dict)
