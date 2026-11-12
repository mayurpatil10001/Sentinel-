"""
app/workers/detection_worker.py
================================
Detection pipeline for Sentinel.  Called by the scheduler (see app/scheduler.py)
and also directly from the manual-trigger API endpoint.

DATA SOURCES (what we actually have via Fyers):
  ✅ Fyers live quotes    — LTP, volume, prev_close (batch, 1 req for all symbols)
  ✅ Fyers daily OHLCV    — 30-day history via get_history() (1 req/symbol, rate-limited)
  ✅ NSE Option Chain     — OI per strike (Fyers API)
  ❌ Market-wide order book — unavailable via broker APIs
  ❌ Other participants' orders

DETECTORS (phase 1 — OHLCV-based):
  1. volume_spike         — Session vol vs N-session avg
  2. price_anomaly        — Session return z-score vs baseline
  3. thin_volume_pump     — Big price move on tiny volume
  4. momentum_reversal    — 3-session run-up then reversal (classic P&D shape)
  5. oi_concentration     — Top-3 strikes hold >65% OI [INDEX only]
  6. sustained_volume     — 3+ consecutive sessions of abnormal volume (multi-day)

LOOKBACK: each call evaluates the last N sessions (default 5, configurable via
LOOKBACK_SESSIONS env var).  For session bar t, the baseline uses all bars
strictly BEFORE t (not t itself, and not today's partial candle).

RETURN CONSISTENCY (Phase 1 methodology fix):
  We use CLOSE-TO-CLOSE returns for both historical baseline and the event session.
  Rationale: change_pct from the quote API is prev_close→LTP (intraday) while the
  historical bars have open and close.  Mixing definitions inflates or deflates z-scores.
  During a live intraday session we do NOT evaluate price-spike until the session
  closes, because a partial-day return cannot be compared to full-day historical
  returns without a normalisation factor.  Volume-spike during live session is also
  skipped for the same reason (partial accumulation vs full-day average).

VOLUME DURING LIVE SESSION:
  Skipped. A partial-day cumulative volume cannot be fairly compared to a
  full-day average without an intraday volume profile (which Fyers does not
  provide in this plan tier).  We evaluate volume only on completed sessions
  (last N closed bars from history).

RATE LIMITS (Fyers Standard plan):
  5 requests/second. Strategy:
    • Batch quotes — single call for all symbols.
    • History calls — 0.25s delay between each (4/sec).

THRESHOLDS:
  All thresholds loaded from app.detection.threshold_registry.
  None carry a "SEBI CASE" label — all are UNVALIDATED_HEURISTIC.
  See threshold_registry.py for full provenance notes.
"""

import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pytz

from app.detection.threshold_registry import get as _t

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# How many recent completed sessions to evaluate per symbol.
# Increasing this means older pumps are caught; decreasing saves API quota.
LOOKBACK_SESSIONS: int = int(os.environ.get("LOOKBACK_SESSIONS", "5"))

# Minimum bars in baseline before we emit any detection.
MIN_HISTORY_DAYS: int = int(_t("min_history_days"))

IST = pytz.timezone("Asia/Kolkata")


# ---------------------------------------------------------------------------
# Severity helper
# ---------------------------------------------------------------------------

def _sev(score: float) -> str:
    if score >= 0.8:
        return "critical"
    if score >= 0.6:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Standard disclaimer appended to every explanation
# ---------------------------------------------------------------------------

_DISCLAIMER = (
    " — IMPORTANT: This is a statistical outlier flag, NOT a finding of manipulation. "
    "Unusual activity requires further investigation before any conclusion can be drawn. "
    "All thresholds are unvalidated heuristics; see /api/system/detector-status."
)


# ---------------------------------------------------------------------------
# Individual detectors (operate on completed sessions only)
# ---------------------------------------------------------------------------

