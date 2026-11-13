"""
app/ml/train_classifier.py
============================
Train the ManipulationClassifier (stage-2 on top of OHLCVScorer IsolationForest).

Strategy (weakly supervised):
  - Positive  : feature vectors from sessions that triggered at least one detector
  - Negative  : feature vectors from normal (non-flagged) sessions
  - Source    : live Fyers OHLCV data (same as train_ohlcv.py)

Usage:
    python -m app.ml.train_classifier

After training, also backfills all existing DB alerts with fresh ml_confidence scores.
"""

import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

# Repo root on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(name)-30s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sentinel.train_classifier")

WATCHLIST = {
    "NIFTY50":    "NSE:NIFTY50-INDEX",
    "BANKNIFTY":  "NSE:NIFTYBANK-INDEX",
    "RELIANCE":   "NSE:RELIANCE-EQ",
    "TCS":        "NSE:TCS-EQ",
    "HDFCBANK":   "NSE:HDFCBANK-EQ",
    "ICICIBANK":  "NSE:ICICIBANK-EQ",
    "INFY":       "NSE:INFY-EQ",
    "HINDUNILVR": "NSE:HINDUNILVR-EQ",
    "ITC":        "NSE:ITC-EQ",
    "SBIN":       "NSE:SBIN-EQ",
    "BHARTIARTL": "NSE:BHARTIARTL-EQ",
    "KOTAKBANK":  "NSE:KOTAKBANK-EQ",
    "LT":         "NSE:LT-EQ",
    "AXISBANK":   "NSE:AXISBANK-EQ",
}
HISTORY_DAYS = 90


def _collect_feature_vectors():
    """
    Collect (feature_vector, is_flagged) pairs for all watchlist symbols.
    Returns X_pos (ndarray), X_neg (ndarray).
    """
    from data.ingest.fyers_stream import FyersMarketDataAdapter
    from app.ml.ohlcv_scorer import build_feature_vector, get_scorer
    from app.ml.manipulation_classifier import build_clf_features
    from app.workers.detection_worker import (
        detect_volume_spike,
        detect_price_zscore,
        detect_thin_volume_pump,
        detect_momentum_reversal,
        detect_sustained_volume,
    )

    adapter = FyersMarketDataAdapter()
    if not adapter.auth_manager.is_token_valid():
        raise RuntimeError(
            "Fyers not authenticated. Set a valid token first:\n"
            "  python -m app.ml.train_ohlcv  (runs authentication check)"
        )

    scorer = get_scorer()
    if not scorer.is_trained:
        raise RuntimeError(
            "OHLCVScorer (Isolation Forest) must be trained first.\n"
            "Run: python -m app.ml.train_ohlcv"
        )

    to_date   = date.today() - timedelta(days=1)
    from_date = to_date - timedelta(days=HISTORY_DAYS)

    X_pos, X_neg = [], []
    skipped = []

    import time
    for symbol, fyers_sym in WATCHLIST.items():
        time.sleep(0.35)
        logger.info("Collecting %s (%s)…", symbol, fyers_sym)
        try:
            bars = adapter.get_history(
                fyers_sym,
                "D",
                HISTORY_DAYS,
                from_date.isoformat(),
                to_date.isoformat(),
            )
            if not bars or len(bars) < 10:
                skipped.append(f"{symbol}: only {len(bars) if bars else 0} bars")
                continue

            # Drop today's incomplete bar
            completed = [b for b in bars if b.get("date_str", "") < date.today().isoformat()]
            logger.info("  Got %d completed bars", len(completed))

            for bar in completed:
                bar_date = bar.get("date_str") or bar.get("date")
                if not bar_date:
                    continue
                try:
                    bar_date_obj = date.fromisoformat(str(bar_date)[:10])
                except Exception:
                    continue

                # Build detection dicts for this single bar
                single_bar_dets = []
                for detect_fn in [
                    detect_volume_spike,
                    detect_price_zscore,
                    detect_thin_volume_pump,
                    detect_momentum_reversal,
                    detect_sustained_volume,
                ]:
                    try:
                        result = detect_fn(symbol, [bar], bar_date_obj)
                        if result:
                            d = result if isinstance(result, dict) else vars(result)
                            d.setdefault("event_date", bar_date_obj)
                            single_bar_dets.append(d)
                    except Exception:
                        pass

                is_flagged = len(single_bar_dets) > 0

                fv = build_feature_vector(symbol, bar_date_obj, single_bar_dets)
                ml_score_if, _ = scorer.score(fv)
                heuristic = fv.heuristic_score()
                clf_x = build_clf_features(fv, ml_score_if, heuristic)

                if is_flagged:
                    X_pos.append(clf_x)
                else:
                    X_neg.append(clf_x)

        except Exception as e:
            skipped.append(f"{symbol}: {e}")
            logger.warning("Skipped %s: %s", symbol, e)

    if skipped:
        logger.warning("Skipped symbols: %s", skipped)

    return np.array(X_pos, dtype=np.float32), np.array(X_neg, dtype=np.float32)


