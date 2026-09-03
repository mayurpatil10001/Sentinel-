"""
Phase 1 Ingestion Tests
=======================

Tests for all four data/ingest modules.

HARD RULE #4 compliance: every module has tests proving BOTH
  (a) a true positive — the happy path actually parses real-shaped data, AND
  (b) a true negative — errors are raised (not swallowed) on bad inputs.

No real network calls are made in these tests — all HTTP is mocked via the
`responses` library. Real-network tests are gated behind @pytest.mark.live
(excluded from CI via pytest.ini addopts).

Run all unit tests (no network):
    pytest tests/test_ingest_phase1.py -v

Run live integration tests (requires network + optionally Kite credentials):
    pytest tests/test_ingest_phase1.py -v -m live
"""

import io
import os
import zipfile
from datetime import date, datetime

import pandas as pd
import pytest
import responses as responses_lib

from data.ingest.errors import (
    BhavcopyFetchError,
    BhavcopyParseError,
    BrokerAuthError,
    BulkDealFetchError,
    BulkDealParseError,
    OptionChainFetchError,
    OptionChainParseError,
)
from data.ingest.nse_bhavcopy import (
    _bhavcopy_url,
    _delivery_url,
    fetch_bhavcopy,
)
from data.ingest.nse_bulk_deals import fetch_block_deals, fetch_bulk_deals
from data.ingest.nse_option_chain import (
    _parse_option_chain_response,
    fetch_option_chain,
)


# ────────────────────────────────────────────────────────────────────────────
# Shared fixtures and helpers
# ────────────────────────────────────────────────────────────────────────────

_NSE_HOME_URL   = "https://www.nseindia.com/"
_BULK_URL       = "https://archives.nseindia.com/content/equities/bulk.csv"
_BLOCK_URL      = "https://archives.nseindia.com/content/equities/block_deal.csv"


