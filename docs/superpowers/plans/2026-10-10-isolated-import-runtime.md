# Isolated Authenticated Import Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The repository's `docs/agent-workflow/README.md` controls packet readiness, ownership, review, and handoff; this plan does not itself authorize implementation of a packet.

**Goal:** Deliver an explicitly enabled, isolated API and independent Worker that authenticate two pre-attested tenants, accept immutable uploads, publish with existing PostgreSQL/S3/Qdrant authority, recover interrupted publications, and read committed results across real process restarts.

**Architecture:** Use a separate composition root and a route allowlist. API processes own PostgreSQL sessions, upload admission, and persisted reads; Worker processes own filtered ordinary claims, one-shot publication, bounded heartbeats, and separate recovery. Reuse the accepted C issuance/adapter/proof/recovery protocol without a second terminal writer.

**Tech Stack:** Python, FastAPI, psycopg/PostgreSQL, versioned S3, Qdrant, pytest; Windows PowerShell and the repository venv at `D:\python_self_agent\venv\Scripts\python.exe`.

## Global Constraints

- Base for initial packet: `38bc8bcabd90e23a7518375d7e2087a399f0eec9`; rebase later packets on accepted predecessor commits, never on a dirty file by assumption.
- First milestone uses only two pre-attested isolated accounts in a new disposable schema, bucket and collections. It does not create a production fresh-user baseline, migrate retained users, open other business routes, change ordinary distributed bootstrap, or perform production cutover.
- The pre-existing 15 dirty paths shown in the packet are preserved. In particular, do not touch `app/import_worker.py` or `tests/test_import_worker.py` for this milestone.
- API and Worker processes have separate pools; no local SQLite/JSON/file-lock/business fallback, embedded Worker, public register, cancel/retry, or whole-router mount.
- Runtime defaults: lease 60 seconds, heartbeat 10 seconds, attempt budget 300 seconds, provider call timeout 30 seconds, stop wait 10 seconds. Enforce `heartbeat < lease / 3`, bounded retries, and `provider_timeout <= attempt_budget`.
- Tenant filters act in SQL before candidate selection, locks, claim, or expiry modification. `None` preserves existing repository behavior; a valid empty filter selects nothing; malformed filters fail before any database use.
- Existing task/user lease tuple, user-first lock order, fresh database clock after locks, recovery capture-first protocol, gate/reservations/queue, and proof semantics remain authoritative.
- One Sol High pytest lane runs tests only after root GO. Astra High independently reviews concurrency and consistency; Luna High may record mechanical evidence. Preserve raw commands, actual exit, XML, logs, source and service identity. Historical 637 passes are baseline evidence, never a count of new tests.

## File map and serial dependency graph

All implementation packets are serial (`parallel-safe: false`). Packet 01 is independently useful and has no new-interface dependency; later packets are **draft** until their predecessor is accepted, current code is re-inspected, and self-contained packets are written and reviewed. A task below defines an intended contract, not an already existing interface.

| Task | Files owned by its future packet | Deliverable and dependency |
|---|---|---|
| 01 — tenant SQL selection | Modify `app/postgres_import_leases.py`, `app/import_publication_recovery.py`; optionally create `app/import_tenant_selection.py`; create `tests/test_import_tenant_selection.py` and `tests/integration/test_isolated_import_tenant_selection.py` | SQL prefilter for ordinary, expiry, recovery; no predecessor |
| 02 — trusted configuration/readiness | Create `app/isolated_import_runtime.py`, `tests/test_isolated_import_runtime.py` | Strict opt-in identity and separate ordinary/recovery readiness; after 01 |
| 03 — PG auth and import facades | Create `app/distributed_import_sessions.py`, `app/distributed_import_service.py`, `tests/integration/test_distributed_import_facades.py` | Session-compatible auth, artifact submission, tenant-bound persisted reads and gate mapping; after 02 |
| 04 — independent Worker | Create `app/distributed_import_worker.py`, `scripts/run_isolated_import_worker.py`, `tests/integration/test_distributed_import_worker.py` | One-shot C publication, bounded heartbeat/attempt, independent filtered recovery; after 03 |
| 05 — restricted API | Create `api/isolated_import_app.py`, `tests/integration/test_isolated_import_api.py` | Exact route allowlist, cookie/CSRF, upload/readback; after 04 |
| 06 — real process acceptance | Create `tests/integration/test_isolated_import_processes.py`, `scripts/verify_isolated_import_runtime.ps1`; evidence under a new `docs/agent-work/isolated-import-runtime/` directory | Actual two API/PID Worker restart and fault windows; after 05 |
| 07 — final integration and delivery | Create `docs/agent-workflow/task-packets/2026-10-10-isolated-import-runtime/FINAL_INTEGRATION_REVIEW.md` and new evidence in `docs/agent-work/isolated-import-runtime/` | Independent Spec/Quality/Delivery reviews, combined verification, source-bound branch commit/push; after 06 |

