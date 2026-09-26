# Distributed Cutover Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. In this repository, `docs/agent-workflow/README.md` first requires Codex plan review and self-contained task packets; implementation begins only after that handoff and a separate user instruction.

**Goal:** Make every currently deployed business path run on PostgreSQL, S3-compatible object storage and independent Workers, then prove a complete isolated migration and a version rollback that preserves writes made after cutover. This plan stops before production migration.

**Architecture:** Keep the current SQLite/local-file mode intact. Add an explicit distributed composition root with PostgreSQL as the sole structured authority, object storage as the sole durable file authority, and independently started Workers. Migrate one frozen paired source copy into an isolated target, verify all source and target inventories, run full business acceptance, then switch between two separately built distributed application images over the same target data.

**Tech Stack:** Python 3.11, FastAPI, psycopg 3 and Alembic, PostgreSQL 17, an S3-compatible test service, Qdrant, Docker Compose, pytest, React/Playwright. Pin actual test image digests in the implementation; do not rely on floating tags.

## Global Constraints

- Governing design: `docs/superpowers/specs/2026-09-24-distributed-cutover-vertical-slice-design.md`. The older `2026-08-09-distributed-persistence-foundation.md` plan is a reference for configuration and schema, not sufficient delivery by itself.
- Current checkout at plan authoring: `528e70e447b0264193126265a7f29d39987e8386`. Reverify this base before editing. The main worktree has unrelated dirty GraphRAG, AGENTS and web files; preserve them. The `distributed-multi-user-system` worktree has uncommitted `requirements.txt`, `app/postgres.py`, and `tests/test_postgres.py`; review it as a source of ideas, not an automatic merge.
- `local` remains supported. `distributed` must fail startup when PostgreSQL, object storage, Worker role, or schema readiness is absent; it must never silently read or write local authoritative data.
- Preserve `user_id`, `document_id`, source-page, citation, RAG namespace, Memory, History, report, learning-plan and request-id isolation. Do not change the embedding provider, embedding revision, Qdrant collection identity, or product behavior in this program.
- One writer authority at each cutover point. No SQLite/PostgreSQL live dual write and no shared SQLite, JSON, or local user directory across API replicas.
- PostgreSQL owns users, sessions/CSRF, documents, jobs, notes, learning, reports, History/Memory authority and leases. Object storage owns durable uploads, documents and report files. Qdrant remains a vector/derived-data backend and must be checked against the matching frozen source.
- API in distributed mode starts no embedded import, QA, deletion, note projection or summary Worker. Claims, heartbeats and terminal writes require database leases/fencing; external effects use deterministic IDs and reconciliation so stale Workers cannot publish authoritative results.
- Preserve production single-instance service and current paired rollback path while executing Tasks 1–10. Do not mount production App data, Qdrant volume, or production credentials in development tests. Runtime evidence, copies, secrets, generated Compose and backups stay outside Git.
- Run Python tests with `D:/python_self_agent/venv/Scripts/python.exe`. Use repository-local `--basetemp` directories. Default tests must not require Docker or live model services; isolated integration gates explicitly do.
- Use compatible schema expansion until both distributed candidate and rollback images have been tested against the same post-cutover writes. Do not contract schema in the first cutover window.

## File and responsibility map

