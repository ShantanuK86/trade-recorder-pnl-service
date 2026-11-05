"""Read the committed per-symbol average-cost projection."""

from decimal import Decimal

from app.finance import money_context
from app.models import Position
from app.schemas import PositionResponse


def _report(row):
    with money_context():
        return PositionResponse(
            symbol=row.symbol,
            net_quantity=row.net_quantity,
            total_cost=row.total_cost,
            average_cost=row.total_cost / row.net_quantity if row.net_quantity else Decimal(0),
        )


def get_positions(db):
    return [_report(row) for row in db.query(Position).order_by(Position.symbol).all()]


def get_position_for_symbol(db, symbol):
    row = db.query(Position).filter_by(symbol=symbol.upper()).one_or_none()
    return _report(row) if row else None
