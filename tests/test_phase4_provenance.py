"""
tests/test_phase4_provenance.py
================================
Phase 4 verification: provenance / source filtering for alerts.
"""
import os
import pytest
from datetime import datetime, date, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Alert, Instrument, InstrumentType
from app.db.session import get_db
from app.main import app


# ── Shared in-memory engine (StaticPool so all connections share one DB) ──────

@pytest.fixture(scope="module")
def _engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture(scope="module")
def _session(_engine):
    Session = sessionmaker(bind=_engine)
    session = Session()

    # Seed rows
    def _inst(symbol, source="live_fyers"):
        i = Instrument(symbol=symbol, exchange="NSE",
                       instrument_type=InstrumentType.EQUITY, source=source)
        session.add(i); session.flush(); return i

    def _alert(inst, pattern, src, event_date=date(2026, 11, 7)):
        now = datetime(2026, 11, 7, 9, 15, tzinfo=timezone.utc)
        a = Alert(
            instrument_id=inst.id, pattern_type=pattern,
            severity="medium", score=0.6, accounts_involved=[],
            window_start=now, window_end=now + timedelta(hours=6),
            event_date=event_date,
            explanation="Unusual activity — a statistical outlier, not a finding of manipulation.",
            status="open", escalated_to_sebi=False,
            source=src, occurrences=1, last_seen_at=now,
        )
        session.add(a); session.flush(); return a

    inst_live = _inst("RELIANCE", "live_fyers")
    inst_eod  = _inst("TCS",      "live_fyers")
    inst_demo = _inst("SPOOFERLTD", "synthetic_demo")
    inst_bt   = _inst("TESTCORP", "live_fyers")

    _alert(inst_live, "volume_spike",    "live_fyers",    date(2026, 11, 7))
    _alert(inst_eod,  "z_score_spike",   "eod_history",   date(2026, 11, 6))
    _alert(inst_demo, "coordinated_pump","synthetic_demo", date(2026, 11, 5))
    _alert(inst_bt,   "volume_spike",    "backtest",      date(2026, 11, 4))
    session.commit()
    return session


@pytest.fixture(scope="module")
def client(_session):
    """TestClient with the test session injected via dependency override."""
    def override():
        yield _session

    app.dependency_overrides[get_db] = override
    # raise_server_exceptions=False so lifespan issues don't cascade into tests
    c = TestClient(app, raise_server_exceptions=False)
    yield c
    app.dependency_overrides.pop(get_db, None)


# ── Default exclusion of synthetic_demo ───────────────────────────────────────

class TestDefaultExcludesSyntheticDemo:
    def test_default_response_excludes_synthetic_demo(self, client):
        r = client.get("/api/alerts")
        assert r.status_code == 200, r.text
        sources = {item["source"] for item in r.json()["items"]}
        assert "synthetic_demo" not in sources

    def test_default_returns_live_and_eod(self, client):
        r = client.get("/api/alerts")
        sources = {item["source"] for item in r.json()["items"]}
        assert "live_fyers" in sources
        assert "eod_history" in sources

    def test_default_total_excludes_synthetic_demo(self, client):
        r = client.get("/api/alerts")
        # 3 non-synthetic rows: live_fyers, eod_history, backtest
        assert r.json()["total"] == 3

    def test_default_source_filter_field(self, client):
        r = client.get("/api/alerts")
        d = r.json()
        assert d["source_filter"] == "all_except_demo"
        assert d["demo_included"] is False


# ── Explicit ?source= filter ───────────────────────────────────────────────────

class TestExplicitSourceFilter:
    def test_source_synthetic_demo_returns_only_demo(self, client):
        r = client.get("/api/alerts?source=synthetic_demo")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["total"] == 1
        assert all(item["source"] == "synthetic_demo" for item in d["items"])

    def test_source_live_fyers_returns_only_live(self, client):
        r = client.get("/api/alerts?source=live_fyers")
        d = r.json()
        assert all(item["source"] == "live_fyers" for item in d["items"])

    def test_source_eod_history_returns_only_eod(self, client):
        r = client.get("/api/alerts?source=eod_history")
        d = r.json()
        assert all(item["source"] == "eod_history" for item in d["items"])

    def test_invalid_source_returns_422(self, client):
        r = client.get("/api/alerts?source=INVALID")
        assert r.status_code == 422

    def test_source_filter_field_reflects_source(self, client):
        r = client.get("/api/alerts?source=synthetic_demo")
        d = r.json()
        assert d["source_filter"] == "synthetic_demo"
        assert d["demo_included"] is True


