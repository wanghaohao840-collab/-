# Qdrant Stabilization and Live Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize the current unified Qdrant vector-store implementation, verify it against an official Qdrant v1.18.2 Windows service, and add tested payload indexes without disturbing unrelated worktree changes.

**Architecture:** Keep `QdrantVectorStore` as the only Qdrant transport boundary and keep `QdrantRAGPipeline` responsible for document semantics and isolation. First make collection creation safe under uncertain responses, then establish a reproducible live-service test harness, and only then add idempotent RAG payload indexes.

**Tech Stack:** Python 3.12, `qdrant-client==1.18.0`, Qdrant server v1.18.2 Windows x86-64, pytest, PowerShell.

## Global Constraints

- Preserve the JSON backend as the default and do not add migration, double-write, failover, or runtime hot switching.
- Preserve `UI -> Assistant -> Tool -> Memory/RAG/Storage`.
- Every RAG operation remains scoped by `rag_namespace`; document operations additionally remain scoped by `document_id`.
- Empty document scopes must not broaden into unfiltered operations.
- Do not silently truncate or pad embedding vectors.
- Never expose API keys or URL credentials in errors or logs.
- Preserve all unrelated staged and unstaged changes, especially Neo4j and multi-user work.
- Use repository-local pytest temporary directories.
- Do not commit runtime binaries, archives, Qdrant data, logs, or process IDs.
- Do not commit, push, or create a pull request unless separately requested.

---

### Task 1: Reconcile uncertain collection creation safely

**Files:**
- Modify: `hello_agents/memory/storage/vector_store.py:295`
- Create: `tests/memory/storage/test_qdrant_vector_store.py`

**Interfaces:**
- Consumes: `QdrantVectorStore.ensure_collection(collection_name: str, dimension: int, distance: str = "Cosine") -> None`.
- Produces: the same public signature; internally collection validation is reusable and `create_collection` is never blindly retried.

- [x] **Step 1: Write failing tests for uncertain creation**

Create a fake client whose first `collection_exists` result is false and whose
single `create_collection` call creates the collection but raises
`TimeoutError`. Its subsequent `get_collection` returns compatible vector
configuration.

```python
def test_uncertain_create_reconciles_by_reading_collection():
    client = UncertainCreateClient(create_then_raise=True)
    store = QdrantVectorStore(client=client, retry_delays=(0, 0, 0))

    store.ensure_collection("documents", dimension=2)

    assert client.create_calls == 1
    assert client.get_calls == 1
```

Add a second test where `create_collection` raises before creating anything and
`get_collection` returns 404. Assert that the original create failure is
reported and `create_calls == 1`.

- [x] **Step 2: Run the new tests and confirm the unsafe behavior**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/storage/test_qdrant_vector_store.py -q --basetemp=.runtime/pytest-qdrant-create
```

Expected: the first test fails because current `_call()` retries
`create_collection`; no test may hang on retry delays.

- [x] **Step 3: Implement single-attempt creation with reconciliation**

Refactor collection validation into:

```python
def _validate_collection(
    self,
    collection_name: str,
    collection_info: Any,
    dimension: int,
    distance: str,
) -> None:
    config = self._extract_vector_config(collection_info)
    size = getattr(config, "size", None)
    actual = str(
        getattr(
            getattr(config, "distance", None),
            "value",
            getattr(config, "distance", ""),
        )
    )
    if int(size or 0) != int(dimension) or actual.lower() != distance.lower():
        raise RAGCollectionError(
            f"Qdrant collection {collection_name} is incompatible: "
            f"vector size {size}, distance {actual}; expected {dimension}/{distance}"
        )
```

When the collection does not exist, call `client.create_collection()` exactly
once. If the raw exception is retryable according to `_should_retry()`, call
`get_collection()` and validate the returned configuration. If reconciliation
cannot find a compatible collection, raise the mapped original create error.
Map non-retryable create errors immediately and do not reconcile them.

- [x] **Step 4: Run focused storage and RAG tests**

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/storage/test_qdrant_vector_store.py tests/memory/storage/test_vector_store_contract.py tests/memory/rag/test_qdrant_pipeline.py -q --basetemp=.runtime/pytest-qdrant-create
```

