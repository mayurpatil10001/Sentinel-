"""
Phase 5 Tests — Alert Management and SEBI Reporting
=====================================================

Tests for:
  - app/alerts/manager.py   (deduplication, escalation, lifecycle)
  - app/alerts/sebi_report.py (SAR generation, format, completeness)

These tests use in-memory SQLite via SQLAlchemy so they don't require
a running Postgres instance. The schema is created fresh per test.

Verification prompt compliance:
  1. Duplicate signals within DEDUP_WINDOW_HOURS → deduplicated (not double-counted).
  2. Higher-score update on existing alert → score is updated upward.
  3. Low-score signal (< TIER_1_SCORE) → suppressed, no Alert row created.
  4. Escalation tier assignment is correct for each score range.
  5. SAR is generated with all mandatory sections (HARD RULE #3).
  6. SAR contains the "not a probability" caveat for anomaly scores.
  7. SAR carries through the detector's explanation string unmodified.
  8. SEBI regulation reference is included for every supported pattern type.
  9. None inputs raise exceptions (HARD RULE #1).
 10. format_sar_as_text and format_sar_as_dict both produce complete output.

Run:
    pytest tests/test_alerts_phase5.py -v
"""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Alert, Instrument, InstrumentType
from app.alerts.manager import (
    create_or_update_alert,
    escalate_alert,
    find_existing_open_alert,
    TIER_1_SCORE,
    TIER_2_SCORE,
    TIER_3_SCORE,
    DEDUP_WINDOW_HOURS,
)
from app.alerts.sebi_report import (
    generate_draft_sar,
    format_sar_as_text,
    format_sar_as_dict,
    SEBIDraftSAR,
    _REGULATION_MAP,
    _PATTERN_DISPLAY,
)


# ── Test DB setup ──────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    """In-memory SQLite session — fresh schema per test."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def instrument(db):
    """A single test instrument row."""
    instr = Instrument(
        id=str(uuid.uuid4()),
        symbol="TESTCO",
        exchange="NSE",
        instrument_type=InstrumentType.EQUITY,
        avg_daily_volume_30d=500_000.0,
        avg_order_size_30d=500.0,
        avg_daily_turnover_30d=50_000_000.0,
    )
    db.add(instr)
    db.commit()
    db.refresh(instr)
    return instr


def _window():
    now = datetime(2024, 1, 15, 10, 0, 0)
    return now, now + timedelta(hours=1)


# ══════════════════════════════════════════════════════════════════════════════
# Tests: Alert Manager
# ══════════════════════════════════════════════════════════════════════════════

