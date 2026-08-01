---
id: "multi-document-qa-01"
title: "Bounded context and stable source primitives"
status: "done"
parallel-safe: false
depends-on: []
base-commit: "614f84e9d01179ce1272281f77e15550c1dcd764"
owner: "codex"
---

# Task Packet: Bounded context and stable source primitives

## Goal

Provide reusable prompt-budget and source-reference helpers that fit copied RAG results within the model input budget while preserving minimum semantic content and never mutating persisted/search result objects.

## Non-goals

- No changes to retrieval ranking, map-reduce orchestration, UI, persistence, Qdrant, or graph behavior.

## Delivery context

`RAGTool._build_context` currently contains partial inline budget logic. It may truncate content to one character and mixes source identity, rendering, and fitting. The accepted design requires a reusable helper with a 200-character floor, stable IDs computed from full pre-truncation content, and explicit capacity failure.

## Relevant files and current interfaces

- `hello_agents/core/llm.py:18` — `HelloAgentsLLM`, currently without `estimate_tokens` or `context_window_tokens`.
- `hello_agents/tools/builtin/rag_tool.py:1300` — inline `_context_budget`, `_citation_id`, and `_build_context` behavior to be delegated later.
- `hello_agents/memory/rag/result_utils.py:32` — canonical source digest and dedupe functions.
- Existing changes to preserve: all current staged/unstaged LLM, RAG, Qdrant, Neo4j, and multi-user changes.

## Prerequisites

### Packet dependencies

- none

### Repository/base state

- Base commit: `614f84e9d01179ce1272281f77e15550c1dcd764` plus the documented dirty worktree.

### External prerequisites

- none

## Explicit change boundary

### Allowed files

- Modify: `hello_agents/core/llm.py`
- Create: `hello_agents/tools/builtin/rag_context.py`
- Create: `tests/core/__init__.py`
- Create: `tests/core/test_llm_budget.py`
- Create: `tests/tools/test_rag_context.py`

### Allowed behavior changes

- Add context-window/token estimation interfaces and pure copied-result fitting helpers.

### Forbidden changes

- Do not modify persisted chunks, retrieval backends, Assistant/UI, graph code, requirements, or unrelated tests.
- Do not add dependencies.

## Interface contract

### Consumes

- `content_digest`, `dedupe_results_by_source`, `normalize_page_number` from `result_utils`.

### Produces

- `HelloAgentsLLM.estimate_tokens(text) -> int` and `context_window_tokens`.
- Context constants and helpers that return rendered context, included copied results, truncation flag, and explicit capacity failure.

### Invariants

- Source IDs derive from full untruncated content.
- Input objects and cached chunks are not mutated.
- Retained non-empty chunk text is never shortened below `min(original_length, 200)` characters.

## Required behavior

- Account for fixed prompt, output reserve, and safety margin.
- Remove low-value unprotected chunks before truncating protected chunks.
- Preserve protected comparison/summary anchors or return a capacity error.
- Mark truncated included results and sources; exclude removed results.

## Implementation guidance

- Keep helpers deterministic and standard-library only.
- Use shallow copied result/metadata dictionaries with copied text; avoid coupling to a concrete Pipeline.
- Raise a focused capacity exception internally and let RAG Tool translate it later.

## Acceptance criteria

- [ ] Exact and fallback token estimators share one public LLM interface.
- [ ] Context fitting respects reserves and the 200-character floor.
- [ ] Source IDs remain stable after truncation.
- [ ] Inputs remain unchanged and removed chunks do not appear in returned sources.

## Test and verification commands

Run from repository root:

```powershell
python -m pytest tests/core/test_llm_budget.py tests/tools/test_rag_context.py -q
```

Expected: all tests pass.

## Stop conditions

Stop if implementation requires changing persistence, retrieval, graph, Assistant, UI, or dependency manifests.

## Implementation handoff

- Packet: `multi-document-qa-01`
- Status: `done`
- Delivered:
  - Unified LLM token estimation and pure bounded-context/source helpers.
- Files changed:
  - `hello_agents/core/llm.py` — context window and token estimation interface.
  - `hello_agents/tools/builtin/rag_context.py` — copied-result budget fitting and stable sources.
  - `tests/core/test_llm_budget.py`, `tests/tools/test_rag_context.py` — focused coverage.
- Interfaces added or changed:
  - `HelloAgentsLLM.estimate_tokens`, `context_window_tokens`, `fit_context`, `context_budget`, `citation_id`.
- Acceptance evidence:
  - [x] All packet criteria covered by focused tests.
- Verification:
  - `python -m pytest tests/core/test_llm_budget.py tests/tools/test_rag_context.py -q` — PASS (8 passed).
- Scope confirmation:
  - changed only allowed files: yes
  - forbidden areas untouched: yes
- Deviations:
  - none
- Residual risks/follow-ups:
  - Integration into RAG Tool is packet 02.
- Commit:
  - `not committed`
