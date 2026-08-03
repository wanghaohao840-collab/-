# Qdrant Follow-up Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the live cleanup flake, Episodic SQLite deletion gap, and bounded-query range-filter gap in dependency order.

**Architecture:** Keep cleanup retry inside live tests, reuse SQLiteDocumentStore deletion from EpisodicMemory, and extend the shared VectorStore filter value model with typed ranges. Every stage preserves local post-filtering and shared-collection isolation.

**Tech Stack:** Python 3.12, pytest 8.4.1, qdrant-client 1.18.0, Qdrant 1.18.2, SQLite

## Global Constraints

- Use repository `venv` for Python and repository-local pytest temp paths.
- Preserve unrelated concurrent import, GraphRAG, Neo4j, UI, and documentation changes.
- Do not commit, push, migrate data, recreate collections, or add dependencies.
- Complete and verify tasks 1 through 3 serially.

---

### Task 1: Retry transient live collection cleanup

**Files:**
- Modify: `tests/integration/test_qdrant_document_scope.py`

**Interfaces:**
- Produces: `_delete_collection_with_retry(client, collection_name, retry_delays=(0.25, 0.5, 1.0)) -> None`

- [ ] Add a fake-client test that fails deletion twice, succeeds on the third call, and uses zero delays.
- [ ] Run it and observe failure because the helper does not exist.
- [ ] Implement the bounded helper and route every live-test `finally` deletion through it.
- [ ] Run the unit helper test and `scripts/run_qdrant_integration.ps1`; require all live tests to pass and the process to stop.

### Task 2: Delete Episodic SQLite rows with vector IDs

**Files:**
- Modify: `hello_agents/memory/types/episodic.py`
- Modify: `tests/memory/test_episodic_vector_cleanup.py`
- Modify: `tests/integration/test_qdrant_document_scope.py`

**Interfaces:**
- Consumes: `SQLiteDocumentStore.delete_document(doc_id: str)`.
- Produces: forget/clear delete the same owned IDs from vector and SQLite stores before committing local maps.

- [ ] Add recording-document-store assertions for forget and clear; observe failure.
- [ ] Implement exact-ID SQLite deletion without changing public signatures.
- [ ] Extend live episodic lifecycle to assert the SQLite row is absent after clear.
- [ ] Run focused and live tests; proceed only on pass.

### Task 3: Push importance and timestamp ranges into Qdrant

**Files:**
- Modify: `hello_agents/memory/storage/vector_store.py`
- Modify: `hello_agents/memory/types/episodic.py`
- Modify: `tests/memory/storage/test_vector_store_contract.py`
- Modify: `tests/memory/storage/test_qdrant_vector_store.py`
- Modify: `tests/memory/test_episodic_vector_store_protocol.py`
- Modify: `tests/integration/test_qdrant_document_scope.py`

**Interfaces:**
- Produces: immutable `VectorRange(lt=None, lte=None, gt=None, gte=None)` filter value; payload schemas `float` and `datetime`.

- [ ] Add failing in-memory and fake-Qdrant range/index mapping tests.
- [ ] Add failing EpisodicMemory assertions for importance and datetime bounds.
- [ ] Implement numeric/datetime range evaluation and Qdrant model mapping.
- [ ] Declare `importance: float` and `timestamp: datetime`; push parsed bounds into search.
- [ ] Verify live schema and filtered retrieval against Qdrant 1.18.2.
- [ ] Run focused, affected, live, and full regressions.

### Commit

Do not commit or push without separate explicit authorization. Record handoff
and final integration evidence in the repository workflow artifacts.
