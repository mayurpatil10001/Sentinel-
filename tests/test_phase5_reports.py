"""
tests/test_phase5_reports.py
=============================
Phase 5: EOD Manipulation Watch Report API.
"""
import pytest
from datetime import datetime, date, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Alert, Instrument, InstrumentType, ScanRun
from app.db.session import get_db
from app.main import app


@pytest.fixture(scope="module")
def _engine():
    e = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(e)
    return e


@pytest.fixture(scope="module")
def _session(_engine):
    Session = sessionmaker(bind=_engine)
    s = Session()

    today = date(2026, 11, 7)
    d_1   = date(2026, 11, 6)
    d_5   = date(2026, 11, 2)   # outside 3-day window

    def inst(sym, src="live_fyers"):
        i = Instrument(symbol=sym, exchange="NSE",
                       instrument_type=InstrumentType.EQUITY, source=src)
        s.add(i); s.flush(); return i

    def alert(i, pat, sev, score, event_date, src, occ=1):
        now = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)
        a = Alert(
            instrument_id=i.id, pattern_type=pat, severity=sev, score=score,
            accounts_involved=[], window_start=now,
            window_end=now + timedelta(hours=6),
            event_date=event_date,
            explanation=("Unusual activity — a statistical outlier, not a finding of "
                         f"manipulation. Pattern={pat}."),
            status="open", escalated_to_sebi=False,
            source=src, occurrences=occ, last_seen_at=now, detected_at=now,
        )
        s.add(a); s.flush(); return a

    i_rel  = inst("RELIANCE")
    i_tcs  = inst("TCS")
    i_demo = inst("SPOOFERLTD", "synthetic_demo")
    i_bt   = inst("TESTCORP")
    i_old  = inst("OLDCORP")

    alert(i_rel,  "volume_spike",    "high",   0.85, today,  "live_fyers",    occ=1)
    alert(i_tcs,  "z_score_spike",   "medium", 0.55, d_1,    "live_fyers",    occ=3)
    alert(i_demo, "coordinated_pump","critical",0.95, today, "synthetic_demo", occ=1)
    alert(i_bt,   "momentum_reversal","low",    0.3,  today,  "backtest",      occ=2)
    alert(i_old,  "volume_spike",    "medium", 0.6,  d_5,    "live_fyers",    occ=1)

    # Scan run
    fin = datetime(2026, 11, 7, 15, 35, tzinfo=timezone.utc)
    r = ScanRun(started_at=fin-timedelta(minutes=2), finished_at=fin,
                mode="eod", symbols_scanned=10, symbols_failed=0,
                detections=2, alerts_created=1, alerts_updated=1, status="ok")
    s.add(r); s.commit()
    return s


@pytest.fixture(scope="module")
def client(_session):
    # Patch _ist_today so the report window is deterministic
    import app.api.reports as rep
    rep._ist_today = lambda: date(2026, 11, 7)

    def override():
        yield _session

    app.dependency_overrides[get_db] = override
    c = TestClient(app, raise_server_exceptions=False)
    yield c
    app.dependency_overrides.pop(get_db, None)


# ── JSON report ───────────────────────────────────────────────────────────────