| Unit | Existing files to adapt | New files / responsibility |
| --- | --- | --- |
| Inventory and contracts | `app/database.py`, `app/storage.py`, `deploy/verify_isolated_restore.py` | `deploy/distributed_inventory.py`, `tests/deploy/test_distributed_inventory.py`: read-only source inventory and coverage map |
| Configuration and schema | `requirements.txt`, `app/bootstrap.py`, `deploy/.env.example` | `app/deployment.py`, `app/postgres.py`, `migrations/`, `tests/test_distributed_settings.py`, `tests/integration/test_distributed_schema.py` |
| Identity and coordination | `app/auth.py`, `app/session.py`, `app/runtime.py`, `app/coordination.py` | `app/distributed/__init__.py`, `app/distributed/identity.py`, `app/distributed/coordination.py`, tests for cross-instance session and same-user writes |
| Relational business state | `app/import_repository.py`, `app/qa_repository.py`, `app/qa_job_repository.py`, `app/qa_deletion.py`, `app/note_repository.py`, `app/note_projection.py`, `app/learning_repository.py`, `app/reports.py` | `app/distributed/repositories/__init__.py`, `import_tasks.py`, `qa.py`, `notes.py`, `learning.py`, `reports.py`; parity tests for all active API routes |
| Durable files and snapshots | `app/storage.py`, `app/history.py`, `app/memory_repository.py`, `app/document_library.py`, `app/reports.py`, RAG path consumers | `app/distributed/objects.py`, `app/distributed/snapshots.py`, tests for keys, versions, integrity and user isolation |
| Independent execution | `app/import_worker.py`, `app/qa_worker.py`, `app/qa_deletion.py`, `app/note_projection.py`, `app/summary_tasks.py`, `app/bootstrap.py` | `app/distributed/worker.py`, tests for lease loss, duplicate delivery, expiry and shutdown |
| Composition and API | `app/bootstrap.py`, `api/app.py`, `api/routes/*.py`, `server.py` | `tests/integration/test_distributed_api.py` and startup contract tests |
| Offline migration | Existing local/legacy migration modules are read-only input contracts | `deploy/distributed_migrate.py`, `deploy/distributed_verify.py`, `tests/deploy/test_distributed_migration.py` |
| Isolated release proof | `deploy/verify_isolated_restore.py`, existing deploy tests | `deploy/verify_distributed_cutover.py`, `deploy/compose.distributed.test.yaml`, `tests/deploy/test_distributed_cutover.py`, local ignored evidence directory |

These are ownership areas, not permission to bulk-copy the old worktree. The plan review must turn them into disjoint or explicitly dependent packets. Direct SQLite calls in services, assistants or routes discovered during implementation belong to the corresponding business-state or composition packet and must be listed in that packet before editing.

## Task 1: Freeze the source contract and enumerate every authority

**Files:** Create `deploy/distributed_inventory.py`, `tests/deploy/test_distributed_inventory.py`; document the table/file/vector ownership matrix in `docs/deployment/distributed-inventory.md`.

**Interfaces:** `inventory_source(app_root: Path, qdrant_manifest: Path) -> dict` returns schema/table row counts and canonical row digests, per-file SHA-256, path class, user/document association, and matching vector collection/point summary. `qdrant_manifest` is produced by reading a newly restored isolated Qdrant volume, not by parsing a raw volume tarball or querying production. It never reads a live production write path. Private payloads and credentials do not enter logs or Git.

- [ ] Add a synthetic fixture containing two users, same-name documents, notes, learning tasks, History, Memory, reports and Qdrant metadata; assert inventory distinguishes users and rejects symlinks, unknown durable files and inconsistent document references. Run `D:/python_self_agent/venv/Scripts/python.exe -m pytest -q tests/deploy/test_distributed_inventory.py --basetemp=.runtime/pytest-distributed-inventory-red` and observe the missing interface failure.
- [ ] Implement read-only SQLite `mode=ro` traversal, bounded file hashing and vector-manifest input. Use deterministic JSON serialization and explicit categories (`postgres`, `object`, `qdrant`, `discardable`, `blocked`); treat unknown durable data as `blocked`, never silently skip it.
- [ ] Produce a checked matrix for every table in `app/database.py` and `app/learning_schema.py`, every path class in `app/storage.py`, and current Qdrant collection/namespace metadata. Restore the paired Qdrant backup into a new isolated volume before generating its manifest. Include session and transient Worker state classification. Fail the inventory when any table/file class lacks a target or any user/document reference is unresolved.
- [ ] Run the focused test green and `D:/python_self_agent/venv/Scripts/python.exe -m pytest -q tests/test_user_storage.py tests/test_history_repository.py tests/test_memory_repository.py --basetemp=.runtime/pytest-distributed-inventory-regression`. Commit only this task's files and evidence-neutral documentation.

## Task 2: Establish fail-closed distributed settings and PostgreSQL schema

