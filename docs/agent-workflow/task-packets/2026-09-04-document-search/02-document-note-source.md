---
id: "document-search-02"
title: "Backward-compatible document-chunk note provenance"
status: "done"
parallel-safe: false
depends-on: ["document-search-01"]
base-commit: "daeb24f"
owner: "Codex-inline"
---

# Task Packet: Backward-compatible document-chunk note provenance

## Goal

Users can save an editable note from a search hit while the server re-resolves its current document chunk and persists a deletion-aware source without breaking legacy QA sources or database rollback.

## Non-goals

- General multi-source editor redesign, attachment storage, schema rebuild, search UI, report integration.

## Delivery context

The old table rejects non-QA kinds. A separate additive table keeps old binaries readable and lets the new repository project both physical source types into the existing model.

## Relevant files and current interfaces

- `app/database.py:294` — old QA-only CHECK; must remain byte-compatible.
- `app/note_models.py:143` — selector validates QA provenance.
- `app/note_service.py:278` — server-side QA resolution and idempotent replay pattern.
- `app/note_repository.py:480,621` — source write/guard/scrub transaction and digest.
- `app/qa_deletion.py:516` — atomic document/conversation source scrubbing caller.
- Packet 01 produces locator and resolver.

## Prerequisites

- Packet 01 done. Current note service/repository tests pass before edits.
- External prerequisites: none.

## Explicit change boundary

- Modify: `app/database.py`, note models/repository/service, existing deletion scrubbing seam, note API schema/route, bootstrap injection.
- Test: database, note model/repository/service/source deletion and note API files listed in Plan Task 2.
- Forbidden: alter/drop/rebuild `note_sources`; change old QA selector behavior; trust client snapshots; mutate note bodies on source deletion; use offline inventory; touch RAG algorithms/UI/deployment.

## Interface contract

- Consumes: Packet 01 `SearchChunkLocator`, `DocumentSearchService.resolve_chunk`.
- Produces: additive `note_document_sources`, selector/API kind `document_chunk`, unified `NoteSource.kind`, and atomic document-source scrubbing.
- Invariants: replay lookup precedes source re-resolution; repository transaction rechecks deletion fence; old binaries may ignore the new table and keep using old tables.

## Required behavior

- Server persists only resolved name/excerpt/locator/checksum; request cannot supply authority snapshots.
- Missing, foreign, fenced or checksum-changed locators fail safely. Idempotent replay still succeeds after later deletion.
- `_get` deterministically merges sources. Document deletion redacts source and increments/enqueues affected notes once; other users and QA sources remain unchanged.
- Fresh DB, upgrade DB, repeated initialization and old-table-only read all pass.

## Implementation guidance

Follow Plan Task 2. Use a companion table with indexes and composite note FK. Branch `_insert_source` and guard logic by source kind. Extend document scrubbing to both tables in its existing transaction; conversation scrubbing touches only legacy QA sources.

## Acceptance criteria

- [x] Additive schema and old-table SQL-read compatibility proven; operational rollback limitation documented separately.
- [x] Server re-resolution/tamper/fence/user isolation proven.
- [x] Idempotency, unified reads and projection behavior proven.
- [x] Existing QA source and deletion tests unchanged in meaning.

## Test and verification commands

`venv/Scripts/python.exe -m pytest -q tests/test_note_models.py tests/test_note_repository.py tests/test_note_service.py tests/test_note_source_deletion.py tests/api/test_note_routes.py --basetemp=deploy-state/pytest-search-notes`

Reality correction: this repository has no `tests/test_database.py`. Place fresh/upgrade/reinitialization companion-schema tests in `tests/test_note_repository.py` (already owned). Baseline on 2026-09-04: these note tests plus Packet 01 search tests passed, 82 tests total. Preserve old QA request digests. Resolve document sources outside the runtime mutation lock, then recheck deletion authority in the write transaction. A completed deletion between resolution and insertion must also reject the new source, not only an active fence. Additive-table readability alone does not prove operational rollback safety: account for old-image deletion/projection behavior during final release review.

Expected: zero failures.

## Stop conditions

Stop if Packet 01 is not done, a destructive table migration becomes necessary, source guard cannot share the deletion transaction, or files outside this boundary are needed.

## Implementation handoff

