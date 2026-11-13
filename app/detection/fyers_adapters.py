"""
app/detection/fyers_adapters.py
================================
Adapters that map Fyers API v3 responses to the input types expected by the
existing detector functions in app/detection/.

DESIGN RULE: This module DOES NOT change detector logic. It only translates
data formats. The detectors remain the authoritative source of thresholds
and scoring; this module just feeds them real data.

DETECTORS CONNECTED HERE:
  ✅ oi_manipulation.detect_oi_concentration — via Fyers get_option_chain()
  ✅ oi_manipulation.detect_oi_iv_decoupling — via two consecutive chain snapshots
  ✅ option_pinning.detect_option_pinning    — via Fyers chain + spot + days-to-expiry
  ✅ basis_distortion.detect_basis_distortion — via Fyers futures + spot quotes

DETECTORS NOT CONNECTED (data unavailable via broker API):
  ❌ spoofing.detect_spoofing       — requires order-lifecycle data (placed→cancelled)
                                     for ALL participants, not just our account.
  ❌ circular_trading.detect_circular_trading — requires account-level trade graph
                                     across all participants.
  ❌ coordinated_pump.detect_coordinated_pump — requires account-level trade data.

These three are marked "Requires order/account-level data — not available via
broker API" in /api/system/detector-status and must NOT be counted as active.

DATA LIMITATIONS (always noted in results):
  - Fyers provides only CURRENT OI at the moment of the call; no historical OI
    endpoint. OI-IV decoupling requires TWO consecutive snapshots — only possible
    if option_chain_snapshots rows exist from a prior scan.
  - Futures basis: requires the near-month futures Fyers symbol. We resolve this
    from the option chain's first expiry entry (not a hardcoded expiry string).
  - IV: Fyers option chain may or may not return IV depending on the plan tier.
    We handle the case where IV is absent by skipping OI-IV decoupling.
"""

import logging
import time
import uuid
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import pytz

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# Underlyings for which we run option-chain detectors on every scan
OPTION_CHAIN_UNDERLYINGS = {
    "NIFTY50":   "NSE:NIFTY50-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "FINNIFTY":  "NSE:FINNIFTY-INDEX",
}

# How many strikes on each side of ATM to fetch (affects API quota)
CHAIN_STRIKE_COUNT = 20


# ---------------------------------------------------------------------------
# Option chain parsing helpers
# ---------------------------------------------------------------------------

def _parse_fyers_chain(raw: Dict, underlying_name: str) -> Optional[pd.DataFrame]:
    """
    Convert Fyers get_option_chain() raw response to a DataFrame with columns:
        strike, expiry (date), option_type (CE/PE), oi, iv, ltp, underlying_value

    Returns None if the response is empty or malformed.
    """
    if not raw:
        return None

    # Fyers v3 option chain response structure:
    # raw["expiryData"] = list of expiry strings
    # raw["optionChainData"] = list of {
    #     "strike_price": float,
    #     "expiry": "YYYY-MM-DD",  or "DD-MMM-YYYY"
    #     "call_options": {"greeks": {"oi": int, "iv": float}, "ltp": float},
    #     "put_options":  {"greeks": {"oi": int, "iv": float}, "ltp": float},
    #     "underlying_value": float
    # }
    # Layout may vary by API version. We handle both the flat Fyers v3 optionsChain
    # and the nested optionChainData.
    rows = []
    option_data = raw.get("optionChainData") or raw.get("d", {}).get("optionChainData")
    flat_chain = raw.get("optionsChain") or raw.get("data", {}).get("optionsChain")

    if flat_chain:
        exp_data = raw.get("expiryData") or raw.get("data", {}).get("expiryData", [])
        default_exp = None
        if exp_data and isinstance(exp_data, list):
            try:
                d_str = exp_data[0].get("date", "")
                default_exp = datetime.strptime(d_str, "%d-%m-%Y").date()
            except Exception:
                default_exp = None

        underlying_val = 0.0
        for item in flat_chain:
            if item.get("strike_price") == -1:
                underlying_val = float(item.get("ltp", 0) or 0)
                break

        for item in flat_chain:
            sp = float(item.get("strike_price", 0) or 0)
            if sp <= 0:
                continue
            opt_type = item.get("option_type", "").upper()
            if opt_type not in ("CE", "PE"):
                continue

            oi = item.get("oi")
            ltp = item.get("ltp")
            iv = item.get("iv") or item.get("greeks", {}).get("iv")

            rows.append({
                "strike": sp,
                "expiry": default_exp or date.today(),
                "option_type": opt_type,
                "oi": float(oi) if oi is not None else None,
                "iv": float(iv) if iv is not None else None,
                "ltp": float(ltp) if ltp is not None else None,
                "underlying_value": underlying_val,
            })

    elif option_data:
        for item in option_data:
            strike = float(item.get("strike_price", 0) or 0)
            if strike <= 0:
                continue

            # Parse expiry
            expiry_raw = item.get("expiry", "")
            try:
                if len(expiry_raw) == 10 and expiry_raw[4] == "-":
                    expiry = date.fromisoformat(expiry_raw)
                else:
                    expiry = datetime.strptime(expiry_raw, "%d-%b-%Y").date()
            except (ValueError, TypeError):
                continue

            underlying_val = float(item.get("underlying_value", 0) or 0)

            for opt_type, key in [("CE", "call_options"), ("PE", "put_options")]:
                opt = item.get(key, {}) or {}
                greeks = opt.get("greeks", {}) or {}
                oi  = greeks.get("oi") or opt.get("oi")
                iv  = greeks.get("iv") or opt.get("iv")
                ltp = opt.get("ltp") or opt.get("last_price")

                rows.append({
                    "strike": strike,
                    "expiry": expiry,
                    "option_type": opt_type,
                    "oi": float(oi) if oi is not None else None,
                    "iv": float(iv) if iv is not None else None,
                    "ltp": float(ltp) if ltp is not None else None,
                    "underlying_value": underlying_val,
                })

    if not rows:
        logger.warning("No rows parsed from Fyers option chain for %s", underlying_name)
        return None

    df = pd.DataFrame(rows)
    df = df.dropna(subset=["strike", "expiry", "option_type"])
    return df


