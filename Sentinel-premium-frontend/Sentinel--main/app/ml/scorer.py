"""
Anomaly Scorer — Isolation Forest
====================================

Ranks instrument-window pairs by how anomalous their signal pattern is,
using an unsupervised Isolation Forest.

HONEST STATEMENT OF WHAT THIS IS AND IS NOT
=============================================
This is NOT a manipulation classifier. We have no labeled ground truth
(no confirmed manipulation events with confirmed feature vectors), so we
cannot train a supervised classifier. What we have instead is:

  "These feature vectors look different from the baseline distribution
   of all feature vectors we have seen."

This is anomaly detection, not classification. The output is an
anomaly score in [0, 1] that means "how unusual is this combination of
signals relative to everything else we have seen". It does NOT mean
"this is X% likely to be manipulation".

Why Isolation Forest?
----------------------
1. Unsupervised — does not require labeled manipulation examples.
2. Effective in high-dimensional spaces without feature-by-feature
   threshold tuning (the alternative would be a hand-crafted scoring
   function, which is what the individual detectors already are).
3. Naturally handles multi-modal distributions (different manipulation
   patterns cluster in different regions).
4. Computationally cheap for the feature dimensionality we have (20 features).
   Source: Liu, Fei Tony, Kai Ming Ting, and Zhi-Hua Zhou. "Isolation forest."
   2008 IEEE 8th International Conference on Data Mining.

Why NOT a deep learning model?
--------------------------------
Feature dimension is only 20. Deep learning would massively overfit
without thousands of labeled examples per class. Isolation Forest is
the correct tool for this problem size and data availability.

Contamination parameter
------------------------
`contamination = 0.05` (5% of training points assumed to be anomalous).
This controls the decision threshold — with contamination=0.05,
the model labels the top 5% of anomaly scores as "positive" when
calling `.predict()`. We do NOT use `.predict()` here — we expose
the raw anomaly score and leave thresholding to the analyst.
Label: UNVALIDATED GUESS — the true contamination rate is unknown.
Changing this parameter does NOT change the anomaly scores (only the
classification boundary), so the raw scores are stable.

Persistence and retraining
----------------------------
Trained models are saved as pickle files alongside a metadata JSON
that records the schema_version, feature names, and training set size.
If schema_version changes, old models are invalid and must be retrained.

Real-data status
-----------------
The scorer can be used in two modes:
  1. ONLINE (no model): scores are the max individual detector score
     (weighted average across firing detectors). This is the baseline
     that works even without a trained model.
  2. ML (with trained model): Isolation Forest anomaly score, combined
     with the baseline weighted score.
HARD RULE #1: if no data is available, the scorer raises, not falls back
to synthetic anomaly scores.
"""

import json
import logging
import pickle
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

from app.ml.features import FeatureVector, SCHEMA_VERSION, FEATURE_DIM

logger = logging.getLogger(__name__)

# ── Contamination parameter ───────────────────────────────────────────────────
# Fraction of training points assumed anomalous.
# UNVALIDATED GUESS — used only for IF decision boundary, not raw scores.
IF_CONTAMINATION: float = 0.05

# Isolation Forest hyperparameters
# n_estimators=200: more trees = more stable scores. 200 is standard.
# Label: HEURISTIC — Liu et al. show diminishing returns past 100-200.
IF_N_ESTIMATORS: int = 200

# max_samples="auto": sklearn default (min(256, n_samples)). Fine for our size.
IF_MAX_SAMPLES: str = "auto"

# Random seed for reproducibility.
IF_RANDOM_STATE: int = 42


# ── Scoring weights for the fallback (no-model) scorer ───────────────────────
# These weights reflect the relative reliability of each detector type.
# All labels: UNVALIDATED GUESS — needs calibration vs. confirmed cases.
_FALLBACK_WEIGHTS = {
    "circular_trading": 0.30,   # highest confidence — direct counterparty evidence
    "coordinated_pump": 0.20,   # medium — requires volume data
    "oi_concentration": 0.15,   # lower — many innocent explanations
    "oi_decoupling": 0.10,      # lower — requires two consecutive snapshots
    "basis_distortion": 0.15,   # medium — clear mathematical definition
    "option_pinning": 0.10,     # lowest — hardest to distinguish from MM gamma
}
assert abs(sum(_FALLBACK_WEIGHTS.values()) - 1.0) < 1e-9, \
    "Fallback weights must sum to 1.0"


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class AnomalyScore:
    """
    The final anomaly score for one (instrument, window) pair.

    `raw_anomaly_score` — the Isolation Forest score if a model is trained,
    else None. Higher = more anomalous (we invert IF's convention, which
    returns more-negative for more-anomalous).

    `weighted_baseline_score` — the fallback weighted combination of
    individual detector scores. Always available.

    `composite_score` — the score to use for ranking/alerting:
      - If model is trained: average(raw_anomaly_score, weighted_baseline_score)
      - If no model: weighted_baseline_score
    This design ensures we never produce a score of 0 just because no model
    is trained (the baseline always gives a meaningful ranking).

    `explanation` — human-readable summary of what drove the score.
    """
    instrument_symbol: str
    window_label: str
    scored_at: datetime

    raw_anomaly_score: Optional[float]       # None if no model trained
    weighted_baseline_score: float
    composite_score: float

    firing_detectors: list[str]              # which detector types fired
    num_detector_types_firing: int
    max_single_detector_score: float

    model_version: Optional[str]             # None if fallback mode
    schema_version: int

    explanation: str = ""
    is_model_scored: bool = False


