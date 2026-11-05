# Design Document — Trade Recorder & PnL Service

## Overview

A backend service that ingests trades from multiple venues, tracks aggregate
positions per symbol, computes realised and unrealised PnL under two
cost-basis methods, and provides reconciliation against external venue
statements.

The system is designed for **correctness first** — every monetary value is a
`Decimal`, every state mutation is atomic, and every edge case has a
corresponding test.

---

## Architecture

```
┌────────────┐       ┌───────────────────────────────────────────┐
│  HTTP API  │──────▶│  Service Layer (domain logic)             │
│  (FastAPI) │       │  ┌──────────────┐  ┌───────────────────┐  │
│            │       │  │ TradeService  │  │ PositionService   │  │
│  /trades   │       │  │ (ingest,      │  │ (read positions)  │  │
│  /positions│       │  │  idempotency, │  └───────────────────┘  │
│  /pnl      │       │  │  oversell)    │  ┌───────────────────┐  │
│  /reconcile│       │  └──────────────┘  │ PnLService        │  │
└────────────┘       │  ┌──────────────┐  │ (realized, unreal)│  │
                     │  │ ReconcileServ│  └───────────────────┘  │
                     │  └──────────────┘                         │
                     └───────────────────┬───────────────────────┘
                                        │
                     ┌──────────────────▼───────────────────────┐
                     │  SQLAlchemy ORM + SQLite / PostgreSQL     │
                     │  ┌────────┐ ┌──────────┐ ┌────────────┐  │
                     │  │ trades │ │ positions│ │ fifo_lots  │  │
                     │  └────────┘ └──────────┘ └────────────┘  │
                     │  ┌───────────────┐                       │
                     │  │ latest_prices │                       │
                     │  └───────────────┘                       │
                     └──────────────────────────────────────────┘
```

---

## Cost-Basis Method

Both methods are implemented and returned simultaneously from the `/pnl`
endpoint.  The consumer chooses which to use.

### Average Cost (primary)

| Step                      | Calculation                          |
|---------------------------|--------------------------------------|
| BUY *q* @ *p*             | `total_cost += q * p; qty += q`      |
| Average cost              | `total_cost / qty`                   |
| SELL *q* @ *p*            | `realized += q * (p − avg_cost)`     |
| Remaining cost after SELL | `total_cost = avg_cost * remaining`  |
| Unrealised PnL            | `qty * (latest_price − avg_cost)`    |

### FIFO (First In, First Out)

Each BUY creates a **lot** with `(price, remaining_quantity)`.  On SELL,
lots are consumed oldest-first (ordered by trade timestamp, then database
ID).

| Step          | Calculation                                          |
|---------------|------------------------------------------------------|
| SELL *q* @ *p*| Consume lots oldest-first; per lot: `(p − lot_price) * consumed_qty` |
| Unrealised    | Sum over open lots: `lot_remaining * (latest − lot_price)`           |

### Worked Example

> BUY 10 @ 100, BUY 10 @ 110, SELL 5 @ 120, latest price 125

|                    | Average Cost | FIFO   |
|--------------------|-------------|--------|
| Quantity           | 15          | 15     |
| Average Cost       | 105         | 106.67 |
| Realised PnL       | 75          | 100    |
| Unrealised PnL     | 300         | 275    |

---

## Data Model

### `trades` (immutable ledger)

| Column       | Type                 | Notes                              |
|--------------|----------------------|------------------------------------|
| id           | INTEGER PK           | Auto-increment                     |
| venue        | TEXT NOT NULL         | Normalised to lowercase            |
| trade_id     | TEXT NOT NULL         | Venue-specific identifier          |
| symbol       | TEXT NOT NULL         | Normalised to uppercase            |
| side         | TEXT NOT NULL         | "BUY" or "SELL"                    |
| quantity     | NUMERIC NOT NULL     | Always positive                    |
| price        | NUMERIC NOT NULL     | Always positive                    |
| timestamp    | DATETIME(tz) NOT NULL| Must be timezone-aware             |
| created_at   | DATETIME(tz) NOT NULL| Server insertion time              |

**Unique constraint**: `(venue, trade_id)` — the idempotency key.

### `positions` (denormalised aggregate)

| Column            | Type             | Notes                         |
|-------------------|------------------|-------------------------------|
| symbol            | TEXT UNIQUE      | Position key                  |
| net_quantity      | NUMERIC          | Current holding               |
| total_cost        | NUMERIC          | qty × avg_cost                |
| realized_pnl      | NUMERIC          | Cumulative (average cost)     |
| fifo_realized_pnl | NUMERIC          | Cumulative (FIFO)             |

