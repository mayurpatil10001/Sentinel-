"""
Ingestion Fallback Strategy
============================

Defines what the system does when live NSE/broker data access fails.

This is actual code — not a comment — because production ingestion failures
must trigger specific, predictable system behaviours. The ``determine_fallback``
function is the single authoritative place that classifies an exception and
returns the recommended next action.

SYNTHETIC DATA FIREWALL
------------------------
``get_safe_fallback_data`` always raises RuntimeError. It exists specifically
so any code path that reaches "return synthetic data" is immediately visible
as a test failure. No production ingestion path should ever call it.

Synthetic data lives ONLY in ``demo/generate_synthetic_orderflow.py``.
It must never be imported by, or reached from, a production ingestion path.

DESIGN NOTE
------------
``determine_fallback`` never raises — it always returns a valid ``IngestionStatus``
even for exception types it has not seen before (safe default: ALERT_OPERATOR).
This means callers can always log or dispatch on the returned status even when
unexpected exceptions occur.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from data.ingest.errors import (
    BhavcopyFetchError,
    BhavcopyParseError,
    BulkDealFetchError,
    BulkDealParseError,
    CircuitBreakerOpenError,
    IngestError,
    MaxRetriesExceededError,
    OptionChainFetchError,
    OptionChainParseError,
)


NextAction = Literal[
    "RETRY_AFTER_COOLDOWN",  # Transient failure — retry after cooldown period
    "ALERT_OPERATOR",         # Human action required (403 block, IP change, data agreement)
    "SKIP_TODAY",             # Non-fatal skip (non-trading day, market closed, 404)
    "ESCALATE",               # Developer action required (NSE changed schema, parse error)
]


@dataclass(frozen=True)
class IngestionStatus:
    """
    The result of classify-and-dispatch on a failed ingest exception.

    Fields
    ------
    source
        Identifier for the data source that failed.
    success
        Always False (this is a failure status).
    error_class
        The Python exception class name (e.g. "BhavcopyFetchError").
    error_detail
        str(exc) — the full error message.
    next_action
        What the system should do next.
    retry_after_seconds
        How long to wait before retrying. Only populated for
        RETRY_AFTER_COOLDOWN actions. None for all others.
    """
    source: str
    success: bool
    error_class: str
    error_detail: str
    next_action: NextAction
    retry_after_seconds: int | None

    def is_operator_alert_needed(self) -> bool:
        """True if a human operator must act before retry is meaningful."""
        return self.next_action == "ALERT_OPERATOR"

    def is_developer_escalation_needed(self) -> bool:
        """True if a code/schema change is required (NSE changed their format)."""
        return self.next_action == "ESCALATE"

    def is_retriable(self) -> bool:
        """True if the system should retry automatically after a delay."""
        return self.next_action == "RETRY_AFTER_COOLDOWN"


def determine_fallback(source: str, exc: Exception) -> IngestionStatus:
    """
    Given an ingestion exception, return the appropriate ``IngestionStatus``
    with the recommended ``next_action``.

    This function NEVER raises. Unknown exception types default to
    ALERT_OPERATOR (conservative safe default).

    Decision tree
    -------------

    CircuitBreakerOpenError
        → RETRY_AFTER_COOLDOWN
          Circuit is managing its own cooldown. Caller should wait
          for cooldown_remaining seconds before retrying.

    MaxRetriesExceededError
        → ALERT_OPERATOR
          Transient failure persisted beyond retry window. Operator must
          investigate (check NSE status page, network connectivity, etc.).

    BhavcopyParseError / BulkDealParseError / OptionChainParseError
        → ESCALATE
          NSE changed their file format. Retrying will get the same malformed
          data. A developer must update the parser schema.

    HTTP 403 (any IngestError with status_code=403)
        → ALERT_OPERATOR
          IP reputation block or bot detection from NSE. Retrying from the
          same IP will not succeed. Human must change IP, use a data
          agreement, or use browser automation. See NSE_ACCESS_LIMITATIONS.md.

    HTTP 404 (any IngestError with status_code=404)
        → SKIP_TODAY
          NSE archive not yet posted (too early in the day) or this is a
          non-trading day. Not an error requiring action.

    HTTP 5xx (any IngestError with 500 <= status_code < 600)
        → RETRY_AFTER_COOLDOWN (300s)
          Server-side issue on NSE's end. Worth retrying after a delay.

    HTTP 429 (rate limited)
        → RETRY_AFTER_COOLDOWN (600s — back off more aggressively)
          NSE's rate limit hit. A longer delay is warranted before retrying.

    OptionChainFetchError (no status code or status outside above ranges)
        → SKIP_TODAY
          Option chain is only available during market hours (09:15–15:30 IST).
          Failures outside these hours are expected and non-actionable.

    BhavcopyFetchError / BulkDealFetchError (no status code)
        → RETRY_AFTER_COOLDOWN (60s)
          Network-level failure (timeout, connection reset). Worth retrying.

    Any other IngestError or unknown exception
        → ALERT_OPERATOR (safe conservative default)

    Parameters
    ----------
    source
        Identifier for the failing data source, e.g. "bhavcopy",
        "bulk_deals", "option_chain".
    exc
        The exception raised by the ingest module.

    Returns
    -------
    IngestionStatus
        Always returns — never raises.
    """
    error_class = type(exc).__name__
    error_detail = str(exc)

    # ── CircuitBreakerOpenError ──────────────────────────────────────────────
    if isinstance(exc, CircuitBreakerOpenError):
        return IngestionStatus(
            source=source,
            success=False,
            error_class=error_class,
            error_detail=error_detail,
            next_action="RETRY_AFTER_COOLDOWN",
            retry_after_seconds=int(exc.cooldown_remaining) + 1,
        )

    # ── MaxRetriesExceededError ──────────────────────────────────────────────
    if isinstance(exc, MaxRetriesExceededError):
        return IngestionStatus(
            source=source,
            success=False,
            error_class=error_class,
            error_detail=error_detail,
            next_action="ALERT_OPERATOR",
            retry_after_seconds=None,
        )

    # ── Parse errors → schema change, developer must fix ────────────────────
    if isinstance(exc, (BhavcopyParseError, BulkDealParseError, OptionChainParseError)):
        return IngestionStatus(
            source=source,
            success=False,
            error_class=error_class,
            error_detail=error_detail,
            next_action="ESCALATE",
            retry_after_seconds=None,
        )

    # ── HTTP status code based routing ───────────────────────────────────────
    status_code: int | None = getattr(exc, "status_code", None)

    if status_code == 403:
        # IP block / bot detection. Same request → same 403. Human must act.
        return IngestionStatus(
            source=source,
            success=False,
            error_class=error_class,
            error_detail=error_detail,
            next_action="ALERT_OPERATOR",
            retry_after_seconds=None,
        )

    if status_code == 404:
        # Non-trading day or archive not yet posted — skip, not an error
        return IngestionStatus(
            source=source,
            success=False,
            error_class=error_class,
            error_detail=error_detail,
            next_action="SKIP_TODAY",
            retry_after_seconds=None,
        )

    if status_code == 429:
        # Rate limited — back off more aggressively than generic 5xx
        return IngestionStatus(
            source=source,
            success=False,
            error_class=error_class,
            error_detail=error_detail,
            next_action="RETRY_AFTER_COOLDOWN",
            retry_after_seconds=600,  # 10-minute back-off for rate limits
        )

    if status_code is not None and 500 <= status_code < 600:
        # Server-side issue on NSE's infrastructure — worth retrying
        return IngestionStatus(
            source=source,
            success=False,
            error_class=error_class,
            error_detail=error_detail,
            next_action="RETRY_AFTER_COOLDOWN",
            retry_after_seconds=300,
        )

    # ── OptionChainFetchError (no status or unmatched status) ────────────────
    # Option chain only works during market hours. Failures outside hours expected.
    if isinstance(exc, OptionChainFetchError):
        return IngestionStatus(
            source=source,
            success=False,
            error_class=error_class,
            error_detail=error_detail,
            next_action="SKIP_TODAY",
            retry_after_seconds=None,
        )

    # ── Network-level failures (no status code at all) ───────────────────────
    if isinstance(exc, (BhavcopyFetchError, BulkDealFetchError)) and status_code is None:
        return IngestionStatus(
            source=source,
            success=False,
            error_class=error_class,
            error_detail=error_detail,
            next_action="RETRY_AFTER_COOLDOWN",
            retry_after_seconds=60,
        )

    # ── Safe default for anything unclassified ───────────────────────────────
    return IngestionStatus(
        source=source,
        success=False,
        error_class=error_class,
        error_detail=error_detail,
        next_action="ALERT_OPERATOR",
        retry_after_seconds=None,
    )


def get_safe_fallback_data(source: str) -> None:
    """
    SYNTHETIC DATA FIREWALL — this function ALWAYS RAISES.

    Any code path that reaches this function is attempting to use synthetic
    data as a production fallback for a live ingest failure. This is explicitly
    prohibited.

    If you are reading this because a test proved this path is reachable from
    production ingestion code, the test is working as designed. Remove the
    synthetic fallback from the production code path.

    Synthetic data is available ONLY from:
        demo/generate_synthetic_orderflow.py

    That module must never be imported by any module in data/ingest/ or app/.

    Production ingest failures must be handled via:
        determine_fallback(source, exc)  →  IngestionStatus
    and the IngestionStatus.next_action acted upon appropriately.

    See also: docs/NSE_ACCESS_LIMITATIONS.md for the correct paths forward
    when live NSE access is unavailable.
    """
    raise RuntimeError(
        f"[SYNTHETIC DATA FIREWALL] get_safe_fallback_data() called for "
        f"source '{source}'. This function never returns data. "
        f"Synthetic data is not a valid production fallback for ingest failures. "
        f"Handle the failure via determine_fallback() and alert the operator. "
        f"See docs/NSE_ACCESS_LIMITATIONS.md for options."
    )