**Files:** Create `app/deployment.py`, `app/postgres.py`, `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/20260926_01_distributed_foundation.py`, `tests/test_distributed_settings.py`, `tests/integration/conftest.py`, `tests/integration/test_distributed_schema.py`, `deploy/compose.distributed.test.yaml`; modify `requirements.txt`, `deploy/.env.example`.

**Interfaces:** `DeploymentSettings.from_env(env: Mapping[str,str]) -> DeploymentSettings`; `validate() -> None`; `PostgresDatabase.transaction()` returns a context-managed cursor. Schema migrations run only through Alembic, never during API startup. Explicit `APP_DATA_MODE=distributed` and `APP_PROCESS_ROLE=api|worker` select the mode and role.

- [ ] Write tests for absent/invalid settings, secret-redacted diagnostics, healthy and unreachable PostgreSQL, incompatible schema revision, and local-mode unchanged startup. Example expected failure: `DeploymentSettings.from_env({'APP_DATA_MODE':'distributed','APP_PROCESS_ROLE':'api'}).validate()` raises `DeploymentConfigurationError` listing missing keys without secret values. Run focused tests red.
- [ ] Bring forward only reviewed settings/connection code from the old worktree. Define all current SQLite tables plus session, document authority and lease/outbox tables in PostgreSQL with composite user scoping, constraints, UTC timestamps and indexes. Use an explicit schema version check during startup; do not issue DDL there.
- [ ] Start pinned PostgreSQL in the isolated Compose profile, apply Alembic to a fresh database, inspect constraints/indexes, then repeat against an already-migrated database. Run `D:/python_self_agent/venv/Scripts/python.exe -m pytest -q tests/test_distributed_settings.py tests/integration/test_distributed_schema.py --basetemp=.runtime/pytest-distributed-foundation` and record the image digest. Commit foundation only after both paths pass.

## Task 3: Move authentication, session and user write coordination to shared state

**Files:** Create `app/distributed/identity.py`, `app/distributed/coordination.py`, `tests/integration/test_distributed_identity.py`; adapt `app/auth.py`, `app/session.py`, `app/runtime.py`, `app/coordination.py` only at backend selection and shared-state boundaries.

**Interfaces:** Distributed identity implements existing `SessionRegistry.register/login/logout/get_session/validate_csrf` behavior. Store a session-token digest, the session CSRF secret, expiry and revocation in PostgreSQL. The CSRF secret must remain recoverable because `GET /api/v1/auth/session` returns it in the existing contract; never include it in inventory or logs. Selected document and other session-visible state must survive an API process restart. `distributed_user_mutation(user_id)` serializes short state commits with PostgreSQL transaction/advisory lock; it never holds a lock during LLM generation.

- [ ] Add two API-instance tests: login through A, read `/auth/session` through B with the same cookie, mutate through B, observe through A, logout through A and reject through B. Exercise wrong-user document selection and CSRF on both instances. Run red.
- [ ] Implement PostgreSQL-backed identity without embedding `UserRuntime` or `PDFLearningAssistant` in durable sessions. Recreate request-scoped runtime/assistant from persisted user and selection; keep local `SessionRegistry` unchanged behind explicit mode selection. Make same-user History/Memory mutations transactional and cross-instance serialized.
- [ ] Test concurrent same-user writes for no lost update, different-user parallelism, token expiry/revocation and process restart. Run `D:/python_self_agent/venv/Scripts/python.exe -m pytest -q tests/integration/test_distributed_identity.py tests/test_session_registry.py tests/test_user_mutation_coordination.py --basetemp=.runtime/pytest-distributed-identity`. Commit after cross-instance behavior passes.

## Task 4: Implement distributed relational repositories for every active business path

**Files:** Create `app/distributed/repositories/__init__.py`, `app/distributed/repositories/import_tasks.py`, `app/distributed/repositories/qa.py`, `app/distributed/repositories/notes.py`, `app/distributed/repositories/learning.py`, `app/distributed/repositories/reports.py`, and `tests/integration/test_distributed_repositories.py`; adapt repository selection in `app/bootstrap.py`. Preserve public route/schema files unless a verified adapter mismatch requires a narrow change.

