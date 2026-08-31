---
id: "notes-vertical-slice-05"
title: "统一 QA 与 legacy Notes 入口"
status: "done"
parallel-safe: false
depends-on: ["notes-vertical-slice-03", "notes-vertical-slice-04"]
base-commit: "8ac2775dc2cb0563095f0fad5b4a83abcbf52fb9"
owner: "unassigned"
---

# Task Packet: 统一 QA 与 legacy Notes 入口

## Goal

在完成回答/引用上提供“记为笔记”草稿入口，并把 Gradio/`PDFLearningAssistant` 的添加、清空、回忆、统计和报告切换到同一 `NoteService`，永久停止新 Notes 的 JSON/随机 Memory 双写。

## Non-goals

- 不修改 Note schema/repository/API、Notes 工作台布局、Penpot 或部署配置。
- 不删除旧 `history.json.notes` 或未精确匹配的 legacy Memory。
- 不改变文档/问题的 legacy recall、RAG 或报告格式范围之外的行为。

## Delivery context

React Notes 已可独立使用，但产品闭环还要求 QA 来源入口和 legacy 兼容。当前 assistant `add_note` 写随机 semantic Memory 并追加 JSON，recall/stats/report 再读不同来源，必须一次性改为统一领域读写，避免长期分叉。

## Relevant files and current interfaces

- `web/src/components/QaWorkspace/QaWorkspace.tsx:77` — citation UI/action insertion seam.
- `web/src/pages/QaPage.tsx` and tests — capability, completed message and router context.
- `assistants/pdf_learning_assistant.py:527-558` — current dual-write `add_note`.
- `assistants/pdf_learning_assistant.py:560-583` — current JSON-only clear.
- `assistants/pdf_learning_assistant.py:587-738` — current mixed recall/stats/report Note reads.
- `ui/gradio_app.py:954-974,1362-1386` — stable handler names and bindings.
- Packet 03 runtime-injected `NoteService`; packet 04 identifier-only `/notes` prefill contract.
- Existing changes to preserve: completed dependencies and review artifacts.

## Prerequisites

### Packet dependencies

- `notes-vertical-slice-03` and `notes-vertical-slice-04` must be `done`.

### Repository/base state

- Base plan commit: `8ac2775dc2cb0563095f0fad5b4a83abcbf52fb9`; use dependency handoff commits.

### External prerequisites

- none.

## Explicit change boundary

### Allowed files

- Modify: `docs/agent-workflow/task-packets/2026-08-30-notes-vertical-slice/05-qa-legacy-notes-integration.md` (status and handoff only)
- Modify: `web/src/components/QaWorkspace/QaWorkspace.tsx`
- Modify: `web/src/components/QaWorkspace/QaWorkspace.test.tsx`
- Modify: `web/src/pages/QaPage.tsx`
- Modify: `web/src/pages/QaPage.test.tsx`
- Modify: `web/src/styles/qa.css`
- Modify: `assistants/pdf_learning_assistant.py`
- Modify: `ui/gradio_app.py`
- Create: `tests/assistants/test_pdf_learning_assistant_notes.py`
- Create: `tests/ui/test_note_handlers.py`
- Create: `tests/integration/test_note_legacy_cutover.py`
- Modify: `tests/test_assistant_user_isolation.py`
- Modify: `tests/test_p0_data_integrity.py`

### Allowed behavior changes

- Add completed QA answer/citation navigation actions.
- Delegate only legacy Note operations/Note portions of recall/stats/report to injected NoteService.
- Update outdated Note-specific Gradio copy while keeping handler/binding names stable.

### Forbidden changes

- No edits to packet 02/03/04 implementation files, database/API routes, package manifests, Penpot, E2E or deployment.
- No direct QA→Memory/History writes, immediate save on click, source excerpts/body/user IDs in URLs, new JSON Note writes or broad Memory clearing.
- Do not change document/question recall, import, RAG or unrelated report semantics.

## Interface contract

### Consumes

- `UserRuntime.note_service` and trusted internal NoteService user-context methods.
- Packet 04 URL: `/notes?source_kind=qa_answer&qa_message_id=...` or `qa_citation` plus `citation_id`; identifiers only.
- Stable QA `message_id`, `citation_id`, completed status and route capability.

### Produces