def backfill_existing_alerts():
    """Score all existing DB alerts that have ml_anomaly_score=NULL."""
    from app.db.session import get_db
    from app.db.models import Alert, Instrument
    from app.ml.ohlcv_scorer import build_feature_vector, get_scorer, OHLCVFeatureVector

    logger.info("Backfilling existing alerts with ML Confidence scores…")
    db = next(get_db())
    try:
        alerts = db.query(Alert).filter(Alert.ml_anomaly_score == None).all()  # noqa: E711
        logger.info("Found %d alerts with no ML confidence score", len(alerts))
        scorer = get_scorer()

        for alert in alerts:
            inst = db.query(Instrument).filter(Instrument.id == alert.instrument_id).first()
            symbol = inst.symbol if inst else "UNKNOWN"
            ev_date = alert.event_date or date.today()

            # Build a feature vector from the alert's own score + pattern type
            det_dict = {
                "pattern_type": alert.pattern_type,
                "score": alert.score,
                "event_date": ev_date,
            }
            fv = build_feature_vector(symbol, ev_date, [det_dict])
            ml_score, method = scorer.score(fv)

            alert.ml_anomaly_score = ml_score
            alert.score_method     = method
            logger.info(
                "  Alert %s (%s/%s): ml_confidence=%.4f method=%s",
                str(alert.id)[:8], symbol, alert.pattern_type, ml_score, method
            )

        db.commit()
        logger.info("Backfill complete: %d alerts updated", len(alerts))
    finally:
        db.close()


def main():
    logger.info("=" * 60)
    logger.info("Step 1: Collecting feature vectors from Fyers…")
    X_pos, X_neg = _collect_feature_vectors()
    logger.info("Collected: %d positive, %d negative", len(X_pos), len(X_neg))

    logger.info("Step 2: Training ManipulationClassifier…")
    from app.ml.manipulation_classifier import ManipulationClassifier, invalidate_classifier
    clf = ManipulationClassifier()
    meta = clf.train(X_pos, X_neg)
    clf.save()
    invalidate_classifier()  # force reload next use

    logger.info("Step 3: Backfilling existing alerts…")
    backfill_existing_alerts()

    print("\n" + "=" * 60)
    print("ManipulationClassifier training complete!")
    print(f"  Positive (flagged) sessions : {meta['n_positive']}")
    print(f"  Negative (normal) sessions  : {meta['n_negative']}")
    print(f"  Cross-validated AUC         : {meta['cv_auc_mean']}")
    print(f"  Method label                : ml_classifier")
    print(f"  Model saved to              : app/ml/models/classifier/")
    print("\nNote:", meta['disclaimer'])
    print("=" * 60)


if __name__ == "__main__":
    main()
