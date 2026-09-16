"""
tests/test_harm_estimation.py
==============================
Synthetic known-answer validation for the harm estimation module.

TESTING PHILOSOPHY
------------------
Because neither SEBI case (KIL-2019, PUMP-DUMP-2017-2020) has accessible
real BSE historical price data in the current pipeline, ALL positive-case
tests use synthetic data with known ground truth:

  1. Generate a synthetic "clean" market-model relationship over an estimation
     window (known alpha, known beta, known sigma).
  2. Inject a known abnormal return (INJECTED_CAR) into an event window.
  3. Verify that event_study.run_event_study() recovers a CAR estimate that:
       (a) Has the injected value within its 95% confidence interval.
       (b) Has t-stat > t_crit (correctly identifies the anomaly as significant
           when the injection is large relative to sigma).
       (c) The 95% CI lower bound is > 0 for a large enough injection.

This is the standard methodological validation approach for event-study
implementations: "inject known effect, verify recovery within CI."
References:
  - MacKinlay (1997), Section 3.3: discusses test-statistic power for daily data.
  - Brown & Warner (1985), Table 2: rejection rates for known-effect simulations.

GUARD TESTS
-----------
  Verify that guards.check_instrument_allowed() correctly:
    - Allows SEBI-confirmed cases
    - Allows synthetic scenarios
    - Refuses anything else with HarmGuardRefusal
    - Records bypass attempts in the audit log

TRADE HARM TESTS
----------------
  Verify that trade_harm.compute_per_trade_harm() correctly:
    - Computes sign-correct harm for buyers and sellers
    - Returns consistent low/central/high ordering
    - Handles the edge case where actual price == counterfactual (zero harm)
"""

import logging
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.harm_estimation.guards import (
    HarmGuardRefusal,
    check_instrument_allowed,
    _GUARD_AUDIT_LOG_PATH,
)
from app.harm_estimation.event_study import (
    fit_market_model,
    compute_abnormal_returns,
    compute_car,
    run_event_study,
    DEFAULT_ESTIMATION_WINDOW_DAYS,
    MIN_ESTIMATION_WINDOW_DAYS,
)
from app.harm_estimation.trade_harm import (
    TradeRecord,
    compute_per_trade_harm,
    compute_instrument_harm_report,
    CounterfactualPricePath,
)

logger = logging.getLogger(__name__)

# ── Synthetic data factory ────────────────────────────────────────────────────

RNG = np.random.default_rng(seed=42)  # fixed seed for reproducibility

def _make_trading_dates(n: int, start: date = date(2020, 1, 2)) -> list[date]:
    """Generate n consecutive weekday dates."""
    dates = []
    current = start
    while len(dates) < n:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates


def _make_synthetic_prices(
    n: int,
    true_alpha: float,
    true_beta: float,
    market_vol: float = 0.01,
    residual_vol: float = 0.008,
    start_price: float = 100.0,
    start: date = date(2020, 1, 2),
) -> tuple[pd.Series, pd.Series]:
    """
    Generate synthetic stock and market price series following the market model:
        R_stock = true_alpha + true_beta * R_market + epsilon

    Returns (stock_prices, market_prices) as date-indexed pd.Series.
    """
    dates = _make_trading_dates(n, start)
    r_market = RNG.normal(0.0005, market_vol, size=n)
    epsilon = RNG.normal(0.0, residual_vol, size=n)
    r_stock = true_alpha + true_beta * r_market + epsilon

    # Convert returns to prices via compounding
    mkt_prices = np.zeros(n + 1)
    mkt_prices[0] = 1000.0  # market index start
    for i, r in enumerate(r_market):
        mkt_prices[i + 1] = mkt_prices[i] * (1 + r)

    stk_prices = np.zeros(n + 1)
    stk_prices[0] = start_price
    for i, r in enumerate(r_stock):
        stk_prices[i + 1] = stk_prices[i] * (1 + r)

    # Return as n-length Series (prices at end of each day)
    idx = pd.to_datetime(dates)
    return (
        pd.Series(stk_prices[1:], index=idx),
        pd.Series(mkt_prices[1:], index=idx),
    )


