"""
Retry and Circuit Breaker Infrastructure
==========================================

Provides two complementary resilience mechanisms for NSE ingestion:

  retry_with_backoff  — decorator that retries transient failures using
                        exponential backoff with full jitter, and that
                        clearly distinguishes RETRYABLE from NON-RETRYABLE
                        failures rather than retrying everything blindly.

  CircuitBreaker      — state machine that prevents hammering a repeatedly
                        failing source by transitioning to a fail-fast OPEN
                        state after N consecutive failures, then allowing
                        one probe after a cooldown period.

WHY 403 IS NON-RETRYABLE
--------------------------
NSE's 403 Forbidden from a datacenter IP is not a transient error. It is a
deliberate rejection by NSE's bot-protection layer based on IP reputation or
TLS fingerprinting. The server understood the request and chose to refuse it.
Retrying the identical request from the same IP will get the same 403.

Retrying a 403 endlessly might look like "the system is trying hard," but it
is actually wasting time and could accelerate further blocking. 403 is marked
NON_RETRYABLE and the caller is immediately told to seek a human operator action
(see fallback_strategy.py). Only a fundamental change — different IP, official
data agreement, or browser automation — will resolve a 403.

JITTER STRATEGY
----------------
Full jitter (AWS architecture blog, Marc Brooker, 2015):
    sleep = random.uniform(0, min(max_delay, base_delay * 2^attempt))

This prevents synchronized retries from multiple concurrent callers all
waking at the same time after a shared source recovers, which would
recreate the thundering-herd problem the backoff was meant to prevent.

CIRCUIT BREAKER PATTERN
------------------------
Pattern: Nygard, "Release It!" (Pragmatic Programmers, 2007), Ch. 5.

State machine:
    CLOSED   → Normal operation. Failures are counted.
    OPEN     → Fail-fast. No requests sent. Cooldown timer running.
    HALF_OPEN → One probe request allowed. Success → CLOSED. Failure → OPEN.

Thread safety note: this implementation uses simple Python integers and
monotonic timestamps. For a single-process Python server (GIL present),
reads/writes to int and float attributes are effectively atomic for our
purposes. For multi-process deployments (gunicorn workers, Celery), use
shared state (Redis, PostgreSQL advisory locks) for the circuit breaker.
"""

import functools
import logging
import random
import time
from enum import Enum, auto
from typing import Callable, Type

from data.ingest.errors import (
    CircuitBreakerOpenError,
    IngestError,
    MaxRetriesExceededError,
)

logger = logging.getLogger(__name__)


# ── Failure classification ────────────────────────────────────────────────────

# HTTP status codes that are RETRYABLE (transient server-side / infrastructure issues).
_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# HTTP status codes that are NON-RETRYABLE.
# The server understood the request and deliberately responded with these.
# Retrying the same request from the same client will not change the outcome
# without a fundamental change in the request or the environment.
_NON_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({
    400,  # Bad request — fix the request
    401,  # Unauthorised — fix credentials
    403,  # Forbidden — IP block / bot detection / data agreement required
    404,  # Not found — wrong URL or non-trading day
    410,  # Gone — resource permanently removed
    422,  # Unprocessable entity — schema mismatch
})


def is_retryable_http_status(status_code: int | None) -> bool:
    """
    Returns True if the HTTP status code indicates a transient, RETRYABLE failure.

    None (no response received) means a network-level failure (timeout,
    connection reset) — these are always RETRYABLE because the server may
    have been temporarily unavailable.

    403 and 404 are explicitly NON-RETRYABLE for NSE:
      - 403: IP reputation block or bot detection. Same request → same 403.
      - 404: Non-trading day or archive not yet posted. Retrying won't post it.
    """
    if status_code is None:
        return True  # Network-level failure — worth retrying
    return status_code in _RETRYABLE_STATUS_CODES


# ── Retry decorator ───────────────────────────────────────────────────────────