def detect_volume_spike(
    sym: str,
    session_vol: float,
    baseline_avg_vol: float,
    session_close: float,
    session_return_pct: float,
    event_date: date,
) -> Optional[Dict]:
    """
    Volume spike: session volume vs average of baseline sessions.

    Both session_vol and baseline_avg_vol are FULL-DAY volumes from closed bars.
    We do NOT call this during a live intraday session (see module docstring).
    """
    if baseline_avg_vol <= 0 or session_vol <= 0:
        return None
    ratio = session_vol / baseline_avg_vol
    medium = _t("volume_spike_medium_x")
    high   = _t("volume_spike_high_x")
    crit   = _t("volume_spike_critical_x")
    if ratio < medium:
        return None

    if ratio >= crit:
        score = min(0.95, 0.75 + (ratio - crit) * 0.02)
    elif ratio >= high:
        score = 0.65 + (ratio - high) / (crit - high) * 0.10
    else:
        score = 0.40 + (ratio - medium) / (high - medium) * 0.25

    direction = "UP" if session_return_pct >= 0 else "DOWN"
    return {
        "pattern_type": "volume_spike",
        "severity": _sev(score),
        "score": round(score, 3),
        "event_date": event_date,
        "explanation": (
            f"[VOLUME_SPIKE | UNVALIDATED_HEURISTIC] {sym} on {event_date}: "
            f"Session volume was {ratio:.1f}× the baseline average "
            f"({int(session_vol):,} vs avg {int(baseline_avg_vol):,}). "
            f"Session return: {session_return_pct:+.2f}% ({direction}), "
            f"close: ₹{session_close:.2f}. "
            f"Threshold: >{medium}×=medium, >{crit}×=critical (unvalidated heuristic)."
            + _DISCLAIMER
        ),
        "evidence": {
            "session_volume": int(session_vol),
            "baseline_avg_volume": int(baseline_avg_vol),
            "volume_ratio": round(ratio, 2),
            "session_close": session_close,
            "session_return_pct": round(session_return_pct, 3),
            "threshold_basis": "UNVALIDATED_HEURISTIC",
            "event_date": str(event_date),
        },
    }


def detect_price_zscore(
    sym: str,
    session_return_pct: float,
    baseline_returns: List[float],
    session_close: float,
    event_date: date,
) -> Optional[Dict]:
    """
    Price z-score: session close-to-close return vs baseline distribution.

    session_return_pct  = (close_t - close_{t-1}) / close_{t-1} * 100
    baseline_returns    = same definition for all bars strictly before session t

    We use close-to-close for both sides so the distributions are comparable.
    We do NOT call this during live session (partial return is incomparable).
    """
    n = len(baseline_returns)
    if n < MIN_HISTORY_DAYS:
        return None
    mean = sum(baseline_returns) / n
    var  = sum((r - mean) ** 2 for r in baseline_returns) / (n - 1)
    std  = var ** 0.5
    if std < 0.001:
        return None
    z  = (session_return_pct - mean) / std
    az = abs(z)

    medium = _t("zscore_medium")
    high   = _t("zscore_high")
    crit   = _t("zscore_critical")
    if az < medium:
        return None

    if az >= crit:
        score = min(0.95, 0.75 + (az - crit) * 0.04)
    elif az >= high:
        score = 0.65 + (az - high) / (crit - high) * 0.10
    else:
        score = 0.40 + (az - medium) / (high - medium) * 0.25

    direction = "positive" if session_return_pct > 0 else "negative"
    return {
        "pattern_type": "price_anomaly",
        "severity": _sev(score),
        "score": round(score, 3),
        "event_date": event_date,
        "explanation": (
            f"[PRICE_ANOMALY | UNVALIDATED_HEURISTIC] {sym} on {event_date}: "
            f"Session return {session_return_pct:+.2f}% is {az:.1f}σ from the "
            f"baseline mean ({mean:+.2f}% ± {std:.2f}%, n={n} sessions). "
            f"z={z:+.2f} — {direction} outlier. "
            f"Close: ₹{session_close:.2f}. "
            f"Return definition: close-to-close (not prev_close→LTP). "
            f"Threshold: z>{medium}=medium, z>{crit}=critical (unvalidated heuristic)."
            + _DISCLAIMER
        ),
        "evidence": {
            "session_return_pct": round(session_return_pct, 3),
            "z_score": round(z, 2),
            "baseline_mean_pct": round(mean, 3),
            "baseline_std_pct": round(std, 3),
            "baseline_sessions": n,
            "session_close": session_close,
            "threshold_basis": "UNVALIDATED_HEURISTIC",
            "return_definition": "close-to-close",
            "event_date": str(event_date),
        },
    }


