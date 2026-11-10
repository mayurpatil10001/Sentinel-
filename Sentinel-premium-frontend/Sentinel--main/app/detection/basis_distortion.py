"""
Basis Distortion Detector
==========================

Detects futures trading far outside their theoretical fair value relative
to the spot price — a pattern consistent with basis manipulation.

What is the futures basis?
---------------------------
The basis is the difference between a futures price and its spot price:
  basis = futures_price - spot_price

In an efficient market, the fair-value basis is:
  fair_value_basis = spot × (risk_free_rate × days_to_expiry / 365)
  (the cost-of-carry model; dividends and borrow costs complicate this
   for individual stocks but the principle holds for index futures)

Basis distortion occurs when:
  1. Futures trade at an abnormally large PREMIUM to spot (positive basis
     >> fair value) — consistent with artificial buying in futures to
     create an appearance of bullish momentum.
  2. Futures trade at an abnormally large DISCOUNT to spot (negative basis
     >> fair value) — consistent with artificial selling in futures to
     create bearish pressure or depress mark-to-market for derivatives.
  3. The basis swings sharply intraday without a corresponding spot move —
     suggesting futures are being used as the manipulation vehicle rather
     than a price discovery mechanism.

Algorithm
----------
1. Compute the theoretical fair-value basis using cost-of-carry:
   FV = spot × RFR × DTE / 365
2. Compute the actual basis: futures_price - spot_price
3. Compute the basis deviation: actual_basis - FV
4. Express deviation as % of spot price
5. Flag if |deviation| >= BASIS_DEVIATION_THRESHOLD

Threshold documentation (HARD RULE #2)
-----------------------------------------
BASIS_DEVIATION_THRESHOLD = 0.005 (0.5% of spot)
  On liquid index futures (NIFTY, BANKNIFTY), the basis rarely deviates
  more than 0.2% from fair value intraday under normal conditions.
  0.5% is a conservative starting point for flagging.
  Source: Informal observation of NSE NIFTY futures vs index levels.
  Label: UNVALIDATED GUESS — needs statistical analysis of historical
  basis distributions for calibration.

RISK_FREE_RATE = 0.065 (6.5% annualised)
  Approximate RBI repo rate as of 2024. This changes with RBI policy.
  Label: APPROXIMATE — update with current RBI repo rate.
  For individual stock futures, dividend yield and borrow costs should
  also be included, but we omit them here (label: SIMPLIFICATION).

Real-data status
-----------------
Operates on live futures price (from order stream or exchange feed) and
spot price (from option chain's underlying_value or bhavcopy close).
No synthetic fallback — raises if inputs are missing.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger(__name__)


# ── Thresholds ────────────────────────────────────────────────────────────────

# |actual_basis - fair_value_basis| / spot_price to flag basis distortion.
# UNVALIDATED GUESS — needs calibration from historical basis distributions.
BASIS_DEVIATION_THRESHOLD: float = 0.005  # 0.5% of spot

# Annualised risk-free rate (RBI repo rate approximation, 2024).
# APPROXIMATE — update with current RBI rate. Ignores dividends (SIMPLIFICATION).
RISK_FREE_RATE: float = 0.065

# NSE trading days per year (approximate, accounting for holidays).
TRADING_DAYS_PER_YEAR: int = 252


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class BasisDistortionSignal:
    """
    Flagged when futures trade materially outside their theoretical fair value.
    """
    symbol: str               # underlying symbol (e.g. NIFTY, RELIANCE)
    exchange: str
    snapshot_time: datetime
    expiry_date: date

    spot_price: float
    futures_price: float
    actual_basis: float       # futures_price - spot_price
    fair_value_basis: float   # spot × RFR × DTE/365
    basis_deviation: float    # actual_basis - fair_value_basis
    deviation_pct: float      # basis_deviation / spot_price

    days_to_expiry: int
    risk_free_rate_used: float  # for reproducibility / audit

    direction: str            # "contango_excess" or "backwardation_excess"
    score: float
    severity: str
    explanation: str = ""


def _severity(score: float) -> str:
    if score >= 0.85: return "critical"
    if score >= 0.65: return "high"
    if score >= 0.45: return "medium"
    return "low"


def detect_basis_distortion(
    symbol: str,
    exchange: str,
    spot_price: float,
    futures_price: float,
    expiry_date: date,
    snapshot_time: datetime,
    risk_free_rate: float = RISK_FREE_RATE,
    basis_deviation_threshold: float = BASIS_DEVIATION_THRESHOLD,
) -> Optional[BasisDistortionSignal]:
    """
    Detect if a futures contract is trading materially outside its theoretical
    fair value relative to the spot price.

    Parameters
    ----------
    symbol
        The underlying symbol (e.g. "NIFTY", "RELIANCE").
    exchange
        "NSE" or "BSE".
    spot_price
        Current spot price of the underlying. For indices, this is the
        index level. For stocks, this is the cash market price.
    futures_price
        Current traded price of the near-month futures contract.
    expiry_date
        The futures contract's expiry date (last Thursday of the month
        for NSE equity/index futures).
    snapshot_time
        When this price snapshot was taken.
    risk_free_rate
        Annualised risk-free rate. Default = 6.5% (RBI repo rate approx 2024).
        Pass the current rate explicitly in production.
    basis_deviation_threshold
        Fraction of spot price that triggers a flag. Default = 0.005 (0.5%).

    Returns
    -------
    BasisDistortionSignal | None
        Signal if basis deviation exceeds threshold, else None.

    HARD RULE #1: raises ValueError for invalid inputs (no synthetic fallback).
    HARD RULE #3: returned signal includes full explanation of the calculation.
    """
    import math

    # NaN inputs must be rejected explicitly: NaN comparisons (e.g. NaN <= 0)
    # are always False in Python, so NaN would silently pass the positivity check
    # and produce a NaN fair_value_basis — which would never trigger a signal.
    if isinstance(spot_price, float) and math.isnan(spot_price):
        raise ValueError(
            f"spot_price is NaN — cannot compute fair value basis. "
            "Pass a valid positive spot price."
        )
    if isinstance(futures_price, float) and math.isnan(futures_price):
        raise ValueError(
            f"futures_price is NaN — cannot compute fair value basis. "
            "Pass a valid positive futures price."
        )
    if spot_price <= 0:
        raise ValueError(
            f"spot_price must be positive, got {spot_price}. "
            "Do not pass zero or negative spot prices."
        )
    if futures_price <= 0:
        raise ValueError(
            f"futures_price must be positive, got {futures_price}."
        )
    if expiry_date < snapshot_time.date():
        raise ValueError(
            f"expiry_date {expiry_date} is in the past "
            f"(snapshot_time: {snapshot_time.date()}). "
            "Pass the correct expiry date."
        )


    days_to_expiry = (expiry_date - snapshot_time.date()).days

    # Fair value basis (cost-of-carry model)
    # FV = spot × RFR × DTE / 365
    # Note: this omits dividend yield (simplification; matters for stocks with
    # known ex-dividend dates). Index futures implicitly include dividends in
    # the index level, so this approximation is better for NIFTY than for stocks.
    fair_value_basis = spot_price * risk_free_rate * (days_to_expiry / 365.0)

    actual_basis = futures_price - spot_price
    basis_deviation = actual_basis - fair_value_basis
    deviation_pct = abs(basis_deviation) / spot_price

    if deviation_pct < basis_deviation_threshold:
        return None

    direction = (
        "contango_excess" if basis_deviation > 0
        else "backwardation_excess"
    )

    # Score: proportional to how far the deviation exceeds the threshold.
    # Capped at 1.0. Larger deviations = stronger signal.
    # No other weighting — basis distortion is a relatively clean signal
    # (unlike OI or volume, which have many innocent explanations).
    score = min(1.0, (deviation_pct - basis_deviation_threshold) / (basis_deviation_threshold * 4))

    direction_desc = {
        "contango_excess": (
            "futures are trading at an ABNORMAL PREMIUM to spot "
            "(excess contango). Consistent with artificial buying pressure "
            "in futures to create bullish momentum signals."
        ),
        "backwardation_excess": (
            "futures are trading at an ABNORMAL DISCOUNT to spot "
            "(excess backwardation). Consistent with artificial selling "
            "pressure in futures to depress mark-to-market prices or "
            "create bearish momentum signals."
        ),
    }[direction]

    explanation = (
        f"Basis distortion detected for {symbol} futures ({exchange}), "
        f"expiry {expiry_date} ({days_to_expiry} days to expiry). "
        f"Spot: {spot_price:,.2f}. "
        f"Futures: {futures_price:,.2f}. "
        f"Actual basis: {actual_basis:+.2f}. "
        f"Fair-value basis (cost-of-carry at {risk_free_rate*100:.1f}% RFR, "
        f"{days_to_expiry} DTE): {fair_value_basis:+.2f}. "
        f"Deviation: {basis_deviation:+.2f} ({deviation_pct*100:.3f}% of spot). "
        f"Threshold: {basis_deviation_threshold*100:.2f}%. "
        f"Interpretation: {direction_desc} "
        f"Caveats: (1) This model omits dividend yield — accuracy degrades "
        f"for stocks with near-term ex-dividend dates. "
        f"(2) The threshold ({basis_deviation_threshold*100:.2f}%) is an "
        f"unvalidated estimate — the actual threshold should be calibrated "
        f"from historical basis distributions for each instrument separately."
    )

    return BasisDistortionSignal(
        symbol=symbol,
        exchange=exchange,
        snapshot_time=snapshot_time,
        expiry_date=expiry_date,
        spot_price=spot_price,
        futures_price=futures_price,
        actual_basis=actual_basis,
        fair_value_basis=fair_value_basis,
        basis_deviation=basis_deviation,
        deviation_pct=deviation_pct,
        days_to_expiry=days_to_expiry,
        risk_free_rate_used=risk_free_rate,
        direction=direction,
        score=score,
        severity=_severity(score),
        explanation=explanation,
    )
