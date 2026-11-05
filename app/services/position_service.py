"""
Position query service.

Positions are read from the denormalised ``positions`` table which is
updated transactionally on every trade ingestion.
"""

from decimal import Decimal
from typing import List

from sqlalchemy.orm import Session

from app.models import Position
from app.schemas import PositionResponse


def get_positions(db: Session) -> List[PositionResponse]:
    """Return aggregate position for every symbol that has been traded."""

    rows = db.query(Position).order_by(Position.symbol).all()
    result: List[PositionResponse] = []
    for row in rows:
        net = Decimal(str(row.net_quantity))
        total = Decimal(str(row.total_cost))
        avg = total / net if net > 0 else Decimal("0")
        result.append(
            PositionResponse(
                symbol=row.symbol,
                net_quantity=net,
                average_cost=avg,
                total_cost=total,
            )
        )
    return result


def get_position_for_symbol(db: Session, symbol: str) -> PositionResponse | None:
    """Return the position for a single symbol, or ``None``."""

    row = db.query(Position).filter(Position.symbol == symbol.upper()).first()
    if row is None:
        return None
    net = Decimal(str(row.net_quantity))
    total = Decimal(str(row.total_cost))
    avg = total / net if net > 0 else Decimal("0")
    return PositionResponse(
        symbol=row.symbol,
        net_quantity=net,
        average_cost=avg,
        total_cost=total,
    )

