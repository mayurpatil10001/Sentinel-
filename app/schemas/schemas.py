from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AlertOut(BaseModel):
    id: str
    instrument_symbol: str
    exchange: str
    pattern_type: str
    severity: str
    # heuristic_score: z-score / volume-ratio normalized to [0,1] by the triggering detector.
    # This is NOT an ML confidence — it is a hand-coded statistical threshold formula.
    score: float
    # ml_anomaly_score: Isolation Forest anomaly score [0,1] from OHLCVScorer.
    # None = model not yet trained (heuristic-only mode).
    # NOT a manipulation probability — anomalousness relative to baseline distribution.
    ml_anomaly_score: Optional[float] = None
    # score_method: how the score was computed ('heuristic' | 'heuristic+if')
    score_method: str = "heuristic"
    accounts_involved: list
    window_start: datetime
    window_end: datetime
    explanation: str
    status: str
    escalated_to_sebi: bool
    # Phase 4: provenance fields — always present in API response.
    # source values: live_fyers | eod_history | synthetic_demo | backtest
    source: str = "live_fyers"
    event_date: Optional[str] = None      # ISO date string, e.g. "2026-11-10"
    occurrences: int = 1
    last_seen_at: Optional[datetime] = None
    # UI badge: "SAMPLE DATA" if source == "synthetic_demo"
    is_sample_data: bool = False

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
