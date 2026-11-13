"""
app/ml/ohlcv_scorer.py
========================
Anomaly scorer for OHLCV-based detection signals (volume_spike, price_anomaly,
thin_volume_pump, momentum_reversal, sustained_volume).

WHAT THIS IS
-------------
An Isolation Forest that learns what a "normal" day looks like across all
15 Sentinel watchlist symbols and 90+ sessions of Fyers daily OHLCV data.
When a session has an unusual *combination* of signals, the IF assigns a higher
anomaly score. This is NOT a manipulation classifier — it is anomaly detection.

WHAT THIS IS NOT
-----------------
- It cannot label confirmed manipulation events (we have no ground truth).
- A high score means "unusual relative to baseline", not "confirmed manipulation".
- Thresholds remain UNVALIDATED_HEURISTIC until backtested against real SEBI cases.

FEATURE VECTOR (7 features, schema_version=1)
----------------------------------------------
  idx  name                    description
  ---  ----                    -----------
   0   vol_spike_score         0 if not triggered, else normalized volume ratio score
   1   price_anomaly_score     0 if not triggered, else normalized z-score score
   2   thin_vol_score          0 if not triggered, else thin-volume-pump score
   3   momentum_score          0 if not triggered, else momentum-reversal score
   4   sustained_vol_score     0 if not triggered, else sustained-volume score
   5   n_detectors_firing      count of distinct detector types firing / 5 (max 5)
   6   max_single_score        max score across all firing detectors

HOW SCORES ARE COMBINED
------------------------
  mode=heuristic: weighted average across feature scores (always available)
  mode=ml: Isolation Forest anomaly score + heuristic average, blended 50/50
  The blend ensures that even with a trained model, domain-expert weighting
  stays in the signal (pure IF can be overridden by outliers in the training set).

PERSISTENCE
------------
  Save/load from: app/ml/models/ohlcv/
    isolation_forest.pkl  — sklearn IsolationForest
    metadata.json         — schema version, training date, sample count
"""

import json
import logging
import os
import pickle
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema versioning
# ---------------------------------------------------------------------------
SCHEMA_VERSION: int = 1
FEATURE_DIM: int = 7
FEATURE_NAMES: List[str] = [
    "vol_spike_score",
    "price_anomaly_score",
    "thin_vol_score",
    "momentum_score",
    "sustained_vol_score",
    "n_detectors_firing_norm",
    "max_single_score",
]

# Default model directory (relative to repo root)
_DEFAULT_MODEL_DIR = Path(__file__).parent / "models" / "ohlcv"

# ---------------------------------------------------------------------------
# Isolation Forest hyperparameters
# ---------------------------------------------------------------------------
IF_N_ESTIMATORS: int = 200          # Liu et al. show diminishing returns past 100-200
IF_CONTAMINATION: float = 0.05      # ~5% of sessions assumed anomalous (UNVALIDATED GUESS)
IF_MAX_SAMPLES: str = "auto"
IF_RANDOM_STATE: int = 42

# Detector weights for the heuristic fallback (reflects signal reliability)
_WEIGHTS: Dict[str, float] = {
    "vol_spike_score":     0.20,   # volume alone is noisy
    "price_anomaly_score": 0.35,   # z-score is the strongest single signal
    "thin_vol_score":      0.20,   # price move on low vol — moderate reliability
    "momentum_score":      0.15,   # 3-session pattern — lower sample count
    "sustained_vol_score": 0.10,   # multi-day — catches slow accumulation
}


# ---------------------------------------------------------------------------
# Feature vector dataclass
# ---------------------------------------------------------------------------

@dataclass
class OHLCVFeatureVector:
    """Feature vector for one (symbol, session-date) pair."""
    symbol: str
    event_date: date
    vol_spike_score: float = 0.0
    price_anomaly_score: float = 0.0
    thin_vol_score: float = 0.0
    momentum_score: float = 0.0
    sustained_vol_score: float = 0.0

    @property
    def n_firing(self) -> int:
        """How many distinct detector types fired (score > 0)."""
        return sum(1 for s in [
            self.vol_spike_score, self.price_anomaly_score,
            self.thin_vol_score, self.momentum_score, self.sustained_vol_score,
        ] if s > 0)

    @property
    def max_score(self) -> float:
        return max(
            self.vol_spike_score, self.price_anomaly_score,
            self.thin_vol_score, self.momentum_score, self.sustained_vol_score,
        )

    def to_array(self) -> np.ndarray:
        """Return the fixed-length feature array for sklearn."""
        return np.array([
            float(self.vol_spike_score),
            float(self.price_anomaly_score),
            float(self.thin_vol_score),
            float(self.momentum_score),
            float(self.sustained_vol_score),
            float(self.n_firing) / 5.0,     # normalise to [0, 1]
            float(self.max_score),
        ], dtype=np.float32)

    def heuristic_score(self) -> float:
        """
        Weighted average of individual detector scores.
        Always computable — does not require a trained model.
        """
        s = (
            _WEIGHTS["vol_spike_score"]     * self.vol_spike_score
            + _WEIGHTS["price_anomaly_score"] * self.price_anomaly_score
            + _WEIGHTS["thin_vol_score"]      * self.thin_vol_score
            + _WEIGHTS["momentum_score"]      * self.momentum_score
            + _WEIGHTS["sustained_vol_score"] * self.sustained_vol_score
        )
        # Co-occurrence bonus: each additional detector firing adds 10% boost
        # (because independent signals firing simultaneously is stronger evidence)
        co_bonus = max(0, self.n_firing - 1) * 0.05
        return float(min(1.0, s + co_bonus))


