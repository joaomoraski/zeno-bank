# ADR-005 — Modular monolith

**Status:** accepted
**Date:** 2026-10-14

---

## Context

The project will eventually have multiple modules: ledger, accounts, transfers, FX, investments, and supporting infrastructure. The architectural question is whether to build them as separate services (microservices) or as modules within a single deployable unit (modular monolith).

## Decision

**Modular monolith.** All modules live in one Python codebase, share one Postgres instance, and deploy as one process. Each module gets its own Postgres schema (`ledger.*`, `accounts.*`, `transfers.*`). No cross-schema foreign keys — modules reference each other by ID, validated through public interfaces.

The distributed part enters later via an outbox table and a worker process, giving at-least-once delivery and idempotent consumers without breaking the ledger's transactional guarantees.

## Alternatives considered

### Microservices from the start — rejected

Rejected for three reasons:

1. **The ledger's central invariant is atomic.** The sum of a transaction's entries must equal zero, verified in the same database commit. Splitting the ledger across a network boundary breaks this guarantee and forces a distributed transaction protocol (saga, 2PC, or outbox-based compensation) to recover it — complexity that exists only to compensate for a boundary we created.

2. **Microservices solve an organizational problem.** Independent deployment matters when multiple teams need to ship on different schedules. This is a one-person project. The overhead of service contracts, network error handling, and distributed tracing between services has no corresponding benefit.

3. **Premature splitting creates debt, not flexibility.** Every service boundary is a commitment to a specific API contract. Getting the boundary wrong early means either living with a bad split or paying the cost of a merge. A modular monolith lets boundaries emerge from usage, and extraction is a future option when there's a real reason (e.g., a Go webhook dispatcher consuming events from the broker — a natural service boundary).

## Consequences

**Positive**
- A single ACID transaction covers ledger entries, balance updates, and the outbox row. No distributed transaction needed.
- Simple deployment: one process, one database, `docker compose up`.
- Module boundaries are enforced by import rules (`import-linter` in CI), not by network calls. This exercises the discipline of clean boundaries without the overhead.
- Any module can be extracted into a service later by replacing the in-process call with an API call and the shared schema with a separate database. The outbox pattern already provides the async communication layer.

**Negative and mitigations**
- Does not demonstrate inter-service communication patterns (gRPC, service mesh, circuit breakers). **Mitigated by** the outbox + worker architecture (module 4), which demonstrates reliable async messaging, and by the optional Go webhook dispatcher (module 7), which is a real separate service consuming events from the broker.
- A single failure (OOM, deadlock) can take down all modules. **Acceptable** for a portfolio project at this scale; in production, this is when extraction earns its cost.
