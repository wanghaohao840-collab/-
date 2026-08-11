# Batch Import Operations and Multiprocess Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade durable batch imports with safe observability, lease-based health and recovery, staging quotas and governance, cooperative pause/cancel, and single-host external Worker processes while preserving the accepted multi-user data-isolation contract.

**Architecture:** Keep SQLite and the existing user-scoped filesystem as the single-host control and data planes. Add versioned schema migrations and focused repositories for metrics, leases, staging, controls, and maintenance; keep `ImportTaskRepository` authoritative for task transitions. Final deployment supports an embedded four-thread mode and an external one-to-four-process mode, with SQLite fencing plus a per-user kernel file lock and Runtime version reload preventing cross-process stale writes.

**Tech Stack:** Python 3.12, SQLite WAL, Gradio 6.19.0, stdlib `threading`/`multiprocessing`/`msvcrt`/`fcntl`, pytest 8.4.1, existing JSON/Qdrant/Neo4j adapters.

## Program Decomposition

This plan is intentionally sequential. It contains seven independently shippable phases and eighteen reviewer-sized tasks. A phase gate runs the exact full suite and receives an independent spec/code review before the next phase starts. Phase 4 records the embedded baseline before task controls and external processes change runtime behavior; Phase 7 repeats the same workload in external mode.

## Global Constraints

- Read `PROJECT_KNOWLEDGE.md` before every implementation task; current code and tests override historical notes.
- Preserve `user_id`, `document_id`, RAG namespace, source page, citation, History, Memory, report, task, staging, and filesystem isolation.
- Preserve unrelated dirty files. Stage only files named by the active task.
- Use `D:\python_self_agent\venv\Scripts\python.exe` in an isolated execution worktree; user-facing commands remain exactly `.\venv\Scripts\python.exe -m pytest -q` and `.\venv\Scripts\python.exe .\ui\gradio_app.py`.
- Do not add Redis, Celery, RQ, PostgreSQL, cloud object storage, hard thread termination, priorities, or cross-host execution.
- Default mode is `embedded`; `external` is explicit and never starts an embedded pool.
- Default Worker count is 4; embedded means four threads and external means four capacity-one processes.
- Heartbeat/stale/lease defaults are exactly 5/30/90 seconds.
- Staging defaults are 2 GiB per user, 10 GiB global, and 1 GiB minimum free disk.
- Retention is implemented but disabled by default; when enabled its default age is 30 days and dry-run uses the identical selection predicate as execution.
- Logs and CLI JSON never contain raw usernames, user IDs, original filenames, paths, errors, tracebacks, tokens, credentials, or API keys.
- New acceptance tests are deterministic and offline; they may not be skipped.
- Existing explicitly live Qdrant/Neo4j tests may retain their documented skips.
- The ignored fixture `evals/data/multi_document_qa.json` must be copied into the isolated worktree when it exists in the source workspace; never stage that copy.
- Every phase ends with `compileall`, `git diff --check`, exact full pytest exit 0, independent review with zero Critical/Important findings, a scoped commit, and a phase report.

## File and Responsibility Map

- `app/import_config.py`: immutable environment configuration and validation.
- `app/import_models.py`: public task, source, health, metrics, control, lease, and quota records.
- `app/database.py`: connection pragmas and fresh final schema.
- `app/import_migrations.py`: versions 1 through 5, transactional table rebuilds, and upgrade verification.
- `app/import_repository.py`: user-scoped task/batch reads and fenced task transitions.
- `app/import_metrics.py`: read-only user/global aggregates and stable health reason predicates.
- `app/import_events.py`: allowlisted structured lifecycle events and safe field filtering.
- `app/import_leases.py`: Worker registry, task claims, heartbeats, generations, and recovery.
- `app/user_data_coordination.py`: reentrant process lock, kernel file lock, SQLite user lease, and Runtime version protocol.
- `app/import_staging.py`: quota reservations, bounded copy, source lifecycle, and byte accounting.
- `app/import_maintenance.py`: source deletion intent, orphan audit, retention, compensation retry, and periodic maintenance.
- `app/import_controls.py`: control token, control outcomes, commit seal, and compensation records.
- `app/import_service.py`: authenticated submission, queries, retries, controls, quota, and source governance.
- `app/import_worker.py`: embedded scheduler and control/lease-aware runner.
- `app/import_worker_main.py`: external Worker process launcher and bounded shutdown.
- `app/import_ops.py`: local JSON CLI for health, metrics, stuck, quota, audit, and cleanup.
- `app/runtime.py`: version-aware Runtime reload and coordinator injection.
- `app/storage.py`: safe lock, staging `.part`, final source, and audit path resolution.
- `assistants/pdf_learning_assistant.py`: explicit control checkpoints, commit seal, and task-owned compensation.
- `hello_agents/memory/rag/prepare.py`: non-swallowed control callback at parsing/chunking/embedding boundaries.
- `hello_agents/memory/rag/pipeline.py`: unique temporary files, atomic replace, reload, and JSON external capability.
- `hello_agents/memory/rag/qdrant_pipeline.py`: reload/capability contract without weakening payload scoping.
- `hello_agents/memory/graph/service.py`: Neo4j external capability under user coordination.
- `ui/gradio_app.py`: user-only metrics, quota, controls, and governance; mode-aware startup.
- `deploy/healthcheck.py`, `deploy/entrypoint.sh`, `compose.yaml`, `deploy/.env.example`: external process and health wiring.
- `scripts/import_capacity.py`: deterministic correctness and soak runner.
- `tests/`: focused schema, repository, service, worker, UI, process, capacity, and deployment contracts.
- `docs/superpowers/reports/`: tracked phase reports and final review; never store uploaded content or secrets.

---

## Phase 1: Read-only observability baseline

### Task 1: Import configuration and operational models

**Files:**
- Create: `app/import_config.py`
- Modify: `app/import_models.py`
- Create: `tests/test_import_config.py`
- Modify: `tests/test_import_models.py`

**Interfaces:**
- Produces `ImportSettings.from_env(env: Mapping[str, str] | None = None) -> ImportSettings`.
- Produces `ImportOperationalMetrics`, `ImportWorkerHealth`, `ImportHealthSnapshot`, `ImportQuotaSnapshot`, and `HealthLevel`.
- Phase 1 reads settings but does not change task statuses or database schema.

- [ ] **Step 1: Write failing configuration and model tests**

