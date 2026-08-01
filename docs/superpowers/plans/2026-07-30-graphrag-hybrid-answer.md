# GraphRAG Hybrid Answer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bounded Neo4j graph context to ordinary RAG answers while preserving vector-only fallback and namespace/document isolation.

**Architecture:** The Neo4j adapter performs a parameterized, document-scoped lexical seed search and one-hop relation expansion. `KnowledgeGraphService` gates reads on a ready build and returns a bounded graph-context envelope. `RAGTool` composes vector context plus optional graph context for normal `ask`; `off`, `auto`, and `required` modes define fallback behavior.

**Tech Stack:** Python 3.11+, official `neo4j` driver, existing `SimpleRAGPipeline`/Qdrant pipeline, existing `HelloAgentsLLM`, pytest/unittest.

## Global Constraints

- Preserve `(rag_namespace, document_id)` isolation for every graph operation.
- Keep graph context additive; do not remove or reorder existing vector sources.
- Never put user query text into Cypher source; pass it as parameters.
- Do not expose Chunk content from graph queries.
- `auto` must preserve the current vector-only answer when graph is unavailable.
- Do not change `compare`, `summary`, cross-document entity merge, or UI behavior in this phase.
- Do not commit or push unless the user explicitly requests it.

---

### Task 1: Add bounded graph-context storage query

**Files:**
- Modify: `hello_agents/memory/storage/neo4j_store.py`
- Test: `tests/memory/storage/test_neo4j_store.py`

**Interfaces:**
- Produce `Neo4jGraphStore.get_graph_context(document_id, *, query_terms, rag_namespace="default", node_limit=8, relation_limit=16) -> dict`.
- Return `{entities: list[dict], relations: list[dict]}` with entity properties excluding Chunk content and relation properties copied as evidence metadata.

- [ ] Add a fake-driver test that asserts query terms, namespace, document ID, and numeric limits are parameters and that returned entities/relations are bounded.
- [ ] Implement a read transaction matching only `Concept`, `KnowledgePoint`, `Person`, and `Chapter` within the exact namespace/document scope; expand one hop and return stable IDs/types/names/properties.
- [ ] Remove `content` from any returned Chunk-like property defensively and deduplicate entities/relations deterministically.
- [ ] Run `D:\Anaconda\python.exe -m pytest tests/memory/storage/test_neo4j_store.py -q`; expect all storage tests to pass.

### Task 2: Add ready-gated graph context service

**Files:**
- Modify: `hello_agents/memory/graph/service.py`
- Modify: `hello_agents/memory/graph/__init__.py`
- Test: `tests/memory/graph/test_service.py`

**Interfaces:**
- Produce `KnowledgeGraphService.get_graph_context(document_id, query, *, node_limit=8, relation_limit=16) -> dict`.
- Success data shape: `{"entities": [...], "relations": [...], "query_terms": [...]}`.

- [ ] Add tests for ready gating, query-term normalization, namespace forwarding, and store failure sanitization.
- [ ] Implement bounded Unicode-aware term extraction: retain alphanumeric runs and CJK bigrams, remove empty terms, cap at 12 terms.
- [ ] Call the store only when the document state is `ready`; otherwise return the existing canonical graph error envelope.
- [ ] Return a successful `graph_response` with graph data and preserve sanitized error behavior.
- [ ] Run `D:\Anaconda\python.exe -m pytest tests/memory/graph/test_service.py -q`.

### Task 3: Compose graph context in ordinary RAG answers

**Files:**
- Modify: `hello_agents/tools/builtin/rag_tool.py`
- Test: `tests/tools/test_rag_tool_graph.py`
- Test: `tests/tools/test_rag_tool_multi_document.py`

**Interfaces:**
- Extend `ask` with `graph_mode="auto"` and optional `graph_node_limit=8`, `graph_relation_limit=16`.
- Add private helpers `_graph_context_for_documents(...)`, `_format_graph_context(...)`, and `_format_graph_sources(...)`.
- `_format_answer(answer, results, truncated=False, graph_sources=None)` remains backward compatible when `graph_sources` is absent.

- [ ] Add tests that `auto` injects graph entities/relations and `G-` citations into the ordinary ask prompt/output.
- [ ] Add tests that `off` never calls the graph service, `auto` falls back after graph failure, and `required` returns an explicit error without calling the LLM.
- [ ] Add a multi-document joint-ask test proving graph calls are limited to selected document IDs.
- [ ] Implement graph-mode validation and schema documentation without changing compare/summary branches.
- [ ] Compose bounded graph context after vector search and before `_generate`; preserve the existing vector context and citation formatting.
- [ ] Run `D:\Anaconda\python.exe -m pytest tests/tools/test_rag_tool_graph.py tests/tools/test_rag_tool_multi_document.py -q`.

### Task 4: Documentation, live verification, and regression

**Files:**
- Modify: `README.md`
- Modify: `docs/agent-workflow/task-packets/2026-07-30-graphrag-hybrid-answer/REVIEW.md`
- Modify: `docs/agent-workflow/task-packets/2026-07-30-graphrag-hybrid-answer/*.md`
- Test: `tests/integration/test_neo4j_live.py`

- [ ] Document `graph_mode`, bounded graph context, fallback semantics, and the explicit non-goals.
- [ ] Extend the live test with a small graph-context read and assert the returned Neo4j entity/relation data.
- [ ] Run `D:\Anaconda\python.exe -m pytest tests/integration/test_neo4j_live.py -q` with `NEO4J_TEST_*` configured; expect `1 passed`.
- [ ] Run `D:\Anaconda\python.exe -m pytest -q --basetemp=.runtime\pytest-graphrag`; record the result.
- [ ] Run `D:\Anaconda\python.exe -m compileall -q hello_agents tests` and `git diff --check`.
