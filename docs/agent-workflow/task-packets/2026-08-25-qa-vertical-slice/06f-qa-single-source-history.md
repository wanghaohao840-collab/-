---
id: "qa-vertical-slice-06f"
title: "Restore single-source QA history"
status: "done"
parallel-safe: false
depends-on: ["qa-vertical-slice-06e"]
base-commit: "4d62bc6"
owner: "codex"
---

# Task Packet: Restore single-source QA history

## Goal

Remove the accidental legacy `history.questions` write from `PDFLearningAssistant.ask()` so durable QA resources remain the only product question-history source.

## Non-goals

- No change to answer generation, RAG scope/mode resolution, Memory events, usage statistics, report projection, migration, deletion, API or UI behavior.
- No removal of the legacy history reader/migrator during its compatibility window.

## Delivery context

Packet 07's full Python gate found two existing contract tests failing. Commit `1966956` deliberately removed flat-history writes when QA generation was isolated; commit `7c83b6c` accidentally restored the block while adding deletion compatibility. This violates the approved no-dual-write migration boundary and can create duplicate/stale history alongside SQLite QA resources.

## Relevant files and current interfaces

- `assistants/pdf_learning_assistant.py` — `ask()` currently writes the generated answer to RAG/Memory and, incorrectly, to flat `history.questions`.
- `tests/assistants/test_pdf_learning_assistant_multi_document.py` — existing summary and auto-mode regressions assert no flat-history write.
- Existing uncommitted Packet 07 E2E, documentation and baseline files must be preserved.

## Explicit change boundary

### Allowed files

- Modify: `assistants/pdf_learning_assistant.py`
- Test: existing assistant/QA migration and service tests
- Modify: this packet and Packet 07 dependency metadata

### Allowed behavior changes

- Successful direct assistant asks no longer append a flat question record to legacy history.

### Forbidden changes

- No production API/UI/schema/migration/deletion/report/Memory/RAG behavior changes.
- No test expectation relaxation and no compatibility reader removal.

## Interface contract

### Consumes

- Existing RAG answer result and QA service durable persistence.

### Produces

- The same answer and Memory side effects without creating a second question-history record.

### Invariants

- Legacy flat history remains readable/migratable.
- Auto-mode resolution, cancellation, explicit document scope, RAG calls and episodic/working Memory events are unchanged.
- Product QA messages remain persisted exactly once by `QaService`.

## Acceptance criteria

- [x] Both full-gate failures pass without changing their assertions.
- [x] Focused assistant suite and QA migration/service tests pass.
- [x] No flat `history.questions` append remains in `PDFLearningAssistant.ask()`.
- [x] Packet 07 real-server QA E2E remains green.

## Verification commands

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/assistants/test_pdf_learning_assistant_multi_document.py tests/test_qa_migration.py tests/test_qa_service.py --basetemp=.runtime/pytest-qa-06f
Push-Location web
npm run build
npx playwright test e2e/qa.spec.ts
Pop-Location
git diff --check
```

## Stop conditions

Stop if eliminating the write requires changing migration, report, deletion, API or durable QA schemas.

## Implementation handoff

- Packet: `qa-vertical-slice-06f`
- Status: `done`
- Delivered:
  - Removed the accidentally restored flat `history.questions` write from `PDFLearningAssistant.ask()` while preserving answer generation, Memory events and durable QA persistence.
- Files changed:
  - `assistants/pdf_learning_assistant.py` — removes only the legacy flat question-history append block.
  - `docs/agent-workflow/task-packets/2026-08-25-qa-vertical-slice/07-qa-integration-acceptance.md` — adds this correction as an acceptance prerequisite.
- Interfaces added or changed:
  - no API, schema, return value or public call signature changes.
- Acceptance evidence:
  - [x] The two original full-gate failures now pass unchanged.
  - [x] Focused assistant, QA migration and QA service suite: 30 passed.
  - [x] Real-server browser matrix: 12 passed across desktop/tablet/mobile with 0 skipped.
  - [x] `PDFLearningAssistant.ask()` contains no flat question-history append.
- Verification:
  - `python -m pytest -q tests/assistants/test_pdf_learning_assistant_multi_document.py tests/test_qa_migration.py tests/test_qa_service.py --basetemp=.runtime/pytest-qa-06f` — PASS, 30 tests.
  - `npm run build` — PASS.
  - `npx playwright test e2e/qa.spec.ts` — PASS, 12 tests / 3 viewports / 0 skipped.
  - `git diff --check` — PASS (line-ending notices only).
- Scope confirmation:
  - changed only allowed files: yes
  - forbidden areas untouched: yes
- Deviations:
  - none.
- Residual risks/follow-ups:
  - The full Python gate must be rerun by Packet 07 to replace the earlier 1029-pass/2-fail evidence with a fully green result.
- Commit:
  - `e67f314`
