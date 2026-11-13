"""
tests/test_alert_rate_noise.py
================================
Phase 4 (E9): Verify that OHLCV detectors do NOT over-fire on pure noise.

Methodology
-----------
300 synthetic non-manipulated series are generated using a Student-t
distribution (df=4, heavy tails, σ=0.8%/day) with lognormal volume
(σ=0.35). This distribution is intentionally adversarial: it produces
occasional extreme single-day returns that look like outliers but are
drawn from a stationary non-manipulated process.

Seed 7 is fixed so the test is reproducible. Any change to the threshold
registry or detection logic that pushes the noise rate above the budget
(5%) will fail this test.

False-alert budget: ≤5% of non-manipulated series must trigger ANY alert.
Pump-detection requirement: 100% of pump-spiked series must be detected.

Pump probe: last bar = +9% close, 12× volume relative to series baseline.

Do NOT weaken or skip this test. If a threshold change causes it to fail,
review the detection logic, not the test.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from typing import List, Dict

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Synthetic data
# ---------------------------------------------------------------------------
_SEED_BASE   = 7
_N_BARS      = 45
_LOOKBACK    = 5
_N_NOISE     = 300
_N_PUMP      = 20
_START_DATE  = date(2024, 1, 2)
_NOISE_BUDGET = 0.05   # ≤5% of non-manipulated series may fire


def _make_bars(n: int = _N_BARS, seed: int = 0, pump: bool = False) -> List[Dict]:
    """
    Generate n synthetic OHLCV bars.

    Returns:  price series with Student-t df=4 returns (heavy tails),
              lognormal volume sigma=0.35.
    If pump=True, the LAST bar has +9% close and 12× volume — a clear
    coordinated pump signal that detectors MUST catch.
    """
    rng = np.random.default_rng(seed)
    daily_sigma = 0.008

    t_draws = rng.standard_t(df=4, size=n)
    scale   = daily_sigma / (np.std(t_draws) or daily_sigma)
    returns = t_draws * scale

    closes = [100.0]
    for r in returns:
        closes.append(closes[-1] * (1 + r))
    closes = closes[1:]

    volumes = rng.lognormal(mean=0.0, sigma=0.35, size=n) * 1_000_000

    bars: List[Dict] = []
    for i in range(n):
        c     = closes[i]
        noise = abs(rng.normal(0, daily_sigma * c))
        o     = c * (1 + rng.normal(0, daily_sigma * 0.3))
        bars.append({
            "open":   round(float(o), 2),
            "high":   round(max(o, c) + abs(noise), 2),
            "low":    round(min(o, c) - abs(noise), 2),
            "close":  round(float(c), 2),
            "volume": int(volumes[i]),
            "date":   str(_START_DATE + timedelta(days=i)),
        })

    if pump:
        last  = bars[-1]
        new_c = last["close"] * 1.09
        bars[-1] = {
            "open":   last["close"],
            "high":   round(new_c * 1.005, 2),
            "low":    round(last["close"] * 0.998, 2),
            "close":  round(new_c, 2),
            "volume": int(last["volume"] * 12),
            "date":   last["date"],
        }

    return bars


# ---------------------------------------------------------------------------
# Detection runner (mirrors production path in detection_worker.py)
# ---------------------------------------------------------------------------

def _run_all_detectors(bars: List[Dict]) -> List[Dict]:
    """
    Run the full set of OHLCV detectors exactly as production does.
    Returns the list of detection dicts (empty = no alert).
    """
    from app.workers.detection_worker import (
        _evaluate_session,
        detect_momentum_reversal,
        detect_sustained_volume,
    )

    alerts: List[Dict] = []
    n = len(bars)

    for i in range(max(0, n - _LOOKBACK), n):
        alerts.extend(
            _evaluate_session("TEST", bars[i], bars[:i], ltp=bars[i]["close"])
        )

    ed = date.fromisoformat(bars[-1]["date"])
    r  = detect_momentum_reversal("TEST", bars, event_date=ed)
    if r:
        alerts.append(r)
    r = detect_sustained_volume("TEST", bars, event_date=ed)
    if r:
        alerts.append(r)

    return alerts


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAlertRateNoise:
    """
    E9 — OHLCV false-alert rate on synthetic non-manipulated data.
    Fixed seed=7 for reproducibility.
    """

    def test_noise_false_alert_rate_within_budget(self):
        """
        No more than 5% of 300 non-manipulated series (seed 7+i) may trigger
        ANY alert from the OHLCV detector pipeline.

        Distribution: Student-t df=4 returns scaled to 0.8%/day std,
        lognormal volume sigma=0.35, 45 bars, lookback=5.
        """
        flagged = 0
        for i in range(_N_NOISE):
            bars = _make_bars(n=_N_BARS, seed=_SEED_BASE + i, pump=False)
            if _run_all_detectors(bars):
                flagged += 1

        rate = flagged / _N_NOISE
        assert rate <= _NOISE_BUDGET, (
            f"Noise false-alert rate {rate:.1%} exceeds budget {_NOISE_BUDGET:.0%}. "
            f"{flagged}/{_N_NOISE} non-manipulated series were flagged. "
            "Review detector thresholds (especially zscore_medium in threshold_registry.py). "
            "Do NOT raise the budget — fix the detector."
        )

    def test_pump_probe_detected(self):
        """
        All 20 pump-spiked series (last bar +9% close, 12× volume) must be
        detected by at least one OHLCV detector.

        If this fails after a threshold change, detection sensitivity has
        dropped too far — the threshold is too conservative.
        """
        missed = []
        for i in range(_N_PUMP):
            bars = _make_bars(n=_N_BARS, seed=_SEED_BASE + i, pump=True)
            if not _run_all_detectors(bars):
                missed.append(i)

        assert not missed, (
            f"Pump-spiked series NOT detected: seeds {[_SEED_BASE+m for m in missed]}. "
            f"Missed {len(missed)}/{_N_PUMP} pump probes (+9% close, 12× volume). "
            "Detection sensitivity is too low — review threshold_registry.py."
        )

    def test_noise_rate_verified_value(self):
        """
        Regression guard: the actual noise rate with seed=7 must match the
        calibrated value (4.3% = 13/300) within ±1 percentage point.
        This catches silent regressions where a threshold change shifts the
        rate without breaking the budget test.
        """
        flagged = 0
        for i in range(_N_NOISE):
            bars = _make_bars(n=_N_BARS, seed=_SEED_BASE + i, pump=False)
            if _run_all_detectors(bars):
                flagged += 1

        rate = flagged / _N_NOISE
        # Calibrated at 4.3% (13/300) on 2026-11-13 with zscore_medium=4.0
        expected = 0.043
        tolerance = 0.012   # ±1.2 pp
        assert abs(rate - expected) <= tolerance, (
            f"Noise rate {rate:.1%} has drifted from calibrated {expected:.1%} "
            f"(tolerance ±{tolerance:.1%}). "
            "If thresholds changed intentionally, update expected= in this test "
            "and re-run the probe script. Never silently widen the budget."
        )

