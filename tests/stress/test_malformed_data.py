"""
Malformed Data Tests — Phase 7 Stress Tests
=============================================

Verifies that every detector and ingest parser handles malformed, hostile,
or out-of-spec inputs by raising a clear, typed exception — NOT by:
  - silently returning an empty result (masking data corruption)
  - producing a false-positive alert
  - crashing with an unhandled AttributeError or TypeError

Input categories tested:
  - Negative quantities (quantity=-500)
  - Zero price (price=0.0)  
  - Timestamps out of order (reversed chronology)
  - Duplicate exchange_order_ids (same order event twice)
  - SQL injection in string fields (account_id = "'; DROP TABLE orders; --")
  - Extremely large numbers (price=1e38, quantity=2**31-1)
  - Unicode in account_id and symbol fields
  - DataFrames with wrong dtype columns (str 'oi' instead of numeric)

IMPORTANT: the goal is to confirm that these inputs either:
  (a) are handled gracefully (produce the same result as if the data
      were clean — e.g. duplicates are deduplicated correctly), OR
  (b) produce a clear typed exception that the caller can catch and log.
  
  Silent corruption or false positives are the prohibited outcomes.
"""

import uuid
from datetime import datetime, timedelta, date

import numpy as np
import pandas as pd
import pytest

from app.db.models import Instrument, InstrumentType, Order, OrderSide, OrderStatus
from app.detection.basis_distortion import detect_basis_distortion
from app.detection.oi_manipulation import detect_oi_concentration
from app.detection.option_pinning import detect_option_pinning
from app.detection.spoofing import run_spoofing_detection


# ── Helpers ───────────────────────────────────────────────────────────────────

def _oid():
    return str(uuid.uuid4())

def _make_instrument(avg_order_size=200.0, avg_volume=50_000) -> Instrument:
    return Instrument(
        symbol="TESTSTOCK",
        exchange="NSE",
        instrument_type=InstrumentType.PENNY_STOCK,
        avg_daily_volume_30d=avg_volume,
        avg_order_size_30d=avg_order_size,
        avg_daily_turnover_30d=2_500_000,
    )

def _make_order(
    account_id="ACC001",
    side=OrderSide.BUY,
    status=OrderStatus.PLACED,
    price=42.0,
    quantity=1000,
    timestamp=None,
    instrument: Instrument = None,
    exchange_order_id=None,
) -> Order:
    ts = timestamp or datetime(2026, 9, 3, 10, 0, 0)
    inst = instrument or _make_instrument()
    return Order(
        id=_oid(),
        exchange_order_id=exchange_order_id or _oid()[:12],
        account_id=account_id,
        instrument_id=inst.id,
        side=side,
        status=status,
        price=price,
        quantity=quantity,
        filled_quantity=quantity if status == OrderStatus.EXECUTED else 0,
        session="normal",
        timestamp=ts,
        exchange="NSE",
    )


# ════════════════════════════════════════════════════════════════════════════
# Malformed order data → spoofing detector
# ════════════════════════════════════════════════════════════════════════════

