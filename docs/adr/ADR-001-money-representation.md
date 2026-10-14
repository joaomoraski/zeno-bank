# ADR-001 — Monetary value representation

**Status:** accepted
**Date:** 2026-10-13

---

## Context

Every monetary value in the ledger must be stored and summed exactly. The system's central invariant is that the sum of a transaction's entries equals zero per currency. If the number representation is not exact, this invariant fails by a fraction of a cent and the ledger stops balancing.

Cent-level precision is not enough: interest, FX, and proration generate sub-cent values in intermediate calculations.

## Decision

- **Postgres:** `NUMERIC(20,4)`
- **Python:** `decimal.Decimal`
- **Never** `float`, `double`, `REAL`, or the Postgres `money` type
- Currency stored in its own column (`currency CHAR(3)`); display scale comes from the currency
- Input arrives as a string and becomes `Decimal` directly, never passing through `float` at any point

## Alternatives considered

### Binary floating point (`float` / `double` / `REAL`) — rejected

Does not represent exact decimals. This is the only point of consensus across all literature consulted: the PostgreSQL manual states in two separate chapters that *"Floating point numbers should not be used to handle money due to the potential for rounding errors"*, and TigerBeetle, Beancount, Modern Treasury, and Crunchy Data say the same independently.

Demonstration: summing 10,000 entries of R$ 0.01 in `float` yields `100.00000000001425`. The `sum = 0` invariant never passes again.

The error is **intermittent**, which is the worst scenario: `int(float("8.65") * 100)` is correct, `int(float("4.35") * 100)` returns `434`. Passes on the tested value and breaks on the untested one.

### Postgres `money` type — rejected

Fractional precision tied to `lc_monetary`, dump not portable across locales, integer division truncates. Discouraged by the Postgres documentation itself.

### Integer in minor units (`BIGINT` in cents) — rejected for this project

This is the convention of payment APIs (Stripe, Adyen, Square) and is the declared implementation of Modern Treasury (*"we primarily use 64-bit Integers to represent money in our system"*, `bigint` column). Also exact, and therefore correct.

Rejected for two reasons:

1. The scale becomes a nearly irreversible schema decision, and intermediate calculations need to leave integer space anyway.
2. Performance is not a valid argument in this case — see Consequences.

The Formance open-source ledger, which would be the strongest example from the integer camp, actually uses **both layers**: `amount numeric` column in Postgres and `*big.Int` in code, with the scale in the asset itself (`USD/2`). This confirms that the column choice and the in-memory type choice are separate decisions.

## Consequences

**Positive**
- Exactness guaranteed by the type; zero-sum invariant verifiable by SQL without tolerance.
- `(20,4)` covers intermediate calculations for interest, FX, and proration without rounding early.
- Explicit scale per currency allows handling JPY (0 decimals) and KWD (3 decimals) without hardcoded `/100`.
- Reversible decision: if it ever becomes a measured bottleneck, migrating to integer is a single migration.

**Negative and mitigations**
- `Decimal` does not serialize to JSON natively, and the easy way out is `float(x)` — which is exactly the bug. **Mitigation:** serialize as string, and lint prohibiting `float()` on monetary values.
- The Postgres manual notes that calculations in `numeric` are *"very slow compared to the integer types"*. **Did not factor into the decision:** in a ledger the dominant cost is I/O, indexes, and locks, not arithmetic — the difference is nanoseconds vs milliseconds. Monzo's public case confirms this: their 400–500 ms P99 came from reading too many rows, and was resolved with pre-computed blocks, not by changing the type. There is no public benchmark of `bigint` vs `NUMERIC` for ledger aggregation.

**Rounding and residual policy** — mandatory, decided together
- Half-even rounding (banker's rounding) at the currency's scale, only on presentation and settlement.
- Proration residual is **never discarded**: it goes to a system account as an entry. This is what Stripe does when rounding currencies with no decimal places — *"We credit or debit any difference from rounding to the customer balance"*.
- Dividing 100 by 3: floor plus unit-by-unit distribution in ratio order. 5 cents in 70:30 → 4 + 1.

## Notes on what is NOT decided here

- Direction (debit/credit) is **not** the sign of `amount`. It goes in its own column. ISO 20022 (*"No negative amounts exist in ISO 20022"*) and TigerBeetle, which uses four separate counters per account, arrive at this independently. Will go into a separate ADR.
- Where the invariant is enforced — application layer or storage layer — is a real divergence among practitioners (Modern Treasury says application, Formance says storage). Will go into a separate ADR.
