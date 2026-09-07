"""
app/api/system.py
=================
GET /api/system/data-sources       — real circuit-breaker state per ingest source
GET /api/system/detector-status    — real thresholds + validation status per detector
GET /api/system/engineering-health — real test/stress numbers (from persisted JSON)
"""
import json
import pathlib
from typing import Optional, Any

from fastapi import APIRouter

# ── Real circuit breaker singletons ──────────────────────────────────────────
# These are module-level objects in data.ingest.resilience — their .state,
# .consecutive_failures, and .last_success_ts reflect REAL in-process state.
from data.ingest.resilience import (
    bhavcopy_circuit,
    delivery_circuit,
    bulk_deals_circuit,
    option_chain_circuit,
)

# ── Real detector threshold constants ─────────────────────────────────────────
# Imported directly from each detector module so they CANNOT drift out of sync
# with the actual running detection logic.
from app.detection.spoofing import (
    MIN_CANCEL_RATIO,
    MIN_SIZE_MULTIPLE,
    MIN_PRICE_IMPACT_PCT,
)
from app.detection.circular_trading import MAX_CYCLE_LENGTH, MIN_TRADES_IN_WINDOW
from app.detection.coordinated_pump import MIN_COORDINATING_ACCOUNTS, VOLUME_SPIKE_MULTIPLE
from app.detection.oi_manipulation import (
    OI_CONCENTRATION_THRESHOLD,
    OI_VELOCITY_THRESHOLD,
    OI_IV_DECOUPLING_THRESHOLD,
)
from app.detection.basis_distortion import BASIS_DEVIATION_THRESHOLD
from app.detection.option_pinning import (
    PIN_DISTANCE_THRESHOLD,
    PIN_EXPIRY_DAYS_THRESHOLD,
    PIN_OI_DOMINANCE_THRESHOLD,
)

router = APIRouter(prefix="/api/system", tags=["system"])

# Path to persisted engineering health JSON (written by dashboard/scripts/run_and_save_health.py)
_DASHBOARD_DATA = pathlib.Path(__file__).parent.parent.parent / "dashboard" / "data"
_HEALTH_FILE = _DASHBOARD_DATA / "engineering_health.json"
_DETECTOR_STATUS_FILE = _DASHBOARD_DATA / "detector_status.json"


# ── /api/system/data-sources ──────────────────────────────────────────────────

@router.get("/data-sources")
def get_data_sources():
    """
    Real-time status of every ingest source.
    Circuit breaker state is read from the live module-level singleton objects.
    last_success_ts is set by each circuit breaker's on_success() call.

    No static placeholders — if a circuit breaker is OPEN, this says so.
    """
    def cb_info(cb, label: str, description: str) -> dict:
        last_ts = getattr(cb, "last_success_ts", None)
        return {
            "source": label,
            "description": description,
            "circuit_state": cb.state,                        # "CLOSED" | "OPEN" | "HALF_OPEN"
            "consecutive_failures": cb.consecutive_failures,
            "failure_threshold": cb.failure_threshold,
            "cooldown_seconds": cb.cooldown_seconds,
            "last_successful_fetch": last_ts,                 # float unix ts or None
            "degraded": cb.state != "CLOSED",
        }

    sources = [
        cb_info(bhavcopy_circuit,     "NSE Bhavcopy",          "Daily OHLCV + volume data from archives.nseindia.com"),
        cb_info(delivery_circuit,     "NSE Delivery Positions", "Supplementary .dat delivery position file (known-unreliable)"),
        cb_info(bulk_deals_circuit,   "NSE Bulk Deals",         "Bulk/block deal disclosures"),
        cb_info(option_chain_circuit, "NSE Option Chain",       "Live option chain OI + Greeks (market-hours only)"),
    ]

    # Broker API sources: not yet circuit-breaker-wrapped as module singletons.
    # Report their real integration status rather than faking a "green" status.
    sources.append({
        "source": "Breeze API (ICICI)",
        "description": "Historical options OI via broker API",
        "circuit_state": "NOT_INTEGRATED",
        "consecutive_failures": None,
        "failure_threshold": None,
        "cooldown_seconds": None,
        "last_successful_fetch": None,
        "degraded": True,
        "note": "Breeze API integration present in broker_order_stream.py but not yet wrapped "
                "in a circuit breaker singleton. Status cannot be reported in real time.",
    })
    sources.append({
        "source": "Kite Connect (Zerodha)",
        "description": "Live market depth + candles via broker API",
        "circuit_state": "NOT_INTEGRATED",
        "consecutive_failures": None,
        "failure_threshold": None,
        "cooldown_seconds": None,
        "last_successful_fetch": None,
        "degraded": True,
        "note": "Kite integration present in broker_order_stream.py but not yet wrapped "
                "in a circuit breaker singleton. Status cannot be reported in real time.",
    })

    any_degraded = any(s["degraded"] for s in sources if s["circuit_state"] != "NOT_INTEGRATED")
    return {
        "overall_health": "degraded" if any_degraded else "ok",
        "sources": sources,
        "note": "Circuit state is read from in-process module-level CircuitBreaker singletons "
                "(data.ingest.resilience). State is correct for single-process deployments; "
                "multi-process (gunicorn) would require shared state (Redis).",
    }