def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    retryable_exceptions: tuple[Type[Exception], ...] = (IngestError,),
    non_retryable_exceptions: tuple[Type[Exception], ...] = (),
) -> Callable:
    """
    Decorator: retry a function on transient failures with exponential
    backoff and full jitter.

    Parameters
    ----------
    max_retries
        Maximum retries AFTER the first attempt. Total attempts = max_retries + 1.
    base_delay
        Initial delay in seconds (doubles per retry).
    max_delay
        Hard cap on per-retry delay.
    retryable_exceptions
        Exception types that MAY be retried (subject to HTTP status check).
        Exceptions not in this list propagate immediately.
    non_retryable_exceptions
        Exception types that are NEVER retried regardless of status code.
        Takes precedence over retryable_exceptions.

    Raises
    ------
    MaxRetriesExceededError
        After all retryable attempts are exhausted.
    Original exception (immediately)
        If the exception has a NON_RETRYABLE HTTP status code, or matches
        non_retryable_exceptions.

    Notes
    -----
    The ``status_code`` attribute on IngestError subclasses is used to
    classify HTTP failures. Exceptions without a status_code attribute
    are treated as network-level failures (RETRYABLE).
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            source_name = func.__qualname__
            last_exc: Exception | None = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)

                except non_retryable_exceptions as exc:
                    # Explicitly declared non-retryable type — fail immediately
                    logger.warning(
                        "[retry:%s] Non-retryable exception type on attempt %d/%d: %s",
                        source_name, attempt + 1, max_retries + 1, exc
                    )
                    raise

                except retryable_exceptions as exc:
                    last_exc = exc

                    # Check HTTP status code to determine retryability
                    status_code: int | None = getattr(exc, "status_code", None)
                    if not is_retryable_http_status(status_code):
                        logger.warning(
                            "[retry:%s] HTTP %s on attempt %d/%d — "
                            "NON-RETRYABLE (not retrying). Error: %s",
                            source_name, status_code,
                            attempt + 1, max_retries + 1, exc
                        )
                        raise  # Re-raise the original, untouched exception

                    if attempt == max_retries:
                        # Final attempt exhausted — will raise below the loop
                        break

                    # Compute full-jitter backoff delay
                    cap = min(max_delay, base_delay * (2.0 ** attempt))
                    delay = random.uniform(0.0, cap)
                    logger.warning(
                        "[retry:%s] RETRYABLE failure on attempt %d/%d "
                        "(status=%s). Retrying in %.2fs. Error: %s",
                        source_name, attempt + 1, max_retries + 1,
                        status_code, delay, exc
                    )
                    time.sleep(delay)

            raise MaxRetriesExceededError(
                source=source_name,
                attempts=max_retries + 1,
                last_error=str(last_exc),
            )

        return wrapper
    return decorator


# ── Circuit Breaker ───────────────────────────────────────────────────────────

class _CircuitState(Enum):
    CLOSED = auto()     # Normal operation — requests pass through
    OPEN = auto()       # Fail-fast — cooldown running, no requests
    HALF_OPEN = auto()  # Cooldown expired — one probe request allowed


class CircuitBreaker:
    """
    Circuit breaker: prevents hammering a repeatedly failing source.

    Usage pattern
    -------------
    Call ``before_request()`` immediately before the network call and
    ``on_success()`` / ``on_failure()`` based on the outcome.
    Only call ``on_failure()`` for RETRYABLE failures — not for 403/404,
    which are the source working correctly (refusing the request on purpose).

        cb = CircuitBreaker("bhavcopy", failure_threshold=5, cooldown_seconds=300)

        cb.before_request()          # raises CircuitBreakerOpenError if OPEN
        try:
            result = _fetch(url)
            cb.on_success()
        except RetryableIngestError:
            cb.on_failure()
            raise

    State transitions
    -----------------
    CLOSED  → OPEN      : consecutive_failures >= failure_threshold
    OPEN    → HALF_OPEN : cooldown_seconds elapsed
    HALF_OPEN → CLOSED  : probe request succeeds
    HALF_OPEN → OPEN    : probe request fails (reset cooldown)

    Parameters
    ----------
    source_name
        Human-readable identifier for logging (e.g. "bhavcopy", "option_chain").
    failure_threshold
        Number of consecutive RETRYABLE failures before opening the circuit.
        HEURISTIC: 5. Adjust based on source reliability observation.
    cooldown_seconds
        How long to stay in OPEN state before allowing a probe.
        HEURISTIC: 300s (5 minutes) for archive sources.
        Shorter (180s) for option_chain which is time-sensitive to market hours.
    """

    def __init__(
        self,
        source_name: str,
        failure_threshold: int = 5,
        cooldown_seconds: float = 300.0,
    ) -> None:
        self.source_name = source_name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds

        self._state: _CircuitState = _CircuitState.CLOSED
        self._consecutive_failures: int = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        """Current state name: 'CLOSED', 'OPEN', or 'HALF_OPEN'."""
        return self._state.name

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def _cooldown_remaining(self) -> float:
        if self._opened_at is None:
            return 0.0
        elapsed = time.monotonic() - self._opened_at
        return max(0.0, self.cooldown_seconds - elapsed)

    def before_request(self) -> None:
        """
        Call immediately before making a network request.

        Raises ``CircuitBreakerOpenError`` if the circuit is OPEN and the
        cooldown has not yet expired.

        If the cooldown has expired, transitions from OPEN to HALF_OPEN
        and allows the probe request through (no raise).
        """
        if self._state == _CircuitState.CLOSED:
            return  # Normal path — no action needed

        if self._state == _CircuitState.OPEN:
            remaining = self._cooldown_remaining()
            if remaining > 0:
                raise CircuitBreakerOpenError(
                    source=self.source_name,
                    cooldown_remaining=remaining,
                )
            # Cooldown expired — transition to HALF_OPEN for one probe
            logger.info(
                "[CircuitBreaker:%s] Cooldown expired. Allowing probe request (HALF_OPEN).",
                self.source_name,
            )
            self._state = _CircuitState.HALF_OPEN

        # HALF_OPEN: allow the probe through

    def on_success(self) -> None:
        """
        Record a successful request. Resets failure counter and closes
        the circuit if it was HALF_OPEN or OPEN.
        """
        if self._state != _CircuitState.CLOSED:
            logger.info(
                "[CircuitBreaker:%s] Success. Resetting to CLOSED (was %s).",
                self.source_name, self._state.name,
            )
        self._state = _CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = None

    def on_failure(self) -> None:
        """
        Record a RETRYABLE failure. Do NOT call this for 403/404 — those are
        the source working correctly (deliberately refusing). Only call for
        transient failures (5xx, timeouts, connection resets).

        If in HALF_OPEN: resets cooldown and returns to OPEN.
        If in CLOSED: increments failure counter; if threshold reached, opens circuit.
        """
        self._consecutive_failures += 1

        if self._state == _CircuitState.HALF_OPEN:
            logger.warning(
                "[CircuitBreaker:%s] Probe FAILED. Resetting cooldown, returning to OPEN.",
                self.source_name,
            )
            self._opened_at = time.monotonic()
            self._state = _CircuitState.OPEN

        elif self._consecutive_failures >= self.failure_threshold:
            logger.error(
                "[CircuitBreaker:%s] %d consecutive failures — opening circuit. "
                "Will refuse requests for %.0fs.",
                self.source_name, self._consecutive_failures, self.cooldown_seconds,
            )
            self._opened_at = time.monotonic()
            self._state = _CircuitState.OPEN

    def reset(self) -> None:
        """Force-reset to CLOSED state. Use for testing or manual operator recovery."""
        self._state = _CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = None


# ── Module-level circuit breaker singletons ──────────────────────────────────
# One per NSE data source. Imported by ingest modules:
#   from data.ingest.resilience import bhavcopy_circuit, ...
#
# These are module-level so they persist across multiple calls within the
# same Python process — exactly the right scope for circuit breaker state.

bhavcopy_circuit = CircuitBreaker(
    source_name="bhavcopy",
    failure_threshold=5,    # HEURISTIC: open after 5 consecutive retryable failures
    cooldown_seconds=300,   # HEURISTIC: 5-minute cooldown for archive server issues
)

bulk_deals_circuit = CircuitBreaker(
    source_name="bulk_deals",
    failure_threshold=5,
    cooldown_seconds=300,
)

option_chain_circuit = CircuitBreaker(
    source_name="option_chain",
    failure_threshold=5,
    cooldown_seconds=180,   # HEURISTIC: shorter cooldown — option chain is market-hours bound
)
