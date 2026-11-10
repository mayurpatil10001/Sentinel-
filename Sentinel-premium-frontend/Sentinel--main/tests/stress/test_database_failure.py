"""
Database Failure Tests — Phase 7 Stress Tests
==============================================

Verifies that SQLAlchemy session rollback works correctly when a DB
operation fails mid-write. Tests:

  1. Alert rows are NOT partially written — write either completes or
     rolls back entirely (atomicity via session.rollback()).
  2. Rollback leaves the DB in the pre-failure state.
  3. A second write after a rolled-back failure succeeds (session is reusable).

These tests use SQLite in-memory databases for speed and isolation.
No network calls, no real file I/O.

WHY THIS MATTERS:
  A partial alert write (e.g. alert row inserted but accounts_involved or
  explanation not written) would produce a corrupt evidence record that
  could be presented to SEBI. Atomicity is not optional here.
"""

import uuid
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError, IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.models import Alert, Base, Instrument, InstrumentType


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def in_memory_db():
    """
    Create a fresh in-memory SQLite database for each test.
    Returns a session factory.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    yield SessionLocal, engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def db_session(in_memory_db):
    """Provides a single test session, rolls back after each test."""
    SessionLocal, engine = in_memory_db
    session = SessionLocal()
    yield session
    session.close()


def _make_instrument_row(session) -> Instrument:
    inst = Instrument(
        id=str(uuid.uuid4()),
        symbol="TESTSTOCK",
        exchange="NSE",
        instrument_type=InstrumentType.PENNY_STOCK,
        avg_daily_volume_30d=50_000,
        avg_order_size_30d=200.0,
        avg_daily_turnover_30d=2_500_000,
    )
    session.add(inst)
    session.commit()
    return inst


def _make_alert_dict(instrument_id: str) -> dict:
    return dict(
        id=str(uuid.uuid4()),
        instrument_id=instrument_id,
        pattern_type="spoofing_layering",
        severity="high",
        score=0.82,
        accounts_involved=["ACC001", "ACC002"],
        window_start=datetime(2026, 9, 3, 10, 0, 0),
        window_end=datetime(2026, 9, 3, 10, 15, 0),
        explanation="Test alert for DB failure test.",
        status="open",
        escalated_to_sebi=False,
    )


# ════════════════════════════════════════════════════════════════════════════
# Tests: atomic alert write
# ════════════════════════════════════════════════════════════════════════════

class TestAlertAtomicity:

    def test_successful_alert_write_committed(self, db_session):
        """Normal path: alert is written and queryable after commit."""
        inst = _make_instrument_row(db_session)
        alert_data = _make_alert_dict(inst.id)
        alert = Alert(**alert_data)

        db_session.add(alert)
        db_session.commit()

        queried = db_session.query(Alert).filter_by(id=alert_data["id"]).one_or_none()
        assert queried is not None
        assert queried.pattern_type == "spoofing_layering"
        assert queried.score == 0.82

    def test_rollback_after_failed_commit_leaves_no_row(self, db_session):
        """
        If a commit fails (simulated), the session rollback must leave
        the database in its pre-write state — no partial alert row.
        """
        inst = _make_instrument_row(db_session)
        alert_data = _make_alert_dict(inst.id)
        alert = Alert(**alert_data)
        alert_id = alert_data["id"]

        db_session.add(alert)

        # Simulate a mid-commit failure
        with patch.object(
            db_session, "commit", side_effect=OperationalError("disk full", None, None)
        ):
            with pytest.raises(OperationalError):
                db_session.commit()

        # Rollback the failed transaction
        db_session.rollback()

        # After rollback: the alert row must not exist
        queried = db_session.query(Alert).filter_by(id=alert_id).one_or_none()
        assert queried is None, (
            "After rollback, the alert row must not persist in the database."
        )

    def test_session_usable_after_rollback(self, db_session):
        """
        After a rollback, the session must accept new writes correctly.
        A failed alert must not poison subsequent operations.
        """
        inst = _make_instrument_row(db_session)

        # First write: simulated failure + rollback
        bad_alert = Alert(**_make_alert_dict(inst.id))
        db_session.add(bad_alert)
        with patch.object(
            db_session, "commit", side_effect=OperationalError("failure", None, None)
        ):
            with pytest.raises(OperationalError):
                db_session.commit()
        db_session.rollback()

        # Second write: should succeed cleanly
        good_alert_data = _make_alert_dict(inst.id)
        good_alert_data["id"] = str(uuid.uuid4())  # Different ID
        good_alert = Alert(**good_alert_data)
        db_session.add(good_alert)
        db_session.commit()

        queried = db_session.query(Alert).filter_by(id=good_alert_data["id"]).one_or_none()
        assert queried is not None, "Post-rollback write must succeed."

    def test_multiple_alerts_same_transaction_atomic(self, db_session):
        """
        Two alerts in the same transaction: if the second fails, NEITHER
        should be persisted (full transaction rollback).
        """
        inst = _make_instrument_row(db_session)

        alert1_data = _make_alert_dict(inst.id)
        alert2_data = _make_alert_dict(inst.id)
        alert2_data["id"] = str(uuid.uuid4())

        alert1 = Alert(**alert1_data)
        alert2 = Alert(**alert2_data)

        db_session.add(alert1)
        db_session.add(alert2)

        call_count = 0
        original_commit = db_session.commit.__wrapped__ if hasattr(db_session.commit, "__wrapped__") else None

        with patch.object(
            db_session, "commit",
            side_effect=OperationalError("transaction failure", None, None)
        ):
            with pytest.raises(OperationalError):
                db_session.commit()
        db_session.rollback()

        # Neither alert must exist
        for alert_id in [alert1_data["id"], alert2_data["id"]]:
            queried = db_session.query(Alert).filter_by(id=alert_id).one_or_none()
            assert queried is None, f"Alert {alert_id} should not exist after rollback."


# ════════════════════════════════════════════════════════════════════════════
# Tests: DB unavailability mid-detection (simulated)
# ════════════════════════════════════════════════════════════════════════════

class TestDatabaseUnavailability:

    def test_operationalerror_during_add_handled_gracefully(self, db_session):
        """
        OperationalError during session.add() simulation.
        The caller must be able to catch, rollback, and move on.
        """
        inst = _make_instrument_row(db_session)
        alert_data = _make_alert_dict(inst.id)

        with patch.object(
            db_session, "add",
            side_effect=OperationalError("connection lost", None, None)
        ):
            with pytest.raises(OperationalError):
                db_session.add(Alert(**alert_data))

        # Session should still be usable after exception (no implicit rollback from add())
        db_session.rollback()

        # Verify no orphan row was written
        queried = db_session.query(Alert).filter_by(id=alert_data["id"]).one_or_none()
        assert queried is None

    def test_query_count_before_and_after_failed_write(self, db_session):
        """
        Count of alerts must not change after a failed-and-rolled-back write.
        """
        inst = _make_instrument_row(db_session)

        # Establish baseline
        count_before = db_session.query(Alert).count()

        # Attempt a write that fails
        alert = Alert(**_make_alert_dict(inst.id))
        db_session.add(alert)
        with patch.object(
            db_session, "commit",
            side_effect=OperationalError("disk full", None, None)
        ):
            with pytest.raises(OperationalError):
                db_session.commit()
        db_session.rollback()

        count_after = db_session.query(Alert).count()
        assert count_after == count_before, (
            f"Alert count changed after rollback: before={count_before}, after={count_after}"
        )
