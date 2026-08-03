# GraphRAG Cross-Document Canonical Entities Implementation Plan

> **For agentic workers:** Execute tasks sequentially in the current session.
> Do not create commits because this repository is a shared dirty worktree and
> the user did not request version-control mutations.

**Goal:** Persist namespace-scoped canonical entities, expose bounded
cross-document entity evidence, and use it in comparison and multi-document
summary answers without weakening document or tenant isolation.

**Architecture:** Document-local extracted nodes remain authoritative for
evidence and lifecycle. Storage-managed `CanonicalEntity` nodes group matching
`Concept`, `Person`, and `KnowledgePoint` nodes by exact normalized name and
type through `REFERS_TO`; a legacy grouping fallback keeps old graphs readable.
The graph service validates ready state and RAGTool consumes shared entities
only for selected multi-document compare/summary operations.

**Tech Stack:** Python 3.12, Neo4j 5.x/Cypher, pytest, existing RAGTool and
KnowledgeGraphService contracts.

## Global constraints

- Preserve `rag_namespace + document_id` isolation for all local evidence.
- Canonical uniqueness is `(rag_namespace, entity_type, normalized_name)`.
- Entity linking is deterministic exact normalization only; no fuzzy or LLM
  merge.
- Cross-document APIs require 2-10 unique document IDs and bounded limits.
- No graph read returns Chunk content.
- `graph_mode=off|auto|required` semantics remain backward compatible.
- Use `D:\python_self_agent\venv\Scripts\python.exe` for every verification.
- Preserve unrelated shared-worktree changes and do not commit or push.

---

### Task 1: Neo4j canonical persistence and cross-document read

**Files:**

- Modify: `hello_agents/memory/storage/neo4j_store.py`
- Test: `tests/memory/storage/test_neo4j_store.py`
- Test: `tests/integration/test_neo4j_live.py`

**Interfaces:**

- Consumes: existing `replace_document_graph()`, `delete_document()`,
  `_require_namespace()`, `_limit()`, and recording-driver test seams.
- Produces:

```python
Neo4jGraphStore.get_cross_document_entities(
    document_ids: Iterable[str],
    *,
    query_terms: Iterable[str],
    rag_namespace: str = "default",
    entity_limit: int = 12,
    evidence_limit: int = 40,
) -> Dict[str, Any]
```

- Returns `{"entities": [...]}` where each entity has `canonical_id`,
  `entity_type`, `normalized_name`, `name`, and bounded `members`; every member
  has `document_id`, `id`, `type`, `name`, and safe `properties`.

- [ ] **Step 1: Add failing storage tests**

Add tests that assert the schema contains a canonical uniqueness constraint;
replacement runs marked canonical-link and orphan-cleanup queries with the
exact namespace/document parameters; deletion cleans only namespace orphans;
and `get_cross_document_entities()` validates cardinality, parameterizes the
query, limits documents, returns deterministic normalized rows, and removes
any `content` property.

- [ ] **Step 2: Verify the focused tests fail**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/storage/test_neo4j_store.py -q --basetemp=.runtime/pytest-cross-entity-storage-red
```

Expected: failures because canonical persistence and the public read method do
not exist.

- [ ] **Step 3: Implement canonical persistence**

Add a `CanonicalEntity` constraint and dedicated Cypher markers:
`GRAPH_LINK_CANONICAL` and `GRAPH_CLEAN_CANONICAL`. Build link rows only from
the three entity collections and only when `normalized_name` is non-empty.
Run linkage and orphan cleanup inside the existing replacement transaction.
Run orphan cleanup after document deletion in that deletion transaction.

Do not add `REFERS_TO` to extractor-controlled `RELATION_TYPES`.

- [ ] **Step 4: Implement bounded cross-document reads**

Normalize and deduplicate IDs, reject fewer than 2 or more than 10, normalize
terms, validate `entity_limit` and `evidence_limit`, and execute one
parameterized `GRAPH_QUERY_CROSS_DOCUMENT_ENTITIES` query. The query must group
local entities by type and normalized name, require at least two distinct
selected documents, optionally use canonical metadata, and never return Chunk
nodes or content. Sanitize member properties in Python as defense in depth.

- [ ] **Step 5: Extend the live fixture**

Create two documents in one unique namespace with the shared concept `neo4j`.
Assert one cross-document entity references both IDs, replace one document,
delete both in `finally`, and assert no scoped canonical result remains.

- [ ] **Step 6: Run storage tests green**

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/storage/test_neo4j_store.py -q --basetemp=.runtime/pytest-cross-entity-storage-green
```

Expected: all storage tests pass.

---

### Task 2: Ready-gated graph service API

**Files:**

- Modify: `hello_agents/memory/graph/service.py`
- Test: `tests/memory/graph/test_service.py`

**Interfaces:**

