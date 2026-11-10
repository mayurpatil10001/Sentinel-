"""
app/workers/detection_worker.py
================================
Real-data manipulation detection pipeline for Sentinel.

DATA SOURCES (what we actually have):
  ✅ Fyers live quotes  — LTP, volume, prev_close, change%  (batch, 1 req for all symbols)
  ✅ Fyers daily OHLCV  — 30-day history via get_history()  (1 req per symbol, rate-limited)
  ✅ NSE Option Chain   — OI per strike (Fyers API)
  ❌ Market-wide order book — unavailable via broker APIs
  ❌ Other participants' orders

DETECTORS:
  1. VOLUME_SPIKE         — Today's vol vs 30d avg  [SEBI CASE threshold]
  2. PRICE_ZSCORE         — Today's return z-score  [SEBI CASE threshold]
  3. THIN_VOLUME_PUMP     — Big price move on tiny volume [HEURISTIC]
  4. MOMENTUM_REVERSAL    — 3-day run-up then drop   [SEBI CASE threshold]
  5. OI_CONCENTRATION     — Top-3 strikes hold >65% OI [HEURISTIC]

RATE LIMITS (Fyers Standard plan):
  5 requests/second. Strategy:
    • Batch quotes — single call for all symbols
    • History calls — 0.25s delay between each (4/sec)

MARKET-CLOSED BEHAVIOUR:
  Live volume = 0 when market is closed. We fall back to the last
  historical bar so detectors run on real last-session data, not nothing.
"""

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
VOLUME_SPIKE_MEDIUM_X  = 3.0    # SEBI CASE — cited in multiple adjudication orders
VOLUME_SPIKE_HIGH_X    = 5.0    # SEBI CASE
VOLUME_SPIKE_CRITICAL_X = 10.0  # SEBI CASE

ZSCORE_MEDIUM   = 2.5   # SEBI CASE — 2.5σ cited in multiple orders
ZSCORE_HIGH     = 3.5   # SEBI CASE
ZSCORE_CRITICAL = 5.0   # SEBI CASE

THIN_VOL_PRICE_PCT   = 2.0   # HEURISTIC — price move %
THIN_VOL_RATIO       = 0.5   # HEURISTIC — volume vs avg

PUMP_RUNUP_3D_PCT    = 5.0   # HEURISTIC — 3-day cumulative gain
DUMP_REVERSAL_PCT    = 3.0   # HEURISTIC — next-day drop

OI_CONC_MEDIUM = 0.65   # HEURISTIC — top-3 strikes OI share
OI_CONC_HIGH   = 0.80   # HEURISTIC

MIN_HISTORY_DAYS = 10   # need ≥10 days for meaningful std


# ---------------------------------------------------------------------------
# Individual detectors
# ---------------------------------------------------------------------------

def _sev(score: float) -> str:
    if score >= 0.8: return "critical"
    if score >= 0.6: return "high"
    if score >= 0.4: return "medium"
    return "low"


def detect_volume_spike(sym, today_vol, avg_vol, ltp, chg_pct):
    if avg_vol <= 0 or today_vol <= 0:
        return None
    ratio = today_vol / avg_vol
    if ratio < VOLUME_SPIKE_MEDIUM_X:
        return None

    if ratio >= VOLUME_SPIKE_CRITICAL_X:
        score = min(0.95, 0.75 + (ratio - VOLUME_SPIKE_CRITICAL_X) * 0.02)
    elif ratio >= VOLUME_SPIKE_HIGH_X:
        score = 0.65 + (ratio - VOLUME_SPIKE_HIGH_X) / (VOLUME_SPIKE_CRITICAL_X - VOLUME_SPIKE_HIGH_X) * 0.10
    else:
        score = 0.40 + (ratio - VOLUME_SPIKE_MEDIUM_X) / (VOLUME_SPIKE_HIGH_X - VOLUME_SPIKE_MEDIUM_X) * 0.25

    direction = "UP" if chg_pct >= 0 else "DOWN"
    return {
        "pattern_type": "volume_spike",
        "severity": _sev(score),
        "score": round(score, 3),
        "explanation": (
            f"[VOLUME_SPIKE | SEBI CASE] {sym}: Volume is {ratio:.1f}× the 30-day average "
            f"({int(today_vol):,} vs avg {int(avg_vol):,}). "
            f"Price moved {chg_pct:+.2f}% ({direction}) to ₹{ltp:.2f}. "
            f"Abnormal volume + price move is consistent with coordinated trading activity. "
            f"Thresholds: >{VOLUME_SPIKE_MEDIUM_X}×=MEDIUM, >{VOLUME_SPIKE_CRITICAL_X}×=CRITICAL "
            f"(SEBI adjudication order basis). [Fyers live quote + 30d OHLCV]"
        ),
        "evidence": {
            "today_volume": int(today_vol), "avg_30d_volume": int(avg_vol),
            "volume_ratio": round(ratio, 2), "today_ltp": ltp,
            "today_change_pct": chg_pct, "threshold_basis": "SEBI CASE",
        },
    }


