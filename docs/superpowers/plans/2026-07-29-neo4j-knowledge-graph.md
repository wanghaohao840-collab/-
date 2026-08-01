# Neo4j Knowledge Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real, document-isolated Neo4j knowledge-graph lifecycle—extraction, atomic persistence, status/recovery, query, retry, deletion, and RAG Tool integration—without using the graph for answer retrieval.

**Architecture:** `RAGTool` remains the orchestration boundary and calls a new `KnowledgeGraphService` after successful document ingestion and after RAG-first deletion. The service owns extraction, validation, status persistence, recovery, locking, and response envelopes; `Neo4jGraphStore` owns only driver lifecycle, constraints, parameterized Cypher, transactions, and graph reads. Both JSON and Qdrant pipelines expose the same document-chunk read interface used for retry.

**Tech Stack:** Python 3.10+, Neo4j Python Driver 5.x, JSON status manifest, existing `HelloAgentsLLM`, pytest.

## Global Constraints

- Preserve `UI -> Assistant -> Tool -> Memory/RAG/Storage`; storage must not import Tool or UI.
- Every graph read, write, retry, and delete is scoped by non-empty `rag_namespace` and `document_id`.
- RAG ingestion/deletion remains successful when LLM or Neo4j graph work fails.
- Graph replacement is one Neo4j transaction and never exposes a partial replacement.
- Neo4j credentials must not be retained in public/serializable store attributes or emitted through errors and representations.
- Default tests require neither a live Neo4j service nor a real LLM.
- Out of scope: GraphRAG answer retrieval, graph UI, cross-document entity merging, background workers, multi-process locking.

---

### Task 1: Real Neo4j storage adapter

**Files:**
- Modify: `requirements.txt`
- Replace: `hello_agents/memory/storage/neo4j_store.py`
- Modify: `hello_agents/memory/storage/__init__.py`
- Create: `tests/memory/storage/test_neo4j_store.py`

**Interfaces:**
- Consumes: injected Neo4j-compatible driver with `session(database=...)`.
- Produces: `Neo4jGraphStore(driver=None, database="neo4j", uri=None, username=None, password=None)`, `initialize_schema()`, namespace-scoped replace/build/query/delete methods.

- [ ] **Step 1: Write failing storage contract tests**

Cover configuration failure, credential-safe representation, idempotent schema initialization, parameterized document scope, transaction commit/rollback, replacement, independent cursors, chunk-content exclusion, and targeted deletion using an injected recording driver.

- [ ] **Step 2: Verify tests fail**

Run: `pytest -q tests/memory/storage/test_neo4j_store.py`

Expected: failure because the current in-memory placeholder lacks the contract.

- [ ] **Step 3: Implement the driver adapter**

Use static Cypher templates and parameter maps. Whitelist labels and relationship types in code. Execute delete plus complete graph write inside `session.execute_write`; use `execute_read` for queries. Do not store URI, username, or password.

- [ ] **Step 4: Verify storage tests**

Run: `pytest -q tests/memory/storage/test_neo4j_store.py`

Expected: all tests pass.

### Task 2: Extraction and durable graph status

**Files:**
- Create: `hello_agents/memory/graph/__init__.py`
- Create: `hello_agents/memory/graph/contracts.py`
- Create: `hello_agents/memory/graph/extractor.py`
- Create: `hello_agents/memory/graph/state.py`
- Create: `tests/memory/graph/__init__.py`
- Create: `tests/memory/graph/test_extractor.py`
- Create: `tests/memory/graph/test_state.py`

**Interfaces:**
- Produces: `GraphExtractor.extract(document_id, chunks, metadata) -> ExtractedGraph`, stable ID helpers, graph validation errors, `GraphStateRepository.get/upsert/list_by_status`, and the canonical response-envelope helper.

- [ ] **Step 1: Write failing extractor and state tests**

Use a fixed fake LLM. Test five-chunk/4,000-token batching, long-chunk windows preserving the source chunk ID, JSON parsing, retry classification and counters, stable IDs, normalization, relation whitelists, dangling-reference rejection, confidence clamping, dedupe, chapters, atomic state writes, 500-character sanitized errors, and status recovery inputs.

- [ ] **Step 2: Verify tests fail**

Run: `pytest -q tests/memory/graph/test_extractor.py tests/memory/graph/test_state.py`

Expected: import failures for the new graph package.

- [ ] **Step 3: Implement contracts, extractor, and state repository**

Represent graph data with dataclasses whose `to_store_payload()` returns JSON-safe dictionaries. The extractor invokes `llm.chat`, strips optional fenced JSON, retries only safe/transient failures up to three calls per batch, validates the whole document before return, and never persists chunk text to state. The state repository writes a temporary file then replaces the manifest atomically.

- [ ] **Step 4: Verify graph-domain tests**

