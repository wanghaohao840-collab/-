# Plan Review: isolated authenticated import runtime

- Source plan: `docs/superpowers/plans/2026-10-10-isolated-import-runtime.md`
- Approved design: `docs/superpowers/specs/2026-10-10-isolated-import-runtime-design.md`
- Reviewed commit: `38bc8bcabd90e23a7518375d7e2087a399f0eec9`
- Review date: 2026-10-10
- Verdict: Task 01 is accepted and done at source commit `d81f0c3494d02bca51fb312c602d215edf6f98a0`. Task 02's complete implementation contract passed independent Astra High review and is ready; its source implementation and tests are pending. Tasks 03–07 remain proposed work, not reviewed or ready packets.

## Repository evidence

- Relevant implementation:
  - `app/postgres_import_leases.py:90` — ordinary `claim_next(worker_id, lease_seconds=60)` scans active users by `import_user_schedule`, then takes a short transaction per candidate, locks user before task, acquires user lease, and writes task/audit/schedule.
  - `app/postgres_import_leases.py:468` — `recover_expired(limit=100)` scans all running tasks, then locks user, user lease and task before audit/evidence checks and expiry mutations.
  - `app/import_publication_recovery.py:265` — recovery `claim_next(worker_id: str, lease_seconds: int=60)` ranks due pending or expired claimed queue rows per user, locks the final queue candidate, commits a short capture, then obtains original authority in another transaction. Both ranked and final predicates need the tenant filter; the pending/claimed `OR` needs complete parentheses.
  - `app/import_publication_recovery.py:41` — `_report_unavailable` translates connection failures; pure allowlist validation must still raise `ValueError` before database use.
- Relevant tests:
  - `tests/test_import_tenant_selection.py` — new service-independent transaction-bomb unit cases must run in a subprocess without the five integration fixture environment keys; `None` is checked with a real database by integration cases.
  - `tests/integration/test_postgres_import_leases.py:15` — `submit` helper and existing ordinary claim, fairness, expiry and stale-handle cases.
  - `tests/integration/test_postgres_import_artifacts.py:21` — `fixture` makes two active UUID users, one disabled user, two pools and an immutable store.
  - `tests/integration/test_import_publication_recovery.py:1570` — existing evidence/queue expiry and recovery claim path. The actual recovery suite filename has no `postgres_` prefix.
  - `tests/integration/test_import_memory_publication.py:36` and `tests/integration/test_import_publication_proof.py:75` — `memory_publication` and `_issue` prepare real evidence and a recovery queue for queue-selection tests.
  - `tests/integration/test_postgres_auth_sessions.py:28`, `tests/integration/test_s3_object_store.py:15`, `tests/integration/test_import_document_publication.py:40` — disposable PG schema, versioned S3 bucket, and target Qdrant collections are fixture prerequisites; absent endpoints skip tests.
- Configuration/runtime facts:
  - The repository venv is `D:\python_self_agent\venv\Scripts\python.exe`. No pytest was run in this review; the root controls the only pytest lane and will give a separate GO.
  - The observed service snapshot `.runtime/distributed-cutover/isolated-runtime-services-root-20261010-n1.json` records disposable PG on loopback 59497, S3 on 59498, target Qdrant on 59539, and protected source Qdrant exited. This is a recorded observation, not current service health or test acceptance.
  - Fixture environment keys are `POSTGRES_TEST_URL`, `S3_TEST_ENDPOINT`, `S3_TEST_ACCESS_KEY`, `S3_TEST_SECRET_KEY`, and `GENERATION_QDRANT_TEST_URL`. Test commands must target the exact disposable endpoints and use a unique basetemp.
- Existing worktree changes to preserve:
  - The 15 pre-existing modified paths are `app/coordination.py`, `app/document_library.py`, `app/history.py`, `app/import_models.py`, `app/import_worker.py`, `app/memory_repository.py`, `app/qa_deletion.py`, `app/qa_persistence.py`, `app/qa_repository.py`, `app/recovery.py`, `migrations/versions/20260926_01_business_schema.sql`, `migrations/versions/20260928_09_import_leases.py`, `tests/conftest.py`, `tests/test_import_worker.py`, and `tests/test_user_mutation_coordination.py`. Status may report them modified even when a normal diff is empty; do not reset, normalize or rewrite them.
  - The source plan itself was pre-existing untracked work before this review.

## Findings

### Blocking

- None for drafting packet 01. Its assignment remains gated on independent Astra review of this packet and any resulting corrections.

### Required revisions

- The plan's Task 01 fixture sentence was revised: ordinary and expiry cases can use the existing two-user artifact fixture; recovery queue cases need the existing `memory_publication` evidence fixture or equivalent valid persisted evidence.
- The plan's Task 01 gate was revised to separate pre-implementation `ready` from post-implementation `done`. Neither status is earned by the current document-only work.
- Task 02's current-code and independent contract review are recorded in `02-runtime-readiness.md`; its new isolated interfaces are reviewed producer contracts, not existing implementation. Tasks 03–07 require fresh interface review after their predecessors.

