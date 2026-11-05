# Trade Recorder & PnL Service

A small FastAPI backend for long-only spot trades, atomic idempotency and oversell
protection, positions, average-cost and FIFO PnL, and venue reconciliation.
SQLite runs locally without external services. PostgreSQL is also supported and
has been tested with real concurrent HTTP requests. No Docker is required.

## Run locally

Use Python 3.12+ with SQLite 3.35+ (included in typical Python distributions):

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

Windows PowerShell activation: `.\.venv\Scripts\Activate.ps1`.
Open http://127.0.0.1:8000/docs. The default database is `trade_recorder.db` in the
working directory. Tables are initialized automatically. Use `DATABASE_URL` to
choose another database. Both cost-basis methods are always returned; the short
`realized_pnl` / `unrealized_pnl` aliases refer to average cost.

For PostgreSQL, create a database using your normal local PostgreSQL installation:

```bash
createdb trade_recorder
export DATABASE_URL='postgresql+psycopg://localhost/trade_recorder'
uvicorn app.main:app --workers 3
```

Use your own username, password and host when required by your local setup.
`postgresql://...` and `postgres://...` URLs also select the included Psycopg driver.
In PowerShell, set `$env:DATABASE_URL = 'your database URL'` instead of `export`.

### Upgrading an existing database

The original PostgreSQL version used NUMERIC(38,18), which could round valid cost
products to zero. Stop application writers, back up the database, and run:

```bash
python -m app.migrate
```

This repeatable, transactional migration widens money columns to NUMERIC(120,36)
and adds database checks. Startup refuses to use the old numeric schema.
SQLite already uses exact decimal TEXT storage and needs no numeric type change.

**A schema migration cannot recover digits already rounded away or positions
already corrupted by older concurrent writes.** Reconcile existing records against
original venue data and repair/rebuild affected projections before relying on them.
The migration preserves rows; it never drops trade data or silently repairs history.
It can refuse invalid historical rows when adding constraints.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/trades/` | Ingest one fill; 201 new, 200 identical retry, 409 oversell/conflict/capacity, 422 invalid input |
| GET | `/positions/` | Positions for all traded symbols |
| GET | `/positions/{symbol}` | One position, or 404; slash-containing symbols are supported |
| POST | `/pnl/set-price` | Set a latest mark; 204 |
| GET | `/pnl/?symbol=BTC-USD` | Average-cost and FIFO PnL; omit symbol for all positions |
| POST | `/reconciliation/` | Read-only missing, extra and field-level mismatch report |
| GET | `/health` | Process liveness; this endpoint is not a database readiness probe |

Create a trade:

```bash
curl -i http://127.0.0.1:8000/trades/ \
  -H 'Content-Type: application/json' \
  -d '{"venue":"binance","trade_id":"T-1001","symbol":"BTC-USD","side":"BUY","quantity":"10","price":"100","timestamp":"2026-11-03T10:00:00Z"}'
```

Use decimal **strings** for quantity and price; exact JSON integers also work.
JSON fractional numbers and booleans are rejected because a float decoder can lose
input precision. Valid amounts are finite, positive, below 1e24 and have at most
18 fractional places, ignoring trailing zeroes. Inputs longer than 120 characters
are rejected. Derived amounts retain 36 fractional places, with a local 160-digit
Decimal context and half-even partial-sale basis allocation.

Venue names are trimmed/lowercased; symbols trimmed/uppercased; trade IDs trimmed
and case-sensitive. Identifiers must contain 1-200 characters and no control or
invalid Unicode characters. Timestamps must be ISO-8601 with a timezone, normalize
to UTC, and support microseconds. Unknown request fields are rejected.

A retry must use the same `(venue, trade_id)` and economic payload. Reusing the key
with a changed symbol, side, quantity, price or timestamp returns 409
`idempotency_conflict`; equivalent Decimal values and UTC instants count as equal.
Retry transient database failures (503) with the **same** identity and payload.
Rejected SELLs leave no ledger row and do not reserve their identity.

### Worked example

After BUY 10 @ 100, BUY 10 @ 110, and SELL 5 @ 120, set the mark:

```bash
curl -X POST http://127.0.0.1:8000/pnl/set-price \
  -H 'Content-Type: application/json' -d '{"symbol":"BTC-USD","price":"125"}'
