# Proof 007 — isolated comparability contract

Status: implementing, **not** production-ready. Independent review dated 2026-11-25 permits isolated Flutter/Dart vs Kotlin/KMP+Compose proof; CORE-005 remains partially open. No V2 code or private data.

## Shared synthetic scenario

- Two companies A/B and branches A1/B1. Only the authorized user scoped to A/A1 may operate A/A1; cross-company access fails before writes.
- Item SKU PROOF-001 on A/A1 with starting quantity 10.000; unit sale price 100 **minor YER units**, unit cost 60 minor units. One command sells 2.000, generating quantity 8.000, revenue 200 and cost 120, with both (cash debit/revenue credit) and (COGS debit/inventory credit) balanced.
- Deterministic command ID and **separate** document ID. Same ID+same canonical content returns stored response without mutation. Same ID+different content rejects. An unauthorized caller with an otherwise matching ID must still be rejected.
- All inventory and journal effects, the document, command result, and durable outbox event must commit in a **single local database transaction**. Injected failure after intermediate writes must roll back *all* effects. Reopening the database must preserve both the original result and stock.
- Two distinct commands selling the last unit concurrently must not both succeed; final stock nonnegative and journal remains balanced. No network-dependent processing in transaction.
- Quantities must be represented by exact fixed-point decimal value (e.g. scale 3); money in minor integer units; no floating point for monetary arithmetic. Test web precision edge values.
- Offline persistence, dummy app UI and basic Arabic layout, Android/Windows/web builds, barcode and printing capability checks, 10k-item search and 100-run p95 measurements are **distinct gates**. Do not mark a gate passed without an executed test artifact.

## Equal technical tracks

- `proofs/flutter/`: Dart + Flutter candidate, local relational transactional storage suitable for Android, Windows and web.
- `proofs/kotlin/`: Kotlin Multiplatform + Compose candidate with a comparable transactional store and supported targets.
- Different target implementations and platform limitations must be documented rather than hidden. Same fixtures, command/replay/failure/access/concurrency/reopen assertions in both tracks.
- Packages, versions, licenses, build environment and any cost are recorded before adopting. No automatic dependency installation beyond free CI; no paid services.

## Boundaries and evidence

No production sales, tax, exchange or pricing policy is adopted by these synthetic fixtures. Current status is branch creation and proof implementation. Source code, tests, CI run links, build logs, and measurements are separate proof evidence. CORE-005 is **not** fully approved; it cannot be silently closed by this experiment.
