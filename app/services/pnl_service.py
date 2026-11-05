"""
PnL computation service.

Computes both **average-cost** and **FIFO** realised / unrealised PnL
from the denormalised ``positions`` and ``fifo_lots`` tables.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models import FifoLot, LatestPrice, Position
from app.schemas import PnLResponse, SetLatestPriceRequest


def set_latest_price(db: Session, payload: SetLatestPriceRequest) -> None:
    """Upsert the latest market price for a symbol."""

    symbol = payload.symbol.upper()
    row = db.query(LatestPrice).filter(LatestPrice.symbol == symbol).first()

    if row is None:
        row = LatestPrice(
            symbol=symbol,
            price=payload.price,
            updated_at=datetime.now(timezone.utc),
        )
        db.add(row)
    else:
        row.price = payload.price
        row.updated_at = datetime.now(timezone.utc)

    db.commit()


def get_pnl(db: Session, symbol: Optional[str] = None) -> List[PnLResponse]:
    """
    Compute PnL for one or all symbols.

    Returned values include both average-cost and FIFO cost-basis
    computations so the consumer can choose.
    """

    query = db.query(Position)
    if symbol:
        query = query.filter(Position.symbol == symbol.upper())
    positions = query.order_by(Position.symbol).all()

    results: List[PnLResponse] = []
    for pos in positions:
        sym = pos.symbol
        net_qty = Decimal(str(pos.net_quantity))
        total_cost = Decimal(str(pos.total_cost))
        avg_realized = Decimal(str(pos.realized_pnl))
        fifo_realized = Decimal(str(pos.fifo_realized_pnl))

        # Average cost
        avg_cost = total_cost / net_qty if net_qty > 0 else Decimal("0")

        # FIFO average cost — weighted average of remaining lots
        fifo_lots = (
            db.query(FifoLot)
            .filter(FifoLot.symbol == sym, FifoLot.remaining_quantity > 0)
            .all()
        )
        fifo_total_cost = Decimal("0")
        fifo_total_qty = Decimal("0")
        for lot in fifo_lots:
            rem = Decimal(str(lot.remaining_quantity))
            bp = Decimal(str(lot.buy_price))
            fifo_total_cost += rem * bp
            fifo_total_qty += rem

        fifo_avg_cost = (
            fifo_total_cost / fifo_total_qty
            if fifo_total_qty > 0
            else Decimal("0")
        )

        # Latest price lookup
        lp_row = (
            db.query(LatestPrice).filter(LatestPrice.symbol == sym).first()
        )
        latest_price: Optional[Decimal] = (
            Decimal(str(lp_row.price)) if lp_row else None
        )

        # Unrealised PnL
        if latest_price is not None and net_qty > 0:
            avg_unrealized = net_qty * (latest_price - avg_cost)
            # FIFO unrealised: per-lot calculation
            fifo_unrealized = Decimal("0")
            for lot in fifo_lots:
                rem = Decimal(str(lot.remaining_quantity))
                bp = Decimal(str(lot.buy_price))
                fifo_unrealized += rem * (latest_price - bp)
        else:
            avg_unrealized = Decimal("0")
            fifo_unrealized = Decimal("0")

        results.append(
            PnLResponse(
                symbol=sym,
                net_quantity=net_qty,
                avg_cost=avg_cost,
                avg_cost_realized_pnl=avg_realized,
                avg_cost_unrealized_pnl=avg_unrealized,
                fifo_avg_cost=fifo_avg_cost,
                fifo_realized_pnl=fifo_realized,
                fifo_unrealized_pnl=fifo_unrealized,
                latest_price=latest_price,
            )
        )

    return results