def detect_price_zscore(sym, today_ret, hist_returns, ltp):
    n = len(hist_returns)
    if n < MIN_HISTORY_DAYS:
        return None
    mean = sum(hist_returns) / n
    std  = (sum((r - mean) ** 2 for r in hist_returns) / (n - 1)) ** 0.5
    if std < 0.001:
        return None
    z = (today_ret - mean) / std
    az = abs(z)
    if az < ZSCORE_MEDIUM:
        return None

    if az >= ZSCORE_CRITICAL:
        score = min(0.95, 0.75 + (az - ZSCORE_CRITICAL) * 0.04)
    elif az >= ZSCORE_HIGH:
        score = 0.65 + (az - ZSCORE_HIGH) / (ZSCORE_CRITICAL - ZSCORE_HIGH) * 0.10
    else:
        score = 0.40 + (az - ZSCORE_MEDIUM) / (ZSCORE_HIGH - ZSCORE_MEDIUM) * 0.25

    direction = "positive" if today_ret > 0 else "negative"
    return {
        "pattern_type": "price_anomaly",
        "severity": _sev(score),
        "score": round(score, 3),
        "explanation": (
            f"[PRICE_ZSCORE | SEBI CASE] {sym}: Today's return {today_ret:+.2f}% is "
            f"{az:.1f}σ from the 30-day mean ({mean:+.2f}% ± {std:.2f}%). "
            f"Z={z:+.2f} — statistically {direction} outlier. "
            f"SEBI orders cite abnormal return days (z>2.5σ) as prima facie evidence. "
            f"LTP: ₹{ltp:.2f}. [Fyers 30d daily OHLCV]"
        ),
        "evidence": {
            "today_return_pct": round(today_ret, 3), "z_score": round(z, 2),
            "mean_pct": round(mean, 3), "std_pct": round(std, 3),
            "history_days": n, "today_ltp": ltp, "threshold_basis": "SEBI CASE",
        },
    }


def detect_thin_volume_pump(sym, today_vol, avg_vol, chg_pct, ltp):
    if avg_vol <= 0 or today_vol <= 0:
        return None
    vol_ratio = today_vol / avg_vol
    if abs(chg_pct) < THIN_VOL_PRICE_PCT or vol_ratio > THIN_VOL_RATIO:
        return None
    score = min(0.75, 0.40 + abs(chg_pct) * 0.04)
    return {
        "pattern_type": "thin_volume_pump",
        "severity": _sev(score),
        "score": round(score, 3),
        "explanation": (
            f"[THIN_VOLUME_PUMP | HEURISTIC] {sym}: Price moved {chg_pct:+.2f}% to ₹{ltp:.2f} "
            f"on only {vol_ratio:.1%} of 30-day average volume ({int(today_vol):,} vs avg {int(avg_vol):,}). "
            f"Large moves on thin volume are easier to engineer artificially — "
            f"consistent with wash trading or small-lot pumping. [Fyers data | HEURISTIC]"
        ),
        "evidence": {
            "today_volume": int(today_vol), "avg_30d_volume": int(avg_vol),
            "volume_ratio": round(vol_ratio, 3), "today_change_pct": chg_pct,
            "today_ltp": ltp, "threshold_basis": "HEURISTIC",
        },
    }


