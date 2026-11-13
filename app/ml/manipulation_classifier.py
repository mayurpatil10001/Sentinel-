"""
app/ml/manipulation_classifier.py
====================================
Manipulation Confidence Classifier — stage-2 model on top of the OHLCV
Isolation Forest.

WHAT THIS IS
-------------
A supervised-style calibration layer that takes the raw IF anomaly score
(from OHLCVScorer) + the heuristic detector scores and outputs a final
0–1 "ML Confidence" value.

Training strategy (weakly supervised):
  - Positive examples  = sessions that triggered at least one detector
                         (volume_spike, price_anomaly, basis_distortion, etc.)
  - Negative examples  = all other sessions with normal OHLCV behaviour
  - Labels are WEAK — we do NOT have confirmed SEBI manipulation cases.
    The confidence score means "how similar to previously flagged sessions",
    not "confirmed manipulation probability".

Model:
  Gradient Boosted classifier (sklearn GradientBoostingClassifier) trained on
  the 7 OHLCV features + 2 derived features (IF raw score, heuristic score).
  Outputs calibrated probability via CalibratedClassifierCV (Platt scaling).

Persistence:
  app/ml/models/classifier/
    manipulation_clf.pkl    — trained GBC + calibration wrapper
    metadata.json           — training date, n_positives, n_negatives, auc
"""

from __future__ import annotations

import json
import logging
import pickle
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_DEFAULT_CLF_DIR = Path(__file__).parent / "models" / "classifier"
_CLF_PATH        = _DEFAULT_CLF_DIR / "manipulation_clf.pkl"
_META_PATH       = _DEFAULT_CLF_DIR / "metadata.json"

# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_clf_singleton: Optional["ManipulationClassifier"] = None


def get_classifier() -> "ManipulationClassifier":
    global _clf_singleton
    if _clf_singleton is None:
        _clf_singleton = ManipulationClassifier()
        _clf_singleton.load()
    return _clf_singleton


def invalidate_classifier() -> None:
    global _clf_singleton
    _clf_singleton = None


# ---------------------------------------------------------------------------
# Feature builder — takes OHLCVFeatureVector + raw IF score + heuristic score
# Returns a 9-dim numpy array
# ---------------------------------------------------------------------------

def build_clf_features(fv, if_score: float, heuristic_score: float) -> np.ndarray:
    """
    9-feature vector for the ManipulationClassifier:
      0-6 : OHLCVFeatureVector fields (same as OHLCVScorer)
      7   : raw Isolation Forest score (0-1, higher = more anomalous)
      8   : heuristic score (0-1, from detector formula)
    """
    return np.array([
        fv.vol_spike_score,
        fv.price_anomaly_score,
        fv.thin_vol_score,
        fv.momentum_score,
        fv.sustained_vol_score,
        fv.n_firing_norm,
        fv.max_score,
        float(if_score),
        float(heuristic_score),
    ], dtype=np.float32)


# ---------------------------------------------------------------------------
# ManipulationClassifier
# ---------------------------------------------------------------------------