curl 'http://127.0.0.1:8000/pnl/?symbol=BTC-USD'
```

| Method | Quantity | Average remaining cost | Realized | Unrealized |
|---|---:|---:|---:|---:|
| Average cost | 15 | 105 | 75 | 300 |
| FIFO | 15 | 106.666… | 100 | 275 |

All financial response fields are strings. For compatibility with the original
API, unrealized PnL is zero before a mark is set; `latest_price` is null, so callers
must check that field before interpreting a zero as a marked valuation.

### Reconciliation

```json
{
  "venue": "binance",
  "trades": [
    {"trade_id":"T1","symbol":"BTC-USD","side":"BUY","quantity":"10","price":"100","timestamp":"2026-11-03T10:00:00Z"},
    {"trade_id":"T2","symbol":"BTC-USD","side":"BUY","quantity":"6","price":"101","timestamp":"2026-11-03T10:00:00Z"},
    {"trade_id":"T3","symbol":"BTC-USD","side":"BUY","quantity":"1","price":"99","timestamp":"2026-11-03T10:00:00Z"}
  ]
}
```

If stored T1 is 10 @ 100 and T2 is 5 @ 101, T2 is a quantity mismatch and T3 is
missing. `extra` means a stored trade absent from the external statement.
Symbol, side, quantity, price and timestamp are compared by value, not formatting.

Supply a **complete** statement for the venue across all symbols, or include both
`start` and `end` to scope it to `[start, end)`. Partial pages cannot establish
which trades are extra. Duplicate statement IDs, mixed venues, invalid rows,
out-of-window rows and more than 10,000 rows are rejected. A row may optionally
include a matching `venue`. Reconciliation never repairs or mutates records.

## Test

```bash
python -m pytest -q
ruff check app tests/test_regressions.py tests_live tests_postgres
```

The default suite includes the original tests plus regression/property-style cases
for the audit findings, precision, statement validation and failure rollback.

The independent live tests use separate HTTP clients, no private application
functions, and require a live server with a **dedicated test database**:

```bash
# With the API running in another terminal:
export BASE_URL='http://127.0.0.1:8000'
python -m pytest tests_live -q
```

They append randomly namespaced synthetic trades rather than resetting user tables.
Repeat with one and three Uvicorn workers to check thread/process concurrency.
To execute the PostgreSQL schema-upgrade regression:

```bash
export TEST_DATABASE_URL='postgresql+psycopg://localhost/trade_recorder_test'
python -m pytest tests_postgres -q
```

That test uses and removes its own randomly named schema. It checks legacy numeric
columns, preserved records, repeatable migration, new constraints and tiny products.
Do not use pytest-xdist with the original in-memory fixtures. See
[TEST_RESULTS.md](TEST_RESULTS.md) for executed results and dependency versions.

## Scope

One portfolio; positions aggregate across venues by canonical symbol. Average-cost
accounting follows transaction serialization order, not source event time. FIFO
consumes available lots by source timestamp, then lot ID. Late fills do not trigger
historical replay. Symbols must identify the same economic instrument across venues.
Fees, shorts, leverage, FX, transfers, trade busts and automatic repair are outside
this exercise. There is no cross-currency portfolio total or public authentication
layer; use a trusted local/internal environment.

SQLite serializes writers database-wide. PostgreSQL serializes trade updates per
symbol. Position capacity is 1e30 units, with absolute cost/PnL balances below 1e70;
exceeding it rolls back the whole trade with 409. See [DESIGN.md](DESIGN.md) for the
transaction protocol and [AI_USAGE.md](AI_USAGE.md) for assistance disclosure.