```python
def test_import_settings_defaults():
    settings = ImportSettings.from_env({})
    assert settings.worker_mode == "embedded"
    assert settings.worker_count == 4
    assert settings.heartbeat_interval_seconds == 5
    assert settings.health_stale_seconds == 30
    assert settings.lease_ttl_seconds == 90
    assert settings.user_quota_bytes == 2 * 1024**3
    assert settings.global_quota_bytes == 10 * 1024**3
    assert settings.min_free_bytes == 1024**3
    assert settings.retention_enabled is False


def test_import_settings_rejects_invalid_threshold_order():
    with pytest.raises(ValueError, match="heartbeat.*health.*lease"):
        ImportSettings.from_env({"IMPORT_HEALTH_STALE_SECONDS": "5"})
```

- [ ] **Step 2: Run the red tests**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_import_config.py tests/test_import_models.py -q`

Expected: FAIL because `app.import_config` and operational records do not exist.

- [ ] **Step 3: Implement immutable validated settings**

Use a frozen dataclass with these exact environment names and defaults:

```python
@dataclass(frozen=True)
class ImportSettings:
    worker_mode: Literal["embedded", "external"] = "embedded"
    worker_count: int = 4
    heartbeat_interval_seconds: int = 5
    health_stale_seconds: int = 30
    lease_ttl_seconds: int = 90
    recovery_grace_seconds: int = 30
    queue_dispatch_delay_seconds: int = 180
    cleanup_stale_seconds: int = 300
    ledger_audit_interval_seconds: int = 300
    user_lock_timeout_seconds: int = 30
    sqlite_busy_timeout_ms: int = 5000
    sqlite_write_retries: int = 5
    user_quota_bytes: int = 2147483648
    global_quota_bytes: int = 10737418240
    min_free_bytes: int = 1073741824
    retention_enabled: bool = False
    retention_days: int = 30
    benchmark_timeout_seconds: int = 300
```

Validation accepts Worker counts 1 through 4, positive numeric values, `heartbeat < stale < lease`, and lowercase boolean strings `true/false/1/0`. Invalid values raise `ValueError` naming only the configuration key.

- [ ] **Step 4: Add immutable metric and health records**

Define typed frozen records with integer counters and UTC age seconds. `ImportHealthSnapshot` contains `level`, sorted unique `reason_codes`, `metrics`, and `observed_at`; no record contains filenames, paths, raw errors, or raw user IDs.

- [ ] **Step 5: Run tests and commit**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_import_config.py tests/test_import_models.py -q`

Expected: PASS.

Commit:

```powershell
git add app/import_config.py app/import_models.py tests/test_import_config.py tests/test_import_models.py
git commit -m "feat: define import operations settings and models"
```

### Task 2: Read-only metrics and safe lifecycle events

**Files:**
- Create: `app/import_metrics.py`
- Create: `app/import_events.py`
- Modify: `app/import_repository.py`
- Modify: `app/import_service.py`
- Modify: `app/import_worker.py`
- Create: `tests/test_import_metrics.py`
- Create: `tests/test_import_events.py`

**Interfaces:**
- Produces `ImportMetricsRepository.user_snapshot(user_id, now) -> ImportOperationalMetrics`.
- Produces `ImportMetricsRepository.global_snapshot(now) -> ImportOperationalMetrics`.
- Produces `emit_import_event(logger, event, **fields) -> None`.
- Produces `ImportTaskService.get_user_metrics(session_token) -> ImportOperationalMetrics`.

- [ ] **Step 1: Write failing isolation, time, and redaction tests**

```python
def test_user_metrics_are_scoped_and_use_injected_time(repo, seeded_tasks):
    metrics = repo.user_snapshot("user-a", now="2026-08-11T00:03:00Z")
    assert metrics.queued == 1
    assert metrics.oldest_queued_age_seconds == 180
    assert "user-b" not in repr(metrics)


def test_event_filter_drops_sensitive_fields(caplog):
    emit_import_event(
        logging.getLogger("imports"),
        "task_failed",
        task_id="opaque-task",
        error_code="unexpected_error",
        original_name="secret.pdf",
        staged_path="D:/private/secret.pdf",
        error_summary="api_key=secret",
    )
    rendered = caplog.text
    assert "task_failed" in rendered
    assert "secret.pdf" not in rendered
    assert "api_key" not in rendered
```

- [ ] **Step 2: Run the red tests**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_import_metrics.py tests/test_import_events.py -q`

Expected: FAIL because the metrics and event modules do not exist.

- [ ] **Step 3: Implement read-only aggregate queries**

Use parameter-bound SQL. User snapshots always filter `user_id = ?`; global snapshots are never exposed through authenticated user service methods. Aggregate current status counts, oldest eligible queue age, running age, terminal counts in the previous hour, and allowlisted `error_code` counts. Inject `now`; do not call wall-clock time inside query formatting.

- [ ] **Step 4: Implement structured event allowlisting**

The event allowlist is:

```python
ALLOWED_IMPORT_EVENTS = frozenset({
    "batch_accepted", "batch_rejected", "reservation_created",
    "reservation_released", "reservation_expired", "task_claimed",
    "task_retry_scheduled", "task_succeeded", "task_failed",
    "task_paused", "task_cancelled", "task_resumed",
    "lease_renewed", "lease_expired", "lease_taken_over", "lease_fenced",
    "staging_cleanup_pending", "staging_cleanup_completed",
    "worker_started", "worker_stopping", "worker_stopped", "worker_unhealthy",
})

ALLOWED_EVENT_FIELDS = frozenset({
    "event", "batch_id", "task_id", "worker_id", "user_ref",
    "status", "stage", "error_code", "count", "bytes", "duration_ms",
    "claim_generation", "reason_code",
})
```

Reject unknown event names during tests; production emission catches logging failures and never changes task results. Convert the high-entropy UUID user ID to `hashlib.sha256(("import-log-v1:" + user_id).encode("utf-8")).hexdigest()[:16]`, and never log the raw ID.

- [ ] **Step 5: Wire lifecycle events without changing transitions**

Emit stable events at batch acceptance/rejection, task claim, retry scheduling, success, failure, recovery, pool start, and pool stop. Pass only opaque IDs and allowlisted codes; replace raw `logger.exception` on user-derived failures with a sanitized event and a constant developer message.

- [ ] **Step 6: Run focused regression and commit**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_import_metrics.py tests/test_import_events.py tests/test_import_repository.py tests/test_import_service.py tests/test_import_worker.py tests/test_import_error_sanitization.py -q`

Expected: PASS.

Commit:

```powershell
git add app/import_metrics.py app/import_events.py app/import_repository.py app/import_service.py app/import_worker.py tests/test_import_metrics.py tests/test_import_events.py
git commit -m "feat: add safe import metrics and lifecycle events"
```

### Task 3: Operations CLI, user summary, and Phase 1 gate

**Files:**
- Create: `app/import_ops.py`
- Modify: `ui/gradio_app.py`
- Create: `tests/test_import_ops.py`
- Modify: `tests/ui/test_import_handlers.py`
- Create: `docs/superpowers/reports/2026-08-11-import-operations-phase-1.md`

**Interfaces:**
- Produces `python -m app.import_ops metrics --json` and `health --json`.
- Phase 1 health is explicitly process-local and does not claim durable lease support.
- Produces `format_user_import_metrics(metrics) -> str` for the authenticated user panel.

- [ ] **Step 1: Write failing CLI and UI authorization tests**

Assert versioned JSON, stable sorted keys, no raw user IDs, no filenames/paths, invalid command exit 2, and forged/expired tokens rejected before metrics lookup. Assert the user panel cannot call `global_snapshot()`.

- [ ] **Step 2: Run red tests**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_import_ops.py tests/ui/test_import_handlers.py -q`

Expected: FAIL because the CLI and user summary do not exist.

- [ ] **Step 3: Implement the Phase 1 CLI and UI summary**

`main(argv=None) -> int` accepts only `health|metrics` plus `--json` and `--data-root`. The JSON envelope is:

```python
{
    "schema_version": 1,
    "command": command,
    "status": "ok",
    "observed_at": observed_at,
    "data": payload,
}
```

The Gradio summary shows only counts, age, recent outcome, and quota placeholder text; it never receives the global repository object.

- [ ] **Step 4: Run the Phase 1 gate**

Run focused:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_import_config.py tests/test_import_models.py tests/test_import_metrics.py tests/test_import_events.py tests/test_import_ops.py tests/test_import_repository.py tests/test_import_service.py tests/test_import_worker.py tests/test_import_error_sanitization.py tests/ui/test_import_handlers.py -q
```

Run static and exact full:

```powershell
.\venv\Scripts\python.exe -m compileall app assistants hello_agents ui
git diff --check
.\venv\Scripts\python.exe -m pytest -q
```

Expected: all commands exit 0. Record exact counts, skips, warnings, commit hashes, and review result in the phase report.

- [ ] **Step 5: Independent review and phase commit**

Review spec compliance and code quality. Fix all Critical/Important findings and rerun the same gate. Commit only the report and any reviewed corrections:

```powershell
git add docs/superpowers/reports/2026-08-11-import-operations-phase-1.md
git commit -m "docs: record import observability gate"
```

---

## Phase 2: Health, heartbeats, leases, and stuck recovery

### Task 4: Version 2 migrations and SQLite connection policy

**Files:**
- Create: `app/import_migrations.py`
- Modify: `app/database.py`
- Create: `tests/test_import_migrations.py`
- Modify: `tests/test_import_repository.py`

**Interfaces:**
- Produces `migrate_import_schema(db_path) -> int`, returning final version.
- Version 1 is the accepted five-state schema; version 2 adds Worker/lease/attempt/timing structures without changing statuses.
- `connect()` enables foreign keys, WAL, and configured busy timeout.

- [ ] **Step 1: Write failing fresh, legacy, repeat, and rollback tests**

Create a true version-1 fixture, insert queued/running/failed rows, migrate, and assert rows and indexes survive. Inject failure after new-table creation and assert version 1 remains readable with no partial tables. Assert repeated migration returns 2 without data changes.

- [ ] **Step 2: Run red migration tests**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_import_migrations.py tests/test_import_repository.py -q`

Expected: FAIL because schema migrations are not versioned.

- [ ] **Step 3: Implement migration ledger and version 2 schema**

Create `app_schema_migrations`, `import_workers`, `import_task_attempts`, and `import_task_stage_timings`; add nullable task claim/heartbeat columns. All DDL and data validation run inside one explicit transaction. Validate row counts, `foreign_key_check`, index presence, and version before commit.

- [ ] **Step 4: Configure SQLite connections**

Every connection executes:

```sql
pragma foreign_keys = on;
pragma journal_mode = wal;
pragma synchronous = normal;
pragma busy_timeout = 5000;
```

Write retries are bounded by `IMPORT_SQLITE_WRITE_RETRIES=5`; do not retry integrity, transition, or authorization failures.

- [ ] **Step 5: Run tests and commit**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_import_migrations.py tests/test_import_repository.py tests/test_p0_data_integrity.py -q`

Expected: PASS.

Commit version 2 migration and tests.

### Task 5: Worker registry, fenced claims, and independent heartbeats

**Files:**
- Create: `app/import_leases.py`
- Modify: `app/import_repository.py`
- Modify: `app/import_worker.py`
- Create: `tests/test_import_leases.py`
- Modify: `tests/test_import_worker.py`

**Interfaces:**
- Produces `ImportLeaseRepository.register_worker(worker_id, mode, capacity, now)`.
- Produces `claim_next(worker_id, blocked_user_ids, now) -> ImportTaskRecord | None` with token/generation.
- Produces `renew_claim(user_id, task_id, claim_token, generation, now) -> bool`.
- Produces `recover_expired(now) -> RecoveryResult`.

- [ ] **Step 1: Write failing claim, heartbeat, and fencing tests**

Cover two repositories claiming concurrently, 5-second renewals while runner progress is silent, old-token progress/terminal rejection after generation changes, same-user uniqueness, and cross-user capacity.

- [ ] **Step 2: Run red tests**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_import_leases.py tests/test_import_worker.py -q`

Expected: FAIL because durable claims do not exist.

- [ ] **Step 3: Implement fenced claim conditions**

Every claim-owned update includes:

```sql
where id = :task_id
  and user_id = :user_id
  and status = 'running'
  and claim_token = :claim_token
  and claim_generation = :generation
  and lease_expires_at > :now
```

Generate claim tokens with `secrets.token_urlsafe(32)`. Increment generation atomically. Never return tokens to UI or logs.

- [ ] **Step 4: Add a dedicated heartbeat thread**

The heartbeat thread renews Worker registration and all claims owned by the process every five seconds. Parsing, embedding, and progress callbacks are not heartbeat sources. On stop, no new claims are taken; in-flight tasks receive bounded graceful shutdown.

- [ ] **Step 5: Replace unconditional startup recovery**

Remove recovery of every `running` row. Recover only expired claims. Existing version-1 running rows migrate with an expired lease and retain current staged-file recovery behavior.

- [ ] **Step 6: Run focused tests and commit**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_import_leases.py tests/test_import_worker.py tests/test_import_repository.py tests/test_runtime_import_leases.py -q`