class ManipulationClassifier:
    """
    Gradient-boosted binary classifier: flagged session vs. normal session.

    Usage:
        clf = ManipulationClassifier()
        clf.train(X_pos, X_neg)   # train on collected feature arrays
        p, label = clf.predict(x) # returns (confidence_0_to_1, method_str)
        clf.save()
        clf.load()
    """

    def __init__(self):
        self.model = None
        self.trained = False
        self.metadata: dict = {}

    # ------------------------------------------------------------------ train
    def train(
        self,
        X_positive: np.ndarray,
        X_negative: np.ndarray,
    ) -> dict:
        """
        Train on positive (flagged) and negative (normal) feature matrices.
        Returns training metrics dict.
        """
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.model_selection import cross_val_score
        from sklearn.preprocessing import StandardScaler
        from sklearn.pipeline import Pipeline

        n_pos = len(X_positive)
        n_neg = len(X_negative)

        if n_pos < 5:
            raise ValueError(
                f"Need at least 5 positive (flagged) examples to train, got {n_pos}. "
                "Run more live scans to generate alerts first."
            )

        X = np.vstack([X_positive, X_negative])
        y = np.array([1] * n_pos + [0] * n_neg)

        logger.info(
            "Training ManipulationClassifier: %d positive, %d negative (total %d)",
            n_pos, n_neg, len(X)
        )

        # Class weight to handle imbalance (normal sessions >> flagged sessions)
        gbc = GradientBoostingClassifier(
            n_estimators=200,
            max_depth=3,
            learning_rate=0.05,
            subsample=0.8,
            random_state=42,
        )
        pipeline = Pipeline([
            ("scaler", StandardScaler()),
            ("gbc",    gbc),
        ])
        calibrated = CalibratedClassifierCV(pipeline, method="sigmoid", cv=min(5, n_pos))
        calibrated.fit(X, y)

        # Cross-validated AUC
        try:
            from sklearn.model_selection import StratifiedKFold
            cv = StratifiedKFold(n_splits=min(5, n_pos), shuffle=True, random_state=42)
            auc_scores = cross_val_score(
                CalibratedClassifierCV(Pipeline([("scaler", StandardScaler()), ("gbc", GradientBoostingClassifier(n_estimators=200, max_depth=3, learning_rate=0.05, subsample=0.8, random_state=42))]), method="sigmoid", cv=min(5, n_pos)),
                X, y, cv=cv, scoring="roc_auc"
            )
            mean_auc = float(np.mean(auc_scores))
        except Exception:
            mean_auc = -1.0

        self.model   = calibrated
        self.trained = True
        self.metadata = {
            "trained_at":    datetime.utcnow().isoformat() + "Z",
            "n_positive":    n_pos,
            "n_negative":    n_neg,
            "cv_auc_mean":   round(mean_auc, 4),
            "label_source":  "weak_labels_from_detectors",
            "disclaimer":    (
                "Labels are WEAK: positive = session triggered a detector, "
                "negative = normal session. No confirmed SEBI cases used. "
                "Confidence score is NOT a manipulation probability."
            ),
        }
        logger.info(
            "ManipulationClassifier trained. CV AUC=%.3f, pos=%d, neg=%d",
            mean_auc, n_pos, n_neg
        )
        return self.metadata

    # --------------------------------------------------------------- predict
    def predict(self, x: np.ndarray) -> Tuple[float, str]:
        """
        Predict ML Confidence for a 9-feature vector.

        Returns:
            (confidence, method) where:
                confidence : float 0–1  (calibrated probability of being a flagged session)
                method     : str  "ml_classifier" | "heuristic_fallback"
        """
        if not self.trained or self.model is None:
            # Fallback: use the heuristic score (feature index 8)
            heuristic = float(x[8]) if len(x) > 8 else 0.5
            return heuristic, "heuristic_fallback"

        try:
            prob = float(self.model.predict_proba(x.reshape(1, -1))[0, 1])
            return round(prob, 4), "ml_classifier"
        except Exception as e:
            logger.debug("ManipulationClassifier.predict failed: %s", e)
            heuristic = float(x[8]) if len(x) > 8 else 0.5
            return heuristic, "heuristic_fallback"

    # ------------------------------------------------------------------- save
    def save(self, model_dir: Path = _DEFAULT_CLF_DIR) -> None:
        model_dir.mkdir(parents=True, exist_ok=True)
        with open(model_dir / "manipulation_clf.pkl", "wb") as f:
            pickle.dump(self.model, f, protocol=pickle.HIGHEST_PROTOCOL)
        with open(model_dir / "metadata.json", "w") as f:
            json.dump(self.metadata, f, indent=2)
        logger.info("ManipulationClassifier saved to %s", model_dir)

    # ------------------------------------------------------------------- load
    def load(self, model_dir: Path = _DEFAULT_CLF_DIR) -> bool:
        clf_path  = model_dir / "manipulation_clf.pkl"
        meta_path = model_dir / "metadata.json"
        if not clf_path.exists():
            logger.debug("ManipulationClassifier model not found at %s — will use heuristic fallback", clf_path)
            self.trained = False
            return False
        with open(clf_path, "rb") as f:
            self.model = pickle.load(f)
        if meta_path.exists():
            with open(meta_path) as f:
                self.metadata = json.load(f)
        self.trained = True
        logger.info(
            "ManipulationClassifier loaded: trained=%s, pos=%d, neg=%d, auc=%.3f",
            self.metadata.get("trained_at", "?"),
            self.metadata.get("n_positive", 0),
            self.metadata.get("n_negative", 0),
            self.metadata.get("cv_auc_mean", -1),
        )
        return True