def detect_thin_volume_pump(
    sym: str,
    session_vol: float,
    baseline_avg_vol: float,
    session_return_pct: float,
    session_close: float,
    event_date: date,
) -> Optional[Dict]:
    """Large price move on thin volume — easier to engineer artificially."""
    if baseline_avg_vol <= 0 or session_vol <= 0:
        return None
    vol_ratio = session_vol / baseline_avg_vol
    price_thr = _t("thin_vol_price_pct")
    vol_thr   = _t("thin_vol_ratio")
    if abs(session_return_pct) < price_thr or vol_ratio > vol_thr:
        return None
    score = min(0.75, 0.40 + abs(session_return_pct) * 0.04)
    return {
        "pattern_type": "thin_volume_pump",
        "severity": _sev(score),
        "score": round(score, 3),
        "event_date": event_date,
        "explanation": (
            f"[THIN_VOLUME_PUMP | UNVALIDATED_HEURISTIC] {sym} on {event_date}: "
            f"Session return {session_return_pct:+.2f}% on only {vol_ratio:.1%} "
            f"of baseline avg volume ({int(session_vol):,} vs avg {int(baseline_avg_vol):,}). "
            f"Large moves on thin volume are easier to engineer artificially."
            + _DISCLAIMER
        ),
        "evidence": {
            "session_volume": int(session_vol),
            "baseline_avg_volume": int(baseline_avg_vol),
            "volume_ratio": round(vol_ratio, 3),
            "session_return_pct": round(session_return_pct, 3),
            "session_close": session_close,
            "threshold_basis": "UNVALIDATED_HEURISTIC",
            "event_date": str(event_date),
        },
    }


def detect_momentum_reversal(
    sym: str,
    bars: List[Dict],  # sorted oldest→newest, full closed sessions
    event_date: date,
) -> Optional[Dict]:
    """
    3-session run-up then reversal — classic pump-and-dump shape.

    bars[-5:] = [t-4, t-3, t-2, t-1, t]
      run-up : close[t-4] → close[t-2]  (3 sessions)
      reversal: close[t-2] → close[t-1] (1 session)

    event_date is the date of bar t (the reversal session being tested).
    """
    if len(bars) < 5:
        return None
    b = bars[-5:]
    c0, c2, c3 = b[0].get("close", 0), b[2].get("close", 0), b[3].get("close", 0)
    if c0 <= 0 or c2 <= 0 or c3 <= 0:
        return None
    runup    = (c2 - c0) / c0 * 100
    reversal = (c3 - c2) / c2 * 100
    runup_thr = _t("pump_runup_3d_pct")
    dump_thr  = _t("dump_reversal_pct")
    if runup < runup_thr or reversal > -dump_thr:
        return None
    score = min(0.85, 0.50 + runup * 0.02 + abs(reversal) * 0.01)
    return {
        "pattern_type": "momentum_reversal",
        "severity": _sev(score),
        "score": round(score, 3),
        "event_date": event_date,
        "explanation": (
            f"[MOMENTUM_REVERSAL | UNVALIDATED_HEURISTIC] {sym} on {event_date}: "
            f"3-session run-up {runup:+.1f}% (₹{c0:.2f}→₹{c2:.2f}) "
            f"followed by reversal {reversal:+.1f}% (₹{c2:.2f}→₹{c3:.2f}). "
            f"This shape resembles a pump-and-dump pattern. "
            f"Thresholds: run-up>{runup_thr}%, reversal<-{dump_thr}% (unvalidated heuristic)."
            + _DISCLAIMER
        ),
        "evidence": {
            "run_up_pct": round(runup, 2),
            "reversal_pct": round(reversal, 2),
            "start_close": c0,
            "peak_close": c2,
            "post_peak_close": c3,
            "threshold_basis": "UNVALIDATED_HEURISTIC",
            "event_date": str(event_date),
        },
    }


