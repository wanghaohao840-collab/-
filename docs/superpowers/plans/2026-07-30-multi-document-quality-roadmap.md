# Multi-Document Quality Roadmap Implementation Plan

> **For agentic workers:** This plan is executed inline in dependency order. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Establish a golden evaluation gate first, then incrementally improve multi-document retrieval quality, caching, task execution, citations, and structured comparison.

**Architecture:** Keep the existing UI → Assistant → RAGTool → Pipeline boundaries. The first phase adds an offline evaluation package without changing production behavior; later phases will introduce isolated helpers at the lowest layer that owns each concern.

**Tech Stack:** Python 3.12 project `venv`, pytest, standard library, existing JSON/Qdrant RAG backends, Gradio.

## Global Constraints

- Always run commands through `.\venv\Scripts\python.exe`.
- Preserve `document_id` isolation and legacy single-document calls.
- Do not change persisted chunk/cache formats without an explicit migration design.
- Do not call external LLMs from golden tests.
- Keep each phase independently testable.

## Phase 1: Golden evaluation baseline

- [ ] Add `evals/data/multi_document_qa.json` with joint, compare, summary, and missing-information cases.
- [ ] Add pure evaluation helpers in `evals/multi_document_qa.py` for loading cases, checking prompts/output, and aggregating failures.
- [ ] Add fake-Pipeline/fake-LLM tests that execute the production `RAGTool` path.
- [ ] Run focused golden tests and the existing multi-document regression suite.

## Phase 2: Hybrid retrieval and MMR

- [ ] Add failing tests for lexical overlap, vector score, selected-scope filtering, and MMR diversity.
- [ ] Implement a deterministic lexical scorer and candidate merge behind an opt-in retrieval mode.
- [ ] Preserve current vector-only default and backend contract.
- [ ] Run JSON/Qdrant parity tests.

## Phase 3: Versioned single-document summary cache

- [ ] Add cache tests for hit, document replacement invalidation, deletion invalidation, and prompt-version changes.
- [ ] Implement cache storage outside chunk persistence and make failures best-effort.
- [ ] Integrate cache into summary map calls without changing source references.

## Phase 4: Async summary task lifecycle

- [ ] Add task-state tests for queued/running/progress/complete/failed/cancelled.
- [ ] Implement bounded background execution and cancellation at Assistant/service boundary.
- [ ] Add Gradio polling and progress display.

## Phase 5: Citation UX and structured comparison

- [ ] Add source payload and renderer tests.
- [ ] Add structured comparison schema validation with Markdown fallback.
- [ ] Expose grouped sources and copyable references in the UI.

## Verification gate

```powershell
.\venv\Scripts\python.exe -m pytest tests/evals tests/tools/test_rag_tool_multi_document.py tests/memory/rag/test_pipeline_multi_document.py tests/memory/rag/test_qdrant_pipeline.py -q
```

Expected: all selected tests pass before moving to the next phase.
