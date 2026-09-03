from fastapi import FastAPI

from app.db.session import init_db
from app.api.routes import router

app = FastAPI(
    title="Sentinel — Early-Stage Market Manipulation Detection",
    description=(
        "Order-level surveillance for Indian markets (equities, penny stocks, "
        "indices, futures, options) with evidence-first alerting for SEBI/"
        "exchange verification."
    ),
    version="0.1.0",
)


@app.on_event("startup")
def on_startup():
    init_db()


app.include_router(router)