Expected: PASS.

Commit the lease repository, Worker changes, and tests.

### Task 6: Durable health predicates, stuck CLI, and Phase 2 gate

**Files:**
- Modify: `app/import_metrics.py`
- Modify: `app/import_ops.py`
- Modify: `deploy/healthcheck.py`
- Create: `tests/test_import_health.py`
- Modify: `tests/deploy/test_smoke_test.py`
- Create: `docs/superpowers/reports/2026-08-11-import-operations-phase-2.md`

**Interfaces:**
- Produces exact `healthy|degraded|unhealthy` snapshots and reason codes from the specification.
- Adds `python -m app.import_ops stuck --json`.
- Deployment health fails only for `unhealthy`.

- [ ] **Step 1: Write boundary tests with injected UTC time**

Verify exact 29/30/89/90-second heartbeat boundaries, 180-second dispatch delay, capacity loss, expired lease plus 30-second recovery grace, SQLite failure, and ordinary document failure not affecting global health.

- [ ] **Step 2: Run red tests**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_import_health.py tests/test_import_ops.py tests/deploy/test_smoke_test.py -q`

Expected: FAIL because durable predicates and stuck output are absent.

- [ ] **Step 3: Implement predicates and CLI exit codes**

`health --json` exits 0 for healthy/degraded, 1 for unhealthy, and 2 for command/usage failure. `stuck` classifies suspected, expired, and blocked without exposing user IDs or paths.

- [ ] **Step 4: Run Phase 2 gate and review**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_import_migrations.py tests/test_import_leases.py tests/test_import_health.py tests/test_import_ops.py tests/test_import_worker.py tests/test_import_repository.py tests/test_runtime_import_leases.py tests/deploy/test_smoke_test.py -q
.\venv\Scripts\python.exe -m compileall app assistants hello_agents ui
git diff --check
.\venv\Scripts\python.exe -m pytest -q
```

Expected: every command exits 0. Independently review migrations, time boundaries, fencing, shutdown, and recovery. Fix every Critical/Important issue, rerun the same commands, and record evidence.

- [ ] **Step 5: Commit the Phase 2 report**

Commit `docs/superpowers/reports/2026-08-11-import-operations-phase-2.md` after the gate is green.

---

## Phase 3: Staging quota and failed-source governance

### Task 7: Version 3 source and reservation ledger

**Files:**
- Modify: `app/import_migrations.py`
- Modify: `app/import_models.py`
- Create: `app/import_staging.py`
- Create: `tests/test_import_staging_schema.py`
- Create: `tests/test_import_staging.py`

**Interfaces:**
- Produces `ImportReservationItemCreate(task_id, document_id, suffix, declared_bytes, part_relative_path, final_relative_path)`.
- Produces `ImportStagingRepository.reserve_batch(user_id: str, batch_id: str, items: Sequence[ImportReservationItemCreate], now: str) -> ImportStagingReservation`.
- Produces `record_copy_progress(reservation_id: str, task_id: str, copied_bytes: int, state: ReservationItemState, now: str) -> None`.
- Produces `usage_snapshot(user_id: str | None, now: str) -> ImportQuotaSnapshot` and `begin_source_cleanup(user_id: str, task_id: str, reason: CleanupReason, now: str) -> ImportStagingSource`.
- Produces `ImportTaskRepository.create_batch_from_reservation(reservation_id: str, tasks: Sequence[ImportTaskCreate], now: str) -> ImportBatchSummary` for the single task/source/reservation settlement transaction.
- Version 3 adds source, reservation header, and reservation item tables with composite user constraints.

- [ ] **Step 1: Write failing migration and ledger tests**

Cover fresh version 3, version 2 upgrade, over-quota legacy data, cross-user FK rejection, concurrent reservations, and exact byte accounting for `available` and `cleanup_pending`.

- [ ] **Step 2: Run red tests**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_import_staging_schema.py tests/test_import_staging.py -q`

Expected: FAIL because version 3 and staging repositories do not exist.

- [ ] **Step 3: Implement version 3 schema and atomic reservation**

Use one `BEGIN IMMEDIATE` transaction to sum user/global sources plus active reservations, reject limits, and insert a reservation header plus pre-generated item rows containing both `.part` and final relative paths. Existing over-quota users remain readable but cannot reserve more bytes.

- [ ] **Step 4: Implement the deletion-intent protocol**

All deletion paths perform `available -> cleanup_pending`, commit, safe unlink, then `cleanup_pending -> deleted`. Missing files after an expressed deletion intent count as successful idempotent deletion; unexpected absence from `available` becomes `missing`.

- [ ] **Step 5: Run tests and commit**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_import_staging_schema.py tests/test_import_staging.py tests/test_staged_path_safety.py -q`

Expected: PASS.

Commit version 3 and staging ledger code.

### Task 8: Bounded streaming submission and atomic quota settlement

**Files:**
- Modify: `app/storage.py`
- Modify: `app/import_service.py`
- Modify: `app/import_repository.py`
- Modify: `tests/test_import_service.py`
- Modify: `tests/test_staged_path_safety.py`

**Interfaces:**
- Submission reserves before copy, streams in bounded chunks, checks actual bytes/free disk, atomically publishes, and notifies only after task/source commit.

- [ ] **Step 1: Write failing TOCTOU and crash-window tests**

Cover source growth after `stat`, quota crossing during copy, free disk falling below 1 GiB, rename-before-source crash, database failure after rename, partial batch copy, and cleanup failure retaining reservation/accounting.

- [ ] **Step 2: Run red tests**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_import_service.py tests/test_staged_path_safety.py -q`

Expected: FAIL because submission uses `shutil.copyfile` without reservation settlement.

- [ ] **Step 3: Implement bounded copy**

Copy 1 MiB chunks, update copied bytes, reject actual per-file/batch/quota overflow, and recheck `shutil.disk_usage(data_root).free` after each chunk. Flush and `os.fsync` before `os.replace(part, final)`.

- [ ] **Step 4: Implement atomic batch/source publication**

Create tasks and `available` source rows and delete the reservation in one SQLite transaction. Failure first marks reservation `cleanup_pending`, then safely cleans every item `.part` and final path; do not release accounting until all files are absent.

- [ ] **Step 5: Run focused tests and commit**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_import_service.py tests/test_import_repository.py tests/test_import_staging.py tests/test_staged_path_safety.py tests/integration/test_batch_import_acceptance.py -q
```