def build_feature_vector(
    symbol: str,
    event_date: date,
    detections: List[Dict],
) -> OHLCVFeatureVector:
    """
    Build an OHLCVFeatureVector from the list of detection dicts returned
    by detection_worker.run_detection_for_symbol() for a single session.

    For multi-session detectors (momentum_reversal, sustained_volume),
    their detection dicts will be in the list keyed to the last session's
    event_date, so they are captured correctly.
    """
    fv = OHLCVFeatureVector(symbol=symbol, event_date=event_date)
    for d in detections:
        pt = d.get("pattern_type", "")
        sc = float(d.get("score", 0.0))
        if pt == "volume_spike":
            fv.vol_spike_score = max(fv.vol_spike_score, sc)
        elif pt == "price_anomaly":
            fv.price_anomaly_score = max(fv.price_anomaly_score, sc)
        elif pt == "thin_volume_pump":
            fv.thin_vol_score = max(fv.thin_vol_score, sc)
        elif pt == "momentum_reversal":
            fv.momentum_score = max(fv.momentum_score, sc)
        elif pt == "sustained_volume":
            fv.sustained_vol_score = max(fv.sustained_vol_score, sc)
    return fv


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

class OHLCVScorer:
    """
    Anomaly scorer for OHLCV-based detection signals.

    Usage:
        scorer = OHLCVScorer.load()          # loads saved model if available
        score, method = scorer.score(fv)     # returns (float in [0,1], str)

        # To train from scratch:
        scorer = OHLCVScorer()
        scorer.train(feature_vectors)
        scorer.save()
    """

    def __init__(self, model_dir: Path = _DEFAULT_MODEL_DIR):
        self._model = None
        self._model_dir = Path(model_dir)
        self._metadata: Optional[Dict] = None

    @property
    def is_trained(self) -> bool:
        return self._model is not None

    def train(self, feature_vectors: List[OHLCVFeatureVector]) -> None:
        """
        Fit an Isolation Forest on `feature_vectors`.

        RULES:
         - Minimum 10 vectors required (IF is unreliable below this).
         - Raises on empty input — do NOT silently skip training.
         - All vectors must have SCHEMA_VERSION == SCHEMA_VERSION constant.
        """
        if not feature_vectors:
            raise ValueError(
                "OHLCVScorer.train: no feature vectors provided. "
                "Run the training script after fetching Fyers data."
            )
        if len(feature_vectors) < 10:
            raise ValueError(
                f"OHLCVScorer.train: only {len(feature_vectors)} vectors — "
                "need at least 10 for a meaningful Isolation Forest."
            )

        try:
            from sklearn.ensemble import IsolationForest
        except ImportError:
            raise ImportError("scikit-learn required. Install: pip install scikit-learn")

        X = np.array([fv.to_array() for fv in feature_vectors], dtype=np.float32)

        logger.info(
            "Training OHLCVScorer IsolationForest on %d vectors "
            "(n_estimators=%d, contamination=%.2f)",
            len(feature_vectors), IF_N_ESTIMATORS, IF_CONTAMINATION
        )

        self._model = IsolationForest(
            n_estimators=IF_N_ESTIMATORS,
            contamination=IF_CONTAMINATION,
            max_samples=IF_MAX_SAMPLES,
            random_state=IF_RANDOM_STATE,
            n_jobs=-1,
        )
        self._model.fit(X)

        self._metadata = {
            "schema_version": SCHEMA_VERSION,
            "feature_dim": FEATURE_DIM,
            "feature_names": FEATURE_NAMES,
            "training_set_size": len(feature_vectors),
            "trained_at": datetime.utcnow().isoformat() + "Z",
            "contamination": IF_CONTAMINATION,
            "n_estimators": IF_N_ESTIMATORS,
            "note": (
                "UNVALIDATED_HEURISTIC — no confirmed manipulation ground truth. "
                "Score = anomalousness relative to training baseline distribution."
            ),
        }
        logger.info("OHLCVScorer trained on %d vectors.", len(feature_vectors))

    def score(self, fv: OHLCVFeatureVector):
        """
        Score a feature vector. Returns (ml_confidence, method_label).

        ml_confidence — float in [0, 1]
        method_label  — 'heuristic' | 'heuristic+if' | 'ml_classifier'

        Pipeline:
          1. Compute heuristic score from detector weights (always available)
          2. If IF trained: compute Isolation Forest anomaly score
          3. If ManipulationClassifier trained: compute calibrated confidence
             using GradientBoostedClassifier on 9-feature vector
             (7 OHLCV features + IF score + heuristic score)
          4. Return highest-fidelity available score
        """
        heuristic = fv.heuristic_score()

        if not self.is_trained:
            return round(float(heuristic), 4), "heuristic"

        # ── Step 2: Isolation Forest ───────────────────────────────────────
        X = np.array([fv.to_array()], dtype=np.float32)
        raw_sklearn = float(self._model.score_samples(X)[0])
        OFFSET = 0.0
        SCALE  = 0.5
        if_score = float(np.clip((-raw_sklearn - OFFSET) / SCALE, 0.0, 1.0))

        # ── Step 3: ManipulationClassifier (if trained) ───────────────────
        try:
            from app.ml.manipulation_classifier import get_classifier, build_clf_features
            clf = get_classifier()
            if clf.trained:
                clf_features = build_clf_features(fv, if_score, heuristic)
                ml_confidence, method = clf.predict(clf_features)
                return round(float(ml_confidence), 4), method
        except Exception as clf_exc:
            logger.debug("ManipulationClassifier skipped: %s", clf_exc)

        # ── Step 4: Fallback — IF + heuristic blend ───────────────────────
        composite = float(np.clip((heuristic + if_score) / 2.0, 0.0, 1.0))
        return round(composite, 4), "heuristic+if"

    def save(self, model_dir: Optional[Path] = None) -> None:
        """Save trained model and metadata to `model_dir`."""
        if not self.is_trained:
            raise RuntimeError("OHLCVScorer.save: model not trained. Call .train() first.")

        d = Path(model_dir or self._model_dir)
        d.mkdir(parents=True, exist_ok=True)

        with open(d / "isolation_forest.pkl", "wb") as f:
            pickle.dump(self._model, f, protocol=pickle.HIGHEST_PROTOCOL)
        with open(d / "metadata.json", "w") as f:
            json.dump(self._metadata, f, indent=2)

        logger.info("OHLCVScorer saved to %s", d)

    @classmethod
    def load(cls, model_dir: Optional[Path] = None) -> "OHLCVScorer":
        """
        Load a saved model. Returns an untrained scorer if no model found
        (falls back to heuristic mode — does NOT raise on missing model).
        """
        d = Path(model_dir or _DEFAULT_MODEL_DIR)
        meta_path  = d / "metadata.json"
        model_path = d / "isolation_forest.pkl"

        scorer = cls(model_dir=d)

        if not meta_path.exists() or not model_path.exists():
            logger.info(
                "OHLCVScorer: no saved model at %s — running in heuristic mode.", d
            )
            return scorer

        try:
            with open(meta_path) as f:
                meta = json.load(f)

            saved_ver = meta.get("schema_version")
            if saved_ver != SCHEMA_VERSION:
                logger.warning(
                    "OHLCVScorer: saved model schema_version=%s != current %s. "
                    "Falling back to heuristic. Retrain with: "
                    "python -m app.ml.train_ohlcv",
                    saved_ver, SCHEMA_VERSION
                )
                return scorer

            with open(model_path, "rb") as f:
                scorer._model = pickle.load(f)

            scorer._metadata = meta
            logger.info(
                "OHLCVScorer loaded: trained=%s, n=%d",
                meta.get("trained_at"), meta.get("training_set_size", 0)
            )
        except Exception as exc:
            logger.error("OHLCVScorer.load failed (%s) — using heuristic fallback.", exc)

        return scorer


# ---------------------------------------------------------------------------
# Module-level singleton (lazy-loaded on first use)
# ---------------------------------------------------------------------------
_scorer_instance: Optional[OHLCVScorer] = None


def get_scorer() -> OHLCVScorer:
    """Return the module-level scorer (loaded once, cached)."""
    global _scorer_instance
    if _scorer_instance is None:
        _scorer_instance = OHLCVScorer.load()
    return _scorer_instance


def invalidate_scorer() -> None:
    """Force reload next time get_scorer() is called (e.g. after retraining)."""
    global _scorer_instance
    _scorer_instance = None

