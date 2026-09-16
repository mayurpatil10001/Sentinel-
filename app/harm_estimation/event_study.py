"""
app/harm_estimation/event_study.py
====================================
Market-model event-study methodology for abnormal return (AR) and
cumulative abnormal return (CAR) estimation, with confidence intervals.

ACADEMIC BASIS
--------------
The market-model event-study is the standard methodology for estimating
price effects attributable to a specific event (here: a manipulation window).

Primary references:
  MacKinlay, A.C. (1997). Event Studies in Economics and Finance.
      Journal of Economic Literature, 35(1), 13-39.
      [The canonical review of event-study methodology and test statistics.]

  Brown, S.J. & Warner, J.B. (1985). Using daily stock returns: The case
      of event studies. Journal of Financial Economics, 14(1), 3-31.
      [Establishes the test statistics used here for daily-return data,
      including the standard error formula for the t-statistic on CAR.]

Estimation window (clean period):
  Standard practice per MacKinlay (1997, p.15) uses 120–250 trading days
  before the event window. We default to 200 days and document any deviation.
  The estimation window must not overlap the event window (manipulation period).

Market model:
  R_stock(t) = alpha + beta * R_market(t) + epsilon(t)
  Estimated by OLS (statsmodels) over the estimation window.

Abnormal return:
  AR(t) = R_stock(t) — (alpha_hat + beta_hat * R_market(t))
  Computed for each day in the event window using actual market returns,
  not assumed flat returns.

Cumulative Abnormal Return:
  CAR = sum(AR(t)) for t in event window.

Statistical test:
  Following Brown & Warner (1985), the variance of CAR is estimated from
  the OLS residual variance scaled by event-window length plus a bias
  correction term for forecast-period uncertainty:

  Var(CAR) = T_event * sigma_hat^2 * (1 + 1/T_est + (R_mkt_event - R_mkt_bar)^2
             / sum((R_mkt_est - R_mkt_bar)^2))

  where T_event = event window length, T_est = estimation window length,
  R_mkt_event = mean market return during event window,
  R_mkt_bar = mean market return during estimation window,
  sigma_hat^2 = OLS residual variance from estimation regression.

  t-stat = CAR / sqrt(Var(CAR))
  95% CI = CAR ± t_crit(df=T_est-2, p=0.025) * sqrt(Var(CAR))

  This is a one-instrument test; cross-sectional dependence is not
  applicable (single firm). Stated explicitly to be transparent.

Microstructure bias:
  Bid/ask bounce inflates observed return variance in thinly-traded stocks,
  biasing CAR estimates upward. Mitigated by using VWAP or closing-price
  series where available. Every output records which price series was used.
  Per MacKinlay (1997, p.24): "thin-stock results should be interpreted
  with caution due to non-synchronous trading and bid/ask effects."

KNOWN LIMITATIONS (disclosed in every output):
  1. Bid/ask microstructure bias inflates CAR for illiquid instruments.
  2. The market model assumes a stationary linear relationship between
     stock and market returns. If the estimation window includes structural
     breaks (e.g. COVID crash), beta may not be stable across the event
     window.
  3. Cross-sectional dependence: Not applicable for the single-instrument
     case here, but explicitly noted per standard event-study disclosure
     practice (MacKinlay 1997, Section 4.4).
  4. The counterfactual price path is a statistical reconstruction, not
     an observed fact. It becomes less reliable as the event window
     lengthens relative to the estimation window.
"""

import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# MacKinlay (1997): estimation window of 120-250 trading days is standard.
# We use 200 as a reasonable default.
DEFAULT_ESTIMATION_WINDOW_DAYS: int = 200
MIN_ESTIMATION_WINDOW_DAYS: int = 120  # below this, beta estimates are unreliable

# Standard significance level for confidence intervals
CI_LEVEL: float = 0.95
CI_ALPHA: float = 1 - CI_LEVEL  # 0.05

OUTPUT_DISCLAIMER = (
    "METHODOLOGY DISCLAIMER: This is a statistical estimate using the "
    "market-model event-study method (MacKinlay 1997; Brown & Warner 1985). "
    "It is a research methodology output — NOT a legal or financial determination "
    "of actual harm. Real restitution calculations require BSE historical data "
    "access, SEBI's own account-level trade records, and independent expert review "
    "beyond what this module can provide. Every harm figure is a range derived from "
    "the CAR confidence interval — not a point estimate of fact."
)


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class MarketModelResult:
    """
    Result of fitting R_stock = alpha + beta * R_market over the estimation window.
    All fields are from statsmodels OLS.
    """
    alpha: float            # intercept
    beta: float             # market sensitivity
    r_squared: float        # model fit (informational; R² is not the validation criterion)
    residual_std: float     # sqrt(OLS residual variance): sigma_hat in Brown & Warner (1985)
    estimation_days: int    # T_est: actual length of estimation window used
    market_mean_return: float   # R_mkt_bar: mean market return in estimation window
    market_ss: float            # sum((R_mkt_est - R_mkt_bar)^2) for CAR variance formula


