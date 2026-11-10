"""
backtest/fyers_detector_backtest.py
====================================
Phase 8: Calibration / Backtest of Sentinel Detectors over Fyers Daily History.

Replays all OHLCV-based Sentinel detectors across 60–90 historical trading sessions
for 15 Indian equity watchlist symbols using Fyers API v3 get_history().

Detectors evaluated per session:
  1. volume_spike       (session vol vs baseline avg vol)
  2. price_anomaly      (close-to-close return z-score vs baseline distribution)
  3. thin_volume_pump   (large return on thin volume)
  4. momentum_reversal  (3-session run-up + sharp reversal)
  5. sustained_volume   (3+ consecutive sessions of abnormal volume)

Methodology:
  - For each symbol, fetch daily OHLCV bars (60–90 sessions).
  - For session bar t (starting after MIN_HISTORY_DAYS baseline):
      * baseline is strictly bars before t (bars[:t])
      * evaluate detectors for bar t
  - Aggregates:
      * total symbol-days evaluated
      * triggers count per detector
      * empirical trigger rate (%) per detector
  - Saves full structured report to backtest/results/phase8_calibration.json
  - Logs execution details to backtest/results/phase8_calibration.log

Rate limiting:
  Fyers Standard plan allows 5 req/s. We enforce a 0.25s sleep between history calls.
"""

import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.workers.detection_worker import (
    MIN_HISTORY_DAYS,
    _evaluate_session,
    detect_momentum_reversal,
    detect_sustained_volume,
    build_fyers_symbol,
)

# ── Results & Logs Directory ────────────────────────────────────────────────
RESULTS_DIR = REPO_ROOT / "backtest" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = RESULTS_DIR / "phase8_calibration.log"
OUTPUT_JSON = RESULTS_DIR / "phase8_calibration.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
    ],
)
logger = logging.getLogger("phase8_backtest")

# 15 Liquid Indian Equity Symbols
WATCHLIST_15 = [
    "RELIANCE",
    "TCS",
    "HDFCBANK",
    "ICICIBANK",
    "INFY",
    "HINDUNILVR",
    "ITC",
    "SBIN",
    "BHARTIARTL",
    "KOTAKBANK",
    "LT",
    "AXISBANK",
    "BAJFINANCE",
    "MARUTI",
    "TATASTEEL",
]

SESSIONS_TARGET = 90  # 60–90 daily sessions


