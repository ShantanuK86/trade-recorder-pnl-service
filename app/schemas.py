"""
Pydantic schemas (request / response models).

All monetary fields are ``Decimal``, serialised as strings in JSON to
avoid IEEE-754 floating-point drift — critical for financial applications.
"""

from datetime import datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, computed_field, field_validator, model_validator


# ── Request schemas ─────────────────────────────────────────────────────── #

class TradeCreate(BaseModel):
    """Payload for ingesting a single trade."""

    venue: str
    trade_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: Decimal
    price: Decimal
    timestamp: datetime

    # -- validators -------------------------------------------------------- #

    @field_validator("quantity")
    @classmethod
    def quantity_must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("quantity must be positive")
        return v

    @field_validator("price")
    @classmethod
    def price_must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("price must be positive")
        return v

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return v

    @field_validator("venue")
    @classmethod
    def venue_must_be_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("venue must be non-empty")
        return v.strip().lower()

    @field_validator("trade_id")
    @classmethod
    def trade_id_must_be_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("trade_id must be non-empty")
        return v.strip()

    @field_validator("symbol")
    @classmethod
    def symbol_must_be_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("symbol must be non-empty")
        return v.strip().upper()


class SetLatestPriceRequest(BaseModel):
    """Payload for setting the latest market price of a symbol."""

    symbol: str
    price: Decimal

    @field_validator("price")
    @classmethod
    def price_must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("price must be positive")
        return v

    @field_validator("symbol")
    @classmethod
    def symbol_must_be_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("symbol must be non-empty")
        return v.strip().upper()


class VenueTradeStatement(BaseModel):
    """A single trade entry from an external venue statement."""

    trade_id: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    timestamp: datetime


class ReconcileRequest(BaseModel):
    """Payload for reconciliation: an external venue statement."""

    venue: str
    trades: List[VenueTradeStatement]

    @field_validator("venue")
    @classmethod
    def venue_must_be_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("venue must be non-empty")
        return v.strip().lower()


# ── Response schemas ────────────────────────────────────────────────────── #

class TradeResponse(BaseModel):
    """Returned after a trade is ingested."""

    id: int
    venue: str
    trade_id: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    timestamp: datetime
    duplicate: bool = False

    model_config = {"from_attributes": True}


class PositionResponse(BaseModel):
    """Aggregate position for one symbol."""

    symbol: str
    net_quantity: Decimal
    average_cost: Decimal
    total_cost: Decimal = Decimal("0")

    @computed_field
    @property
    def quantity(self) -> Decimal:
        return self.net_quantity

    @computed_field
    @property
    def current_position(self) -> Decimal:
        return self.net_quantity

    @computed_field
    @property
    def avg_cost(self) -> Decimal:
        return self.average_cost

    model_config = {"from_attributes": True}


class PnLResponse(BaseModel):
    """Profit-and-loss for one symbol (both cost-basis methods)."""

    symbol: str
    net_quantity: Decimal
    # Average-cost basis
    avg_cost: Decimal
    avg_cost_realized_pnl: Decimal
    avg_cost_unrealized_pnl: Decimal
    # FIFO basis
    fifo_avg_cost: Decimal
    fifo_realized_pnl: Decimal
    fifo_unrealized_pnl: Decimal
    # Market price
    latest_price: Optional[Decimal] = None

    @computed_field
    @property
    def realized_pnl(self) -> Decimal:
        return self.avg_cost_realized_pnl

    @computed_field
    @property
    def unrealized_pnl(self) -> Decimal:
        return self.avg_cost_unrealized_pnl

    @computed_field
    @property
    def quantity(self) -> Decimal:
        return self.net_quantity


class MismatchDetail(BaseModel):
    """One field-level mismatch between stored and venue trade."""

    field: str
    stored: str
    venue: str


class ReconcileMismatch(BaseModel):
    """A trade present on both sides but with differing fields."""

    trade_id: str
    mismatches: List[MismatchDetail]


class ReconcileResponse(BaseModel):
    """Result of reconciling stored trades against a venue statement."""

    venue: str
    missing: List[VenueTradeStatement]   # in venue statement, not in our records
    extra: List[TradeResponse]           # in our records, not in venue statement
    mismatched: List[ReconcileMismatch]  # in both but fields differ