def _nearest_expiry(df: pd.DataFrame) -> Optional[date]:
    """Return the nearest (soonest) expiry in the chain DataFrame."""
    if df is None or df.empty:
        return None
    today = date.today()
    upcoming = df[df["expiry"] >= today]["expiry"].unique()
    if len(upcoming) == 0:
        return None
    return min(upcoming)


def _days_to_expiry(expiry: date) -> int:
    return (expiry - date.today()).days


def _fyers_futures_symbol(symbol: str, expiry: date) -> str:
    """
    Build the Fyers futures symbol string from the nearest expiry date.
    Format: NSE:{SYMBOL}YYMMMFUT  e.g. NSE:NIFTY26JANFUT

    This is derived from the actual expiry we resolved from the chain,
    NOT a hardcoded month string. When expiry is None, returns empty string
    (caller must skip).
    """
    if expiry is None:
        return ""
    mon = expiry.strftime("%b").upper()   # JAN, FEB … DEC
    yy  = expiry.strftime("%y")            # 25, 26 …
    return f"NSE:{symbol}{yy}{mon}FUT"


# ---------------------------------------------------------------------------
# Store option chain snapshot to DB
# ---------------------------------------------------------------------------

def _store_chain_snapshot(
    db,
    scan_run_id: Optional[str],
    underlying: str,
    df: pd.DataFrame,
    snapshot_at: datetime,
) -> int:
    """
    Persist all rows in `df` to option_chain_snapshots.
    Returns number of rows written.
    """
    from app.db.models import OptionChainSnapshot

    rows_written = 0
    for _, row in df.iterrows():
        snap = OptionChainSnapshot(
            id=str(uuid.uuid4()),
            scan_run_id=scan_run_id,
            snapshot_at=snapshot_at,
            underlying=underlying,
            expiry=row["expiry"],
            strike=float(row["strike"]),
            option_type=str(row["option_type"]),
            oi=int(row["oi"]) if pd.notna(row.get("oi")) else None,
            volume=None,
            ltp=float(row["ltp"]) if pd.notna(row.get("ltp")) else None,
            iv=float(row["iv"]) if pd.notna(row.get("iv")) else None,
        )
        db.add(snap)
        rows_written += 1
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error("Failed to store chain snapshot for %s: %s", underlying, e)
        rows_written = 0
    return rows_written