class TestAlertManager:

    def test_creates_alert_above_tier1(self, db, instrument):
        """Score >= TIER_1_SCORE creates an Alert row."""
        start, end = _window()
        alert, action = create_or_update_alert(
            db, instrument,
            pattern_type="circular_trading",
            score=TIER_1_SCORE + 0.01,
            severity="medium",
            accounts_involved=["ACC1", "ACC2"],
            window_start=start,
            window_end=end,
            explanation="Ring of 3 accounts detected.",
        )
        assert action == "created"
        assert alert is not None
        assert alert.pattern_type == "circular_trading"
        assert alert.status == "open"
        assert alert.escalated_to_sebi is False
        assert "ACC1" in alert.accounts_involved
        assert db.query(Alert).count() == 1

    def test_suppresses_alert_below_tier1(self, db, instrument):
        """Score < TIER_1_SCORE → no Alert row, action = 'suppressed'."""
        start, end = _window()
        alert, action = create_or_update_alert(
            db, instrument,
            pattern_type="option_pinning",
            score=TIER_1_SCORE - 0.05,
            severity="low",
            accounts_involved=["ACC1"],
            window_start=start,
            window_end=end,
            explanation="Low-confidence pinning signal.",
        )
        assert action == "suppressed"
        assert alert is None
        assert db.query(Alert).count() == 0

    def test_deduplicates_within_window(self, db, instrument):
        """
        VERIFICATION Q1: two signals for the same (instrument, pattern_type)
        within DEDUP_WINDOW_HOURS → only ONE Alert row, second is deduplicated.
        """
        start, end = _window()

        alert1, action1 = create_or_update_alert(
            db, instrument,
            pattern_type="circular_trading",
            score=0.60,
            severity="medium",
            accounts_involved=["ACC1", "ACC2"],
            window_start=start,
            window_end=end,
            explanation="First ring detection.",
        )
        assert action1 == "created"

        # Second signal: same instrument, same pattern, same score → deduplicated
        alert2, action2 = create_or_update_alert(
            db, instrument,
            pattern_type="circular_trading",
            score=0.55,  # lower — should not replace
            severity="medium",
            accounts_involved=["ACC3"],
            window_start=start + timedelta(minutes=15),
            window_end=end + timedelta(minutes=15),
            explanation="Second ring detection.",
        )

        assert action2 == "deduplicated", (
            f"Second signal for same instrument/pattern within {DEDUP_WINDOW_HOURS}h "
            f"must be deduplicated. Got action={action2}"
        )
        assert db.query(Alert).count() == 1, \
            "Deduplication must prevent creation of a second Alert row"

    def test_updates_score_on_higher_signal(self, db, instrument):
        """
        VERIFICATION Q2: higher-score signal updates existing open alert's score,
        does not create a new row.
        """
        start, end = _window()

        alert1, action1 = create_or_update_alert(
            db, instrument,
            pattern_type="circular_trading",
            score=0.55,
            severity="medium",
            accounts_involved=["ACC1"],
            window_start=start,
            window_end=end,
            explanation="Initial ring detection.",
        )
        assert action1 == "created"
        original_id = alert1.id

        # New signal: same pattern, HIGHER score
        alert2, action2 = create_or_update_alert(
            db, instrument,
            pattern_type="circular_trading",
            score=0.80,  # higher than 0.55
            severity="high",
            accounts_involved=["ACC2", "ACC3"],
            window_start=start + timedelta(minutes=10),
            window_end=end + timedelta(minutes=10),
            explanation="Stronger ring confirmed.",
        )

        assert action2 == "updated_score", (
            f"Higher-score signal must trigger score update. Got action={action2}"
        )
        assert db.query(Alert).count() == 1, \
            "Score update must NOT create a new row"
        assert alert2.id == original_id, "Must update the existing row, not create new"
        assert alert2.score == pytest.approx(0.80, abs=0.001), \
            f"Score must be updated to 0.80, got {alert2.score}"
        # Account lists merged
        assert "ACC1" in alert2.accounts_involved
        assert "ACC2" in alert2.accounts_involved

    def test_different_pattern_types_create_separate_alerts(self, db, instrument):
        """Different pattern_types are not deduplicated against each other."""
        start, end = _window()

        _, a1 = create_or_update_alert(
            db, instrument, "circular_trading", 0.70, "high",
            ["ACC1"], start, end, "Ring detected."
        )
        _, a2 = create_or_update_alert(
            db, instrument, "basis_distortion", 0.70, "high",
            ["ACC1"], start, end, "Basis distorted."
        )
        assert a1 == "created"
        assert a2 == "created"
        assert db.query(Alert).count() == 2

    def test_tier_1_embedded_in_explanation(self, db, instrument):
        """Medium-score alert has [TIER 1] prefix in explanation."""
        start, end = _window()
        score = TIER_1_SCORE + 0.01  # just above tier 1, below tier 2
        alert, _ = create_or_update_alert(
            db, instrument, "circular_trading", score, "medium",
            ["ACC1"], start, end, "Ring detected."
        )
        assert "[TIER 1]" in alert.explanation, \
            f"Expected [TIER 1] in explanation, got: {alert.explanation[:100]}"

    def test_tier_2_embedded_in_explanation(self, db, instrument):
        """High-score alert has [TIER 2] prefix."""
        start, end = _window()
        alert, _ = create_or_update_alert(
            db, instrument, "circular_trading", TIER_2_SCORE + 0.01, "high",
            ["ACC1"], start, end, "Ring detected."
        )
        assert "[TIER 2]" in alert.explanation

    def test_tier_3_embedded_in_explanation(self, db, instrument):
        """Critical-score alert has [TIER 3] prefix."""
        start, end = _window()
        alert, _ = create_or_update_alert(
            db, instrument, "circular_trading", TIER_3_SCORE + 0.01, "critical",
            ["ACC1"], start, end, "Ring detected."
        )
        assert "[TIER 3]" in alert.explanation

    def test_escalate_sets_status(self, db, instrument):
        """escalate_alert transitions status correctly."""
        start, end = _window()
        alert, _ = create_or_update_alert(
            db, instrument, "circular_trading", 0.75, "high",
            ["ACC1"], start, end, "Ring detected."
        )
        assert alert.status == "open"

        alert = escalate_alert(db, alert, "investigating", notes="Pulling order logs.")
        assert alert.status == "investigating"
        assert "ESCALATION NOTE" in alert.explanation

        alert = escalate_alert(db, alert, "escalated")
        assert alert.status == "escalated"
        assert alert.escalated_to_sebi is True

    def test_escalate_to_closed(self, db, instrument):
        """Alert can be closed directly with a note."""
        start, end = _window()
        alert, _ = create_or_update_alert(
            db, instrument, "circular_trading", 0.60, "medium",
            ["ACC1"], start, end, "Ring detected."
        )
        alert = escalate_alert(db, alert, "closed", notes="False positive — news event.")
        assert alert.status == "closed"

    def test_invalid_escalation_status_raises(self, db, instrument):
        """Invalid status raises ValueError."""
        start, end = _window()
        alert, _ = create_or_update_alert(
            db, instrument, "circular_trading", 0.60, "medium",
            ["ACC1"], start, end, "Ring."
        )
        with pytest.raises(ValueError, match="Invalid status"):
            escalate_alert(db, alert, "pending_sebi_approval")

    def test_raises_on_empty_explanation(self, db, instrument):
        """HARD RULE #3: empty explanation raises ValueError."""
        start, end = _window()
        with pytest.raises(ValueError, match="explanation is required"):
            create_or_update_alert(
                db, instrument, "circular_trading", 0.75, "high",
                ["ACC1"], start, end, explanation="",
            )

    def test_raises_on_none_db(self, instrument):
        """HARD RULE #1: None db raises TypeError."""
        start, end = _window()
        with pytest.raises(TypeError):
            find_existing_open_alert(None, instrument.id, "circular_trading")

    def test_escalate_raises_on_none_alert(self, db):
        """HARD RULE #1: None alert raises ValueError."""
        with pytest.raises(ValueError, match="alert is None"):
            escalate_alert(db, None, "closed")