**Interfaces:** Distributed repositories expose the service-consumed methods of `ImportTaskRepository`, `QaRepository`, `QaJobRepository`, `QaDeletionRepository`, `NoteRepository`, `NoteProjectionRepository`, `LearningRepository`, and report metadata. Every query/mutation is scoped by `user_id`; a claim creates `(worker_id, lease_version, lease_token)`, while heartbeat and terminal methods require these values and database server time.

- [ ] For each active API route in `api/routes/auth.py`, `documents.py`, `imports.py`, `qa.py`, `search.py`, `notes.py`, `learning.py`, and `insights.py`, add contract cases on the distributed backend covering owner, another user, idempotent replay, conflict, delete fence and restart persistence. Run focused tests red.
- [ ] Port SQL semantics deliberately: preserve stable IDs, ordering, request digest, version conflict, source attribution and cascading deletion. Implement PostgreSQL claims with `FOR UPDATE SKIP LOCKED` and a same-user running uniqueness constraint. Route all service accesses through selected repositories; reject any direct SQLite connection in distributed mode.
- [ ] Run `D:/python_self_agent/venv/Scripts/python.exe -m pytest -q tests/integration/test_distributed_repositories.py tests/test_import_repository.py tests/test_qa_repository.py tests/test_qa_job_repository.py tests/test_note_repository.py tests/test_learning_plan_repository.py --basetemp=.runtime/pytest-distributed-repositories`. Audit `rg -n 'sqlite3|from app.database import|connect\(' app api assistants` and account for every hit in the backend map. Commit when route parity and user isolation pass.

## Task 5: Replace persistent local files and snapshots with object/structured authority

**Files:** Create `app/distributed/objects.py`, `app/distributed/snapshots.py`, `tests/integration/test_distributed_objects.py`; adapt `app/storage.py`, `app/history.py`, `app/memory_repository.py`, `app/document_library.py`, `app/reports.py` and the specific RAG/assistant path consumers found in Task 1's matrix.

**Interfaces:** Object keys derive only from validated `user_id`, `document_id`/report ID and content digest. An upload is staged and verified before a PostgreSQL pointer becomes visible; reads verify ownership and expected size/digest. History and Memory authority is structured PostgreSQL state, with versioned compare-and-swap updates. Local paths in distributed mode are disposable per-attempt scratch only.

- [ ] Add tests for two users with colliding filenames, duplicate upload, partial upload, object mismatch, report download after API restart, History/Memory concurrent updates, and missing-object fail-closed behavior. Run red.
- [ ] Implement a bounded object adapter over the chosen S3 API. Replace `UserStorage` path assumptions at the business boundary; keep a local scratch directory only for parser/generator inputs, delete it after terminal task outcome. Persist the canonical object key/digest in PostgreSQL and make retries idempotent. Keep Qdrant vector state keyed to the existing user/document namespace.
- [ ] Run `D:/python_self_agent/venv/Scripts/python.exe -m pytest -q tests/integration/test_distributed_objects.py tests/test_user_storage.py tests/test_history_repository.py tests/test_memory_repository.py tests/test_report_service.py --basetemp=.runtime/pytest-distributed-objects`. Stop if any distributed API path still resolves `users/<id>/history.json`, `memory/memories.json`, `rag/rag_cache.json` or report/document files as durable authority. Commit the storage boundary.

## Task 6: Run all background work outside the API with fencing

**Files:** Create `app/distributed/worker.py`, `tests/integration/test_distributed_workers.py`; adapt `app/import_worker.py`, `app/qa_worker.py`, `app/qa_deletion.py`, `app/note_projection.py`, `app/summary_tasks.py`, `app/bootstrap.py`, and the test Compose worker service.

**Interfaces:** `python -m app.distributed.worker` starts only worker-role services. API mode starts none. A claim assigns owner, token, incremented lease version and server-time deadline; heartbeat/terminal updates use all three. External Qdrant/object effects have deterministic IDs and a reconciliation record before authoritative terminal commit.

