# GraphRAG Compare and Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend bounded Neo4j graph context and `G-*` citations to compare and multi-document summary answers.

**Architecture:** Reuse the existing graph-context service and formatting helpers. Compare appends selected-document graph context to its vector prompt and citation validator. Summary prefetches graph context before map execution, incorporates each document's graph evidence into map/cache/reduce contracts, and preserves existing vector-only fallback.

**Tech Stack:** Python 3.11+, existing RAGTool/Neo4j graph service, pytest 8.4.1, repository `venv`.

## Global Constraints

- Run all commands through `.\venv\Scripts\python.exe`.
- Preserve `(rag_namespace, document_id)` isolation.
- Preserve existing MMR, summary cache, progress/cancellation, and structured comparison behavior.
- `auto` must fall back; `required` must fail before any answer LLM call.
- Do not add cross-document entity merging, Neo4j writes, or graph UI.
- Do not commit or push without explicit user authorization.

---

### Task 1: Route graph controls into compare and summary

**Files:**
- Modify: `hello_agents/tools/builtin/rag_tool.py`
- Test: `tests/tools/test_rag_tool_multi_document.py`

**Interfaces:**
- `_ask_compare(..., graph_mode, graph_node_limit, graph_relation_limit)`
- `_ask_multi_summary(..., graph_mode, graph_node_limit, graph_relation_limit)`

- [ ] Add failing tests proving compare/summary receive the selected documents
  and honor off/auto/required.
- [ ] Forward graph mode and limits from `_ask` without passing them to Pipeline
  search kwargs.
- [ ] Run the multi-document tool tests.

### Task 2: Add GraphRAG compare composition

**Files:**
- Modify: `hello_agents/tools/builtin/rag_tool.py`
- Test: `tests/tools/test_rag_tool_multi_document.py`
- Test: `tests/tools/test_rag_compare.py`

**Interfaces:**
- Reuse `_graph_context_for_documents`, `_format_graph_context`,
  `_append_graph_context`, `_graph_sources`, and `_format_answer`.

- [ ] Add tests for graph prompt injection, selected-document calls,
  auto fallback, required pre-LLM failure, and `G-*` structured citations.
- [ ] Append graph context within the existing compare token budget.
- [ ] Include graph citation IDs in the structured comparison allowlist and
  graph sources in final action data/output.
- [ ] Run compare and multi-document focused tests.

### Task 3: Add GraphRAG summary map/cache/reduce composition

**Files:**
- Modify: `hello_agents/tools/builtin/rag_tool.py`
- Test: `tests/tools/test_rag_tool_multi_document.py`

**Interfaces:**
- `_summary_cache_key(..., graph_mode="off", graph_context=None)`
- Cached map values include `graph_sources`.

- [ ] Add tests for per-document map injection, reduce citation propagation,
  graph-context cache invalidation, auto fallback, and required zero-LLM
  failure.
- [ ] Prefetch selected-document graph contexts before map execution.
- [ ] Append each document's graph context within its map budget and include
  `G-*` refs in map/reduce allowed citations.
- [ ] Add graph mode/context fingerprint to cache keys and preserve graph
  sources on cache hits.
- [ ] Format final answers with deduplicated graph sources.
- [ ] Run summary and cache focused tests.

### Task 4: Documentation and combined verification

**Files:**
- Modify: `README.md`
- Modify: this task packet directory

- [ ] Document compare/summary GraphRAG behavior and remaining non-goals.
- [ ] Run real Neo4j live test in the repository `venv`.
- [ ] Run the GraphRAG/multi-document focused suite.
- [ ] Run `.\venv\Scripts\python.exe -m pytest -q`.
- [ ] Run compileall, `pip check`, and `git diff --check`.