Expected: all tests pass.

- [x] **Step 5: Record delivery without committing**

Update the corresponding task packet handoff with changed files, exact test
counts, deviations, and `Commit: not committed`.

---

### Task 2: Run an official Qdrant service and verify the real lifecycle

**Files:**
- Create: `scripts/run_qdrant_integration.ps1`
- Modify: `tests/integration/test_qdrant_document_scope.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `QDRANT_TEST_URL`, optional `QDRANT_TEST_API_KEY`,
  `QdrantVectorStore`, `QdrantRAGPipeline`, and `DocumentSegment`.
- Produces: a repeatable PowerShell runner that starts Qdrant v1.18.2,
  runs live tests, and always stops only the process it started.

- [x] **Step 1: Expand the live integration test**

Add a deterministic two-dimensional embedder:

```python
class LiveTestEmbedder:
    def encode(self, text):
        text = str(text).lower()
        return [1.0, 0.0] if "alpha" in text else [0.0, 1.0]
```

Add a live test that monkeypatches
`hello_agents.memory.rag.qdrant_pipeline.get_text_embedder` and
`get_dimension`, creates two pipelines sharing a temporary collection but
using different namespaces, and verifies:

```python
assert {item["metadata"]["document_id"] for item in ns_a.search("alpha", limit=10)} == {"doc-a"}
assert ns_b.stats()["chunk_count"] == 1
assert ns_a.replace_document("doc-a", [DocumentSegment("alpha replacement", {})])["success"]
assert ns_a.delete_document("doc-a")["chunks_removed"] == 1
assert ns_b.stats()["chunk_count"] == 1
assert ns_b.clear()["chunks_removed"] == 1
```

Delete the temporary collection in `finally`.

- [x] **Step 2: Create the PowerShell live-test runner**

The runner must:

1. resolve repository root from `$PSScriptRoot`;
2. use `.runtime/qdrant/v1.18.2` and `.runtime/qdrant-test`;
3. download only
   `https://github.com/qdrant/qdrant/releases/download/v1.18.2/qdrant-x86_64-pc-windows-msvc.zip`
   when the executable is absent;
4. extract with `Expand-Archive`;
5. fail before startup if `127.0.0.1:6333` already responds;
6. start `qdrant.exe` with `Start-Process -WindowStyle Hidden -PassThru`;
7. poll `http://127.0.0.1:6333/` for at most 30 seconds and require version
   `1.18.2`;
8. set `QDRANT_TEST_URL=http://127.0.0.1:6333`;
9. run the live test file with repository-local `--basetemp`; and
10. stop and wait for the owned process in `finally`.

The process must use a runtime working directory so default Qdrant storage and
logs remain under `.runtime/`.

- [x] **Step 3: Install and verify the pinned client**

```powershell
.\venv\Scripts\python.exe -m pip install qdrant-client==1.18.0
.\venv\Scripts\python.exe -c "from importlib.metadata import version; assert version('qdrant-client') == '1.18.0'; print(version('qdrant-client'))"
```

Expected output: `1.18.0`.

- [x] **Step 4: Run live integration**

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_qdrant_integration.ps1
```

Expected:

- Qdrant server version `1.18.2`;
- all tests in `tests/integration/test_qdrant_document_scope.py` pass with no skips;
- the started process is stopped;
- `.runtime/` remains untracked.

- [x] **Step 5: Document the reproducible command**

Add a concise README section that states the pinned server/client versions,
the Windows command above, runtime location, loopback-only behavior, and the
fact that artifacts are ignored.

- [x] **Step 6: Run focused fake and live regressions**

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/test_qdrant_store.py tests/memory/test_semantic_vector_store_protocol.py tests/memory/test_semantic_fallback.py tests/memory/rag tests/tools/test_rag_tool_backend_contract.py -q --basetemp=.runtime/pytest-qdrant-focused
powershell -ExecutionPolicy Bypass -File scripts/run_qdrant_integration.ps1
```

Expected: focused suite passes; live suite passes with no skips.