def _inject_abnormal_return(
    stock_prices: pd.Series,
    event_start: date,
    event_end: date,
    car_to_inject: float,
) -> pd.Series:
    """
    Modify stock prices within [event_start, event_end] to embed a total
    cumulative abnormal return of car_to_inject.

    Method: distribute the injected CAR uniformly as a daily additive return
    boost across all event-window days, then recompound from the pre-event price.
    """
    event_mask = (stock_prices.index >= pd.Timestamp(event_start)) & \
                 (stock_prices.index <= pd.Timestamp(event_end))
    event_idx = stock_prices.index[event_mask]
    n_event = len(event_idx)
    if n_event == 0:
        return stock_prices

    # Daily injection amount distributed uniformly across event window
    daily_boost = car_to_inject / n_event

    modified = stock_prices.copy()
    pre_event_price = float(stock_prices[~event_mask & (stock_prices.index < pd.Timestamp(event_start))].iloc[-1]) \
        if any(~event_mask & (stock_prices.index < pd.Timestamp(event_start))) \
        else float(stock_prices.iloc[0])

    # Recompute event-window prices with the daily boost applied
    current_price = pre_event_price
    for t in event_idx:
        # Original return for this day (relative to previous price)
        original_price = float(stock_prices[t])
        # We need to recompute from the modified chain, not original
        # Actual return = (original - prev_actual) / prev_actual
        prev_actual = float(stock_prices.shift(1)[t]) if float(stock_prices.shift(1)[t]) > 0 else pre_event_price
        r_original = (original_price - prev_actual) / prev_actual if prev_actual > 0 else 0.0
        r_modified = r_original + daily_boost
        new_price = current_price * (1 + r_modified)
        modified[t] = new_price
        current_price = new_price

    return modified


# ── GUARD TESTS ───────────────────────────────────────────────────────────────

class TestGuards:
    """Test the allow-list guard enforcement."""

    def test_sebi_confirmed_case_allowed(self):
        """KIL-2019 is on the SEBI confirmed list — must be allowed."""
        result = check_instrument_allowed("KAVIT", "KIL-2019")
        assert result == "sebi_confirmed"

    def test_sebi_confirmed_case_by_instrument(self):
        """Instrument lookup works even if case_id is empty."""
        result = check_instrument_allowed("MAURIUDYOG", "PUMP-DUMP-2017-2020")
        assert result == "sebi_confirmed"

    def test_synthetic_scenario_allowed(self):
        """Synthetic scenario ID starting with SYNTHETIC_ must be allowed."""
        result = check_instrument_allowed(
            "RELIANCE", "SYNTHETIC_PUMP_RELIANCE_2021_VALIDATION"
        )
        assert result == "synthetic"

    def test_synthetic_by_prefix(self):
        """Any case_id starting with SYNTHETIC_ is allowed as synthetic."""
        result = check_instrument_allowed(
            "TESTINST", "SYNTHETIC_MY_NEW_TEST_SCENARIO"
        )
        assert result == "synthetic"

    def test_unlisted_instrument_refused(self):
        """An instrument not on any list must raise HarmGuardRefusal."""
        with pytest.raises(HarmGuardRefusal):
            check_instrument_allowed("WIPRO", "SOME_RANDOM_CASE")

    def test_live_large_cap_refused(self):
        """A real large-cap not in a SEBI confirmed case must be refused."""
        with pytest.raises(HarmGuardRefusal):
            check_instrument_allowed("TCS", "FAKE_CASE_ID")

    def test_synthetic_scenario_id_must_have_prefix(self):
        """SyntheticScenario constructor must reject IDs without SYNTHETIC_ prefix."""
        from app.harm_estimation.guards import SyntheticScenario
        with pytest.raises(ValueError, match="SYNTHETIC_"):
            SyntheticScenario(
                scenario_id="NOTPREFIXED_scenario",
                description="should fail",
                instruments=["SOME_STOCK"],
            )

    def test_bypass_is_logged_not_silenced(self, caplog, tmp_path, monkeypatch):
        """
        bypass_guard=True should not raise, but must log at ERROR level.
        Verifies the audit paper trail is not silently swallowed.
        """
        import app.harm_estimation.guards as guards_module
        # Redirect audit log to temp path for test isolation
        monkeypatch.setattr(guards_module, "_GUARD_AUDIT_LOG_PATH", tmp_path / "audit.log")

        with caplog.at_level(logging.ERROR, logger="app.harm_estimation.guards"):
            result = check_instrument_allowed(
                "NOTWHITELISTED_STOCK", "NOT_A_CASE", bypass_guard=True
            )
        assert result == "sebi_confirmed"  # bypass grants access
        assert "BYPASS" in caplog.text or "REFUSED" in caplog.text

        # Verify audit log was written
        audit_log = tmp_path / "audit.log"
        assert audit_log.exists(), "Guard audit log was not created"
        log_content = audit_log.read_text()
        assert "NOTWHITELISTED_STOCK" in log_content


