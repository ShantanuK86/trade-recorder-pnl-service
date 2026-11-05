"""Position query endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import PositionResponse
from app.services.position_service import get_position_for_symbol, get_positions

router = APIRouter(prefix="/positions", tags=["positions"])


@router.get("/", response_model=list[PositionResponse])
def list_positions(db: Session = Depends(get_db)):
    """Return aggregate positions for all symbols."""
    return get_positions(db)


@router.get("/{symbol}", response_model=PositionResponse)
def get_position(symbol: str, db: Session = Depends(get_db)):
    """Return the position for a single symbol."""
    pos = get_position_for_symbol(db, symbol)
    if pos is None:
        raise HTTPException(status_code=404, detail="No position for symbol")
    return pos