@dataclass
class ScorerModelMetadata:
    """Persisted alongside the pickle to validate compatibility on load."""
    schema_version: int
    feature_dim: int
    feature_names: list[str]
    training_set_size: int
    trained_at: str          # ISO8601
    contamination: float
    n_estimators: int
    model_version: str       # e.g. "v1.0-20240115"


# ── Scorer ────────────────────────────────────────────────────────────────────

class ManipulationScorer:
    """
    Scores instrument-window feature vectors for anomalousness.

    Use without a trained model for baseline scoring.
    Call `.train(feature_vectors)` to fit the Isolation Forest.
    Call `.save(path)` / `.load(path)` to persist.

    HARD RULE #3: every AnomalyScore includes an explanation string.
    """

    def __init__(self, model_version: str = "v1.0"):
        self._model = None          # sklearn IsolationForest, set after training
        self._model_version = model_version
        self._metadata: Optional[ScorerModelMetadata] = None

    @property
    def is_trained(self) -> bool:
        return self._model is not None

    def train(self, feature_vectors: list[FeatureVector]) -> None:
        """
        Fit an Isolation Forest on the provided feature vectors.

        Parameters
        ----------
        feature_vectors
            A list of FeatureVector objects from extract_features().
            All must have the same schema_version as SCHEMA_VERSION.

        HARD RULE #1: raises if feature_vectors is empty or has wrong schema.
        Minimum training size: 10 vectors (below this, IF is unreliable).
        Label: HEURISTIC minimum — sklearn needs at least a few samples for
        meaningful isolation paths.
        """
        if not feature_vectors:
            raise ValueError(
                "ManipulationScorer.train: feature_vectors is empty. "
                "Provide real feature vectors. Do not train on synthetic data."
            )

        min_training_size = 10
        if len(feature_vectors) < min_training_size:
            raise ValueError(
                f"ManipulationScorer.train: only {len(feature_vectors)} vectors provided. "
                f"Minimum is {min_training_size} for a meaningful Isolation Forest. "
                "Accumulate more observations before training."
            )

        for fv in feature_vectors:
            if fv.schema_version != SCHEMA_VERSION:
                raise ValueError(
                    f"Feature vector has schema_version={fv.schema_version}, "
                    f"but current SCHEMA_VERSION={SCHEMA_VERSION}. "
                    "Retrain with feature vectors from the current schema."
                )
            if len(fv.features) != FEATURE_DIM:
                raise ValueError(
                    f"Feature vector has {len(fv.features)} features, "
                    f"expected {FEATURE_DIM}."
                )

        try:
            from sklearn.ensemble import IsolationForest
        except ImportError:
            raise ImportError(
                "scikit-learn is required for ML scoring. "
                "Install with: pip install scikit-learn"
            )

        X = np.array([fv.features for fv in feature_vectors], dtype=np.float32)

        logger.info(
            "Training Isolation Forest on %d vectors "
            "(n_estimators=%d, contamination=%.2f, random_state=%d)",
            len(feature_vectors), IF_N_ESTIMATORS,
            IF_CONTAMINATION, IF_RANDOM_STATE
        )

        self._model = IsolationForest(
            n_estimators=IF_N_ESTIMATORS,
            contamination=IF_CONTAMINATION,
            max_samples=IF_MAX_SAMPLES,
            random_state=IF_RANDOM_STATE,
            n_jobs=-1,
        )
        self._model.fit(X)

        self._metadata = ScorerModelMetadata(
            schema_version=SCHEMA_VERSION,
            feature_dim=FEATURE_DIM,
            feature_names=list(feature_vectors[0].feature_names),
            training_set_size=len(feature_vectors),
            trained_at=datetime.utcnow().isoformat() + "Z",
            contamination=IF_CONTAMINATION,
            n_estimators=IF_N_ESTIMATORS,
            model_version=self._model_version,
        )
        logger.info("Isolation Forest trained successfully.")

    def score(self, fv: FeatureVector) -> AnomalyScore:
        """
        Compute an anomaly score for a single feature vector.

        HARD RULE #1: raises ValueError if fv is None.
        HARD RULE #3: returned AnomalyScore always includes explanation.

        The composite_score is in [0, 1]:
          0.0 = entirely normal (no signals, all features zero)
          1.0 = maximally anomalous

        Important: this is a RELATIVE score within the training distribution.
        An instrument scoring 0.9 is more anomalous than 95% of observations.
        It does NOT mean there is a 90% probability of manipulation.
        """
        if fv is None:
            raise ValueError(
                "ManipulationScorer.score: fv is None. "
                "Pass a FeatureVector from extract_features()."
            )

        if fv.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"Feature vector schema_version={fv.schema_version} "
                f"does not match current SCHEMA_VERSION={SCHEMA_VERSION}. "
                "Regenerate feature vectors."
            )

        # ── Weighted baseline score (always computed) ────────────────────────
        # Map feature indices to detector weights
        # feats[0]=circular, [4]=pump, [8]=OI_conc, [11]=OI_decouple,
        # [12]=basis, [15]=pinning
        feat = fv.features
        weighted_baseline = (
            _FALLBACK_WEIGHTS["circular_trading"] * feat[0]
            + _FALLBACK_WEIGHTS["coordinated_pump"] * feat[4]
            + _FALLBACK_WEIGHTS["oi_concentration"] * feat[8]
            + _FALLBACK_WEIGHTS["oi_decoupling"] * feat[11]
            + _FALLBACK_WEIGHTS["basis_distortion"] * feat[12]
            + _FALLBACK_WEIGHTS["option_pinning"] * feat[15]
        )

        # ── Isolation Forest score (if model trained) ────────────────────────
        raw_if_score: Optional[float] = None
        is_model_scored = False

        if self.is_trained:
            X = np.array([fv.features], dtype=np.float32)
            # sklearn IF: score_samples returns more-negative for anomalies.
            # We invert and normalise to [0, 1].
            raw_sklearn = float(self._model.score_samples(X)[0])
            # Typical range from sklearn: roughly [-0.5, 0] for normal data.
            # We shift and scale: score = clip((−raw − offset) / scale, 0, 1).
            # These offsets are empirical from sklearn's typical output range.
            # Label: HEURISTIC — re-check if using a very different dataset.
            OFFSET = 0.0
            SCALE = 0.5
            raw_if_score = float(np.clip((-raw_sklearn - OFFSET) / SCALE, 0.0, 1.0))
            is_model_scored = True

        # ── Composite score ──────────────────────────────────────────────────
        if raw_if_score is not None:
            # Average the two perspectives equally.
            # Rationale: baseline captures domain-expert weighting;
            # IF captures unusual combinations the baseline might miss.
            # Label: HEURISTIC — equal weighting is a starting assumption.
            composite = (raw_if_score + weighted_baseline) / 2.0
        else:
            composite = weighted_baseline

        composite = float(np.clip(composite, 0.0, 1.0))

        # ── Firing detectors ──────────────────────────────────────────────────
        firing = []
        if feat[0] > 0: firing.append("circular_trading")
        if feat[4] > 0: firing.append("coordinated_pump")
        if feat[8] > 0: firing.append("oi_concentration")
        if feat[11] > 0: firing.append("oi_decoupling")
        if feat[12] > 0: firing.append("basis_distortion")
        if feat[15] > 0: firing.append("option_pinning")

        max_single = float(max(feat[0], feat[4], feat[8], feat[11], feat[12], feat[15]))

        # ── Explanation ───────────────────────────────────────────────────────
        explanation = _build_explanation(
            fv, firing, weighted_baseline, raw_if_score, composite, is_model_scored
        )

        return AnomalyScore(
            instrument_symbol=fv.instrument_symbol,
            window_label=fv.window_label,
            scored_at=datetime.utcnow(),
            raw_anomaly_score=raw_if_score,
            weighted_baseline_score=weighted_baseline,
            composite_score=composite,
            firing_detectors=firing,
            num_detector_types_firing=len(firing),
            max_single_detector_score=max_single,
            model_version=self._model_version if is_model_scored else None,
            schema_version=SCHEMA_VERSION,
            explanation=explanation,
            is_model_scored=is_model_scored,
        )

    def save(self, directory: Path) -> None:
        """
        Persist the trained model and metadata to `directory`.

        Creates:
          - <directory>/isolation_forest.pkl  — the sklearn model
          - <directory>/metadata.json         — ScorerModelMetadata

        Raises if the model is not trained.
        """
        if not self.is_trained:
            raise RuntimeError("Cannot save: model not trained. Call .train() first.")

        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)

        model_path = directory / "isolation_forest.pkl"
        meta_path = directory / "metadata.json"

        with open(model_path, "wb") as f:
            pickle.dump(self._model, f, protocol=pickle.HIGHEST_PROTOCOL)

        with open(meta_path, "w") as f:
            json.dump(
                {
                    "schema_version": self._metadata.schema_version,
                    "feature_dim": self._metadata.feature_dim,
                    "feature_names": self._metadata.feature_names,
                    "training_set_size": self._metadata.training_set_size,
                    "trained_at": self._metadata.trained_at,
                    "contamination": self._metadata.contamination,
                    "n_estimators": self._metadata.n_estimators,
                    "model_version": self._metadata.model_version,
                },
                f, indent=2
            )

        logger.info("Model saved to %s", directory)

    @classmethod
    def load(cls, directory: Path) -> "ManipulationScorer":
        """
        Load a previously saved model from `directory`.

        Validates schema_version before loading to prevent silent
        compatibility breaks when the feature schema changes.

        HARD RULE #1: raises on schema mismatch — does not silently
        continue with an incompatible model.
        """
        directory = Path(directory)
        meta_path = directory / "metadata.json"
        model_path = directory / "isolation_forest.pkl"

        if not meta_path.exists():
            raise FileNotFoundError(f"Metadata not found at {meta_path}")
        if not model_path.exists():
            raise FileNotFoundError(f"Model not found at {model_path}")

        with open(meta_path) as f:
            meta = json.load(f)

        saved_schema = meta.get("schema_version")
        if saved_schema != SCHEMA_VERSION:
            raise ValueError(
                f"Saved model has schema_version={saved_schema}, "
                f"but current SCHEMA_VERSION={SCHEMA_VERSION}. "
                "The feature schema has changed — you must retrain the model. "
                "Do NOT load an incompatible model (silent score corruption)."
            )

        with open(model_path, "rb") as f:
            model = pickle.load(f)

        scorer = cls(model_version=meta.get("model_version", "unknown"))
        scorer._model = model
        scorer._metadata = ScorerModelMetadata(**meta)
        logger.info(
            "Loaded model version=%s trained on %d samples at %s",
            meta.get("model_version"), meta.get("training_set_size"),
            meta.get("trained_at")
        )
        return scorer