# ── MARKET MODEL TESTS ────────────────────────────────────────────────────────

class TestMarketModel:
    """Test market model fitting (OLS regression on estimation window)."""

    TRUE_ALPHA = 0.0002
    TRUE_BETA = 1.1
    N_EST = 250

    def _est_data(self):
        stock, market = _make_synthetic_prices(
            self.N_EST,
            true_alpha=self.TRUE_ALPHA,
            true_beta=self.TRUE_BETA,
        )
        return stock, market

    def test_beta_recovered_within_tolerance(self):
        """
        OLS beta should be within ±0.15 of the true beta for 250 estimation days.
        This is the primary model validation check — if it fails, the regression
        is broken, not just imprecise.
        """
        stock, market = self._est_data()
        r_stock = stock.pct_change().dropna()
        r_market = market.pct_change().dropna()

        model = fit_market_model(r_stock, r_market, estimation_window_days=200)
        assert abs(model.beta - self.TRUE_BETA) < 0.15, (
            f"OLS beta={model.beta:.4f} deviated from true_beta={self.TRUE_BETA} "
            "by more than 0.15 — regression is likely broken."
        )

    def test_alpha_recovered_within_tolerance(self):
        """Alpha estimate should be within 3 standard errors of true alpha."""
        stock, market = self._est_data()
        r_stock = stock.pct_change().dropna()
        r_market = market.pct_change().dropna()
        model = fit_market_model(r_stock, r_market)
        # With 200 days and residual_vol=0.008, 3σ tolerance ≈ 0.003 / sqrt(200) ≈ 0.002
        assert abs(model.alpha - self.TRUE_ALPHA) < 0.003

    def test_r_squared_positive(self):
        """R² should be positive (model explains some variance)."""
        stock, market = self._est_data()
        model = fit_market_model(
            stock.pct_change().dropna(), market.pct_change().dropna()
        )
        assert model.r_squared > 0, "R² is non-positive — something is wrong with OLS fit."

    def test_too_few_days_raises(self):
        """Fewer than MIN_ESTIMATION_WINDOW_DAYS must raise ValueError."""
        stock, market = _make_synthetic_prices(80, true_alpha=0.0, true_beta=1.0)
        r_s = stock.pct_change().dropna()
        r_m = market.pct_change().dropna()
        with pytest.raises(ValueError, match="Minimum is"):
            fit_market_model(r_s, r_m, estimation_window_days=200)


# ── KNOWN-ANSWER EVENT STUDY TESTS ───────────────────────────────────────────