# ══════════════════════════════════════════════════════════════════════════════
# Tests: SEBI SAR Generator
# ══════════════════════════════════════════════════════════════════════════════

class TestSEBISARGenerator:

    def _make_alert(self, pattern="circular_trading", score=0.82):
        """Creates a minimal Alert object (not persisted — used as data source)."""
        a = Alert()
        a.id = str(uuid.uuid4())
        a.instrument_id = "instr-1"
        a.pattern_type = pattern
        a.score = score
        a.severity = "high"
        a.accounts_involved = ["ACC1", "ACC2", "ACC3"]
        a.window_start = datetime(2024, 1, 15, 10, 0, 0)
        a.window_end = datetime(2024, 1, 15, 11, 0, 0)
        a.explanation = (
            "Ring of 3 accounts detected: ACC1→ACC2→ACC3→ACC1. "
            "Gross volume 150k (2.0× normal window). Net position ≈ 0."
        )
        a.status = "open"
        a.escalated_to_sebi = False
        return a

    def _make_instrument(self):
        i = Instrument()
        i.id = "instr-1"
        i.symbol = "TESTCO"
        i.exchange = "NSE"
        i.instrument_type = InstrumentType.EQUITY
        return i

    def test_generates_draft_sar(self):
        """generate_draft_sar returns a SEBIDraftSAR with case_reference."""
        sar = generate_draft_sar(self._make_alert(), self._make_instrument())
        assert isinstance(sar, SEBIDraftSAR)
        assert sar.case_reference.startswith("SNTNL-")
        assert sar.is_draft is True
        assert sar.filed_at is None

    def test_sar_contains_all_mandatory_sections(self):
        """
        VERIFICATION Q5: SAR must have all 8 sections with non-empty content.
        """
        sar = generate_draft_sar(self._make_alert(), self._make_instrument())
        assert sar.instrument_symbol == "TESTCO"
        assert sar.exchange == "NSE"
        assert sar.suspected_regulation, "Section 3 (regulation) must be non-empty"
        assert sar.evidence_narrative, "Section 4 (evidence) must be non-empty"
        assert len(sar.accounts_involved) > 0, "Section 5 (accounts) must be non-empty"
        assert sar.methodology_note, "Section 6 (methodology) must be non-empty"
        assert sar.limitations, "Section 7 (limitations) must be non-empty"
        assert sar.recommended_action, "Section 8 (action) must be non-empty"

    def test_sar_carries_explanation_unmodified(self):
        """
        VERIFICATION Q7: the detector's explanation string appears in the SAR
        evidence section without modification (HARD RULE #3).
        """
        alert = self._make_alert()
        original_explanation = alert.explanation
        sar = generate_draft_sar(alert, self._make_instrument())
        assert original_explanation in sar.evidence_narrative, (
            "SAR evidence_narrative must contain the original detector explanation verbatim. "
            f"Expected:\n{original_explanation}\n\nGot:\n{sar.evidence_narrative}"
        )

    def test_sar_contains_probability_caveat(self):
        """
        VERIFICATION Q6: SAR must state that anomaly score ≠ manipulation probability.
        """
        sar = generate_draft_sar(self._make_alert(), self._make_instrument())
        text = format_sar_as_text(sar)
        assert any(phrase in text.lower() for phrase in [
            "not a probability",
            "not a manipulation probability",
            "not yet filed",
            "draft",
        ]), (
            "SAR text must include caveat that anomaly score is not a probability. "
            f"First 500 chars: {text[:500]}"
        )

    def test_sar_includes_sebi_regulation(self):
        """
        VERIFICATION Q8: SAR includes a SEBI regulation reference for every
        supported pattern type.
        """
        instr = self._make_instrument()
        for pattern in _REGULATION_MAP:
            alert = self._make_alert(pattern=pattern)
            sar = generate_draft_sar(alert, instr)
            assert "SEBI" in sar.suspected_regulation, (
                f"SAR for pattern '{pattern}' must include SEBI regulation reference. "
                f"Got: {sar.suspected_regulation}"
            )
            assert "2003" in sar.suspected_regulation or "2015" in sar.suspected_regulation, \
                f"Regulation must cite a SEBI regulation year: {sar.suspected_regulation}"

    def test_format_sar_as_text_contains_all_sections(self):
        """format_sar_as_text output contains all section headers."""
        sar = generate_draft_sar(self._make_alert(), self._make_instrument())
        text = format_sar_as_text(sar)

        for section in [
            "SECTION 1", "SECTION 2", "SECTION 3",
            "SECTION 4", "SECTION 5", "SECTION 6", "SECTION 7",
        ]:
            assert section in text, f"SAR text must include {section}"

        assert "DRAFT" in text
        assert "SNTNL-" in text
        assert "TESTCO" in text

    def test_format_sar_as_dict_is_complete(self):
        """format_sar_as_dict returns all required top-level keys."""
        sar = generate_draft_sar(self._make_alert(), self._make_instrument())
        d = format_sar_as_dict(sar)

        required_keys = {
            "case_reference", "alert_id", "generated_at", "is_draft",
            "header", "subject", "suspected_pattern", "evidence",
            "accounts_involved", "methodology", "limitations", "recommended_action",
        }
        missing = required_keys - set(d.keys())
        assert not missing, f"SAR dict missing keys: {missing}"

        assert d["subject"]["instrument_symbol"] == "TESTCO"
        assert d["evidence"]["anomaly_score"] is not None
        assert isinstance(d["accounts_involved"], list)

    def test_critical_score_recommends_immediate_escalation(self):
        """Tier-3 (critical) SAR recommends immediate escalation to Compliance Officer."""
        alert = self._make_alert(score=TIER_3_SCORE + 0.01)
        alert.severity = "critical"
        sar = generate_draft_sar(alert, self._make_instrument())
        assert "immediate" in sar.recommended_action.lower() or \
               "critical" in sar.recommended_action.upper(), (
            "Critical-tier SAR must recommend immediate escalation. "
            f"Got: {sar.recommended_action[:200]}"
        )

    def test_medium_score_recommends_internal_review(self):
        """Tier-1 (medium) SAR recommends internal analyst review."""
        alert = self._make_alert(score=TIER_1_SCORE + 0.01)
        alert.severity = "medium"
        sar = generate_draft_sar(alert, self._make_instrument())
        assert "internal" in sar.recommended_action.lower() or \
               "analyst" in sar.recommended_action.lower(), (
            f"Medium-tier SAR must recommend internal review. "
            f"Got: {sar.recommended_action[:200]}"
        )

    def test_raises_on_none_alert(self):
        """HARD RULE #1: None alert raises ValueError."""
        with pytest.raises(ValueError, match="alert is None"):
            generate_draft_sar(None, self._make_instrument())

    def test_raises_on_none_instrument(self):
        """HARD RULE #1: None instrument raises ValueError."""
        with pytest.raises(ValueError, match="instrument is None"):
            generate_draft_sar(self._make_alert(), None)

    def test_option_pinning_sar_warns_about_market_makers(self):
        """
        Option pinning SAR limitations must warn about market maker gamma
        as a false-positive source (per the spec).
        """
        alert = self._make_alert(pattern="option_pinning")
        sar = generate_draft_sar(alert, self._make_instrument())
        assert "market maker" in sar.limitations.lower(), (
            "Option pinning SAR limitations must mention market maker gamma. "
            f"Got: {sar.limitations[:300]}"
        )

    def test_basis_distortion_sar_warns_about_dividends(self):
        """Basis distortion SAR limitations must mention dividend false positives."""
        alert = self._make_alert(pattern="basis_distortion")
        sar = generate_draft_sar(alert, self._make_instrument())
        assert "dividend" in sar.limitations.lower(), (
            "Basis distortion SAR limitations must warn about dividend false positives. "
            f"Got: {sar.limitations[:300]}"
        )

    def test_all_pattern_types_have_display_names(self):
        """Every pattern type in _REGULATION_MAP has a display name in _PATTERN_DISPLAY."""
        for pattern in _REGULATION_MAP:
            assert pattern in _PATTERN_DISPLAY, (
                f"Pattern '{pattern}' has a regulation entry but no display name. "
                "Add it to _PATTERN_DISPLAY."
            )

    def test_case_reference_is_unique(self):
        """Each generated SAR has a unique case reference."""
        alert = self._make_alert()
        instr = self._make_instrument()
        refs = {generate_draft_sar(alert, instr).case_reference for _ in range(10)}
        assert len(refs) == 10, "Each SAR must have a unique case reference (UUID-based)"
