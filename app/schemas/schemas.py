from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AlertOut(BaseModel):
    id: str
    instrument_symbol: str
    exchange: str
    pattern_type: str
    severity: str
    score: float
    accounts_involved: list
    window_start: datetime
    window_end: datetime
    explanation: str
    status: str
    escalated_to_sebi: bool

    class Config:
        from_attributes = True


class EvidenceRowOut(BaseModel):
    order_id: str
    exchange_order_id: str
    account_id: str
    side: str
    status: str
    price: float
    quantity: int
    filled_quantity: int
    session: Optional[str]
    exchange: str
    timestamp: str


class EvidenceLogOut(BaseModel):
    alert_id: str
    pattern_type: str
    instrument_symbol: str
    exchange: str
    window_start: str
    window_end: str
    accounts_involved: list
    severity: str
    score: float
    explanation: str
    rows: list[EvidenceRowOut]
    generated_at: str
    disclaimer: str


class DetectionRunRequest(BaseModel):
    instrument_id: str
    window_minutes: int = 15


class EscalateRequest(BaseModel):
    new_status: str          # open | investigating | escalated | closed
    notes: str = ""


class DraftSAROut(BaseModel):
    case_reference: str
    alert_id: str
    generated_at: str
    is_draft: bool
    header: dict
    subject: dict
    suspected_pattern: dict
    evidence: dict
    accounts_involved: list
    methodology: str
    limitations: str
    recommended_action: str
    text_document: str       # full plain-text SAR for download/filing
