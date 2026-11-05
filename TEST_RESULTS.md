# Executed validation after the fixes

Date: 5 November 2026. Base repository commit:
`955d6f5d52783bbc78eb9a835bf3bc2aac2e4fbb`.

These results are from the corrected working tree, not the unmodified base commit.
The final published commit includes this report, the fixes and all regression tests.
No original test assertions were removed or weakened.

## Final results

| Suite | Configuration | Result |
|---|---|---|
| Original tests plus new regressions | In-memory SQLite API fixtures | **142 passed**, 0 failures, 0 errors, 0 skips |
| Independent HTTP audit | File SQLite, 1 Uvicorn worker | **52 passed**, 0 failures, 0 errors, 0 skips |
| Independent HTTP audit | File SQLite, 3 Uvicorn workers | **52 passed**, 0 failures, 0 errors, 0 skips |
| Independent HTTP audit | PostgreSQL 16.2, 3 Uvicorn workers | **52 passed**, 0 failures, 0 errors, 0 skips |
| Existing-schema migration regression | Real PostgreSQL, isolated schema | **1 passed**, 0 failures, 0 errors, 0 skips |
| Ruff lint and formatting, changed app/new tests | Repository configuration | Passed |
| Dependency installation/check | Updated requirements | Passed |

This is **299 test executions**, including the same 52 live cases on three
configurations. The default suite comprises the original 97 cases plus 45 added
regressions; these counts should not be described as 299 distinct cases.

Recorded durations: default suite 12.27s; SQLite/1 worker 2.75s;
SQLite/3 workers 11.64s; PostgreSQL/3 workers 11.03s; migration 0.59s.
The native coverage run reports approximately **90% line coverage**. This coverage
measurement is for the SQLite default suite only; it does not combine the separately
executed PostgreSQL/migration/live-worker runs. The one native-suite warning is a
Starlette deprecation notice about its HTTPX TestClient transport.

## Previously failing scenarios now passing

- Simultaneous identical first fills: one creation, the remaining duplicate
  successes, one ledger effect and no unhandled position-creation race.
- Twelve distinct concurrent BUYs after one seed unit: ledger and position both
  contain thirteen units, without lost updates.
- Competing seven-unit SELLs against ten units: one accepted sale, all others
  rejected; ledger net and position agree at three units.
- Concurrent duplicate full-position SELLs: one accepted sale, every retry succeeds
  as a duplicate even after the balance is zero.
- Rejected duplicate SELLs followed by a top-up: the rejected identity remains
  available and is applied once when inventory becomes sufficient.
- Concurrent legal SELLs preserve both average-cost and FIFO accounting.
- Concurrent first price updates succeed through an atomic upsert.
- Changed payloads on existing keys return explicit conflicts; equivalent decimal
  spellings and timezone offsets remain successful duplicates.
- `10`, `10.0` and `1e1` reconcile as equal; timestamp-only changes are reported.
- The T1/T2/T3 reconciliation example reports only the true quantity mismatch and
  missing trade, including when PostgreSQL returns fixed-scale Decimals.
- BUY `1e-18` @ `1e-18` retains basis `1e-36`; larger accepted products keep their
  significant digits instead of using the ambient 28-digit Decimal context.
- Repeated partial sales with a recurring average close with exactly zero basis.
- A failure injected after updating an existing position rolls back the ledger,
  position and FIFO changes. Retrying the same failed identity then succeeds.
- Capacity failures and inconsistent legacy FIFO inventory abort the whole sale
  or purchase without retaining a partial trade.
- Statement duplicates, invalid amounts/sides/times, mixed venues, invalid windows
  and excess rows are rejected. Window endpoints are start-inclusive/end-exclusive.
- Four seeded 35-operation accounting sequences are checked against independent
  exact rational arithmetic and cash conservation, without reusing service math.

## PostgreSQL upgrade verification

The migration test creates an isolated schema, narrows its money columns to the
original NUMERIC(38,18) shape, removes the new checks and inserts an existing trade.
It verifies that startup refuses that old numeric schema. It then migrates twice,
checks the existing trade/position and idempotent retry, verifies newly installed
checks, and confirms a new tiny trade retains its 36-place product. The schema is
removed afterward; no real user database is altered by that test.

This migration widens storage and adds constraints. It cannot recover numbers or
positions already corrupted by earlier code. Stop writers, back up, migrate and
reconcile against authoritative venue records before resuming an existing database.

## Reproduce

Install `requirements.txt`, then:

```bash
python -m pytest -q
ruff check app tests/test_regressions.py tests_live tests_postgres
ruff format --check app tests/test_regressions.py tests_live tests_postgres
python -m pip check
```

With a separate live server and dedicated test database:

```bash
export BASE_URL='http://127.0.0.1:8000'
python -m pytest tests_live -q
```

Run the server with one and three workers, using SQLite and then PostgreSQL.
The live suite uses real independent HTTP clients and no application imports/SQL.
It contains the original 48 audit cases plus four added concurrent price/SELL cases.
For the separate migration test:

```bash
export TEST_DATABASE_URL='postgresql+psycopg://localhost/trade_recorder_test'
python -m pytest tests_postgres -q
```

Python 3.12.14; FastAPI 0.162.0; SQLAlchemy 2.1.8; Pydantic 2.14.3;
Psycopg 3.3.8; HTTPX 0.28.1; pytest 9.2.0; Ruff 0.17.3; PostgreSQL 16.2.
A separate virtual environment was used for this repository. The temporary
PostgreSQL startup harness needed by the development environment is outside the
repository and changes no SQL behavior; see AI_USAGE.md.

The migration test uses a fresh service-owned write session, as the real API does.
No assertions were suppressed or marked as expected failures to obtain these results.
No failover, power-loss, lost-COMMIT-packet or sustained-load test was performed.
These finite tests establish the listed cases, not a proof of all possible inputs
or schedules.
