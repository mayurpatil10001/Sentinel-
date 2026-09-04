"""
Phase 6 Resilience Tests
========================

Tests for:
  - retry_with_backoff: retries on 5xx, fails immediately on 403/404,
    raises MaxRetriesExceededError after exhausting retries.
  - CircuitBreaker: state transitions CLOSED → OPEN → HALF_OPEN → CLOSED,
    CircuitBreakerOpenError while OPEN.
  - determine_fallback: correct next_action for each exception class.
  - get_safe_fallback_data: ALWAYS raises (synthetic data firewall test).
  - No production ingest code path leads to synthetic data on failure.

All tests are fully mocked — no real network calls.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from data.ingest.errors import (
    BhavcopyFetchError,
    BhavcopyParseError,
    BulkDealFetchError,
    BulkDealParseError,
    CircuitBreakerOpenError,
    MaxRetriesExceededError,
    OptionChainFetchError,
    OptionChainParseError,
)
from data.ingest.fallback_strategy import (
    IngestionStatus,
    determine_fallback,
    get_safe_fallback_data,
)
from data.ingest.resilience import (
    CircuitBreaker,
    _CircuitState,
    is_retryable_http_status,
    retry_with_backoff,
)


# ════════════════════════════════════════════════════════════════════════════
# Tests: is_retryable_http_status
# ════════════════════════════════════════════════════════════════════════════

class TestRetryableStatusClassification:

    def test_none_is_retryable(self):
        """Network-level failure (no response) is always retryable."""
        assert is_retryable_http_status(None) is True

    def test_503_is_retryable(self):
        assert is_retryable_http_status(503) is True

    def test_500_is_retryable(self):
        assert is_retryable_http_status(500) is True

    def test_429_is_retryable(self):
        assert is_retryable_http_status(429) is True

    def test_403_is_not_retryable(self):
        """403 is IP/bot block — retrying the same request won't fix it."""
        assert is_retryable_http_status(403) is False

    def test_404_is_not_retryable(self):
        """404 is non-trading day or archive not posted — retrying won't help."""
        assert is_retryable_http_status(404) is False

    def test_401_is_not_retryable(self):
        assert is_retryable_http_status(401) is False

    def test_400_is_not_retryable(self):
        assert is_retryable_http_status(400) is False

    def test_200_is_not_retryable(self):
        """200 is success — not in retryable set."""
        assert is_retryable_http_status(200) is False


# ════════════════════════════════════════════════════════════════════════════
# Tests: retry_with_backoff
# ════════════════════════════════════════════════════════════════════════════

