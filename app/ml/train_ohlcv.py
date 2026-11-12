"""
app/ml/train_ohlcv.py
======================
Training script for the OHLCVScorer Isolation Forest.

Fetches 90-day OHLCV history for all 15 Sentinel watchlist symbols via
Fyers API v3, runs all 5 OHLCV-based detectors across every completed session,
collects feature vectors (normal AND anomalous), trains the Isolation Forest,
and saves the model to app/ml/models/ohlcv/.

Usage:
    python -m app.ml.train_ohlcv                     # train and save
    python -m app.ml.train_ohlcv --dry-run           # collect vectors, do not save
    python -m app.ml.train_ohlcv --show-stats        # print vector stats

This should be re-run:
  - After every 30 trading days (to include recent market regime)
  - After changing any OHLCV detector threshold in threshold_registry.py
  - After changing OHLCV_SCORER_SCHEMA_VERSION

HARD RULE: Does not use synthetic data. All vectors come from real Fyers OHLCV.
If Fyers is not authenticated, the script exits with a clear error.
"""

import argparse
import json
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import List

# ---------------------------------------------------------------------------
# Add repo root to path so this can be run as a module
# ---------------------------------------------------------------------------
_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from app.ml.ohlcv_scorer import OHLCVScorer, OHLCVFeatureVector, build_feature_vector
from app.workers.detection_worker import (
    run_detection_for_symbol,
    build_fyers_symbol,
    LOOKBACK_SESSIONS,
    MIN_HISTORY_DAYS,
    detect_momentum_reversal,
    detect_sustained_volume,
    detect_volume_spike,
    detect_price_zscore,
    detect_thin_volume_pump,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sentinel.train_ohlcv")

# ---------------------------------------------------------------------------
# Training configuration
# ---------------------------------------------------------------------------

# 90-day lookback for training (more sessions = better IF baseline distribution)
TRAIN_LOOKBACK_DAYS: int = 95
TRAIN_LOOKBACK_SESSIONS: int = 85   # sessions evaluated per symbol

WATCHLIST = [
    ("NIFTY50",    "INDEX"),
    ("BANKNIFTY",  "INDEX"),
    ("FINNIFTY",   "INDEX"),
    ("RELIANCE",   "EQUITY"),
    ("TCS",        "EQUITY"),
    ("HDFCBANK",   "EQUITY"),
    ("ICICIBANK",  "EQUITY"),
    ("INFY",       "EQUITY"),
    ("HINDUNILVR", "EQUITY"),
    ("ITC",        "EQUITY"),
    ("SBIN",       "EQUITY"),
    ("BHARTIARTL", "EQUITY"),
    ("KOTAKBANK",  "EQUITY"),
    ("LT",         "EQUITY"),
    ("AXISBANK",   "EQUITY"),
]

# Rate limit: Fyers Standard plan = 5 req/s. Keep each history call separate.
RATE_LIMIT_SLEEP: float = 0.35


def _bar_date(bar: dict):
    """Extract the trading date from a bar dict."""
    import pytz
    from datetime import datetime as _dt
    IST = pytz.timezone("Asia/Kolkata")

    d = bar.get("date")
    if isinstance(d, date):
        return d
    if isinstance(d, str):
        try:
            return date.fromisoformat(d[:10])
        except ValueError:
            pass
    ts = bar.get("timestamp") or bar.get("ts")
    if ts:
        try:
            return _dt.utcfromtimestamp(int(ts)).astimezone(IST).date()
        except Exception:
            pass
    return None


def collect_feature_vectors(adapter, dry_run: bool = False) -> List[OHLCVFeatureVector]:
    """
    For each watchlist symbol, fetch 90-day OHLCV from Fyers, run all OHLCV
    detectors on every completed session, and return a list of OHLCVFeatureVectors
    (one per symbol-session, including sessions where score=0 — those are
    the "normal" observations that the Isolation Forest needs to learn from).
    """
    import pytz
    from datetime import datetime as _dt
    IST = pytz.timezone("Asia/Kolkata")

    today_ist = _dt.now(IST).date()
    to_date   = (today_ist - timedelta(days=1)).isoformat()
    from_date = (today_ist - timedelta(days=TRAIN_LOOKBACK_DAYS)).isoformat()

    all_vectors: List[OHLCVFeatureVector] = []
    skipped = []

    for symbol, itype in WATCHLIST:
        fsym = build_fyers_symbol(symbol, itype)
        if not fsym:
            skipped.append(f"{symbol}: FUTURE — expiry not resolved")
            continue

        logger.info("Fetching %s (%s)…", symbol, fsym)
        try:
            bars = adapter.get_history(fsym, from_date=from_date, to_date=to_date, resolution="D")
        except Exception as exc:
            logger.warning("  get_history failed: %s — skipping", exc)
            skipped.append(f"{symbol}: {exc}")
            time.sleep(RATE_LIMIT_SLEEP)
            continue

        if not bars:
            logger.warning("  No bars returned — skipping")
            skipped.append(f"{symbol}: no bars")
            time.sleep(RATE_LIMIT_SLEEP)
            continue

        # Drop today's partial candle
        completed_bars = [b for b in bars if _bar_date(b) != today_ist]

        if len(completed_bars) < MIN_HISTORY_DAYS + 1:
            logger.warning(
                "  Only %d completed bars — need at least %d. Skipping.",
                len(completed_bars), MIN_HISTORY_DAYS + 1
            )
            skipped.append(f"{symbol}: insufficient bars ({len(completed_bars)})")
            time.sleep(RATE_LIMIT_SLEEP)
            continue

        logger.info("  Got %d completed bars — running detectors across all sessions", len(completed_bars))

        # For each session t (from MIN_HISTORY_DAYS onward), collect detections
        for t_idx in range(MIN_HISTORY_DAYS, len(completed_bars)):
            session_bar   = completed_bars[t_idx]
            baseline_bars = completed_bars[:t_idx]
            ev_date       = _bar_date(session_bar) or today_ist

            session_dets = []

            # Per-session detectors
            s_close = session_bar.get("close", 0) or 0
            if s_close <= 0:
                continue

            bvols = [b.get("volume", 0) or 0 for b in baseline_bars if b.get("volume", 0)]
            avg_vol = sum(bvols) / len(bvols) if bvols else 0.0

            baseline_returns = []
            for i in range(1, len(baseline_bars)):
                c_prev = baseline_bars[i - 1].get("close", 0) or 0
                c_curr = baseline_bars[i].get("close", 0) or 0
                if c_prev > 0 and c_curr > 0:
                    baseline_returns.append((c_curr - c_prev) / c_prev * 100)

            prev_close = (baseline_bars[-1].get("close", 0) or 0) if baseline_bars else 0
            session_return_pct = (
                (s_close - prev_close) / prev_close * 100
                if prev_close > 0 and s_close > 0
                else 0.0
            )
            s_vol = session_bar.get("volume", 0) or 0

            ltp = s_close  # for training use close as "current price"

            d = detect_volume_spike(symbol, s_vol, avg_vol, s_close, session_return_pct, ev_date)
            if d:
                session_dets.append(d)

            d = detect_price_zscore(symbol, session_return_pct, baseline_returns, s_close, ev_date)
            if d:
                session_dets.append(d)

            d = detect_thin_volume_pump(symbol, s_vol, avg_vol, session_return_pct, s_close, ev_date)
            if d:
                session_dets.append(d)

            # Multi-session detectors only at end of a window
            if t_idx >= len(completed_bars) - 1 or t_idx % 5 == 0:
                d = detect_momentum_reversal(symbol, completed_bars[:t_idx + 1], ev_date)
                if d:
                    session_dets.append(d)

                d = detect_sustained_volume(symbol, completed_bars[:t_idx + 1], ev_date)
                if d:
                    session_dets.append(d)

            fv = build_feature_vector(symbol, ev_date, session_dets)
            all_vectors.append(fv)

        logger.info(
            "  Added %d feature vectors (this symbol). Anomalous: %d",
            len(completed_bars) - MIN_HISTORY_DAYS,
            sum(1 for fv in all_vectors[-len(completed_bars):] if fv.n_firing > 0)
        )
        time.sleep(RATE_LIMIT_SLEEP)

    if skipped:
        logger.warning("Skipped symbols: %s", skipped)

    return all_vectors


def print_stats(vectors: List[OHLCVFeatureVector]) -> None:
    """Print summary statistics about the collected feature vectors."""
    if not vectors:
        print("No vectors collected.")
        return

    total = len(vectors)
    firing = [fv for fv in vectors if fv.n_firing > 0]
    multi  = [fv for fv in vectors if fv.n_firing > 1]

    scores = [fv.heuristic_score() for fv in vectors]
    scores_sorted = sorted(scores, reverse=True)

    print(f"\n{'='*60}")
    print(f"OHLCV Feature Vector Statistics")
    print(f"{'='*60}")
    print(f"  Total vectors:          {total}")
    print(f"  Anomalous (≥1 det):    {len(firing)} ({100*len(firing)/total:.1f}%)")
    print(f"  Multi-detector (≥2):   {len(multi)} ({100*len(multi)/total:.1f}%)")
    print(f"  Heuristic score > 0.4: {sum(1 for s in scores if s > 0.4)}")
    print(f"  Heuristic score > 0.6: {sum(1 for s in scores if s > 0.6)}")
    print(f"  Top 10 heuristic:      {[round(s, 3) for s in scores_sorted[:10]]}")

    # Per-detector firing counts
    print(f"\n  Detector firing rates:")
    for attr in ["vol_spike_score","price_anomaly_score","thin_vol_score","momentum_score","sustained_vol_score"]:
        cnt = sum(1 for fv in vectors if getattr(fv, attr) > 0)
        print(f"    {attr:30s} {cnt:4d} ({100*cnt/total:.1f}%)")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Train OHLCVScorer Isolation Forest")
    parser.add_argument("--dry-run",    action="store_true", help="Collect vectors but do not train/save")
    parser.add_argument("--show-stats", action="store_true", help="Print vector statistics")
    args = parser.parse_args()

    # Import here (after sys.path is set)
    from data.ingest.fyers_stream import FyersMarketDataAdapter

    adapter = FyersMarketDataAdapter()
    if not adapter.auth_manager.is_token_valid():
        logger.error(
            "Fyers token is not valid. Run the dashboard Quick-Auth to authenticate first.\n"
            "Cannot train on real data without a valid token — HARD RULE."
        )
        sys.exit(1)

    logger.info("Fyers authenticated. Collecting feature vectors from %d symbols…", len(WATCHLIST))
    vectors = collect_feature_vectors(adapter, dry_run=args.dry_run)

    if args.show_stats or args.dry_run:
        print_stats(vectors)

    if args.dry_run:
        logger.info("Dry run — not training or saving.")
        return

    logger.info("Collected %d feature vectors. Training Isolation Forest…", len(vectors))
    scorer = OHLCVScorer()
    scorer.train(vectors)
    scorer.save()

    # Validate: score a few known anomalous vectors
    from app.ml.ohlcv_scorer import invalidate_scorer
    invalidate_scorer()
    scorer2 = OHLCVScorer.load()
    test_fv = OHLCVFeatureVector(
        symbol="TEST", event_date=date.today(),
        price_anomaly_score=0.667, vol_spike_score=0.412
    )
    sc, method = scorer2.score(test_fv)
    logger.info("Validation score for synthetic anomalous vector: %.4f (method=%s)", sc, method)

    test_normal = OHLCVFeatureVector(symbol="TEST", event_date=date.today())
    sc2, _ = scorer2.score(test_normal)
    logger.info("Validation score for normal (all-zero) vector:   %.4f", sc2)

    if sc2 >= sc:
        logger.warning(
            "WARNING: Normal vector scored >= anomalous vector (%.4f >= %.4f). "
            "IF may need more training data or threshold tuning.",
            sc2, sc
        )
    else:
        logger.info("OK: anomalous > normal (%.4f > %.4f)", sc, sc2)

    print(f"\nModel saved to: {scorer._model_dir}")
    print(f"Training vectors: {len(vectors)}")
    print(f"To use: get_scorer() in app/ml/ohlcv_scorer.py auto-loads the saved model.\n")


if __name__ == "__main__":
    main()

