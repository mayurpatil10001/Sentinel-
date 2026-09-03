"""
Open Interest (OI) Manipulation Detector
==========================================

Detects abnormal OI concentration in specific option strikes that is
inconsistent with hedging or normal speculative activity.

What is OI manipulation?
--------------------------
Open interest (OI) is the total number of outstanding option contracts.
Manipulators can:
  1. Build large OI positions in deep OTM strikes to create false
     "market expectation" signals (e.g. OI surge in 10% OTM puts to
     suggest market crash expected, then sell spot).
  2. Concentrate OI in strikes near current spot to force market makers
     into expensive hedges (gamma squeeze setup).
  3. Rapidly build-then-unwind OI to create artificial volume/interest
     in illiquid strikes.

This detector focuses on:
  (a) OI concentration: is one strike holding a disproportionate share
      of total OI across the chain?
  (b) OI velocity: did OI at a specific strike change abnormally fast
      in a single session (large OI build without corresponding volume)?
  (c) OI-IV decoupling: OI is rising while IV is falling (or vice versa)
      in a direction inconsistent with directional hedging.

Threshold documentation (HARD RULE #2)
-----------------------------------------
OI_CONCENTRATION_THRESHOLD = 0.35
  Any single strike holding >= 35% of total chain OI is flagged as
  abnormally concentrated. Normal OI distributes across strikes near the
  money, so a single strike dominating the chain is unusual.
  Source: Informal review of NSE NIFTY option OI distributions — the
  top strike rarely exceeds 20-25% on a normal day. 35% is the 95th
  percentile estimate. Label: UNVALIDATED GUESS — needs backtesting.

OI_VELOCITY_THRESHOLD = 3.0
  OI change at a strike > 3× its rolling average daily OI change is
  flagged as abnormally fast accumulation.
  Label: UNVALIDATED GUESS — needs backtesting vs known squeeze events.

OI_IV_DECOUPLING_THRESHOLD = 0.30
  If OI grows by >= 30% while IV moves in the "wrong" direction
  (buying pressure → IV should rise; selling pressure → IV should fall),
  this may indicate OI is being built for structural reasons (pinning,
  squeeze) rather than pure directional bets.
  Label: HEURISTIC.

Real-data status
-----------------
Operates on DataFrames from `nse_option_chain.fetch_option_chain()`.
No synthetic fallback — raises if input is empty.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ── Thresholds ────────────────────────────────────────────────────────────────

# Single-strike OI as fraction of total chain OI.
# UNVALIDATED GUESS — needs backtesting.
OI_CONCENTRATION_THRESHOLD: float = 0.35

# OI change magnitude as multiple of the strike's recent average.
# UNVALIDATED GUESS.
OI_VELOCITY_THRESHOLD: float = 3.0

# Fraction OI grew while IV moved in the "wrong" direction.
# HEURISTIC.
OI_IV_DECOUPLING_THRESHOLD: float = 0.30

# Minimum total chain OI for the detector to have any signal — below this,
# the chain is too thin to be worth monitoring.
# HEURISTIC.
MIN_CHAIN_OI: int = 50_000

# Minimum OI at the flagged strike — tiny positions can look concentrated
# in very illiquid chains without being meaningful.
# HEURISTIC.
MIN_STRIKE_OI: int = 10_000


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class OIConcentrationSignal:
    """Flagged when one strike holds a disproportionate fraction of chain OI."""
    symbol: str
    exchange: str
    snapshot_time: datetime
    expiry: str                  # formatted expiry date string
    option_type: str             # CE / PE
    strike: float
    strike_oi: int
    total_chain_oi: int
    concentration_ratio: float   # strike_oi / total_chain_oi
    underlying_value: float
    moneyness_pct: float         # (strike - spot) / spot × 100
    score: float
    severity: str
    explanation: str = ""


@dataclass
class OIVelocitySignal:
    """Flagged when OI at a strike builds abnormally fast in one session."""
    symbol: str
    exchange: str
    snapshot_time: datetime
    expiry: str
    option_type: str
    strike: float
    oi_change: int               # change in this session
    avg_daily_oi_change: float   # rolling average for comparison
    velocity_multiple: float     # oi_change / avg_daily_oi_change
    score: float
    severity: str
    explanation: str = ""


@dataclass
class OIIVDecouplingSignal:
    """Flagged when OI grows significantly while IV moves in the wrong direction."""
    symbol: str
    exchange: str
    snapshot_time: datetime
    expiry: str
    option_type: str
    strike: float
    oi_change_pct: float         # fractional OI change (>0 = build)
    iv_change_pct: float         # fractional IV change (>0 = vol rising)
    expected_iv_direction: str   # "up" (OI build = buying) or "down" (selling)
    actual_iv_direction: str
    score: float
    severity: str
    explanation: str = ""


def _severity(score: float) -> str:
    if score >= 0.85: return "critical"
    if score >= 0.65: return "high"
    if score >= 0.45: return "medium"
    return "low"


# ── Detectors ─────────────────────────────────────────────────────────────────

def detect_oi_concentration(
    chain_df: pd.DataFrame,
    symbol: str,
    exchange: str,
    snapshot_time: datetime,
) -> list[OIConcentrationSignal]:
    """
    Detect abnormal OI concentration in one or a few strikes.

    Parameters
    ----------
    chain_df
        DataFrame from `nse_option_chain.fetch_option_chain()`.
        Required columns: strike, expiry, option_type, oi, underlying_value.
    symbol, exchange
        Instrument identity for the signal record.
    snapshot_time
        When this chain snapshot was taken.

    Returns
    -------
    list[OIConcentrationSignal]
        One signal per (expiry, option_type) combination where a single
        strike dominates total OI. Empty list = no anomaly.

    HARD RULE #1: raises ValueError if chain_df is empty (no synthetic fallback).
    """
    if chain_df is None or len(chain_df) == 0:
        raise ValueError(
            f"OI concentration detector received empty chain for {symbol}. "
            "Provide a real option chain snapshot. "
            "Do not substitute synthetic data."
        )

    required = {"strike", "expiry", "option_type", "oi", "underlying_value"}
    missing = required - set(chain_df.columns)
    if missing:
        raise ValueError(
            f"chain_df is missing required columns: {missing}. "
            "Ensure the DataFrame comes from nse_option_chain.fetch_option_chain()."
        )

    signals: list[OIConcentrationSignal] = []

    for (expiry, opt_type), group in chain_df.groupby(["expiry", "option_type"]):
        group = group.dropna(subset=["oi"])
        if group.empty:
            continue

        total_oi = int(group["oi"].sum())
        if total_oi < MIN_CHAIN_OI:
            continue

        max_idx = group["oi"].idxmax()
        max_row = group.loc[max_idx]
        strike_oi = int(max_row["oi"])

        if strike_oi < MIN_STRIKE_OI:
            continue

        ratio = strike_oi / total_oi
        if ratio < OI_CONCENTRATION_THRESHOLD:
            continue

        underlying = float(
            group["underlying_value"].dropna().iloc[0]
            if "underlying_value" in group.columns and len(group) > 0
            else 0.0
        )
        strike = float(max_row["strike"])
        moneyness = ((strike - underlying) / underlying * 100) if underlying > 0 else 0.0

        # Score: higher concentration → higher score, modulated by moneyness
        # Concentration at deep OTM strikes is MORE suspicious (no hedging rationale)
        # than concentration near ATM (which has natural clustering).
        atm_discount = max(0.0, 1.0 - abs(moneyness) / 20.0)
        concentration_score = min((ratio - OI_CONCENTRATION_THRESHOLD) / (1.0 - OI_CONCENTRATION_THRESHOLD), 1.0)
        # Deep OTM concentration is MORE suspicious → invert atm_discount
        otm_suspicion = 1.0 - atm_discount if abs(moneyness) > 10 else 0.3
        score = min(1.0, 0.60 * concentration_score + 0.40 * otm_suspicion)

        expiry_str = expiry.strftime("%Y-%m-%d") if hasattr(expiry, "strftime") else str(expiry)
        moneyness_desc = "OTM" if (
            (opt_type == "CE" and moneyness > 0) or (opt_type == "PE" and moneyness < 0)
        ) else "ITM" if (
            (opt_type == "CE" and moneyness < 0) or (opt_type == "PE" and moneyness > 0)
        ) else "ATM"

        explanation = (
            f"Abnormal OI concentration in {symbol} {opt_type} options "
            f"(expiry {expiry_str}): strike {strike:,.0f} holds "
            f"{ratio*100:.1f}% of total {opt_type} chain OI "
            f"({strike_oi:,} / {total_oi:,} contracts). "
            f"Threshold: {OI_CONCENTRATION_THRESHOLD*100:.0f}%. "
            f"Strike is {abs(moneyness):.1f}% {moneyness_desc} "
            f"(spot: {underlying:,.2f}). "
            f"High OI concentration in a {'deep OTM' if abs(moneyness) > 10 else 'near ATM'} "
            f"strike suggests {'potential false signal / squeeze setup' if abs(moneyness) > 10 else 'possible pinning target'}. "
            f"Threshold source: unvalidated 95th-percentile estimate from NSE NIFTY chains — "
            f"needs backtesting before use in enforcement referrals."
        )

        signals.append(OIConcentrationSignal(
            symbol=symbol,
            exchange=exchange,
            snapshot_time=snapshot_time,
            expiry=expiry_str,
            option_type=opt_type,
            strike=strike,
            strike_oi=strike_oi,
            total_chain_oi=total_oi,
            concentration_ratio=ratio,
            underlying_value=underlying,
            moneyness_pct=moneyness,
            score=score,
            severity=_severity(score),
            explanation=explanation,
        ))

    logger.info(
        "OI concentration: %d signals for %s at %s",
        len(signals), symbol, snapshot_time
    )
    return signals


def detect_oi_iv_decoupling(
    current_chain: pd.DataFrame,
    prev_chain: pd.DataFrame,
    symbol: str,
    exchange: str,
    snapshot_time: datetime,
) -> list[OIIVDecouplingSignal]:
    """
    Detect strikes where OI is growing but IV moves in the wrong direction,
    suggesting the OI build is structural (pinning, squeeze) rather than
    directional speculation.

    Convention:
      - OI build (buyers entering) → IV should rise (demand for options increases vol)
      - OI build (writers entering) → IV should fall (supply increases)
      - If OI rises significantly while IV falls: more writing than buying
        (unusual if price is moving — writers are adding supply, capping vol).
      - If OI rises while IV also rises: normal buying pressure.

    The decoupling flag fires when OI grows >= OI_IV_DECOUPLING_THRESHOLD
    while IV moves in the OPPOSITE direction to what pure buying would predict
    AND the magnitude of the IV move is also >= OI_IV_DECOUPLING_THRESHOLD.

    Parameters
    ----------
    current_chain, prev_chain
        Two consecutive option chain snapshots for the same symbol.
        Both must have columns: strike, expiry, option_type, oi, iv.

    HARD RULE #1: raises ValueError if either DataFrame is empty.
    """
    for name, df in [("current_chain", current_chain), ("prev_chain", prev_chain)]:
        if df is None or len(df) == 0:
            raise ValueError(
                f"OI-IV decoupling detector: {name} is empty for {symbol}. "
                "Provide real consecutive option chain snapshots."
            )

    required = {"strike", "expiry", "option_type", "oi", "iv"}
    for name, df in [("current_chain", current_chain), ("prev_chain", prev_chain)]:
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"{name} missing columns: {missing}")

    # Merge on (strike, expiry, option_type) to get pairs
    merged = current_chain.merge(
        prev_chain[["strike", "expiry", "option_type", "oi", "iv"]],
        on=["strike", "expiry", "option_type"],
        suffixes=("_curr", "_prev"),
    )
    merged = merged.dropna(subset=["oi_curr", "oi_prev", "iv_curr", "iv_prev"])

    signals: list[OIIVDecouplingSignal] = []

    for _, row in merged.iterrows():
        oi_change = float(row["oi_curr"]) - float(row["oi_prev"])
        prev_oi = float(row["oi_prev"])
        if prev_oi < 1:
            continue

        oi_change_pct = oi_change / prev_oi
        if abs(oi_change_pct) < OI_IV_DECOUPLING_THRESHOLD:
            continue  # OI change too small to be interesting

        prev_iv = float(row["iv_prev"])
        curr_iv = float(row["iv_curr"])
        if prev_iv < 0.01:
            continue

        iv_change_pct = (curr_iv - prev_iv) / prev_iv

        # Decoupling: OI growing (buyers adding long positions) while IV FALLING
        # IV falling while OI grows means writers (sellers) are dominant —
        # unusual in a strong directional move. This suggests structural positioning.
        oi_growing = oi_change_pct >= OI_IV_DECOUPLING_THRESHOLD
        iv_falling_while_oi_growing = oi_growing and iv_change_pct <= -OI_IV_DECOUPLING_THRESHOLD

        if not iv_falling_while_oi_growing:
            continue

        score = min(1.0, abs(oi_change_pct) / 1.0 * 0.5 + abs(iv_change_pct) / 0.5 * 0.5)
        expiry = row["expiry"]
        expiry_str = expiry.strftime("%Y-%m-%d") if hasattr(expiry, "strftime") else str(expiry)

        explanation = (
            f"OI-IV decoupling detected for {symbol} "
            f"{row['option_type']} strike {row['strike']:,.0f} "
            f"(expiry {expiry_str}): "
            f"OI grew by {oi_change_pct*100:.1f}% "
            f"(+{int(oi_change):,} contracts) while IV fell by "
            f"{abs(iv_change_pct)*100:.1f}% "
            f"({prev_iv:.1f}% → {curr_iv:.1f}%). "
            f"OI growth while IV falls implies writers (option sellers) are "
            f"dominant — consistent with structural positioning (e.g. pinning, "
            f"spread strategies) rather than directional speculation. "
            f"Threshold: OI change >= {OI_IV_DECOUPLING_THRESHOLD*100:.0f}% "
            f"and IV move >= {OI_IV_DECOUPLING_THRESHOLD*100:.0f}% in opposite direction. "
            f"Label: HEURISTIC — analyst review required."
        )

        signals.append(OIIVDecouplingSignal(
            symbol=symbol,
            exchange=exchange,
            snapshot_time=snapshot_time,
            expiry=expiry_str,
            option_type=str(row["option_type"]),
            strike=float(row["strike"]),
            oi_change_pct=oi_change_pct,
            iv_change_pct=iv_change_pct,
            expected_iv_direction="up",
            actual_iv_direction="down",
            score=score,
            severity=_severity(score),
            explanation=explanation,
        ))

    logger.info(
        "OI-IV decoupling: %d signals for %s at %s",
        len(signals), symbol, snapshot_time
    )
    return signals