Do not give a future worker only this table. Codex must issue each later packet with verified interfaces, owned files, prerequisites, exact commands, stop rules, and a handoff format after the predecessor lands.

### Task 01: Tenant selection before PostgreSQL authority mutation

**Files:** Modify `app/postgres_import_leases.py:90`, `:468`; modify `app/import_publication_recovery.py:265`; create `tests/test_import_tenant_selection.py` and `tests/integration/test_isolated_import_tenant_selection.py`; optionally create pure `app/import_tenant_selection.py` if sharing validation avoids divergence.

**Interfaces:** Produce `PostgresImportLeaseRepository.claim_next(worker_id, lease_seconds=60, *, allowed_user_ids: frozenset[str] | None = None)`, `recover_expired(limit=100, *, allowed_user_ids: frozenset[str] | None = None)`, and `PostgresImportPublicationRecoveryRepository.claim_next(worker_id: str, lease_seconds: int = 60, *, allowed_user_ids: frozenset[str] | None = None) -> RecoveryClaim | None`. Existing positional callers continue unchanged.

- [ ] Write three red integration cases using the existing disposable `shared_database` and `store` fixtures for ordinary/expiry selection and the existing `memory_publication` fixture for recovery queue evidence. Ordinary claim must skip a disallowed earlier task, expiry must leave a disallowed expired running task/audit untouched, and recovery must skip both a due pending and an expired claimed disallowed queue row. Assert schedule, queue, tokens and audit as well as selected tenant. Add a service-independent transaction-bomb unit test for empty and malformed filters so an unavailable fixture cannot hide validation failures as skips; `None` must retain legacy database behavior and is checked by integration tests.
- [ ] Run `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/test_import_tenant_selection.py tests/integration/test_isolated_import_tenant_selection.py --basetemp=.runtime/pytest-isolated-runtime-p1-red-n1`; expected RED only for newly required selection behavior. Root controls the sole pytest lane.
- [ ] Add a pure validation helper or equivalent in each repository. For a shared helper, the complete contract is:

```python
def validate_allowed_user_ids(value: frozenset[str] | None) -> frozenset[str] | None:
    if value is None:
        return None
    if type(value) is not frozenset or any(
        type(user_id) is not str or not user_id or user_id != user_id.strip()
        for user_id in value
    ):
        raise ValueError('allowed_user_ids must be a frozenset of nonempty user IDs')
    return value
```

- [ ] Bind the same validated set in the initial candidate query and every later row-selection query, using `(%s::text[] is null or user_id = any(%s::text[]))` or equivalent bound SQL. For recovery, parenthesize the pre-existing pending/expired `OR` **before** adding the filter in ranked and final locked candidate predicates. The exact query shape is:

```sql
where q.user_id = any(%s::text[])
  and ((q.state='pending' and q.due_at<=clock_timestamp())
    or (q.state='claimed' and q.claim_expires_at<=clock_timestamp()))
```

Use a separate unfiltered branch for `None` or a nullable bound array; do not interpolate tenant values. Bind `sorted(validated_ids)` as a PostgreSQL text-array parameter (a Python list, not a tuple). Empty `frozenset()` returns without opening a transaction. Recheck candidate `user_id` inside the locked row path to protect against stale candidate selection. Keep fairness ordering and original lock/fresh-clock logic verbatim.
- [ ] Rerun the new file and the three existing selector suites serially. Expected: all pass, zero skipped when `POSTGRES_TEST_URL` names dedicated port 59497 and S3/Qdrant fixture prerequisites are present. Capture actual exit and XML; do not infer from console dots.
- [ ] Before implementation, Codex reviews the packet and Astra independently reviews its SQL/lock contract; only then may the packet become `ready`. After implementation and actual test evidence, Root reviews the diff and Astra independently reviews SQL precedence, lock order, starvation and zero side effects for excluded users before packet status becomes `done`. Commit only exact owned paths after gate.

### Task 02: Explicit runtime identity and distinct readiness

**Files:** Create `app/isolated_import_runtime.py`, `tests/test_isolated_import_runtime.py`.

