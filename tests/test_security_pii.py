"""
Phase 5b Tests — PII Protection, Retention, and Access Audit
=============================================================

Tests for:
  app/security/pii.py         — hash_account_identifier, mask_pan
  app/security/retention.py   — purge_expired_orders
  app/security/access_log.py  — EvidenceAccessLog, log_evidence_access
  app/detection/evidence.py   — access-log wiring, hash/raw-ID mode

VERIFICATION CHECKLIST (Hard Rule #3)
--------------------------------------
For each capability:
  POSITIVE: the protection/feature actually works.
  NEGATIVE: legitimate access / recent data is NOT blocked/purged.

Run:
    pytest tests/test_security_pii.py -v
"""

import os
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Alert, Instrument, Order, Trade, InstrumentType
from app.db.models import OrderStatus, OrderSide, EVIDENCE_LOG_USE_HASHED_ID
from app.security.pii import (
    hash_account_identifier,
    mask_pan,
    is_valid_pan_format,
)
from app.security.retention import (
    purge_expired_orders,
    RETENTION_DAYS_DEFAULT,
)
from app.security.access_log import (
    EvidenceAccessLog,
    log_evidence_access,
)
from app.detection.evidence import build_evidence_log


# ── Test DB setup ──────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    """In-memory SQLite session with EvidenceAccessLog table included."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    # Import EvidenceAccessLog so its table is included in Base.metadata
    from app.security.access_log import EvidenceAccessLog as _AL  # noqa: F401
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def set_test_salt(monkeypatch):
    """Set a deterministic test salt so hash tests don't require env setup."""
    monkeypatch.setenv("ACCOUNT_ID_SALT", "test-salt-for-sentinel-unit-tests-only")


def _make_instrument(db) -> Instrument:
    instr = Instrument(
        id=str(uuid.uuid4()),
        symbol="TESTCO",
        exchange="NSE",
        instrument_type=InstrumentType.EQUITY,
    )
    db.add(instr)
    db.commit()
    db.refresh(instr)
    return instr


