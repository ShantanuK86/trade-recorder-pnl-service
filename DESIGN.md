# Design and correctness

## Scope and components

FastAPI validates requests. SQLAlchemy services own each transaction; the trade
ledger, average-cost position and FIFO lots commit together. SQLite is the
zero-service local default; PostgreSQL is the preferred multi-writer deployment.
Both implementations are exercised by real HTTP concurrency tests.

`trades` has a database unique key on `(venue, trade_id)`. Positions aggregate by
symbol across venues. FIFO lots reference their originating trade. Latest prices
are upserted by symbol. Canonical symbols must refer to the same instrument; this
is portfolio inventory, not per-venue wallet availability.

## Ingestion transaction

Every ingestion receives a fresh session and owns its full write transaction.

1. SQLite executes `BEGIN IMMEDIATE` before any identity or inventory read. This
   reserves the database writer, including across threads and processes. Waiting
   requests read fresh state only after the previous writer commits or rolls back.
2. Insert the ledger row with `ON CONFLICT (venue, trade_id) DO NOTHING RETURNING id`.
   The unique index arbitrates simultaneous duplicates, rather than a check-then-
   insert query. PostgreSQL may wait for an in-flight identical key to resolve.
3. If the key already exists, compare all payload fields against the stored row.
   Exact numeric values and equivalent UTC instants are equal. Return a duplicate
   success only for equal payloads; otherwise raise a 409 identity conflict.
   This happens before oversell validation, so retrying a successful closing SELL
   after the position becomes zero remains successful.
4. For a new trade, ensure its position exists using a conflict-safe insert.
   PostgreSQL then reads that row with `SELECT ... FOR UPDATE`. The lock protects
   the balance read, oversell check, average-cost update and FIFO mutations until
   commit. Locking a nonexistent row without first creating it would not suffice.
5. Reject quantity above available inventory. Otherwise apply accounting, check
   numeric capacity and flush/commit every change before returning success.

SQLite needs its explicit early write reservation: merely wrapping stale reads
and later writes in a transaction does not prevent lost updates. PostgreSQL uses
READ COMMITTED, whose follow-up query sees the committed duplicate winner and
whose locked position read serializes competing writers. All ingestions acquire
one trade identity before one position, avoiding cross-symbol lock cycles in the
current single-trade API. Different PostgreSQL symbols can proceed independently.
Future multi-symbol operations need a deterministic lock order.

Any exception rolls back the ledger insertion, position/FIFO changes and newly
created empty position. A rejected key is not reserved and can be retried after
funding inventory. Database connect/lock/statement failures return 503 without
exposing credentials; callers retry the same identity and payload. A lost commit
acknowledgement has an ambiguous outcome, resolved by that idempotent retry. The
service guarantees a single durable effect for an accepted identity, not exactly-
once network delivery. SQLite waits up to 30 seconds for the writer; PostgreSQL
connections/locks wait 5 seconds and statements 15 seconds.

## Average cost and FIFO

Let Q be quantity, C remaining average-cost basis and R cumulative realized PnL.

- BUY q @ p: Q += q; C += q*p; R unchanged.
- SELL q @ p: require q <= Q. Allocate A = C*q/Q; Q -= q; C -= A;
  R += q*p - A. A partial allocation rounds half-even to 36 fractional places.
- A full close removes **exactly C**, leaving zero basis and no rounding dust.
- Average cost is derived as C/Q, or zero if closed. It is never stored as a
  rounded price used to calculate later allocations.
- At mark m, unrealized PnL = Q*m - C. Mark updates never change realized PnL.

FIFO consumes available lots ordered by source timestamp then database lot ID.
Each consumed quantity realizes q*(sale price - lot price). Remaining FIFO basis
is the sum of remaining lot quantities times buy prices. A mismatch between FIFO
availability and the position aborts the whole trade with a clear conflict.

Both methods are always returned. The short PnL aliases use average cost. The
API keeps the original zero-unrealized-without-mark convention and returns a null
latest_price; clients must check that field. A full close/reopen starts a fresh
basis and retains cumulative realized PnL. The provided worked example yields
average realized/unrealized 75/300 and FIFO 100/275 at mark 125.

## Precision and validation

Money never travels through application float arithmetic. Requests accept decimal
strings or exact integers; fractional JSON numbers and booleans are rejected before
Decimal conversion. Finite amounts must be positive, below 1e24 and have at most
18 fractional places, ignoring trailing zeroes. Scientific notation is accepted
within those limits. Text length is capped at 120 characters.