**Interfaces:** Produce immutable `IsolatedImportSettings.from_env(role: Literal['api','worker'], env=None)`, `trusted_user_ids: frozenset[str]`, `schema`, `bucket`, `rag_collection`, `episode_collection`, `rag_profile`, `episode_profile`, and numeric timing fields; `ProviderReadiness`; `open_isolated_runtime(settings, *, provider_probe=None) -> IsolatedImportRuntime`; `runtime.recovery_ready() -> bool`; `runtime.ordinary_ready(user_id: str) -> bool`. The reviewed packet fixes the full types and environment keys.

- [ ] Write red unit cases for disabled default, missing/contradictory schema, bucket, collection, profile or allowlist, empty allowlist, unsafe retained/protected resource IDs, timeout inequality, and API versus Worker resource ownership. Assert configuration errors before network calls.
- [ ] Run `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/test_isolated_import_runtime.py --basetemp=.pytest-tmp-isolated-runtime-p2-n1`; expected RED on missing isolated module only.
- [ ] Implement immutable settings from explicitly named isolated environment keys and reject ordinary deployment identity reuse. Preserve secrets in existing environment/config plumbing and scrub `repr`/public errors. The startup split must follow this actual decision table:

```python
database_identity_ok = check_schema_identity(settings.schema)
recovery_ready = database_identity_ok
ordinary_ready = (
    recovery_ready and versioned_object_store_ready()
    and exact_profile_provider_report_ready(provider_probe)
    and attested_paired_baseline(user_id, settings.rag_profile, settings.episode_profile)
)
```

These names describe the decision, not existing repository APIs. Task 02 supplies no embedding engine: a missing provider hook makes ordinary readiness false. Tasks 03/04 must supply a real provider adapter proving availability and exact profiles before ordinary admission. A synthetic hook verifies only the readiness composition contract. `S3ObjectStore.check_ready()` returns None on success, so its successful call must be mapped to a boolean rather than used directly in an `and` expression.

The paired baseline proof reads published heads/receipts, History witness, and Memory for the configured identity; missing is an error, never inferred empty. Capture complete per-user database authority in short single-statement snapshots before and after external reads; validate final snapshot/document pairing itself and bind public episode membership/payloads to final receipt data. Versions alone and equal invalid before/after states cannot pass. The packet requires mutation and ABA tests plus an unfiltered full new-test-file run. Recovery remains available if ordinary providers or baseline are unavailable.
- [ ] Run focused tests and a real-schema readiness integration test added to this task's owned test file; require active isolated PG identity and do not touch protected resources.

### Task 03: Shared auth and persisted import facades

**Files:** Create `app/distributed_import_sessions.py`, `app/distributed_import_service.py`, `tests/integration/test_distributed_import_facades.py`.

**Interfaces:** Produce a session facade with `login(username: str, password: str) -> str`, `get_session(token: str | None) -> UserSession`, `validate_csrf(token: str | None, csrf_token: str | None) -> UserSession`, `logout(token: str | None) -> None`; an import facade with `submit_uploads(session: UserSession, uploads: list[ImportUpload]) -> ImportBatchSummary`, `list_batches(user_id: str, ...)`, `get_batch(user_id: str, batch_id: str)`, `get_task(user_id: str, task_id: str)`, and `read_published_document(user_id: str, document_id: str)`; typed fail-closed errors distinguish 403, 409 `needs_reconciliation`/`retryable=false`, and 503.

- [ ] Write red two-pool tests: same cookie crosses pools, logout invalidates both, disabled/out-of-allowlist user refuses, CSRF refuses before `ImportUpload` stream consumption, tenant A cannot read tenant B task/document, unresolved gate maps only after authoritative per-user gate query, and non-gate `PublicationEvidenceError` maps to 503.
- [ ] Run `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/integration/test_distributed_import_facades.py --basetemp=.pytest-tmp-isolated-runtime-p3-n1`; expected RED on new facade imports.
- [ ] Compose `PostgresAuthService` and `PostgresSessionRepository`, then `PostgresImportArtifactService.submit_uploads` and `PostgresImportTaskRepository` user-scoped reads. The core admission order is:

```python
session = sessions.validate_csrf(cookie_token, csrf_token)
require_trusted_active_user(session.user_id, settings.trusted_user_ids)
require_ordinary_ready(session.user_id)
summary = artifacts.submit_uploads(session.user_id, uploads)
```

