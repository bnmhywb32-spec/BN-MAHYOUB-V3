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

## Local transport and authority proof

Run from the repository root:

```sh
python3 -B -m unittest -v proofs.sync.test_transport proofs.sync.test_http_transport proofs.sync.offline_authority proofs.search.local_search proofs.search.test_scoped_search
```

The SQLite authority fixture reserves finite stock or cash per company and device. It settles queued commands atomically, returning the stored receipt after a lost acknowledgement and accepting only contiguous inbox revisions. The test harness issues opaque, revocable device sessions scoped to a company. Sessions are read-only by default; a write-capable session still needs a resource grant. The sale fixture posts a balanced four-line journal and consumes stock in the same transaction. Tests cover an injected journal failure, rejection of a snapshot older than a known receipt, and replay against an integrity-checked backup. The receipt checkpoint must be retained independently of the snapshot; it does not establish that all recent permissions or offline operations were captured. User authentication, release of offline reservations, stale-backup fencing and remote transport remain separate acceptance checks. Revoking a device invalidates all its issued sessions and keeps reserved stock encumbered until reconciliation. The offline sale commits inventory, journal and pending transport payload together. The loopback HTTP test delivers that committed payload over separate connections and checks concurrent replay against the authority.

For the isolated recovery fixture, retain the per-company result of `recovery_checkpoint` independently from the database. It captures the latest receipt, authorization and reservation fingerprint, and ordered operation-history fingerprint in one read transaction. Recovery requires all three kinds of evidence for each relevant company. The history check rejects a changed earlier operation even if the latest receipt and a rebalanced journal still match. Establishing trustworthy, durable checkpoint storage and handling observations that occur after the retained checkpoint remain separate production gates.

The loopback transport test also starts a temporary TLS server with a short-lived synthetic certificate. It checks an untrusted certificate and a hostname mismatch before exercising a scoped sale, duplicate delivery and device revocation over the verified connection. This establishes the test client's certificate checks and local message path. Enrollment, key rotation, trust provisioning and a real remote deployment remain open.

A separate HMAC fixture signs the per-company checkpoint and verifies it against an independently supplied latest tag. The verification test rejects altered contents, another company, a wrong key and a previously valid checkpoint after revocation. The key and latest tag are generated by the test and retained outside the snapshot. Their secure storage, renewal and availability in an actual deployment are not established by this fixture.

The isolated Arabic search normalizes diacritics, elongation and decimal digits. Barcode matching preserves leading zeros and gives an exact original barcode priority when visually equivalent digit scripts collide. Search version 2 rebuilds a legacy catalog's terms and normalized barcodes atomically; an unknown newer normalization version is rejected. The fixture does not authenticate a caller or replace scoped product-level authorization.

The authorized search fixture derives the company and device from a live session and checks an explicit branch grant in the same SQLite read transaction as the query. Tests cover company and branch isolation, device and branch revocation, and a revocation concurrent with a read. The lower-level search API remains a technical component; callers requiring access control use the scoped fixture. Branch scopes are included in recovery fingerprints, and legacy backups without scope records restore with no branch grants. User enrollment, policy administration and equivalent platform integration remain open.
