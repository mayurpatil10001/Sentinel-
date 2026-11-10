"""
Evidence Access Log — Who Pulled What, When, and With What ID Mode
===================================================================

Every call to build_evidence_log() must be recorded here automatically.
The caller must NOT be able to bypass this logging by forgetting to call
a separate log function — the logging must be wired into the call path
itself (done in evidence.py, not in each API route separately).

What is logged:
  - alert_id                — which alert's evidence was accessed
  - accessed_by             — caller identity (API key, user ID, system name)
  - accessed_at             — timestamp (UTC)
  - used_raw_ids            — boolean: were raw account IDs returned?
  - evidence_row_count      — how many order rows were in the log
  - source_ip               — optional, from the API request context

What is NOT stored in this table:
  - The evidence payload itself (that would duplicate sensitive data into
    a second location with potentially weaker access controls).
  - The actual account IDs (raw or hashed) — the access log's purpose is
    WHO/WHEN/WHICH ALERT, not WHAT WAS RETURNED.

DESIGN RATIONALE
-----------------
Purpose: make raw-ID access a visible, searchable, auditable event.

If EVIDENCE_LOG_USE_HASHED_ID = True (the default), most access log
entries will show used_raw_ids = False. An alert triggered by
`used_raw_ids = True` entries would indicate that an analyst deliberately
requested the sensitive form and should trigger a manual review of
whether that access was justified.

This does NOT prevent unauthorised access — it makes it observable.
A determined insider who bypasses the API entirely (direct DB query) would
not generate an access log entry. This is an application-layer control,
not a database-layer control.

SEBI confidentiality obligations (SEBI Act 1992, Sections 11 and 15Y,
read with the general surveillance framework) require that surveillance
data not be disclosed except for regulatory purposes. Logging all evidence
pulls provides an audit trail if disclosure is ever questioned.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.orm import Session

from app.db.models import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


class EvidenceAccessLog(Base):
    """
    One row per call to build_evidence_log().

    Schema is intentionally minimal — only WHO/WHEN/WHICH ALERT, not WHAT.
    Storing the evidence payload here would create a second copy of sensitive
    data with potentially different (weaker) access controls.
    """

    __tablename__ = "evidence_access_log"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    alert_id = Column(String(36), nullable=False, index=True)   # FK-less: alert may be deleted
    accessed_by = Column(String(128), nullable=False)           # caller identity
    accessed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    used_raw_ids = Column(Boolean, nullable=False)              # True = raw IDs returned
    evidence_row_count = Column(Integer, nullable=False)        # number of Order rows returned
    source_ip = Column(String(45), nullable=True)               # IPv4 or IPv6, optional


def log_evidence_access(
    db: Session,
    alert_id: str,
    accessed_by: str,
    used_raw_ids: bool,
    evidence_row_count: int,
    source_ip: str | None = None,
) -> EvidenceAccessLog:
    """
    Record an evidence log access event. Called automatically by
    build_evidence_log() — do not call this manually from API routes
    (the purpose is to make logging automatic and non-optional).

    Parameters
    ----------
    db
        Active SQLAlchemy session.
    alert_id
        The alert whose evidence was accessed.
    accessed_by
        Caller identity string. Use a stable identifier:
          - For API access: the API key ID or authenticated user ID.
          - For system jobs: the job name (e.g. "sebi_report_generator").
          - If unknown: "anonymous" — but consider this a security gap
            if this appears in production logs.
    used_raw_ids
        True if raw (unhashed) account IDs were returned.
        False if hashed IDs were returned.
    evidence_row_count
        Number of Order rows included in the evidence log.
    source_ip
        Optional request IP address.

    Returns
    -------
    EvidenceAccessLog
        The persisted log row. Commit is called internally.

    Raises
    ------
    TypeError
        If db is None.
    ValueError
        If alert_id or accessed_by is empty.
    """
    if db is None:
        raise TypeError("log_evidence_access: db is None.")
    if not alert_id:
        raise ValueError("log_evidence_access: alert_id must not be empty.")
    if not accessed_by:
        raise ValueError(
            "log_evidence_access: accessed_by must not be empty. "
            "Use 'anonymous' if the caller identity is genuinely unknown, "
            "but investigate why — anonymous raw-ID access is a red flag."
        )

    entry = EvidenceAccessLog(
        alert_id=str(alert_id),
        accessed_by=str(accessed_by),
        accessed_at=datetime.utcnow(),
        used_raw_ids=used_raw_ids,
        evidence_row_count=evidence_row_count,
        source_ip=source_ip,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