Expected: PASS. Commit submission changes.

### Task 9: Maintenance, user governance, quota UI/CLI, and Phase 3 gate

**Files:**
- Create: `app/import_maintenance.py`
- Modify: `app/import_service.py`
- Modify: `app/import_worker.py`
- Modify: `app/import_ops.py`
- Modify: `ui/gradio_app.py`
- Create: `tests/test_import_maintenance.py`
- Modify: `tests/ui/test_import_handlers.py`
- Create: `docs/superpowers/reports/2026-08-11-import-operations-phase-3.md`

**Interfaces:**
- Produces authenticated source preview/delete for one task and one batch.
- Adds CLI `quota`, `audit`, `cleanup --dry-run`, and `cleanup --execute`.
- Maintenance retries source/reservation cleanup and registers safe UUID orphans before deleting.

- [ ] **Step 1: Write failing governance tests**

Cover cross-user rejection, active/paused/pending-compensation rejection, failed/cancelled eligibility, dry-run/execution selection equality, retention disabled, 30-day boundary, partial unlink failure, idempotent repeat, and symlink/reparse refusal.

- [ ] **Step 2: Run red tests**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_import_maintenance.py tests/test_import_ops.py tests/ui/test_import_handlers.py -q`

Expected: FAIL because maintenance and governance handlers do not exist.

- [ ] **Step 3: Implement maintenance and CLI**

Maintenance uses conditional state updates and a 300-second audit interval. Unknown linked/non-UUID paths produce an allowlisted alert and are never deleted. CLI returns versioned JSON and opaque identifiers only.

- [ ] **Step 4: Implement user UI**

Show current-user used/reserved/pending bytes and quota. Add explicit preview then confirmation for single/batch source deletion. Logout and batch change clear hidden selections. No global data reaches Gradio.

- [ ] **Step 5: Run Phase 3 gate, review, and report**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_import_staging_schema.py tests/test_import_staging.py tests/test_import_maintenance.py tests/test_import_service.py tests/test_import_repository.py tests/test_import_worker.py tests/test_import_ops.py tests/test_staged_path_safety.py tests/ui/test_import_handlers.py tests/integration/test_batch_import_acceptance.py -q
.\venv\Scripts\python.exe -m compileall app assistants hello_agents ui
git diff --check
.\venv\Scripts\python.exe -m pytest -q
```

Expected: every command exits 0. Independently review quota atomicity, crash windows, path safety, and authorization. Fix findings, rerun the same commands, record evidence, then commit the report.

---

## Phase 4: Embedded capacity baseline

### Task 10: Deterministic capacity and soak harness

**Files:**
- Create: `scripts/import_capacity.py`
- Create: `tests/performance/test_import_capacity.py`
- Create: `tests/performance/test_import_soak_contract.py`
- Create: `docs/benchmarks/import-embedded-baseline.md`
- Create: `docs/superpowers/reports/2026-08-11-import-operations-phase-4.md`

**Interfaces:**
- Produces `run_capacity(users=100, tasks=1000, workers=4, timeout_seconds=300, mode="embedded")`.
- Produces a JSON summary with correctness counters, throughput, P95 queue wait, SQLite busy, takeover, fencing, and RSS deltas.

- [ ] **Step 1: Write failing harness-contract tests**

Use an offline fake runner and assert 100 users/1000 tasks, stable seeds, bounded shutdown, no external services, no upload content in reports, and nonzero exit on duplicate/lost/cross-user/nonterminal tasks.

- [ ] **Step 2: Run red tests**

Run: `.\venv\Scripts\python.exe -m pytest tests/performance/test_import_capacity.py tests/performance/test_import_soak_contract.py -q`

Expected: FAIL because the harness does not exist.

- [ ] **Step 3: Implement deterministic metrics and process cleanup**

Use `time.monotonic()` for durations, `statistics.quantiles(samples, n=100, method="inclusive")[94]` for P95 when at least two samples exist, and a fixed random seed. Register cleanup in `finally`; assert no `import-*` threads/processes remain.

- [ ] **Step 4: Run the embedded correctness gate**

Run:

```powershell
.\venv\Scripts\python.exe .\scripts\import_capacity.py --mode embedded --users 100 --tasks 1000 --workers 4 --timeout-seconds 300 --json-out .runtime\import-capacity-embedded.json
```

Expected: exit 0, exactly 1000 terminal tasks, zero duplicate/lost/cross-user tasks.

- [ ] **Step 5: Run the 30-minute embedded soak**

Run:

```powershell
.\venv\Scripts\python.exe .\scripts\import_capacity.py --mode embedded --users 100 --workers 4 --soak-seconds 1800 --json-out .runtime\import-soak-embedded.json
```

Expected: exit 0, bounded shutdown, no correctness violation. Copy only sanitized aggregate numbers and environment metadata into the benchmark Markdown; never commit `.runtime` output.

- [ ] **Step 6: Run Phase 4 full gate and commit**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/performance/test_import_capacity.py tests/performance/test_import_soak_contract.py -q
.\venv\Scripts\python.exe -m compileall app assistants hello_agents ui scripts
git diff --check
.\venv\Scripts\python.exe -m pytest -q
```

Expected: every command exits 0. Independently review harness validity and confirm it exercised the durable repository rather than a bypass. Commit scripts, tests, sanitized baseline, and phase report.

---

## Phase 5: Cooperative pause, cancel, and resume

### Task 11: Version 4 control state machine and compensation ledger

**Files:**
- Modify: `app/import_migrations.py`
- Modify: `app/import_models.py`
- Modify: `app/import_repository.py`
- Create: `app/import_controls.py`
- Create: `tests/test_import_controls_schema.py`
- Create: `tests/test_import_controls_repository.py`

**Interfaces:**
- Version 4 adds `paused/cancelled`, request/ack fields, compensation rows, and composite constraints.
- Produces `request_pause`, `request_cancel`, `resume_task`, `seal_committing`, `claim_compensation`, and `complete_compensation`.

- [ ] **Step 1: Write failing migration and full transition-matrix tests**

Cover version 3 upgrade/rollback, queued/running/retry/paused/failed/succeeded/cancelled actions, pause-to-cancel upgrade, duplicate requests, commit race, source missing, and compensation pending restrictions.

- [ ] **Step 2: Run red tests**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_import_controls_schema.py tests/test_import_controls_repository.py -q`

Expected: FAIL because version 4 and controls do not exist.

- [ ] **Step 3: Rebuild `import_tasks` transactionally**

