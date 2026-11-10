"""
tests/test_phase6_frontend_honesty.py
=======================================
Phase 6: Frontend truth — verify index.html does NOT contain fabricated values
or hardcoded status chips that are not traced to real API responses.

These tests are build-time checks (they read the HTML file, not a running server).
They must pass on every commit.

HARD RULES (from spec):
- index.html must NOT contain hardcoded "0.38 ms" detection latency.
- index.html must NOT contain hardcoded "48,250 msgs/s" ingest rate.
- index.html must NOT contain "FALSE POSITIVE RATE: 0.0% (90-DAY EMPIRICAL)".
- index.html must NOT contain the broker client ID ("FAA25578").
- index.html must NOT contain hardcoded "CONNECTED" as a bare static string.
- Subtitle must NOT claim live detection of "order book layering" or "circular trade loops".
- Detector disclosure must NOT say "SEBI CASE INFORMED" or "EMPIRICALLY TESTED" (Phase 2 mandate).
"""
import pathlib
import pytest

HTML_PATH = pathlib.Path(__file__).parent.parent / "dashboard" / "static" / "index.html"


@pytest.fixture(scope="module")
def html_text():
    if not HTML_PATH.exists():
        pytest.skip(f"Dashboard HTML not found: {HTML_PATH}")
    return HTML_PATH.read_text(encoding="utf-8", errors="ignore")


class TestNoFabricatedNumbers:
    def test_no_hardcoded_038ms_latency(self, html_text):
        """0.38 ms was a fabricated detection latency — must be gone."""
        assert "0.38 ms" not in html_text, (
            "index.html contains hardcoded '0.38 ms' detection latency. "
            "Replace with a value read from /api/live/scan/runs."
        )

    def test_no_hardcoded_48250_ingest_rate(self, html_text):
        """48,250 msgs/s was a fabricated ingest rate — must be gone."""
        assert "48,250 msgs/s" not in html_text, (
            "index.html contains hardcoded '48,250 msgs/s' ingest rate — a fabricated value."
        )

    def test_no_hardcoded_fp_rate_90_day(self, html_text):
        """'0.0% (90-DAY EMPIRICAL)' was a fabricated false-positive claim — must be gone."""
        assert "0.0% (90-DAY EMPIRICAL)" not in html_text, (
            "index.html contains hardcoded '0.0% (90-DAY EMPIRICAL)' — no such study exists."
        )

    def test_no_hardcoded_fp_rate_in_ticker(self, html_text):
        """FALSE POSITIVE RATE in the ticker bar must not have a hardcoded value."""
        assert "FALSE POSITIVE RATE:" not in html_text, (
            "index.html contains 'FALSE POSITIVE RATE:' in ticker — must read from API."
        )


class TestNoBrokerIdHardcoded:
    def test_no_broker_client_id_in_html(self, html_text):
        """Broker client ID must never appear in HTML or JS."""
        assert "FAA25578" not in html_text, (
            "index.html contains hardcoded broker client ID 'FAA25578'. "
            "Must read from /api/live/status at runtime."
        )


class TestNoFalseAuthorityPhrases:
    def test_no_sebi_case_informed_label(self, html_text):
        """'SEBI CASE INFORMED' implies a citation that doesn't exist."""
        assert "SEBI CASE INFORMED" not in html_text, (
            "index.html contains 'SEBI CASE INFORMED' — a false authority claim. "
            "Must be replaced with 'UNVALIDATED_HEURISTIC'."
        )

    def test_no_empirically_tested_label_without_basis(self, html_text):
        """'EMPIRICALLY TESTED' label without real backtests is a false claim."""
        assert "EMPIRICALLY TESTED" not in html_text, (
            "index.html contains 'EMPIRICALLY TESTED' label — no real study backs this."
        )


class TestNoHardcodedCapabilityOverclaims:
    def test_subtitle_does_not_claim_order_book_layering(self, html_text):
        """
        Sentinel cannot detect order-book layering (requires order/account-level data).
        The subtitle must not claim it does.
        """
        # The phrase should not appear as a bare capability claim
        assert "Live detected order book layering" not in html_text, (
            "index.html subtitle claims 'Live detected order book layering' "
            "— Sentinel cannot detect this via broker API."
        )

    def test_subtitle_does_not_claim_circular_trade_loops(self, html_text):
        """Sentinel cannot detect circular trade loops (requires account-level data)."""
        assert "circular trade loops" not in html_text, (
            "index.html claims circular trade loop detection — not possible via broker API."
        )


class TestAutoRefreshPresent:
    def test_setinterval_present_for_scan_refresh(self, html_text):
        """Auto-refresh must be in place (setInterval for scan status)."""
        # At minimum one setInterval call for auto-refresh
        assert "setInterval" in html_text, "index.html must have auto-refresh via setInterval"

    def test_scan_runs_api_called(self, html_text):
        """Dashboard must fetch from /api/live/scan/runs (the real source of truth)."""
        assert "/api/live/scan/runs" in html_text, (
            "index.html must call /api/live/scan/runs to get real last-scan time."
        )

    def test_alerts_fetched_from_api(self, html_text):
        """Alerts must be fetched from /api/alerts (not hardcoded)."""
        assert "/api/alerts" in html_text, (
            "index.html must fetch alerts from /api/alerts"
        )


class TestSampleDataBadge:
    def test_is_sample_data_checked_in_js(self, html_text):
        """
        Every synthetic_demo alert row must render a SAMPLE DATA badge.
        This is enforced by checking the JS reads a.is_sample_data.
        """
        assert "is_sample_data" in html_text, (
            "index.html JS must check a.is_sample_data to render SAMPLE DATA badge "
            "on synthetic_demo rows."
        )

    def test_sample_data_badge_text_present(self, html_text):
        """The literal 'SAMPLE DATA' badge must be in the JS template."""
        assert "SAMPLE DATA" in html_text, (
            "index.html must render a 'SAMPLE DATA' badge for is_sample_data=true rows."
        )


class TestScanDurationFromAPI:
    def test_ticker_latency_reads_from_scan_runs(self, html_text):
        """
        Detection latency shown in the ticker must be computed from real
        scan_run.finished_at - started_at, not hardcoded.
        """
        # The JS should compute duration from started_at / finished_at
        assert "started_at" in html_text and "finished_at" in html_text, (
            "index.html must compute scan duration from real started_at/finished_at "
            "DB values — never hardcode '0.38 ms'."
        )