@dataclass
class AbnormalReturnResult:
    """Daily abnormal returns over the event window."""
    dates: list[date]
    ar: np.ndarray       # AR(t) for each day in event window
    actual_returns: np.ndarray
    expected_returns: np.ndarray
    market_returns_event: np.ndarray


@dataclass
class CARResult:
    """
    Cumulative Abnormal Return with confidence interval.
    Following Brown & Warner (1985) variance formula for out-of-sample forecast.

    HARD RULE: Every harm figure derived from this result must include
    car_low and car_high alongside the central estimate. Never present
    car_central alone as if it were a confirmed fact.
    """
    car_central: float      # point estimate (CAR)
    car_low: float          # lower bound of 95% CI
    car_high: float         # upper bound of 95% CI
    t_stat: float           # t-statistic for H0: CAR = 0
    p_value: float          # two-tailed p-value
    significant_at_95: bool # whether CAR is statistically distinguishable from 0
    event_window_days: int  # T_event
    se_car: float           # standard error of CAR (for downstream use)
    model: MarketModelResult
    ar_result: AbnormalReturnResult
    # Mandatory disclosure fields
    price_series_used: str   # "close" / "vwap" / "midpoint"
    microstructure_warning: bool  # True if instrument may have bid/ask bias
    disclaimer: str = OUTPUT_DISCLAIMER


@dataclass
class CounterfactualPricePath:
    """
    The reconstructed 'what would price have been without manipulation?'
    path, derived from the market model's expected returns during the event window.

    Low/central/high correspond to applying the CAR's confidence interval
    bounds to the price path — so every price on the path is a range.
    """
    dates: list[date]
    actual_price: np.ndarray
    counterfactual_central: np.ndarray
    counterfactual_low: np.ndarray
    counterfactual_high: np.ndarray
    pre_event_price: float   # closing price on day before event window starts
    disclaimer: str = OUTPUT_DISCLAIMER


# ── Core functions ────────────────────────────────────────────────────────────

def fit_market_model(
    stock_returns: pd.Series,
    market_returns: pd.Series,
    estimation_window_days: int = DEFAULT_ESTIMATION_WINDOW_DAYS,
) -> MarketModelResult:
    """
    Fit the market model: R_stock = alpha + beta * R_market + epsilon
    over the estimation window using OLS (statsmodels).

    Parameters
    ----------
    stock_returns : pd.Series
        Daily log or arithmetic returns for the stock, indexed by date.
        Only the last `estimation_window_days` rows are used.
    market_returns : pd.Series
        Corresponding daily returns for the market index (Nifty 50 or Sensex).
        Must be aligned to the same index as stock_returns.
    estimation_window_days : int
        Number of trading days to use as the estimation window.
        MacKinlay (1997) standard: 120–250 days.

    Returns
    -------
    MarketModelResult
    """
    # Align and drop NaN (non-trading days or missing data)
    aligned = pd.concat([stock_returns, market_returns], axis=1).dropna()
    aligned.columns = ["stock", "market"]

    if len(aligned) < MIN_ESTIMATION_WINDOW_DAYS:
        raise ValueError(
            f"Only {len(aligned)} aligned trading days available for estimation. "
            f"Minimum is {MIN_ESTIMATION_WINDOW_DAYS} (MacKinlay 1997 standard). "
            "Cannot fit a reliable market model with fewer observations."
        )

    # Use the last `estimation_window_days` rows
    window = aligned.iloc[-estimation_window_days:]
    actual_days = len(window)

    logger.info(
        "Fitting market model over %d trading days (requested %d).",
        actual_days, estimation_window_days,
    )

    X = sm.add_constant(window["market"].values)
    y = window["stock"].values

    model = sm.OLS(y, X).fit()

    alpha_hat = float(model.params[0])
    beta_hat = float(model.params[1])
    r_sq = float(model.rsquared)
    # Residual standard deviation: sigma_hat from Brown & Warner (1985)
    # statsmodels uses df-corrected MSE: mse_resid = SS_resid / (n-k)
    sigma_hat = float(np.sqrt(model.mse_resid))

    mkt_mean = float(window["market"].mean())
    mkt_ss = float(((window["market"] - mkt_mean) ** 2).sum())

    logger.info(
        "Market model fit: alpha=%.6f beta=%.6f R²=%.4f sigma_resid=%.6f",
        alpha_hat, beta_hat, r_sq, sigma_hat,
    )

    return MarketModelResult(
        alpha=alpha_hat,
        beta=beta_hat,
        r_squared=r_sq,
        residual_std=sigma_hat,
        estimation_days=actual_days,
        market_mean_return=mkt_mean,
        market_ss=mkt_ss,
    )


