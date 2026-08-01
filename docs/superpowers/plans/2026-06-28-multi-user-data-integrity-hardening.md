# Multi-User Data Integrity Hardening Plan

**Goal:** Close the remaining multi-user concurrency, migration recovery, and
end-to-end isolation gaps without reimplementing behavior already present in
the current worktree.

**Current-state correction:** The originating audit is stale in several areas.
The worktree already contains user-level locking, reload-and-update history
writes, immutable Markdown report records, Word export from a selected
snapshot, user-scoped upload paths with temporary files, original-file
deletion, structured `document_ids`/`document_names`/`mode`, fail-closed
history and memory reads, migration staging/manifest/status fields, and basic
tests. These behaviors must be preserved and strengthened, not duplicated.

## Architecture

Preserve `UI -> Assistant -> Tool -> Memory/RAG/Storage`. A shared
per-user runtime remains the owner of mutable user state and its `RLock`.
Assistant sessions retain only session-local selection and counters. Repository
objects remain responsible for validated atomic persistence. Long-running LLM
generation must occur outside the user write lock; only snapshot capture and
result commit are coordinated.

## Delivery sequence

1. Define and enforce one user-mutation coordination contract for History,
   RAG, Memory, and source-file lifecycle.
2. Make legacy migration transactional at the application level, retry-safe,
   conflict-aware, and evidence-rich.
3. Complete explicit corruption recovery workflows without weakening
   fail-closed reads.
4. Add end-to-end authorization, concurrency, handler, report, migration, and
   restart acceptance tests.
5. Run a mandatory Codex final integration review over the combined diff.

## Global constraints

- Current code and tests override this plan when they disagree.
- Preserve per-user UUID roots and explicit `document_id` scoping.
- Do not hold the user lock across LLM generation.
- Do not silently convert corrupt persistence into empty data.
- Do not expose filesystem paths, tokens, credentials, or another user's IDs
  in user-facing authorization errors.
- Do not introduce distributed locking or claim multi-process safety; the
  current runtime registry is process-local.
- Do not change RAG backend selection, Qdrant collection semantics, retrieval
  ranking, or multi-document QA behavior.
- Do not migrate ambiguous Memory records by guessing ownership.
- Tests must use temporary roots and injected/fake services; they must not
  require a live Qdrant server or external LLM.

## Acceptance

- Same-user concurrent mutations do not lose committed history entries or
  leave History/RAG/source-file state silently divergent.
- LLM calls can run while another session commits a short mutation.
- Failed migration leaves no partially published user state and can be retried
  without duplicate documents or report rows.
- Corrupt History and Memory remain write-blocked until an explicit recovery
  action succeeds.
- Cross-user IDs, paths, reports, and expired/forged tokens cannot cross the
  user boundary.
- Existing report snapshot, upload cleanup, source deletion, structured QA,
  and restart persistence behavior remains covered.

## Non-goals

- Cross-process/distributed transactions or locks.
- Account deletion, report deletion, password reset, OAuth, or public APIs.
- Redesigning the Gradio UI.
- Changing persisted RAG chunk formats or Qdrant schemas.
- Automatically repairing unknown corrupt JSON.