Do not expose `register` and do not use `SessionRegistry` local runtime. Gate recognition uses a fresh authoritative `user_publication_gates` read for that exact session user; never classify all evidence exceptions as gates. Readback verifies History/reference/witness, pinned source and published generation at the tenant scope before returning sanitized DTO data.
- [ ] Rerun focused tests and existing PG auth/artifact suites; preserve old return defaults and no local fallback.

### Task 04: Single publication Worker and separate recovery loop

**Files:** Create `app/distributed_import_worker.py`, `scripts/run_isolated_import_worker.py`, `tests/integration/test_distributed_import_worker.py`.

**Interfaces:** Produce `IsolatedImportWorker.run_one_ordinary() -> bool`, `run_one_recovery() -> RecoveryOutcome | None`, `run_forever(stop_event) -> WorkerExit`, and CLI `main() -> int`. Constructor receives trusted runtime, `PostgresImportLeaseRepository`, `PostgresImportPublicationRecoveryRepository`, artifact service, `ImportMemoryPublicationService`, embedding runtime, and exact scopes. It always passes `settings.trusted_user_ids` into all three selectors.

- [ ] Write red tests for source verification, real text/PDF parse, matching RAG and episode profile, exactly one original live issuance and exactly one `ImportPublicationWorkerAdapter(enabled=True).run_once(live)`, ordinary pre-intent failure, ambiguous issuance, held Unknown, success versus lease-lost heartbeat, expiry/recovery, and stop timeout with a still-running provider.
- [ ] Run `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/integration/test_distributed_import_worker.py --basetemp=.pytest-tmp-isolated-runtime-p4-n1`; expected RED on missing Worker.
- [ ] Keep the publication path singular:

```python
attempt = leases.claim_next(worker_id, lease_seconds=60,
                            allowed_user_ids=settings.trusted_user_ids)
source = artifacts.read_source_bytes(attempt.task.user_id, attempt.task.task_id)
points = prepare_import_document(
    source_bytes=source, task_id=attempt.task.task_id,
    user_id=attempt.task.user_id, document_id=attempt.task.document_id,
    original_name=attempt.task.original_name, suffix=attempt.task.file_suffix,
    scope=trusted_rag_scope(attempt.task.user_id), embedding_runtime=embedding_runtime)
live = publication._issue_live_publication(
    trusted_rag_scope(attempt.task.user_id), attempt, points,
    event_vector=embed_episode_event(attempt, episode_profile),
    event_profile=episode_profile)
disposition = ImportPublicationWorkerAdapter(publication, enabled=True).run_once(live)
```

The actual event text and profile must match C's current contract; no fixed 4D test vector. Never call ordinary `complete`/`mark_succeeded` after adapter. Heartbeat renews both task/user authority on the original attempt; its returned snapshot does not replace `live`. Use finite call timeouts, finite retries and monotonic 300-second attempt budget. On Unknown or ambiguous issuance, stop renewal and let authoritative recovery decide. On pre-intent deterministic parse failure use existing `leases.fail`; never use it after ambiguous issuance. A late provider thread prevents clean exit assertion.
- [ ] Implement recovery separately: `leases.recover_expired(..., allowed_user_ids=trusted)` then `recovery.claim_next(..., allowed_user_ids=trusted)` and `prove_or_hold`; renew only recovery claim when needed. Preserve queue capture, original lease expiry wait, retry cap/manual hold, and permanent dual-UUID revocation on qualified abandon. Recovery runs when ordinary readiness is false.
- [ ] Rerun focused tests and existing publication proof/recovery suites; Astra independently inspects issuance identity, all stopped/late paths, queue locks, and stale Worker rejection.

### Task 05: Restricted HTTP composition

**Files:** Create `api/isolated_import_app.py`, `tests/integration/test_isolated_import_api.py`.

**Interfaces:** Produce `create_isolated_import_app(settings: IsolatedImportSettings | None = None) -> FastAPI`. The route table is exactly `/healthz`, `/api/v1/auth/login`, `/api/v1/auth/session`, `/api/v1/auth/logout`, `/api/v1/imports` POST/GET, `/api/v1/imports/{batch_id}` GET as the existing DTO requires, `/api/v1/imports/tasks/{task_id}` GET, and one explicit `/api/v1/documents/{document_id}` GET readback. The future packet must verify existing path shapes before locking them.

