"""
app/api/audit.py
================
GET /api/audit/evidence-access  — real EvidenceAccessLog query
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.security.access_log import EvidenceAccessLog

router = APIRouter(prefix="/api/audit", tags=["audit"])


class AccessLogEntryOut(BaseModel):
    id: str
    alert_id: str
    accessed_by: str
    accessed_at: str
    used_raw_ids: bool
    evidence_row_count: int
    source_ip: Optional[str] = None

    class Config:
        from_attributes = True


@router.get("/evidence-access", response_model=dict)
def list_evidence_access(
    alert_id: Optional[str] = Query(None),
    from_dt: Optional[datetime] = Query(None, description="ISO8601 datetime"),
    to_dt: Optional[datetime] = Query(None, description="ISO8601 datetime"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """
    Filterable evidence-access audit log.
    Returns: { total, page, page_size, items: [AccessLogEntryOut] }

    Every row is a real EvidenceAccessLog DB row — created automatically by
    build_evidence_log() each time an analyst or the system pulls alert evidence.
    This is the complete, unfiltered audit trail of who pulled what and when.
    """
    q = db.query(EvidenceAccessLog)

    if alert_id:
        q = q.filter(EvidenceAccessLog.alert_id == alert_id)
    if from_dt:
        q = q.filter(EvidenceAccessLog.accessed_at >= from_dt)
    if to_dt:
        q = q.filter(EvidenceAccessLog.accessed_at <= to_dt)

    total = q.count()
    rows = (
        q.order_by(EvidenceAccessLog.accessed_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [
        AccessLogEntryOut(
            id=r.id,
            alert_id=r.alert_id,
            accessed_by=r.accessed_by,
            accessed_at=r.accessed_at.isoformat() if r.accessed_at else "",
            used_raw_ids=r.used_raw_ids,
            evidence_row_count=r.evidence_row_count,
            source_ip=r.source_ip,
        ).model_dump()
        for r in rows
    ]

    return {"total": total, "page": page, "page_size": page_size, "items": items}
