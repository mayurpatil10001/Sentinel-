"""
Sentinel ingestion error hierarchy.

Every ingestion module raises a subclass of IngestError on failure.
HARD RULE: No module ever silently falls back to synthetic data — if a
real source fails, it raises one of these exceptions and lets the caller
decide how to handle it.
"""


class IngestError(Exception):
    """Base class for all ingestion errors."""


# ── NSE Bhavcopy ────────────────────────────────────────────────────────────

class BhavcopyFetchError(IngestError):
    """
    Raised when the HTTP request to NSE's bhavcopy archive fails.
    Includes the URL attempted and the HTTP status code (or None for
    network-level failures before a response is received).
    """

    def __init__(self, url: str, status_code: int | None, message: str = ""):
        self.url = url
        self.status_code = status_code
        super().__init__(
            f"Bhavcopy fetch failed for {url!r} "
            f"(HTTP {status_code}): {message}"
        )


class BhavcopyParseError(IngestError):
    """
    Raised when the downloaded bhavcopy ZIP/CSV cannot be parsed into the
    expected schema. Includes which columns were missing or malformed.
    """

    def __init__(self, missing_columns: list[str], message: str = ""):
        self.missing_columns = missing_columns
        super().__init__(
            f"Bhavcopy parse failed. Missing/unexpected columns: "
            f"{missing_columns}. {message}"
        )


# ── NSE Bulk / Block Deals ──────────────────────────────────────────────────

class BulkDealFetchError(IngestError):
    """
    Raised when the HTTP request to NSE's bulk/block deal CSV endpoint fails.
    """

    def __init__(self, url: str, status_code: int | None, message: str = ""):
        self.url = url
        self.status_code = status_code
        super().__init__(
            f"Bulk deal fetch failed for {url!r} "
            f"(HTTP {status_code}): {message}"
        )


class BulkDealParseError(IngestError):
    """
    Raised when the bulk/block deal CSV cannot be parsed into the expected schema.
    """

    def __init__(self, message: str = ""):
        super().__init__(f"Bulk deal parse failed: {message}")


# ── Broker Order Stream (Kite Connect) ──────────────────────────────────────

class BrokerAuthError(IngestError):
    """
    Raised when Kite Connect credentials are missing or invalid.
    The module does NOT fall back to synthetic data — this error is
    intentionally fatal so the caller knows they have no real data source.

    Required environment variables:
        KITE_API_KEY        — your Kite Connect app's API key
        KITE_ACCESS_TOKEN   — session access token (rotates daily)
    """

    def __init__(self, message: str = ""):
        super().__init__(
            f"Kite Connect authentication failed: {message}. "
            "Set KITE_API_KEY and KITE_ACCESS_TOKEN environment variables."
        )


class BrokerStreamError(IngestError):
    """
    Raised when the live Kite WebSocket stream fails after authentication.
    """

    def __init__(self, message: str = ""):
        super().__init__(f"Kite order stream error: {message}")


# ── NSE Option Chain ─────────────────────────────────────────────────────────

class OptionChainFetchError(IngestError):
    """
    Raised when the NSE option chain API request fails.
    Note: NSE's API requires a cookie-based browser session; this error
    fires if the session setup or the API call itself fails.
    """

    def __init__(self, url: str, status_code: int | None, message: str = ""):
        self.url = url
        self.status_code = status_code
        super().__init__(
            f"Option chain fetch failed for {url!r} "
            f"(HTTP {status_code}): {message}"
        )


class OptionChainParseError(IngestError):
    """
    Raised when the option chain JSON response cannot be parsed into the
    expected schema (e.g. NSE changed their response format).
    """

    def __init__(self, message: str = ""):
        super().__init__(f"Option chain parse failed: {message}")
