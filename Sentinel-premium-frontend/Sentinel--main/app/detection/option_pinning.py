"""
Option Pinning Detector
========================

Detects when the spot price of an underlying is being held near a specific
option strike as expiry approaches — the "max pain" or "pin risk" pattern.

What is option pinning?
------------------------
Near options expiry, market makers (who are net short options) benefit from
the underlying expiring at a strike price where total option payoff is
minimised — the "max pain" point. Some participants (or the market makers
themselves) may trade in the spot market to nudge the underlying toward
a specific strike.

Indicators of pinning:
  1. Spot price is abnormally close to a high-OI strike as expiry approaches.
  2. Spot oscillates around that strike (bouncing off it repeatedly rather
     than trending through it).
  3. The pinning strike has materially higher OI than adjacent strikes.
  4. Effect intensifies in the last 1-2 trading days before expiry.

This detector measures:
  (a) Distance from spot to the highest-OI strike at expiry, as a % of spot.
  (b) Days to expiry — signal is stronger closer to expiry.
  (c) OI dominance of the pinning strike vs. adjacent strikes.

Threshold documentation (HARD RULE #2)
-----------------------------------------
PIN_DISTANCE_THRESHOLD = 0.005 (0.5% of spot)
  Spot within 0.5% of a high-OI strike is "near" it.
  For NIFTY at 21,500: 0.5% = ±107 points.
  Label: HEURISTIC — no formal citation. Adjust for each instrument's
  tick size and typical intraday range.

PIN_EXPIRY_DAYS_THRESHOLD = 2
  Pinning is most meaningful in the last 2 trading days before expiry.
  Outside this window, proximity to a strike is normal price action.
  Label: HEURISTIC — based on where gamma-squeeze risk is highest.

PIN_OI_DOMINANCE_THRESHOLD = 2.0
  The pinning strike's OI must be >= 2× the average OI of adjacent strikes
  for the proximity to be suspicious (otherwise the strike is just "near"
  a randomly placed strike, not one with genuine open interest).
  Label: HEURISTIC.

False-positive risk
--------------------
High-OI strikes near ATM exist on every expiry day — this is normal.
The detector intentionally requires ALL THREE conditions (proximity,
short DTE, AND OI dominance) to trigger. Even so, legitimate gamma
exposure by market makers creates the same pattern as intentional pinning.
This detector should be treated as a "watch" flag requiring manual review,
not an automatic escalation signal.

Real-data status
-----------------
Operates on option chain DataFrames from nse_option_chain and a spot price.
No synthetic fallback — raises ValueError on empty input.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, date
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ── Thresholds ────────────────────────────────────────────────────────────────

# Spot within this fraction of a high-OI strike = "near" it.
# HEURISTIC.
PIN_DISTANCE_THRESHOLD: float = 0.005  # 0.5% of spot

# Only flag pinning within this many calendar days of expiry.
# HEURISTIC.
PIN_EXPIRY_DAYS_THRESHOLD: int = 2

# Pinning strike OI must be this multiple of adjacent strikes' average OI.
# HEURISTIC.
PIN_OI_DOMINANCE_THRESHOLD: float = 2.0

# Minimum OI at the pinning strike to be meaningful.
# HEURISTIC.
MIN_PIN_STRIKE_OI: int = 20_000


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class OptionPinningSignal:
    """
    Flagged when spot is being held near a high-OI strike close to expiry.
    """
    symbol: str
    exchange: str
    snapshot_time: datetime
    expiry_date: date

    spot_price: float
    pin_strike: float
    distance_pct: float          # |spot - pin_strike| / spot
    pin_strike_total_oi: int     # combined CE + PE OI at the strike
    adjacent_avg_oi: float       # average OI of ±1 adjacent strikes
    oi_dominance_ratio: float    # pin_strike_oi / adjacent_avg_oi
    days_to_expiry: int
    max_pain_strike: float       # theoretical max-pain point (for context)

    score: float
    severity: str
    explanation: str = ""


def _severity(score: float) -> str:
    if score >= 0.85: return "critical"
    if score >= 0.65: return "high"
    if score >= 0.45: return "medium"
    return "low"


def _compute_max_pain(chain_df: pd.DataFrame, strikes: list) -> float:
    """
    Compute the theoretical max-pain strike (where total option payout
    to buyers is minimised if underlying expires at that level).

    Max pain = argmin over strikes of:
      sum_{all CE strikes < S} (S - K) × CE_OI
    + sum_{all PE strikes > S} (K - S) × PE_OI

    This is a reference calculation for context, not the detection signal itself.
    """
    min_pain = float("inf")
    max_pain_strike = strikes[0] if strikes else 0.0

    ce_df = chain_df[chain_df["option_type"] == "CE"][["strike", "oi"]].dropna()
    pe_df = chain_df[chain_df["option_type"] == "PE"][["strike", "oi"]].dropna()

    for candidate in strikes:
        # CE pain: for each CE strike <= candidate, holder profits (candidate - K) × OI
        ce_pain = float(
            ce_df[ce_df["strike"] <= candidate]
            .apply(lambda r: (candidate - r["strike"]) * r["oi"], axis=1)
            .sum()
        )
        # PE pain: for each PE strike >= candidate, holder profits (K - candidate) × OI
        pe_pain = float(
            pe_df[pe_df["strike"] >= candidate]
            .apply(lambda r: (r["strike"] - candidate) * r["oi"], axis=1)
            .sum()
        )
        total_pain = ce_pain + pe_pain
        if total_pain < min_pain:
            min_pain = total_pain
            max_pain_strike = candidate

    return max_pain_strike


def detect_option_pinning(
    chain_df: pd.DataFrame,
    symbol: str,
    exchange: str,
    spot_price: float,
    expiry_date: date,
    snapshot_time: datetime,
) -> Optional[OptionPinningSignal]:
    """
    Detect if the spot price is being pinned near a high-OI strike
    close to expiry.

    Parameters
    ----------
    chain_df
        Option chain DataFrame from nse_option_chain.fetch_option_chain(),
        filtered to the expiry of interest.
        Required columns: strike, option_type, oi.
    symbol, exchange
        Instrument identity.
    spot_price
        Current underlying spot price.
    expiry_date
        The expiry date of the chain being analysed.
    snapshot_time
        When this snapshot was taken.

    Returns
    -------
    OptionPinningSignal | None
        Signal if all three criteria (proximity, DTE, OI dominance) are met.
        None otherwise — no synthetic substitution.

    HARD RULE #1: raises ValueError on empty/invalid inputs.
    HARD RULE #3: returned signal has a full human-readable explanation.
    """
    if chain_df is None or len(chain_df) == 0:
        raise ValueError(
            f"Option pinning detector: chain_df is empty for {symbol}. "
            "Provide a real option chain snapshot."
        )
    if spot_price <= 0:
        raise ValueError(f"spot_price must be positive, got {spot_price}.")
    if expiry_date < snapshot_time.date():
        raise ValueError(
            f"expiry_date {expiry_date} is in the past "
            f"(snapshot: {snapshot_time.date()})."
        )

    required = {"strike", "option_type", "oi"}
    missing = required - set(chain_df.columns)
    if missing:
        raise ValueError(f"chain_df missing columns: {missing}")

    days_to_expiry = (expiry_date - snapshot_time.date()).days

    # Only flag pinning close to expiry
    if days_to_expiry > PIN_EXPIRY_DAYS_THRESHOLD:
        return None

    # Aggregate OI per strike (combined CE + PE)
    strike_oi = (
        chain_df.dropna(subset=["oi"])
        .groupby("strike")["oi"]
        .sum()
        .sort_index()
    )

    if strike_oi.empty:
        return None

    strikes = sorted(strike_oi.index.tolist())

    # Find the strike with highest total OI — the "pin candidate"
    pin_strike = float(strike_oi.idxmax())
    pin_oi = int(strike_oi[pin_strike])

    if pin_oi < MIN_PIN_STRIKE_OI:
        return None

    # Check spot proximity to pin candidate
    distance_pct = abs(spot_price - pin_strike) / spot_price
    if distance_pct > PIN_DISTANCE_THRESHOLD:
        return None  # spot not close enough to be considered "pinned"

    # OI dominance: compare to adjacent strikes
    pin_idx = strikes.index(pin_strike)
    adjacent_strikes = []
    if pin_idx > 0:
        adjacent_strikes.append(strikes[pin_idx - 1])
    if pin_idx < len(strikes) - 1:
        adjacent_strikes.append(strikes[pin_idx + 1])

    if not adjacent_strikes:
        return None  # can't compute dominance with no neighbours

    adjacent_avg_oi = float(
        sum(strike_oi.get(s, 0) for s in adjacent_strikes) / len(adjacent_strikes)
    )
    if adjacent_avg_oi < 1:
        adjacent_avg_oi = 1.0

    oi_dominance = pin_oi / adjacent_avg_oi
    if oi_dominance < PIN_OI_DOMINANCE_THRESHOLD:
        return None  # the strike is close to spot but not unusually dominant in OI

    # Compute max-pain for context
    max_pain = _compute_max_pain(chain_df, strikes)

    # Score:
    # - Proximity (closer = higher): 1 - (distance / threshold)
    # - DTE urgency (0 DTE = max urgency): 1 - (DTE / threshold_DTE)
    # - OI dominance: capped at 1.0
    proximity_score = 1.0 - (distance_pct / PIN_DISTANCE_THRESHOLD)
    dte_score = 1.0 - (days_to_expiry / PIN_EXPIRY_DAYS_THRESHOLD)
    dominance_score = min((oi_dominance - PIN_OI_DOMINANCE_THRESHOLD) / 3.0 + 0.3, 1.0)

    score = min(1.0, 0.35 * proximity_score + 0.40 * dte_score + 0.25 * dominance_score)

    max_pain_note = (
        f"Max-pain strike for this chain: {max_pain:,.0f} "
        f"({'same as pin candidate' if max_pain == pin_strike else f'differs from pin candidate {pin_strike:,.0f} — review both'})."
    )

    explanation = (
        f"Option pinning detected for {symbol} ({exchange}): "
        f"spot {spot_price:,.2f} is within {distance_pct*100:.3f}% "
        f"of strike {pin_strike:,.0f} "
        f"(threshold: {PIN_DISTANCE_THRESHOLD*100:.2f}%), "
        f"with {days_to_expiry} calendar day(s) to expiry {expiry_date}. "
        f"Strike {pin_strike:,.0f} holds {pin_oi:,} total OI "
        f"({oi_dominance:.1f}× the average OI of adjacent strikes: "
        f"{adjacent_avg_oi:,.0f}; dominance threshold: {PIN_OI_DOMINANCE_THRESHOLD:.1f}×). "
        f"{max_pain_note} "
        f"Interpretation: spot price may be being actively maintained near "
        f"this strike to minimise option payout on expiry. "
        f"FALSE POSITIVE WARNING: market makers with natural gamma exposure "
        f"produce identical patterns without manipulative intent. "
        f"This signal requires manual review before any regulatory referral."
    )

    return OptionPinningSignal(
        symbol=symbol,
        exchange=exchange,
        snapshot_time=snapshot_time,
        expiry_date=expiry_date,
        spot_price=spot_price,
        pin_strike=pin_strike,
        distance_pct=distance_pct,
        pin_strike_total_oi=pin_oi,
        adjacent_avg_oi=adjacent_avg_oi,
        oi_dominance_ratio=oi_dominance,
        days_to_expiry=days_to_expiry,
        max_pain_strike=max_pain,
        score=score,
        severity=_severity(score),
        explanation=explanation,
    )
