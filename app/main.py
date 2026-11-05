"""
Trade Recorder & PnL Service — FastAPI application entry point.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import init_db
from app.routers import pnl, positions, reconciliation, trades


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Trade Recorder & PnL Service",
    description=(
        "Backend service that ingests trades from multiple venues, "
        "tracks positions, and reports PnL with idempotency, "
        "reconciliation, and oversell protection."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# ── Routers ─────────────────────────────────────────────────────────────── #
app.include_router(trades.router)
app.include_router(positions.router)
app.include_router(pnl.router)
app.include_router(reconciliation.router)


@app.get("/health", tags=["ops"])
def health_check():
    return {"status": "ok"}