def detect_oi_concentration(
    sym: str,
    chain: List[Dict],
    event_date: date,
) -> Optional[Dict]:
    """
    OI concentration: top-3 strikes hold >65% of total OI.
    Only run for index underlyings (NIFTY50/BANKNIFTY/FINNIFTY).
    """
    if not chain:
        return None
    total_oi = sum(s.get("ce_oi", 0) + s.get("pe_oi", 0) for s in chain)
    if total_oi <= 0:
        return None
    top3 = sorted(chain, key=lambda s: s.get("ce_oi", 0) + s.get("pe_oi", 0), reverse=True)[:3]
    top3_oi = sum(s.get("ce_oi", 0) + s.get("pe_oi", 0) for s in top3)
    ratio = top3_oi / total_oi
    medium = _t("oi_conc_medium")
    high   = _t("oi_conc_high")
    if ratio < medium:
        return None
    score = (
        0.65 + (ratio - medium) / (1 - medium) * 0.25
        if ratio >= high
        else 0.45 + (ratio - medium) / (high - medium) * 0.20
    )
    strikes = [s.get("strike") for s in top3]
    return {
        "pattern_type": "oi_concentration",
        "severity": "high" if ratio >= high else "medium",
        "score": round(score, 3),
        "event_date": event_date,
        "explanation": (
            f"[OI_CONCENTRATION | UNVALIDATED_HEURISTIC] {sym} on {event_date}: "
            f"Top 3 strikes {strikes} hold {ratio:.1%} of total OI "
            f"({int(top3_oi):,}/{int(total_oi):,}). "
            f"Concentrated OI may indicate pinning or large directional positioning."
            + _DISCLAIMER
        ),
        "evidence": {
            "top3_strikes": strikes,
            "top3_oi": int(top3_oi),
            "total_oi": int(total_oi),
            "concentration_ratio": round(ratio, 3),
            "threshold_basis": "UNVALIDATED_HEURISTIC",
            "event_date": str(event_date),
        },
    }


