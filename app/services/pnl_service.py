"""Atomic mark upserts and a single-statement snapshot for PnL reports."""

from datetime import UTC, datetime
from decimal import Decimal

from app.database import write_transaction
from app.finance import money_context
from app.models import FifoLot, LatestPrice, Position
from app.schemas import PnLResponse
from app.services.trade_service import insert_for


def set_latest_price(db, payload):
    with write_transaction(db):
        statement = insert_for(db, LatestPrice).values(
            symbol=payload.symbol, price=payload.price, updated_at=datetime.now(UTC)
        )
        db.execute(
            statement.on_conflict_do_update(
                index_elements=["symbol"],
                set_={
                    "price": statement.excluded.price,
                    "updated_at": statement.excluded.updated_at,
                },
            )
        )


def get_pnl(db, symbol=None):
    # Positions, marks and lots must come from the same committed snapshot.
    query = (
        db.query(Position, LatestPrice.price, FifoLot)
        .outerjoin(LatestPrice, LatestPrice.symbol == Position.symbol)
        .outerjoin(FifoLot, FifoLot.symbol == Position.symbol)
    )
    if symbol:
        query = query.filter(Position.symbol == symbol.upper())
    grouped = {}
    for position, mark, lot in query.order_by(Position.symbol, FifoLot.id).all():
        entry = grouped.setdefault(position.symbol, (position, mark, []))
        if lot is not None and lot.remaining_quantity > 0:
            entry[2].append(lot)
    reports = []
    with money_context():
        for position, mark, lots in grouped.values():
            quantity = position.net_quantity
            average = position.total_cost / quantity if quantity else Decimal(0)
            fifo_cost = sum((lot.remaining_quantity * lot.buy_price for lot in lots), Decimal(0))
            fifo_quantity = sum((lot.remaining_quantity for lot in lots), Decimal(0))
            fifo_average = fifo_cost / fifo_quantity if fifo_quantity else Decimal(0)
            # Preserve the existing API's zero-without-mark convention; latest_price
            # remains null so callers can distinguish an unknown mark.
            unrealized = quantity * mark - position.total_cost if mark is not None else Decimal(0)
            fifo_unrealized = quantity * mark - fifo_cost if mark is not None else Decimal(0)
            reports.append(
                PnLResponse(
                    symbol=position.symbol,
                    net_quantity=quantity,
                    avg_cost=average,
                    avg_cost_realized_pnl=position.realized_pnl,
                    avg_cost_unrealized_pnl=unrealized,
                    fifo_avg_cost=fifo_average,
                    fifo_realized_pnl=position.fifo_realized_pnl,
                    fifo_unrealized_pnl=fifo_unrealized,
                    latest_price=mark,
                )
            )
    return reports
