"""
SQLAlchemy ORM models.

Financial quantities use Numeric (maps to NUMERIC/DECIMAL in Postgres,
REAL with driver-level Decimal adaptation in SQLite).  All application
code works exclusively with ``decimal.Decimal``.

Tables
------
- trades          Immutable ledger of ingested trades.
- positions       Denormalised position state per symbol (average-cost basis).
- fifo_lots       Open buy-lots for FIFO cost-basis tracking.
- latest_prices   Most recent market prices per symbol.
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    Numeric,
    String,
    TypeDecorator,
    UniqueConstraint,
    ForeignKey,
    Index,
)

from app.database import Base


class DecimalField(TypeDecorator):
    """
    Stores Decimal as String in SQLite (preserving arbitrary precision and scale
    without floating-point inaccuracies or truncation), while compiling to
    NUMERIC(38, 18) in PostgreSQL.
    """
    impl = Numeric
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String())
        return dialect.type_descriptor(Numeric(38, 18, asdecimal=True))

    def process_bind_param(self, value, dialect):
        return str(value) if value is not None else None

    def process_result_value(self, value, dialect):
        return Decimal(str(value)) if value is not None else None


class TZDateTime(TypeDecorator):
    """
    Stores and retrieves timezone-aware datetimes normalized to UTC.
    In SQLite, DateTime(timezone=True) drops tzinfo on retrieval.
    In PostgreSQL, it compiles to TIMESTAMP WITH TIME ZONE.
    """
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("datetime must be timezone-aware")
            return value.astimezone(timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        return value


class Trade(Base):
    """Immutable trade record.  (venue, trade_id) is the idempotency key."""

    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    venue = Column(String, nullable=False)
    trade_id = Column(String, nullable=False)
    symbol = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False)           # "BUY" | "SELL"
    quantity = Column(DecimalField, nullable=False)  # always positive
    price = Column(DecimalField, nullable=False)     # always positive
    timestamp = Column(TZDateTime, nullable=False)
    created_at = Column(
        TZDateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("venue", "trade_id", name="uq_venue_trade_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<Trade {self.venue}:{self.trade_id} "
            f"{self.side} {self.quantity}x{self.symbol}@{self.price}>"
        )


class Position(Base):
    """
    Denormalised aggregate position per symbol (average-cost method).

    Updated *atomically* inside the same transaction that inserts a trade.
    """

    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False, unique=True, index=True)
    net_quantity = Column(DecimalField, nullable=False, default=Decimal("0"))
    total_cost = Column(DecimalField, nullable=False, default=Decimal("0"))       # qty * avg
    realized_pnl = Column(DecimalField, nullable=False, default=Decimal("0"))     # avg-cost basis
    fifo_realized_pnl = Column(DecimalField, nullable=False, default=Decimal("0"))  # fifo basis

    def __repr__(self) -> str:
        return f"<Position {self.symbol} qty={self.net_quantity}>"


class FifoLot(Base):
    """
    A remaining buy-lot used by the FIFO cost-basis method.

    When a SELL occurs, lots are consumed oldest-first (ordered by the
    originating trade's timestamp then database id).
    """

    __tablename__ = "fifo_lots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False)
    symbol = Column(String, nullable=False, index=True)
    buy_price = Column(DecimalField, nullable=False)
    original_quantity = Column(DecimalField, nullable=False)
    remaining_quantity = Column(DecimalField, nullable=False)
    trade_timestamp = Column(TZDateTime, nullable=False)

    __table_args__ = (
        Index("ix_fifo_lots_symbol_ts", "symbol", "trade_timestamp"),
    )

    def __repr__(self) -> str:
        return (
            f"<FifoLot trade={self.trade_id} "
            f"remaining={self.remaining_quantity}@{self.buy_price}>"
        )


class LatestPrice(Base):
    """Latest observed market price for a symbol."""

    __tablename__ = "latest_prices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False, unique=True, index=True)
    price = Column(DecimalField, nullable=False)
    updated_at = Column(
        TZDateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<LatestPrice {self.symbol}={self.price}>"

