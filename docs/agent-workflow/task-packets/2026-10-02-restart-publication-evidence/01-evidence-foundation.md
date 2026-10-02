---
id: "restart-publication-evidence-01"
title: "Bounded durable evidence foundation and read-only planner"
status: "ready"
parallel-safe: false
depends-on: []
base-commit: "bc3176387ec4e41d452c5cac6e07bce1ba5fbc20"
owner: "unassigned"
---

# Task Packet: Durable evidence foundation

## Goal

Provide an independently testable PostgreSQL intent/evidence repository, permanent two-generation reservations, durable user gate, bounded canonical codec and complete read-only Import-Memory planner. The new durable publication entry remains closed until packets 2 and 3 finish independent review.

The user approved the structural design on 2026-10-02 by replying “可以”, then requested continued implementation. Sol High implements, Astra High independently reviews concurrency/consistency, and Luna High records progress. These model roles replace the older workflow's Claude implementer role for this task.

## Non-goals

- No recovery queue, Worker/API/bootstrap enablement, detached terminal proof or durable live publication.
- No production or retained-resource migration, historical re-embedding, new-user episode profile selection, or changes to recall policy.
- Do not increase the existing 100000 vector bound or replace existing publication tests with expected failures.

## Delivery context and verified interfaces

- Approved design: `docs/superpowers/specs/2026-10-02-restart-publication-evidence-design.md`. Main plan: `docs/superpowers/plans/2026-10-02-restart-publication-evidence.md`, packet 1 tasks 1–4.
- `app/import_memory_publication.py`: C currently freezes `_Expected` after document preparation; `_preflight` checks explicit profile/vector, History, the full attested episode bundle and identity collisions. `_terminal` writes History/Memory/documents in the caller transaction, `_reconcile` uses exact receipts/domain in read-only repeatable read.
- `app/import_document_publication.py`: `_prepare_document(scope, attempt, new_points, task=None)` currently reads pinned source then puts the document before its History/vector checks. Split into read-only `_plan_document` and `_write_planned_document`; keep the existing `_prepare_document` wrapper and old public `publish` compatible.
- `app/postgres_import_leases.py`: `ImportAttempt` contains full task, worker, task token/version, `UserMutationLease`, source `ObjectRef`, bucket and expiry. `_live(cursor, attempt)` validates database task/user ownership under user-first locks and current DB clock. Recheck after blocking locks.
- `app/postgres_snapshots.py`: `_lock_user`, `compare_and_swap_in_transaction` and `update` already serialize on user rows. Guard `update` before invoking its mutation callback.
- `app/postgres_memory_documents.py`: add/delete transaction methods hold the user row; raw metadata TEXT must remain exact.
- `app/postgres_document_objects.py`: `VerifiedDocumentRef` is a live issuing-repository capability, never a reconstructed durable token. History's deterministic object path excludes VersionId; final scalar ref is appended after verification.
- Latest migration is `20260930_13_history_document_witnesses.py`. The new additive revision is `20261002_14`, `down_revision='20260930_13'`.
- Existing `shared_database`, `publication`, `memory_publication`, `store` fixtures create disposable random schemas/buckets/collections. Import the entire fixture dependency chain when reusing fixtures.
- `tests/integration/test_import_document_publication.py::test_013_upgrade_leaves_existing_strict_paired_head_without_witness` deliberately constructs revision 12 before upgrade. Seed that legacy fixture with its original SQL or a test-only schema-aware adapter; never catch missing gate tables in production and interpret them as an absent gate.
- Pre-existing changes: approved-design title/status and approval ledger, plus the uncommitted main plan and this packet. The coordinator owns all these documents; preserve them.

## Prerequisites

The structural design is approved. Before implementing the codec, review `.runtime/distributed-cutover/restart-evidence-sizing.md` and its JSON/helper from the independent offline sizing task. If those are not finished, perform the same bounded synthetic measurement yourself and freeze constants before code. Measurement uses synthetic complete intent shapes, representative and seeded incompressible text, and reports refusal where limits are exceeded; it is not product capacity acceptance.

