# ADR-002 — Direction column vs signed value

**Status:** accepted
**Date:** 2026-10-13

---

## Context

ADR-001 decided that monetary values use `NUMERIC(20,4)` with `amount > 0`, but left the representation of debit/credit direction to a separate decision. The briefing's invariant query (`SELECT sum(amount) ... GROUP BY transaction_id, currency → 0`) presupposes signed values, while ADR-001's notes state that direction should live in its own column.

Both approaches exist in production systems. The choice affects how the zero-sum invariant is expressed, how entries are queried, and how the audit trail reads.

## Decision

- Direction lives in a separate column: `direction TEXT CHECK (direction IN ('debit', 'credit'))`
- `amount` is always positive: `CHECK (amount > 0)`
- The zero-sum invariant query uses a `CASE` expression:

```sql
SELECT transaction_id, currency
FROM entry
GROUP BY transaction_id, currency
HAVING sum(CASE direction WHEN 'debit' THEN amount ELSE -amount END) <> 0;
-- must return zero rows
```

## Alternatives considered

### Signed value (amount carries the sign) — rejected

Used by Square's Books system, where debits are positive and credits are negative. The invariant simplifies to `sum(amount) = 0`.

Rejected because:

1. A negative amount is ambiguous — is it a credit, or is it a correction? The direction column makes intent explicit.
2. ISO 20022 states *"No negative amounts exist in ISO 20022"* and uses separate debit/credit indicators. TigerBeetle uses four separate counters per account (debits/credits × posted/pending). Modern Treasury also separates direction from amount.
3. Queries that filter "all debits on this account" become `WHERE amount > 0` instead of `WHERE direction = 'debit'`, which is less readable and breaks if conventions change.

## Consequences

**Positive**
- Every entry is unambiguous: direction + positive amount = no interpretation needed.
- Audit trail is clearer — a reversal is visible as flipped direction, not a sign change.
- Aligns with the majority of ledger implementations and with ISO 20022.

**Negative and mitigations**
- The zero-sum query is slightly more complex (`CASE` expression instead of bare `sum`). This is a one-line difference and is enforced by a constraint trigger, so application code rarely writes it directly.
