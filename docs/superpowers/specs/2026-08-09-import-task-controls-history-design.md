# Import Task Controls and History Design

**Date:** 2026-08-09

**Status:** Approved design

## Goal

Extend the durable batch-import module with safe cooperative cancellation,
pause/resume controls, queryable task history, batch-level history deletion,
and an opt-in retention policy. The design preserves the current single-node
SQLite worker architecture, per-user serialization, multi-user isolation,
idempotent document imports, and controlled staging paths.

## Scope

This phase delivers:

- task- and batch-level pause, resume, and cancellation;
- cooperative control checkpoints for running imports;
- restart-safe control requests and compensation;
- append-only task events and filtered, cursor-paginated history;
- safe batch-level history deletion;
- disabled-by-default terminal-history retention;
- Gradio controls, history filters, event timelines, and confirmation flows;
- offline unit, concurrency, recovery, authorization, UI, and acceptance tests.

This phase does not deliver hard thread termination, exact chunk-offset resume,
single-task history deletion, priorities, distributed workers, Redis, Celery,
or default automatic expiration.

## Architecture Decision

Extend the existing SQLite-backed import state machine and reuse
`ImportTaskRepository`, `ImportTaskService`, `ImportWorkerPool`,
`ImportTaskRunner`, `UserRuntimeRegistry`, and the current Gradio task panel.
Do not introduce a second command store or an external job system.

Control requests are durable task states. Repository transitions remain the
only authority for state changes. A running task cooperatively observes its
state through bounded checkpoints and raises an internal control signal. The
runner compensates the current attempt before committing the requested
terminal or paused state.

## State Model

Existing states remain:

- `queued`
- `running`
- `retry_wait`
- `succeeded`
- `failed`

New states are:

- `pause_requested`
- `paused`
- `cancel_requested`
- `cancelled`

Allowed control transitions are:

- `queued -> paused`
- `retry_wait -> paused`
- `running -> pause_requested -> paused`
- `queued -> cancel_requested -> cancelled`
- `retry_wait -> cancel_requested -> cancelled`
- `paused -> cancel_requested -> cancelled`
- `running -> cancel_requested -> cancelled`
- `pause_requested -> cancel_requested -> cancelled`
- `paused -> queued`

`succeeded` and `cancelled` are never retryable as import work. Existing
manual import retry remains available for `failed`. A failed compensation may
accept a new cancellation request that performs cleanup only, without
re-importing the document.

`running` and `pause_requested` occupy the database's per-user execution slot.
Multiple tasks from one cancelled batch may be `cancel_requested`, so that
state is not part of the unique execution index. The scheduler nevertheless
processes requested-control work before normal imports, serializes it by user,
and does not claim a new import for a user who has pending control work.
`queued`, `retry_wait`, `paused`, `pause_requested`, and `cancel_requested`
count as active for destructive document operations. Therefore a user must
finish or cancel them before clearing all documents.

Resuming a paused task transitions it to `queued`, clears any prior retry
deadline, and wakes the worker pool. It retains the original task ID,
batch ID, document ID, staged source, automatic/manual attempt counters, and
last progress snapshot. Execution restarts idempotently from the document-task
entry point rather than from an exact parser or chunk offset.

## Persistent Data

Import tasks gain durable control timestamps:

- `control_requested_at`
- `control_claimed_at`

The existing `finished_at`, safe error code, and safe summary fields describe
terminal completion, compensation, or cleanup failures. State updates continue
to set `updated_at`.

`control_claimed_at` is null for unclaimed control work. Repository claim sets
it atomically for `pause_requested` or `cancel_requested`; normal completion,
failure, or shutdown release clears it. Startup recovery clears stale control
claims before scheduling, so a process interruption cannot strand or duplicate
cleanup work.

Import batches gain deletion-lifecycle fields:

- `lifecycle_state`, with `active` and `deleting` values;
- `delete_requested_at`;
- safe cleanup error code and summary fields.

Existing batches migrate to `active`. A `deleting` batch is hidden from normal
active/history queries and cannot accept controls or retries.

Add an append-only `import_task_events` table with:

- immutable event ID;
- user ID, batch ID, and task ID;
- event type;
- resulting status and stage;
- safe, bounded message;
- creation timestamp.

