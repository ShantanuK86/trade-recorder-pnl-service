"""PnL endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import PnLResponse, SetLatestPriceRequest
from app.services.pnl_service import get_pnl, set_latest_price

router = APIRouter(prefix="/pnl", tags=["pnl"])


@router.post("/set-price", status_code=204)
def update_latest_price(
    payload: SetLatestPriceRequest,
    db: Session = Depends(get_db),
):
    """Set the latest market price for a symbol (used for unrealised PnL)."""
    set_latest_price(db, payload)


@router.get("/", response_model=list[PnLResponse])
def list_pnl(
    symbol: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Return PnL for all symbols (or a single symbol via query param).

    Both average-cost and FIFO figures are included.
    """
    return get_pnl(db, symbol)