Run: `pytest -q tests/memory/graph/test_extractor.py tests/memory/graph/test_state.py`

Expected: all tests pass.

### Task 3: Knowledge graph lifecycle service

**Files:**
- Create: `hello_agents/memory/graph/service.py`
- Modify: `hello_agents/memory/graph/__init__.py`
- Create: `tests/memory/graph/test_service.py`

**Interfaces:**
- Consumes: extractor from Task 2, state repository from Task 2, store from Task 1, and `chunk_loader(document_id)`.
- Produces: `KnowledgeGraphService` methods `build_document_graph`, `get_document_graph`, `get_chapter_tree`, `get_concept_relations`, `get_knowledge_dependencies`, `get_person_relations`, `get_graph_status`, `retry_document_graph`, and `delete_document_graph`, each returning `{success, document_id, status, data, error, page}`.

- [ ] **Step 1: Write failing lifecycle tests**

Test state transitions, build/LLM counts, safe failure envelopes, exact-build recovery, retry admission, cleanup retry, query-ready gating, limit validation, RAG chunk reload isolation, same-document serialization, different-document independence, and lock-registry cleanup.

- [ ] **Step 2: Verify tests fail**

Run: `pytest -q tests/memory/graph/test_service.py`

Expected: failure because the service does not exist.

- [ ] **Step 3: Implement the lifecycle service**

Use a ref-counted per-document `threading.Lock` registry. Public mutation methods acquire exactly once and call private locked methods. On initialization, recover persisted `building` states by comparing store `build_id` and `graph_status`. Persist `ready` only after the store transaction returns.

- [ ] **Step 4: Verify service tests**

Run: `pytest -q tests/memory/graph/test_service.py`

Expected: all tests pass.

### Task 4: RAG pipeline and Tool integration

**Files:**
- Modify: `hello_agents/memory/rag/pipeline.py`
- Modify: `hello_agents/memory/rag/qdrant_pipeline.py`
- Modify: `hello_agents/tools/builtin/rag_tool.py`
- Modify: `hello_agents/memory/types/semantic.py`
- Create: `tests/tools/test_rag_tool_graph.py`
- Modify: `tests/memory/test_semantic_fallback.py`

**Interfaces:**
- Produces: `get_document_chunks(document_id) -> list[dict]` on both pipelines; optional `graph_service` injection on `RAGTool`; graph actions and graph status in structured add/delete results.

- [ ] **Step 1: Write failing integration tests**

Test exact document chunk loading on both backends, graph build after successful import, graph failure preserving RAG success, RAG-first deletion with `cleanup_pending`, graph query action routing, required document IDs, retry loading only the target document, and SemanticMemory operation without Neo4j configuration.

- [ ] **Step 2: Verify tests fail**

Run: `pytest -q tests/tools/test_rag_tool_graph.py tests/memory/test_semantic_fallback.py`

Expected: failures for missing integration APIs.

- [ ] **Step 3: Implement minimal integration**

Create a graph service from environment configuration only when all required Neo4j values are present, unless one is injected. Keep legacy string messages while placing canonical graph envelopes under `_last_action_data["graph"]`. Graph failures are reported but do not change successful RAG operation status.

- [ ] **Step 4: Verify integration and regression**

Run: `pytest -q tests/tools/test_rag_tool_graph.py tests/memory/test_semantic_fallback.py tests/memory/rag tests/tools/test_rag_tool_backend_contract.py tests/tools/test_rag_tool_multi_document.py`

Expected: all selected tests pass.

### Task 5: Optional live contract and documentation

**Files:**
- Create: `tests/integration/test_neo4j_live.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `NEO4J_TEST_URI`, `NEO4J_TEST_USERNAME`, `NEO4J_TEST_PASSWORD`, `NEO4J_TEST_DATABASE`.

- [ ] **Step 1: Add a live integration test guarded by `NEO4J_TEST_URI`**

The test initializes constraints, replaces a uniquely named document, queries it, replaces it again without duplicates, deletes it, and always attempts scoped cleanup.

- [ ] **Step 2: Update README configuration and scope**

Document the four runtime variables, graph actions, weak-consistency behavior, default exclusion of chunk text, single-worker limitation, and explicit GraphRAG retrieval non-goal.

- [ ] **Step 3: Run final verification**

Run: `pytest -q tests/memory/graph tests/memory/storage/test_neo4j_store.py tests/tools/test_rag_tool_graph.py tests/integration/test_neo4j_live.py`

Expected: unit/contract tests pass; live test skips when `NEO4J_TEST_URI` is absent.

Run: `pytest -q --ignore=tests/ui/test_authenticated_handlers.py`

Expected: all non-UI tests pass. The UI suite is separately blocked in the current interpreter when Gradio is not installed.