- Consumes: Task 1's `Neo4jGraphStore.get_cross_document_entities()` and the
  existing `_ready_state()`, `_success()`, and `_error()` envelope helpers.
- Produces:

```python
KnowledgeGraphService.get_cross_document_entities(
    document_ids: Iterable[str],
    query: str,
    *,
    entity_limit: int = 12,
    evidence_limit: int = 40,
) -> dict[str, Any]
```

- [ ] **Step 1: Add failing service tests**

Extend `FakeStore` with call recording and add tests for successful term
normalization/delegation, a not-ready selected document that prevents storage
access, invalid cardinality, and sanitized storage exceptions.

- [ ] **Step 2: Verify failures**

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/graph/test_service.py -q --basetemp=.runtime/pytest-cross-entity-service-red
```

Expected: failures because the service method does not exist.

- [ ] **Step 3: Implement the service method**

Deduplicate IDs while preserving order, validate 2-10 IDs, call
`_ready_state()` for every ID before storage, derive lexical terms with the
same bounded query-term logic used by `get_graph_context()`, delegate with the
service namespace, and return existing canonical success/error envelopes.

- [ ] **Step 4: Run service tests green**

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/graph/test_service.py -q --basetemp=.runtime/pytest-cross-entity-service-green
```

Expected: all graph-service tests pass.

---

### Task 3: Compare/summary integration, configuration, and acceptance

**Files:**

- Modify: `.env`
- Modify: `hello_agents/tools/builtin/rag_tool.py`
- Test: `tests/tools/test_rag_tool_multi_document.py`
- Modify: `README.md`
- Modify: task-packet handoff files created for this plan

**Interfaces:**

- Consumes: Task 2's service method and existing `_graph_sources()`,
  `_format_graph_context()`, `_append_graph_context()`, `_ask_compare()`, and
  `_ask_multi_summary()` paths.
- Produces: an internal bounded cross-document context helper whose result is
  added to comparison prompts and summary reduce prompts, plus canonical graph
  sources shaped with `document_ids` and stable `G-*` IDs.

- [ ] **Step 1: Synchronize local Neo4j configuration safely**

Replace `.env`'s URI, username, password, and database with the already
verified local values. Preserve all connection timeout/pool settings. Do not
print or document the password.

- [ ] **Step 2: Add failing RAGTool tests**

Extend `FakeGraphContextService` with the cross-document method and add tests
that prove: compare receives shared entity context and accepts its `G-*`
citation; summary map prompts do not receive shared context while reduce does;
`off` makes no call; `auto` falls back; `required` failure happens before any
LLM call; and an empty successful shared result is allowed.

- [ ] **Step 3: Verify failures**

```powershell
.\venv\Scripts\python.exe -m pytest tests/tools/test_rag_tool_multi_document.py -q --basetemp=.runtime/pytest-cross-entity-tool-red
```

Expected: new cross-document integration tests fail.

- [ ] **Step 4: Implement RAGTool integration**

Fetch shared entities only for 2+ selected documents and non-off graph mode.
Convert each result into a graph context/source with a stable ID derived from
the entity type, normalized name, namespace-neutral selected-document set, and
canonical ID when available. In compare, append it to the main context before
generation. In summary, prefetch before map execution but append it only to the
reduce context. On service failure, follow auto/required semantics; treat an
empty success as valid.

- [ ] **Step 5: Update README**

Document canonical identity rules, `REFERS_TO`, legacy read compatibility,
compare/summary behavior, exact-match limitation, and deletion cleanup.

- [ ] **Step 6: Run focused and live verification**

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/storage/test_neo4j_store.py tests/memory/graph/test_service.py tests/tools/test_rag_tool_graph.py tests/tools/test_rag_compare.py tests/tools/test_rag_tool_multi_document.py -q --basetemp=.runtime/pytest-cross-entity-focused
```

Expected: all focused tests pass.

Run the live test with `NEO4J_TEST_*` populated from `.env` without printing
credentials:

```powershell
.\venv\Scripts\python.exe -m pytest tests/integration/test_neo4j_live.py -q --basetemp=.runtime/pytest-cross-entity-live
```

Expected: live Neo4j test executes and passes, not skips.

- [ ] **Step 7: Run repository gates**

```powershell
.\venv\Scripts\python.exe -m pytest -q --basetemp=.runtime/pytest-cross-entity-full
.\venv\Scripts\python.exe -m compileall -q hello_agents tests
.\venv\Scripts\python.exe -m pip check
git diff --check
```

Expected: complete suite passes; optional unconfigured integrations may skip;
all static/dependency checks exit zero.

- [ ] **Step 8: Complete workflow handoffs**

Mark all packets done with exact files and test evidence, inspect the combined
diff, and create
`docs/agent-workflow/task-packets/2026-08-01-graphrag-cross-document-entities/FINAL_INTEGRATION_REVIEW.md`
with result `accepted` only if every requirement and gate passes.
