# Trade Recorder & PnL Service

A backend service that ingests trades from multiple venues, tracks positions,
and reports PnL — with idempotency, reconciliation, and oversell protection.

Built with **Python**, **FastAPI**, **SQLAlchemy**, and **SQLite** (swap to
PostgreSQL by changing one config line).

---

## Quick Start

```bash
# 1. Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the server
uvicorn app.main:app --reload

# 4. Run the test suite
pytest -v

# Run with coverage
pytest --cov=app --cov-report=term-missing -v
```

The API will be available at `http://127.0.0.1:8000`.  Interactive docs at
`http://127.0.0.1:8000/docs`.

---

## API Endpoints

### Ingest a Trade
```bash
curl -X POST http://127.0.0.1:8000/trades/ \
  -H "Content-Type: application/json" \
  -d '{
    "venue": "binance",
    "trade_id": "T-1001",
    "symbol": "BTC-USD",
    "side": "BUY",
    "quantity": "10",
    "price": "100",
    "timestamp": "2026-11-03T10:00:00Z"
  }'
```

- **201** — Trade ingested
- **200** — Duplicate (idempotent replay), `"duplicate": true`
- **409** — Oversell rejected
- **422** — Validation error

### Get Positions
```bash
# All positions
curl http://127.0.0.1:8000/positions/

# Single symbol
curl http://127.0.0.1:8000/positions/BTC-USD
```

### Set Latest Price & View PnL
```bash
# Set latest market price
curl -X POST http://127.0.0.1:8000/pnl/set-price \
  -H "Content-Type: application/json" \
  -d '{"symbol": "BTC-USD", "price": "125"}'

# View PnL (both average-cost and FIFO)
curl http://127.0.0.1:8000/pnl/

# Filter by symbol
curl "http://127.0.0.1:8000/pnl/?symbol=BTC-USD"
```

### Reconciliation
```bash
curl -X POST http://127.0.0.1:8000/reconciliation/ \
  -H "Content-Type: application/json" \
  -d '{
    "venue": "binance",
    "trades": [
      {"trade_id": "T-1001", "symbol": "BTC-USD", "side": "BUY",
       "quantity": "10", "price": "100", "timestamp": "2026-11-03T10:00:00Z"},
      {"trade_id": "T-1002", "symbol": "BTC-USD", "side": "BUY",
       "quantity": "6", "price": "101", "timestamp": "2026-11-03T11:00:00Z"},
      {"trade_id": "T-1003", "symbol": "BTC-USD", "side": "BUY",
       "quantity": "1", "price": "99", "timestamp": "2026-11-03T12:00:00Z"}
    ]
  }'
```

Returns `missing`, `extra`, and `mismatched` trade lists.

---

## Features

| Feature | Description |
|---------|-------------|
| **Idempotency** | Same `(venue, trade_id)` sent twice → counted once. UNIQUE constraint + IntegrityError catch handles concurrent duplicates. |
| **Oversell Protection** | SELL that would take position below zero is rejected (409) with clear error. Position unchanged. |
| **Dual Cost-Basis** | Both average-cost and FIFO PnL computed simultaneously. |
| **Reconciliation** | Compare stored trades against venue statement. Reports missing, extra, and mismatched trades with field-level diffs. |
| **Decimal Precision** | All money uses `decimal.Decimal`, never `float`. |
| **Validation** | Positive qty/price, BUY/SELL only, timezone-aware timestamps. |

---

## Project Structure

```
├── app/
│   ├── main.py            # FastAPI app entry point
│   ├── config.py           # Configuration (DATABASE_URL, etc.)
│   ├── database.py         # Engine, session, Base
│   ├── models.py           # SQLAlchemy ORM models
│   ├── schemas.py          # Pydantic request/response models
│   ├── routers/
│   │   ├── trades.py       # POST /trades/
│   │   ├── positions.py    # GET /positions/
│   │   ├── pnl.py          # GET /pnl/, POST /pnl/set-price
│   │   └── reconciliation.py
│   └── services/
│       ├── trade_service.py       # Ingestion, idempotency, oversell
│       ├── position_service.py    # Position queries
│       ├── pnl_service.py         # PnL computation
│       └── reconciliation_service.py
├── tests/                  # 80+ test cases
│   ├── conftest.py         # Fixtures (in-memory DB per test)
│   ├── test_trade_ingestion.py
│   ├── test_idempotency.py
│   ├── test_oversell.py
│   ├── test_positions.py
│   ├── test_pnl.py
│   ├── test_reconciliation.py
│   └── test_edge_cases.py
├── DESIGN.md               # Architecture & design decisions
├── AI_USAGE.md             # AI tool usage log
├── README.md               # This file
└── requirements.txt
```

---

## Switching to PostgreSQL

```bash
export DATABASE_URL="postgresql://user:pass@localhost:5432/trades"
```

No code changes needed — SQLAlchemy abstracts the database layer.

---

## Design Document

See [DESIGN.md](DESIGN.md) for architecture decisions, cost-basis
calculations, data model details, and trade-offs.