- Completed assistant action “记为笔记” and citation action “记录此引用”, each opening editable unsaved draft.
- Assistant Note methods that read/write/count/search/recent/clear through NoteService only.
- Cross-entry integration tests proving React API and legacy assistant share rows.

### Invariants

- QA action never saves until explicit Notes save and never exposes excerpt/body in URL.
- Pending/failed QA messages have no Note action; route-disabled state does not expose unusable action.
- History JSON remains unchanged by new Note operations; projection worker alone changes learning-note Memory.
- User isolation, clear semantics and handler names remain intact.

## Required behavior

- Answer/citation actions are keyboard labeled, ≥44px on mobile and navigate using stable server IDs.
- `PDFLearningAssistant` obtains injected service from runtime; supported app path must not fall back to legacy dual write.
- Standalone assistant tests use an isolated repository-backed adapter, not the old persistence path.
- Recall combines NoteService FTS Note hits with existing document/question hits without duplicate legacy Note scans.
- Stats use active Note count; report uses active count + latest ten Notes; in-process `notes_added` is no longer authoritative.
- Clear soft-deletes current user's Notes/enqueues projections and explicitly states documents/QA remain.

## Implementation guidance

Add frontend tests first. For legacy, capture history before/after each operation and inspect Memory calls so dual writes cannot hide. Keep trusted user-context adapter inside NoteService API established by packet 02; do not manufacture `UserSession` objects in the assistant. Preserve Chinese success/error message intent and current handler arity.

## Acceptance criteria

- [ ] Completed answer/citation actions open identifier-only Notes drafts; other states do not.
- [ ] Legacy add is visible through NoteService/API and leaves old history Note array unchanged.
- [ ] Legacy clear/recall/stats/report use SQLite Note truth and preserve document/question behavior.
- [ ] No random-ID `knowledge_type=learning_note` write or broad Memory clear occurs.
- [ ] Cross-user and multiple-session isolation regressions pass.