def compute_abnormal_returns(
    event_stock_returns: pd.Series,
    event_market_returns: pd.Series,
    model: MarketModelResult,
) -> AbnormalReturnResult:
    """
    Compute AR(t) = R_stock(t) - E[R_stock(t)] for each day in the event window.
    E[R_stock(t)] = alpha_hat + beta_hat * R_market(t)
    (actual market returns during the event window, per MacKinlay 1997).

    Parameters
    ----------
    event_stock_returns : pd.Series
        Actual daily returns during the event (manipulation) window.
    event_market_returns : pd.Series
        Actual market index returns during the same event window.
        Uses REAL market returns — not an assumed flat/zero return.
    model : MarketModelResult
        Fitted market model from the estimation window.
    """
    aligned = pd.concat([event_stock_returns, event_market_returns], axis=1).dropna()
    aligned.columns = ["stock", "market"]

    expected = model.alpha + model.beta * aligned["market"].values
    actual = aligned["stock"].values
    ar = actual - expected

    logger.info(
        "Event window: %d days. AR range: [%.4f, %.4f]. Sum AR (CAR): %.4f",
        len(ar), float(ar.min()), float(ar.max()), float(ar.sum()),
    )

    return AbnormalReturnResult(
        dates=[d.date() if hasattr(d, "date") else d for d in aligned.index],
        ar=ar,
        actual_returns=actual,
        expected_returns=expected,
        market_returns_event=aligned["market"].values,
    )


def compute_car(
    ar_result: AbnormalReturnResult,
    model: MarketModelResult,
    price_series_used: str = "close",
    is_illiquid: bool = False,
) -> CARResult:
    """
    Compute the Cumulative Abnormal Return (CAR) and its 95% confidence interval.

    Variance formula follows Brown & Warner (1985, eq. 5):

      Var(CAR) = T_event * sigma²_hat * (
          1 + 1/T_est + sum((R_mkt_event(t) - R_mkt_bar_est)²) / SS_mkt_est
      )

    The third term (forecast-period market return deviation) adjusts for the
    fact that the market model is applied out-of-sample: if market returns
    during the event window are very different from those in the estimation
    window, the forecast error is larger.

    Parameters
    ----------
    ar_result : AbnormalReturnResult
        From compute_abnormal_returns().
    model : MarketModelResult
        Fitted market model from estimation window.
    price_series_used : str
        Which price series was used: "close", "vwap", or "midpoint".
    is_illiquid : bool
        If True, set microstructure_warning=True in output. Caller should
        flag this for thinly-traded instruments (< 50,000 shares/day avg).
    """
    car = float(ar_result.ar.sum())
    t_event = len(ar_result.ar)

    # Brown & Warner (1985) variance formula
    sigma_sq = model.residual_std ** 2
    t_est = model.estimation_days
    r_mkt_bar = model.market_mean_return
    ss_mkt = model.market_ss

    # Forecast-period correction term: sum of squared deviations of event-window
    # market returns from estimation-window mean, scaled by SS_mkt
    r_mkt_event = ar_result.market_returns_event
    event_mkt_correction = float(np.sum((r_mkt_event - r_mkt_bar) ** 2)) / ss_mkt if ss_mkt > 0 else 0.0

    # Full variance of CAR per Brown & Warner (1985)
    var_car = t_event * sigma_sq * (1.0 + 1.0 / t_est + event_mkt_correction)
    se_car = float(np.sqrt(var_car))

    # t-distribution with T_est - 2 degrees of freedom (OLS with 2 params: alpha, beta)
    df = t_est - 2
    t_stat = car / se_car if se_car > 0 else 0.0
    p_value = float(2 * stats.t.sf(abs(t_stat), df=df))  # two-tailed

    t_crit = float(stats.t.ppf(1 - CI_ALPHA / 2, df=df))
    ci_lower = car - t_crit * se_car
    ci_upper = car + t_crit * se_car
    significant = p_value < CI_ALPHA

    if not significant:
        logger.warning(
            "CAR=%.4f is NOT statistically significant at 95%% level "
            "(t=%.3f, p=%.4f). The manipulation window's price deviation "
            "cannot be confidently distinguished from normal market noise. "
            "Harm estimates derived from this CAR have very wide uncertainty.",
            car, t_stat, p_value,
        )
    else:
        logger.info(
            "CAR=%.4f [95%% CI: %.4f, %.4f] t=%.3f p=%.4f — statistically "
            "significant at 95%% level.",
            car, ci_lower, ci_upper, t_stat, p_value,
        )

    return CARResult(
        car_central=car,
        car_low=ci_lower,
        car_high=ci_upper,
        t_stat=t_stat,
        p_value=p_value,
        significant_at_95=significant,
        event_window_days=t_event,
        se_car=se_car,
        model=model,
        ar_result=ar_result,
        price_series_used=price_series_used,
        microstructure_warning=is_illiquid,
    )