### Non-blocking notes

- The first packet is limited to SQL tenant selection. It makes no claim that an isolated API, Worker, business migration, rollback, or production route is operational.
- The source plan's test steps describe intended RED/GREEN evidence. This review observed code and fixture definitions but ran no tests.

## Accepted scope

- Goal: permit the existing ordinary claim, ordinary expiry scanner, and publication recovery claim to select only explicitly permitted users at SQL candidate selection time.
- In scope: three optional keyword-only `allowed_user_ids: frozenset[str] | None = None` parameters, exact input validation, parameter-bound SQL filters, locked-row rechecks, and focused integration tests.
- Out of scope: runtime configuration, API/session facades, Worker loops, publication execution, schema changes, deployment, protected source Qdrant, and any production cutover.
- Compatibility requirements: `None` preserves existing positional calls and unfiltered behavior; an empty valid frozenset returns `None` or `0` without a transaction; malformed values fail before database access.
- Architecture/data-isolation constraints: filter in the initial and final SQL selectors before claim/expiry mutation; preserve user-first authority lock order, fresh DB clock after locks, fairness without a candidate cap, short recovery capture transaction, gate/evidence checks, and exact task/user lease tuples.

## Packet graph

| Packet | Depends on | Parallel-safe | Owned files | Outcome |
|---|---|---:|---|---|
| `01-tenant-selection.md` | none | no | `app/postgres_import_leases.py`, `app/import_publication_recovery.py`, optional `app/import_tenant_selection.py`, `tests/test_import_tenant_selection.py`, `tests/integration/test_isolated_import_tenant_selection.py` | SQL tenant prefilter for ordinary claim, expiry, and recovery |
| `02-runtime-readiness.md` | `01-tenant-selection.md` (done) | no | create `app/isolated_import_runtime.py`, `tests/test_isolated_import_runtime.py` | Reviewed explicit isolated configuration, resource ownership, and independent recovery/ordinary readiness |
| Tasks 03–07 | serial predecessors per source plan | no | not yet assigned | New packets only after predecessor acceptance and current-code review |

## Packet readiness audit

| Packet | Goal/non-goals | Context/interfaces | Prerequisites | Change boundary | Acceptance/tests | Forbidden changes | Handoff format | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `01-tenant-selection.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `02-runtime-readiness.md` | yes | yes | yes | yes | yes | yes | yes | yes |

Task 01 passed its earlier ready gate, was implemented, then independently accepted. The completed packet records final GREEN 28 and regression 194 with actual exits 0; the root's accepted delivery binding is `.runtime/isolated-import-runtime/p1-delivery-root-n3.json`, and Astra's final Spec/Quality PASS report is `p1-final-accepted-astra-n4.md` (SHA256 `d911fcc66e1c920034cf8e57752fe9836e8049fb03dffe846b14286f81004cd5`). Task 02 passed independent plan review: `.runtime/isolated-import-runtime/p2-plan-astra-n2.md`, SHA256 `16130c01e2e8c57a4c4300eb8ed173838ec2cd19a8565af4312077331a686553`, binding the complete pre-bookkeeping packet `a8a89fb5bde13e528b8c32bfb3acd1a94b808e999808cc203327c8d83fd4c791` and source plan `7a05589b77990a8d0940b1b52628af7b52b30f3399e10fedaaae09405e38988c`. Task 02 is not implemented or tested. Its source base is `d81f0c3`; root's later progress-document commit `181e2d9` is a descendant and changes no source interface. Readiness bookkeeping does not change the independently reviewed contract.

## Integration verification

- After root grants the sole pytest lane, first run the packet's service-independent transaction-bomb unit file in a child process with only the five fixture environment keys removed, then run the new integration file and the existing `tests/integration/test_postgres_import_leases.py`, `tests/integration/test_import_publication_recovery.py`, and `tests/integration/test_postgres_import_artifacts.py` with the fixed venv, disposable PG/S3/target Qdrant endpoints, distinct basetemps, JUnit XML, actual exit codes, and zero skips. The exact serial commands are in packet 01.
- At final integration, also verify ordinary local behavior and ordinary distributed bootstrap refusal with commands chosen from then-current tests; do not count today's document review as runtime validation.

## Final integration review requirement

- Output: `docs/agent-workflow/task-packets/2026-10-10-isolated-import-runtime/FINAL_INTEGRATION_REVIEW.md`
- Required after: every implementation packet is `done`.
- Result must be: `accepted | changes-required | blocked`.
- Required checks:
  - cross-packet interfaces
  - missing requirements
  - duplicate or overlapping implementation
  - central integration points
  - architecture, compatibility, persistence, and isolation
  - combined regression verification

## Open decisions

- No unresolved plan findings for Tasks 01–02. Task 02 implementation requires the assigned Sol High model to be available; its last planning attempt ended on an explicit usage-limit error. Tasks 03–07 still require fresh packets and review after their predecessors are accepted.