# ── /api/system/detector-status ───────────────────────────────────────────────

@router.get("/detector-status")
def get_detector_status():
    """
    Real threshold values + validation status for all 6 detectors.
    Thresholds are imported directly from each detector module —
    they cannot drift out of sync with actual running logic.
    Validation status is read from dashboard/data/detector_status.json
    (maintained after each backtest run).
    """
    # Read validation status override file if present
    extra: dict[str, Any] = {}
    if _DETECTOR_STATUS_FILE.exists():
        try:
            extra = json.loads(_DETECTOR_STATUS_FILE.read_text(encoding="utf-8"))
        except Exception:
            extra = {}

    def vstatus(det_id: str) -> dict:
        """Read per-detector validation status from file, or return known defaults."""
        if det_id in extra:
            return extra[det_id]
        # Default: reflect actual knowledge from backtest/REPORT.md
        # 0 of 6 detectors have been tested against confirmed manipulation.
        # All 5 large-cap NSE symbols returned 0.0% FP (negative control only).
        return {
            "validated_against_real_manipulation": False,
            "negative_control_tested": det_id in {
                "spoofing", "circular_trading", "coordinated_pump"
            },
            "negative_control_note": (
                "0.0% FP on 90 real NSE trading days (5 large-cap equities) "
                "if negative_control_tested=True, else not tested (F&O detectors require "
                "option chain OI data not available in public NSE archives)."
            ),
            "backtest_verdict": "UNTESTABLE — no confirmed manipulation case was testable "
                                "with publicly available Indian market data.",
        }

    detectors = [
        {
            "id": "spoofing",
            "file": "app/detection/spoofing.py",
            "description": "Cancel-heavy order layering with price impact",
            "thresholds": {
                "MIN_CANCEL_RATIO": MIN_CANCEL_RATIO,
                "MIN_SIZE_MULTIPLE": MIN_SIZE_MULTIPLE,
                "MIN_PRICE_IMPACT_PCT": MIN_PRICE_IMPACT_PCT,
            },
            "threshold_labels": "HEURISTIC — not validated against real manipulation distributions",
            **vstatus("spoofing"),
        },
        {
            "id": "circular_trading",
            "file": "app/detection/circular_trading.py",
            "description": "Account-ring cycle detection via directed graph",
            "thresholds": {
                "MAX_CYCLE_LENGTH": MAX_CYCLE_LENGTH,
                "MIN_TRADES_IN_WINDOW": MIN_TRADES_IN_WINDOW,
            },
            "threshold_labels": "HEURISTIC",
            **vstatus("circular_trading"),
        },
        {
            "id": "coordinated_pump",
            "file": "app/detection/coordinated_pump.py",
            "description": "Dormant-account coordinated buying + volume spike",
            "thresholds": {
                "MIN_COORDINATING_ACCOUNTS": MIN_COORDINATING_ACCOUNTS,
                "VOLUME_SPIKE_MULTIPLE": VOLUME_SPIKE_MULTIPLE,
            },
            "threshold_labels": "HEURISTIC",
            **vstatus("coordinated_pump"),
        },
        {
            "id": "oi_manipulation",
            "file": "app/detection/oi_manipulation.py",
            "description": "Open interest concentration + IV decoupling",
            "thresholds": {
                "OI_CONCENTRATION_THRESHOLD": OI_CONCENTRATION_THRESHOLD,
                "OI_VELOCITY_THRESHOLD": OI_VELOCITY_THRESHOLD,
                "OI_IV_DECOUPLING_THRESHOLD": OI_IV_DECOUPLING_THRESHOLD,
            },
            "threshold_labels": "UNVALIDATED GUESS — no real OI distribution to calibrate against",
            **vstatus("oi_manipulation"),
        },
        {
            "id": "basis_distortion",
            "file": "app/detection/basis_distortion.py",
            "description": "Futures/spot spread vs fair-value excess contango/backwardation",
            "thresholds": {
                "BASIS_DEVIATION_THRESHOLD": BASIS_DEVIATION_THRESHOLD,
            },
            "threshold_labels": "UNVALIDATED GUESS",
            **vstatus("basis_distortion"),
        },
        {
            "id": "option_pinning",
            "file": "app/detection/option_pinning.py",
            "description": "Strike OI dominance on near-expiry (max pain manipulation)",
            "thresholds": {
                "PIN_DISTANCE_THRESHOLD": PIN_DISTANCE_THRESHOLD,
                "PIN_EXPIRY_DAYS_THRESHOLD": PIN_EXPIRY_DAYS_THRESHOLD,
                "PIN_OI_DOMINANCE_THRESHOLD": PIN_OI_DOMINANCE_THRESHOLD,
            },
            "threshold_labels": "UNVALIDATED GUESS",
            **vstatus("option_pinning"),
        },
    ]

    validated_count = sum(1 for d in detectors if d.get("validated_against_real_manipulation"))
    neg_control_count = sum(1 for d in detectors if d.get("negative_control_tested"))

    return {
        "total_detectors": len(detectors),
        "validated_against_real_manipulation": validated_count,
        "negative_control_tested": neg_control_count,
        "honest_summary": (
            f"{validated_count} of 6 detectors validated against confirmed manipulation cases. "
            f"{neg_control_count} of 6 tested via negative control (0.0% FP on 90 real NSE days). "
            "All 6 detectors have HEURISTIC or UNVALIDATED thresholds — see threshold_labels per detector."
        ),
        "detectors": detectors,
    }


# ── /api/system/engineering-health ────────────────────────────────────────────

@router.get("/engineering-health")
def get_engineering_health():
    """
    Real test suite and stress test numbers.
    Reads dashboard/data/engineering_health.json, written by
    dashboard/scripts/run_and_save_health.py after a real test run.

    Returns {"status": "no_test_run_recorded"} if no run has been saved yet.
    NEVER returns fabricated numbers.
    """
    if not _HEALTH_FILE.exists():
        return {
            "status": "no_test_run_recorded",
            "message": (
                "No test run has been persisted yet. "
                "Run: python dashboard/scripts/run_and_save_health.py"
            ),
        }

    try:
        data = json.loads(_HEALTH_FILE.read_text(encoding="utf-8"))
        data["status"] = "recorded"
        return data
    except Exception as exc:
        return {
            "status": "error_reading_file",
            "error": str(exc),
            "file": str(_HEALTH_FILE),
        }