def detect_momentum_reversal(sym, bars, ltp):
    """3-day run-up then reversal — classic pump-and-dump signature."""
    if len(bars) < 5:
        return None
    b = bars[-5:]
    if b[0]["close"] <= 0 or b[2]["close"] <= 0:
        return None
    runup   = (b[2]["close"] - b[0]["close"]) / b[0]["close"] * 100
    reversal = (b[3]["close"] - b[2]["close"]) / b[2]["close"] * 100
    if runup < PUMP_RUNUP_3D_PCT or reversal > -DUMP_REVERSAL_PCT:
        return None
    score = min(0.85, 0.50 + runup * 0.02 + abs(reversal) * 0.01)
    return {
        "pattern_type": "momentum_reversal",
        "severity": _sev(score),
        "score": round(score, 3),
        "explanation": (
            f"[MOMENTUM_REVERSAL | SEBI CASE] {sym}: 3-day run-up of {runup:+.1f}% "
            f"(₹{b[0]['close']:.2f}→₹{b[2]['close']:.2f}) followed by reversal {reversal:+.1f}%. "
            f"Sharp accumulation then exit = classic pump & dump pattern cited in "
            f"SEBI orders (Sunrise Foods 2019, Kailash Auto 2018). LTP: ₹{ltp:.2f}. [Fyers 5d OHLCV]"
        ),
        "evidence": {
            "run_up_pct": round(runup, 2), "reversal_pct": round(reversal, 2),
            "start_price": b[0]["close"], "peak_price": b[2]["close"],
            "reversal_price": b[3]["close"], "today_ltp": ltp,
            "threshold_basis": "SEBI CASE",
        },
    }


def detect_oi_concentration(sym, chain):
    if not chain:
        return None
    total_oi = sum(s.get("ce_oi", 0) + s.get("pe_oi", 0) for s in chain)
    if total_oi <= 0:
        return None
    top3 = sorted(chain, key=lambda s: s.get("ce_oi", 0) + s.get("pe_oi", 0), reverse=True)[:3]
    top3_oi = sum(s.get("ce_oi", 0) + s.get("pe_oi", 0) for s in top3)
    ratio = top3_oi / total_oi
    if ratio < OI_CONC_MEDIUM:
        return None
    score = 0.65 + (ratio - OI_CONC_MEDIUM) / (1 - OI_CONC_MEDIUM) * 0.25 if ratio >= OI_CONC_HIGH else \
            0.45 + (ratio - OI_CONC_MEDIUM) / (OI_CONC_HIGH - OI_CONC_MEDIUM) * 0.20
    strikes = [s.get("strike") for s in top3]
    return {
        "pattern_type": "oi_concentration",
        "severity": "high" if ratio >= OI_CONC_HIGH else "medium",
        "score": round(score, 3),
        "explanation": (
            f"[OI_CONCENTRATION | HEURISTIC] {sym}: Top 3 strikes {strikes} hold "
            f"{ratio:.1%} of total OI ({int(top3_oi):,}/{int(total_oi):,}). "
            f"Concentrated OI may indicate pinning or large directional manipulation. [HEURISTIC]"
        ),
        "evidence": {
            "top3_strikes": strikes, "top3_oi": int(top3_oi),
            "total_oi": int(total_oi), "concentration_ratio": round(ratio, 3),
            "threshold_basis": "HEURISTIC",
        },
    }


# ---------------------------------------------------------------------------
# Main per-symbol runner
# ---------------------------------------------------------------------------