- [ ] Write an integration test with API and two Workers: assert only one active same-user task, cross-user progress, lease expiry/reclaim, stale Worker heartbeat and terminal rejection, duplicate delivery idempotence and durable status after restart. Include import, QA, deletion, note projection and summary paths. Run red.
- [ ] Implement claim/renew/finish and cooperative cancellation checks around each external side effect. Worker shutdown drains claims or lets leases expire; API startup must not spawn existing pools in distributed mode. Where current jobs lack durable records (notably in-process summary tasks), add them before enabling the route in distributed mode.
- [ ] Run `D:/python_self_agent/venv/Scripts/python.exe -m pytest -q tests/integration/test_distributed_workers.py tests/test_import_worker.py tests/test_qa_worker.py tests/test_qa_deletion.py tests/test_note_projection.py --basetemp=.runtime/pytest-distributed-workers`. Demonstrate a killed Worker cannot publish a later terminal result after another Worker owns the lease. Commit the worker runtime and lifecycle.

## Task 7: Wire one explicit distributed composition root and preserve API contracts

**Files:** Modify `app/bootstrap.py`, `api/app.py`, `server.py` only as needed for mode/role selection; create `tests/integration/test_distributed_api.py`. Keep existing local application factory and UI route semantics.

**Interfaces:** `create_application(settings: DeploymentSettings | None = None)` selects either complete local services or complete distributed services. It must not mix backends. The existing public API routes and response models are unchanged; `/healthz` becomes unhealthy if PostgreSQL/object/required worker capability is unavailable in distributed mode.

- [ ] Add a startup test with poisoned local data paths: distributed API starts when shared dependencies are healthy without creating/opening SQLite or user directories; missing dependency/schema fails startup. Add a local startup test that keeps current behavior. Run red.
- [ ] Compose the Task 3–6 adapters in one place; do not scatter environment checks throughout business services. Ensure `APP_PROCESS_ROLE=api` never instantiates in-process Worker pools and `worker` role never serves public HTTP endpoints.
- [ ] Run `D:/python_self_agent/venv/Scripts/python.exe -m pytest -q tests/integration/test_distributed_api.py tests/test_app_bootstrap.py tests/api/test_app_lifecycle.py tests/api/test_import_routes.py tests/api/test_qa_routes.py tests/api/test_note_routes.py tests/api/test_learning_routes.py --basetemp=.runtime/pytest-distributed-api`. Commit only after local regression and distributed route parity pass.
- [ ] Build and record the immutable distributed **rollback image R0** from this passing commit and its migration/schema revision. It must pass the Task 7 tests and later Task 9 tests against the post-migration target. Task 9 builds the distinct candidate image R1 from its later commit.

## Task 8: Build repeatable, offline, all-data migration and verification

**Files:** Create `deploy/distributed_migrate.py`, `deploy/distributed_verify.py`, `tests/deploy/test_distributed_migration.py`; use Task 1 inventory as the manifest contract. Add a versioned target migration ledger to PostgreSQL via Alembic.

**Interfaces:** `migrate(source_root, source_manifest, target_settings, operation_id)` accepts only a frozen read-only App/Qdrant pair and an empty/version-compatible target. It copies every approved SQLite row and durable file preserving IDs and user scope, records source and target counts/digests/object versions and never connects to production write endpoints. `verify(...)` compares the full manifest and fails on unexplained differences. Rerun with the same operation ID is idempotent; a different source manifest cannot reuse the target.

- [ ] Use a synthetic paired backup with users, documents, imports, QA, notes and sources, learning tasks, History, Memory, reports, pending/failed jobs and Qdrant vectors. Add tests for truncated files, corrupt SQLite, missing vector points, wrong-user object key and interrupted/repeated migration. Run red.
- [ ] Implement deterministic row transforms and object copy with per-item checkpoint after target hash verification. Current `app/session.py` sessions live in process memory, so do not invent source SQLite session rows: explicitly expire old sessions at cutover and require login while preserving user identity. Classify stale in-flight jobs into a safe recoverable state without duplicate external writes. Verify source read-only access and a new or same-operation target ledger before any target mutation.
- [ ] Run `D:/python_self_agent/venv/Scripts/python.exe -m pytest -q tests/deploy/test_distributed_migration.py --basetemp=.runtime/pytest-distributed-migration`. Repeat migrate/verify against a disposable PostgreSQL/object/Qdrant environment and compare canonical manifests byte-for-byte. Commit code and sanitized example evidence only; never commit copied user data.