class TestRetryWithBackoff:

    def test_succeeds_on_first_attempt(self):
        """No retry needed if function succeeds immediately."""
        call_count = 0

        @retry_with_backoff(max_retries=3, base_delay=0.0, retryable_exceptions=(BhavcopyFetchError,))
        def succeeds():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = succeeds()
        assert result == "ok"
        assert call_count == 1

    def test_retries_on_503(self):
        """Retryable 503 should be retried."""
        call_count = 0

        @retry_with_backoff(max_retries=2, base_delay=0.0, retryable_exceptions=(BhavcopyFetchError,))
        def fails_twice_then_succeeds():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise BhavcopyFetchError("http://x", 503, "server error")
            return "ok"

        result = fails_twice_then_succeeds()
        assert result == "ok"
        assert call_count == 3

    def test_does_not_retry_on_403(self):
        """403 is NON-RETRYABLE: must fail immediately on first attempt."""
        call_count = 0

        @retry_with_backoff(max_retries=3, base_delay=0.0, retryable_exceptions=(BhavcopyFetchError,))
        def always_403():
            nonlocal call_count
            call_count += 1
            raise BhavcopyFetchError("http://x", 403, "forbidden")

        with pytest.raises(BhavcopyFetchError) as exc_info:
            always_403()

        assert exc_info.value.status_code == 403
        assert call_count == 1, f"Should only attempt once for 403, but attempted {call_count} times"

    def test_does_not_retry_on_404(self):
        """404 is NON-RETRYABLE: must fail immediately on first attempt."""
        call_count = 0

        @retry_with_backoff(max_retries=3, base_delay=0.0, retryable_exceptions=(BhavcopyFetchError,))
        def always_404():
            nonlocal call_count
            call_count += 1
            raise BhavcopyFetchError("http://x", 404, "not found")

        with pytest.raises(BhavcopyFetchError) as exc_info:
            always_404()

        assert exc_info.value.status_code == 404
        assert call_count == 1

    def test_raises_max_retries_exceeded_after_exhaustion(self):
        """After all retries fail, MaxRetriesExceededError should be raised."""
        @retry_with_backoff(max_retries=2, base_delay=0.0, retryable_exceptions=(BhavcopyFetchError,))
        def always_503():
            raise BhavcopyFetchError("http://x", 503, "server error")

        with pytest.raises(MaxRetriesExceededError) as exc_info:
            always_503()

        assert exc_info.value.attempts == 3  # 1 initial + 2 retries

    def test_total_attempts_is_max_retries_plus_one(self):
        """max_retries=2 means 3 total attempts (1 initial + 2 retries)."""
        call_count = 0

        @retry_with_backoff(max_retries=2, base_delay=0.0, retryable_exceptions=(BhavcopyFetchError,))
        def always_fails():
            nonlocal call_count
            call_count += 1
            raise BhavcopyFetchError("http://x", 503, "server error")

        with pytest.raises(MaxRetriesExceededError):
            always_fails()

        assert call_count == 3

    def test_non_retryable_exception_type_propagates_immediately(self):
        """Exceptions not in retryable_exceptions should propagate immediately."""
        call_count = 0

        @retry_with_backoff(
            max_retries=3,
            base_delay=0.0,
            retryable_exceptions=(BhavcopyFetchError,),
            non_retryable_exceptions=(BhavcopyParseError,),
        )
        def parse_error():
            nonlocal call_count
            call_count += 1
            raise BhavcopyParseError([], "bad format")

        with pytest.raises(BhavcopyParseError):
            parse_error()

        assert call_count == 1

    def test_network_error_no_status_is_retryable(self):
        """Exceptions with status_code=None (network failure) are retried."""
        call_count = 0

        @retry_with_backoff(max_retries=1, base_delay=0.0, retryable_exceptions=(BhavcopyFetchError,))
        def network_error():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise BhavcopyFetchError("http://x", None, "connection reset")
            return "ok"

        result = network_error()
        assert result == "ok"
        assert call_count == 2


# ════════════════════════════════════════════════════════════════════════════
# Tests: CircuitBreaker
# ════════════════════════════════════════════════════════════════════════════