def detect_sustained_volume(
    sym: str,
    bars: List[Dict],       # closed sessions, sorted oldest→newest
    event_date: date,       # date of the last bar in the window
) -> Optional[Dict]:
    """
    Multi-day sustained abnormal volume: 3+ consecutive sessions with vol >2× baseline avg.

    Baseline: all bars before the first session in the run.
    Event date: the date of the last session in the sustained-volume run.
    """
    min_sessions = int(_t("sustained_vol_min_sessions"))
    vol_ratio_thr = _t("sustained_vol_ratio")

    if len(bars) < min_sessions + MIN_HISTORY_DAYS:
        return None

    # Try each possible ending position for a run of `min_sessions` length
    # covering the LOOKBACK window (last LOOKBACK_SESSIONS bars).
    # We only report on the most recent qualifying run to avoid duplicate events.
    n = len(bars)
    for end_idx in range(n - 1, min_sessions + MIN_HISTORY_DAYS - 2, -1):
        # Candidate run: bars[end_idx - min_sessions + 1 .. end_idx]
        run_bars   = bars[end_idx - min_sessions + 1 : end_idx + 1]
        pre_bars   = bars[:end_idx - min_sessions + 1]
        if len(pre_bars) < MIN_HISTORY_DAYS:
            continue
        avg_vol = sum(b.get("volume", 0) or 0 for b in pre_bars) / len(pre_bars)
        if avg_vol <= 0:
            continue
        ratios = [(b.get("volume", 0) or 0) / avg_vol for b in run_bars]
        if all(r >= vol_ratio_thr for r in ratios):
            avg_ratio = sum(ratios) / len(ratios)
            score = min(0.80, 0.50 + (avg_ratio - vol_ratio_thr) * 0.10)
            run_dates = [b.get("date", "?") for b in run_bars]
            return {
                "pattern_type": "sustained_volume",
                "severity": _sev(score),
                "score": round(score, 3),
                "event_date": event_date,
                "explanation": (
                    f"[SUSTAINED_VOLUME | UNVALIDATED_HEURISTIC] {sym}: "
                    f"{min_sessions}+ consecutive sessions of abnormal volume "
                    f"(avg {avg_ratio:.1f}× baseline, threshold {vol_ratio_thr}×). "
                    f"Sessions: {run_dates}."
                    + _DISCLAIMER
                ),
                "evidence": {
                    "run_sessions": run_dates,
                    "volume_ratios": [round(r, 2) for r in ratios],
                    "avg_ratio": round(avg_ratio, 2),
                    "baseline_avg_volume": int(avg_vol),
                    "threshold_basis": "UNVALIDATED_HEURISTIC",
                    "event_date": str(event_date),
                },
            }
    return None


# ---------------------------------------------------------------------------
# Per-session evaluator (operates on ONE closed session)
# ---------------------------------------------------------------------------

def _evaluate_session(
    sym: str,
    session_bar: Dict,
    baseline_bars: List[Dict],
    ltp: float,
) -> List[Dict]:
    """
    Run all OHLCV-based detectors for a single closed session.

    session_bar   — the bar for session t (a closed trading day)
    baseline_bars — bars strictly before session t (used to build statistics)
    ltp           — latest traded price (used only for display, not for computation)

    Returns a list of detection dicts (may be empty).
    """
    detections = []

    s_close  = session_bar.get("close", 0) or 0
    s_vol    = session_bar.get("volume", 0) or 0
    s_date   = session_bar.get("date")  # may be string "YYYY-MM-DD" or date object

    # Parse event_date
    if isinstance(s_date, date):
        event_date = s_date
    elif isinstance(s_date, str):
        try:
            event_date = date.fromisoformat(s_date[:10])
        except ValueError:
            event_date = date.today()
    else:
        # Fallback: use datetime from timestamp field if present
        ts = session_bar.get("timestamp") or session_bar.get("ts")
        if ts:
            try:
                event_date = datetime.utcfromtimestamp(int(ts)).astimezone(IST).date()
            except Exception:
                event_date = date.today()
        else:
            event_date = date.today()

    if s_close <= 0:
        return detections

    # ── Baseline stats ─────────────────────────────────────────────────────
    # Volume: mean of completed sessions
    bvols = [b.get("volume", 0) or 0 for b in baseline_bars if b.get("volume", 0)]
    avg_vol = sum(bvols) / len(bvols) if bvols else 0.0

    # Returns: close-to-close for every consecutive pair in baseline
    # We need prev_close for session_bar itself; that's the close of the bar
    # immediately before session_bar in the full sequence.
    # The caller passes baseline_bars = all bars before session_bar, so
    # the prev_close for session_bar is baseline_bars[-1]["close"].
    baseline_returns: List[float] = []
    for i in range(1, len(baseline_bars)):
        c_prev = baseline_bars[i - 1].get("close", 0) or 0
        c_curr = baseline_bars[i].get("close", 0) or 0
        if c_prev > 0 and c_curr > 0:
            baseline_returns.append((c_curr - c_prev) / c_prev * 100)

    # Close-to-close return for the session being evaluated
    prev_close = (baseline_bars[-1].get("close", 0) or 0) if baseline_bars else 0
    if prev_close > 0 and s_close > 0:
        session_return_pct = (s_close - prev_close) / prev_close * 100
    else:
        session_return_pct = 0.0

    # ── Run detectors ──────────────────────────────────────────────────────
    d = detect_volume_spike(sym, s_vol, avg_vol, s_close, session_return_pct, event_date)
    if d:
        detections.append(d)

    d = detect_price_zscore(sym, session_return_pct, baseline_returns, s_close, event_date)
    if d:
        detections.append(d)

    d = detect_thin_volume_pump(sym, s_vol, avg_vol, session_return_pct, s_close, event_date)
    if d:
        detections.append(d)

    return detections