## Task 9: Prove full isolated business flow, failures and version rollback

**Files:** Create `deploy/verify_distributed_cutover.py`, `tests/deploy/test_distributed_cutover.py`; complete `deploy/compose.distributed.test.yaml` and an evidence schema under `docs/deployment/distributed-isolated-acceptance.md`.

**Interfaces:** A single runner consumes immutable source backup IDs, immutable candidate and rollback image IDs, pinned PostgreSQL/object/Qdrant test images, and an isolated embedding profile. It creates new containers, network, DB, bucket and vector collection; writes only synthetic post-migration acceptance data. It outputs status, counts, digests, route results, fault results and cleanup result without bodies or secrets.

- [ ] Add a test that rejects missing backup pair, mutable image tag, candidate/rollback image equality, wrong Qdrant collection, existing target volume/bucket and any production mount/network. Run red.
- [ ] Build immutable candidate image **R1** from the post-Task-8 revision, separately from R0. On a restored, frozen production-shaped copy, perform Task 8 migration and verify. Exercise authentication and CSRF; import and document listing; search, citation and QA; source note create/edit/reload; learning plan/task state/reload; report generation/download; delete/fence; API/Worker restarts. Use the actual enabled PostgreSQL, object store and isolated Qdrant; use a non-production approved model credential only where a real answer/embedding call is required. Missing actual model/service access blocks that acceptance gate rather than turning a fake response into release evidence.
- [ ] Fault-inject API death, Worker death, duplicate claim and lease expiry; prove no cross-user read/write, lost acknowledged write or stale terminal commit. After a synthetic new write, stop candidate and start a separately built, older compatible distributed rollback image against the same PostgreSQL, objects and Qdrant. Read and mutate the new record and finish an outstanding job. Do not restore pre-cutover SQLite to claim rollback success.
- [ ] Run `D:/python_self_agent/venv/Scripts/python.exe -m pytest -q tests/deploy/test_distributed_cutover.py --basetemp=.runtime/pytest-distributed-cutover`, then the explicit Docker drill. Require zero unexplained inventory differences and cleanup of all isolated resources. Record all image IDs, schema revision, object service versioning result and test evidence. Commit runner, tests and documentation only.

## Task 10: Close the isolated gate and prepare a separate production decision

**Files:** Create `docs/deployment/distributed-cutover-readiness.md`; update the governing task packets and this plan with verified results. Do not create or execute a production migration operation in this task.

- [ ] Re-run the exact local regression groups for all changed modules, the isolated end-to-end drill and both rollback timings: before opening distributed writes restore the original paired single-instance system; after synthetic distributed writes use only the compatible distributed rollback image. Record what passed and what was not exercised.
- [ ] Verify evidence references immutable image IDs and source/target manifest digests, identifies the actual object store/version behavior, preserves the current production image and paired backup, and includes a stop condition for every unexplained mismatch. Check no secret, copied data, generated Compose, volume, or backup path is staged in Git.
- [ ] Mark the isolated gate complete only if every active business route, all source authority classes and both rollback semantics have passing evidence. If any gate fails, leave production on its current single-instance authority and record the exact blocker. The next plan may then cover production maintenance, cutover, observation and later scale-out using the tested artifacts.

## Review and execution order

Tasks 1–7 are prerequisite implementation, Task 8 is the complete migration, Task 9 is the release gate, and Task 10 records the decision. Complete them serially at backend boundaries; parallel packets are allowed only after Codex proves disjoint file ownership and no uncommitted dependency. The old foundation plan and worktree cannot be marked complete by inheriting test counts. Each task ends with its listed verification and an explicit commit. The entire plan remains unfulfilled until Task 9's actual isolated migration and rollback pass.

