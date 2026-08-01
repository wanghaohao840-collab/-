---
id: "multi-user-integrity-01"
title: "Enforce the per-user mutation coordination contract"
status: "done"
parallel-safe: true
depends-on: []
base-commit: "614f84e9d01179ce1272281f77e15550c1dcd764"
owner: "claude-code"
---

# Task Packet: Enforce the per-user mutation coordination contract

## Goal

Make same-user History, RAG, Memory, and uploaded-source mutations follow one
explicit process-local coordination contract, with fresh History merge and
defined compensation behavior, while proving LLM generation is outside the
write lock.

## Non-goals

- Distributed or multi-process locking.
- A real transaction spanning filesystem, SQLite, JSON, and RAG.
- Changes to retrieval, prompts, report history, migration, or authentication.
- Making session-local counters globally consistent.

## Delivery context

`UserRuntimeRegistry` already shares one `RLock`. History updates and several
delete paths use it, and `MemoryTool.execute()` uses `coordination_lock`.
However the contract is implicit and mutation ordering/compensation is spread
through Assistant methods. This packet turns that partial implementation into
a reviewable invariant without holding the lock over LLM calls.

## Relevant files and current interfaces

- `app/runtime.py:17-26` — `UserRuntime` owns lock, RAG, Memory, History,
  reports, and paths.
- `app/runtime.py:53-68` — creates the shared `RLock`.
- `assistants/pdf_learning_assistant.py:120-199` — document import mutates RAG,
  History, Memory, and session state in separate phases.
- `assistants/pdf_learning_assistant.py:201-291` — QA performs model-backed RAG
  work before committing structured History.
- `assistants/pdf_learning_assistant.py:561-569` — fresh History update and
  Memory lock helpers.
- `assistants/pdf_learning_assistant.py:652-685` — delete/clear ordering.
- `hello_agents/tools/builtin/memory_tool.py:156-161` — shared lock wrapper.
- Existing changes to preserve: all current uncommitted changes in these files.

## Prerequisites

### Packet dependencies

- None.

### Repository/base state

- Base commit plus current dirty worktree described in `REVIEW.md`.
- Existing `HistoryRepository.update()` and structured RAG mutation results.

### External prerequisites

- None; use fakes and temporary paths.

## Explicit change boundary

### Allowed files

- Create: `app/coordination.py`
- Modify: `app/runtime.py`
- Modify: `assistants/pdf_learning_assistant.py`
- Modify: `hello_agents/tools/builtin/memory_tool.py` only if required to avoid
  nested or long-held lock behavior
- Modify/Create: `tests/test_user_mutation_coordination.py`
- Modify: `tests/test_p0_data_integrity.py`

### Allowed behavior changes

- Introduce a small runtime-owned coordination abstraction or equivalent
  explicit API.
- Reload latest History inside the lock immediately before every commit.
- Define mutation order and best-effort compensation for import/delete/clear.
- Capture pre-LLM state and commit post-LLM results in separate short critical
  sections.

### Forbidden changes

- Do not edit migration, report, storage, UI, auth, session, RAG pipeline, or
  Qdrant files.
- Do not change public Assistant method signatures or persisted History fields.
- Do not hold the user lock while `rag_tool.execute("ask", ...)` or another LLM
  generation runs.
- Do not weaken user/document isolation or corrupt-data fail-closed behavior.

## Interface contract

### Consumes

- `HistoryRepository.load/update/delete_document/clear_documents`.
- `RAGTool.execute()` / `execute_result()` mutation interfaces.
- `MemoryTool.execute()` with `coordination_lock`.
- `UserRuntime.lock`, paths, and shared tools.

### Produces

- One documented coordinator interface used by runtime-backed assistants for
  user-state writes.
- Deterministic error behavior when a coordinated step fails.
- No breaking change to existing Assistant callers.

### Invariants

- Same-user commits serialize through the same runtime lock.
- Every History write merges into the latest persisted snapshot.
- Failed RAG mutation does not silently publish matching History success.
- Source unlink never escapes the current user's document root.
- LLM generation occurs without owning the user write lock.

## Required behavior

- Concurrent notes from two assistants are both retained.
- Concurrent import/delete/note operations cannot replace newer History with an
  older session snapshot.
- Import failure compensates staged/published source and does not add History.
- Delete/clear reports partial failure explicitly and preserves enough state to
  retry; it must not claim full success after a failed step.
- QA records always commit structured `document_ids`, `document_names`, and
  `mode` after generation, using a short lock section.

## Implementation guidance

Prefer a focused coordinator that exposes a lock context and mutation helpers;
do not build a generic transaction framework. Document ordering and
compensation. Reentrant locking is permitted. Test lock duration with a fake
blocking RAG/LLM call and a second mutation rather than timing fragile sleeps.

## Acceptance criteria

- [ ] Two same-user assistants concurrently append notes/questions without
  lost History entries.
- [ ] Import/delete/clear use the shared coordinator and expose failed partial
  operations without false success.
- [ ] RAG and Memory writes use the same user coordination boundary.
- [ ] A blocked fake LLM call does not prevent another session from committing
  a note.
- [ ] Structured question scope and original-file deletion regressions pass.

## Test and verification commands

```powershell
$env:TEMP=(Resolve-Path '.runtime/pytest-tmp').Path
$env:TMP=$env:TEMP
D:\Anaconda\python.exe -m pytest tests/test_user_mutation_coordination.py tests/test_p0_data_integrity.py tests/assistants/test_pdf_learning_assistant_multi_document.py -q
```

Expected: all tests pass; no live LLM or Qdrant access.

## Stop conditions