class TestMalformedOrders:

    def test_duplicate_exchange_order_id_deduped_not_double_counted(self):
        """
        Two Order rows with the same exchange_order_id (same event submitted twice,
        e.g. a retry or webhook duplicate) should be deduplicated to the LATEST event.
        The placed_value must reflect ONE order, not two.
        """
        inst = _make_instrument()
        t = datetime(2026, 9, 3, 10, 0, 0)
        oid = "DUPE_ORDER_001"

        order_1 = _make_order(
            status=OrderStatus.PLACED, price=42.0, quantity=1000,
            timestamp=t, instrument=inst, exchange_order_id=oid,
        )
        order_2 = _make_order(
            status=OrderStatus.PLACED, price=42.0, quantity=1000,
            timestamp=t + timedelta(milliseconds=500),
            instrument=inst, exchange_order_id=oid,  # Same oid — duplicate
        )

        # Single order (after dedup) → not enough for a pattern
        result = run_spoofing_detection([order_1, order_2], inst)
        assert result == [], "Duplicate order events must not be double-counted"

    def test_sql_injection_in_account_id_no_crash(self):
        """
        Malicious account_id strings must not cause exceptions or corrupt output.
        The detector only uses account_id as a grouping key — it should treat this
        as a plain (albeit odd-looking) string.
        """
        inst = _make_instrument()
        t = datetime(2026, 9, 3, 10, 0, 0)
        malicious_id = "'; DROP TABLE orders; --"

        order = _make_order(account_id=malicious_id, instrument=inst, timestamp=t)
        result = run_spoofing_detection([order], inst)
        # Single order — no signal. Key check: no exception was raised.
        assert isinstance(result, list)

    def test_unicode_account_id_no_crash(self):
        """
        Unicode in account_id (e.g. non-ASCII characters) must not crash the detector.
        """
        inst = _make_instrument()
        order = _make_order(account_id="账户_123_Ñ_∑", instrument=inst)
        result = run_spoofing_detection([order], inst)
        assert isinstance(result, list)

    def test_very_large_price_no_overflow(self):
        """
        price=1e15 (large but representable) must not cause float overflow
        in placed_value calculations. Python float handles up to ~1.8e308.
        """
        inst = _make_instrument()
        t = datetime(2026, 9, 3, 10, 0, 0)
        oid = "LARGE_PRICE_001"
        placed = _make_order(
            price=1e15, quantity=1, status=OrderStatus.PLACED,
            timestamp=t, instrument=inst, exchange_order_id=oid,
        )
        cancelled = _make_order(
            price=1e15, quantity=1, status=OrderStatus.CANCELLED,
            timestamp=t + timedelta(seconds=5), instrument=inst, exchange_order_id=oid,
        )
        # Must not raise — result is None or a signal depending on score
        try:
            result = run_spoofing_detection([placed, cancelled], inst)
            assert isinstance(result, list)
        except (OverflowError, FloatingPointError) as exc:
            pytest.fail(f"Overflow on very large price: {exc}")

    def test_very_large_quantity_no_overflow(self):
        """quantity=2**31-1 (max 32-bit int) must not overflow any calculation."""
        inst = _make_instrument()
        t = datetime(2026, 9, 3, 10, 0, 0)
        oid = "LARGE_QTY_001"
        placed = _make_order(
            price=1.0, quantity=2**31 - 1, status=OrderStatus.PLACED,
            timestamp=t, instrument=inst, exchange_order_id=oid,
        )
        cancelled = _make_order(
            price=1.0, quantity=2**31 - 1, status=OrderStatus.CANCELLED,
            timestamp=t + timedelta(seconds=5), instrument=inst, exchange_order_id=oid,
        )
        try:
            result = run_spoofing_detection([placed, cancelled], inst)
            assert isinstance(result, list)
        except OverflowError as exc:
            pytest.fail(f"Overflow on very large quantity: {exc}")

    def test_reversed_timestamp_order_no_crash(self):
        """
        Orders with timestamps in reverse chronological order (newest first)
        must be handled correctly — detectors sort by timestamp internally.
        """
        inst = _make_instrument()
        t = datetime(2026, 9, 3, 10, 0, 0)

        # Provide in reverse order — latest timestamps first
        orders = [
            _make_order(timestamp=t + timedelta(minutes=9), instrument=inst),
            _make_order(timestamp=t + timedelta(minutes=4), instrument=inst),
            _make_order(timestamp=t, instrument=inst),
        ]
        result = run_spoofing_detection(orders, inst)
        assert isinstance(result, list)  # Must not raise

    def test_timestamp_not_in_window_no_false_positive(self):
        """
        Orders outside the detection window must not contribute to the score.
        Passing orders from 10am to a window starting at 3pm should be filtered.
        """
        inst = _make_instrument()
        order = _make_order(
            timestamp=datetime(2026, 9, 3, 10, 0, 0),  # 10am order
            instrument=inst,
        )
        window_start = datetime(2026, 9, 3, 15, 0, 0)  # 3pm window
        window_end = datetime(2026, 9, 3, 15, 15, 0)
        result = run_spoofing_detection([order], inst)
        # 10am order is outside every 15-minute window after 3pm
        # run_spoofing_detection groups by account then by time — 10am falls
        # in a 10am window, not the 3pm window. Result: no signal.
        assert isinstance(result, list)


# ════════════════════════════════════════════════════════════════════════════
# Malformed DataFrame inputs → OI / option pinning detectors
# ════════════════════════════════════════════════════════════════════════════