- [x] **Step 7: Record delivery without committing**

Update the corresponding packet handoff. Include client/server versions, live
test counts, health result, process cleanup, and `Commit: not committed`.

---

### Task 3: Create idempotent RAG payload indexes

**Files:**
- Modify: `hello_agents/memory/storage/vector_store.py`
- Modify: `hello_agents/memory/rag/qdrant_pipeline.py`
- Modify: `tests/memory/storage/test_vector_store_contract.py`
- Modify: `tests/memory/rag/test_qdrant_pipeline.py`
- Modify: `tests/integration/test_qdrant_document_scope.py`

**Interfaces:**
- Consumes: the stabilized `VectorStore` protocol and live runner from Tasks 1
  and 2.
- Produces:
  `VectorStore.ensure_payload_indexes(collection_name: str, indexes: Mapping[str, str]) -> None`.

- [x] **Step 1: Add failing protocol and fake-client tests**

Extend the protocol contract test:

```python
store.ensure_payload_indexes(
    "documents",
    {
        "rag_namespace": "keyword",
        "document_id": "keyword",
        "chunk_index": "integer",
    },
)
```

The in-memory implementation must accept this as a no-op.

Extend the Qdrant RAG fake client with `create_payload_index()` call capture and
assert pipeline construction requests exactly:

```python
{
    "rag_namespace": "keyword",
    "document_id": "keyword",
    "chunk_index": "integer",
}
```

- [x] **Step 2: Run tests and confirm the missing interface**

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/storage/test_vector_store_contract.py tests/memory/rag/test_qdrant_pipeline.py -q --basetemp=.runtime/pytest-qdrant-indexes
```

Expected: fail because `ensure_payload_indexes` does not exist.

- [x] **Step 3: Implement the vector-store index boundary**

Add to `VectorStore`:

```python
def ensure_payload_indexes(
    self,
    collection_name: str,
    indexes: Mapping[str, str],
) -> None: ...
```

Implement a no-op in `InMemoryVectorStore`.

In `QdrantVectorStore`, map:

```python
schema_types = {
    "keyword": self.models.PayloadSchemaType.KEYWORD,
    "integer": self.models.PayloadSchemaType.INTEGER,
}
```

For each field, call `client.create_payload_index` with
`collection_name`, `field_name`, `field_schema`, and `wait=True`. Use the
existing retry/error mapping because index creation is idempotent. Reject
unknown schema names with `ValueError` before issuing a remote call.

- [x] **Step 4: Request indexes from the RAG pipeline**

Immediately after collection assurance, call:

```python
self.vector_store.ensure_payload_indexes(
    self.collection_name,
    {
        "rag_namespace": "keyword",
        "document_id": "keyword",
        "chunk_index": "integer",
    },
)
```

Do not add SemanticMemory-specific indexes in this task.

- [x] **Step 5: Verify indexes on the live collection**

Extend the live RAG test to call `get_collection()` and assert the payload
schema reports keyword indexes for `rag_namespace` and `document_id`, and an
integer index for `chunk_index`.

- [x] **Step 6: Run focused and live verification**

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/storage/test_qdrant_vector_store.py tests/memory/storage/test_vector_store_contract.py tests/memory/rag/test_qdrant_pipeline.py -q --basetemp=.runtime/pytest-qdrant-indexes
powershell -ExecutionPolicy Bypass -File scripts/run_qdrant_integration.ps1
```

Expected: all fake-client tests pass and live payload schema assertions pass.

- [x] **Step 7: Run the combined affected regression**

```powershell
.\venv\Scripts\python.exe -m compileall -q hello_agents
.\venv\Scripts\python.exe -m pytest tests/memory tests/tools/test_rag_tool_backend_contract.py tests/tools/test_rag_tool_multi_document.py tests/integration/test_qdrant_document_scope.py -q --basetemp=.runtime/pytest-qdrant-final
```

Run the integration file through the live runner as a separate command so its
tests execute without skips. Expected: compilation succeeds and all affected
tests pass.

- [x] **Step 8: Record delivery without committing**

Update the packet handoff with exact verification evidence and
`Commit: not committed`.