Copy every legacy row, validate counts/foreign keys/indexes, and only then swap tables and record version 4. Status CHECK is exactly `queued,running,retry_wait,paused,succeeded,failed,cancelled`.

- [ ] **Step 4: Implement conditional control transitions**

Cancel dominates pause. `seal_committing` and control requests use mutually exclusive conditions so an accepted request cannot be ignored. Ordinary `failed -> queued` requires available source and no pending/failed compensation.

- [ ] **Step 5: Run tests and commit**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_import_controls_schema.py tests/test_import_controls_repository.py tests/test_import_migrations.py tests/test_import_repository.py tests/test_import_worker.py -q
```

Expected: PASS. Commit version 4 and repository controls.

### Task 12: Assistant checkpoints and idempotent compensation

**Files:**
- Modify: `hello_agents/memory/rag/prepare.py`
- Modify: `hello_agents/memory/rag/pipeline.py`
- Modify: `hello_agents/memory/rag/qdrant_pipeline.py`
- Modify: `hello_agents/tools/builtin/rag_tool.py`
- Modify: `hello_agents/tools/builtin/memory_tool.py`
- Modify: `hello_agents/memory/types/episodic.py`
- Modify: `assistants/pdf_learning_assistant.py`
- Modify: `app/import_worker.py`
- Create: `tests/test_import_control_checkpoints.py`
- Create: `tests/test_import_compensation.py`

**Interfaces:**
- Produces `ImportControlToken.checkpoint(stage)`, `seal_committing()`, and `ImportControlRequested`.
- Assistant accepts a control token separate from the progress callback.
- Compensation removes only the current task/document effects and is restart-idempotent.

- [ ] **Step 1: Write failing checkpoint and compensation tests**

Pause/cancel at every stage, remote embedding delay, exception plus accepted control, commit seal, compensation failure/retry, and History/Memory/RAG/formal-file isolation are mandatory.

- [ ] **Step 2: Run red tests**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_import_control_checkpoints.py tests/test_import_compensation.py -q`

Expected: FAIL because progress callbacks swallow exceptions and no explicit control path exists.

- [ ] **Step 3: Add explicit checkpoints**

Call the control token before and after parsing, chunking, embedding, persisting, and before committing. Never route control through the best-effort progress callback. Once commit is sealed, later requests are rejected by the repository.

- [ ] **Step 4: Persist and run compensation before acknowledging control**

Insert a pending compensation row before cleanup. Delete only exact task/document effects under the user coordinator. Mark success before entering paused/cancelled/failed. On failure, set `compensation_failed`, retain source and document guard, and expose authenticated retry-compensation.

- [ ] **Step 5: Run focused tests and commit**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_import_control_checkpoints.py tests/test_import_compensation.py tests/assistants/test_import_idempotency.py tests/assistants/test_import_active_guard.py tests/tools/test_rag_tool_backend_contract.py tests/test_import_worker.py -q
```

Expected: PASS. Commit Assistant/Tool/RAG/Worker changes.

### Task 13: Authenticated control UI and Phase 5 gate

**Files:**
- Modify: `app/import_service.py`
- Modify: `ui/gradio_app.py`
- Modify: `tests/ui/test_import_handlers.py`
- Modify: `tests/ui/test_authenticated_handlers.py`
- Modify: `tests/integration/test_batch_import_acceptance.py`
- Create: `docs/superpowers/reports/2026-08-11-import-operations-phase-5.md`

**Interfaces:**
- Adds authenticated pause, cancel, resume, and retry-compensation methods and Gradio handlers.
- UI displays paused/cancelled counts and clears batch-bound hidden selections after mutations/logout.

- [ ] **Step 1: Write failing authorization and race tests**

Test empty/forged/expired tokens before blank-ID returns, cross-batch hidden selection, cross-user task IDs, duplicate actions, logout continuity, restart persistence, and commit-boundary rejection.

- [ ] **Step 2: Run red UI/integration tests**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/ui/test_import_handlers.py tests/ui/test_authenticated_handlers.py tests/integration/test_batch_import_acceptance.py -q
```

Expected: FAIL because handlers and components do not exist.

- [ ] **Step 3: Implement service and Gradio controls**

Service derives user ID from session and passes expected batch ID into repository validation. UI never accepts user/document/path fields. Pause/cancel/resume refresh the selected batch and clear or retain selection according to the resulting row.

- [ ] **Step 4: Run Phase 5 gate and review**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_import_controls_schema.py tests/test_import_controls_repository.py tests/test_import_control_checkpoints.py tests/test_import_compensation.py tests/test_import_worker.py tests/test_import_service.py tests/assistants/test_import_idempotency.py tests/assistants/test_import_active_guard.py tests/ui/test_import_handlers.py tests/ui/test_authenticated_handlers.py tests/integration/test_batch_import_acceptance.py -q
.\venv\Scripts\python.exe -m compileall app assistants hello_agents ui
git diff --check
.\venv\Scripts\python.exe -m pytest -q
```

Expected: every command exits 0. Review control races, compensation boundaries, and staging retention. Fix findings and rerun the same commands.

- [ ] **Step 5: Commit Phase 5 report**

Record exact results and commit the report after zero Critical/Important findings.

---

## Phase 6: Single-host external Worker processes

### Task 14: Reentrant user coordination and version 5 Runtime reload

**Files:**
- Create: `app/user_data_coordination.py`
- Modify: `app/import_migrations.py`
- Modify: `app/coordination.py`
- Modify: `app/runtime.py`
- Modify: `app/history.py`
- Modify: `app/memory_repository.py`
- Modify: `hello_agents/memory/rag/pipeline.py`
- Modify: `hello_agents/memory/rag/qdrant_pipeline.py`
- Modify: `hello_agents/memory/graph/service.py`
- Create: `tests/test_user_data_coordination.py`
- Create: `tests/test_runtime_data_versions.py`

**Interfaces:**
- Version 5 adds user leases and `user_data_versions`.
- Produces `UserDataCoordinator.acquire(user_id, owner, timeout_seconds)` as a reentrant context manager.
- Produces `UserRuntime.ensure_version(version)` and backend capability checks.

- [ ] **Step 1: Write failing lock-order, death, and stale-Runtime tests**

Cover `RLock -> kernel lock -> short SQLite lease`, no transaction while waiting, reverse release, nested same-thread calls, different-thread timeout, process death releasing kernel lock, old token not releasing new lease, and cached Web version N preserving Worker version N+1 on the next write.

- [ ] **Step 2: Run red tests**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_user_data_coordination.py tests/test_runtime_data_versions.py -q
```

