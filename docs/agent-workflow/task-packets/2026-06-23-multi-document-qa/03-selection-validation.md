---
id: "multi-document-qa-03"
title: "Consistent document selection and mode validation"
status: "done"
parallel-safe: false
depends-on: ["multi-document-qa-02"]
base-commit: "614f84e9d01179ce1272281f77e15550c1dcd764"
owner: "codex"
---

# Task Packet: Consistent document selection and mode validation

## Goal

Enforce one-to-ten selected documents and valid QA modes before side effects at the Assistant boundary and in both Gradio multi-select controls, while preserving single-document delete/switch semantics.

## Non-goals

- No RAG orchestration, storage, history migration, authentication, graph, or general UI redesign.

## Delivery context

Assistant and RAG Tool already enforce the maximum in several paths, and search UI already has `max_choices=10`. The question dropdown lacks the limit, and Assistant mode validation currently happens downstream after stats/memory side effects.

## Relevant files and current interfaces

- `assistants/document_selection.py:24` — label parsing and ordered dedupe.
- `assistants/pdf_learning_assistant.py:227` — `ask(question, limit, selected_documents, mode)`.
- `assistants/pdf_learning_assistant.py:314` — `search(query, limit, selected_documents)`.
- `ui/gradio_app.py:628` and `ui/gradio_app.py:634` — QA dropdown and mode radio.
- Existing changes to preserve: authentication/session/recovery handlers in the same UI and Assistant coordination/history changes.

## Prerequisites

### Packet dependencies

- `multi-document-qa-02` must be `done`.

### Repository/base state

- Active RAG mode set remains `auto`, `joint`, `compare`, `summary`.

### External prerequisites

- none

## Explicit change boundary

### Allowed files

- Modify: `hello_agents/memory/rag/result_utils.py`
- Modify: `hello_agents/tools/builtin/rag_context.py`
- Modify: `hello_agents/tools/builtin/rag_tool.py`
- Modify: `assistants/document_selection.py`
- Modify: `assistants/pdf_learning_assistant.py`
- Modify: `ui/gradio_app.py`
- Modify: `tests/assistants/test_pdf_learning_assistant_multi_document.py`
- Modify: `tests/ui/test_document_selection.py`
- Modify: `tests/ui/test_authenticated_handlers.py` only if handler expectations require it.

### Allowed behavior changes

- Centralize selected-count and mode validation in the shared RAG utility layer and add the missing UI maximum.

### Forbidden changes

- Do not alter authentication, recovery, reporting, deletion semantics, persistence schema, Tool/Pipeline interfaces, graph code, or styling.

## Interface contract

### Consumes

- Document labels in `name | document_id` form and existing Assistant methods.

### Produces

- Shared validation helpers or constants, understandable input errors, and identical limits in both query controls.

### Invariants

- Explicit empty scope never falls back to current document.
- Duplicate labels count once and preserve first-seen order.
- Invalid input causes no stats, memory, RAG, or history mutation.

## Required behavior

- Validate mode and compare document count before side effects.
- Enforce at most ten documents for both ask and search.
- Both UI query dropdowns expose `max_choices=10`; delete/switch still require one document.

## Acceptance criteria

- [ ] Empty, over-limit, invalid-mode, and single-document compare errors happen before side effects.
- [ ] Manual mode overrides keyword detection.
- [ ] Both UI multi-select controls cap selection at ten.
- [ ] Legacy current-document calls and history fields remain compatible.

## Test and verification commands

```powershell
python -m pytest tests/assistants/test_pdf_learning_assistant_multi_document.py tests/ui/test_document_selection.py tests/ui/test_authenticated_handlers.py -q
```

Expected: all tests pass.

## Stop conditions

Stop if the work requires modifying authentication/session behavior, persisted history schema, Tool/Pipeline code, or deletion semantics.

## Implementation handoff

- Packet: `multi-document-qa-03`
- Status: `done`
- Delivered:
  - Shared mode resolution, pre-side-effect Assistant validation, and matching ten-document UI limits.
- Files changed:
  - `hello_agents/memory/rag/result_utils.py` — canonical QA mode resolution.
  - `hello_agents/tools/builtin/rag_context.py`, `hello_agents/tools/builtin/rag_tool.py` — consume shared constants/rules.
  - `assistants/pdf_learning_assistant.py`, `ui/gradio_app.py` — validation and UI maximum.
  - Assistant/UI/result utility tests — boundary coverage.
- Interfaces added or changed:
  - `resolve_qa_mode(query, mode, summary_mode=False)` and `MAX_SELECTED_DOCUMENTS`.
- Acceptance evidence:
  - [x] Invalid modes and single-document auto comparison produce no side effects.
  - [x] Ask/search reject over ten documents.
  - [x] Both query controls declare `max_choices=10`.
- Verification:
  - `python -m pytest tests/assistants/test_pdf_learning_assistant_multi_document.py tests/ui/test_document_selection.py tests/memory/rag/test_result_utils.py -q --basetemp=.pytest-tmp-multi-doc-packet3b` — PASS (27 passed).
  - `.\venv\Scripts\python.exe -m pytest tests/test_corruption_recovery.py tests/test_p0_data_integrity.py tests/ui/test_authenticated_handlers.py -q --basetemp=.pytest-tmp-dependency-fix` — PASS (140 passed).
- Scope confirmation:
  - changed only allowed files: yes
  - forbidden areas untouched: yes
- Deviations:
  - none
- Residual risks/follow-ups:
  - none
- Commit:
  - `not committed`
