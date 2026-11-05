"""Read-only comparison by venue identity, Decimal values, and UTC instants."""

from datetime import datetime
from decimal import Decimal

from app.finance import decimal_text
from app.models import Trade
from app.schemas import (
    MismatchDetail,
    ReconcileMismatch,
    ReconcileResponse,
    TradeResponse,
)


def _text(value):
    if isinstance(value, Decimal):
        return decimal_text(value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def reconcile(db, payload):
    query = db.query(Trade).filter(Trade.venue == payload.venue)
    if payload.start is not None:
        query = query.filter(Trade.timestamp >= payload.start, Trade.timestamp < payload.end)
    stored = {row.trade_id: row for row in query.all()}
    external = {row.trade_id: row for row in payload.trades}
    mismatched = []
    for key in sorted(stored.keys() & external.keys()):
        differences = [
            MismatchDetail(
                field=field,
                stored=_text(getattr(stored[key], field)),
                venue=_text(getattr(external[key], field)),
            )
            for field in ("symbol", "side", "quantity", "price", "timestamp")
            if getattr(stored[key], field) != getattr(external[key], field)
        ]
        if differences:
            mismatched.append(ReconcileMismatch(trade_id=key, mismatches=differences))
    return ReconcileResponse(
        venue=payload.venue,
        missing=[external[key] for key in sorted(external.keys() - stored.keys())],
        extra=[
            TradeResponse.model_validate(stored[key])
            for key in sorted(stored.keys() - external.keys())
        ],
        mismatched=mismatched,
    )