Events cover submission, claim, meaningful progress-stage changes, automatic
retry, manual retry, pause request, pause completion, resume, cancel request,
cancel completion, failure, and success.
Every event query includes the authenticated immutable user ID. Task deletion
cascades to its events.

Schema changes use the repository's existing idempotent SQLite migration
pattern. Existing task rows remain valid and require no state rewrite.

## Cooperative Checkpoints and Compensation

The worker checks durable control state at these bounded boundaries:

- before creating the formal source copy;
- after parsing;
- after each bounded chunking or embedding progress batch;
- after each RAG persistence batch;
- before History and Memory commit;
- before marking the task succeeded.

Progress callbacks perform the checks without exposing repository or UI
details to RAG internals. An internal pause/cancel signal is not classified as
an ordinary import failure and does not consume automatic retry budget.

For a running pause or cancel, the assistant and runner reuse the current
idempotent import compensation boundary. They remove only artifacts owned by
the task's immutable document ID and import task ID:

- the attempt's temporary/formal source file;
- task-owned RAG data;
- the matching History document record;
- the deterministic import Memory event.

A pause retains the validated staged source. A cancellation removes it only
after compensation succeeds. Only successful compensation may write `paused`
or `cancelled`.

If compensation fails, the task becomes `failed` with a safe structured code
such as `pause_cleanup_failed` or `cancel_cleanup_failed`. The staged source is
retained. A subsequent cancel command is accepted for these cleanup-failure
codes only, performs cleanup without importing, and may complete as
`cancelled`; a normal import retry remains idempotent.

On process restart, `paused` remains paused. `pause_requested` and
`cancel_requested` are recovered as control work and completed before normal
queued imports for that user. They are not silently converted into ordinary
imports.

## Repository and Concurrency Rules

All state transitions are conditional SQLite updates inside transactions.
Concurrent tabs may submit the same control command, but only one transition
succeeds; the other receives the latest task or batch summary.

The scheduler selects `pause_requested` and `cancel_requested` work before
ordinary queued or retry-ready imports. Its existing in-memory blocked-user set
still enforces one executing item per user, while repository claim predicates
also exclude users with pending control work so restart and multi-worker races
cannot bypass the ordering. Requested-control candidates require
`control_claimed_at is null`; claiming one atomically sets the timestamp before
returning the task to the worker.

Batch controls transition each eligible task in one transaction:

- pause: queued/retry-wait tasks become paused and a running task becomes
  pause-requested;
- resume: paused tasks become queued;
- cancel: queued/retry-wait/paused/running/pause-requested tasks become
  cancel-requested immediately and therefore cannot be claimed as normal
  import work;
- succeeded and already-cancelled tasks remain unchanged.

File cleanup happens outside the SQLite write transaction but only after the
durable request transition. A control worker marks the task `cancelled` only
after cleanup succeeds. Controlled staging resolution must continue to reject
absolute paths, traversal, mismatched IDs, symlinks, Windows junctions, and
reparse points. Cleanup is idempotent.

## Service Boundary

Add authenticated methods:

- `pause_task(session_token, task_id)`
- `resume_task(session_token, task_id)`
- `cancel_task(session_token, task_id)`
- `pause_batch(session_token, batch_id)`
- `resume_batch(session_token, batch_id)`
- `cancel_batch(session_token, batch_id)`
- `list_task_history(session_token, filters, cursor, limit)`
- `list_task_events(session_token, task_id)`
- `delete_batch_history(session_token, batch_id)`

Every method resolves the current session first, derives the immutable user
ID from it, and passes that user ID to every repository operation. Callers
cannot supply a user ID, staging path, document ID, or arbitrary target state.

Task and batch control methods notify the worker pool after a successful state
change. Responses return current safe summaries rather than raw database rows.

## History Queries

History supports optional status, original filename, date range, and batch ID
filters. Results use a stable cursor based on creation timestamp plus immutable
batch ID and have a bounded page size. The active panel continues to query
recent active batches; history queries do not scan or return every task.

Task event timelines are fetched only when a user expands a task. Event
messages use the same credential, path, identifier, and length sanitization as
task error summaries.

## Batch History Deletion

Only a batch whose tasks are all `succeeded`, `failed`, or `cancelled` may be
deleted. Single-task history deletion is deliberately unsupported.

Deletion follows a durable two-phase workflow:

1. Authenticate and verify ownership and terminal status.
2. Mark the batch `deleting` with a timestamp.
3. Safely remove retained failed/cancelled staged files through controlled
   staging-path validation.
4. In a transaction, delete task events and task records for that user/batch.
5. Remove the now-empty batch staging directory if safe.

Deleting history never removes a succeeded document's formal source, RAG,
History, questions, notes, reports, or Memory. A crash or cleanup failure leaves
the batch in `deleting`; startup and the maintenance loop retry the same
idempotent cleanup. Other users and batches are never included.

## Retention

History is retained permanently by default. An opt-in environment setting may
define a positive terminal-batch retention period in days. Zero or an absent
setting disables automatic expiration.

The maintenance loop considers only fully terminal batches older than the
configured period and invokes the same durable batch-deletion workflow used by
manual deletion. It performs bounded work per pass and records safe failures
without blocking import scheduling.

## Gradio UX

The import area gains two views:

### Active tasks

- retain one-second read-only polling;
- select a task using a hidden `(batch_id, task_id)` binding;
- show pause, resume, and cancel actions according to current status;
- offer batch pause, resume, and cancel actions;
- require confirmation for task/batch cancellation;
- clear stale selections when the batch changes or a task becomes ineligible.

### History

- filter by status, original filename, and date range;
- load bounded cursor pages;
- expand a task to view its safe event timeline;
- allow terminal batch history deletion with explicit confirmation that
  imported documents are preserved.

Pause and resume do not require confirmation. Successful commands refresh the
summary and table immediately. Expired sessions, forged IDs, stale selections,
and cross-user targets raise safe authentication or not-found errors without
changing SQLite, staging files, RAG, History, or Memory.

The UI continues to hide user IDs, task/batch/document IDs, staging paths,
absolute server paths, credentials, credential-bearing URLs, and stack traces.

## Error Handling

Control signals are distinct from backend failures. They never consume the
three automatic import retries. SQLite busy/locked handling remains bounded;
an uncommitted control transition may be retried, while a committed transition
must not be duplicated.

Cancellation, history deletion, and retention cleanup use stable safe error
codes. Raw backend messages, paths, and secrets are sanitized before SQLite
persistence, not only at UI rendering.

## Verification

Required offline tests include:

- every allowed and rejected state transition;
- concurrent duplicate commands and conditional-update behavior;
- task- and batch-level mixed-state controls;
- checkpoints during parsing, chunking, embedding, persistence, and commit;
- compensation of formal files, RAG, History, and deterministic Memory events;
- compensation failure and cleanup-only cancellation retry;
- paused identity/counter/progress preservation and idempotent resume;
- restart recovery for paused and requested-control states;
- logout/browser-close continuation of control work;
- per-user execution-slot behavior for requested-control states;
- history filters, stable cursor pagination, and event isolation;
- manual and retention-driven two-phase batch deletion;
- deletion recovery after interruption, SQLite lock, missing files, and
  staging-link/junction attacks;
- cross-user authorization for every new service and UI handler;
- stale selection, confirmation, login/logout, and polling lifecycle behavior;
- README and configuration documentation.

The default full suite must not require a real Qdrant, Neo4j, LLM, or project
runtime data. Explicit live tests remain separately marked. Completion requires
focused tests, the complete offline suite, `compileall`, a side-effect-free UI
module import, `git diff --check`, and an independent whole-branch review with
no Critical or Important findings.

## Acceptance Criteria

1. Queued, retry-wait, and paused tasks enter `cancel_requested` immediately,
   cannot be claimed as imports, and become cancelled only after safe cleanup.
2. Running tasks pause or cancel at bounded checkpoints without hard thread
   termination.
3. A paused task resumes with the same task and document identity and produces
   no duplicate RAG, History, or Memory records.
4. A cancelled task leaves no task-owned document artifacts or staged source.
5. Failed compensation is visible and safely retryable; it is never reported
   as paused or cancelled.
6. Restart preserves paused tasks and completes durable control requests.
7. Batch controls preserve terminal tasks and atomically transition all
   eligible tasks.
8. History and events are filtered, paginated, safe, and user-isolated.
9. Terminal batch history deletion and optional retention never delete imported
   documents or data belonging to another task, batch, or user.
10. The UI exposes safe controls and history without revealing private IDs,
    paths, credentials, or raw errors.
11. All focused, full-suite, static, import-side-effect, and independent review
    gates pass.
