# The Ledger

## What is double-entry and why it matters here

Every time money moves, the ledger records at least two entries that sum to zero per currency. Money is never created or destroyed — it is moved between accounts. A deposit of R$ 100 debits `external:deposits:BRL` (where outside money comes from) and credits the customer's BRL wallet. The global sum across all accounts, per currency, is always zero. If it isn't, something is broken, and a single SQL query finds it.

The customer's wallet is a **credit-normal** account — it represents a **liability** of the bank. The bank owes the customer that money. In accounting, increasing a liability means crediting it; decreasing it means debiting. So: money arriving in the wallet = credit (liability goes up); money leaving = debit (liability goes down). This is counterintuitive at first — "credit" feels like it should mean "add" — but once you internalize that the ledger is written from the bank's perspective, every entry follows logically. This matches how real banks model customer balances (Modern Treasury, Accounting for Developers Part II; Fowler, Account pattern).

The zero-sum check is **per currency**: the invariant query groups by `(transaction_id, currency)`. A debit of R$ 100 BRL against a credit of US$ 100 USD is rejected — BRL has an unmatched debit (-100) and USD has an unmatched credit (+100). Currencies are never mixed. A valid FX conversion requires two balanced legs, each zeroing out in its own currency (e.g., debit customer BRL + credit FX position BRL; debit FX position USD + credit customer USD).

**Why not single-entry** (one row per movement, signed amount): nothing prevents a credit without a matching debit. Money can appear from nowhere and only manual inspection catches it. Double-entry makes that structurally impossible.

## Why the balance is a projection, not just a SUM

Entries are append-only and immutable — they are the source of truth. The `account` row carries a `balance` column that is updated in the same transaction as the entries. This is a **projection**: a cached value derived from the entries, not an independent truth.

Why not compute `SUM(entries)` on every read: it gets slower as entries accumulate. Monzo hit 400–500 ms P99 on balance reads because the number of entries keeps growing (Monzo, "Speeding up our balance read time," 2023). A projection gives O(1) reads.

Why not make the balance column the source of truth (no entries): you lose the audit trail, corrections become `UPDATE` instead of reversal, and when two numbers disagree you don't know which one is right. With entries as truth and balance as projection, reconciliation is mechanical: recompute from entries, compare, flag any divergence.

**Reconciliation** runs as a test and a function: the global sum per currency is zero, and each account's projected balance matches `SUM` of its entries.

## Reversals

Corrections in an append-only ledger are never `UPDATE` or `DELETE`. A reversal is a new transaction with entries that mirror the original but with inverted directions (debit becomes credit, credit becomes debit). The original stays in the ledger forever. The `reverses_id` column on `ledger_transaction` links the reversal to the original, providing a full audit trail.

**Preventing double reversal:** a `UNIQUE` constraint on `reverses_id` ensures only one reversal per original transaction. Without this, reversing the same transaction twice would credit the sender twice — a business logic bug that the zero-sum invariant wouldn't catch, since each reversal is individually balanced.

## Idempotency

Every transaction carries a unique `idempotency_key`. The same request arriving twice produces one transaction — the second attempt returns the original result.

**What happens with concurrent duplicates:** two requests with the same key arrive simultaneously. Both try to insert the `ledger_transaction` row. The `UNIQUE` constraint on `idempotency_key` means one wins and the other blocks until the first commits. After the commit, the second sees the existing row and returns the original result. No double-posting.

This follows the pattern described by Stripe ("Designing robust and predictable APIs with idempotency," 2017) and the IETF Idempotency-Key draft.

## Isolation level, locking, and why double-spending can't happen

**Isolation level: READ COMMITTED** (Postgres default).

**Why not SERIALIZABLE:** with SERIALIZABLE, Postgres detects conflicts and aborts one of the transactions with error `40001`, forcing a retry. On a busy account, this means transactions keep getting killed and retried instead of queuing up. With explicit `FOR UPDATE` locks in READ COMMITTED, conflicting transactions *wait* in line — one finishes, the next goes. No aborts, no wasted work. You control exactly which rows serialize and in what order, instead of letting Postgres decide what conflicts.

**Locking strategy:** before inserting entries, the transaction locks the involved **account rows** (not the entries — entries are inserts, not rows to lock) with `SELECT ... FROM account WHERE id = ANY(:ids) ORDER BY id FOR UPDATE`. Ordering by ID prevents deadlocks: without ordering, transfer A→B locks A then waits for B, while transfer B→A locks B then waits for A — a cycle where neither can proceed (deadlock). With ordering, both lock the lower ID first, so one waits and the other proceeds. No cycle, no deadlock (PostgreSQL docs, "Explicit Locking"; pgledger uses this exact approach).

**Why double-spending can't happen:** with the account row locked, the transaction reads the projected balance, checks if the debit is affordable, updates the balance, and inserts the entries — all while holding the lock. A second concurrent debit on the same account waits for the lock, then sees the updated balance. 100 simultaneous debits of R$ 80 on an account with R$ 100: exactly 1 succeeds, final balance R$ 20.

**Why not optimistic locking** (version column, retry on conflict): works well for low-contention resources like profile edits, but on a hot account it becomes a retry storm. Pessimistic locking with ordering is the standard for ledgers (pgledger, Modern Treasury's "How to Scale a Ledger").

## How external money enters

System accounts represent the outside world, one per currency: `external:deposits:BRL`, `external:deposits:USD`, `external:fees:BRL`. These accounts have `allow_negative = true` because they **start at zero and every deposit debits them** — after 10 deposits of R$ 500, `external:deposits:BRL` sits at -5,000. If `allow_negative` were false, the very first deposit would be rejected. Their negative balance means "this much money has entered the system from outside." We don't track the external world's actual balance; the system account is just our side of the boundary.

A deposit of R$ 500: debit `external:deposits:BRL`, credit customer wallet BRL. The customer's balance goes up by 500, the system account goes down by 500, the global sum stays zero.

Fees work the same way: debit the customer wallet, credit `external:fees:BRL`. Every movement has a counterpart. The global zero-sum invariant holds across all operations.

## How a new currency fits in

"Open a dollar account" = create a new `account` row with `currency = 'USD'`. No schema change, no migration. The entry table already carries a `currency` column validated against the account's currency. System accounts for the new currency (`external:deposits:USD`) are created the same way.

This follows the model used by Wise (one balance per currency per profile) and TigerBeetle (one ledger per currency, accounts can only transact within the same ledger).

**Currency scale validation:** `NUMERIC(20,4)` stores all currencies, but the number of meaningful decimal places differs (BRL: 2, JPY: 0, KWD: 3). The application validates that customer account entries use the correct scale for their currency — ¥100.50 doesn't exist in the real world. The 4 decimal places are available for system accounts and intermediate calculations (interest, proration). The zero-sum invariant won't catch a scale violation because the math still balances.

---

**Sources:** Modern Treasury, Accounting for Developers I–III · Fowler, Account and Accounting Transaction patterns · pgledger (SQL + blog post) · PostgreSQL docs: Transaction Isolation, Explicit Locking · Stripe, Idempotency (2017) · Monzo, How we calculate balances (2022) and Speeding up balance read time (2023) · TigerBeetle, data modeling and Account reference · Starling, QCon 2020 ("the ledger always adds up to zero") · IETF draft-ietf-httpapi-idempotency-key-header-07. Full links in `docs/research/digital-banking-architecture.md`.