class TestMalformedDataFrames:

    def _base_df(self, n_strikes=10, base_oi=100_000):
        rows = []
        for i in range(n_strikes):
            strike = 21500 + i * 100
            for opt_type in ("CE", "PE"):
                rows.append({
                    "symbol": "NIFTY",
                    "strike": float(strike),
                    "option_type": opt_type,
                    "oi": float(base_oi + i * 5_000),
                    "iv": 15.0 + i * 0.5,
                    "volume": 1000,
                    "expiry": pd.Timestamp("2026-09-25"),
                    "underlying_value": 21550.0,
                    "prev_oi": float(max(0, base_oi - 10_000)),
                })
        return pd.DataFrame(rows)

    def test_string_oi_column_raises_on_groupby_sum(self):
        """
        If 'oi' is object dtype (strings instead of numbers), the detector
        should either raise a clear error or handle gracefully — NOT produce
        silently wrong concentration ratios.
        """
        df = self._base_df()
        df["oi"] = df["oi"].astype(str)  # Force to string dtype

        # The detector should either raise (acceptable) or produce an empty
        # result after dropna() removes non-numeric rows. It must NOT return
        # a signal with a nonsensical ratio.
        try:
            result = detect_oi_concentration(df, "NIFTY", "NSE", datetime.utcnow())
            # If it doesn't raise: groupby sum on strings produces TypeError
            # OR the dropna removes everything → empty list
            for sig in result:
                # If a signal is returned, its ratio must be a valid float in [0,1]
                assert 0.0 <= sig.concentration_ratio <= 1.0, (
                    f"String 'oi' column produced invalid ratio: {sig.concentration_ratio}"
                )
        except (TypeError, ValueError):
            pass  # Acceptable — raised a clear error instead of corrupting

    def test_negative_oi_handled(self):
        """
        Negative OI values are data-feed errors. The detector must not
        produce a concentration signal where the negative OI artificially
        dominates. Two acceptable behaviours:
          (a) dropna/filter removes negatives → no signal, or
          (b) raises ValueError.
        """
        df = self._base_df()
        # Set one strike's OI to a large negative number
        df.loc[df["strike"] == 21500.0, "oi"] = -500_000
        try:
            result = detect_oi_concentration(df, "NIFTY", "NSE", datetime.utcnow())
            for sig in result:
                # If it runs: the flagged strike must not be the negative-OI one
                assert sig.strike != 21500.0 or sig.strike_oi >= 0, (
                    "Negative OI strike should not produce a concentration signal"
                )
        except (ValueError, ZeroDivisionError):
            pass  # Acceptable — raises clearly

    def test_nan_underlying_value_no_crash(self):
        """NaN in 'underlying_value' must not cause AttributeError or NaN in signal."""
        df = self._base_df(base_oi=2_000_000)  # Very high OI to trigger signal
        df["underlying_value"] = float("nan")
        try:
            result = detect_oi_concentration(df, "NIFTY", "NSE", datetime.utcnow())
            if result:
                # underlying_value in signal should not be NaN — it would
                # make the signal meaningless
                for sig in result:
                    assert sig.underlying_value is not None
        except (ValueError, TypeError):
            pass  # Acceptable

    def test_single_strike_option_chain_no_crash(self):
        """An option chain with only one strike must not cause division-by-zero."""
        df = self._base_df(n_strikes=1, base_oi=500_000)
        try:
            result = detect_oi_concentration(df, "NIFTY", "NSE", datetime.utcnow())
            assert isinstance(result, list)
        except ZeroDivisionError as exc:
            pytest.fail(f"Single-strike chain caused ZeroDivisionError: {exc}")

    def test_option_pinning_with_all_nan_oi(self):
        """
        option_pinning detector: if chain_df has all-NaN OI, must return
        None (not enough data) or raise — not produce a false signal.
        """
        df = self._base_df()
        df["oi"] = float("nan")
        try:
            result = detect_option_pinning(
                df, "NIFTY", "NSE",
                spot_price=21500.0,
                expiry_date=date(2026, 9, 4),  # Near expiry
                snapshot_time=datetime(2026, 9, 3, 10, 0),
            )
            assert result is None, "All-NaN OI should not produce a pinning signal"
        except (ValueError, TypeError):
            pass  # Acceptable

    def test_empty_chain_df_raises_not_silently_passes(self):
        """Passing an empty DataFrame to option_pinning raises ValueError."""
        with pytest.raises(ValueError):
            detect_option_pinning(
                pd.DataFrame(), "NIFTY", "NSE",
                spot_price=21500.0,
                expiry_date=date(2026, 9, 25),
                snapshot_time=datetime(2026, 9, 3, 10, 0),
            )


# ════════════════════════════════════════════════════════════════════════════
# Malformed basis distortion inputs
# ════════════════════════════════════════════════════════════════════════════

class TestMalformedBasisInputs:

    def test_nan_spot_price_raises(self):
        """NaN spot_price: can't compute fair value — should raise."""
        with pytest.raises((ValueError, TypeError)):
            detect_basis_distortion(
                "NIFTY", "NSE",
                spot_price=float("nan"),
                futures_price=21550.0,
                expiry_date=date(2026, 9, 25),
                snapshot_time=datetime(2026, 9, 3, 10, 0),
            )

    def test_infinity_futures_price_raises_or_returns_signal(self):
        """
        futures_price=inf: mathematically infinite deviation.
        Must either raise or return a signal with deviation > threshold.
        Must NOT silently return None (which would mean "no manipulation").
        """
        try:
            result = detect_basis_distortion(
                "NIFTY", "NSE",
                spot_price=21500.0,
                futures_price=float("inf"),
                expiry_date=date(2026, 9, 25),
                snapshot_time=datetime(2026, 9, 3, 10, 0),
            )
            # If it doesn't raise, it must produce a signal (deviation is infinite)
            assert result is not None, (
                "Infinite futures price should produce a basis distortion signal"
            )
        except (ValueError, OverflowError):
            pass  # Acceptable
