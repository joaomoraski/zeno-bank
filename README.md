# Zeno Bank

A multi-currency double-entry ledger inspired by [Wise](https://wise.com), built for correctness under concurrency.

Zeno is a fictional digital bank where each customer holds balances in multiple currencies, backed by an append-only, double-entry ledger. The focus is on **provable invariants**: every design choice has a test that proves it works, including under concurrent load with real threads.

## What it proves

| Invariant | How it's enforced | Test |
|---|---|---|
| Every transaction sums to zero per currency | `DEFERRABLE INITIALLY DEFERRED` constraint trigger | Unbalanced transaction rejected; cross-currency (BRL vs USD) rejected |
| Entries are append-only | `BEFORE UPDATE/DELETE` triggers on `entry` and `ledger_transaction` | UPDATE and DELETE both raise an error |
| Balance is a projection, not the truth | `balance` column on `account`, updated via trigger; reconciliation verifies against `SUM(entries)` | Deposits accumulate correctly; reconciliation passes after mixed operations |
| Customer balance never goes negative | `AFTER UPDATE` trigger on `account.balance` | Overdraft rejected; 100 concurrent debits of R$80 on R$100 → exactly 1 success, final balance R$20 |
| No deadlocks on bidirectional transfers | `SELECT ... FOR UPDATE ORDER BY id` — consistent lock ordering | 50 A→B + 50 B→A simultaneous transfers → zero deadlocks, sum unchanged |
| Idempotency | `UNIQUE` constraint on `idempotency_key` | Same key sent 10× in parallel → 1 transaction |
| No double reversal | `UNIQUE` constraint on `reverses_id` | Second reversal of the same transaction rejected |

## Architecture decisions

Decisions are documented as ADRs in [`docs/adr/`](docs/adr/):

- **ADR-001** — `NUMERIC(20,4)` and `Decimal`, never `float`. Binary floating point can't represent exact decimals; `sum = 0` would never pass.
- **ADR-002** — Direction in its own column (`debit`/`credit`), amount always positive. Follows ISO 20022 and TigerBeetle.
- **ADR-003** — Balance as projection (not pure `SUM`), pessimistic locking on account rows in ID order, READ COMMITTED. SERIALIZABLE aborts transactions instead of queuing; optimistic locking causes retry storms on hot accounts.
- **ADR-004** — Invariants enforced in both application and storage (defense in depth).
- **ADR-005** — Modular monolith. The ledger's atomic invariant can't cross a network boundary.

## Data model

```
account                          ledger_transaction              entry
─────────────────                ─────────────────────           ─────────────────
id          uuid PK              id               uuid PK       id             bigint PK
code        text UNIQUE          idempotency_key  text UNIQUE   transaction_id uuid FK
currency    char(3)              description      text          account_id     uuid FK
normal_balance text               reverses_id     uuid FK UQ    direction      text
allow_negative boolean           created_at       timestamptz   amount         numeric(20,4)
balance     numeric(20,4)                                       currency       char(3)
created_at  timestamptz                                         created_at     timestamptz
```

- **`account`** — a money bucket in one currency. Customer wallets are credit-normal (bank liability). System accounts (`external:deposits:BRL`) are debit-normal with `allow_negative = true`.
- **`ledger_transaction`** — groups entries atomically. The `idempotency_key` prevents double-posting. The `reverses_id` links reversals and prevents double-reversal.
- **`entry`** — one leg of a transaction. Direction is explicit, amount is always positive. Append-only — corrections are reversals.

## How money moves

**Deposit R$ 500:**

| Account | Direction | Amount | Currency |
|---|---|---|---|
| `external:deposits:BRL` | debit | 500.00 | BRL |
| Customer wallet | credit | 500.00 | BRL |

**Transfer R$ 100, Alice → Bob:**

| Account | Direction | Amount | Currency |
|---|---|---|---|
| Alice BRL wallet | debit | 100.00 | BRL |
| Bob BRL wallet | credit | 100.00 | BRL |

Every transaction balances to zero per currency. The global sum across all accounts is always zero. Reconciliation verifies this.

## Known simplifications

- In Wise Brazil, the BRL balance, foreign currency balances, and FX operations are in three separate legal entities. Here it's one.
- A "Wise-like" in Brazil would be a payment institution with prepaid accounts, not a bank. Customer balances can't go negative because prepaid accounts can't extend credit ([Lei 12.865/2013](https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2013/lei/l12865.htm)).
- No authentication, no HTTP API, no frontend in v1. The ledger is a pure domain module tested directly against Postgres.

## Running

Prerequisites: Docker, Python 3.13+, [uv](https://docs.astral.sh/uv/)

```bash
# Start Postgres
docker compose up -d --wait

# Install dependencies and run tests
uv sync --dev
uv run pytest -v
```

## Roadmap

- [x] **Ledger (v1)** — double-entry, append-only, concurrency-safe, with reconciliation
- [ ] Accounts — customer, per-currency balances
- [ ] Transfers — HTTP API with idempotency
- [ ] Outbox + worker — reliable async events
- [ ] FX — rate snapshots, quotes with expiry, multi-currency conversion
- [ ] Investments — orders, positions, portfolio valuation

## References

The design draws from: [Modern Treasury — Accounting for Developers](https://www.moderntreasury.com/journal/accounting-for-developers-part-i) · [pgledger](https://pgrs.net/2025/03/24/pgledger-ledger-implementation-in-postgresql/) · [Monzo — How we calculate balances](https://monzo.com/blog/2022/02/18/how-we-calculate-balances) · [Stripe — Idempotency](https://stripe.com/blog/idempotency) · [TigerBeetle — Data modeling](https://docs.tigerbeetle.com/coding/data-modeling/) · [PostgreSQL — Explicit Locking](https://www.postgresql.org/docs/current/explicit-locking.html). Full list in [`docs/research/digital-banking-architecture.md`](docs/research/digital-banking-architecture.md).
