# AI usage disclosure

The initial repository's log attributed its generated application and tests to
Antigravity / Claude Opus 4.6. The following work was subsequently performed with
substantial OpenAI Codex assistance in ChatGPT.

## Audit and fixes

Codex retrieved the repository, ran the original suite and wrote independent HTTP
tests against the documented API. Those tests reproduced lost concurrent updates,
oversells, first-position duplicate races, reconciliation value/timestamp errors,
and insufficient numeric precision. Passing sequential tests and high line
coverage had not established concurrency correctness.

Codex then generated and reviewed changes to the transaction protocol, Decimal
storage/arithmetic, shared validation, mark upserts, reporting snapshots and
reconciliation. It added explicit PostgreSQL migration support, preserved the
existing API paths and both cost-basis methods, and documented numeric limits,
late-event semantics, missing-price behavior and upgrade requirements.

The original tests were retained. Added tests include the prior external audit,
new concurrent price/SELL cases, injected-failure rollback, exact rational
accounting checks, request/statement boundaries and PostgreSQL migration checks.
Tests were actually executed on SQLite with one and three workers and PostgreSQL
with three workers. See TEST_RESULTS.md for the final recorded results. Official
PostgreSQL and SQLAlchemy documentation was consulted for transaction behavior.

## Ownership and limits

AI generated substantial code, tests and documentation; this must not be
represented as entirely handwritten or independently verified by the candidate.
Code and tests created together can share mistakes. Independent arithmetic and
real database/network concurrency tests reduce that risk but do not prove all
inputs or schedules correct. The candidate should review the code, run the tests
on their own installation and explain the locks, transaction/commit boundary,
precision policy, reconciliation scope and migration before submitting.

The ephemeral development environment required a test-only PostgreSQL startup
accommodation for its single Unix UID and isolated command networking. It changed
no SQL behavior and is not included as an application dependency or recommended
local setup. No production data, proprietary systems or actual exchange accounts
were accessed. Historical corruption cannot be repaired without authoritative
venue records; the code does not pretend otherwise.
