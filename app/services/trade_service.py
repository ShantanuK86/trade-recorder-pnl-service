"""One transaction owns the ledger, locked position and FIFO lot mutations."""

from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.database import write_transaction
from app.finance import COST_QUANTUM, ConflictError, check_capacity, money_context
from app.models import FifoLot, Position, Trade
from app.schemas import TradeCreate, TradeResponse

FIELDS = ("venue", "trade_id", "symbol", "side", "quantity", "price", "timestamp")


class OversellError(Exception):
    def __init__(self, symbol, current_qty, sell_qty):
        self.symbol, self.current_qty, self.sell_qty = symbol, current_qty, sell_qty
        super().__init__(
            f"Oversell rejected: position for {symbol} is {current_qty}, cannot sell {sell_qty}"
        )


def insert_for(db, model):
    return pg_insert(model) if db.bind.dialect.name == "postgresql" else sqlite_insert(model)


def ingest_trade(db: Session, payload: TradeCreate) -> TradeResponse:
    with write_transaction(db), money_context():
        values = payload.model_dump()
        # The unique index resolves simultaneous identities BEFORE balance checks.
        inserted = db.execute(
            insert_for(db, Trade)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["venue", "trade_id"])
            .returning(Trade.id)
        ).scalar_one_or_none()
        if inserted is None:
            existing = (
                db.query(Trade).filter_by(venue=payload.venue, trade_id=payload.trade_id).one()
            )
            changed = [field for field in FIELDS if getattr(existing, field) != values[field]]
            if changed:
                raise ConflictError(
                    "idempotency_conflict",
                    "trade identity already exists with different data",
                    fields=changed,
                )
            return TradeResponse.model_validate(existing).model_copy(update={"duplicate": True})

        db.execute(
            insert_for(db, Position)
            .values(
                symbol=payload.symbol,
                net_quantity=Decimal(0),
                total_cost=Decimal(0),
                realized_pnl=Decimal(0),
                fifo_realized_pnl=Decimal(0),
            )
            .on_conflict_do_nothing(index_elements=["symbol"])
        )
        query = db.query(Position).filter_by(symbol=payload.symbol)
        if db.bind.dialect.name == "postgresql":
            query = query.with_for_update()
        position = query.one()

        if payload.side == "SELL" and payload.quantity > position.net_quantity:
            raise OversellError(payload.symbol, position.net_quantity, payload.quantity)
        _update_avg_cost_position(position, payload)
        _update_fifo(db, inserted, position, payload)
        check_capacity(
            position.net_quantity,
            position.total_cost,
            position.realized_pnl,
            position.fifo_realized_pnl,
        )
        # Context exit flushes and commits before the caller receives this response.
        return TradeResponse(id=inserted, **values)


def _update_avg_cost_position(position, payload):
    qty, price = payload.quantity, payload.price
    if payload.side == "BUY":
        position.total_cost += qty * price
        position.net_quantity += qty
    else:
        removed = (
            position.total_cost
            if qty == position.net_quantity
            else (position.total_cost * qty / position.net_quantity).quantize(COST_QUANTUM)
        )
        position.realized_pnl += qty * price - removed
        position.net_quantity -= qty
        position.total_cost -= removed


def _update_fifo(db, trade_id, position, payload):
    if payload.side == "BUY":
        db.add(
            FifoLot(
                trade_id=trade_id,
                symbol=payload.symbol,
                buy_price=payload.price,
                original_quantity=payload.quantity,
                remaining_quantity=payload.quantity,
                trade_timestamp=payload.timestamp,
            )
        )
        return
    lots = (
        db.query(FifoLot)
        .filter_by(symbol=payload.symbol)
        .order_by(FifoLot.trade_timestamp, FifoLot.id)
        .all()
    )
    remaining = payload.quantity
    profit = Decimal(0)
    for lot in lots:
        # SQLite stores exact decimal TEXT; never use lexicographic SQL comparisons.
        if lot.remaining_quantity <= 0:
            continue
        consumed = min(remaining, lot.remaining_quantity)
        profit += consumed * (payload.price - lot.buy_price)
        lot.remaining_quantity -= consumed
        remaining -= consumed
        if not remaining:
            break
    if remaining:
        raise ConflictError("inconsistent_inventory", "FIFO lots disagree with the position")
    position.fifo_realized_pnl += profit