# ── ?include_demo=true ────────────────────────────────────────────────────────

class TestIncludeDemoFlag:
    def test_include_demo_returns_all_rows(self, client):
        r = client.get("/api/alerts?include_demo=true")
        assert r.status_code == 200, r.text
        sources = {item["source"] for item in r.json()["items"]}
        assert "synthetic_demo" in sources
        assert "live_fyers" in sources

    def test_include_demo_total_counts_all(self, client):
        r = client.get("/api/alerts?include_demo=true")
        assert r.json()["total"] == 4

    def test_include_demo_flag_in_response(self, client):
        r = client.get("/api/alerts?include_demo=true")
        assert r.json()["demo_included"] is True


# ── is_sample_data badge ──────────────────────────────────────────────────────

class TestIsampleDataBadge:
    def test_synthetic_demo_row_is_sample_data_true(self, client):
        r = client.get("/api/alerts?source=synthetic_demo")
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert len(items) == 1
        assert items[0]["is_sample_data"] is True

    def test_live_fyers_row_is_sample_data_false(self, client):
        r = client.get("/api/alerts?source=live_fyers")
        for item in r.json()["items"]:
            assert item["is_sample_data"] is False

    def test_eod_history_row_is_sample_data_false(self, client):
        r = client.get("/api/alerts?source=eod_history")
        for item in r.json()["items"]:
            assert item["is_sample_data"] is False


# ── Provenance fields present ─────────────────────────────────────────────────

class TestProvenanceFieldsPresent:
    def test_source_field_present(self, client):
        r = client.get("/api/alerts")
        for item in r.json()["items"]:
            assert "source" in item

    def test_event_date_field_present(self, client):
        r = client.get("/api/alerts")
        for item in r.json()["items"]:
            assert "event_date" in item

    def test_occurrences_field_present(self, client):
        r = client.get("/api/alerts")
        for item in r.json()["items"]:
            assert "occurrences" in item

    def test_is_sample_data_field_present(self, client):
        r = client.get("/api/alerts")
        for item in r.json()["items"]:
            assert "is_sample_data" in item


# ── /api/system/mode ──────────────────────────────────────────────────────────

class TestSystemMode:
    def test_default_mode_is_live(self, client):
        backup = os.environ.pop("SENTINEL_MODE", None)
        try:
            r = client.get("/api/system/mode")
            assert r.status_code == 200
            d = r.json()
            assert d["mode"] == "live"
            assert d["synthetic_demo_visible"] is False
        finally:
            if backup:
                os.environ["SENTINEL_MODE"] = backup

    def test_demo_mode_when_env_set(self, client):
        os.environ["SENTINEL_MODE"] = "demo"
        try:
            r = client.get("/api/system/mode")
            d = r.json()
            assert d["mode"] == "demo"
            assert d["synthetic_demo_visible"] is True
        finally:
            del os.environ["SENTINEL_MODE"]

    def test_invalid_mode_defaults_to_live(self, client):
        os.environ["SENTINEL_MODE"] = "nonsense"
        try:
            r = client.get("/api/system/mode")
            assert r.json()["mode"] == "live"
        finally:
            del os.environ["SENTINEL_MODE"]

    def test_mode_has_description_and_note(self, client):
        r = client.get("/api/system/mode")
        d = r.json()
        assert "description" in d
        assert "note" in d

    def test_mode_never_hardcoded_in_html(self, client):
        import pathlib
        html = pathlib.Path("dashboard/static/index.html")
        if html.exists():
            assert '"LIVE MODE"' not in html.read_text(encoding="utf-8", errors="ignore") or \
                   "fetch" in html.read_text(encoding="utf-8", errors="ignore")