- [ ] Write red API tests that enumerate the OpenAPI route table and send register, cancel, retry, QA, notes, learning, reports, delete, search, and `/legacy/` requests. Require 404/405. Test cookie on a second API instance, CSRF before file read, logout invalidation, tenant-bound task/document, 409 gate and 503 baseline/provider errors.
- [ ] Run `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/integration/test_isolated_import_api.py --basetemp=.pytest-tmp-isolated-runtime-p5-n1`; expected RED on missing factory.
- [ ] Build a fresh `FastAPI` lifespan with API-role runtime only; mount each allowlisted endpoint explicitly. Route handler shape:

```python
@router.post('/api/v1/imports', status_code=202)
def submit(request: Request, files: list[UploadFile] = File(...)):
    session = sessions.validate_csrf(cookie(request), csrf(request))
    uploads = [ImportUpload(item.filename or '', item.file) for item in files]
    return import_batch_response(imports.submit_uploads(session, uploads))
```

Resolve validation before reading bytes or issuing object writes; public errors omit SQL, source text and credentials. Do not include existing `api.app.create_api_app`, full `auth.router`, or full `imports.router` because they register excluded routes.
- [ ] Run focused tests, route-table check, and ordinary local API regression; verify `ApplicationServices.create()` still rejects ordinary distributed mode.

### Task 06: Isolated actual-process acceptance

**Files:** Create `tests/integration/test_isolated_import_processes.py`, `scripts/verify_isolated_import_runtime.ps1`; create evidence files under `docs/agent-work/isolated-import-runtime/` only.

- [ ] Provision a **new** disposable PostgreSQL schema, versioned S3 bucket, and distinct RAG/episode collections. Attest two accounts with real compatible empty History/Memory, heads/receipts and witness using existing services; report the fixture identity and never import product local runtime. Reject absent or mismatched baselines.
- [ ] Run two API PIDs and one independent Worker PID against the same exact resources; exercise login/cookie cross API, CSRF refusal, two-tenant isolation, text and PDF upload, committed task/document/History/episode readback, and provider identity. The script records PIDs, process command lines, source hash/commit, schema/bucket/collection IDs, and actual exit codes.
- [ ] Persist upload, kill the submitting API, let Worker finish, start a replacement API and read back. Kill a Worker after claim, start another PID, then resume old PID and prove old tuple/vector bytes cannot become visible. Inject claim-before-intent, intent-commit-before-return, document put, each seal, terminal-commit-before-response, Unknown, held, recovery pause, ack/abandon, fresh attempt, heartbeat versus terminal/expiry/recovery lock waits, and two-user fairness. Existing authority test seams may be used; test process death must be genuine for the restart cases.
- [ ] Run `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/integration/test_isolated_import_processes.py --basetemp=.pytest-tmp-isolated-runtime-p6-n1 --junitxml=.runtime/isolated-import-runtime/processes-n1.xml` only after root's sole-lane GO. Preserve raw command/output/exit and resource identities. No test result is accepted from just a started process, health response, or reconstructed count.

### Task 07: Combined review and branch delivery

**Files:** Create `docs/agent-workflow/task-packets/2026-10-10-isolated-import-runtime/FINAL_INTEGRATION_REVIEW.md`; append evidence in `docs/agent-work/isolated-import-runtime/`.

- [ ] Freeze exact source bytes and run unfiltered focused/combined regression commands with unique basetemps and raw XML/log/exit; use one pytest lane. Include old local tests and ordinary distributed refusal. The accepted old 637 baseline is compared by test identity, not added to new counts.
- [ ] Astra independently reviews tenant SQL, user/task lease, issuance/adapter single execution, Unknown, stale Worker/vector fence, cross-store consistency and readback. Sol resolves concrete findings in an owned corrective packet; Luna records process/log/resource evidence. Codex performs required final combined diff review against every packet, including route allowlist and config; verdict is `accepted`, `changes-required`, or `blocked`.
- [ ] After all gates pass, commit exact intended files and push the authorized branch; record actual commit and remote ancestry. First-milestone acceptance remains distinct from fresh-user initializer, full business migration, retained-source migration and different-image rollback, each of which needs a later independent plan and production authorization.

## Self-review and scope boundary

The plan covers every section of `docs/superpowers/specs/2026-10-10-isolated-import-runtime-design.md`: separate composition/readiness, SQL allowlist, auth/import/readback, one-shot Worker, bounded lease/recovery, attested fixture, real process interruptions and evidence. The only implementation packet being prepared now is 01; future signatures and route shapes in Tasks 02–06 are proposed contracts and must be rechecked against the accepted predecessor before packet readiness. No production cutover or parent-task completion follows from this plan.