## Test and verification commands

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/integration/test_note_legacy_cutover.py tests/assistants/test_pdf_learning_assistant_notes.py tests/ui/test_note_handlers.py tests/test_assistant_user_isolation.py tests/test_p0_data_integrity.py --basetemp=.runtime/pytest-notes-cross-entry
Set-Location web
npm test -- --run src/components/QaWorkspace/QaWorkspace.test.tsx src/pages/QaPage.test.tsx src/pages/NotesPage.test.tsx
npm run typecheck
Set-Location ..
git diff --check
```

Expected: all commands PASS and tests explicitly prove no dual writes.

## Stop conditions

Stop with a reality-conflict report if NoteService trusted interfaces differ, supported runtimes lack injection, QA IDs/statuses changed, a handler needs an unowned API change, or legacy behavior cannot be preserved in boundary.

## Reality-conflict resolution: QA action touch target

- The existing QA action styling is centralized in `web/src/styles/qa.css`; the new answer/citation actions cannot meet the approved >=44px mobile target using only component markup.
- Resolution: extend this packet boundary narrowly to `web/src/styles/qa.css` for the Notes action classes only. Preserve all unrelated QA layout and behavior.

## Reality-conflict resolution: Notes capability propagation

- `QaWorkspace.MessageList` cannot determine whether the Notes route is enabled; the existing capability-query seam is owned by `web/src/pages/QaPage.tsx`.
- Resolution: extend this packet boundary narrowly to `web/src/pages/QaPage.tsx` so it can read `useNotesCapabilities()` and pass the resulting availability flag to the message list. No other QA page behavior may change.

## Reality-conflict resolution: obsolete JSON Note regression fixtures

- `tests/test_assistant_user_isolation.py` and `tests/test_p0_data_integrity.py` construct legacy runtimes without `NoteService` and explicitly assert that `add_note()` writes `history.json`. Those assertions contradict the accepted permanent cutover in this packet and make the exact verification command require the forbidden fallback.
- Resolution: extend this packet boundary only to those two tests. Replace Note-specific fixtures/assertions with the shared repository-backed `NoteService`/application-service path, or assert explicit safe unavailability where the test is solely about an intentionally service-less runtime. Preserve their document deletion, report snapshot, concurrency and user-isolation intent; do not weaken unrelated assertions.

## Implementation handoff

- Status: done
- Files changed:
  - `assistants/pdf_learning_assistant.py`
  - `ui/gradio_app.py`
  - `web/src/components/QaWorkspace/QaWorkspace.tsx`
  - `web/src/components/QaWorkspace/QaWorkspace.test.tsx`
  - `web/src/pages/QaPage.tsx`
  - `web/src/pages/QaPage.test.tsx`
  - `web/src/styles/qa.css`
  - `tests/assistants/test_pdf_learning_assistant_notes.py`
  - `tests/integration/test_note_legacy_cutover.py`
  - `tests/ui/test_note_handlers.py`
  - `tests/test_assistant_user_isolation.py`
  - `tests/test_p0_data_integrity.py`
  - `docs/agent-workflow/task-packets/2026-08-30-notes-vertical-slice/05-qa-legacy-notes-integration.md`
- Dependency/interfaces:
  - Consumed Packet 03 runtime-injected trusted `NoteService` facade from `4e4abb5` and `5086aca`.
  - Consumed Packet 04 identifier-only `/notes` prefill navigation contract from `57f516b`/`801b8f4`.
  - Completed QA answers expose keyboard-labeled answer/citation links with only stable QA/citation identifiers only after a successful Notes capability response explicitly returns `enabled: true`; disabled, pending, failed, and error states expose no action.
  - Supported legacy runtime operations create, clear, FTS-search, count, and list recent Notes through `NoteService`; missing-service operations fail explicitly without JSON/Memory Note fallback, while document/question history recall remains available.
  - Recall reserves visibility for both sources when capacity permits, then backfills the bounded result set from the source with remaining hits. Note-only and sparse-legacy queries use the full limit; legacy-saturated queries still expose an FTS Note. It never scans legacy JSON Notes.
  - Authenticated `/api/v1/notes` integration coverage proves the React list contract sees assistant-created rows and a later assistant session recalls an API-created row.
  - The approved regression-fixture extension moves Note-specific isolation, concurrency, and report-snapshot assertions to real `ApplicationServices`/`NoteService` runtimes without weakening their original guarantees.
- Acceptance criteria:
  - [x] Completed answer/citation actions open identifier-only Notes drafts; disabled, pending, and error capability states, plus pending/failed QA states, do not expose actions.
  - [x] Legacy assistant creates rows visible through the shared SQLite-backed `NoteService` and leaves legacy history Notes unchanged.
  - [x] Legacy clear/recall/stats/report use active Note rows while preserving document/question recall and report behavior; combined recall is bounded, fully allocated, and cannot starve FTS Notes.
  - [x] Supported paths and explicit missing-service behavior do not issue random `learning_note` Memory writes, JSON Note writes, or broad Memory clears; Notes projection remains the sole Memory writer.
  - [x] Cross-user and multi-session integration coverage confirms shared same-user rows and user-scoped clear.
- Verification:
  - `& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/assistants/test_pdf_learning_assistant_notes.py --basetemp=.runtime/pytest-notes-recall-allocation` — PASS (6 passed in 1.83s).
  - `& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/integration/test_note_legacy_cutover.py tests/assistants/test_pdf_learning_assistant_notes.py tests/ui/test_note_handlers.py tests/test_assistant_user_isolation.py tests/test_p0_data_integrity.py --basetemp=.runtime/pytest-notes-cross-entry` — PASS (17 passed in 88.45s).
  - `Set-Location web; npm test -- --run src/components/QaWorkspace/QaWorkspace.test.tsx src/pages/QaPage.test.tsx src/pages/NotesPage.test.tsx` — PASS (18 files, 155 tests).
  - `Set-Location web; npm run typecheck` — PASS.
  - `git diff --check` — PASS.
- Deviations:
  - The repository `test` script expands the supplied frontend paths to the full `src tests` suite; the final verification passed all 18 files.
- Residual risks:
  - The repository frontend command expands to the full `src tests` suite. Two earlier attempts exposed existing focus-timing failures in unowned `src/auth/AuthProvider.test.tsx` and `src/auth/ProtectedRoute.test.tsx`; the final identical retry passed all 155 tests. No Packet 05 files touch either test.
- Commit:
  - `57fc2c0` — closes the independent review findings and updates regression coverage.
  - `944e044` — fully allocates bounded combined recall results and adds allocation regressions.