All arithmetic/reporting uses a **local** 160-significant-digit Decimal context,
ROUND_HALF_EVEN. It neither relies on nor mutates the ambient context. Products of
supported inputs have at most 36 fractional places. SQLite stores exact decimal
TEXT; PostgreSQL stores NUMERIC(120,36), leaving sufficient room for products and
supported aggregates. Partial average-cost allocation is the only persisted
rounding step. Output formats Decimals as canonical strings; recurring averages
are reported at the local context precision. Quantity is capped at 1e30, and basis
and absolute realized PnL at below 1e70; exceeding capacity aborts the transaction.

PostgreSQL checks backstop positive amounts, side, nonnegative inventory/basis,
closed-position basis and FIFO remaining quantities. SQLite uses the same service
validation/capacity rules under its write transaction; exact TEXT values are not
compared with SQL numeric operators or converted to floating point. FIFO queries
filter nonpositive quantities with Python Decimal comparisons instead.

Trade and external-statement fields share validation. Identifiers are nonempty,
trimmed and bounded, and reject control characters/invalid Unicode. Venues become
lowercase, symbols uppercase, IDs stay case-sensitive. Aware ISO timestamps are
normalized to UTC. Nonzero sub-microsecond precision, numeric epoch tokens and UTC
conversion overflow are rejected instead of silently changing a retry's identity.
Unknown request fields and duplicate statement IDs are rejected.

## Prices and reporting consistency

Latest prices use an atomic upsert, including first-write races. Manual mark updates
use last-writer-wins semantics; no external quote timestamp is available to reject
stale source updates. PnL fetches positions, prices and lots in **one joined SQL
statement**, so all fields come from a consistent committed statement snapshot.
Separate requests may observe different committed instants. No rounded displayed
average is fed back into accounting.

## Reconciliation

One SQL statement reads stored trades for the selected venue. Join identities by
trade_id within that venue, then compare symbol, side, Decimal quantity/price and
UTC timestamp values directly. Convert to strings only when reporting differences.

- Missing: external identity absent from stored records in scope.
- Extra: stored identity absent from the external statement in scope.
- Mismatched: shared identity with any different compared field.

Default scope is the complete venue history across symbols. Optional start/end
must both be provided and define a start-inclusive, end-exclusive window. Statement
rows must belong to the same venue and window. Input is capped at 10,000 rows.
A complete statement is required for the selected scope; a partial page cannot
prove which stored trades are extra. Output ordering is deterministic. Equivalent
decimal spellings and timezone offsets are not mismatches. A timestamp discrepancy
that crosses a window boundary can appear as missing/extra; broaden the window to
investigate it. The operation is read-only and never automatically repairs records.
The implementation loads the scoped rows into memory; very large histories should
use staged or streaming reconciliation and a settled ingestion cutoff.

## Database initialization and upgrade

Workers can initialize tables concurrently: SQLite uses an early write transaction;
PostgreSQL uses a transaction-scoped advisory lock for schema initialization only.
Runtime trade serialization uses position row locks, not a global PostgreSQL lock.

The legacy PostgreSQL NUMERIC(38,18) schema cannot preserve 36-place cost products.
`python -m app.migrate` explicitly widens existing money columns and adds missing
checks in one transaction, preserving records. Stop writers and back up first.
Startup refuses the old numeric schema. The migration is repeatable and tested
with existing rows and old-width columns. SQLite decimal TEXT needs no type change.
A migration cannot recover prior rounding or races; historical discrepancies
require original venue data and an audited repair/rebuild, not invented balances.

## Limits and future work

This is long-only spot accounting without fees, funding, shorts, leverage, FX,
transfers, accounts or trade busts. Average cost follows serialized ingestion order;
late trades do not replay history. FIFO orders currently available lots by their
source time. Database identity IDs are **not** a guaranteed accounting/commit order;
a reliable rebuild feature would need explicit per-symbol sequencing before it
can safely replay concurrent history. No such rebuild endpoint is claimed here.

There is no public authentication, rate limiting, durable outbox, failover testing
or load benchmark. Use a trusted local/internal deployment. Measure throughput
before adding infrastructure; SQLite deliberately has one writer at a time. The
root test suite, standalone multi-worker HTTP suite and explicit PostgreSQL
migration test cover the reported defects without Docker or application mocks.

References: PostgreSQL explicit locking and INSERT ON CONFLICT documentation;
SQLAlchemy transaction management and SQLite transaction-control documentation.
