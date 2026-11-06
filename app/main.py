"""
Trade Recorder & PnL Service — FastAPI application entry point.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.exc import SQLAlchemyError

from app.database import init_db
from app.finance import ConflictError
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


@app.exception_handler(ConflictError)
async def conflict_error(_request: Request, exc: ConflictError):
    return JSONResponse(
        status_code=409,
        content={
            "error": exc.code,
            "detail": exc.message,
            **exc.details,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError):
    # Do not echo nonfinite numbers, raw payloads, or exception objects into JSON.
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]} for e in exc.errors()
            ]
        },
    )


@app.exception_handler(SQLAlchemyError)
async def database_error(_request: Request, exc: SQLAlchemyError):
    logging.getLogger(__name__).error("Database failure: %s", type(exc).__name__)
    return JSONResponse(
        status_code=503,
        content={
            "error": "database_unavailable",
            "detail": "Database operation failed. Retry trades with the same venue, trade_id and payload.",
        },
    )


@app.get("/", tags=["ops"])
def root(request: Request):
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return RedirectResponse(url="/docs")
    return {
        "service": "Trade Recorder & PnL Service",
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
        "endpoints": {
            "trades": "/trades/",
            "positions": "/positions/",
            "pnl": "/pnl/",
            "reconciliation": "/reconciliation/",
        },
    }


@app.get("/health", tags=["ops"])
def health_check():
    return {"status": "ok"}