class TestWatchReportJSON:
    def test_returns_200(self, client):
        r = client.get("/api/reports/watch?days=3")
        assert r.status_code == 200, r.text

    def test_has_required_top_level_keys(self, client):
        d = client.get("/api/reports/watch?days=3").json()
        for k in ("report_type","generated_at","period","summary","alerts",
                  "data_provenance","limitations","analyst_guidance"):
            assert k in d, f"Missing key: {k}"

    def test_report_type(self, client):
        assert client.get("/api/reports/watch").json()["report_type"] == "manipulation_watch"

    def test_summary_total_excludes_synthetic_demo(self, client):
        d = client.get("/api/reports/watch?days=3").json()
        # In 3-day window (Nov 5-7): live_fyers(2) + backtest(1) = 3; synthetic excluded
        assert d["summary"]["total_alerts"] == 3

    def test_high_critical_count(self, client):
        d = client.get("/api/reports/watch?days=3").json()
        assert d["summary"]["high_critical"] == 1   # RELIANCE high

    def test_medium_count(self, client):
        d = client.get("/api/reports/watch?days=3").json()
        assert d["summary"]["medium"] == 1   # TCS

    def test_low_count(self, client):
        d = client.get("/api/reports/watch?days=3").json()
        assert d["summary"]["low"] == 1   # TESTCORP backtest

    def test_new_this_period(self, client):
        d = client.get("/api/reports/watch?days=3").json()
        # occurrences=1 → new: only RELIANCE (occ=1); TCS occ=3, TESTCORP occ=2
        assert d["summary"]["new_this_period"] == 1

    def test_existing_redetected(self, client):
        d = client.get("/api/reports/watch?days=3").json()
        assert d["summary"]["existing_redetected"] == 2

    def test_old_alert_outside_window_excluded(self, client):
        d = client.get("/api/reports/watch?days=3").json()
        all_inst = [a["instrument"] for tier in d["alerts"].values() for a in tier]
        assert "OLDCORP" not in all_inst

    def test_synthetic_demo_excluded_by_default(self, client):
        d = client.get("/api/reports/watch?days=3").json()
        all_inst = [a["instrument"] for tier in d["alerts"].values() for a in tier]
        assert "SPOOFERLTD" not in all_inst

    def test_source_synthetic_demo_returns_only_demo(self, client):
        d = client.get("/api/reports/watch?days=3&source=synthetic_demo").json()
        assert d["summary"]["total_alerts"] == 1
        hc = d["alerts"]["high_critical"]
        assert len(hc) == 1 and hc[0]["instrument"] == "SPOOFERLTD"

    def test_data_provenance_contains_unvalidated(self, client):
        d = client.get("/api/reports/watch").json()
        assert "UNVALIDATED" in d["data_provenance"].upper() or \
               "unvalidated" in d["data_provenance"].lower()

    def test_limitations_mentions_no_spoofing_without_order_data(self, client):
        d = client.get("/api/reports/watch").json()
        lim = d["limitations"].lower()
        assert "order" in lim and ("spoofing" in lim or "not available" in lim)

    def test_disclaimer_statistical_outlier(self, client):
        d = client.get("/api/reports/watch").json()
        all_exp = [a["explanation"] for tier in d["alerts"].values() for a in tier]
        assert any("statistical outlier" in e.lower() or "not a finding" in e.lower()
                   for e in all_exp)

    def test_high_critical_alerts_have_sar_url(self, client):
        d = client.get("/api/reports/watch").json()
        for a in d["alerts"]["high_critical"]:
            assert a.get("sar_draft_url") is not None, f"high alert {a.get('alert_id')} missing sar_draft_url"

    def test_medium_low_no_sar_url(self, client):
        d = client.get("/api/reports/watch").json()
        for tier in ("medium", "low"):
            for a in d["alerts"][tier]:
                assert a.get("sar_draft_url") is None

    def test_period_matches_requested_days(self, client):
        d = client.get("/api/reports/watch?days=5").json()
        assert d["period"]["days"] == 5

    def test_last_scan_from_db(self, client):
        d = client.get("/api/reports/watch").json()
        assert d["last_scan"]["finished_at"] is not None
        assert d["last_scan"]["symbols_scanned"] == 10

    def test_provenance_always_present_even_when_empty(self, client):
        # 1-day window for today only — may have alerts
        d = client.get("/api/reports/watch?days=1").json()
        assert "data_provenance" in d
        assert "total_alerts" in d["summary"]


# ── HTML report ───────────────────────────────────────────────────────────────

class TestWatchReportHTML:
    def test_returns_200(self, client):
        r = client.get("/api/reports/watch/html?days=3")
        assert r.status_code == 200, r.text

    def test_content_type_html(self, client):
        r = client.get("/api/reports/watch/html?days=3")
        assert "text/html" in r.headers.get("content-type", "")

    def test_contains_provenance_warning(self, client):
        r = client.get("/api/reports/watch/html?days=3")
        assert "UNVALIDATED" in r.text or "unvalidated" in r.text.lower()

    def test_no_fabricated_latency(self, client):
        r = client.get("/api/reports/watch/html?days=3")
        assert "0.38 ms" not in r.text
        assert "48,250" not in r.text
        assert "FALSE POSITIVE RATE" not in r.text

    def test_no_hardcoded_client_id(self, client):
        r = client.get("/api/reports/watch/html?days=3")
        assert "FAA25578" not in r.text

    def test_no_connected_badge_hardcoded(self, client):
        r = client.get("/api/reports/watch/html?days=3")
        assert '"CONNECTED"' not in r.text
