# AI Usage Log

This document describes how AI tools were used in the development of this
project, following the assignment's requirement for transparency.

## Tools Used

- **Antigravity (Claude Opus 4.6)** — AI coding assistant used for pair-programming.

## What AI Was Used For

### 1. Architecture & Design Planning
AI assisted in thinking through the architecture: the choice of a
denormalised `positions` table for atomic oversell checks, the
belt-and-suspenders idempotency approach (check-then-insert + UNIQUE
constraint), and implementing both average-cost and FIFO cost-basis
methods simultaneously.

### 2. Code Generation
The application code (models, services, routers, schemas) and the test
suite were generated with AI assistance.  Every generated piece was
reviewed for correctness — especially the financial calculations, which
were verified against the worked examples in the spec.

### 3. Test Case Design
AI helped enumerate boundary conditions and edge cases that a trading
firm interviewer would expect:
- Oversell at exact position boundary
- Decimal precision (0.1 + 0.1 + 0.1 ≠ 0.3 with floats)
- Idempotent replay after a SELL
- Cross-venue / cross-symbol independence
- Fractional quantities and large values

### 4. Documentation
AI assisted in writing DESIGN.md (this architecture document), README.md,
and this AI_USAGE.md file.

## What AI Was NOT Used For

- **Domain understanding**: The developer understood positions, PnL, FIFO
  vs average cost, and reconciliation concepts from prior experience.
- **Design decisions**: Choices like "use Decimal, never float" and "atomic
  oversell via transaction" reflect engineering judgement, not AI output.
- **Review & validation**: All financial calculations were manually verified
  against the worked examples in the spec.

## Verification Steps Taken

1. Ran the full test suite (`pytest -v`) and verified all tests pass.
2. Manually verified the worked example (BUY 10@100, BUY 10@110, SELL 5@120,
   latest 125) against both cost-basis methods.
3. Confirmed idempotency by reviewing the UNIQUE constraint and the
   IntegrityError catch path.
4. Confirmed oversell atomicity by reviewing the transaction boundaries.

