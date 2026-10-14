# CLAUDE.md — fictional bank

Personal study and portfolio project by João: a fictional digital bank in the style of Wise (multi-currency balances). Not a real bank. **v1 is just the `ledger` module.**

## Read before any work

Precedence in case of conflict:

1. `docs/adr/` — accepted decisions (ADR-001: money representation)
2. `docs/briefing.md` — v1 scope, module order, ledger rules, workflow
3. `docs/PLAN.md` — consolidated plan and **open conflicts (C1–C9)**
4. `docs/reference/architecture-report-2026-09-30.md` — long-term vision; parts are superseded (see its header)
5. `docs/research/digital-banking-architecture.md` — study material with links; does not make decisions on its own

Do not modify `docs/reference/` or `docs/research/` without an explicit request. If you find a new conflict between documents, **stop and ask**; do not resolve it on your own.

## Ledger rules (non-negotiable)

- Double-entry: every transaction has >= 2 entries and sums to zero **per currency**. Never add BRL and USD together.
- Append-only: never `UPDATE`/`DELETE` on entries or transactions. Correction is a reversal transaction.
- Balance is a projection on the `account` row, updated in the same transaction as the entries. Entries are the source of truth. Reconciliation verifies the projection matches `SUM` of entries.
- Values: `NUMERIC(20,4)` in Postgres and `decimal.Decimal` in Python. **Never** `float`, `double`, `REAL`, or the `money` type. Input arrives as a string and becomes `Decimal` directly. JSON output as string.
- One currency per ledger account. System accounts are also per currency (`external:deposits:BRL`).
- Unique `idempotency_key`: the same request twice produces only one transaction.
- Customer account never goes negative. Verification happens with the account row locked (`FOR UPDATE`, in ID order).
- Concurrency tests use real threads, not sequential calls.

## Workflow

- One idea per commit. Explain the *why*, not just the *what*.
- Explanation before code: no implementation before `docs/ledger.md` exists and passes review (see `docs/briefing.md`, decision 6).
- ADRs are written by the project owner. You can ask questions, point out alternatives, and review, but you don't write the decision.
- Don't add dependencies, frameworks, Docker, FastAPI, or folder structure before the step that calls for it. When in doubt, ask.
- Docs and code in English.
- When you finish each step, stop and say what you did, what's left, and which decision is needed.

For additional contributor-specific instructions, see `work-instructions.md` (if present).
