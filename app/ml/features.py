"""
Signal Feature Extraction
==========================

Converts raw detection signals from all Phase 2/3 detectors into a
unified numerical feature vector for downstream ML scoring.

Design philosophy
------------------
The feature extractor is DELIBERATELY SIMPLE. It does not try to learn
anything — it just converts heterogeneous signal objects into a fixed-length
float vector that the anomaly scorer can consume.

Features are designed to be:
  1. Interpretable: every feature has a name and a clear meaning.
  2. Bounded: all features are normalised to approximately [0, 1] range
     to avoid any single feature dominating Isolation Forest distances.
  3. Conservative: when a field is missing, we use 0 (absence of signal)
     not imputation — missing = not observed, not "average".

Feature vector schema (SCHEMA_VERSION = 1)
-------------------------------------------
The schema is versioned because adding features changes vector dimensionality
and invalidates any saved Isolation Forest models. If you add a feature,
bump SCHEMA_VERSION and retrain.

Index  Name                        Source
----------------------------------------------------------------------
0      circular_trading_score      CircularTradingSignal.score or 0
1      circular_cycle_length       signal.cycle_length / MAX_CYCLE_LEN or 0
2      circular_net_position       signal.max_net_position_pct (already 0..1)
3      circular_is_illiquid        1.0 if signal.is_illiquid else 0.0
4      pump_score                  CoordinatedPumpSignal.score or 0
5      pump_num_accounts           signal.num_accounts / 20 (cap at 20) or 0
6      pump_volume_multiple        signal.volume_multiple / 20 (cap at 20) or 0
7      pump_dormancy_fraction      len(dormant) / num_accounts or 0
8      oi_concentration_score      OIConcentrationSignal.score or 0
9      oi_concentration_ratio      signal.concentration_ratio or 0
10     oi_moneyness_abs            abs(signal.moneyness_pct) / 30 (cap)
11     oi_decoupling_score         OIIVDecouplingSignal.score or 0
12     basis_score                 BasisDistortionSignal.score or 0
13     basis_deviation_pct         signal.deviation_pct / 0.05 (cap at 5%) or 0
14     basis_direction             1.0=contango_excess, -1.0=backwardation or 0
15     pinning_score               OptionPinningSignal.score or 0
16     pinning_dte                 1 - (signal.days_to_expiry / PIN_DTE_THRESHOLD)
17     pinning_oi_dominance        signal.oi_dominance_ratio / 10 (cap)
18     num_detector_types_firing   count of distinct detector types with score>0 / 6
19     max_single_score            max score across all signals
----------------------------------------------------------------------
Total: 20 features.

Multi-signal aggregation
-------------------------
A single instrument may fire multiple signals (e.g. OI concentration AND
pinning on the same expiry). The feature vector uses the MAX score across
signals of the same type — this is conservative (uses the worst-case signal)
and avoids double-counting. An additional feature (18) counts how many
distinct detector types are firing simultaneously, because co-occurring
signals from independent detectors are a stronger manipulation indicator
than a single detector firing repeatedly.

This design choice is documented: it means a stock with strong OI
concentration alone will score differently from one with moderate OI
concentration + moderate circular trading (the latter is more suspicious).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Bump when adding/removing/reordering features.
SCHEMA_VERSION: int = 1
FEATURE_DIM: int = 20

# Normalisation caps (values above these are clipped to 1.0)
_CAP_CYCLE_LEN = 6.0       # MAX_CYCLE_LENGTH from circular_trading.py
_CAP_ACCOUNTS = 20.0       # pump: cap at 20 coordinating accounts
_CAP_VOL_MULT = 20.0       # pump: cap at 20× volume spike
_CAP_MONEYNESS = 30.0      # OI: cap at 30% OTM (beyond this all are equally suspicious)
_CAP_BASIS_DEV = 0.05      # basis: cap at 5% deviation
_CAP_OI_DOM = 10.0         # pinning: cap OI dominance at 10×
_CAP_PIN_DTE = 2.0         # PIN_EXPIRY_DAYS_THRESHOLD


def _clip(value: float, cap: float) -> float:
    """Normalise value to [0, 1] by dividing by cap and clipping."""
    return min(1.0, max(0.0, value / cap)) if cap > 0 else 0.0


@dataclass
class SignalBundle:
    """
    All signals for a single (instrument, time window) pair.

    Pass whatever signals you have — None = that detector did not fire.
    Multiple signals of the same type → the highest-scoring one is used.
    """
    circular_signals: list[Any] = field(default_factory=list)   # CircularTradingSignal
    pump_signals: list[Any] = field(default_factory=list)       # CoordinatedPumpSignal
    oi_concentration_signals: list[Any] = field(default_factory=list)
    oi_decoupling_signals: list[Any] = field(default_factory=list)
    basis_signals: list[Any] = field(default_factory=list)      # BasisDistortionSignal
    pinning_signals: list[Any] = field(default_factory=list)    # OptionPinningSignal

    instrument_symbol: str = ""
    window_label: str = ""        # e.g. "2024-01-15T10:00"


@dataclass
class FeatureVector:
    """
    The numerical representation of a SignalBundle.

    `features` is a list of 20 floats, all in [0, 1] approximately.
    `feature_names` are the column labels (useful for debugging and SHAP).
    """
    schema_version: int
    instrument_symbol: str
    window_label: str
    features: list[float]
    feature_names: list[str]
    num_signals: int           # total raw signal count across all types
    source_bundle: SignalBundle | None = None  # kept for explainability


FEATURE_NAMES: list[str] = [
    "circular_trading_score",
    "circular_cycle_length_norm",
    "circular_net_position",
    "circular_is_illiquid",
    "pump_score",
    "pump_num_accounts_norm",
    "pump_volume_multiple_norm",
    "pump_dormancy_fraction",
    "oi_concentration_score",
    "oi_concentration_ratio",
    "oi_moneyness_abs_norm",
    "oi_decoupling_score",
    "basis_score",
    "basis_deviation_pct_norm",
    "basis_direction",
    "pinning_score",
    "pinning_dte_urgency",
    "pinning_oi_dominance_norm",
    "num_detector_types_firing_norm",
    "max_single_score",
]

assert len(FEATURE_NAMES) == FEATURE_DIM, \
    f"FEATURE_NAMES length {len(FEATURE_NAMES)} != FEATURE_DIM {FEATURE_DIM}"


def extract_features(bundle: SignalBundle) -> FeatureVector:
    """
    Convert a SignalBundle into a fixed-length float feature vector.

    HARD RULE #1: raises ValueError if bundle is None (no synthetic substitution).
    Returns a zero vector if all signal lists are empty — this is a valid
    observation (no anomalies detected), not an error.

    Parameters
    ----------
    bundle
        All signals for one (instrument, time window).

    Returns
    -------
    FeatureVector with schema_version, feature names, and 20-element list.
    """
    if bundle is None:
        raise ValueError(
            "extract_features: bundle is None. "
            "Pass a SignalBundle (possibly with empty signal lists). "
            "Do not pass None."
        )

    feats = [0.0] * FEATURE_DIM
    total_signals = 0

    # ── Features 0–3: circular trading ──────────────────────────────────────
    if bundle.circular_signals:
        best = max(bundle.circular_signals, key=lambda s: s.score)
        total_signals += len(bundle.circular_signals)
        feats[0] = float(best.score)
        feats[1] = _clip(getattr(best, "cycle_length", 0), _CAP_CYCLE_LEN)
        feats[2] = float(min(1.0, getattr(best, "max_net_position_pct", 0.0)))
        feats[3] = 1.0 if getattr(best, "is_illiquid", False) else 0.0

    # ── Features 4–7: coordinated pump ──────────────────────────────────────
    if bundle.pump_signals:
        best = max(bundle.pump_signals, key=lambda s: s.score)
        total_signals += len(bundle.pump_signals)
        feats[4] = float(best.score)
        feats[5] = _clip(getattr(best, "num_accounts", 0), _CAP_ACCOUNTS)
        feats[6] = _clip(getattr(best, "volume_multiple", 0), _CAP_VOL_MULT)
        num_acct = getattr(best, "num_accounts", 1) or 1
        dormant = len(getattr(best, "dormant_accounts", []))
        feats[7] = dormant / num_acct

    # ── Features 8–10: OI concentration ─────────────────────────────────────
    if bundle.oi_concentration_signals:
        best = max(bundle.oi_concentration_signals, key=lambda s: s.score)
        total_signals += len(bundle.oi_concentration_signals)
        feats[8] = float(best.score)
        feats[9] = float(min(1.0, getattr(best, "concentration_ratio", 0.0)))
        feats[10] = _clip(abs(getattr(best, "moneyness_pct", 0.0)), _CAP_MONEYNESS)

    # ── Feature 11: OI-IV decoupling ─────────────────────────────────────────
    if bundle.oi_decoupling_signals:
        best = max(bundle.oi_decoupling_signals, key=lambda s: s.score)
        total_signals += len(bundle.oi_decoupling_signals)
        feats[11] = float(best.score)

    # ── Features 12–14: basis distortion ────────────────────────────────────
    if bundle.basis_signals:
        best = max(bundle.basis_signals, key=lambda s: s.score)
        total_signals += len(bundle.basis_signals)
        feats[12] = float(best.score)
        feats[13] = _clip(getattr(best, "deviation_pct", 0.0), _CAP_BASIS_DEV)
        direction = getattr(best, "direction", "")
        feats[14] = (
            1.0 if direction == "contango_excess"
            else -1.0 if direction == "backwardation_excess"
            else 0.0
        )

    # ── Features 15–17: option pinning ──────────────────────────────────────
    if bundle.pinning_signals:
        best = max(bundle.pinning_signals, key=lambda s: s.score)
        total_signals += len(bundle.pinning_signals)
        feats[15] = float(best.score)
        dte = getattr(best, "days_to_expiry", _CAP_PIN_DTE)
        feats[16] = max(0.0, 1.0 - (dte / _CAP_PIN_DTE))
        feats[17] = _clip(getattr(best, "oi_dominance_ratio", 0.0), _CAP_OI_DOM)

    # ── Features 18–19: cross-detector meta-features ─────────────────────────
    firing_types = sum([
        1 if bundle.circular_signals else 0,
        1 if bundle.pump_signals else 0,
        1 if bundle.oi_concentration_signals else 0,
        1 if bundle.oi_decoupling_signals else 0,
        1 if bundle.basis_signals else 0,
        1 if bundle.pinning_signals else 0,
    ])
    feats[18] = firing_types / 6.0
    feats[19] = max(feats[:18])  # max score before meta-features

    assert len(feats) == FEATURE_DIM
    # Verify all features are in a reasonable range
    for i, f in enumerate(feats):
        if not (-1.0 <= f <= 1.0):
            logger.warning(
                "Feature %d (%s) = %.4f is outside [-1, 1]. "
                "Check normalisation caps.",
                i, FEATURE_NAMES[i], f
            )

    return FeatureVector(
        schema_version=SCHEMA_VERSION,
        instrument_symbol=bundle.instrument_symbol,
        window_label=bundle.window_label,
        features=feats,
        feature_names=FEATURE_NAMES,
        num_signals=total_signals,
        source_bundle=bundle,
    )