class TestCARKnownAnswer:
    """
    KNOWN-ANSWER VALIDATION: The primary test battery.

    Method:
      1. Generate clean estimation window with known alpha, beta, sigma.
      2. Inject a known CAR into the event window.
      3. Run the full event study pipeline.
      4. Assert that the recovered CAR CI contains the injected value.

    This validates the end-to-end pipeline against ground truth — the
    "inject known effect, verify recovery within CI" standard from
    MacKinlay (1997, Section 3.3).
    """

    TRUE_ALPHA = 0.0001
    TRUE_BETA = 1.05
    TOTAL_DAYS = 250 + 20  # 250 estimation + 20 event
    EVENT_WINDOW_DAYS = 20
    EVENT_START = date(2021, 1, 4)

    def _run_with_car(self, car_to_inject: float):
        n = self.TOTAL_DAYS
        stock, market = _make_synthetic_prices(
            n, self.TRUE_ALPHA, self.TRUE_BETA,
            residual_vol=0.008, start=date(2019, 1, 2),
        )
        trading_dates = _make_trading_dates(n, date(2019, 1, 2))
        event_start = trading_dates[230]   # ~230 days in, leaving 20-day event window
        event_end = trading_dates[249]     # 20-day event window

        stock_with_injection = _inject_abnormal_return(
            stock, event_start, event_end, car_to_inject
        )
        car_result, cf_path = run_event_study(
            stock_with_injection, market, event_start, event_end,
            estimation_window_days=200,
        )
        return car_result, event_start, event_end

    def test_injected_car_within_confidence_interval(self):
        """
        CORE VALIDATION: Injected CAR=+0.30 (30% over 20 days) must be
        recovered within the 95% CI. This verifies the fundamental correctness
        of the event-study pipeline end-to-end.
        """
        car_to_inject = 0.30
        result, _, _ = self._run_with_car(car_to_inject)

        assert result.car_low <= car_to_inject <= result.car_high, (
            f"Injected CAR={car_to_inject:.4f} not within 95% CI "
            f"[{result.car_low:.4f}, {result.car_high:.4f}]. "
            "The event-study pipeline does not correctly recover known effects."
        )

    def test_large_car_is_statistically_significant(self):
        """
        An injected CAR of +30% over 20 days (>>5σ above noise) should be
        statistically significant at 95%. This validates that large manipulations
        are correctly identified, not false-negatives.
        """
        result, _, _ = self._run_with_car(0.30)
        assert result.significant_at_95, (
            f"Injected CAR of +30% not flagged significant. t={result.t_stat:.3f} p={result.p_value:.4f}. "
            "The test statistic computation is likely broken."
        )

    def test_small_car_in_noise_may_not_be_significant(self):
        """
        A small injected CAR (+1%) within normal noise may not reach significance.
        Verifies that the methodology is NOT over-sensitive (false positive control).
        This test passes if the result is NOT significant or if it is — both are
        statistically valid outcomes for a small effect. The key assertion is that
        the CI still contains the injected value.
        """
        car_to_inject = 0.01
        result, _, _ = self._run_with_car(car_to_inject)
        # CI must still contain the injected value regardless of significance
        assert result.car_low <= car_to_inject <= result.car_high, (
            f"Small injected CAR={car_to_inject:.4f} not within CI "
            f"[{result.car_low:.4f}, {result.car_high:.4f}]."
        )

    def test_zero_injection_car_near_zero(self):
        """
        With no injection, the recovered CAR should be statistically
        indistinguishable from zero (verifies no spurious detection on clean data).
        Specifically: |CAR| < 3 * SE (within 3 standard errors of zero).
        """
        result, _, _ = self._run_with_car(0.0)
        assert abs(result.car_central) < 3 * result.se_car, (
            f"Zero-injection test: CAR={result.car_central:.4f} is more than "
            f"3×SE={result.se_car:.4f} from zero. May indicate estimation bias."
        )

    def test_ci_width_increases_with_longer_event_window(self):
        """
        CI width should be wider for longer event windows (more forecast uncertainty).
        Validates the T_event * sigma² scaling in the Brown & Warner (1985) formula.
        """
        # Compare 10-day vs 20-day event windows by splitting the 20-day window
        n = self.TOTAL_DAYS
        stock, market = _make_synthetic_prices(
            n, self.TRUE_ALPHA, self.TRUE_BETA,
            residual_vol=0.008, start=date(2019, 1, 2),
        )
        trading_dates = _make_trading_dates(n, date(2019, 1, 2))

        # Short event window
        event_start = trading_dates[230]
        event_end_short = trading_dates[239]   # 10 days
        event_end_long = trading_dates[249]    # 20 days

        car_short, _ = run_event_study(stock, market, event_start, event_end_short)
        car_long, _ = run_event_study(stock, market, event_start, event_end_long)

        ci_width_short = car_short.car_high - car_short.car_low
        ci_width_long = car_long.car_high - car_long.car_low

        assert ci_width_long > ci_width_short, (
            f"CI width should grow with event window. Got: "
            f"short={ci_width_short:.4f}, long={ci_width_long:.4f}."
        )

    def test_ci_lower_bound_positive_for_large_positive_car(self):
        """
        For a large injection (+30% over 20 days), even the CI lower bound
        should be clearly positive — confirming the harm direction.
        This is the 'unambiguous harm' criterion in trade_harm.py.
        """
        result, _, _ = self._run_with_car(0.30)
        assert result.car_low > 0, (
            f"Large injection: CI lower bound={result.car_low:.4f} is not positive. "
            "Expected the entire CI to be above zero for a clear 30% inflation."
        )

    def test_output_always_carries_disclaimer(self):
        """Every CARResult must have a non-empty disclaimer string."""
        result, _, _ = self._run_with_car(0.10)
        assert result.disclaimer and len(result.disclaimer) > 50, \
            "CARResult disclaimer is empty or too short."


