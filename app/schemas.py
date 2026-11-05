"""Validated requests and Decimal-string responses; shared statement semantics."""

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    computed_field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.finance import decimal_text, positive_amount


def identifier(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 200:
        raise ValueError("identifier must contain 1-200 characters")
    if any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError("identifier contains invalid control or Unicode characters")
    return value


def timestamp_input(value: object) -> object:
    if not isinstance(value, (str, datetime)):
        raise ValueError("use a timezone-aware ISO-8601 timestamp")
    if isinstance(value, str):
        if not re.match(r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}", value):
            raise ValueError("use a timezone-aware ISO-8601 timestamp")
        fraction = re.search(r"[Tt ]\d{2}:\d{2}:\d{2}[.,](\d+)", value)
        if fraction and any(c != "0" for c in fraction[1][6:]):
            raise ValueError("timestamps support microsecond precision")
    return value


def utc(value: datetime) -> datetime:
    try:
        return value.astimezone(UTC)
    except (OverflowError, ValueError) as exc:
        raise ValueError("timestamp is outside the supported UTC range") from exc


Amount = Annotated[Decimal, BeforeValidator(positive_amount, json_schema_input_type=str | int)]
Timestamp = Annotated[AwareDatetime, BeforeValidator(timestamp_input), AfterValidator(utc)]


class APIModel(BaseModel):
    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_decimal(self, value):
        return decimal_text(value) if isinstance(value, Decimal) else value


class TradeFields(APIModel):
    model_config = ConfigDict(extra="forbid")
    trade_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: Amount
    price: Amount
    timestamp: Timestamp

    @field_validator("trade_id", "symbol")
    @classmethod
    def validate_identifier(cls, value):
        return identifier(value)

    @field_validator("symbol")
    @classmethod
    def canonical_symbol(cls, value):
        return value.upper()


class TradeCreate(TradeFields):
    venue: str

    @field_validator("venue")
    @classmethod
    def canonical_venue(cls, value):
        return identifier(value).lower()


class SetLatestPriceRequest(APIModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    price: Amount

    @field_validator("symbol")
    @classmethod
    def canonical_symbol(cls, value):
        return identifier(value).upper()


class VenueTradeStatement(TradeFields):
    # Also accepts a full trade object, while preserving the original response shape.
    venue: str | None = Field(default=None, exclude=True)

    @field_validator("venue")
    @classmethod
    def canonical_venue(cls, value):
        return identifier(value).lower() if value is not None else None


class ReconcileRequest(APIModel):
    model_config = ConfigDict(extra="forbid")
    venue: str
    trades: list[VenueTradeStatement] = Field(max_length=10000)
    start: Timestamp | None = None
    end: Timestamp | None = None

    @field_validator("venue")
    @classmethod
    def canonical_venue(cls, value):
        return identifier(value).lower()

    @model_validator(mode="after")
    def validate_statement(self):
        if (self.start is None) != (self.end is None):
            raise ValueError("provide both start and end, or neither")
        if self.start is not None and self.start >= self.end:
            raise ValueError("start must precede end; interval is [start, end)")
        seen = set()
        for trade in self.trades:
            if trade.trade_id in seen:
                raise ValueError("duplicate trade_id in external statement")
            seen.add(trade.trade_id)
            if trade.venue is not None and trade.venue != self.venue:
                raise ValueError("statement contains a trade from another venue")
            if self.start is not None and not self.start <= trade.timestamp < self.end:
                raise ValueError("external trade is outside [start, end)")
        return self


class TradeResponse(APIModel):
    id: int
    venue: str
    trade_id: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    timestamp: datetime
    duplicate: bool = False
    model_config = ConfigDict(from_attributes=True)


class PositionResponse(APIModel):
    symbol: str
    net_quantity: Decimal
    average_cost: Decimal
    total_cost: Decimal = Decimal("0")
    model_config = ConfigDict(from_attributes=True)

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


class PnLResponse(APIModel):
    symbol: str
    net_quantity: Decimal
    avg_cost: Decimal
    avg_cost_realized_pnl: Decimal
    avg_cost_unrealized_pnl: Decimal
    fifo_avg_cost: Decimal
    fifo_realized_pnl: Decimal
    fifo_unrealized_pnl: Decimal
    latest_price: Decimal | None = None

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


class MismatchDetail(APIModel):
    field: str
    stored: str
    venue: str


class ReconcileMismatch(APIModel):
    trade_id: str
    mismatches: list[MismatchDetail]


class ReconcileResponse(APIModel):
    venue: str
    missing: list[VenueTradeStatement]
    extra: list[TradeResponse]
    mismatched: list[ReconcileMismatch]
