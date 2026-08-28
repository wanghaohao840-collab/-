---
id: "qa-vertical-slice-06g"
title: "Align coordination test with durable QA ownership"
status: "done"
parallel-safe: false
depends-on: ["qa-vertical-slice-06f"]
base-commit: "9944007"
owner: "codex"
---

# Task Packet: Align coordination test with durable QA ownership

## Goal

Replace one obsolete coordination-test expectation that requires legacy flat-history writes with the accepted durable-QA single-source contract, while preserving structured document-scope and mode coverage.

## Non-goals

- No production code, API, schema, migration, report, deletion or UI change.
- No removal of legacy history readers or migration fixtures.

## Delivery context

After correction 06f, the full Python suite reached 1030 passes and one failure. The failing test predates durable QA and contradicts both the assistant regression tests and the approved no-dual-write architecture: it expects `PDFLearningAssistant.ask()` to append a structured flat-history record. The structured scope is still part of the generation contract, but persistence belongs to `QaService`/SQLite.

## Explicit change boundary

### Allowed files

- Modify: `tests/test_user_mutation_coordination.py`
- Modify: this packet and Packet 07 dependency metadata

### Allowed behavior changes

- Test contract only: assert structured scope/mode at the RAG generation boundary and no flat-history append.

### Forbidden changes

- No production code edits.
- No assertion weakening through skipped tests, broad matching or removed scope/mode checks.

## Acceptance criteria

- [x] The updated test asserts `document_ids == ["doc-a"]` and `mode == "summary"` on the RAG call.
- [x] The updated test asserts legacy `history.questions` remains empty.
- [x] Coordination, assistant multi-document, QA migration and QA service suites pass.
- [x] The previously conflicting full-gate test passes in the focused suite; Packet 07 owns the final full-suite rerun.

## Verification commands

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_user_mutation_coordination.py tests/assistants/test_pdf_learning_assistant_multi_document.py tests/test_qa_migration.py tests/test_qa_service.py --basetemp=.runtime/pytest-qa-06g
git diff --check
```

## Stop conditions

Stop if the structured scope/mode is not observable at the existing RAG boundary or the change requires production edits.

## Implementation handoff

- Packet: `qa-vertical-slice-06g`
- Status: `done`
- Delivered:
  - Replaced one pre-durable-QA dual-write expectation with the accepted generation-boundary and single-source persistence contract.
- Files changed:
  - `tests/test_user_mutation_coordination.py` — retains exact scope/mode assertions on the RAG call and asserts no flat-history append.
  - `docs/agent-workflow/task-packets/2026-08-25-qa-vertical-slice/07-qa-integration-acceptance.md` — adds this correction as an acceptance prerequisite.
- Interfaces added or changed:
  - no production interfaces or behavior changed.
- Acceptance evidence:
  - [x] Exact `document_ids` and `mode` generation arguments are asserted.
  - [x] Legacy `history.questions` is asserted empty.
  - [x] Coordination, assistant, migration and QA service focus: 45 tests passed.
- Verification:
  - `python -m pytest -q tests/test_user_mutation_coordination.py tests/assistants/test_pdf_learning_assistant_multi_document.py tests/test_qa_migration.py tests/test_qa_service.py --basetemp=.runtime/pytest-qa-06g` — PASS, 45 tests.
  - `git diff --check` — PASS (line-ending notices only).
- Scope confirmation:
  - changed only allowed files: yes
  - production files untouched: yes
- Deviations:
  - The final full-suite rerun is intentionally recorded by Packet 07 so the expensive repository-wide gate runs once after all corrections.
- Residual risks/follow-ups:
  - Packet 07 must replace the prior 1030-pass/1-fail run with a fully green result.
- Commit:
  - `0162cea`
