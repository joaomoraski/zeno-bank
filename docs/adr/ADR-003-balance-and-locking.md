# ADR-003 — Balance projection, account-row locking, and READ COMMITTED isolation

**Status:** accepted
**Date:** 2026-10-14

---

## Context

The ledger needs to answer two questions on every transaction: "what is this account's current balance?" and "is this debit affordable?" Both must be answered under concurrent load without double-spending or deadlocks. The choices are:

1. How to read the balance — derive it from `SUM(entries)` every time, or maintain a projection.
2. Where to place the lock that serializes concurrent writes to the same account.
3. Which Postgres isolation level to use.

## Decision

- **Entries are the source of truth.** They are append-only and immutable.
- **The `account` row carries a `balance` column**, updated in the same transaction as the entries via an `AFTER INSERT` trigger on `entry`. This is a projection — a cached derived value, not an independent truth.
- **Locking is on the `account` row**, acquired with `SELECT ... FROM account WHERE id = ANY(:ids) ORDER BY id FOR UPDATE` before inserting entries. Ordering by ID prevents deadlocks.
- **Isolation level: READ COMMITTED** (the Postgres default). Explicit row locks provide the serialization needed.
- **Reconciliation** verifies that the projected balance matches `SUM` of entries. If they diverge, the entries win.

## Alternatives considered

### Pure derived balance (SUM on every read, no projection) — rejected for v1

The briefing originally called for this. It is conceptually clean: no mutable state, the balance is always a query.

Rejected because balance reads get slower as entries accumulate. Monzo documented this problem: their P99 for balance reads reached 400–500 ms because "the number of ledger entries to read is always increasing" ("Speeding up our balance read time," 2023). They solved it with pre-computed balance blocks — a form of projection.

A projection on the account row gives O(1) reads from day one. The entries remain the source of truth, and reconciliation catches any drift.

### SERIALIZABLE isolation — rejected

With SERIALIZABLE, Postgres detects conflicts and aborts one transaction with error `40001`, forcing a retry. On a busy account, this means transactions are repeatedly killed and retried instead of queuing up orderly. With explicit `FOR UPDATE` locks in READ COMMITTED, conflicting transactions wait in line — one finishes, the next proceeds. No wasted work.

### Optimistic locking (version column, retry on conflict) — rejected

Works well for low-contention resources like profile edits. On a hot account (many concurrent debits), it becomes a retry storm: every attempt reads, computes, and writes, only to discover someone else committed first. Pessimistic locking with ordering is the standard for ledgers (pgledger, Modern Treasury's "How to Scale a Ledger").

### Lock on a separate `balances` row — not needed

The architecture report (sections 7–9) proposed a separate `balances` table updated in the same transaction. With the balance column directly on `account`, the account row itself serves as the lock point. No extra table needed.

## Consequences

**Positive**
- O(1) balance reads.
- Deadlock-free by construction: locks are always acquired in ascending ID order.
- READ COMMITTED is the Postgres default — no special configuration, no surprise serialization failures.
- Reconciliation is mechanical: `SUM` of entries vs projected balance, global zero-sum check per currency.

**Negative and mitigations**
- The balance column can drift from reality if a bug bypasses the trigger. **Mitigated by** reconciliation, which is both a test and a runtime function.
- Balance reads grow stale under concurrent writes (you see the committed balance, not in-flight changes). **This is correct behavior** — READ COMMITTED guarantees you see only committed data.
