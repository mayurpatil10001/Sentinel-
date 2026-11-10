"""
tests/test_phase8_backtest.py
==============================
Tests for Phase 8: Calibration / Backtest runner over historical sessions.
"""

import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backtest.fyers_detector_backtest import (
    MIN_HISTORY_DAYS,
    WATCHLIST_15,
    run_fyers_backtest,
)


def _generate_synthetic_bars(
    num_bars: int = 40,
    base_price: float = 100.0,
    base_volume: float = 100000.0,
    inject_spike_at: int = -1,
    inject_reversal_at: int = -1,
):
    """Generate synthetic daily bars for testing."""
    bars = []
    start_date = date(2024, 1, 1)
    price = base_price

    for i in range(num_bars):
        d = start_date + timedelta(days=i)
        vol = base_volume

        # Inject volume spike
        if i == inject_spike_at:
            vol = base_volume * 12.0  # 12x volume spike

        # Inject 3-day pump then dump reversal
        if inject_reversal_at != -1 and i in (
            inject_reversal_at - 2,
            inject_reversal_at - 1,
        ):
            price *= 1.08  # +8% each day
        elif i == inject_reversal_at:
            price *= 0.85  # -15% dump

        bars.append(
            {
                "date": d,
                "timestamp": int(1704067200 + i * 86400),
                "open": round(price * 0.99, 2),
                "high": round(price * 1.02, 2),
                "low": round(price * 0.98, 2),
                "close": round(price, 2),
                "volume": float(vol),
            }
        )
    return bars


class TestPhase8BacktestRunner:
    def test_watchlist_contains_15_symbols(self):
        assert len(WATCHLIST_15) == 15
        assert "RELIANCE" in WATCHLIST_15
        assert "TCS" in WATCHLIST_15
        assert "HDFCBANK" in WATCHLIST_15

    def test_backtest_with_mock_data(self, tmp_path):
        # 30 bars with an injected volume spike at bar index 25
        mock_bars = _generate_synthetic_bars(num_bars=30, inject_spike_at=25)
        mock_data = {"RELIANCE": mock_bars}

        test_json = tmp_path / "test_report.json"
        with patch("backtest.fyers_detector_backtest.OUTPUT_JSON", test_json):
            report = run_fyers_backtest(
                symbols=["RELIANCE"],
                mock_data=mock_data,
                rate_limit_delay=0.0,
            )

        assert report["symbols_requested"] == 1
        # Evaluated sessions = len(mock_bars) - MIN_HISTORY_DAYS
        expected_eval = 30 - MIN_HISTORY_DAYS
        assert report["total_symbol_days_evaluated"] == expected_eval
        assert "volume_spike" in report["detector_summary"]
        assert report["detector_summary"]["volume_spike"]["trigger_count"] >= 1
        assert test_json.exists()

        saved_data = json.loads(test_json.read_text(encoding="utf-8"))
        assert saved_data["total_symbol_days_evaluated"] == expected_eval

    def test_insufficient_bars_handled_gracefully(self):
        mock_bars = _generate_synthetic_bars(num_bars=5)  # Less than MIN_HISTORY_DAYS
        mock_data = {"TCS": mock_bars}

        report = run_fyers_backtest(
            symbols=["TCS"],
            mock_data=mock_data,
            rate_limit_delay=0.0,
        )

        assert report["total_symbol_days_evaluated"] == 0
        assert report["per_symbol_summary"]["TCS"]["evaluated_sessions"] == 0
        assert report["per_symbol_summary"]["TCS"]["error"] == "insufficient_bars"

    def test_multi_day_momentum_reversal_detected(self, tmp_path):
        mock_bars = _generate_synthetic_bars(
            num_bars=35,
            inject_reversal_at=28,
        )
        mock_data = {"INFY": mock_bars}

        test_json = tmp_path / "test_rev.json"
        with patch("backtest.fyers_detector_backtest.OUTPUT_JSON", test_json):
            report = run_fyers_backtest(
                symbols=["INFY"],
                mock_data=mock_data,
                rate_limit_delay=0.0,
            )

        summary = report["detector_summary"]
        assert summary["momentum_reversal"]["trigger_count"] >= 1