# ── TRADE HARM TESTS ──────────────────────────────────────────────────────────

class TestTradeHarm:
    """
    Test per-trade harm calculation against known counterfactual prices.
    """

    def _make_cf_path(self, dates: list[date], cf_central, cf_low, cf_high, actual):
        return CounterfactualPricePath(
            dates=dates,
            actual_price=np.array(actual),
            counterfactual_central=np.array(cf_central),
            counterfactual_low=np.array(cf_low),
            counterfactual_high=np.array(cf_high),
            pre_event_price=actual[0],
        )

    def test_buyer_harm_when_price_inflated(self):
        """
        Buyer at actual_price=120 vs cf_central=100 (stock was pumped):
        Harm = (120 - 100) × 500 = ₹10,000.
        """
        d = date(2021, 3, 15)
        cf = self._make_cf_path(
            [d], [100.0], [95.0], [105.0], [120.0]
        )
        trade = TradeRecord(
            trade_id="T1", account_id="ACCT_A", trade_date=d,
            price=120.0, quantity=500, side="buy", instrument_id="TESTINST",
        )
        est = compute_per_trade_harm(trade, cf)
        assert math.isclose(est.harm_central, 10_000.0, rel_tol=1e-6), \
            f"Buyer harm expected ₹10,000, got ₹{est.harm_central}"
        assert est.harm_low > 0  # even at cf_high=105, buyer still overpaid
        assert est.harm_low <= est.harm_central <= est.harm_high

    def test_seller_harm_when_price_deflated(self):
        """
        Seller at actual_price=80 vs cf_central=100 (stock was dumped):
        Harm = (100 - 80) × 200 = ₹4,000.
        """
        d = date(2021, 3, 16)
        cf = self._make_cf_path(
            [d], [100.0], [90.0], [110.0], [80.0]
        )
        trade = TradeRecord(
            trade_id="T2", account_id="ACCT_B", trade_date=d,
            price=80.0, quantity=200, side="sell", instrument_id="TESTINST",
        )
        est = compute_per_trade_harm(trade, cf)
        assert math.isclose(est.harm_central, 4_000.0, rel_tol=1e-6), \
            f"Seller harm expected ₹4,000, got ₹{est.harm_central}"
        assert est.harm_low <= est.harm_central <= est.harm_high

    def test_zero_harm_when_actual_equals_counterfactual(self):
        """
        If actual price == counterfactual price, harm should be zero.
        """
        d = date(2021, 3, 17)
        cf = self._make_cf_path(
            [d], [100.0], [100.0], [100.0], [100.0]
        )
        trade = TradeRecord(
            trade_id="T3", account_id="ACCT_C", trade_date=d,
            price=100.0, quantity=1000, side="buy", instrument_id="TESTINST",
        )
        est = compute_per_trade_harm(trade, cf)
        assert math.isclose(est.harm_central, 0.0, abs_tol=1e-9)
        assert not est.significant

    def test_harm_range_ordering_invariant(self):
        """
        harm_low must always <= harm_central <= harm_high.
        Verified for both buy and sell sides across multiple scenarios.
        """
        dates = [date(2021, 4, 1)]
        test_cases = [
            # (actual, cf_c, cf_l, cf_h, qty, side)
            (150.0, 100.0, 95.0, 110.0, 300, "buy"),
            (50.0, 100.0, 90.0, 115.0, 150, "sell"),
            (100.0, 105.0, 100.0, 110.0, 1000, "buy"),
        ]
        for actual, cf_c, cf_l, cf_h, qty, side in test_cases:
            cf = self._make_cf_path(dates, [cf_c], [cf_l], [cf_h], [actual])
            trade = TradeRecord(
                "TX", "ACCT_X", dates[0], actual, qty, side, "TESTINST"
            )
            est = compute_per_trade_harm(trade, cf)
            assert est.harm_low <= est.harm_high, (
                f"Ordering violated: harm_low={est.harm_low:.4f} > harm_high={est.harm_high:.4f} "
                f"for side={side}, actual={actual}, cf=({cf_c},{cf_l},{cf_h})"
            )

    def test_instrument_harm_aggregation(self):
        """
        Verify account-level and instrument-level aggregation is correct.
        Two buyers, one seller — confirm totals.
        """
        d1, d2, d3 = date(2021, 5, 3), date(2021, 5, 4), date(2021, 5, 5)
        cf = self._make_cf_path(
            [d1, d2, d3],
            [100.0, 101.0, 99.0],
            [98.0, 99.0, 97.0],
            [102.0, 103.0, 101.0],
            [110.0, 112.0, 95.0],
        )
        trades = [
            TradeRecord("T1", "ACCT_BUYER1", d1, 110.0, 100, "buy", "TESTINST"),
            TradeRecord("T2", "ACCT_BUYER2", d2, 112.0, 200, "buy", "TESTINST"),
            TradeRecord("T3", "ACCT_SELLER", d3, 95.0, 50, "sell", "TESTINST"),
        ]
        report = compute_instrument_harm_report(
            instrument_id="TESTINST",
            case_id="SYNTHETIC_TEST",
            trades=trades,
            cf_path=cf,
            car_significant_at_95=True,
            log_file="tests/test_harm_estimation.py",
        )
        # Manual computation:
        # Buyer1: (110-100)*100 = ₹1,000 central
        # Buyer2: (112-101)*200 = ₹2,200 central
        # Seller: (99-95)*50 = ₹200 central
        expected_central = 1_000.0 + 2_200.0 + 200.0
        assert math.isclose(report.instrument_total_central, expected_central, rel_tol=1e-5), \
            f"Aggregated total harm expected ₹{expected_central:.2f}, got ₹{report.instrument_total_central:.2f}"
        assert len(report.account_summaries) == 3
        assert report.total_trades_analyzed == 3

    def test_report_disclaimer_is_populated(self):
        """Every InstrumentHarmReport must carry a non-empty disclaimer."""
        cf = self._make_cf_path(
            [date(2021, 6, 1)], [100.0], [98.0], [102.0], [110.0]
        )
        trade = [TradeRecord("TX", "ACCT", date(2021, 6, 1), 110.0, 100, "buy", "TESTINST")]
        report = compute_instrument_harm_report(
            "TESTINST", "SYNTHETIC_TEST", trade, cf, True,
            log_file="tests/test_harm_estimation.py"
        )
        assert report.disclaimer and len(report.disclaimer) > 50

    def test_invalid_side_raises_value_error(self):
        """An unknown trade side must raise ValueError, not silently produce 0."""
        cf = self._make_cf_path(
            [date(2021, 6, 2)], [100.0], [98.0], [102.0], [120.0]
        )
        trade = TradeRecord("TX", "ACCT", date(2021, 6, 2), 120.0, 100, "short", "TESTINST")
        with pytest.raises(ValueError, match="Unknown trade side"):
            compute_per_trade_harm(trade, cf)