class TestCircuitBreaker:

    def _make_cb(self, threshold=3, cooldown=60.0) -> CircuitBreaker:
        return CircuitBreaker("test_source", failure_threshold=threshold, cooldown_seconds=cooldown)

    def test_starts_closed(self):
        cb = self._make_cb()
        assert cb.state == "CLOSED"

    def test_before_request_passes_when_closed(self):
        cb = self._make_cb()
        cb.before_request()  # Should not raise

    def test_transitions_to_open_after_threshold(self):
        cb = self._make_cb(threshold=3)
        for _ in range(3):
            cb.on_failure()
        assert cb.state == "OPEN"

    def test_stays_closed_below_threshold(self):
        cb = self._make_cb(threshold=3)
        cb.on_failure()
        cb.on_failure()
        assert cb.state == "CLOSED"

    def test_raises_circuit_breaker_open_when_open(self):
        cb = self._make_cb(threshold=2, cooldown=999.0)
        cb.on_failure()
        cb.on_failure()
        assert cb.state == "OPEN"
        with pytest.raises(CircuitBreakerOpenError) as exc_info:
            cb.before_request()
        assert exc_info.value.source == "test_source"
        assert exc_info.value.cooldown_remaining > 0

    def test_transitions_to_half_open_after_cooldown(self):
        cb = self._make_cb(threshold=1, cooldown=0.01)
        cb.on_failure()
        assert cb.state == "OPEN"
        time.sleep(0.05)  # wait out the cooldown
        cb.before_request()  # should NOT raise — transitions to HALF_OPEN
        assert cb.state == "HALF_OPEN"

    def test_transitions_closed_after_probe_success(self):
        cb = self._make_cb(threshold=1, cooldown=0.01)
        cb.on_failure()
        time.sleep(0.05)
        cb.before_request()   # → HALF_OPEN
        cb.on_success()       # → CLOSED
        assert cb.state == "CLOSED"
        assert cb.consecutive_failures == 0

    def test_returns_to_open_after_probe_failure(self):
        cb = self._make_cb(threshold=1, cooldown=0.01)
        cb.on_failure()
        time.sleep(0.05)
        cb.before_request()   # → HALF_OPEN
        cb.on_failure()       # probe failed → back to OPEN
        assert cb.state == "OPEN"

    def test_on_success_resets_failure_counter(self):
        cb = self._make_cb(threshold=5)
        cb.on_failure()
        cb.on_failure()
        cb.on_success()
        assert cb.consecutive_failures == 0
        assert cb.state == "CLOSED"

    def test_reset_clears_everything(self):
        cb = self._make_cb(threshold=1)
        cb.on_failure()
        assert cb.state == "OPEN"
        cb.reset()
        assert cb.state == "CLOSED"
        assert cb.consecutive_failures == 0


# ════════════════════════════════════════════════════════════════════════════
# Tests: determine_fallback
# ════════════════════════════════════════════════════════════════════════════

class TestDetermineFallback:

    def test_circuit_open_returns_retry_after_cooldown(self):
        exc = CircuitBreakerOpenError("bhavcopy", 120.0)
        status = determine_fallback("bhavcopy", exc)
        assert status.next_action == "RETRY_AFTER_COOLDOWN"
        assert status.retry_after_seconds == 121  # int(120.0) + 1
        assert isinstance(status, IngestionStatus)

    def test_max_retries_exceeded_alerts_operator(self):
        exc = MaxRetriesExceededError("bhavcopy", 3, "503 server error")
        status = determine_fallback("bhavcopy", exc)
        assert status.next_action == "ALERT_OPERATOR"
        assert status.is_operator_alert_needed() is True

    def test_bhavcopy_parse_error_escalates(self):
        exc = BhavcopyParseError(["CLOSE", "OPEN"], "schema changed")
        status = determine_fallback("bhavcopy", exc)
        assert status.next_action == "ESCALATE"
        assert status.is_developer_escalation_needed() is True

    def test_bulk_deal_parse_error_escalates(self):
        exc = BulkDealParseError("column missing")
        status = determine_fallback("bulk_deals", exc)
        assert status.next_action == "ESCALATE"

    def test_option_chain_parse_error_escalates(self):
        exc = OptionChainParseError("key 'records' missing")
        status = determine_fallback("option_chain", exc)
        assert status.next_action == "ESCALATE"

    def test_403_alerts_operator(self):
        """403 means IP block — human must act. No automatic retry possible."""
        exc = BhavcopyFetchError("http://x", 403, "forbidden")
        status = determine_fallback("bhavcopy", exc)
        assert status.next_action == "ALERT_OPERATOR"
        assert status.retry_after_seconds is None

    def test_404_skips_today(self):
        """Non-trading day or archive not yet posted — skip without action."""
        exc = BhavcopyFetchError("http://x", 404, "not found")
        status = determine_fallback("bhavcopy", exc)
        assert status.next_action == "SKIP_TODAY"

    def test_503_retries_after_cooldown(self):
        exc = BhavcopyFetchError("http://x", 503, "server error")
        status = determine_fallback("bhavcopy", exc)
        assert status.next_action == "RETRY_AFTER_COOLDOWN"
        assert status.retry_after_seconds == 300

    def test_429_retries_with_longer_cooldown(self):
        """Rate limited — back off more aggressively."""
        exc = BulkDealFetchError("http://x", 429, "rate limited")
        status = determine_fallback("bulk_deals", exc)
        assert status.next_action == "RETRY_AFTER_COOLDOWN"
        assert status.retry_after_seconds == 600

    def test_option_chain_no_status_skips_today(self):
        """Option chain outside market hours — skip, not an error."""
        exc = OptionChainFetchError("http://x", None, "connection refused")
        status = determine_fallback("option_chain", exc)
        assert status.next_action == "SKIP_TODAY"

    def test_network_level_failure_retries(self):
        """No HTTP response (timeout, connection reset) — retry after 60s."""
        exc = BhavcopyFetchError("http://x", None, "connection reset")
        status = determine_fallback("bhavcopy", exc)
        assert status.next_action == "RETRY_AFTER_COOLDOWN"
        assert status.retry_after_seconds == 60

    def test_never_raises_for_unknown_exception(self):
        """determine_fallback must never raise — unknown types default to ALERT_OPERATOR."""
        status = determine_fallback("unknown_source", ValueError("unexpected"))
        assert status.next_action == "ALERT_OPERATOR"
        assert isinstance(status, IngestionStatus)

    def test_status_has_error_class_populated(self):
        exc = BhavcopyFetchError("http://x", 403, "forbidden")
        status = determine_fallback("bhavcopy", exc)
        assert status.error_class == "BhavcopyFetchError"
        assert "403" in status.error_detail or "forbidden" in status.error_detail.lower()

    def test_is_retriable_helper(self):
        exc = BhavcopyFetchError("http://x", 503, "server error")
        status = determine_fallback("bhavcopy", exc)
        assert status.is_retriable() is True

    def test_403_is_not_retriable(self):
        exc = BhavcopyFetchError("http://x", 403, "forbidden")
        status = determine_fallback("bhavcopy", exc)
        assert status.is_retriable() is False