### `fifo_lots` (open buy lots)

| Column             | Type             | Notes                        |
|--------------------|------------------|------------------------------|
| trade_id           | INTEGER FK       | The originating BUY trade    |
| symbol             | TEXT             | For filtering                |
| buy_price          | NUMERIC          | Purchase price               |
| original_quantity  | NUMERIC          | Initial lot size             |
| remaining_quantity | NUMERIC          | Unconsumed portion           |
| trade_timestamp    | DATETIME(tz)     | For FIFO ordering            |

### `latest_prices`

| Column     | Type         | Notes           |
|------------|-------------|-----------------|
| symbol     | TEXT UNIQUE  | Price key       |
| price      | NUMERIC     | Latest price    |
| updated_at | DATETIME(tz)| Last update     |

---

## Key Design Decisions

### 1. Decimal Everywhere — Never Float

All monetary and quantity fields use Python's `decimal.Decimal` in
application code and `NUMERIC` in the database.  JSON serialisation
converts Decimals to strings to avoid IEEE-754 rounding artefacts.  This
is non-negotiable in financial software.

### 2. Idempotency via UNIQUE Constraint + Check-Then-Insert

The `(venue, trade_id)` UNIQUE constraint is the ultimate guard.  The
service first checks for an existing row (fast path for replays), then
attempts an INSERT.  If a race condition causes the INSERT to hit the
constraint, the `IntegrityError` is caught and the existing trade is
returned with `duplicate: true`.  This belt-and-suspenders approach
handles both sequential and concurrent duplicates.

### 3. Atomic Oversell Check

The oversell guard (SELL quantity ≤ current position) and the position
update happen inside the **same database transaction**.  With SQLite this
is naturally serialised via its write-lock.  With PostgreSQL, the
`positions` row would be locked with `SELECT ... FOR UPDATE` to prevent
TOCTOU races under concurrent requests.

### 4. Denormalised Positions Table

Rather than computing positions by aggregating all trades on every read
(O(n) per query), the `positions` table is maintained transactionally
alongside each trade insert.  This gives O(1) reads and makes the
oversell check efficient.  The trade of a denormalised table is that it
must be kept in sync — guaranteed by wrapping both writes in one
transaction.

### 5. Both Cost-Basis Methods Computed Simultaneously

The average-cost position fields and the FIFO lot table are both updated
on every trade.  The `/pnl` endpoint returns both calculations, letting
the consumer choose.  This avoids config-switching complexity and
demonstrates understanding of both methods.

### 6. SQLite for Simplicity, Postgres-Ready

The ORM (SQLAlchemy) abstracts the database.  Switching to PostgreSQL
requires only changing `DATABASE_URL` — all queries, transactions, and
constraints work identically.  SQLite was chosen for zero-setup local
development (no Docker).

### 7. Reconciliation by Trade-ID Key

Reconciliation matches stored trades against a venue statement using
`trade_id` as the join key within a venue.  Discrepancies are classified
as **missing** (venue has it, we don't), **extra** (we have it, venue
doesn't), or **mismatched** (both have it, fields differ).  Field-level
diffs are reported for mismatches.

---

## API Endpoints

| Method | Path                  | Purpose                        | Status Codes         |
|--------|-----------------------|--------------------------------|----------------------|
| POST   | `/trades/`            | Ingest a trade                 | 201, 200 (dup), 409 (oversell), 422 |
| GET    | `/positions/`         | List all positions             | 200                  |
| GET    | `/positions/{symbol}` | Single position                | 200, 404             |
| POST   | `/pnl/set-price`      | Set latest market price        | 204                  |
| GET    | `/pnl/`               | PnL (optional `?symbol=`)      | 200                  |
| POST   | `/reconciliation/`    | Reconcile venue statement      | 200                  |
| GET    | `/health`             | Health check                   | 200                  |

---

## Trade-offs & Future Improvements

1. **Row-level locking**: In production (PostgreSQL), the oversell check
   should use `SELECT ... FOR UPDATE` on the positions row.  SQLite's
   write-level lock is sufficient for single-process deployments.

2. **Event sourcing**: The `trades` table is an append-only ledger.
   Positions can be rebuilt by replaying trades, serving as a recovery
   mechanism.

3. **Batch ingestion**: The current API ingests one trade at a time.
   A `/trades/batch` endpoint with transactional semantics would reduce
   HTTP overhead for high-volume venues.

4. **Timestamp handling**: Timestamps are stored timezone-aware.  A
   production system might enforce UTC normalisation on ingestion.

5. **Short selling**: The current design rejects oversells.  A futures
   or margin account system would allow negative positions.