**Not in this plan:** production data mutation, production deployment, API replica count increase, Kubernetes, new quotas/fairness/cancellation enhancements, embedding reindex, or GraphRAG feature changes. Production cutover requires a subsequent plan based on the isolation evidence and real infrastructure selection.

## Test execution and packet boundaries

The test paths above are proposed deliverables, not tests already present or already run. Task 2's integration fixture starts the pinned services with a unique Compose project, fresh database/bucket/volumes and generated test-only credentials. It exports endpoints to the test processes directly; it must not load `deploy/.env`. An ordinary regression run may skip unavailable external integration tests, but a task's acceptance run sets `DISTRIBUTED_TEST_REQUIRED=1`; the fixture then fails when Docker/services are unavailable. A skip never satisfies Tasks 2–9.

```powershell
$env:PYTHON_DOTENV_DISABLED = '1'
$env:DISTRIBUTED_TEST_REQUIRED = '1'
D:/python_self_agent/venv/Scripts/python.exe -m pytest -q tests/integration/test_distributed_schema.py --basetemp=.runtime/pytest-distributed-schema
```

All runner-created containers, including one-shot restore helpers, override `com.docker.compose.project` with the unique test project. The existing pinned Qdrant image carries a production project label; accepting inherited labels previously interrupted a production observation. Verify that test containers are absent from the production project filter before starting a drill and that production container IDs remain unchanged after cleanup. The test network is isolated; a live-model test uses an explicit test egress configuration without joining the production network.

Plan review must split Task 4 into separate import/document, QA/deletion, note/projection, learning, and report repository packets. Each packet includes the exact consumed methods and current model types extracted from its callers, SQLite/PostgreSQL parity tests, and its SQL ownership. Task 5's file/object and History/Memory changes require separate packets after the Task 1 path map is complete. Task 6 gets one lifecycle packet plus separate domain-worker packets. Only the composition packet may edit central bootstrap wiring after those prerequisites are verified. Do not mark the broad sections here as independently ready implementation packets.

The following concrete contracts anchor the first review; implementation packets must expand them into complete tests using synthetic fixtures:

```python
# Task 2: reject an incomplete distributed selection before local bootstrap.
with pytest.raises(DeploymentConfigurationError):
    DeploymentSettings.from_env({
        'APP_DATA_MODE': 'distributed',
        'APP_PROCESS_ROLE': 'api',
    }).validate()
```

```python
# Task 3: /session must expose the same CSRF token on both replicas.
login = api_a.post('/api/v1/auth/login', json=synthetic_credentials)
assert login.status_code == 200
api_b.cookies.update(api_a.cookies)
session = api_b.get('/api/v1/auth/session')
assert session.status_code == 200
assert session.json()['csrf_token'] == login.json()['csrf_token']
```

```python
# Task 9: the two image IDs and all gate evidence must be distinct/complete.
assert evidence['candidate_image'] != evidence['rollback_image']
assert evidence['unexplained_differences'] == []
for gate in (
    'source_inventory', 'migration', 'all_business_routes',
    'cross_user_isolation', 'expired_lease_rejection',
    'pre_write_local_restore', 'post_write_distributed_rollback', 'cleanup',
):
    assert evidence['gates'][gate] == 'passed'
```

## Author self-review — 2026-09-26

- Spec coverage: Tasks 1/8 cover complete source ownership and migration; Tasks 2–7 cover shared authority and runtime boundaries; Task 9 covers full business, faults and both rollback semantics; Task 10 is the production decision handoff. Production cutover and later scaling remain subsequent reviewed plans.
- Current-code corrections: sessions are in memory, not SQLite; the CSRF route requires a recoverable session secret; bootstrap creates four embedded Worker families; old PostgreSQL worktree work is incomplete. These facts are reflected above.
- Build order: distributed rollback R0 is built after Task 7; later candidate R1 is built after Task 8. Both are tested over post-migration/new-write data; a renamed or retagged copy is not the second build.
- Outstanding external prerequisites for implementation: test-service image digests, actual object-store capability validation and a separate credential for live provider acceptance. Their absence blocks the relevant integration gate, not plan authorship. No passing implementation or production-migration claim is made by this document.
