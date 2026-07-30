# Plan Review: Qdrant Stabilization and Live Verification

- Source plan: `docs/superpowers/plans/2026-07-29-qdrant-stabilization.md`
- Reviewed commit: `614f84e9d01179ce1272281f77e15550c1dcd764`
- Review date: `2026-07-29`
- Verdict: `accepted`

## Repository evidence

- Relevant implementation:
  - `hello_agents/memory/storage/vector_store.py:38` defines the current
    `VectorStore` protocol and shared data types.
  - `hello_agents/memory/storage/vector_store.py:272` defines the remote
    `QdrantVectorStore`.
  - `hello_agents/memory/storage/vector_store.py:295` currently delegates
    `create_collection` to the generic retry loop, so an uncertain response can
    issue duplicate create requests.
  - `hello_agents/memory/storage/qdrant_store.py:14` caches stores by URL,
    credential, collection, tenant, and namespace.
  - `hello_agents/memory/types/semantic.py:161` and `:188` already synchronize
    forget/clear operations with the vector store.
  - `hello_agents/memory/rag/qdrant_pipeline.py:28` uses the shared vector-store
    boundary and keeps document lifecycle behavior in the RAG layer.
- Relevant tests:
  - `tests/memory/rag/test_qdrant_pipeline.py` covers isolation, replacement,
    retry classification, redaction, summary sampling, metadata safety, and
    upsert batching with a fake client.
  - `tests/memory/test_qdrant_store.py` covers cache isolation and concurrent
    initialization.
  - `tests/memory/storage/test_vector_store_contract.py` covers the generic
    in-memory lifecycle and collection dimensions.
  - `tests/integration/test_qdrant_document_scope.py` contains two opt-in live
    tests controlled by `QDRANT_TEST_URL`.
- Configuration/runtime facts:
  - `requirements.txt` pins `qdrant-client==1.18.0`.
  - The active Python environment initially lacked `qdrant-client`.
  - `docker` is unavailable and WSL2 is not ready.
  - The official v1.18.2 GitHub release exposes
    `qdrant-x86_64-pc-windows-msvc.zip`.
  - `.gitignore` excludes `.runtime/`.
  - Focused baseline:
    `71 passed, 2 skipped` with repository-local `--basetemp`.
- Existing worktree changes to preserve:
  - All current staged and unstaged files. In particular,
    `hello_agents/memory/storage/vector_store.py`,
    `hello_agents/memory/rag/qdrant_pipeline.py`,
    `hello_agents/memory/types/semantic.py`, `README.md`, and the Qdrant tests
    already contain uncommitted work that is part of the starting reality.
  - Active Neo4j changes under `hello_agents/memory/graph/`,
    `hello_agents/memory/storage/neo4j_store.py`,
    `tests/memory/graph/`, and related packet files are unrelated and forbidden.

## Findings

### Blocking

- None.

### Required revisions

- None.

### Non-blocking notes

- Live server installation changes the active Python environment and downloads
  an ignored runtime artifact; both actions were explicitly authorized.
- Packet 3 deliberately follows live verification so index API behavior is
  tested against the pinned server/client pair.

## Accepted scope

- Goal: stabilize current Qdrant behavior, run a real v1.18.2 service, verify
  the full scoped lifecycle, and add idempotent RAG payload indexes.
- In scope:
  - uncertain collection-create reconciliation;
  - official Windows service runner;
  - pinned Python client installation and version evidence;
  - live vector-store and RAG lifecycle tests;
  - RAG payload indexes for `rag_namespace`, `document_id`, and `chunk_index`;
  - focused and combined regression verification.
- Out of scope:
  - JSON migration, double-write, failover, hot switching;
  - Docker/WSL installation;
  - Qdrant Cloud;
  - SemanticMemory-specific payload indexes;
  - concurrency redesign;
  - Neo4j and unrelated multi-user changes.
- Compatibility requirements:
  - JSON remains the default backend;
  - current public vector-store and RAG methods remain compatible;
  - source/page/document metadata remains available;
  - no runtime artifacts become tracked.
- Architecture/data-isolation constraints:
  - Qdrant-specific transport remains in the storage layer;
  - RAG operations remain namespace scoped and document operations remain
    document scoped;
  - `clear()` does not delete a shared collection.

## Packet graph

| Packet | Depends on | Parallel-safe | Owned files | Outcome |
|---|---|---:|---|---|
| `01-collection-create-safety.md` | none | no | `vector_store.py`, new focused test | uncertain creation is reconciled without duplicate create |
| `02-live-qdrant-verification.md` | 01 | no | live test, runner, README | official v1.18.2 lifecycle is reproducibly verified |
| `03-rag-payload-indexes.md` | 01, 02 | no | vector/RAG implementation and tests | required RAG payload fields are indexed and live verified |

## Packet readiness audit

| Packet | Goal/non-goals | Context/interfaces | Prerequisites | Change boundary | Acceptance/tests | Forbidden changes | Handoff format | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `01-collection-create-safety.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `02-live-qdrant-verification.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `03-rag-payload-indexes.md` | yes | yes | yes | yes | yes | yes | yes | yes |

No packet may have `status: ready` while any readiness column is `no`.

## Integration verification

- `.\venv\Scripts\python.exe -m compileall -q hello_agents`
- `.\venv\Scripts\python.exe -m pytest tests/memory tests/tools/test_rag_tool_backend_contract.py tests/tools/test_rag_tool_multi_document.py tests/integration/test_qdrant_document_scope.py -q --basetemp=.runtime/pytest-qdrant-final`
- `powershell -ExecutionPolicy Bypass -File scripts/run_qdrant_integration.ps1`
- `git status --short .runtime`

## Final integration review requirement

- Output:
  `docs/agent-workflow/task-packets/2026-07-29-qdrant-stabilization/FINAL_INTEGRATION_REVIEW.md`
- Required after: every implementation packet is `done`
- Result must be: `accepted | changes-required | blocked`
- Required checks:
  - cross-packet interfaces
  - missing requirements
  - duplicate or overlapping implementation
  - central integration points
  - architecture, compatibility, persistence, and isolation
  - combined regression verification

## Open decisions

- None.
