# Distributed Cutover Vertical Slice Implementation Plan

> **For agentic workers:** Execute the phases in order. Keep each phase's checks and evidence in this document; a passing infrastructure test alone does not close the delivery.

**Goal:** In an isolated environment, migrate the complete current SQLite/file/Qdrant business state to PostgreSQL/object storage/Qdrant, run the live business journey and failure drills, then switch between two independently built compatible distributed application images on the same new data while retaining new writes.

**Architecture:** `local` remains the current single-process mode. `distributed` uses PostgreSQL for all structured authority, S3-compatible object storage for durable bytes, Qdrant for vectors, and a separate Worker process for durable background work. A read-only paired source copy is migrated offline at one stopped-write boundary. Production cutover remains a separate, explicitly authorized operation after isolated acceptance.

**Tech Stack:** Python 3.11, FastAPI, React, PostgreSQL, psycopg 3, Alembic, S3-compatible storage, Qdrant, Docker Compose, pytest.

## Global Constraints

- Baseline: `codex/next-improvements` at `528e70e`; the linked worktree begins clean. Never stage changes in `D:/python_self_agent` or `D:/python_self_agent/.worktrees/distributed-multi-user-system`.
- `docs/superpowers/specs/2026-09-24-distributed-cutover-vertical-slice-design.md` governs order and cutover semantics; `2026-08-09-distributed-multi-user-import-system-design.md` supplies tenant, lease, fencing and idempotency rules.
- Preserve `user_id`, `document_id`, namespace, citation, page, deletion-fence, note-source, learning and report contracts. Do not change embedding model or Qdrant index identity during this migration.
- No live dual write. Distributed configuration must fail closed if PostgreSQL, object storage or independent Worker requirements are missing. A second API instance cannot start while process-local session, runtime or user-write authority remains.
- Use `D:/python_self_agent/venv/Scripts/python.exe` and a worktree-local `--basetemp` with an existing `.runtime` parent. Do not commit secrets, source data, generated backups or runtime logs.
- The first deliverable is **the complete isolated business and rollback result**, not a schema, adapter, unit-test count or mock-only UI check. Do not begin production data migration without renewed explicit user approval.

## File and responsibility map

| Boundary | Existing files and planned owner |
| --- | --- |
| Mode and schema | `app/deployment.py`, `app/postgres.py`, `app/bootstrap.py`, `app/database.py`, `migrations/`, `requirements.txt`, `compose.yaml` |
| Structured business state | `app/auth.py`, `app/import_repository.py`, `app/qa_repository.py`, `app/qa_job_repository.py`, `app/qa_deletion.py`, `app/note_repository.py`, `app/note_projection.py`, `app/learning_repository.py`, `app/learning_queries.py`, `app/reports.py`, `app/history.py`, `app/memory_repository.py` |
| Shared runtime and bytes | `app/session.py`, `app/runtime.py`, `app/coordination.py`, `app/storage.py`, `app/import_service.py`, `app/import_worker.py`, `hello_agents/memory/rag/pipeline.py`, `hello_agents/tools/builtin/rag_tool.py` |
| Migration and evidence | `deploy/` isolated migration/verification commands, `tests/integration/` real dependency journeys, `docs/agent-work/distributed-cutover/` evidence and operator steps |

## Phase 0 — frozen baseline and runnable acceptance source

- [x] Read repository instructions, project knowledge, both designs, current Git state and old worktree draft status.
- [x] Record `528e70e`, current application image digest, Qdrant image/collection identity, deployed service identity, and source paired backup names. Read production artifacts only to construct an isolated copy; do not mutate the running deployment. Evidence: `docs/agent-work/distributed-cutover/progress.md`.
- [ ] Finish baseline tests from this worktree and record exact command, exit code and failures. Build a minimal read-only source inventory of SQLite tables, rows by user and status, files with SHA-256/size, Qdrant collections/point counts and index identity from one paired backup.
- [x] Record an authenticated product journey checklist: login, import, search/citations, QA, source note, learning plan/task, report generation/read/download, delete, cross-user denial, restart persistence. Recorded in `docs/agent-work/distributed-cutover/business-acceptance.md`; execution remains pending. Synthetic models may make requests deterministic, but real PostgreSQL/S3/Qdrant and actual product endpoints must be exercised.

**Gate:** source inventory is reproducible from a paired backup and the baseline failures, if any, are classified before code changes.

## Phase 1 — shared persistence contract