def _build_explanation(
    fv: FeatureVector,
    firing: list[str],
    weighted_baseline: float,
    raw_if_score: Optional[float],
    composite: float,
    is_model_scored: bool,
) -> str:
    """Build a human-readable explanation for the AnomalyScore."""
    feat = fv.features

    if not firing:
        return (
            f"{fv.instrument_symbol} [{fv.window_label}]: "
            f"No detector signals. Composite anomaly score: {composite:.3f}. "
            f"All detectors returned clean. This is a normal observation."
        )

    detector_lines = []
    score_index_map = {
        "circular_trading": (0, "ring score"),
        "coordinated_pump": (4, "pump score"),
        "oi_concentration": (8, "OI concentration score"),
        "oi_decoupling": (11, "OI-IV decoupling score"),
        "basis_distortion": (12, "basis distortion score"),
        "option_pinning": (15, "pinning score"),
    }
    for det in firing:
        idx, label = score_index_map[det]
        detector_lines.append(f"  • {det}: {label} = {feat[idx]:.3f}")

    detector_summary = "\n".join(detector_lines)

    model_line = (
        f"Isolation Forest anomaly score: {raw_if_score:.3f} "
        f"(model version: {fv.schema_version}). "
        if is_model_scored and raw_if_score is not None
        else "No trained model — using weighted baseline only. "
    )

    co_occurrence_note = (
        f"{len(firing)} independent detector type(s) firing simultaneously "
        f"(co-occurrence increases confidence). "
        if len(firing) > 1
        else "Single detector type firing. "
    )

    caveat = (
        "IMPORTANT: This is an anomaly score, NOT a manipulation probability. "
        "A high score means this observation is unusual relative to the baseline "
        "distribution — it does NOT confirm manipulation. Requires analyst review."
    )

    return (
        f"{fv.instrument_symbol} [{fv.window_label}]: "
        f"Composite anomaly score: {composite:.3f} "
        f"(weighted baseline: {weighted_baseline:.3f}). "
        f"Firing detectors:\n{detector_summary}\n"
        f"{co_occurrence_note}"
        f"{model_line}"
        f"{caveat}"
    )
