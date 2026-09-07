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

Static:
    /dashboard/*  → dashboard/static/
"""
import pathlib

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

app = FastAPI(
    title="Sentinel — Market Surveillance Platform",
    description=(
        "Order-level surveillance for Indian markets (equities, penny stocks, "
        "indices, futures, options) with evidence-first alerting for SEBI/exchange "
        "verification. Operational dashboard at /dashboard/."
    ),
    version="0.2.0",
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


@app.on_event("startup")
def on_startup():
    init_db()


# ── Routers ────────────────────────────────────────────────────────────────────

# Legacy endpoints — no prefix, backward compatible
app.include_router(legacy_router)

# New API endpoints
app.include_router(instruments_router)
app.include_router(alerts_ext_router)
app.include_router(audit_router)
app.include_router(system_router)
app.include_router(backtest_router)

# ── Dashboard static files ─────────────────────────────────────────────────────
_DASHBOARD_STATIC = pathlib.Path(__file__).parent.parent / "dashboard" / "static"
if _DASHBOARD_STATIC.exists():
    app.mount(
        "/dashboard",
        StaticFiles(directory=str(_DASHBOARD_STATIC), html=True),
        name="dashboard",
    )
