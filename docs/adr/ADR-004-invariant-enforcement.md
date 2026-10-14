# ADR-004 — Invariant enforcement: application and storage

**Status:** accepted
**Date:** 2026-10-14

---

## Context

The ledger has several invariants that must never be violated: zero-sum per transaction per currency, minimum two entries per transaction, append-only entries, non-negative customer balances, and entry currency matching account currency. The question is where to enforce them — in the application layer, in the storage layer (Postgres), or both.

This is a real divergence among practitioners. Modern Treasury enforces invariants in the application ("we primarily enforce these constraints in our application code"). Formance enforces them in storage (constraint triggers and checks in Postgres).

## Decision

**Both.** Every invariant that can be expressed in Postgres is enforced there. The application layer adds enforcement for invariants that require cross-table logic or are more naturally expressed in Python.

### Storage layer (Postgres)

| Invariant | Mechanism |
| --- | --- |
| Zero-sum per transaction per currency | `DEFERRABLE INITIALLY DEFERRED` constraint trigger on `entry`, checked at `COMMIT` |
| Minimum 2 entries per transaction | `DEFERRABLE INITIALLY DEFERRED` constraint trigger on `entry` |
| Append-only (no UPDATE/DELETE on entry and ledger_transaction) | `BEFORE UPDATE/DELETE` triggers that raise an exception |
| Non-negative customer balance | `AFTER UPDATE OF balance` trigger on `account`, checks `allow_negative` |
| Unique idempotency key | `UNIQUE` constraint on `ledger_transaction.idempotency_key` |
| No double reversal | `UNIQUE` constraint on `ledger_transaction.reverses_id` |
| Amount always positive | `CHECK (amount > 0)` on `entry` |
| Valid direction | `CHECK (direction IN ('debit', 'credit'))` on `entry` |

### Application layer (Python)

| Invariant | Mechanism |
| --- | --- |
| Lock ordering (prevent deadlocks) | `SELECT ... ORDER BY id FOR UPDATE` before inserting entries |
| Entry currency matches account currency | Validated before insert |
| Customer account scale matches currency | Validated before insert (BRL: 2 decimals, JPY: 0) |

## Alternatives considered

### Application only — rejected

If the application has a bug, invalid data reaches the database and stays there forever (append-only). A constraint trigger is a safety net that costs nothing in the happy path and prevents corruption in the unhappy one.

### Storage only — rejected

Some invariants require cross-row or cross-table logic that is awkward in triggers (e.g., lock ordering, currency scale validation). The application layer is the natural place for these. Also, application-level checks produce better error messages and are easier to test.

## Consequences

**Positive**
- Defense in depth at zero cost: the happy path hits no extra overhead (deferred triggers fire only at commit), and the unhappy path is caught regardless of which code path reached the database.
- Storage-level enforcement survives application bugs, manual SQL sessions, and future code changes.
- Application-level enforcement provides clear error messages and handles logic that doesn't fit in triggers.

**Negative and mitigations**
- Two places to maintain invariant logic. **Mitigated by** keeping the trigger logic minimal (pure SQL checks) and testing both layers.