Use `D:/python_self_agent/venv/Scripts/python.exe`. Root has assigned test-only PG `127.0.0.1:59497`, S3 `127.0.0.1:59498`, and target Qdrant `127.0.0.1:59499`. Source Qdrant `59500`, original PG/MinIO volumes, retained schemas/buckets/generations, the main application and `main` are protected. Inspect current mapping/health before testing. Only one pytest invocation may run; this packet's implementer owns it after the coordinator's handoff.

## Explicit owned-file boundary

- Create: `app/import_publication_evidence.py` (codec, validated immutable evidence data and fenced repository).
- Create: `migrations/versions/20261002_14_import_publication_evidence.py`.
- Create: `tests/integration/test_import_publication_evidence.py`.
- Modify: `app/postgres_snapshots.py`, `app/postgres_memory_documents.py`, `app/import_document_publication.py`, `app/import_memory_publication.py`.
- Modify for necessary regression assertions: `tests/integration/test_postgres_schema.py`, `tests/integration/test_postgres_snapshots.py`, `tests/integration/test_postgres_memory_documents.py`, `tests/integration/test_import_memory_preflight.py`, `tests/integration/test_import_memory_publication.py`, `tests/integration/test_import_document_publication.py`.
- Ignored measurement/report/log files under `.runtime/distributed-cutover/` are permitted; no runtime evidence or private data may be committed.
- Do not edit vector authority/lease recovery/coordination/Worker/API/bootstrap, other migrations, dependency manifests, or coordinator-owned documents. Report a concrete required boundary amendment to root before editing another path.

## Interface contract and required behavior

1. `encode_intent(intent: dict) -> EncodedIntent` and `decode_intent(encoded: bytes, *, canonical_bytes: int, digest: str) -> dict`: versioned canonical UTF-8 JSON, finite JSON-native values, sorted keys/compact separators, duplicate-key rejection, exact length/hash, bounded zlib streaming, no trailing/truncated streams, bounded decoded output and explicit typed identity/version validation. Publish the exact validated intent schema in tests and the handoff; no digest-only replacement for full values. No secret credentials, vectors, file bytes or traceback fields.
2. `PostgresImportPublicationEvidenceRepository.reserve_intent(attempt, intent) -> AttemptKey`, exact `(user_id, task_id, task_lease_version)`: one transaction revalidates source, task tuple, base heads/indexes, old full receipts and snapshots, then inserts evidence, the unresolved user gate and both globally unique UUID reservations. Duplicate IDs/drift/oversize refuse; ambiguous commit causes Unknown and no external write. Never reallocate IDs inside SQL or infer rollback from absence.
3. `read_exact(user_id, task_id, task_lease_version) -> FrozenEvidence | None`: copied private data, exact typed/hash/format/shape validation, tenant selectors; no live authority reconstruction of missing expected values. Durable evidence contains copied intent, exact source pin, owner/lease tuple, phase/version, full immutable hash and nullable write-once document/sealed/terminal slots. It is not a mutable context or a capability.
4. Migration enforces immutable identity/intent; legal forward phases, distinct unknown/manual-hold reasons, write-once slots, terminal non-demotion, a single unresolved gate per user and permanent global UUID reservation. No cascade/age deletion of reservations; bounded encoded/declared/slot lengths. Downgrade fails closed where it would erase proof.
5. Ordinary snapshot/Memory-document mutation guards check after the user lock. No gate preserves current behavior. An unresolved gate rejects every ordinary direct mutation, including a caller claiming the exact live attempt. No public attempt-only bypass, ambient context or recovery lease authorization. Packet 2 may add only a narrowly bound live terminal admission for the exact frozen values; this packet does not enable it.
6. Existing C/RAG entry points must reject an unresolved gate before any S3/Qdrant publication write, while preserving ungated library success. The new durable planner freezes copied task/source, both scope/index identities and base heads, old full receipts, old/new snapshots, raw row evidence, event/timestamp/item/metadata, deterministic document key/record, full domain count/digest and distinct RAG/episode candidate UUIDs before document put. Keep vectors in private live data only. The exact post-put VersionId and verified capability are absent from intent.
7. `_plan_document(...) -> _PlannedDocument`, `_write_planned_document(planned, attempt) -> _PreparedDocument`, and C `_plan_intent(...) -> _FrozenMemoryPlan` provide the read-only/private split. Revalidate freezes before writing; never rebuild expected values from changed current state. Hold the original issuing repository capability in the live path.
8. Future document/sealed/terminal slots are SQL write-once and phase-checked. Do not expose an implementation that writes unchecked terminal evidence merely because someone supplies a tuple or arbitrary `VerifiedDocumentRef`. If a method requires packet-2 issuance/admission that does not exist yet, keep it private/unavailable and report the interface contract without implementing a permissive stub.