# ════════════════════════════════════════════════════════════════════════════
# Tests: get_safe_fallback_data — SYNTHETIC DATA FIREWALL
# ════════════════════════════════════════════════════════════════════════════

class TestSyntheticDataFirewall:

    def test_get_safe_fallback_data_always_raises(self):
        """
        CRITICAL: get_safe_fallback_data must ALWAYS raise RuntimeError.
        If this test fails, a production ingest failure path is returning
        synthetic data — which is explicitly prohibited.
        """
        with pytest.raises(RuntimeError) as exc_info:
            get_safe_fallback_data("bhavcopy")
        assert "SYNTHETIC DATA FIREWALL" in str(exc_info.value)

    def test_firewall_raises_for_any_source(self):
        """The firewall applies to every source name, not just 'bhavcopy'."""
        for source in ("bulk_deals", "option_chain", "broker_stream", "anything"):
            with pytest.raises(RuntimeError):
                get_safe_fallback_data(source)

    def test_production_ingest_modules_do_not_import_demo(self):
        """
        Verify that production ingest modules do not import from demo/.
        The synthetic data generator (demo/generate_synthetic_orderflow.py)
        must never be imported from production code paths.

        This test checks the import graph at the module attribute level
        (not running the actual imports) to confirm no ingest module has
        demo as a dependency.
        """
        import importlib
        import sys

        ingest_modules = [
            "data.ingest.nse_bhavcopy",
            "data.ingest.nse_bulk_deals",
            "data.ingest.nse_option_chain",
            "data.ingest.resilience",
            "data.ingest.fallback_strategy",
        ]

        for mod_name in ingest_modules:
            mod = importlib.import_module(mod_name)
            # Check no attribute of the module references a demo module
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name, None)
                if hasattr(attr, "__module__") and attr.__module__:
                    assert "demo" not in attr.__module__, (
                        f"{mod_name}.{attr_name} references demo module "
                        f"'{attr.__module__}' — synthetic data must not be "
                        f"reachable from production ingest code."
                    )
