"""
Reconciliation service.

Compares stored trades for a given venue with an external venue
statement and classifies discrepancies into three buckets:

- **missing**: trades present in the venue statement but absent locally.
- **extra**: trades stored locally but absent from the venue statement.
- **mismatched**: trades present on *both* sides but with differing
  field values (quantity, price, symbol, side, timestamp).
"""

from decimal import Decimal
from typing import Dict

from sqlalchemy.orm import Session

from app.models import Trade
from app.schemas import (
    MismatchDetail,
    ReconcileRequest,
    ReconcileMismatch,
    ReconcileResponse,
    TradeResponse,
    VenueTradeStatement,
)


def reconcile(db: Session, payload: ReconcileRequest) -> ReconcileResponse:
    """Run a full reconciliation for a single venue."""

    venue = payload.venue

    # ── Index stored trades by trade_id ─────────────────────────────── #
    stored_trades = (
        db.query(Trade).filter(Trade.venue == venue).all()
    )
    stored_by_id: Dict[str, Trade] = {t.trade_id: t for t in stored_trades}

    # ── Index venue-statement trades by trade_id ────────────────────── #
    venue_by_id: Dict[str, VenueTradeStatement] = {
        t.trade_id: t for t in payload.trades
    }

    stored_ids = set(stored_by_id.keys())
    venue_ids = set(venue_by_id.keys())

    # ── Missing: in venue but not stored ────────────────────────────── #
    missing = [venue_by_id[tid] for tid in sorted(venue_ids - stored_ids)]

    # ── Extra: stored but not in venue ──────────────────────────────── #
    extra = []
    for tid in sorted(stored_ids - venue_ids):
        t = stored_by_id[tid]
        extra.append(
            TradeResponse(
                id=t.id,
                venue=t.venue,
                trade_id=t.trade_id,
                symbol=t.symbol,
                side=t.side,
                quantity=t.quantity,
                price=t.price,
                timestamp=t.timestamp,
            )
        )

    # ── Mismatched: present on both sides with field differences ───── #
    mismatched = []
    for tid in sorted(stored_ids & venue_ids):
        stored = stored_by_id[tid]
        venue_t = venue_by_id[tid]

        diffs = _compare_trade(stored, venue_t)
        if diffs:
            mismatched.append(
                ReconcileMismatch(trade_id=tid, mismatches=diffs)
            )

    return ReconcileResponse(
        venue=venue,
        missing=missing,
        extra=extra,
        mismatched=mismatched,
    )


def _compare_trade(
    stored: Trade,
    venue: VenueTradeStatement,
) -> list[MismatchDetail]:
    """Return a list of field-level mismatches between stored and venue trade."""

    diffs: list[MismatchDetail] = []

    # Compare each reconcilable field
    fields = [
        ("symbol", str(stored.symbol), str(venue.symbol)),
        ("side", str(stored.side), str(venue.side)),
        ("quantity", str(Decimal(str(stored.quantity))), str(Decimal(str(venue.quantity)))),
        ("price", str(Decimal(str(stored.price))), str(Decimal(str(venue.price)))),
    ]

    for field_name, stored_val, venue_val in fields:
        if stored_val != venue_val:
            diffs.append(
                MismatchDetail(
                    field=field_name,
                    stored=stored_val,
                    venue=venue_val,
                )
            )

    return diffs