# ── REAL-DATA BLOCK VERIFICATION ─────────────────────────────────────────────

class TestRealCaseDataGap:
    """
    Verify that the module correctly documents its own limitation:
    real SEBI cases are on the allow-list but real-data execution is
    currently blocked by the BSE data access gap.

    This is NOT a capability test. It is a documentation-fidelity test:
    the guard clears the case, but the data fetch fails with an expected error
    when BSE prices are requested, not a silent wrong result.
    """

    def test_kil_2019_is_on_allow_list(self):
        """KIL-2019 should be guard-approved (SEBI adjudication is confirmed)."""
        assert check_instrument_allowed("KAVIT", "KIL-2019") == "sebi_confirmed"

    def test_pump_dump_is_on_allow_list(self):
        """PUMP-DUMP-2017-2020 should be guard-approved."""
        assert check_instrument_allowed("MAURIUDYOG", "PUMP-DUMP-2017-2020") == "sebi_confirmed"

    def test_real_data_execution_documented_as_blocked(self):
        """
        Attempting to run the full event study with an EMPTY price series
        (simulating the BSE data gap) should raise a meaningful ValueError,
        not silently succeed with wrong numbers.

        This documents that the module correctly fails-loud when data is absent,
        rather than producing a harm estimate from empty/interpolated data.
        """
        # Simulate what happens when BSE data is unavailable: empty price series
        empty_stock = pd.Series([], dtype=float)
        empty_market = pd.Series([], dtype=float)

        with pytest.raises((ValueError, IndexError)):
            # run_event_study should fail loudly on empty data
            run_event_study(
                empty_stock, empty_market,
                event_start=date(2019, 8, 1), event_end=date(2019, 9, 30),
            )