Expected: FAIL because coordination is process-local and Runtime has no version.

- [ ] **Step 3: Implement cross-platform kernel locking**

Use a validated one-byte `.locks/<user-uuid>.lock`. Windows locks one byte with `msvcrt.locking`; POSIX uses `fcntl.flock`. Scheduler acquisition is nonblocking; foreground waits at most 30 seconds. Do not hold a SQLite transaction while waiting.

- [ ] **Step 4: Implement pending/committed version protocol**

Reload file-backed Runtime state when local version differs. Reserve pending version before mutation, write owner-token-unique temporary files with atomic replace, then publish committed version. Expired pending mutations require task compensation/recovery before publication or cancellation.

- [ ] **Step 5: Implement backend capability matrix**

JSON, Qdrant, and Neo4j each explicitly report external capability after their reload/namespace requirements pass. Unknown backends fail external startup. Add cross-process contract tests for all three using offline fakes.

- [ ] **Step 6: Run tests and commit**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_user_data_coordination.py tests/test_runtime_data_versions.py tests/test_user_mutation_coordination.py tests/test_assistant_user_isolation.py tests/memory/rag/test_backend_selection_and_contracts.py tests/memory/rag/test_qdrant_pipeline.py tests/memory/graph/test_service.py -q
```

Expected: PASS. Commit version 5 and coordination changes.

### Task 15: External Worker launcher and mode-aware composition

**Files:**
- Create: `app/import_worker_main.py`
- Modify: `app/import_worker.py`
- Modify: `ui/gradio_app.py`
- Modify: `deploy/entrypoint.sh`
- Modify: `compose.yaml`
- Modify: `deploy/.env.example`
- Modify: `deploy/windows/Operations.Common.psm1`
- Create: `tests/test_import_worker_processes.py`
- Modify: `tests/ui/test_launch_config.py`
- Modify: `tests/deploy/test_compose_contract.py`
- Modify: `tests/deploy/test_windows_operations.py`

**Interfaces:**
- Produces `.\venv\Scripts\python.exe -m app.import_worker_main --processes N` for N=1..4.
- External Web never starts an embedded pool; embedded rejects active external registrations.
- Each external child registers capacity 1 and owns a unique Worker ID.

- [ ] **Step 1: Write failing process and composition tests**

Verify 1–4 child processes, invalid counts, startup capability failure, Web thread-free external import, embedded compatibility, graceful stop, forced child death, no orphan process, and compose service sharing the exact data volume.

- [ ] **Step 2: Run red tests**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_import_worker_processes.py tests/ui/test_launch_config.py tests/deploy/test_compose_contract.py tests/deploy/test_windows_operations.py -q
```

Expected: FAIL because no external launcher or mode-aware composition exists.

- [ ] **Step 3: Implement spawn-safe launcher**

Use `multiprocessing.get_context("spawn")`. Parent handles Ctrl+C/termination, requests graceful stop, joins with a bound, terminates only its own remaining children, and returns nonzero if any child exits unexpectedly. Child initialization occurs inside the child function; importing the module starts nothing.

- [ ] **Step 4: Make Gradio mode-aware**

`initialize_app_services()` always creates repositories/service. `launch_app()` starts/stops the pool only in embedded mode. External mode launches Gradio with no `import-*` thread and health expects external registrations.

- [ ] **Step 5: Add Compose worker service**

The app and Worker share the same SQLite/user-data volume and configuration. Keep one Web replica; Worker process count is bounded by 4. Do not expose new host ports.

Windows operations health requires `app`, `worker`, and `qdrant` when `IMPORT_WORKER_MODE=external`, but preserves the existing `app/qdrant` requirement in embedded mode. Backup, restore, update, and recovery commands operate on the whole Compose project so the Worker is stopped before SQLite/user-data archive operations.

- [ ] **Step 6: Run focused tests and commit**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_import_worker_processes.py tests/ui/test_launch_config.py tests/deploy/test_compose_contract.py tests/deploy/test_windows_operations.py tests/test_import_health.py tests/test_import_worker.py tests/test_import_service.py -q
```

Expected: PASS. Commit launcher and deployment composition.

### Task 16: Cross-process fault acceptance and Phase 6 gate

**Files:**
- Create: `tests/integration/test_import_multiprocess_acceptance.py`
- Modify: `tests/integration/test_multi_user_acceptance.py`
- Modify: `tests/integration/test_batch_import_acceptance.py`
- Create: `docs/superpowers/reports/2026-08-11-import-operations-phase-6.md`

**Interfaces:**
- Provides deterministic process-boundary acceptance for claims, fencing, Runtime reload, and user isolation.

- [ ] **Step 1: Add failure injection at every durable boundary**

Kill a child after claim, each stage, pending data version, compensation record, source cleanup intent, and before terminal write. Assert only expired leases recover, old tokens fail, same user never overlaps, different users progress, and no data is lost or crossed.

- [ ] **Step 2: Add stale Web Runtime acceptance**

Load a Web Runtime before Worker import, complete Worker import, then add a note/question from Web and assert both Worker document/history and new Web data survive after process restart.

- [ ] **Step 3: Run Phase 6 gate and independent review**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/integration/test_import_multiprocess_acceptance.py tests/integration/test_multi_user_acceptance.py tests/integration/test_batch_import_acceptance.py tests/test_user_data_coordination.py tests/test_runtime_data_versions.py tests/test_import_worker_processes.py tests/test_user_mutation_coordination.py tests/test_assistant_user_isolation.py tests/memory/rag/test_backend_selection_and_contracts.py tests/memory/rag/test_qdrant_pipeline.py tests/memory/graph/test_service.py -q
.\venv\Scripts\python.exe -m compileall app assistants hello_agents ui
git diff --check
.\venv\Scripts\python.exe -m pytest -q
```

Expected: every command exits 0. Review kernel locks, claim fencing, pending versions, shutdown, and deployment modes. Fix findings and rerun the same commands.

- [ ] **Step 4: Commit Phase 6 report**

Commit exact evidence only after zero Critical/Important findings.

---

## Phase 7: Final external capacity, operations, and integration

### Task 17: External capacity, soak, health, and recovery documentation

**Files:**
- Modify: `scripts/import_capacity.py`
- Modify: `tests/performance/test_import_capacity.py`
- Modify: `deploy/healthcheck.py`
- Modify: `deploy/README.md`
- Modify: `README.md`
- Modify: `compose.yaml`
- Create: `docs/benchmarks/import-external-baseline.md`
- Create: `docs/superpowers/reports/2026-08-11-import-operations-phase-7.md`