# ---------------------------------------------------------------------------
# Main per-symbol runner
# ---------------------------------------------------------------------------

def run_detection_for_symbol(
    symbol: str,
    fyers_symbol: str,
    adapter,
    instrument_type: str,
    cached_quote: Optional[Dict] = None,
    lookback_sessions: int = LOOKBACK_SESSIONS,
) -> Tuple[List[Dict], Optional[str]]:
    """
    Run all applicable detectors for one symbol across the last N sessions.

    Args:
        symbol            — DB symbol (e.g. "RELIANCE")
        fyers_symbol      — Fyers API format (e.g. "NSE:RELIANCE-EQ")
        adapter           — FyersMarketDataAdapter instance
        instrument_type   — "EQUITY" | "INDEX" | "FUTURE" | "OPTION"
        cached_quote      — pre-fetched quote dict (avoids extra API call when batching)
        lookback_sessions — how many recent sessions to evaluate

    Returns:
        (detections, skip_reason)
        detections  — list of detection dicts; empty if nothing found or skipped
        skip_reason — None on success; a string explaining why detection was skipped
                      (e.g. "insufficient history", "contract not resolved")
    """
    detections: List[Dict] = []

    try:
        # ── Quote (use cached if available) ───────────────────────────────
        if cached_quote:
            q = cached_quote
        else:
            quotes = adapter.get_quotes_normalized([fyers_symbol])
            if not quotes:
                return [], "quote fetch returned empty"
            q = quotes[0]

        ltp = q.get("ltp", 0) or 0
        if ltp <= 0:
            return [], "LTP is zero or negative"

        # ── 30-day daily OHLCV ────────────────────────────────────────────
        try:
            history = adapter.get_history(fyers_symbol, resolution="D", days=35)
        except Exception as e:
            logger.warning("History failed for %s: %s", symbol, e)
            return [], f"history fetch failed: {e}"

        bars = [b for b in (history or []) if b.get("close", 0) and b.get("volume", 0)]

        if len(bars) < MIN_HISTORY_DAYS + 1:
            return [], f"insufficient history ({len(bars)} bars, need {MIN_HISTORY_DAYS + 1})"

        # ── Determine which sessions to evaluate ──────────────────────────
        # We skip bar[-1] if it appears to be a today's partial candle.
        # Detection for the current intraday session is intentionally deferred
        # until the session closes (see module docstring).
        # Heuristic: if the last bar's date == today IST, treat it as partial.
        today_ist = datetime.now(IST).date()

        def _bar_date(bar: Dict) -> Optional[date]:
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
                    return datetime.utcfromtimestamp(int(ts)).astimezone(IST).date()
                except Exception:
                    pass
            return None

        # Drop today's partial candle if present
        completed_bars = [b for b in bars if _bar_date(b) != today_ist]
        if not completed_bars:
            return [], "all bars are today's partial candle"

        # Evaluate the last min(lookback_sessions, available) sessions
        n_eval = min(lookback_sessions, len(completed_bars) - MIN_HISTORY_DAYS)
        if n_eval <= 0:
            return [], f"not enough history for lookback (need {MIN_HISTORY_DAYS} baseline + 1)"

        # For each session t in the lookback window, baseline = bars before t
        for t_idx in range(len(completed_bars) - n_eval, len(completed_bars)):
            session_bar    = completed_bars[t_idx]
            baseline_bars  = completed_bars[:t_idx]  # strictly before t
            if len(baseline_bars) < MIN_HISTORY_DAYS:
                continue

            session_dets = _evaluate_session(symbol, session_bar, baseline_bars, ltp)
            detections.extend(session_dets)

        # ── Multi-session detectors (operate on the full completed window) ─
        full_bars_with_ohlc = [b for b in completed_bars if b.get("open") and b.get("close")]
        if full_bars_with_ohlc:
            event_date_last = _bar_date(full_bars_with_ohlc[-1]) or today_ist
            d = detect_momentum_reversal(symbol, full_bars_with_ohlc, event_date_last)
            if d:
                detections.append(d)

            d = detect_sustained_volume(symbol, completed_bars, event_date_last)
            if d:
                detections.append(d)

        # ── OI concentration — indices only ───────────────────────────────
        if symbol in ("NIFTY50", "BANKNIFTY", "FINNIFTY"):
            try:
                idx_sym = {
                    "NIFTY50":   "NSE:NIFTY50-INDEX",
                    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
                    "FINNIFTY":  "NSE:NIFTYFIN-INDEX",
                }.get(symbol, fyers_symbol)
                chain = adapter.get_option_chain(idx_sym, strike_count=20)
                if chain:
                    event_date_now = datetime.now(IST).date()
                    d = detect_oi_concentration(symbol, chain, event_date_now)
                    if d:
                        detections.append(d)
            except Exception as e:
                logger.debug("OI chain failed for %s: %s", symbol, e)

        # ── ML anomaly scoring — applied across ALL detections ────────────
        # Groups detections by event_date and scores each (symbol, date) pair
        # using the OHLCV Isolation Forest (or heuristic fallback if no model).
        # Adds ml_anomaly_score and score_method to each detection dict.
        if detections:
            try:
                from app.ml.ohlcv_scorer import build_feature_vector, get_scorer
                from collections import defaultdict

                # Group by event_date so multi-detector sessions score together
                by_date: dict = defaultdict(list)
                for det in detections:
                    by_date[det.get("event_date", date.today())].append(det)

                scorer = get_scorer()
                for ev_date, dets_for_date in by_date.items():
                    fv = build_feature_vector(symbol, ev_date, dets_for_date)
                    ml_score, method = scorer.score(fv)
                    for det in dets_for_date:
                        det["ml_anomaly_score"] = ml_score
                        det["score_method"] = method
            except Exception as ml_exc:
                logger.debug("ML scoring skipped for %s: %s", symbol, ml_exc)

    except Exception as e:
        logger.error("Detection failed for %s: %s", symbol, e, exc_info=True)
        return [], f"unexpected error: {e}"

    return detections, None


# ---------------------------------------------------------------------------
# Symbol format helper
# ---------------------------------------------------------------------------

def build_fyers_symbol(symbol: str, instrument_type: str) -> str:
    """
    Convert DB symbol to Fyers API format.

    For FUTURE contracts: we do NOT hardcode the expiry month string (e.g.
    "25NOVFUT") because that expires and breaks detection silently.
    Instead, we return None and log a visible skip reason.  The caller is
    responsible for resolving the nearest expiry via the Fyers symbol master
    before calling detection.  This function only handles EQUITY and INDEX.
    """
    it = instrument_type.upper()
    if it == "INDEX":
        return {
            "NIFTY50":    "NSE:NIFTY50-INDEX",
            "BANKNIFTY":  "NSE:NIFTYBANK-INDEX",
            "FINNIFTY":   "NSE:NIFTYFIN-INDEX",
            "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
        }.get(symbol, f"NSE:{symbol}-INDEX")
    elif it == "FUTURE":
        # Cannot hardcode expiry — return sentinel value.
        # Caller must resolve nearest expiry from symbol master.
        # Logged as "skipped: contract not resolved" in the scan loop.
        return ""
    else:
        return f"NSE:{symbol}-EQ"