## Acceptance and verification

- [ ] Sizing records canonical, compressed and total-slot bounds with exact samples/commands; above-bound complete intents refuse before external writes.
- [ ] Codec handles finite native types, bool/int distinctions, malformed UUID/time/schema, hash/length mismatch, duplicate keys, truncation/trailing streams and decompression bombs without partial data or unbounded output.
- [ ] Fresh schema upgrades from 13 to 14, preserves prior business rows, and enforces immutable proof/slots, tenant/attempt identity, global UUID uniqueness and permanent reservations.
- [ ] Real two-connection tests prove gate survives lease expiry, direct mutations fail before callbacks/data changes, unrelated users proceed, and source/base drift atomically leaves no partial gate/evidence/reservations.
- [ ] Complete private planner performs no authority or external writes; `reserve_intent` is a separate explicit transaction. All profile/collision/source/size refusals precede S3 put and candidate stage. Caller mutation does not alter frozen intent.
- [ ] Existing ungated C/RAG success and regression paths still pass; no durable publication or runtime enablement is claimed.

Run the new evidence module first; distinguish genuine behavioral RED from import/setup errors. Freeze source hashes before final runs and never edit loaded files. For the final selected suite:

```powershell
$env:POSTGRES_TEST_URL = 'postgresql://isolated:isolated-test-only@127.0.0.1:59497/cutover'
$env:S3_TEST_ENDPOINT = 'http://127.0.0.1:59498'
$env:S3_TEST_ACCESS_KEY = 'isolated'
$env:S3_TEST_SECRET_KEY = 'isolated-test-only'
$env:GENERATION_QDRANT_TEST_URL = 'http://127.0.0.1:59499'
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest -q tests/integration/test_import_publication_evidence.py tests/integration/test_postgres_schema.py tests/integration/test_postgres_snapshots.py tests/integration/test_postgres_memory_documents.py tests/integration/test_import_memory_preflight.py tests/integration/test_import_memory_publication.py tests/integration/test_import_document_publication.py --basetemp=.runtime/distributed-cutover/pytest-restart-packet1-final --junitxml=.runtime/distributed-cutover/restart-packet1-final.xml
```

Expected: all selected tests pass; no required service cases skip. Save full exit/log/meta/XML, exact hashes/command/timing and each disposable resource identity. Do not rerun the physical 100k slow test merely for a formatting or codec change. Run `py_compile` for changed Python sources and `git diff --check` after successful required checks.

## Handoff

Commit only owned implementation/test paths after verification and self-review; coordinator commits documents. Report actual parent/HEAD, exact files/interfaces, codec limits/measurements, acceptance evidence, precise test counts/exit/hash, historical failures, permitted residues and open packet-2/3 dependencies in ignored `restart-packet1-implementation-report.md`. Root generates the exact review package; Astra reviews before downstream implementation.

If current interfaces/required files do not support this boundary, report the specific mismatch, evidence and smallest amendment to root. Do not treat a routine implementation choice or already-approved structural scope as needing renewed user approval.