def _load_prev_chain_snapshot(
    db,
    underlying: str,
    expiry: date,
) -> Optional[pd.DataFrame]:
    """
    Load the most recent *prior* chain snapshot from the DB for OI-IV decoupling.
    Returns None if no prior snapshot exists.
    """
    from app.db.models import OptionChainSnapshot
    from sqlalchemy import func

    latest_ts = (
        db.query(func.max(OptionChainSnapshot.snapshot_at))
        .filter(
            OptionChainSnapshot.underlying == underlying,
            OptionChainSnapshot.expiry == expiry,
        )
        .scalar()
    )
    if latest_ts is None:
        return None

    rows = (
        db.query(OptionChainSnapshot)
        .filter(
            OptionChainSnapshot.underlying == underlying,
            OptionChainSnapshot.expiry == expiry,
            OptionChainSnapshot.snapshot_at == latest_ts,
        )
        .all()
    )
    if not rows:
        return None

    return pd.DataFrame([{
        "strike": r.strike,
        "expiry": r.expiry,
        "option_type": r.option_type,
        "oi": r.oi,
        "iv": r.iv,
        "ltp": r.ltp,
        "underlying_value": None,
    } for r in rows])


# ---------------------------------------------------------------------------
# Main Phase 3 scan runner — called once per scheduler tick
# ---------------------------------------------------------------------------

