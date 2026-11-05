"""Trade ingestion endpoint."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import TradeCreate, TradeResponse
from app.services.trade_service import OversellError, ingest_trade

router = APIRouter(prefix="/trades", tags=["trades"])


@router.post("/", response_model=TradeResponse, status_code=201)
def create_trade(
    payload: TradeCreate,
    db: Session = Depends(get_db),
):
    """
    Ingest a single trade.

    - Returns **201** with the trade (first insertion).
    - Returns **200** with ``duplicate: true`` if the (venue, trade_id)
      pair already exists (idempotent replay).
    - Returns **409** if the SELL would drive the position below zero.
    """
    try:
        result = ingest_trade(db, payload)
    except OversellError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "detail": f"oversell_rejected: position for {exc.symbol} is {exc.current_qty}, cannot sell {exc.sell_qty}",
                "error": "oversell_rejected",
                "symbol": exc.symbol,
                "current_position": str(exc.current_qty),
                "attempted_sell": str(exc.sell_qty),
                "message": str(exc),
            },
        )

    # Distinguish first-insert (201) from idempotent replay (200)
    if result.duplicate:
        return JSONResponse(
            content=result.model_dump(mode="json"),
            status_code=200,
        )

    return result