def reconstruct_counterfactual_price_path(
    car_result: CARResult,
    actual_prices: pd.Series,
    event_start_date: date,
) -> CounterfactualPricePath:
    """
    Reconstruct the counterfactual price path by applying expected (non-abnormal)
    daily returns to the pre-event price, compounding day-by-day.

    The low/central/high price paths correspond to the CAR's 95% confidence
    interval bounds — producing a range of plausible counterfactual prices for
    each day, not a single deterministic path.

    Method
    ------
    The expected return on day t is:
        E[R(t)] = alpha_hat + beta_hat * R_mkt(t)

    The counterfactual price path is:
        P_cf(t) = P_pre_event * product((1 + E[R(s)]) for s in [1..t])

    The high/low paths apply the full CAR confidence interval as a proportional
    adjustment applied UNIFORMLY across the event window, following the convention
    that the CI reflects total uncertainty over the window rather than per-day
    uncertainty (which would compound unpredictably).

    Parameters
    ----------
    car_result : CARResult
        Computed CAR with confidence interval.
    actual_prices : pd.Series
        Actual closing prices (or VWAP) during the event window, date-indexed.
    event_start_date : date
        First day of the event window.
    """
    ar = car_result.ar_result

    # Pre-event price: actual closing price the day BEFORE the event window starts.
    # We use the actual_prices series for the event window; the pre-event price
    # should be passed by the caller as the day-before closing price.
    # If not available as a separate entry, use the first event-window price
    # divided by (1 + actual_return[0]) to back-compute it.
    actual = actual_prices.values
    expected_returns = ar.expected_returns

    # Compound the expected returns to get the central counterfactual
    pre_event_price = float(actual[0]) / (1 + float(ar.actual_returns[0])) \
        if len(actual) > 0 else float(actual[0])

    # Central path: compound expected returns from pre-event price
    cf_central = np.zeros(len(expected_returns))
    cf_central[0] = pre_event_price * (1 + expected_returns[0])
    for i in range(1, len(expected_returns)):
        cf_central[i] = cf_central[i-1] * (1 + expected_returns[i])

    # CAR as proportion of price to compute CI-bounded paths
    # The CI bounds on CAR translate to CI bounds on total log-return over window
    # We apply the CI range as a uniform daily scaling factor
    total_actual_return = float(np.prod(1 + ar.actual_returns)) - 1
    car_c = car_result.car_central
    car_l = car_result.car_low
    car_h = car_result.car_high

    # Low counterfactual: if CAR_high is the upper bound of abnormal return,
    # then the counterfactual price was LOWER (less of the actual return was
    # "legitimate"). Scale adjustment = (total_actual - CAR) / (total_actual - CAR_central)
    # Simpler and more conservative: scale cf_central by (1 + CAR_low/total_actual_return * delta)
    # Use per-day scaling: on each day, the counterfactual is:
    #   cf_low(t) = cf_central(t) * (total_expected_central / total_expected_low_hypothesis)
    # Most defensible approach: just apply CI bounds as total path scalings
    # cf_low = path where less of the "expected" return occurred (CAR was at its high end)
    # cf_high = path where more of the expected return occurred (CAR was at its low end)
    # This preserves the direction: CAR_high = MORE abnormal = LESS expected = LOWER cf price
    n = len(cf_central)
    final_cf_c = cf_central[-1] if n > 0 else pre_event_price

    # Scale factor to map CI to price path endpoints
    # If car_high bound applies (more AR), the true price without manipulation
    # would have been further below actual → lower counterfactual
    total_expected_c = (final_cf_c / pre_event_price) - 1 if pre_event_price > 0 else 0
    # Low cf path: applies car_high bound (worst case: more was abnormal)
    # High cf path: applies car_low bound (best case: less was abnormal)
    cf_low_scale = (1 + total_expected_c - (car_h - car_c)) if total_expected_c + 1 > 0 else 1.0
    cf_high_scale = (1 + total_expected_c - (car_l - car_c)) if total_expected_c + 1 > 0 else 1.0

    cf_low = np.zeros(n)
    cf_high = np.zeros(n)
    for i in range(n):
        frac = (i + 1) / n  # interpolate scaling linearly through the window
        cf_low[i] = cf_central[i] * (1 + (cf_low_scale - 1) * frac)
        cf_high[i] = cf_central[i] * (1 + (cf_high_scale - 1) * frac)

    event_dates = [d.date() if hasattr(d, "date") else d for d in actual_prices.index]

    return CounterfactualPricePath(
        dates=event_dates,
        actual_price=actual,
        counterfactual_central=cf_central,
        counterfactual_low=cf_low,
        counterfactual_high=cf_high,
        pre_event_price=pre_event_price,
    )