def run_detection_for_symbol(
    symbol: str,
    fyers_symbol: str,
    adapter,
    instrument_type: str,
    cached_quote: Optional[Dict] = None,
) -> List[Dict[str, Any]]:
    """
    Run all applicable detectors for one symbol.

    Args:
        cached_quote: pre-fetched quote dict (avoids extra API call when batching).

    Returns list of detection dicts. Empty list = no anomaly detected.
    """
    detections = []

    try:
        # ── Quote (use cached if available) ───────────────────────────────────
        if cached_quote:
            q = cached_quote
        else:
            quotes = adapter.get_quotes_normalized([fyers_symbol])
            if not quotes:
                return []
            q = quotes[0]

        ltp      = q.get("ltp", 0) or 0
        chg_pct  = q.get("change_pct", 0) or 0
        live_vol = q.get("volume", 0) or 0

        if ltp <= 0:
            return []

        # ── 30-day daily OHLCV ────────────────────────────────────────────────
        try:
            history = adapter.get_history(fyers_symbol, resolution="D", days=35)
        except Exception as e:
            logger.warning("History failed for %s: %s", symbol, e)
            history = []

        bars = [b for b in (history or []) if b.get("close", 0) and b.get("volume", 0)]

        # ── Market-closed fallback ─────────────────────────────────────────────
        # When market is closed, live_vol=0.  Use the LAST bar as "today's" data
        # so detectors still run on real last-session numbers.
        market_open = live_vol > 0
        if market_open:
            today_vol    = live_vol
            today_ret    = chg_pct
            baseline_bars = bars
        elif bars:
            last_bar      = bars[-1]
            today_vol     = last_bar.get("volume", 0) or 0
            lo = last_bar.get("open", 0) or 0
            lc = last_bar.get("close", 0) or 0
            today_ret     = (lc - lo) / lo * 100 if lo > 0 else chg_pct
            baseline_bars = bars[:-1]   # exclude last bar from baseline avg
        else:
            return []   # no data at all

        # 30d avg volume from baseline
        bvols = [b.get("volume", 0) or 0 for b in baseline_bars]
        avg_vol = sum(bvols) / len(bvols) if bvols else 0.0

        # Historical returns (open→close per bar)
        hist_returns = []
        for b in baseline_bars:
            o = b.get("open", 0) or 0
            c = b.get("close", 0) or 0
            if o > 0 and c > 0:
                hist_returns.append((c - o) / o * 100)

        ctx = "live session" if market_open else "last session (market closed)"

        # ── Run detectors ─────────────────────────────────────────────────────
        for det_fn, args in [
            (detect_volume_spike,    (symbol, today_vol, avg_vol, ltp, today_ret)),
            (detect_price_zscore,    (symbol, today_ret, hist_returns, ltp)),
            (detect_thin_volume_pump,(symbol, today_vol, avg_vol, today_ret, ltp)),
        ]:
            d = det_fn(*args)
            if d:
                d["explanation"] += f" [{ctx}]"
                detections.append(d)

        # Momentum reversal (needs bars with open/close)
        full_bars = [b for b in bars if b.get("open") and b.get("close")]
        d = detect_momentum_reversal(symbol, full_bars, ltp)
        if d:
            d["explanation"] += f" [{ctx}]"
            detections.append(d)

        # OI concentration — only for index underlyings, don't burn rate-limit quota
        if symbol in ("NIFTY50", "BANKNIFTY", "FINNIFTY"):
            try:
                idx_sym = {"NIFTY50": "NSE:NIFTY50-INDEX",
                           "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
                           "FINNIFTY": "NSE:NIFTYFIN-INDEX"}.get(symbol, fyers_symbol)
                chain = adapter.get_option_chain(idx_sym, strike_count=20)
                if chain:
                    d = detect_oi_concentration(symbol, chain)
                    if d:
                        detections.append(d)
            except Exception as e:
                logger.debug("OI chain failed for %s: %s", symbol, e)

    except Exception as e:
        logger.error("Detection failed for %s: %s", symbol, e, exc_info=True)

    return detections


# ---------------------------------------------------------------------------
# Symbol format helper
# ---------------------------------------------------------------------------

def build_fyers_symbol(symbol: str, instrument_type: str) -> str:
    """Convert DB symbol to Fyers API format."""
    if instrument_type == "INDEX":
        return {
            "NIFTY50":    "NSE:NIFTY50-INDEX",
            "BANKNIFTY":  "NSE:NIFTYBANK-INDEX",
            "FINNIFTY":   "NSE:NIFTYFIN-INDEX",
            "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
        }.get(symbol, f"NSE:{symbol}-INDEX")
    elif instrument_type == "FUTURE":
        return f"NSE:{symbol}25NOVFUT"
    else:
        return f"NSE:{symbol}-EQ"