Report schema compatibility evidence, interfaces, tests/counts, source race behavior, scope confirmation, deviations/risks and `not committed`.

### 2026-09-04 implementation handoff

- Status: **done** in `D:/python_self_agent/.worktrees/bge-m3-runtime-identity`, branch `codex/bge-m3-runtime-identity`. Not published to the stable application.
- Delivered: additive `note_document_sources` with composite ownership FK and live/deleted-row checks; merged deterministic source reads and document-source filtering; server-resolved `DocumentChunkSourceSelector`; API discriminated source validation; singleton resolver injection; atomic source scrubbing across both tables with one note-version/outbox update per affected note.
- API: POST `/api/v1/notes` accepts `{kind: document_chunk, locator: {document_id, chunk_id, chunk_index, content_sha256}}` inside `source`. Client title/excerpt snapshots and QA fields are rejected. Existing body/concept/tags/request-ID fields remain unchanged. Missing/changed/foreign sources: 404; deletion race: 409; unavailable resolver: retryable 503; session expiry during resolution: 401. Existing create/replay 201/200 semantics remain.
- Files changed: `app/database.py`, `app/note_models.py`, `app/note_repository.py`, `app/note_service.py`, `app/bootstrap.py`, `api/schemas/notes.py`, `api/routes/notes.py`; tests in `test_note_models.py`, `test_note_repository.py`, `test_note_service.py`, `test_note_source_deletion.py`, `api/test_note_routes.py` under `tests/`.
- Compatibility: old `note_sources` DDL and existing row bytes stay unchanged across initialization; fresh and repeated initialization and partial-index recreation pass. Old-table SQL reads still work. Source snapshots are not added to semantic-memory projection; user-edited body remains authoritative.
- Race evidence: resolver runs without user mutation lock; early replay does not re-resolve a deleted source; queued/running/failed/completed deletion between resolution and insertion rejects the source; actual deletion worker scrubs a direct source even with no QA conversation. Mixed QA/document sources increment a note once, conversation-only scrub leaves direct document sources intact, and other users are unaffected.
- Verification:
  - Pre-edit packet baseline: **45 passed**.
  - Red tests: new source kind and absent companion schema failed as expected before implementation.
  - `python -m pytest -q tests/api tests/test_document_search.py tests/test_note_models.py tests/test_note_repository.py tests/test_note_service.py tests/test_note_source_deletion.py --basetemp=D:/python_self_agent/deploy-state/pytest-document-note-final-api`: **203 passed**.
  - `python -m pytest -q --basetemp=D:/python_self_agent/deploy-state/pytest-document-note-full`: **1753 passed, 8 skipped, 1 warning**, 320.40 seconds, exit 0. Run began before the final session-expiry refinement and three final tests.
  - Final current-code combined run: `python -m pytest -q tests/test_note_models.py tests/test_note_repository.py tests/test_note_service.py tests/test_note_source_deletion.py tests/api/test_note_routes.py tests/test_note_projection.py tests/test_note_migration.py tests/integration/test_note_legacy_cutover.py tests/ui/test_note_handlers.py tests/assistants/test_pdf_learning_assistant_notes.py tests/test_document_search.py tests/api/test_search_routes.py --basetemp=D:/python_self_agent/deploy-state/pytest-document-note-final-combined`: **118 passed**.
  - All commands used `D:/python_self_agent/venv/Scripts/python.exe` from the isolated worktree. `git diff --check` passed; only line-ending notices. The full-suite warning is the existing local-Qdrant payload-index limitation; external service tests remain conditional.
- Scope: allowed files only; production data, `.env`, containers, model/index identity and Neo4j untouched. Existing dirty work preserved.
- Deviations: no QA selector redesign; introduced a separate document selector alongside it to retain existing callers/digests. No extra preview HTTP endpoint. Existing deletion transaction hook was sufficient; `app/qa_deletion.py` did not need editing.
- Residual release constraint: old-table readability is not ongoing old-image deletion compatibility. Use safe updater paired image/cold-backup rollback, never old-image-only rollback against newly written source data. Packet 04 records this explicitly.
- Next: Packet 03 responsive UI, then Packet 04 release and final integration review. No claim of completed end-user `/search` yet.
- Commit: **not committed, not pushed**.