def _make_order(db, instrument, account_id="ACC001", days_old=0) -> Order:
    ts = datetime.utcnow() - timedelta(days=days_old)
    order = Order(
        id=str(uuid.uuid4()),
        exchange_order_id=f"EXO-{uuid.uuid4().hex[:8]}",
        account_id=account_id,
        instrument_id=instrument.id,
        side=OrderSide.BUY,
        status=OrderStatus.EXECUTED,
        price=100.0,
        quantity=100,
        filled_quantity=100,
        timestamp=ts,
        exchange="NSE",
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def _make_alert(db, instrument, status="open", days_old=0) -> Alert:
    ts = datetime.utcnow() - timedelta(days=days_old)
    alert = Alert(
        id=str(uuid.uuid4()),
        instrument_id=instrument.id,
        pattern_type="circular_trading",
        severity="high",
        score=0.80,
        accounts_involved=["ACC001"],
        window_start=ts - timedelta(hours=1),
        window_end=ts,
        explanation="[TIER 2] Ring detected.",
        status=status,
        escalated_to_sebi=(status == "escalated"),
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert


# ══════════════════════════════════════════════════════════════════════════════
# Tests: hash_account_identifier
# ══════════════════════════════════════════════════════════════════════════════

class TestHashAccountIdentifier:

    def test_deterministic_same_salt(self):
        """
        POSITIVE: same input + same salt always produces the same hash.
        Required for database lookups: we must be able to re-hash at query
        time and match stored hashes.
        """
        h1 = hash_account_identifier("ACC001", salt="fixed-salt")
        h2 = hash_account_identifier("ACC001", salt="fixed-salt")
        assert h1 == h2, "Hash must be deterministic for same input+salt"

    def test_different_salt_changes_hash(self):
        """
        POSITIVE (proves salting actually works): same raw_id with different
        salts must produce different hashes. This is the core property that
        prevents rainbow-table attacks across deployments.
        """
        h_salt_a = hash_account_identifier("ACC001", salt="salt-A")
        h_salt_b = hash_account_identifier("ACC001", salt="salt-B")
        assert h_salt_a != h_salt_b, (
            "Different salts must produce different hashes for the same raw_id. "
            "If this fails, salting is not working — the system is vulnerable to "
            "pre-computed hash tables."
        )

    def test_different_inputs_different_hashes(self):
        """POSITIVE: different account IDs hash to different values."""
        h1 = hash_account_identifier("ACC001", salt="salt")
        h2 = hash_account_identifier("ACC002", salt="salt")
        assert h1 != h2

    def test_output_is_hex_string_64_chars(self):
        """POSITIVE: SHA-256 output is exactly 64 hex characters."""
        h = hash_account_identifier("ACC001", salt="salt")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h), \
            "Output must be lowercase hex"

    def test_does_not_return_input_unchanged(self):
        """
        NEGATIVE (anti-regression): the function must never return the raw_id
        unchanged, even for edge cases. A hash that equals the input would
        indicate a broken implementation.
        """
        test_inputs = ["A", "AB", "ABC", "1234", "ABCDE1234F"]
        salt = "test-salt"
        for raw_id in test_inputs:
            h = hash_account_identifier(raw_id, salt=salt)
            assert h != raw_id, (
                f"hash_account_identifier returned the input unchanged for '{raw_id}'. "
                "This indicates the function is not hashing — it is leaking raw IDs."
            )

    def test_does_not_return_raw_id_for_short_input(self):
        """
        NEGATIVE: a one-character input still gets hashed, not returned as-is.
        SHA-256 of a 1-char input is 64 hex chars — far longer than the input.
        """
        h = hash_account_identifier("X", salt="salt")
        assert h != "X"
        assert len(h) == 64

    def test_raises_on_empty_string(self):
        """
        NEGATIVE: empty string raises ValueError. An empty-string hash could
        accidentally match other empty-string inputs, creating false joins.
        """
        with pytest.raises(ValueError, match="non-empty"):
            hash_account_identifier("", salt="salt")

    def test_raises_on_whitespace_only(self):
        """NEGATIVE: whitespace-only input raises ValueError."""
        with pytest.raises(ValueError, match="non-empty"):
            hash_account_identifier("   ", salt="salt")

    def test_reads_salt_from_env(self):
        """
        POSITIVE: when no salt is passed, function reads ACCOUNT_ID_SALT.
        The autouse fixture sets this env var — so this should succeed.
        """
        h = hash_account_identifier("ACC001")
        assert len(h) == 64

    def test_raises_when_env_salt_missing(self, monkeypatch):
        """NEGATIVE: missing env var raises RuntimeError (not silent default)."""
        monkeypatch.delenv("ACCOUNT_ID_SALT", raising=False)
        with pytest.raises(RuntimeError, match="ACCOUNT_ID_SALT"):
            hash_account_identifier("ACC001")

    def test_raises_on_short_env_salt(self, monkeypatch):
        """NEGATIVE: salt shorter than 16 chars raises RuntimeError."""
        monkeypatch.setenv("ACCOUNT_ID_SALT", "tooshort")
        with pytest.raises(RuntimeError, match="16 characters"):
            hash_account_identifier("ACC001")


# ══════════════════════════════════════════════════════════════════════════════
# Tests: mask_pan
# ══════════════════════════════════════════════════════════════════════════════

class TestMaskPan:

    def test_masks_all_but_last_4(self):
        """POSITIVE: only the last 4 characters are visible."""
        masked = mask_pan("ABCDE1234F")
        assert masked.endswith("234F"), \
            f"Last 4 chars must be visible. Got: {masked}"
        assert masked.startswith("XXXXXX"), \
            f"All but last 4 must be masked. Got: {masked}"
        assert len(masked) == len("ABCDE1234F"), \
            "Masked output must be same length as input"

    def test_all_masked_chars_are_x(self):
        """POSITIVE: masked characters are literal 'X', not redacted differently."""
        masked = mask_pan("ABCDE1234F")
        prefix = masked[:-4]
        assert all(c == "X" for c in prefix), \
            f"Masked prefix must be all 'X'. Got: '{prefix}'"

    def test_short_pan_returned_fully(self):
        """NEGATIVE (edge case): PAN of 4 or fewer chars is returned as-is."""
        assert mask_pan("ABCD") == "ABCD"
        assert mask_pan("ABC") == "ABC"

    def test_empty_pan_returns_empty(self):
        """NEGATIVE: empty string returns empty string, no error."""
        assert mask_pan("") == ""

    def test_masking_is_not_a_hash(self):
        """
        NEGATIVE (contract check): masking does NOT produce a 64-char hex string.
        This verifies callers haven't confused mask_pan with hash_account_identifier.
        """
        masked = mask_pan("ABCDE1234F")
        assert len(masked) != 64, \
            "mask_pan result should NOT be 64 chars — it should not be a hash"
        assert masked != hash_account_identifier("ABCDE1234F", salt="salt"), \
            "mask_pan and hash_account_identifier must produce different outputs"

    def test_valid_pan_format(self):
        """POSITIVE: is_valid_pan_format correctly validates PAN format."""
        assert is_valid_pan_format("ABCDE1234F") is True
        assert is_valid_pan_format("ABCDE1234f") is True  # case-insensitive

    def test_invalid_pan_format(self):
        """NEGATIVE: invalid formats correctly return False."""
        assert is_valid_pan_format("") is False
        assert is_valid_pan_format("1234567890") is False  # starts with digit
        assert is_valid_pan_format("ABCDE123") is False   # too short


# ══════════════════════════════════════════════════════════════════════════════
# Tests: purge_expired_orders
# ══════════════════════════════════════════════════════════════════════════════

class TestPurgeExpiredOrders:

    def test_purges_old_unreferenced_orders(self, db):
        """
        POSITIVE: orders older than retention_days with no active alert
        are deleted.
        """
        instr = _make_instrument(db)
        # 3000 days old — well outside any sane retention window
        old_order = _make_order(db, instr, days_old=3000)
        assert db.query(Order).count() == 1

        result = purge_expired_orders(db, retention_days=365)

        assert result["orders_deleted"] == 1
        assert db.query(Order).count() == 0, \
            "Old unreferenced order must be deleted"

    def test_does_not_purge_recent_orders(self, db):
        """
        NEGATIVE: orders within the retention window are NOT deleted.
        """
        instr = _make_instrument(db)
        _make_order(db, instr, days_old=10)   # 10 days old, well within window

        result = purge_expired_orders(db, retention_days=365)

        assert result["orders_deleted"] == 0
        assert db.query(Order).count() == 1, \
            "Recent order must NOT be purged"

    def test_does_not_purge_order_protected_by_open_alert(self, db):
        """
        NEGATIVE (CRITICAL): an old Order that falls within the window of
        an OPEN Alert must NOT be purged, regardless of age.

        This is the most important test in the suite. Evidence tied to an
        active investigation must survive even if the data is outside the
        normal retention window.
        """
        instr = _make_instrument(db)
        # Create an order 3000 days old
        old_order = _make_order(db, instr, account_id="ACC001", days_old=3000)
        # Create an OPEN alert whose window covers this old order
        alert = _make_alert(db, instr, status="open", days_old=3000)

        assert db.query(Order).count() == 1
        result = purge_expired_orders(db, retention_days=365)

        # Order must survive because it falls within an open alert's window
        assert db.query(Order).count() == 1, (
            "Old order protected by an OPEN alert must NOT be purged. "
            f"purge result: {result}"
        )
        assert result["protected_by_alert"] >= 1, \
            "protected_by_alert count must reflect skipped rows"
        assert result["orders_deleted"] == 0

    def test_does_not_purge_order_protected_by_escalated_alert(self, db):
        """
        NEGATIVE: same test with status='escalated' — escalated alerts
        (referred to SEBI) must also protect their evidence.
        """
        instr = _make_instrument(db)
        _make_order(db, instr, account_id="ACC001", days_old=3000)
        _make_alert(db, instr, status="escalated", days_old=3000)

        result = purge_expired_orders(db, retention_days=365)
        assert db.query(Order).count() == 1, \
            "Order protected by escalated alert must NOT be purged"

    def test_purges_order_protected_only_by_closed_alert(self, db):
        """
        POSITIVE: a 'closed' alert does NOT protect its orders from purging.
        Once a case is closed, the data reverts to normal retention schedule.

        This test specifically uses a CLOSED alert — the requirement says
        only open/investigating/escalated alerts protect rows.
        """
        instr = _make_instrument(db)
        _make_order(db, instr, account_id="ACC001", days_old=3000)
        _make_alert(db, instr, status="closed", days_old=3000)

        result = purge_expired_orders(db, retention_days=365)
        assert result["orders_deleted"] == 1, (
            "Old order tied ONLY to a CLOSED alert must be purged "
            "(closed = no active investigation). "
            f"purge result: {result}"
        )

    def test_dry_run_does_not_delete(self, db):
        """POSITIVE: dry_run=True reports counts but makes no changes."""
        instr = _make_instrument(db)
        _make_order(db, instr, days_old=3000)

        result = purge_expired_orders(db, retention_days=365, dry_run=True)

        assert result["orders_deleted"] == 1   # would be deleted
        assert result["dry_run"] is True
        assert db.query(Order).count() == 1, \
            "dry_run must NOT actually delete rows"

    def test_raises_on_none_db(self):
        """HARD RULE #1: None db raises TypeError."""
        with pytest.raises(TypeError, match="db is None"):
            purge_expired_orders(None)

    def test_raises_on_zero_retention_days(self, db):
        """Zero retention_days raises ValueError."""
        with pytest.raises(ValueError, match="retention_days"):
            purge_expired_orders(db, retention_days=0)

    def test_default_retention_constant_is_documented(self):
        """
        Meta-test: the default retention constant is 7 years (2555 days).
        If this value changes silently, this test will catch it and force
        a deliberate decision.
        """
        assert RETENTION_DAYS_DEFAULT == 2555, (
            f"RETENTION_DAYS_DEFAULT changed to {RETENTION_DAYS_DEFAULT}. "
            "This is a compliance-sensitive constant — any change must be "
            "reviewed against SEBI/regulatory recordkeeping requirements."
        )


# ══════════════════════════════════════════════════════════════════════════════
# Tests: access log + evidence.py integration
# ══════════════════════════════════════════════════════════════════════════════

class TestEvidenceAccessLog:

    def test_log_evidence_access_creates_row(self, db):
        """
        POSITIVE: calling log_evidence_access creates one EvidenceAccessLog row.
        """
        log_evidence_access(
            db,
            alert_id="alert-001",
            accessed_by="analyst-A",
            used_raw_ids=False,
            evidence_row_count=5,
        )
        assert db.query(EvidenceAccessLog).count() == 1

    def test_access_log_records_correct_fields(self, db):
        """POSITIVE: the logged row captures alert_id, caller, and ID mode."""
        log_evidence_access(
            db,
            alert_id="alert-001",
            accessed_by="analyst-A",
            used_raw_ids=True,
            evidence_row_count=3,
            source_ip="10.0.0.1",
        )
        entry = db.query(EvidenceAccessLog).first()
        assert entry.alert_id == "alert-001"
        assert entry.accessed_by == "analyst-A"
        assert entry.used_raw_ids is True
        assert entry.evidence_row_count == 3
        assert entry.source_ip == "10.0.0.1"
        assert entry.accessed_at is not None

    def test_access_log_does_not_store_payload(self, db):
        """
        NEGATIVE (CRITICAL): the EvidenceAccessLog table must NOT contain
        any account ID data — it logs WHO/WHEN/WHICH ALERT, not WHAT WAS RETURNED.

        This verifies that the access log doesn't accidentally duplicate
        sensitive data into a second, potentially less-protected location.
        """
        log_evidence_access(
            db,
            alert_id="alert-001",
            accessed_by="analyst-A",
            used_raw_ids=False,
            evidence_row_count=5,
        )
        entry = db.query(EvidenceAccessLog).first()

        # The table must NOT have columns that could store account data
        columns = [c.key for c in EvidenceAccessLog.__table__.columns]
        sensitive_column_names = [
            "account_id", "account_id_hash", "order_id", "payload",
            "evidence_data", "raw_data"
        ]
        for sensitive_col in sensitive_column_names:
            assert sensitive_col not in columns, (
                f"EvidenceAccessLog must NOT have a '{sensitive_col}' column. "
                "Storing account data in the access log creates a second copy "
                "of sensitive data with potentially weaker controls."
            )

    def test_raises_on_none_db(self):
        """HARD RULE #1: None db raises TypeError."""
        with pytest.raises(TypeError, match="db is None"):
            log_evidence_access(None, "alert-001", "analyst", False, 0)

    def test_raises_on_empty_alert_id(self, db):
        """HARD RULE #1: empty alert_id raises ValueError."""
        with pytest.raises(ValueError, match="alert_id"):
            log_evidence_access(db, "", "analyst", False, 0)

    def test_raises_on_empty_accessed_by(self, db):
        """HARD RULE #1: empty accessed_by raises ValueError."""
        with pytest.raises(ValueError):
            log_evidence_access(db, "alert-001", "", False, 0)

    def test_build_evidence_log_creates_access_log_entry(self, db):
        """
        POSITIVE: calling build_evidence_log automatically creates one
        EvidenceAccessLog entry. This is the wiring test — ensures the
        caller cannot forget to log access.
        """
        instr = _make_instrument(db)
        order = _make_order(db, instr)
        alert = _make_alert(db, instr)
        # Attach instrument relationship manually for the in-memory test
        alert.instrument = instr

        build_evidence_log(db, alert, accessed_by="analyst-B")

        assert db.query(EvidenceAccessLog).count() == 1, (
            "build_evidence_log must automatically create an EvidenceAccessLog entry. "
            "If count is 0, the logging is not wired into the function."
        )
        entry = db.query(EvidenceAccessLog).first()
        assert entry.accessed_by == "analyst-B"
        assert entry.alert_id == str(alert.id)

    def test_build_evidence_log_uses_hashed_id_by_default(self, db):
        """
        POSITIVE: with EVIDENCE_LOG_USE_HASHED_ID=True (default), the
        evidence log should use hashed IDs and log used_raw_ids=False.

        We can verify via the access log entry.
        """
        instr = _make_instrument(db)
        # Create order WITH a pre-set account_id_hash
        order = _make_order(db, instr, account_id="ACC001")
        order.account_id_hash = hash_account_identifier("ACC001")
        db.commit()

        alert = _make_alert(db, instr)
        alert.instrument = instr

        # use_raw_ids=False explicitly to bypass global flag for test isolation
        result = build_evidence_log(db, alert, accessed_by="test", use_raw_ids=False)

        entry = db.query(EvidenceAccessLog).first()
        assert entry.used_raw_ids is False, \
            "Access log must record that hashed IDs were used (not raw)"
        # The returned account_id in rows should be the hash (64 chars)
        if result.rows:
            returned_id = result.rows[0]["account_id"]
            assert len(returned_id) == 64, \
                f"Hashed ID must be 64 chars. Got: {returned_id!r}"
            assert returned_id != "ACC001", \
                "Returned account_id must be the hash, not the raw ID"

    def test_build_evidence_log_raw_id_mode_logged_as_such(self, db):
        """
        POSITIVE: when use_raw_ids=True, the access log entry records
        used_raw_ids=True so raw-ID access is visible and auditable.
        """
        instr = _make_instrument(db)
        _make_order(db, instr, account_id="ACC001")
        alert = _make_alert(db, instr)
        alert.instrument = instr

        build_evidence_log(db, alert, accessed_by="sebi-verifier", use_raw_ids=True)

        entry = db.query(EvidenceAccessLog).first()
        assert entry.used_raw_ids is True, (
            "Raw-ID access must be recorded in the access log with used_raw_ids=True. "
            "If this fails, raw-ID access is occurring but not being flagged."
        )

    def test_multiple_evidence_pulls_create_multiple_log_entries(self, db):
        """
        POSITIVE: each call to build_evidence_log creates a separate access
        log entry (not idempotent). This ensures every pull is individually
        auditable.
        """
        instr = _make_instrument(db)
        _make_order(db, instr)
        alert = _make_alert(db, instr)
        alert.instrument = instr

        build_evidence_log(db, alert, accessed_by="analyst-A")
        build_evidence_log(db, alert, accessed_by="analyst-B")

        count = db.query(EvidenceAccessLog).count()
        assert count == 2, \
            f"Two evidence pulls must create 2 access log entries. Got {count}."