def run_derivative_detectors(
    adapter,
    db,
    scan_run_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Run all three broker-API-compatible derivative detectors for every configured
    underlying (NIFTY50, BANKNIFTY, FINNIFTY).

    Returns a flat list of detection dicts (same format as OHLCV detections),
    each with keys: pattern_type, severity, score, event_date, explanation, evidence,
    underlying, source="live_fyers".

    Never raises — all errors are caught, logged, and returned as skip entries.
    """
    from app.detection.oi_manipulation import detect_oi_concentration, detect_oi_iv_decoupling
    from app.detection.option_pinning import detect_option_pinning
    from app.detection.basis_distortion import detect_basis_distortion

    all_detections: List[Dict] = []
    now_utc = datetime.utcnow()
    now_ist = datetime.now(IST)
    today   = now_ist.date()

    for sym, fyers_sym in OPTION_CHAIN_UNDERLYINGS.items():
        time.sleep(0.3)
        try:
            logger.info("[phase3] Fetching option chain for %s (%s)", sym, fyers_sym)
            raw_response = adapter.get_option_chain(fyers_sym, strike_count=CHAIN_STRIKE_COUNT)
            raw          = raw_response.get("raw", {})
            df           = _parse_fyers_chain(raw, sym)

            if df is None or df.empty:
                logger.warning("[phase3] Empty chain for %s — skipping", sym)
                continue

            # Store snapshot for OI-IV decoupling in future scans
            snap_rows = _store_chain_snapshot(db, scan_run_id, sym, df, now_utc)
            logger.info("[phase3] Stored %d chain rows for %s", snap_rows, sym)

            spot_price = float(df["underlying_value"].dropna().iloc[0]) if "underlying_value" in df.columns and df["underlying_value"].notna().any() else 0.0
            nearest_exp = _nearest_expiry(df)
            dte = _days_to_expiry(nearest_exp) if nearest_exp else 999

            # ── 1. OI concentration ─────────────────────────────────────
            try:
                signals = detect_oi_concentration(
                    chain_df=df,
                    symbol=sym,
                    exchange="NSE",
                    snapshot_time=now_utc,
                )
                for sig in signals:
                    all_detections.append(_oi_concentration_to_dict(sig, today))
            except ValueError as e:
                logger.warning("[phase3] OI concentration skipped for %s: %s", sym, e)
            except Exception as e:
                logger.error("[phase3] OI concentration error for %s: %s", sym, e)

            # ── 2. OI-IV decoupling (requires previous snapshot) ─────────
            prev_df = _load_prev_chain_snapshot(db, sym, nearest_exp) if nearest_exp else None
            if prev_df is not None and not prev_df.empty:
                # Only run if IV data is available in both snapshots
                has_iv = df["iv"].notna().any() and prev_df["iv"].notna().any()
                if has_iv:
                    try:
                        decoupling_sigs = detect_oi_iv_decoupling(
                            current_chain=df[df["expiry"] == nearest_exp].copy(),
                            prev_chain=prev_df[prev_df["expiry"] == nearest_exp].copy(),
                            symbol=sym,
                            exchange="NSE",
                            snapshot_time=now_utc,
                        )
                        for sig in decoupling_sigs:
                            all_detections.append(_oi_iv_decoupling_to_dict(sig, today))
                    except ValueError as e:
                        logger.warning("[phase3] OI-IV decoupling skipped for %s: %s", sym, e)
                    except Exception as e:
                        logger.error("[phase3] OI-IV decoupling error for %s: %s", sym, e)
                else:
                    logger.info("[phase3] IV data absent for %s — skipping OI-IV decoupling", sym)
            else:
                logger.info("[phase3] No prior chain snapshot for %s — OI-IV decoupling needs 2 snapshots", sym)

            # ── 3. Option pinning — only within PIN_EXPIRY_DAYS_THRESHOLD ─
            if nearest_exp and dte <= 2:
                if spot_price > 0:
                    chain_for_expiry = df[df["expiry"] == nearest_exp].copy()
                    try:
                        pin_signal = detect_option_pinning(
                            chain_df=chain_for_expiry,
                            symbol=sym,
                            exchange="NSE",
                            spot_price=spot_price,
                            expiry_date=nearest_exp,
                            snapshot_time=now_utc,
                        )
                        if pin_signal:
                            all_detections.append(_pin_signal_to_dict(pin_signal, today))
                    except ValueError as e:
                        logger.warning("[phase3] Option pinning skipped for %s: %s", sym, e)
                    except Exception as e:
                        logger.error("[phase3] Option pinning error for %s: %s", sym, e)
                else:
                    logger.warning("[phase3] No spot price for %s — cannot run pinning", sym)
            else:
                logger.info(
                    "[phase3] Option pinning skipped for %s: %d days to expiry (threshold ≤2)",
                    sym, dte
                )

            # ── 4. Basis distortion ──────────────────────────────────────
            if nearest_exp:
                fut_sym = _fyers_futures_symbol(sym, nearest_exp)
                if fut_sym:
                    try:
                        fut_quotes = adapter.get_quotes_normalized([fut_sym])
                        if fut_quotes and spot_price > 0:
                            fut_ltp = fut_quotes[0].get("ltp", 0) or 0
                            if fut_ltp > 0:
                                from app.detection.basis_distortion import detect_basis_distortion
                                bd_signal = detect_basis_distortion(
                                    symbol=sym,
                                    exchange="NSE",
                                    spot_price=spot_price,
                                    futures_price=fut_ltp,
                                    expiry_date=nearest_exp,
                                    snapshot_time=now_utc,
                                )
                                if bd_signal:
                                    all_detections.append(_basis_distortion_to_dict(bd_signal, today))
                            else:
                                logger.info("[phase3] Futures LTP=0 for %s — basis check skipped", sym)
                        else:
                            logger.info("[phase3] No futures quote for %s (%s)", sym, fut_sym)
                    except Exception as e:
                        logger.error("[phase3] Basis distortion error for %s: %s", sym, e)
                else:
                    logger.warning("[phase3] Could not resolve futures symbol for %s (expiry=%s)", sym, nearest_exp)

        except Exception as e:
            logger.error("[phase3] Unhandled error for %s: %s", sym, e, exc_info=True)

    # ── Anomaly scoring — applied to ALL derivative detections ───────────────
    # Uses OHLCVScorer (Isolation Forest). score_method='heuristic' until the
    # IF is retrained on raw OHLCV features (E10 — current IF inputs are
    # detector outputs, so it cannot flag sessions the rules missed).
    if all_detections:
        try:
            from app.ml.ohlcv_scorer import build_feature_vector, get_scorer
            from collections import defaultdict
            by_sym: dict = defaultdict(list)
            for det in all_detections:
                key = det.get("underlying", det.get("symbol", "UNKNOWN"))
                by_sym[key].append(det)
            scorer = get_scorer()
            for sym_key, dets_for_sym in by_sym.items():
                from datetime import date as _date
                ev_date = dets_for_sym[0].get("event_date", _date.today())
                fv = build_feature_vector(sym_key, ev_date, dets_for_sym)
                ml_score, method = scorer.score(fv)
                for det in dets_for_sym:
                    det["ml_anomaly_score"] = ml_score
                    det["score_method"] = method
                    logger.debug("[phase3-ml] %s anomaly_score=%.4f method=%s", sym_key, ml_score, method)
        except Exception as ml_exc:
            logger.debug("ML scoring skipped for derivative detections: %s", ml_exc)

    return all_detections


# ---------------------------------------------------------------------------
# Converter helpers — dataclass signal → detection dict
# ---------------------------------------------------------------------------

_DISCLAIMER = (
    " — IMPORTANT: This is a statistical outlier flag, NOT a finding of manipulation. "
    "Unusual activity requires further investigation before any conclusion can be drawn. "
    "All thresholds are unvalidated heuristics; see /api/system/detector-status."
)


def _oi_concentration_to_dict(sig, event_date: date) -> Dict:
    return {
        "pattern_type": "oi_concentration_advanced",
        "severity": sig.severity,
        "score": round(sig.score, 3),
        "event_date": event_date,
        "underlying": sig.symbol,
        "source": "live_fyers",
        "explanation": sig.explanation + _DISCLAIMER,
        "evidence": {
            "symbol": sig.symbol,
            "expiry": sig.expiry,
            "option_type": sig.option_type,
            "strike": sig.strike,
            "strike_oi": sig.strike_oi,
            "total_chain_oi": sig.total_chain_oi,
            "concentration_ratio": round(sig.concentration_ratio, 3),
            "underlying_value": sig.spot_price if hasattr(sig, "spot_price") else sig.underlying_value,
            "moneyness_pct": round(sig.moneyness_pct, 2),
            "threshold_basis": "UNVALIDATED_HEURISTIC",
        },
    }


def _oi_iv_decoupling_to_dict(sig, event_date: date) -> Dict:
    return {
        "pattern_type": "oi_iv_decoupling",
        "severity": sig.severity,
        "score": round(sig.score, 3),
        "event_date": event_date,
        "underlying": sig.symbol,
        "source": "live_fyers",
        "explanation": sig.explanation + _DISCLAIMER,
        "evidence": {
            "symbol": sig.symbol,
            "expiry": sig.expiry,
            "option_type": sig.option_type,
            "strike": sig.strike,
            "oi_change_pct": round(sig.oi_change_pct, 3),
            "iv_change_pct": round(sig.iv_change_pct, 3),
            "data_note": (
                "OI change computed from two consecutive Fyers chain snapshots. "
                "Fyers provides only current OI — no historical OI endpoint. "
                "This is an approximation over the scan interval, not tick-level OI change."
            ),
            "threshold_basis": "UNVALIDATED_HEURISTIC",
        },
    }


def _pin_signal_to_dict(sig, event_date: date) -> Dict:
    return {
        "pattern_type": "option_pinning",
        "severity": sig.severity,
        "score": round(sig.score, 3),
        "event_date": event_date,
        "underlying": sig.symbol,
        "source": "live_fyers",
        "explanation": sig.explanation + _DISCLAIMER,
        "evidence": {
            "symbol": sig.symbol,
            "expiry_date": str(sig.expiry_date),
            "spot_price": sig.spot_price,
            "pin_strike": sig.pin_strike,
            "distance_pct": round(sig.distance_pct, 4),
            "pin_strike_total_oi": sig.pin_strike_total_oi,
            "oi_dominance_ratio": round(sig.oi_dominance_ratio, 2),
            "days_to_expiry": sig.days_to_expiry,
            "max_pain_strike": sig.max_pain_strike,
            "threshold_basis": "UNVALIDATED_HEURISTIC",
        },
    }


def _basis_distortion_to_dict(sig, event_date: date) -> Dict:
    return {
        "pattern_type": "basis_distortion",
        "severity": sig.severity,
        "score": round(sig.score, 3),
        "event_date": event_date,
        "underlying": sig.symbol,
        "source": "live_fyers",
        "explanation": sig.explanation + _DISCLAIMER,
        "evidence": {
            "symbol": sig.symbol,
            "expiry_date": str(sig.expiry_date),
            "spot_price": sig.spot_price,
            "futures_price": sig.futures_price,
            "actual_basis": round(sig.actual_basis, 4),
            "fair_value_basis": round(sig.fair_value_basis, 4),
            "basis_deviation": round(sig.basis_deviation, 4),
            "deviation_pct": round(sig.deviation_pct, 5),
            "direction": sig.direction,
            "days_to_expiry": sig.days_to_expiry,
            "risk_free_rate_used": sig.risk_free_rate_used,
            "threshold_basis": "UNVALIDATED_HEURISTIC",
        },
    }