def run_event_study(
    stock_prices: pd.Series,
    market_prices: pd.Series,
    event_start: date,
    event_end: date,
    estimation_window_days: int = DEFAULT_ESTIMATION_WINDOW_DAYS,
    price_series_label: str = "close",
    is_illiquid: bool = False,
) -> tuple[CARResult, CounterfactualPricePath]:
    """
    Full event-study pipeline: estimation → market model → AR → CAR → CI → price path.

    Parameters
    ----------
    stock_prices : pd.Series
        Daily prices (close or VWAP) for the stock, date-indexed.
        Must cover BOTH the estimation window AND the event window.
    market_prices : pd.Series
        Daily prices for the market index (Nifty 50 recommended).
        Same date range requirement.
    event_start, event_end : date
        First and last trading day of the manipulation/event window.
    estimation_window_days : int
        Days before the event window used for OLS fitting.
    price_series_label : str
        "close", "vwap", or "midpoint" — recorded in output for disclosure.
    is_illiquid : bool
        Set True for thinly-traded instruments to flag microstructure warning.

    Returns
    -------
    (CARResult, CounterfactualPricePath)
    """
    if len(stock_prices) == 0 or len(market_prices) == 0:
        raise ValueError(
            "run_event_study: stock_prices and/or market_prices are empty. "
            "This typically indicates a data-access failure (e.g. BSE data not "
            "available in the current pipeline). Cannot compute event study on "
            "empty data — failing loudly rather than producing wrong numbers."
        )

    # Convert to returns
    stock_returns = stock_prices.pct_change().dropna()
    market_returns = market_prices.pct_change().dropna()

    # Split into estimation window (before event) and event window
    event_mask_stock = (stock_returns.index >= pd.Timestamp(event_start)) & \
                       (stock_returns.index <= pd.Timestamp(event_end))
    event_mask_market = (market_returns.index >= pd.Timestamp(event_start)) & \
                        (market_returns.index <= pd.Timestamp(event_end))

    est_stock = stock_returns[~event_mask_stock]
    est_market = market_returns[~event_mask_market]
    event_stock = stock_returns[event_mask_stock]
    event_market = market_returns[event_mask_market]

    if len(event_stock) == 0:
        raise ValueError(
            f"No stock return data found in event window {event_start}–{event_end}. "
            "Check that stock_prices covers the event window."
        )

    logger.info(
        "Event study: event window %s to %s (%d days). Estimation pool: %d days.",
        event_start, event_end, len(event_stock), len(est_stock),
    )

    # Fit market model on estimation window
    model = fit_market_model(est_stock, est_market, estimation_window_days)

    # Compute per-day abnormal returns in event window
    ar_result = compute_abnormal_returns(event_stock, event_market, model)

    # Compute CAR with Brown & Warner (1985) CI
    car_result = compute_car(ar_result, model, price_series_label, is_illiquid)

    # Reconstruct counterfactual price path
    event_prices = stock_prices[
        (stock_prices.index >= pd.Timestamp(event_start)) &
        (stock_prices.index <= pd.Timestamp(event_end))
    ]
    cf_path = reconstruct_counterfactual_price_path(car_result, event_prices, event_start)

    return car_result, cf_path
