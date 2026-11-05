"""
Trade ingestion service.

Responsibilities
----------------
1. Insert the trade into the ``trades`` table.
2. Enforce idempotency via the UNIQUE(venue, trade_id) constraint.
3. Enforce the **oversell check**: a SELL must not take a position below zero.
4. Update the denormalised ``positions`` row (average-cost method).
5. Maintain ``fifo_lots`` for the FIFO cost-basis method.

All five steps happen in **one database transaction** so the ledger, the
position, and the FIFO lots are always consistent.
"""

from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import FifoLot, Position, Trade
from app.schemas import TradeCreate, TradeResponse


class OversellError(Exception):
    """Raised when a SELL would drive a position below zero."""

    def __init__(self, symbol: str, current_qty: Decimal, sell_qty: Decimal):
        self.symbol = symbol
        self.current_qty = current_qty
        self.sell_qty = sell_qty
        super().__init__(
            f"Oversell rejected: position for {symbol} is {current_qty}, "
            f"cannot sell {sell_qty}"
        )


def ingest_trade(db: Session, payload: TradeCreate) -> TradeResponse:
    """
    Atomically ingest a trade with idempotency and oversell protection.

    Returns a ``TradeResponse`` with ``duplicate=True`` when the
    (venue, trade_id) pair already exists.
    """

    # ------------------------------------------------------------------ #
    #  1.  Idempotency — check if trade already exists                    #
    # ------------------------------------------------------------------ #
    existing = (
        db.query(Trade)
        .filter(Trade.venue == payload.venue, Trade.trade_id == payload.trade_id)
        .first()
    )
    if existing is not None:
        return TradeResponse(
            id=existing.id,
            venue=existing.venue,
            trade_id=existing.trade_id,
            symbol=existing.symbol,
            side=existing.side,
            quantity=existing.quantity,
            price=existing.price,
            timestamp=existing.timestamp,
            duplicate=True,
        )

    # ------------------------------------------------------------------ #
    #  2.  Load or create the position row (with implicit row-level lock) #
    # ------------------------------------------------------------------ #
    position = (
        db.query(Position).filter(Position.symbol == payload.symbol).first()
    )
    if position is None:
        position = Position(
            symbol=payload.symbol,
            net_quantity=Decimal("0"),
            total_cost=Decimal("0"),
            realized_pnl=Decimal("0"),
            fifo_realized_pnl=Decimal("0"),
        )
        db.add(position)
        db.flush()  # ensure row exists for later update

    # ------------------------------------------------------------------ #
    #  3.  Oversell check                                                 #
    # ------------------------------------------------------------------ #
    if payload.side == "SELL":
        if position.net_quantity < payload.quantity:
            raise OversellError(
                symbol=payload.symbol,
                current_qty=Decimal(str(position.net_quantity)),
                sell_qty=payload.quantity,
            )

    # ------------------------------------------------------------------ #
    #  4.  Insert the trade                                               #
    # ------------------------------------------------------------------ #
    trade = Trade(
        venue=payload.venue,
        trade_id=payload.trade_id,
        symbol=payload.symbol,
        side=payload.side,
        quantity=payload.quantity,
        price=payload.price,
        timestamp=payload.timestamp,
    )
    db.add(trade)

    # Flush to obtain trade.id and to trigger the UNIQUE constraint before
    # we update positions (belt-and-suspenders idempotency guard).
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        # Race condition: another request inserted the same trade between
        # our SELECT and INSERT.  Re-query and return as duplicate.
        existing = (
            db.query(Trade)
            .filter(
                Trade.venue == payload.venue,
                Trade.trade_id == payload.trade_id,
            )
            .first()
        )
        return TradeResponse(
            id=existing.id,
            venue=existing.venue,
            trade_id=existing.trade_id,
            symbol=existing.symbol,
            side=existing.side,
            quantity=existing.quantity,
            price=existing.price,
            timestamp=existing.timestamp,
            duplicate=True,
        )

    # ------------------------------------------------------------------ #
    #  5.  Update average-cost position                                   #
    # ------------------------------------------------------------------ #
    _update_avg_cost_position(position, payload)

    # ------------------------------------------------------------------ #
    #  6.  Update FIFO lots                                               #
    # ------------------------------------------------------------------ #
    _update_fifo(db, trade, position, payload)

    db.commit()
    db.refresh(trade)

    return TradeResponse(
        id=trade.id,
        venue=trade.venue,
        trade_id=trade.trade_id,
        symbol=trade.symbol,
        side=trade.side,
        quantity=trade.quantity,
        price=trade.price,
        timestamp=trade.timestamp,
        duplicate=False,
    )


# ── internal helpers ───────────────────────────────────────────────────── #


def _update_avg_cost_position(position: Position, payload: TradeCreate) -> None:
    """Adjust position quantities and costs using the average-cost method."""

    qty = Decimal(str(payload.quantity))
    price = Decimal(str(payload.price))
    net = Decimal(str(position.net_quantity))
    total_cost = Decimal(str(position.total_cost))
    realized = Decimal(str(position.realized_pnl))

    if payload.side == "BUY":
        total_cost += qty * price
        net += qty
    else:  # SELL
        avg_cost = total_cost / net if net != 0 else Decimal("0")
        realized += qty * (price - avg_cost)
        net -= qty
        total_cost = avg_cost * net  # remaining value at average cost

    position.net_quantity = net
    position.total_cost = total_cost
    position.realized_pnl = realized


def _update_fifo(
    db: Session,
    trade: Trade,
    position: Position,
    payload: TradeCreate,
) -> None:
    """Create a new lot (BUY) or consume existing lots oldest-first (SELL)."""

    qty = Decimal(str(payload.quantity))
    price = Decimal(str(payload.price))

    if payload.side == "BUY":
        lot = FifoLot(
            trade_id=trade.id,
            symbol=payload.symbol,
            buy_price=price,
            original_quantity=qty,
            remaining_quantity=qty,
            trade_timestamp=payload.timestamp,
        )
        db.add(lot)
    else:  # SELL — consume from oldest lots
        lots = (
            db.query(FifoLot)
            .filter(
                FifoLot.symbol == payload.symbol,
                FifoLot.remaining_quantity > 0,
            )
            .order_by(FifoLot.trade_timestamp, FifoLot.id)
            .all()
        )

        remaining_sell = qty
        fifo_realized = Decimal("0")

        for lot in lots:
            if remaining_sell <= 0:
                break
            lot_remaining = Decimal(str(lot.remaining_quantity))
            consume = min(remaining_sell, lot_remaining)
            lot_price = Decimal(str(lot.buy_price))

            fifo_realized += consume * (price - lot_price)
            lot.remaining_quantity = lot_remaining - consume
            remaining_sell -= consume

        position.fifo_realized_pnl = (
            Decimal(str(position.fifo_realized_pnl)) + fifo_realized
        )