def _make_bhavcopy_zip(symbol: str = "RELIANCE") -> bytes:
    """Builds a minimal valid bhavcopy CSV inside a ZIP, matching NSE's format."""
    csv_content = (
        "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,"
        "TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN\r\n"
        f"{symbol},EQ,2800.00,2850.00,2790.00,2830.00,2830.00,2795.00,"
        "1234567,3489765432.00,01-JAN-2024,12345,INE002A01018\r\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("cm01JAN2024bhav.csv", csv_content)
    return buf.getvalue()


def _make_bulk_deal_csv() -> str:
    """Builds a minimal valid bulk deal CSV matching NSE's format."""
    return (
        "Date,Symbol,Security Name,Client Name,Buy / Sell,"
        "Quantity Traded,Trade Price / Wght. Avg. Price\r\n"
        "01-Jan-2024,RELIANCE,Reliance Industries Ltd,Some Fund,BUY,"
        "500000,2830.50\r\n"
        "01-Jan-2024,TCS,Tata Consultancy Services Ltd,Another Fund,SELL,"
        "200000,3600.00\r\n"
    )


def _make_option_chain_response(symbol: str = "NIFTY") -> dict:
    """Builds a minimal valid NSE option chain JSON response."""
    return {
        "records": {
            "expiryDates": ["18-Jan-2024"],
            "underlyingValue": 21500.0,
            "data": [
                {
                    "strikePrice": 21500,
                    "expiryDate": "18-Jan-2024",
                    "CE": {
                        "openInterest": 100000,
                        "changeinOpenInterest": 5000,
                        "impliedVolatility": 12.5,
                        "totalTradedVolume": 50000,
                        "lastPrice": 180.5,
                        "bidprice": 179.0,
                        "askPrice": 181.0,
                        "underlyingValue": 21500.0,
                    },
                    "PE": {
                        "openInterest": 80000,
                        "changeinOpenInterest": -2000,
                        "impliedVolatility": 13.0,
                        "totalTradedVolume": 40000,
                        "lastPrice": 155.0,
                        "bidprice": 154.0,
                        "askPrice": 156.0,
                        "underlyingValue": 21500.0,
                    },
                },
                {
                    "strikePrice": 21600,
                    "expiryDate": "18-Jan-2024",
                    "CE": {
                        "openInterest": 90000,
                        "changeinOpenInterest": 3000,
                        "impliedVolatility": 11.0,
                        "totalTradedVolume": 30000,
                        "lastPrice": 120.0,
                        "bidprice": 119.0,
                        "askPrice": 121.0,
                        "underlyingValue": 21500.0,
                    },
                    # No PE entry for this strike (deep OTM) — parser must handle this
                },
            ],
        }
    }


def _register_homepage():
    """Register the NSE homepage mock (needed for cookie handshake)."""
    responses_lib.add(
        responses_lib.GET, _NSE_HOME_URL,
        body="<html>ok</html>", status=200, content_type="text/html"
    )


def _nifty_oc_url() -> str:
    return "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"


def _reliance_oc_url() -> str:
    return "https://www.nseindia.com/api/option-chain-equities?symbol=RELIANCE"


# ════════════════════════════════════════════════════════════════════════════
# Tests: nse_bhavcopy.py — URL builder (no HTTP)
# ════════════════════════════════════════════════════════════════════════════

class TestBhavcopyUrlBuilder:
    """URL construction — no network needed."""

    def test_url_format_is_correct(self):
        """True positive: URL for 2024-01-15 matches NSE's documented pattern."""
        url = _bhavcopy_url(date(2024, 1, 15))
        assert url == (
            "https://archives.nseindia.com/content/historical/EQUITIES"
            "/2024/JAN/cm15JAN2024bhav.csv.zip"
        )

    def test_url_zero_pads_day(self):
        """Single-digit days must be zero-padded (e.g. 5 → 05)."""
        url = _bhavcopy_url(date(2024, 3, 5))
        assert "cm05MAR2024" in url

    def test_delivery_url_format(self):
        url = _delivery_url(date(2024, 1, 15))
        assert "sec_deliverypos_15JAN2024.dat" in url


# ════════════════════════════════════════════════════════════════════════════
# Tests: nse_bhavcopy.py — HTTP fetch (mocked)
# ════════════════════════════════════════════════════════════════════════════

@responses_lib.activate
def test_bhavcopy_valid_date_returns_dataframe():
    """
    TRUE POSITIVE: Valid bhavcopy ZIP parses into a DataFrame with
    expected columns and non-zero rows.
    """
    trading_date = date(2024, 1, 1)
    url = _bhavcopy_url(trading_date)
    responses_lib.add(
        responses_lib.GET, url,
        body=_make_bhavcopy_zip("RELIANCE"),
        status=200, content_type="application/zip",
    )
    responses_lib.add(responses_lib.GET, _delivery_url(trading_date), status=404)

    df = fetch_bhavcopy(trading_date)

    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0
    assert "symbol" in df.columns
    assert "close" in df.columns
    assert "volume" in df.columns
    assert "date" in df.columns
    assert df["symbol"].iloc[0] == "RELIANCE"
    assert df["close"].iloc[0] == pytest.approx(2830.00)


@responses_lib.activate
def test_bhavcopy_eq_series_filter_applied():
    """Only EQ-series records are returned by default (not BE, BZ etc.)."""
    trading_date = date(2024, 1, 1)
    url = _bhavcopy_url(trading_date)
    csv = (
        "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,"
        "TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN\r\n"
        "RELIANCE,EQ,2800,2850,2790,2830,2830,2795,1000000,2830000000,01-JAN-2024,5000,INE002A01018\r\n"
        "SOMENOTE,BE,100,105,98,103,103,99,50000,5150000,01-JAN-2024,200,INE999X01010\r\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("cm01JAN2024bhav.csv", csv)
    responses_lib.add(
        responses_lib.GET, url, body=buf.getvalue(), status=200,
        content_type="application/zip"
    )
    responses_lib.add(responses_lib.GET, _delivery_url(trading_date), status=404)

    df = fetch_bhavcopy(trading_date)
    assert all(df["series"] == "EQ")
    assert "SOMENOTE" not in df["symbol"].values


@responses_lib.activate
def test_bhavcopy_http_404_raises_fetch_error():
    """
    TRUE NEGATIVE: HTTP 404 (non-trading day or file not yet posted)
    must raise BhavcopyFetchError — NOT return None or empty DataFrame.
    """
    trading_date = date(2024, 1, 6)  # Saturday — no trading
    url = _bhavcopy_url(trading_date)
    responses_lib.add(responses_lib.GET, url, status=404)

    with pytest.raises(BhavcopyFetchError) as exc_info:
        fetch_bhavcopy(trading_date)

    assert exc_info.value.status_code == 404
    # The URL must be in the error so the caller knows what was attempted
    assert "JAN" in str(exc_info.value) or "nseindia" in str(exc_info.value).lower()


@responses_lib.activate
def test_bhavcopy_http_503_raises_fetch_error():
    """Server error raises BhavcopyFetchError with the correct status code."""
    trading_date = date(2024, 1, 2)
    url = _bhavcopy_url(trading_date)
    responses_lib.add(responses_lib.GET, url, status=503)

    with pytest.raises(BhavcopyFetchError) as exc_info:
        fetch_bhavcopy(trading_date)

    assert exc_info.value.status_code == 503


@responses_lib.activate
def test_bhavcopy_malformed_zip_raises_parse_error():
    """Non-ZIP bytes raise BhavcopyParseError, not a silent failure."""
    trading_date = date(2024, 1, 3)
    url = _bhavcopy_url(trading_date)
    responses_lib.add(
        responses_lib.GET, url,
        body=b"this is not a zip file",
        status=200, content_type="application/zip",
    )
    responses_lib.add(responses_lib.GET, _delivery_url(trading_date), status=404)

    with pytest.raises(BhavcopyParseError):
        fetch_bhavcopy(trading_date)


@responses_lib.activate
def test_bhavcopy_missing_columns_raises_parse_error():
    """CSV missing required columns raises BhavcopyParseError naming the missing columns."""
    trading_date = date(2024, 1, 4)
    url = _bhavcopy_url(trading_date)
    # CSV missing TOTTRDQTY and TOTTRDVAL (volume/turnover)
    csv = (
        "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,TIMESTAMP,ISIN\r\n"
        "RELIANCE,EQ,2800,2850,2790,2830,01-JAN-2024,INE002A01018\r\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bhav.csv", csv)
    responses_lib.add(
        responses_lib.GET, url, body=buf.getvalue(), status=200,
        content_type="application/zip"
    )
    responses_lib.add(responses_lib.GET, _delivery_url(trading_date), status=404)

    with pytest.raises(BhavcopyParseError) as exc_info:
        fetch_bhavcopy(trading_date)

    # Must tell us WHICH columns are missing (HARD RULE #3 — explanation)
    assert exc_info.value.missing_columns  # non-empty list
    assert ("TOTTRDQTY" in exc_info.value.missing_columns or
            "TOTTRDVAL" in exc_info.value.missing_columns)


@responses_lib.activate
def test_bhavcopy_no_synthetic_fallback_on_error():
    """
    Confirms the module does NOT silently return a DataFrame on failure.
    Any HTTP error must propagate — this would catch a hypothetical
    'except: return mock_df' anti-pattern.
    """
    trading_date = date(2024, 1, 5)
    url = _bhavcopy_url(trading_date)
    responses_lib.add(responses_lib.GET, url, status=500)

    with pytest.raises(BhavcopyFetchError):
        fetch_bhavcopy(trading_date)


# ════════════════════════════════════════════════════════════════════════════
# Tests: nse_bulk_deals.py
# ════════════════════════════════════════════════════════════════════════════

@responses_lib.activate
def test_bulk_deals_valid_csv_parses():
    """
    TRUE POSITIVE: Valid bulk deal CSV parses into a DataFrame with
    expected normalised columns and non-zero rows.
    """
    responses_lib.add(
        responses_lib.GET, _BULK_URL,
        body=_make_bulk_deal_csv().encode("latin-1"),
        status=200, content_type="text/csv",
    )

    df = fetch_bulk_deals()

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert {"date", "symbol", "client_name", "buy_sell",
            "quantity", "price", "deal_type"}.issubset(df.columns)
    assert df["deal_type"].iloc[0] == "bulk"
    assert "RELIANCE" in df["symbol"].values


@responses_lib.activate
def test_block_deals_valid_csv_parses():
    """TRUE POSITIVE: Block deal CSV parses correctly with deal_type='block'."""
    responses_lib.add(
        responses_lib.GET, _BLOCK_URL,
        body=_make_bulk_deal_csv().encode("latin-1"),
        status=200, content_type="text/csv",
    )

    df = fetch_block_deals()
    assert isinstance(df, pd.DataFrame)
    assert df["deal_type"].iloc[0] == "block"


@responses_lib.activate
def test_bulk_deals_http_404_raises_fetch_error():
    """
    TRUE NEGATIVE: HTTP 404 raises BulkDealFetchError — not empty DataFrame.
    """
    responses_lib.add(responses_lib.GET, _BULK_URL, status=404)

    with pytest.raises(BulkDealFetchError) as exc_info:
        fetch_bulk_deals()

    assert exc_info.value.status_code == 404
    assert _BULK_URL in exc_info.value.url


@responses_lib.activate
def test_bulk_deals_malformed_csv_raises_parse_error():
    """
    TRUE NEGATIVE: CSV missing required columns raises BulkDealParseError
    with a message naming the missing columns.
    """
    bad_csv = "Date,Symbol\r\n01-Jan-2024,RELIANCE\r\n"
    responses_lib.add(
        responses_lib.GET, _BULK_URL,
        body=bad_csv.encode("latin-1"),
        status=200, content_type="text/csv",
    )

    with pytest.raises(BulkDealParseError) as exc_info:
        fetch_bulk_deals()

    assert "missing" in str(exc_info.value).lower()


@responses_lib.activate
def test_bulk_deals_server_error_does_not_return_empty_df():
    """Network error must raise — never silently return empty data."""
    responses_lib.add(responses_lib.GET, _BULK_URL, status=500)

    with pytest.raises(BulkDealFetchError):
        fetch_bulk_deals()


# ════════════════════════════════════════════════════════════════════════════
# Tests: broker_order_stream.py (no HTTP — credential + parsing tests)
# ════════════════════════════════════════════════════════════════════════════

class TestBrokerOrderStream:
    """
    Tests for KiteOrderStream. We never call real Kite APIs in unit tests —
    that would require a live account. Instead we test:
      - Credential validation (no network needed)
      - Order event parsing (no network needed)
      - Error propagation (no network needed)
    """

    def test_raises_broker_auth_error_without_credentials(self):
        """
        TRUE NEGATIVE: Constructing KiteOrderStream without credentials
        raises BrokerAuthError IMMEDIATELY — does not defer to first API call.
        """
        env_backup = {}
        for var in ("KITE_API_KEY", "KITE_ACCESS_TOKEN"):
            env_backup[var] = os.environ.pop(var, None)

        try:
            from data.ingest.broker_order_stream import KiteOrderStream
            with pytest.raises(BrokerAuthError) as exc_info:
                KiteOrderStream(api_key=None, access_token=None)

            error_msg = str(exc_info.value)
            assert "KITE_API_KEY" in error_msg or "KITE_ACCESS_TOKEN" in error_msg, (
                "BrokerAuthError must name the missing credential(s) — "
                "a generic error message is insufficient for debugging."
            )
        finally:
            for var, val in env_backup.items():
                if val is not None:
                    os.environ[var] = val

    def test_raises_broker_auth_error_with_empty_api_key(self):
        """Empty string API key is treated the same as missing."""
        env_backup = {}
        for var in ("KITE_API_KEY", "KITE_ACCESS_TOKEN"):
            env_backup[var] = os.environ.pop(var, None)

        try:
            from data.ingest.broker_order_stream import KiteOrderStream
            with pytest.raises(BrokerAuthError):
                KiteOrderStream(api_key="", access_token="some_token")
        finally:
            for var, val in env_backup.items():
                if val is not None:
                    os.environ[var] = val

    def test_order_event_parsing(self):
        """
        TRUE POSITIVE: A valid Kite order dict is parsed into an OrderEvent
        with correctly mapped fields.
        """
        from data.ingest.broker_order_stream import _kite_order_to_event

        raw_order = {
            "order_id": "240101001234567",
            "tradingsymbol": "RELIANCE",
            "exchange": "NSE",
            "instrument_type": "EQ",
            "transaction_type": "BUY",
            "status": "COMPLETE",
            "price": 2830.5,
            "quantity": 10,
            "filled_quantity": 10,
            "order_timestamp": "2024-01-01T10:30:00",
        }

        event = _kite_order_to_event(raw_order, account_id="XY1234")

        assert event.exchange_order_id == "240101001234567"
        assert event.symbol == "RELIANCE"
        assert event.account_id == "XY1234"
        assert event.side == "buy"
        assert event.status == "executed"   # "COMPLETE" → "executed"
        assert event.price == pytest.approx(2830.5)
        assert event.quantity == 10
        assert event.filled_quantity == 10
        assert event.raw == raw_order        # raw dict preserved for audit

    def test_kite_status_mapping_covers_all_states(self):
        """
        TRUE POSITIVE: All known Kite status strings map to valid Sentinel statuses.
        Unmapped statuses must default gracefully, not raise KeyError.
        """
        from data.ingest.broker_order_stream import _kite_status_to_sentinel

        kite_statuses = [
            "OPEN", "COMPLETE", "CANCELLED", "REJECTED",
            "UPDATE", "TRIGGER PENDING",
            "SOME_FUTURE_STATUS",  # must not raise KeyError
        ]
        sentinel_valid = {"placed", "modified", "cancelled", "executed",
                          "partially_executed", "rejected"}

        for kite_status in kite_statuses:
            result = _kite_status_to_sentinel(kite_status)
            assert result in sentinel_valid, (
                f"Status {kite_status!r} mapped to {result!r} "
                f"which is not a valid Sentinel OrderStatus"
            )

    def test_invalid_credentials_raise_broker_auth_error_immediately(self):
        """
        TRUE NEGATIVE: Fake-but-structurally-valid credentials must raise
        BrokerAuthError at __init__ time — not at the first API call.
        This ensures the module fails loudly and immediately on bad auth,
        rather than appearing to succeed and failing silently later.
        """
        from data.ingest.broker_order_stream import KiteOrderStream
        with pytest.raises(BrokerAuthError) as exc_info:
            KiteOrderStream(api_key="fake_key_1234", access_token="fake_token_5678")

        error_msg = str(exc_info.value).lower()
        assert any(kw in error_msg for kw in (
            "kite", "api_key", "access_token", "authentication", "token"
        )), (
            f"BrokerAuthError message should identify the credential problem. "
            f"Got: {exc_info.value}"
        )


# ════════════════════════════════════════════════════════════════════════════
# Tests: nse_option_chain.py — HTTP fetch (mocked)
# ════════════════════════════════════════════════════════════════════════════

@responses_lib.activate
def test_option_chain_valid_nifty_parses():
    """
    TRUE POSITIVE: Valid NSE option chain JSON parses into a DataFrame with
    correct columns, one row per (strike, expiry, option_type) combination.
    """
    _register_homepage()
    responses_lib.add(
        responses_lib.GET, _nifty_oc_url(),
        json=_make_option_chain_response("NIFTY"),
        status=200, content_type="application/json",
    )

    df = fetch_option_chain("NIFTY")

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 3   # 2 strikes × 2 option types, minus 1 missing PE
    assert {"symbol", "expiry", "strike", "option_type",
            "oi", "iv", "volume", "ltp"}.issubset(df.columns)
    assert df["symbol"].iloc[0] == "NIFTY"


@responses_lib.activate
def test_option_chain_underlying_value_propagated():
    """Underlying value from the response is available in the DataFrame."""
    _register_homepage()
    responses_lib.add(
        responses_lib.GET, _nifty_oc_url(),
        json=_make_option_chain_response("NIFTY"),
        status=200, content_type="application/json",
    )
    df = fetch_option_chain("NIFTY")
    # All rows should have underlying_value ≈ 21500.0
    # Use numeric comparison — pytest.approx doesn't work with Series.all()
    assert (df["underlying_value"] - 21500.0).abs().max() < 1.0, (
        f"Expected underlying_value ≈ 21500.0, got: {df['underlying_value'].tolist()}"
    )


@responses_lib.activate
def test_option_chain_equity_symbol_uses_equities_endpoint():
    """RELIANCE (not an index) uses the /option-chain-equities endpoint."""
    _register_homepage()
    responses_lib.add(
        responses_lib.GET, _reliance_oc_url(),
        json=_make_option_chain_response("RELIANCE"),
        status=200, content_type="application/json",
    )
    df = fetch_option_chain("RELIANCE")
    assert df["symbol"].iloc[0] == "RELIANCE"


@responses_lib.activate
def test_option_chain_missing_pe_handled_gracefully():
    """A strike with only CE (no PE key) is handled gracefully."""
    _register_homepage()
    data = _make_option_chain_response("NIFTY")
    # Remove PE from both strikes to create all-CE scenario
    data["records"]["data"][0].pop("PE", None)
    data["records"]["data"][1].pop("PE", None)

    responses_lib.add(
        responses_lib.GET, _nifty_oc_url(),
        json=data, status=200, content_type="application/json"
    )
    df = fetch_option_chain("NIFTY")
    assert len(df) == 2
    assert all(df["option_type"] == "CE")


@responses_lib.activate
def test_option_chain_http_404_raises_fetch_error():
    """
    TRUE NEGATIVE: HTTP 404 raises OptionChainFetchError — not empty DataFrame.
    """
    _register_homepage()
    responses_lib.add(responses_lib.GET, _nifty_oc_url(), status=404)

    with pytest.raises(OptionChainFetchError) as exc_info:
        fetch_option_chain("NIFTY")

    assert exc_info.value.status_code == 404


@responses_lib.activate
def test_option_chain_html_response_raises_fetch_error():
    """
    TRUE NEGATIVE: NSE returning an HTML page (block/rate-limit response)
    instead of JSON raises OptionChainFetchError with a clear message.
    This is the most common real-world failure mode for this API —
    NSE returns 200 OK even for block pages, so content-type checking
    is the only reliable way to detect it.
    """
    _register_homepage()
    responses_lib.add(
        responses_lib.GET, _nifty_oc_url(),
        body="<html><body>Access denied</body></html>",
        status=200, content_type="text/html",
    )

    with pytest.raises(OptionChainFetchError) as exc_info:
        fetch_option_chain("NIFTY")

    assert "html" in str(exc_info.value).lower() or \
           "session" in str(exc_info.value).lower()


@responses_lib.activate
def test_option_chain_malformed_json_raises_parse_error():
    """
    TRUE NEGATIVE: JSON response missing the 'records' key raises
    OptionChainParseError with a message naming the missing keys.
    """
    _register_homepage()
    bad_response = {"status": "ok", "something_else": []}
    responses_lib.add(
        responses_lib.GET, _nifty_oc_url(),
        json=bad_response, status=200, content_type="application/json",
    )

    with pytest.raises(OptionChainParseError) as exc_info:
        fetch_option_chain("NIFTY")

    assert "records" in str(exc_info.value).lower() or \
           "keys" in str(exc_info.value).lower()


@responses_lib.activate
def test_option_chain_empty_data_array_raises_parse_error():
    """Empty 'data' array raises OptionChainParseError (not silent empty DF)."""
    _register_homepage()
    data = {
        "records": {
            "expiryDates": [],
            "underlyingValue": 21500.0,
            "data": [],
        }
    }
    responses_lib.add(
        responses_lib.GET, _nifty_oc_url(),
        json=data, status=200, content_type="application/json"
    )

    with pytest.raises(OptionChainParseError):
        fetch_option_chain("NIFTY")


# ════════════════════════════════════════════════════════════════════════════
# Tests: option chain parser (pure data transformation, no HTTP)
# ════════════════════════════════════════════════════════════════════════════

class TestOptionChainParser:
    """Tests _parse_option_chain_response directly — no HTTP mock needed."""

    def test_numeric_types_correct(self):
        """OI, IV, volume, strike must be numeric (not strings)."""
        data = _make_option_chain_response("NIFTY")
        df = _parse_option_chain_response(data, "NIFTY", datetime.utcnow())
        # OI can be int or float depending on values
        assert pd.api.types.is_numeric_dtype(df["oi"]), \
            f"oi should be numeric, got {df['oi'].dtype}"
        assert pd.api.types.is_float_dtype(df["iv"]), \
            f"iv should be float, got {df['iv'].dtype}"
        # Strike prices are whole numbers (e.g. 21500) — pandas infers int64,
        # which is fine: the point is it must not be object/string dtype.
        assert pd.api.types.is_numeric_dtype(df["strike"]), \
            f"strike should be numeric, got {df['strike'].dtype}"

    def test_expiry_column_is_datetime(self):
        """Expiry strings are parsed into pandas datetime."""
        data = _make_option_chain_response("NIFTY")
        df = _parse_option_chain_response(data, "NIFTY", datetime.utcnow())
        assert pd.api.types.is_datetime64_any_dtype(df["expiry"])


# ════════════════════════════════════════════════════════════════════════════
# Live integration tests (skipped in CI — run manually with -m live)
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.live
def test_live_bhavcopy_yesterday():
    """Fetch yesterday's bhavcopy and verify it has >0 rows."""
    from data.ingest.nse_bhavcopy import get_last_trading_day
    last_day = get_last_trading_day()
    df = fetch_bhavcopy(last_day)
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 100, f"Expected >100 symbols, got {len(df)}"


@pytest.mark.live
def test_live_bulk_deals():
    """Fetch current-year bulk deals — should have thousands of rows."""
    df = fetch_bulk_deals()
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0


@pytest.mark.live
def test_live_option_chain_nifty():
    """Fetch live NIFTY option chain. May fail outside market hours."""
    df = fetch_option_chain("NIFTY")
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0
    assert "NIFTY" in df["symbol"].values


@pytest.mark.live
def test_live_broker_auth_error_without_creds():
    """Without env vars, KiteOrderStream raises BrokerAuthError on any system."""
    env_backup = {}
    for var in ("KITE_API_KEY", "KITE_ACCESS_TOKEN"):
        env_backup[var] = os.environ.pop(var, None)

    try:
        from data.ingest.broker_order_stream import KiteOrderStream
        with pytest.raises(BrokerAuthError):
            KiteOrderStream()
    finally:
        for var, val in env_backup.items():
            if val is not None:
                os.environ[var] = val