def run_fyers_backtest(
    symbols: List[str] = WATCHLIST_15,
    days: int = 130,  # ~90 trading days span ~130 calendar days
    rate_limit_delay: float = 0.25,
    mock_data: Optional[Dict[str, List[Dict]]] = None,
) -> Dict[str, Any]:
    """
    Run backtest across all symbols and return summary report.
    """
    logger.info("=" * 70)
    logger.info("STARTING PHASE 8 CALIBRATION BACKTEST")
    logger.info("Watchlist symbols (%d): %s", len(symbols), ", ".join(symbols))
    logger.info("Min baseline sessions: %d", MIN_HISTORY_DAYS)
    logger.info("Log file: %s", LOG_FILE)
    logger.info("=" * 70)

    adapter = None
    if not mock_data:
        try:
            from data.ingest.fyers_stream import get_fyers_adapter
            adapter = get_fyers_adapter()
            status = adapter.get_connection_status()
            logger.info("Fyers adapter status: %s", status)
            if not status.get("token_valid"):
                logger.warning(
                    "Fyers token is not valid for today. Backtest will fail if live fetch is attempted."
                )
        except Exception as e:
            logger.error("Failed to initialize Fyers adapter: %s", e)

    total_symbol_days = 0
    detector_counts: Dict[str, int] = {
        "volume_spike": 0,
        "price_anomaly": 0,
        "thin_volume_pump": 0,
        "momentum_reversal": 0,
        "sustained_volume": 0,
    }

    per_symbol_results: Dict[str, Any] = {}
    all_detections: List[Dict[str, Any]] = []

    for sym in symbols:
        logger.info("Processing %s...", sym)
        bars: List[Dict] = []

        if mock_data and sym in mock_data:
            bars = mock_data[sym]
            logger.info("Loaded %d mock bars for %s", len(bars), sym)
        elif adapter:
            fyers_sym = build_fyers_symbol(sym, "EQUITY")
            try:
                time.sleep(rate_limit_delay)
                history = adapter.get_history(fyers_sym, resolution="D", days=days)
                if history:
                    # Convert to standard format
                    for h in history:
                        ts = h.get("timestamp")
                        bar_date = (
                            datetime.utcfromtimestamp(ts).date() if ts else None
                        )
                        bars.append(
                            {
                                "date": bar_date,
                                "timestamp": ts,
                                "open": float(h.get("open", 0)),
                                "high": float(h.get("high", 0)),
                                "low": float(h.get("low", 0)),
                                "close": float(h.get("close", 0)),
                                "volume": float(h.get("volume", 0)),
                            }
                        )
                    logger.info("Fetched %d historical bars for %s (%s)", len(bars), sym, fyers_sym)
                else:
                    logger.warning("No history returned for %s", sym)
            except Exception as e:
                logger.error("Error fetching history for %s: %s", sym, e)

        if len(bars) < MIN_HISTORY_DAYS + 1:
            logger.warning(
                "Skipping %s: insufficient bars (%d < %d required)",
                sym,
                len(bars),
                MIN_HISTORY_DAYS + 1,
            )
            per_symbol_results[sym] = {
                "bars_count": len(bars),
                "evaluated_sessions": 0,
                "error": "insufficient_bars",
            }
            continue

        # Evaluate each session from MIN_HISTORY_DAYS onwards
        sym_eval_days = 0
        sym_detections: List[Dict] = []

        for t in range(MIN_HISTORY_DAYS, len(bars)):
            session_bar = bars[t]
            baseline_bars = bars[:t]  # strictly before session t
            ltp = session_bar["close"]
            ev_date = session_bar["date"] or date.today()

            # 1. Single-session detectors (volume_spike, price_anomaly, thin_volume_pump)
            dets = _evaluate_session(sym, session_bar, baseline_bars, ltp)

            # 2. Multi-day detector: momentum_reversal (needs closed bars up to t)
            bars_up_to_t = bars[: t + 1]
            rev_det = detect_momentum_reversal(sym, bars_up_to_t, ev_date)
            if rev_det:
                dets.append(rev_det)

            # 3. Multi-day detector: sustained_volume (needs closed bars up to t)
            sust_det = detect_sustained_volume(sym, bars_up_to_t, ev_date)
            if sust_det:
                dets.append(sust_det)

            sym_eval_days += 1
            total_symbol_days += 1

            for d in dets:
                pt = d.get("pattern_type")
                if pt in detector_counts:
                    detector_counts[pt] += 1
                sym_detections.append(
                    {
                        "symbol": sym,
                        "event_date": str(d.get("event_date")),
                        "pattern_type": pt,
                        "severity": d.get("severity"),
                        "score": d.get("score"),
                        "explanation": d.get("explanation"),
                    }
                )
                all_detections.append(sym_detections[-1])

        per_symbol_results[sym] = {
            "bars_count": len(bars),
            "evaluated_sessions": sym_eval_days,
            "detections_count": len(sym_detections),
            "detections": sym_detections,
        }
        logger.info(
            "Completed %s: %d sessions evaluated, %d anomalies detected",
            sym,
            sym_eval_days,
            len(sym_detections),
        )

    # Calculate trigger rates
    detector_rates: Dict[str, Any] = {}
    for d_name, count in detector_counts.items():
        rate = (count / total_symbol_days * 100) if total_symbol_days > 0 else 0.0
        detector_rates[d_name] = {
            "trigger_count": count,
            "total_symbol_days": total_symbol_days,
            "trigger_rate_pct": round(rate, 3),
        }

    report = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "symbols_requested": len(symbols),
        "total_symbol_days_evaluated": total_symbol_days,
        "detector_summary": detector_rates,
        "per_symbol_summary": per_symbol_results,
        "total_detections": len(all_detections),
        "log_file": str(LOG_FILE),
    }

    OUTPUT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info("Saved calibration report to %s", OUTPUT_JSON)

    logger.info("=" * 70)
    logger.info("PHASE 8 CALIBRATION BACKTEST SUMMARY:")
    logger.info("Total Symbol-Days Evaluated: %d", total_symbol_days)
    for d_name, stat in detector_rates.items():
        logger.info(
            "  - %-20s: %4d triggers (%6.2f%%)",
            d_name,
            stat["trigger_count"],
            stat["trigger_rate_pct"],
        )
    logger.info("=" * 70)

    return report


if __name__ == "__main__":
    run_fyers_backtest()

