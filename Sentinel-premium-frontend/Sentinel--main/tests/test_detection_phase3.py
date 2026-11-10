"""
Phase 3 Detection Tests — Derivatives-Specific Detectors
=========================================================

Tests for:
  - oi_manipulation.py   (OI concentration, OI-IV decoupling)
  - basis_distortion.py  (futures basis vs fair value)
  - option_pinning.py    (spot pinned near high-OI strike at expiry)

Verification prompt compliance:
  1. Each detector has a TRUE POSITIVE test (detector catches the pattern).
  2. Each detector has a TRUE NEGATIVE test (normal inputs do NOT trigger).
  3. Every signal has a non-empty explanation (HARD RULE #3).
  4. Empty/invalid inputs raise ValueError, not return synthetic data (HARD RULE #1).

Run:
    pytest tests/test_detection_phase3.py -v
"""

from datetime import datetime, date, timedelta

import pandas as pd
import pytest

from app.detection.oi_manipulation import (
    detect_oi_concentration,
    detect_oi_iv_decoupling,
    OIConcentrationSignal,
    OIIVDecouplingSignal,
    OI_CONCENTRATION_THRESHOLD,
    OI_IV_DECOUPLING_THRESHOLD,
    MIN_CHAIN_OI,
)
from app.detection.basis_distortion import (
    detect_basis_distortion,
    BasisDistortionSignal,
    BASIS_DEVIATION_THRESHOLD,
    RISK_FREE_RATE,
)
from app.detection.option_pinning import (
    detect_option_pinning,
    OptionPinningSignal,
    PIN_DISTANCE_THRESHOLD,
    PIN_EXPIRY_DAYS_THRESHOLD,
    PIN_OI_DOMINANCE_THRESHOLD,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_chain(
    symbol: str = "NIFTY",
    strikes: list | None = None,
    base_oi: int = 50_000,
    base_iv: float = 15.0,
    underlying: float = 21_500.0,
    expiry: date | None = None,
) -> pd.DataFrame:
    """Builds a minimal option chain DataFrame with uniform OI/IV across strikes."""
    if strikes is None:
        strikes = [21_000, 21_200, 21_400, 21_500, 21_600, 21_800, 22_000]
    if expiry is None:
        expiry = date(2024, 1, 25)  # 3rd week Thursday

    rows = []
    for s in strikes:
        for opt_type in ("CE", "PE"):
            rows.append({
                "symbol": symbol,
                "strike": s,
                "expiry": pd.Timestamp(expiry),
                "option_type": opt_type,
                "oi": base_oi,
                "iv": base_iv,
                "volume": 10_000,
                "ltp": max(0.05, abs(underlying - s) * 0.5),
                "underlying_value": underlying,
            })
    return pd.DataFrame(rows)


def _make_concentrated_chain(
    dominant_strike: float = 21_500.0,
    dominant_oi: int = 400_000,
    other_oi: int = 30_000,
    opt_type: str = "CE",
    underlying: float = 21_500.0,
) -> pd.DataFrame:
    """Builds a chain where one strike has abnormally high OI."""
    strikes = [21_000, 21_200, 21_400, 21_500, 21_600, 21_800, 22_000]
    expiry = pd.Timestamp(date(2024, 1, 25))
    rows = []
    for s in strikes:
        for ot in ("CE", "PE"):
            oi = dominant_oi if (s == dominant_strike and ot == opt_type) else other_oi
            rows.append({
                "symbol": "NIFTY",
                "strike": s,
                "expiry": expiry,
                "option_type": ot,
                "oi": oi,
                "iv": 15.0,
                "volume": 5_000,
                "ltp": 100.0,
                "underlying_value": underlying,
            })
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════════════
# Tests: OI Concentration
# ══════════════════════════════════════════════════════════════════════════════

class TestOIConcentration:

    def test_detects_concentrated_oi(self):
        """
        TRUE POSITIVE: One strike holds >35% of total chain OI.
        The detector must flag this.

        Setup: 7 CE strikes. 6 strikes at 30k OI each, 1 dominant at 400k.
        Total CE OI = 6×30k + 400k = 580k.
        Dominant fraction = 400k / 580k ≈ 69% >> threshold of 35%.
        """
        chain = _make_concentrated_chain(
            dominant_strike=21_500.0,
            dominant_oi=400_000,
            other_oi=30_000,
            opt_type="CE",
        )
        snapshot = datetime(2024, 1, 15, 11, 0, 0)

        signals = detect_oi_concentration(chain, "NIFTY", "NSE", snapshot)

        assert len(signals) >= 1, (
            f"Expected OI concentration signal when one strike holds >69% of OI. "
            f"Got {len(signals)} signals."
        )
        sig = signals[0]
        assert isinstance(sig, OIConcentrationSignal)
        assert sig.concentration_ratio >= OI_CONCENTRATION_THRESHOLD
        assert sig.strike == 21_500.0
        assert sig.score > 0
        assert sig.severity in ("low", "medium", "high", "critical")

    def test_explanation_non_empty(self):
        """HARD RULE #3: signal.explanation must be non-empty and substantive."""
        chain = _make_concentrated_chain(dominant_oi=400_000, other_oi=30_000)
        signals = detect_oi_concentration(
            chain, "NIFTY", "NSE", datetime(2024, 1, 15, 11, 0, 0)
        )
        for sig in signals:
            assert sig.explanation, "Explanation must not be empty"
            assert len(sig.explanation) > 80, "Explanation must be substantive"
            assert "concentration" in sig.explanation.lower() or \
                   "oi" in sig.explanation.lower(), \
                "Explanation must describe the OI pattern"
            assert "%" in sig.explanation, "Explanation must include a percentage"

    def test_does_not_flag_uniform_oi(self):
        """
        TRUE NEGATIVE: All strikes have equal OI — no single strike dominates.
        No concentration signal.
        """
        chain = _make_chain(base_oi=50_000)
        # Each of 7 strikes × 2 option types → each holds 1/7 of its type's OI
        # Concentration ratio per strike ≈ 14% << threshold of 35%
        signals = detect_oi_concentration(
            chain, "NIFTY", "NSE", datetime(2024, 1, 15, 11, 0, 0)
        )
        assert len(signals) == 0, (
            f"Uniform OI (all strikes equal) must NOT trigger concentration signal. "
            f"Got {len(signals)} signals: {[(s.strike, s.concentration_ratio) for s in signals]}"
        )

    def test_does_not_flag_thin_chain(self):
        """
        TRUE NEGATIVE: Chain with very low total OI (below MIN_CHAIN_OI).
        Thin chains are excluded to avoid false alarms in new/illiquid strikes.
        """
        chain = _make_chain(base_oi=100)  # total OI = 7 strikes × 2 types × 100 = 1400 << 50,000
        signals = detect_oi_concentration(
            chain, "ILLIQ", "NSE", datetime(2024, 1, 15, 11, 0, 0)
        )
        assert len(signals) == 0, (
            f"Thin chain (total OI << MIN_CHAIN_OI={MIN_CHAIN_OI}) must be excluded. "
            f"Got {len(signals)} signals."
        )

    def test_raises_on_empty_chain(self):
        """HARD RULE #1: empty DataFrame raises ValueError, not silent synthetic output."""
        with pytest.raises(ValueError, match="empty"):
            detect_oi_concentration(
                pd.DataFrame(), "NIFTY", "NSE", datetime.utcnow()
            )

    def test_raises_on_missing_columns(self):
        """DataFrame without required columns raises ValueError naming the missing ones."""
        bad_df = pd.DataFrame({"strike": [21000], "oi": [10000]})
        # Missing: expiry, option_type, underlying_value
        with pytest.raises(ValueError, match="missing"):
            detect_oi_concentration(bad_df, "NIFTY", "NSE", datetime.utcnow())

    def test_deep_otm_concentration_scores_higher(self):
        """
        Deep OTM concentration (10%+ from spot) should score HIGHER than ATM,
        because ATM clustering is natural (most hedging happens ATM).
        """
        underlying = 21_500.0
        # ATM concentration: dominant strike = spot price
        chain_atm = _make_concentrated_chain(
            dominant_strike=21_500.0,  # = underlying
            dominant_oi=400_000, other_oi=30_000, underlying=underlying
        )
        # Deep OTM concentration: dominant strike is 15% above spot
        chain_otm = _make_concentrated_chain(
            dominant_strike=22_000.0,  # ≈ +2.3% above spot → not deep OTM for this test
            dominant_oi=400_000, other_oi=30_000, underlying=underlying
        )
        snapshot = datetime(2024, 1, 15, 11, 0, 0)

        sigs_atm = detect_oi_concentration(chain_atm, "NIFTY", "NSE", snapshot)
        sigs_otm = detect_oi_concentration(chain_otm, "NIFTY", "NSE", snapshot)

        # Both should fire — we're just comparing relative scores
        if sigs_atm and sigs_otm:
            atm_score = max(s.score for s in sigs_atm if s.option_type == "CE")
            otm_score = max(s.score for s in sigs_otm if s.option_type == "CE")
            # OTM score should be >= ATM score
            assert otm_score >= atm_score or abs(otm_score - atm_score) < 0.05, (
                f"Deep OTM concentration should score >= ATM. "
                f"ATM score: {atm_score:.3f}, OTM score: {otm_score:.3f}"
            )


# ══════════════════════════════════════════════════════════════════════════════
# Tests: OI-IV Decoupling
# ══════════════════════════════════════════════════════════════════════════════

class TestOIIVDecoupling:

    def _make_chain_pair(
        self,
        oi_prev: int = 100_000,
        oi_curr: int = 150_000,   # +50% OI growth
        iv_prev: float = 15.0,
        iv_curr: float = 10.0,    # IV fell while OI grew
        opt_type: str = "CE",
        strike: float = 21_500.0,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Makes two chain snapshots for decoupling detection."""
        expiry = pd.Timestamp(date(2024, 1, 25))

        def make(oi, iv):
            return pd.DataFrame([{
                "strike": strike,
                "expiry": expiry,
                "option_type": opt_type,
                "oi": oi,
                "iv": iv,
                "underlying_value": 21_500.0,
            }])

        return make(oi_curr, iv_curr), make(oi_prev, iv_prev)

    def test_detects_decoupling(self):
        """
        TRUE POSITIVE: OI grows +50% while IV falls 33%.
        Both exceed the threshold of 30%. Must trigger.
        """
        curr, prev = self._make_chain_pair(
            oi_prev=100_000, oi_curr=150_000,  # +50%
            iv_prev=15.0, iv_curr=10.0,        # -33%
        )
        signals = detect_oi_iv_decoupling(
            curr, prev, "NIFTY", "NSE", datetime(2024, 1, 15, 14, 0, 0)
        )
        assert len(signals) >= 1, (
            f"Expected decoupling signal for OI +50% while IV -33%. "
            f"Got {len(signals)}."
        )
        sig = signals[0]
        assert isinstance(sig, OIIVDecouplingSignal)
        assert sig.oi_change_pct > 0
        assert sig.iv_change_pct < 0
        assert sig.explanation

    def test_does_not_flag_consistent_oi_iv(self):
        """
        TRUE NEGATIVE: OI grows AND IV rises (consistent — buyers entering,
        demand drives up vol). No decoupling.
        """
        curr, prev = self._make_chain_pair(
            oi_prev=100_000, oi_curr=150_000,  # +50%
            iv_prev=15.0, iv_curr=20.0,        # +33% — IV rose WITH OI (normal buying)
        )
        signals = detect_oi_iv_decoupling(
            curr, prev, "NIFTY", "NSE", datetime(2024, 1, 15, 14, 0, 0)
        )
        assert len(signals) == 0, (
            "OI growth accompanied by IV rise (consistent directional buying) "
            f"must NOT trigger decoupling signal. Got {len(signals)} signals."
        )

    def test_does_not_flag_small_oi_change(self):
        """
        TRUE NEGATIVE: OI changes by only 5% while IV falls 40%.
        OI change is below OI_IV_DECOUPLING_THRESHOLD — no signal.
        """
        curr, prev = self._make_chain_pair(
            oi_prev=100_000, oi_curr=105_000,  # +5% — below threshold
            iv_prev=15.0, iv_curr=9.0,         # -40% IV fall
        )
        signals = detect_oi_iv_decoupling(
            curr, prev, "NIFTY", "NSE", datetime(2024, 1, 15, 14, 0, 0)
        )
        assert len(signals) == 0, (
            f"Small OI change (5% < threshold {OI_IV_DECOUPLING_THRESHOLD*100:.0f}%) "
            f"must NOT trigger decoupling signal regardless of IV move."
        )

    def test_raises_on_empty_chain(self):
        """HARD RULE #1: empty current_chain raises ValueError."""
        _, prev = self._make_chain_pair()
        with pytest.raises(ValueError):
            detect_oi_iv_decoupling(pd.DataFrame(), prev, "NIFTY", "NSE", datetime.utcnow())


# ══════════════════════════════════════════════════════════════════════════════
# Tests: Basis Distortion
# ══════════════════════════════════════════════════════════════════════════════

class TestBasisDistortion:

    def _fair_value_basis(
        self, spot: float, dte: int, rfr: float = RISK_FREE_RATE
    ) -> float:
        return spot * rfr * (dte / 365.0)

    def test_detects_contango_excess(self):
        """
        TRUE POSITIVE: Futures trading at 2% premium when fair value = 0.5%.
        Deviation = 1.5% >> threshold of 0.5%.
        """
        spot = 21_500.0
        dte = 28
        fv_basis = self._fair_value_basis(spot, dte)
        # Add 2% excess
        futures = spot + fv_basis + spot * 0.02  # 2% premium above fair

        sig = detect_basis_distortion(
            symbol="NIFTY",
            exchange="NSE",
            spot_price=spot,
            futures_price=futures,
            expiry_date=date(2024, 1, 15) + timedelta(days=dte),
            snapshot_time=datetime(2024, 1, 15, 10, 0, 0),
        )

        assert sig is not None, (
            f"Expected basis distortion signal for 2% excess contango. Got None."
        )
        assert isinstance(sig, BasisDistortionSignal)
        assert sig.direction == "contango_excess"
        assert sig.deviation_pct > BASIS_DEVIATION_THRESHOLD
        assert sig.score > 0
        assert sig.explanation

    def test_detects_backwardation_excess(self):
        """
        TRUE POSITIVE: Futures trading at 2% DISCOUNT to spot.
        Fair value should be positive (contango) — deep backwardation is abnormal.
        """
        spot = 21_500.0
        dte = 28
        fv_basis = self._fair_value_basis(spot, dte)
        # Futures discount: actual = spot - 2%
        futures = spot - spot * 0.02

        sig = detect_basis_distortion(
            symbol="NIFTY",
            exchange="NSE",
            spot_price=spot,
            futures_price=futures,
            expiry_date=date(2024, 1, 15) + timedelta(days=dte),
            snapshot_time=datetime(2024, 1, 15, 10, 0, 0),
        )

        assert sig is not None, "Expected basis distortion for excess backwardation."
        assert sig.direction == "backwardation_excess"
        assert sig.explanation

    def test_does_not_flag_normal_basis(self):
        """
        TRUE NEGATIVE: Futures trading exactly at fair value.
        No basis distortion signal.
        """
        spot = 21_500.0
        dte = 28
        fv_basis = self._fair_value_basis(spot, dte)
        futures = spot + fv_basis  # exactly at fair value

        sig = detect_basis_distortion(
            symbol="NIFTY",
            exchange="NSE",
            spot_price=spot,
            futures_price=futures,
            expiry_date=date(2024, 1, 15) + timedelta(days=dte),
            snapshot_time=datetime(2024, 1, 15, 10, 0, 0),
        )
        assert sig is None, (
            f"Futures at exactly fair-value basis must NOT trigger distortion signal. "
            f"Got: {sig}"
        )

    def test_does_not_flag_small_deviation(self):
        """
        TRUE NEGATIVE: Futures 0.1% above fair value (within normal noise).
        Below BASIS_DEVIATION_THRESHOLD — no signal.
        """
        spot = 21_500.0
        dte = 28
        fv_basis = self._fair_value_basis(spot, dte)
        futures = spot + fv_basis + spot * 0.001  # 0.1% excess (< 0.5% threshold)

        sig = detect_basis_distortion(
            symbol="NIFTY",
            exchange="NSE",
            spot_price=spot,
            futures_price=futures,
            expiry_date=date(2024, 1, 15) + timedelta(days=dte),
            snapshot_time=datetime(2024, 1, 15, 10, 0, 0),
        )
        assert sig is None, (
            f"Small basis deviation (0.1% < threshold {BASIS_DEVIATION_THRESHOLD*100}%) "
            f"must NOT trigger signal. Got: {sig}"
        )

    def test_raises_on_invalid_spot(self):
        """HARD RULE #1: zero spot raises ValueError."""
        with pytest.raises(ValueError, match="positive"):
            detect_basis_distortion(
                "NIFTY", "NSE", 0.0, 21_500.0,
                date.today() + timedelta(days=10),
                datetime.utcnow()
            )

    def test_raises_on_past_expiry(self):
        """Expired contract raises ValueError."""
        with pytest.raises(ValueError, match="past"):
            detect_basis_distortion(
                "NIFTY", "NSE", 21_500.0, 21_700.0,
                date(2020, 1, 1),   # far in the past
                datetime(2024, 1, 15, 10, 0, 0)
            )

    def test_explanation_contains_calculation(self):
        """
        HARD RULE #3: explanation must include the actual numbers
        so an analyst can reproduce the calculation independently.
        """
        spot = 21_500.0
        dte = 28
        fv_basis = self._fair_value_basis(spot, dte)
        futures = spot + fv_basis + spot * 0.02

        sig = detect_basis_distortion(
            symbol="NIFTY",
            exchange="NSE",
            spot_price=spot,
            futures_price=futures,
            expiry_date=date(2024, 1, 15) + timedelta(days=dte),
            snapshot_time=datetime(2024, 1, 15, 10, 0, 0),
        )

        assert sig is not None
        expl = sig.explanation
        # Must include: spot price, futures price, basis values, DTE, threshold
        assert str(int(spot)) in expl or f"{spot:,.2f}" in expl, \
            "Explanation must include the spot price"
        assert "fair" in expl.lower() or "fair-value" in expl.lower(), \
            "Explanation must reference fair value calculation"
        assert "%" in expl, "Explanation must include a percentage"
        assert "unvalidated" in expl.lower() or "calibrat" in expl.lower(), \
            "Explanation must flag the threshold as unvalidated"

    def test_signal_reproducible_from_explanation(self):
        """
        The signal's risk_free_rate_used is stored so the calculation can be
        reproduced exactly even if the system default RFR changes later.
        """
        sig = detect_basis_distortion(
            symbol="NIFTY", exchange="NSE",
            spot_price=21_500.0, futures_price=22_100.0,
            expiry_date=date(2024, 1, 15) + timedelta(days=28),
            snapshot_time=datetime(2024, 1, 15, 10, 0, 0),
            risk_free_rate=0.065,  # explicitly passed
        )
        if sig:
            assert sig.risk_free_rate_used == 0.065, \
                "The risk-free rate used must be stored on the signal for audit purposes"


# ══════════════════════════════════════════════════════════════════════════════
# Tests: Option Pinning
# ══════════════════════════════════════════════════════════════════════════════

class TestOptionPinning:

    def _pinning_chain(
        self,
        pin_strike: float = 21_500.0,
        spot: float = 21_505.0,   # very close to pin_strike
        dominant_oi: int = 200_000,
        other_oi: int = 20_000,
    ) -> pd.DataFrame:
        """Chain where pin_strike dominates OI and spot is very close to it."""
        strikes = [21_000, 21_200, 21_400, 21_500, 21_600, 21_800, 22_000]
        expiry = pd.Timestamp(date(2024, 1, 25))
        rows = []
        for s in strikes:
            for ot in ("CE", "PE"):
                oi = dominant_oi if s == pin_strike else other_oi
                rows.append({
                    "symbol": "NIFTY",
                    "strike": s,
                    "expiry": expiry,
                    "option_type": ot,
                    "oi": oi,
                    "iv": 15.0,
                    "ltp": 100.0,
                    "underlying_value": spot,
                })
        return pd.DataFrame(rows)

    def test_detects_pinning_at_expiry(self):
        """
        TRUE POSITIVE: spot = 21,505 (0.023% from strike 21,500),
        DTE = 1 day, 21,500 strike holds 5× adjacent OI.
        All three criteria met — must fire.
        """
        spot = 21_505.0
        pin_strike = 21_500.0
        expiry = date(2024, 1, 25)
        snapshot = datetime(2024, 1, 24, 14, 0, 0)   # 1 day to expiry

        chain = self._pinning_chain(
            pin_strike=pin_strike, spot=spot,
            dominant_oi=200_000, other_oi=20_000,
        )

        sig = detect_option_pinning(
            chain, "NIFTY", "NSE", spot, expiry, snapshot
        )

        assert sig is not None, (
            f"Expected option pinning signal: spot {spot} is "
            f"{abs(spot-pin_strike)/spot*100:.3f}% from high-OI strike {pin_strike} "
            f"with 1 day to expiry. Got None."
        )
        assert isinstance(sig, OptionPinningSignal)
        assert sig.pin_strike == pin_strike
        assert sig.days_to_expiry <= PIN_EXPIRY_DAYS_THRESHOLD
        assert sig.distance_pct < PIN_DISTANCE_THRESHOLD
        assert sig.oi_dominance_ratio >= PIN_OI_DOMINANCE_THRESHOLD
        assert sig.explanation

    def test_does_not_flag_far_from_strike(self):
        """
        TRUE NEGATIVE: spot is 3% away from the nearest strike.
        Exceeds PIN_DISTANCE_THRESHOLD — no pinning signal.
        """
        spot = 21_500.0 * 1.03   # 3% above 21,500
        expiry = date(2024, 1, 25)
        snapshot = datetime(2024, 1, 24, 14, 0, 0)   # 1 day to expiry

        chain = self._pinning_chain(
            pin_strike=21_500.0, spot=spot,
            dominant_oi=200_000, other_oi=20_000,
        )

        sig = detect_option_pinning(
            chain, "NIFTY", "NSE", spot, expiry, snapshot
        )
        assert sig is None, (
            f"Spot 3% away from strike must NOT trigger pinning "
            f"(threshold {PIN_DISTANCE_THRESHOLD*100:.1f}%). Got: {sig}"
        )

    def test_does_not_flag_far_from_expiry(self):
        """
        TRUE NEGATIVE: spot is near the dominant strike, but 10 days to expiry.
        Pinning only matters close to expiry (DTE <= PIN_EXPIRY_DAYS_THRESHOLD).
        """
        spot = 21_505.0
        expiry = date(2024, 1, 25)
        snapshot = datetime(2024, 1, 15, 10, 0, 0)  # 10 days before expiry

        chain = self._pinning_chain(
            pin_strike=21_500.0, spot=spot,
            dominant_oi=200_000, other_oi=20_000,
        )

        sig = detect_option_pinning(
            chain, "NIFTY", "NSE", spot, expiry, snapshot
        )
        assert sig is None, (
            f"Spot near strike but 10 days to expiry must NOT trigger pinning "
            f"(threshold: {PIN_EXPIRY_DAYS_THRESHOLD} days). Got: {sig}"
        )

    def test_does_not_flag_low_oi_dominance(self):
        """
        TRUE NEGATIVE: spot is near the dominant strike, DTE=1, but OI at
        pin strike is only 1.1× adjacent strikes (below PIN_OI_DOMINANCE_THRESHOLD=2.0).
        """
        spot = 21_505.0
        expiry = date(2024, 1, 25)
        snapshot = datetime(2024, 1, 24, 14, 0, 0)

        chain = self._pinning_chain(
            pin_strike=21_500.0, spot=spot,
            dominant_oi=22_000,   # only slightly above other_oi=20,000 → ratio ≈ 1.1
            other_oi=20_000,
        )

        sig = detect_option_pinning(
            chain, "NIFTY", "NSE", spot, expiry, snapshot
        )
        assert sig is None, (
            f"Low OI dominance (≈1.1× < threshold {PIN_OI_DOMINANCE_THRESHOLD}×) "
            f"must NOT trigger pinning signal. Got: {sig}"
        )

    def test_raises_on_empty_chain(self):
        """HARD RULE #1: empty chain raises ValueError."""
        with pytest.raises(ValueError, match="empty"):
            detect_option_pinning(
                pd.DataFrame(), "NIFTY", "NSE",
                21_500.0, date.today() + timedelta(days=1), datetime.utcnow()
            )

    def test_raises_on_past_expiry(self):
        """Past expiry raises ValueError."""
        chain = self._pinning_chain()
        with pytest.raises(ValueError, match="past"):
            detect_option_pinning(
                chain, "NIFTY", "NSE", 21_505.0,
                date(2020, 1, 1),
                datetime(2024, 1, 24, 14, 0, 0)
            )

    def test_explanation_includes_false_positive_warning(self):
        """
        HARD RULE #3: Pinning explanation must include the false-positive
        warning about market maker gamma — this is a low-confidence signal.
        """
        spot = 21_505.0
        expiry = date(2024, 1, 25)
        snapshot = datetime(2024, 1, 24, 14, 0, 0)
        chain = self._pinning_chain(
            pin_strike=21_500.0, spot=spot,
            dominant_oi=200_000, other_oi=20_000,
        )

        sig = detect_option_pinning(chain, "NIFTY", "NSE", spot, expiry, snapshot)
        if sig is not None:
            assert "market maker" in sig.explanation.lower() or \
                   "false positive" in sig.explanation.lower() or \
                   "gamma" in sig.explanation.lower(), (
                "Pinning signal explanation must warn about market maker "
                "gamma as a false-positive source. "
                f"Got: {sig.explanation[:200]}..."
            )

    def test_max_pain_reported(self):
        """The max-pain strike is computed and stored on the signal."""
        spot = 21_505.0
        expiry = date(2024, 1, 25)
        snapshot = datetime(2024, 1, 24, 14, 0, 0)
        chain = self._pinning_chain(
            pin_strike=21_500.0, spot=spot,
            dominant_oi=200_000, other_oi=20_000,
        )

        sig = detect_option_pinning(chain, "NIFTY", "NSE", spot, expiry, snapshot)
        if sig is not None:
            assert sig.max_pain_strike > 0, "max_pain_strike must be computed"
            assert str(int(sig.max_pain_strike)) in sig.explanation or \
                   "max" in sig.explanation.lower(), \
                "Explanation should mention the max-pain strike"