**Interfaces:**
- Reuses the identical Phase 4 workload in external mode.
- Documents embedded/external start, health, metrics, stuck, quota, audit, cleanup, controls, backup, and recovery.

- [ ] **Step 1: Run external 100-user/1000-task correctness**

```powershell
.\venv\Scripts\python.exe .\scripts\import_capacity.py --mode external --users 100 --tasks 1000 --workers 4 --timeout-seconds 300 --json-out .runtime\import-capacity-external.json
```

Expected: exit 0, 1000 terminal tasks, zero lost/duplicate/cross-user task, and no orphan child process.

- [ ] **Step 2: Run external 30-minute soak**

```powershell
.\venv\Scripts\python.exe .\scripts\import_capacity.py --mode external --users 100 --workers 4 --soak-seconds 1800 --json-out .runtime\import-soak-external.json
```

Expected: exit 0 and no correctness violation. Record sanitized aggregate comparison with Phase 4; do not turn machine-specific throughput into a hard threshold.

- [ ] **Step 3: Verify deployment and backup/restore contracts**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/deploy/test_compose_contract.py tests/deploy/test_smoke_test.py tests/deploy/test_backup_restore_contract.py tests/deploy/test_windows_operations.py -q
```

Expected: PASS. A restored database/data root must preserve task states, leases, quota/source rows, versions, and safe cleanup recovery. Update docs to remove the obsolete “single worker only” statement while retaining single-host and one-Web-replica limits.

- [ ] **Step 4: Verify fixed launch commands**

Run the exact command `.\venv\Scripts\python.exe .\ui\gradio_app.py` in a bounded subprocess, verify `Blocks` serves, then terminate only that process tree. Separately run `.\venv\Scripts\python.exe -m app.import_worker_main --processes 4` and prove Web starts no embedded threads. Record both process IDs and verify both owned process trees are absent after shutdown.

- [ ] **Step 5: Commit benchmark and documentation**

Commit only sanitized Markdown summaries and code/docs. Never commit `.runtime`, databases, staging, uploads, logs, or process dumps.

### Task 18: Whole-branch final review and release gate

**Files:**
- Create: `docs/superpowers/reports/2026-08-11-import-operations-final-review.md`
- Modify: `docs/superpowers/reports/2026-08-11-import-operations-phase-7.md`

**Interfaces:**
- Produces the immutable final acceptance record.

- [ ] **Step 1: Run complete focused unions**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_import_config.py tests/test_import_models.py tests/test_import_migrations.py tests/test_import_repository.py tests/test_import_metrics.py tests/test_import_events.py tests/test_import_ops.py tests/test_import_leases.py tests/test_import_health.py tests/test_import_staging_schema.py tests/test_import_staging.py tests/test_import_maintenance.py tests/test_import_controls_schema.py tests/test_import_controls_repository.py tests/test_import_control_checkpoints.py tests/test_import_compensation.py tests/test_user_data_coordination.py tests/test_runtime_data_versions.py tests/test_import_worker_processes.py tests/test_import_service.py tests/test_import_worker.py tests/test_staged_path_safety.py tests/assistants/test_import_active_guard.py tests/assistants/test_import_idempotency.py tests/ui/test_import_handlers.py tests/ui/test_authenticated_handlers.py tests/integration/test_batch_import_acceptance.py tests/integration/test_import_multiprocess_acceptance.py tests/integration/test_multi_user_acceptance.py tests/deploy/test_compose_contract.py tests/deploy/test_smoke_test.py tests/deploy/test_backup_restore_contract.py tests/deploy/test_windows_operations.py tests/performance/test_import_capacity.py tests/performance/test_import_soak_contract.py -q
```

Expected: exit 0 with no new skip.

- [ ] **Step 2: Run static and exact full gates on an immutable HEAD**

```powershell
.\venv\Scripts\python.exe -m compileall app assistants hello_agents ui scripts
git diff --check
.\venv\Scripts\python.exe -m pytest -q
```

Record HEAD before and after; if it changes, invalidate the result and rerun on the stable HEAD. Expected: exit 0.

- [ ] **Step 3: Run independent whole-branch review**

Review the approved design against production boundaries, migrations, all seven phase reports, benchmark evidence, user isolation, path safety, secret handling, process lifecycle, and deployment docs. Any Critical/Important finding creates a corrective task, targeted regression, focused rerun, exact full rerun, and re-review.

- [ ] **Step 4: Verify clean delivery scope**

Confirm no database, uploaded document, `.env`, `.runtime`, staging, benchmark raw JSON, logs, backups, or unrelated dirty files are staged. Confirm every required change is committed and nothing is pushed without explicit user authorization.

- [ ] **Step 5: Mark final READY**

Write final commit hashes, exact commands/results, two soak summaries, known live-service skips, and `READY` only when Critical/Important findings are zero and all commands exit 0. Commit the final review/report.

---

## Plan Self-Review Checklist

- Spec coverage map:

| Approved design section | Implemented by |
|---|---|
| Process roles and modes | Tasks 15–17 |
| Cross-process coordination and Runtime versions | Tasks 14–16 |
| Task/control state machine | Tasks 11–13 |
| Worker/task/user leases and schema migrations | Tasks 4–7, 11, 14 |
| Staging reservation, quota, source lifecycle, retention | Tasks 7–9 |
| Submission, execution, compensation, recovery | Tasks 5, 8, 12, 16 |
| Metrics, health, CLI, logs | Tasks 1–3, 6, 9 |
| Embedded and external capacity/soak | Tasks 10, 17 |
| Security, isolation, deployment, final gates | Every task; final consolidation in Tasks 16–18 |

- Every approved design section maps to at least one numbered task.
- Phase order is observability, health/leases, quota/governance, embedded baseline, controls, external processes, final external acceptance.
- Configuration names and 5/30/90, 2 GiB/10 GiB/1 GiB, 100/1000/300, and 1800-second values match the approved design.
- JSON stale Runtime protection requires lock plus reload/version protocol; external mode cannot disable the default JSON backend.
- All source deletions use `cleanup_pending` before unlink.
- Reservation items track both `.part` and final paths through crash windows.
- Cancel retains staging; success cleans staging; pause retains staging and resumes with the same `document_id`.
- No task instructs external infrastructure, cross-host execution, admin UI, hard kill cancellation, or user-supplied paths/IDs.
- Function and type names are introduced before later tasks consume them.
- Exact full pytest is required at every phase gate and final review.