Use the Reality-conflict report if the RAG mutation contract cannot distinguish
success, compensation requires files outside the boundary, or current code
cannot prove lock ownership without changing public APIs.

## Implementation handoff

- Status: done
- Files changed:
  - `app/coordination.py` (created)
  - `app/runtime.py`
  - `assistants/pdf_learning_assistant.py`
  - `tests/test_user_mutation_coordination.py` (created)
  - `tests/test_p0_data_integrity.py`
  - `tests/test_history_repository.py`
  - `tests/assistants/test_pdf_learning_assistant_multi_document.py`
- Acceptance criteria:
  - [x] Two same-user assistants concurrently append notes/questions without
    lost History entries.
    → `test_two_sessions_merge_concurrent_notes` (P0),
    `test_concurrent_notes_merge_without_loss`,
    `test_lock_serializes_same_user_writes`
  - [x] Import/delete/clear use the shared coordinator and expose failed partial
    operations without false success.
    → `test_import_failure_leaves_history_untouched`,
    `test_import_compensates_rag_on_history_failure`,
    `test_delete_document_unlinks_source_inside_user_root`,
    `test_clear_documents_retains_notes`
  - [x] RAG and Memory writes use the same user coordination boundary.
    → Coordinator created with `runtime.lock` (shared with
    `memory_tool.coordination_lock`); all History mutations route through
    `coordinator.update_history` which acquires the same lock.
  - [x] A blocked fake LLM call does not prevent another session from committing
    a note.
    → `test_ask_generates_outside_lock` — `BlockingRAGTool` holds
    `ask` open while a concurrent `add_note` commits successfully.
  - [x] Structured question scope and original-file deletion regressions pass.
    → `test_structured_question_scope_committed_after_generation`,
    `test_delete_and_clear_remove_original_uploads`,
    `test_pdf_learning_assistant_multi_document.py` (10/10)
  - [x] Out-of-root source files survive delete/clear and the operation
    reports partial failure explicitly (Codex blocking finding).
    → `test_delete_preserves_out_of_root_source_and_reports_partial`,
    `test_clear_preserves_out_of_root_source_and_reports_partial`
- Verification:
  - `python -m pytest tests/test_user_mutation_coordination.py tests/test_p0_data_integrity.py tests/assistants/test_pdf_learning_assistant_multi_document.py tests/test_history_repository.py tests/test_assistant_user_isolation.py -v` — 33 passed
  - `python -m pytest tests/ -q` — 125 passed, 1 skipped (Qdrant integration)
  - `python -m compileall -q app assistants hello_agents` — clean
- Deviations:
  - **safe_unlink fallback removed** (Codex blocking finding): `_delete_document_coordinated`
    and `_clear_documents_coordinated` previously caught `ValueError` from
    `coordinator.safe_unlink()` and fell back to `path.unlink()`. This is now
    removed. When the coordinator rejects a path, it is collected into
    `skipped_paths` and reported in the return message as "source files outside
    user root were not deleted". Runtime-backed assistants never silently unlink
    a coordinator-rejected path.
  - **Test fixture edits** (`tests/test_history_repository.py`,
    `tests/assistants/test_pdf_learning_assistant_multi_document.py`): Both files
    are outside the packet's Allowed files but needed a one-line addition
    (`assistant.coordinator = None`) so that `__new__`-bypassing test fixtures
    remain compatible after `_update_history`, `load_document`,
    `_delete_document_coordinated`, and `_clear_documents_coordinated` were
    changed to reference `self.coordinator`. These are fixture-only
    compatibility edits — no test behavior was changed, no production path was
    affected.
- Residual risks:
  - RAG compensation is best-effort; if the compensation `delete_document` also
    fails, the RAG store retains the orphaned document while History is clean.
    This is documented in the coordinator docstring.
- Commit:
  - not committed

## Codex acceptance review

- Review status: changes required
- Independent verification:
  - `python -m pytest tests/test_user_mutation_coordination.py tests/test_p0_data_integrity.py tests/assistants/test_pdf_learning_assistant_multi_document.py tests/test_history_repository.py -q`
    — 30 passed
- Blocking finding:
  - `assistants/pdf_learning_assistant.py` catches `ValueError` from
    `coordinator.safe_unlink(path)` and immediately calls `path.unlink()`.
    For runtime-backed assistants this defeats the user-root boundary and can
    delete a path that the coordinator explicitly rejected.
- Required correction:
  - Runtime-backed assistants must propagate/report the rejected path and must
    never fall back to direct unlink after `safe_unlink()` rejects it.
  - Add regression tests for delete and clear proving an out-of-root History
    path remains untouched and the operation does not claim full success.
- Scope deviation:
  - `tests/test_history_repository.py` and
    `tests/assistants/test_pdf_learning_assistant_multi_document.py` were
    modified outside **Allowed files**. The handoff must record this deviation
    and justify why fixture-only compatibility edits were necessary, or the
    edits must be avoided through an allowed test fixture.
- Resume condition:
  - Apply the correction, rerun the focused and full verification, update the
    handoff truthfully, and return `status: done`.

## Codex acceptance re-review

- Review status: accepted
- Blocking finding resolution:
  - Runtime-backed delete and clear now retain coordinator-rejected paths,
    report the skipped source files, and never fall back to direct unlink.
  - Delete and clear both have out-of-root regression coverage.
  - Fixture-only edits outside the original Allowed files are now disclosed and
    justified in the handoff.
- Independent verification:
  - Focused packet suite — 33 passed.
  - Full repository suite — 125 passed, 1 skipped.
  - `git diff --check` — passed.
- Decision:
  - Packet `multi-user-integrity-01` is accepted as `done`.