- [ ] Implement strict mode/role validation and PostgreSQL pool/schema as the first executable slice in `docs/superpowers/plans/2026-08-09-distributed-persistence-foundation.md`, updating that older slice's assumptions where this plan narrows scope. Unit tests and an opt-in real PostgreSQL migration test must pass; service bootstrap must reject distributed startup until all business repositories have distributed implementations.
- [ ] Create explicit PostgreSQL repository implementations for users/authentication, sessions/CSRF, import batches/tasks, QA conversations/messages/jobs/deletion fences, notes/sources/projections, learning plans/tasks/events, report metadata, History and Memory snapshots. Every public method keeps user-scoped lookups, idempotency keys and expected versions. SQL claiming uses `FOR UPDATE SKIP LOCKED`; database time controls leases.
- [ ] Introduce a storage interface for staged uploads, documents, reports and any persisted RAG/Memory bytes. Implement S3-compatible keys from server IDs, object hash checking, atomic metadata publication, compensation and a reconcile command. A local task scratch directory may be deleted after use; it is never the durable authority.
- [ ] Replace process-local session/CSRF and user-write coordination with PostgreSQL-backed state and fencing. A second API process must read the first process's session and reject stale writes; same-user mutations serialize while different users can progress independently.
- [ ] Run repository contract tests against both modes and real PostgreSQL/S3 integration tests. Verify repeated commands do not create duplicates and cross-user IDs are inaccessible.

**Gate:** every existing product endpoint can use the new repositories and objects; two API processes share one authoritative state without relying on a shared filesystem.

## Phase 2 — independent Worker and failure safety

- [ ] Create a dedicated Worker entry point. In distributed API role, `ApplicationServices.start()` starts no embedded import, QA, deletion or note-projection thread. Worker role starts only these processors.
- [ ] Persist task claims, heartbeat, lease token/version and terminal conditions for each durable work type. Every result publication checks current lease ownership, including external object/vector writes; loss of ownership stops the old attempt. Reclaimed tasks reuse stable IDs and content hashes.
- [ ] Prove API termination, Worker termination, duplicate claim, lease expiry, stale-worker commit rejection, database interruption and object-store timeout using real dependencies. Check user isolation and one active import per user.

**Gate:** no active business task depends on a particular API process surviving; recovery and stale-write rejection have recorded traces.

## Phase 3 — offline migration and consistency

- [ ] Implement a read-only source scanner and resumable offline migration with `--dry-run`, `--apply` and `--verify` modes. Validate the paired SQLite/files/Qdrant source belongs to one stopped-write boundary before any target write.
- [ ] Copy all users, sessions, business tables, History/Memory snapshots, documents, staged imports and reports. Preserve IDs, timestamps, tenant scope, task status and checksums. Translate SQLite full-text behavior to PostgreSQL search without changing note-search results expected by the business contract.
- [ ] Restore the paired Qdrant snapshot into a separate collection/service and compare per-tenant document/vector identity, point count and sampled content. Keep the existing embedding identity. If optional graph storage is enabled, inventory and verify it too.
- [ ] Produce a machine-readable manifest containing source/target counts and hashes, unsupported or missing rows/objects, retry state, collection identity, command revision and timestamps. A mismatch exits nonzero and blocks startup/opening writes. Re-run apply and verify to prove idempotency.

**Gate:** one isolated migrated target has no unexplained business/object/vector mismatch; failure is recoverable without touching production.

## Phase 4 — real business, restart, recovery and rollback

- [ ] Start one distributed API plus independent Worker on the migrated PostgreSQL/S3/Qdrant copy. Use at least two users and the authenticated product API to complete the Phase 0 journey. Verify response content, source citation identity, note provenance, learning state and report bytes; restart and repeat read checks.
- [ ] Start a second API against that same target and repeat session continuity and concurrent same-user/different-user checks. Inject API and Worker failure, duplicate delivery and expired lease; confirm final state and object/vector consistency.
- [ ] Build two distinct versioned images from compatible distributed source revisions: candidate and rollback. After opening isolated writes on candidate, create an import, QA, note, learning state and report. Switch **only** the application image to the rollback build, keeping the same PostgreSQL/S3/Qdrant. Read and continue writing all newly created data, including a queued Worker task. Record both image digests and the data IDs/hashes before and after.
- [ ] Rehearse failure before opening writes by discarding the candidate target and restoring the old single-node image from the stopped-write paired backup. Rehearse shared-store disaster recovery separately; measure recovery point, elapsed time and any cross-store difference rather than promising zero loss.

**Gate:** complete isolated business and both rollback semantics pass with real dependencies, distinct image digests and auditable records.

## Phase 5 — delivery and production decision

- [ ] Run affected backend suites, frontend checks, image build and isolated acceptance once on the final code. Review `git diff`, secret scans and untracked files; commit and push only this branch's verified files.
- [ ] Write the exact maintenance, stop-write, backup, migration, verification, single API/Worker opening, observation and rollback runbook. Include fail-stop points and the two built image digests.
- [ ] Report Phase 4 evidence to the user and request renewed explicit confirmation before any production data migration. Even with approval, production expansion to multiple replicas is a later window with its own acceptance.

**Gate:** no production switch or production data migration is performed by this plan without the separate decision.
