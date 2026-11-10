"""
app/main.py
===========
FastAPI application entry point.

Routers:
  Legacy (no prefix, backward compat):
    /health, /detect/spoofing, /alerts, /alerts/{id}/evidence-log

  New API routers (all under /api/):
    /api/instruments
    /api/alerts         (extended: filters, PATCH status, SEBI escalation)
    /api/audit
    /api/system
    /api/backtest
    /api/live           (Fyers live market data — quotes, depth, option chain)
    /api/live/scan*     (detection endpoints, scan_runs, heatmap)

Static:
    /dashboard/*  → dashboard/static/

Scheduler:
    Background APScheduler fires every SCAN_INTERVAL_MINUTES (default 5).
    During market hours (09:15–15:30 IST Mon–Fri): runs intraday detection.
    After close: one EOD scan per day.
    If token is missing/expired: writes "skipped: not authenticated" ScanRun row.
"""
import pathlib
from contextlib import asynccontextmanager

# Load .env file if present (before any other imports that read env vars)
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=pathlib.Path(__file__).parent.parent / ".env", override=False)
except ImportError:
    pass  # python-dotenv not installed; env vars must be set externally

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.db.session import init_db
from app.api.routes import router as legacy_router
from app.api.instruments import router as instruments_router
from app.api.alerts_ext import router as alerts_ext_router
from app.api.audit import router as audit_router
from app.api.system import router as system_router
from app.api.backtest_api import router as backtest_router
from app.api.live_market import router as live_router
from app.api.live_detect import router as live_detect_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.
    Runs init_db and starts the scheduler on startup; stops scheduler on shutdown.
    """
    # Startup
    init_db()
    logger.info("DB initialised.")

    # Apply Phase 1 migration: populate event_date for existing alerts that have none.
    _migrate_alert_event_dates()

    # Start background scanner
    try:
        from app.scheduler import start_scheduler
        start_scheduler()
        logger.info("Detection scheduler started.")
    except Exception as e:
        logger.error("Could not start scheduler: %s", e)

    yield

    # Shutdown
    try:
        from app.scheduler import stop_scheduler
        stop_scheduler()
    except Exception:
        pass


def _migrate_alert_event_dates():
    """
    Safe migration for Phase 1: set event_date = window_start.date() for
    any existing Alert rows that have event_date=NULL.
    This is additive and idempotent — safe to run on every startup.
    """
    from app.db.session import SessionLocal
    from app.db.models import Alert
    db = SessionLocal()
    try:
        null_rows = db.query(Alert).filter(Alert.event_date.is_(None)).all()
        if not null_rows:
            return
        logger.info("Migrating %d alert rows: setting event_date from window_start.", len(null_rows))
        for a in null_rows:
            if a.window_start:
                a.event_date = a.window_start.date()
        db.commit()
        logger.info("Migration complete.")
    except Exception as e:
        db.rollback()
        logger.error("Alert event_date migration failed: %s", e)
    finally:
        db.close()


app = FastAPI(
    title="Sentinel — Market Surveillance Platform",
    description=(
        "Order-level surveillance for Indian markets (equities, penny stocks, "
        "indices, futures, options) with evidence-first alerting for SEBI/exchange "
        "verification. Operational dashboard at /dashboard/."
    ),
    version="0.3.0",
    lifespan=lifespan,
)

# Allow the dashboard (served as static from the same origin) to call the API.
# In development, also allow file:// origin if the dashboard is opened directly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Tighten to specific origin in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Routers ────────────────────────────────────────────────────────────────────

# Legacy endpoints — no prefix, backward compatible
app.include_router(legacy_router)

# New API endpoints
app.include_router(instruments_router)
app.include_router(alerts_ext_router)
app.include_router(audit_router)
app.include_router(system_router)
app.include_router(backtest_router)
app.include_router(live_router)          # Fyers live market data
app.include_router(live_detect_router)   # Real-time detection scan + scan_runs


# ── Dashboard static files ─────────────────────────────────────────────────────
_DASHBOARD_STATIC = pathlib.Path(__file__).parent.parent / "dashboard" / "static"
if _DASHBOARD_STATIC.exists():
    app.mount(
        "/dashboard",
        StaticFiles(directory=str(_DASHBOARD_STATIC), html=True),
        name="dashboard",
    )

# ── Operational Console ────────────────────────────────────────────────────────
# Serves the consolidated regulatory surveillance console at /console/.
_CONSOLE_DIR = pathlib.Path(__file__).parent.parent / "console"
if _CONSOLE_DIR.exists():
    app.mount(
        "/console",
        StaticFiles(directory=str(_CONSOLE_DIR), html=True),
        name="console",
    )
