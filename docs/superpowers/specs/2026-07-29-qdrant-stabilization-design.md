# Qdrant Stabilization and Live Verification Design

## Purpose

Stabilize the repository's current, uncommitted Qdrant implementation before
adding further performance features. The result must preserve the existing
JSON backend, document and user isolation, source metadata, and the dependency
direction `UI -> Assistant -> Tool -> Memory/RAG/Storage`.

This work covers both Qdrant paths currently present in the repository:

- the reusable `VectorStore` used by semantic memory; and
- the Qdrant-backed RAG pipeline used for document chunks.

## Verified Current State

The following facts were verified against the worktree on 2026-07-29:

- `hello_agents/memory/storage/vector_store.py` defines a `VectorStore`
  protocol, an `InMemoryVectorStore`, and a remote `QdrantVectorStore`.
- `hello_agents/memory/storage/qdrant_store.py` selects and caches vector-store
  instances rather than implementing an in-memory class named as Qdrant.
- `SemanticMemory.forget()` and `SemanticMemory.clear()` issue scoped vector
  deletions.
- `QdrantRAGPipeline` uses the shared vector-store boundary and implements
  document and namespace filters.
- Existing focused fake-client and contract tests pass:
  `71 passed, 2 skipped`.
- The two skipped tests require a reachable external Qdrant service.
- The active Python 3.12 environment does not currently have
  `qdrant-client` installed.
- Docker is not installed and WSL2 is not ready for container execution.
- Qdrant release v1.18.2 provides an official
  `qdrant-x86_64-pc-windows-msvc.zip` artifact.
- The worktree contains extensive unrelated staged and unstaged changes,
  including active Neo4j and multi-user work. They must be preserved.

## Scope and Priority

### Phase 1: Stabilize the existing implementation

Audit the current Qdrant-related diff and its callers as one integrated
delivery. Fix only defects that can affect correctness, compatibility,
security, data isolation, or reliable verification.

Priority order:

1. namespace, user, and document isolation;
2. deletion and replacement consistency;
3. collection and vector-dimension compatibility;
4. retry classification and credential redaction;
5. JSON/Qdrant contract parity;
6. reliable automated verification.

Phase 1 must not introduce a broad redesign if the current `VectorStore`
boundary can satisfy the requirement.

### Phase 2: Live Qdrant verification

Install the repository-pinned `qdrant-client==1.18.0` into the active Python
environment. Download the official Qdrant v1.18.2 Windows x86-64 release to
an ignored repository-local runtime directory.

Run Qdrant as a hidden, temporary background process with:

- REST API on `127.0.0.1:6333`;
- runtime data and logs outside tracked source files;
- no API key, because the service is loopback-only and temporary;
- an explicit health check before tests start; and
- explicit process shutdown after verification, including failed-test paths.

The downloaded archive, executable, data, logs, and process identifiers must
not be committed.

Run the live integration tests against that service. Verify at minimum:

- collection creation and reuse;
- vector upsert and search;
- namespace and document filters;
- replacement and orphan cleanup;
- document deletion and namespace-scoped clear;
- restart persistence when the integration test supports it; and
- compatibility with `qdrant-client==1.18.0`.

### Phase 3: Focused production optimizations

After Phase 1 and Phase 2 pass, add only optimizations supported by observable
tests:

- payload indexes for high-frequency filter fields such as
  `rag_namespace`, `document_id`, and numeric `chunk_index`;
- idempotent index creation for existing collections;
- explicit handling for index-creation compatibility and failure;
- performance-sensitive deletion or scrolling improvements where the current
  implementation demonstrably performs an unnecessary full scan.

Exact document counts may continue to use scrolling unless a replacement
preserves correctness without adding a second source of truth.

Concurrency versioning and distributed locking are excluded from this
stabilization unless a reproducible current-code race violates an existing
contract.

## Component Boundaries

### Vector store

`hello_agents/memory/storage/vector_store.py` owns Qdrant client models,
collection operations, retry mapping, vector CRUD, filtering, and scrolling.
Callers must not depend directly on Qdrant model classes.

### Semantic memory

`hello_agents/memory/types/semantic.py` owns semantic-memory behavior,
importance-based forgetting, user scoping, fallback behavior, and graph/vector
coordination. It consumes the `VectorStore` protocol.

### RAG pipeline

`hello_agents/memory/rag/qdrant_pipeline.py` owns document chunk semantics,
namespace and document scopes, replacement behavior, result shaping, and
summary selection. It delegates Qdrant transport operations to the vector
store.

### Backend selection

`hello_agents/memory/storage/qdrant_store.py` and
`hello_agents/memory/rag/pipeline.py` own configuration resolution and backend
selection. Existing JSON defaults and environment-variable behavior remain
backward compatible.

## Data and Isolation Rules

- Every RAG read, count, update, and delete must include `rag_namespace`.
- Document-specific RAG operations must additionally include `document_id`.
- Semantic-memory vector operations must preserve their current user scope and
  memory type.
- Empty document scopes must never broaden into an unfiltered search.
- A failed or empty document parse must not erase an existing document unless
  the caller explicitly requests empty replacement.
- `clear()` must delete only the current logical scope and must not delete a
  shared collection.
- Payload fields used for filtering are authoritative at the payload top level.
  Metadata returned to callers must retain source, page, and document identity.

## Error and Retry Behavior

- Retry only transport interruptions, timeouts, and HTTP 5xx failures.
- Do not retry authentication failures, rate limits, other 4xx responses,
  validation errors, serialization errors, or ordinary programming errors.
- Retry only operations that are idempotent or use deterministic point IDs.
- Collection creation with an uncertain response must be reconciled by reading
  collection state before another creation attempt.
- API keys, URL credentials, and sensitive query values must not appear in
  exceptions, logs, or test output.
- Embedding dimension mismatches must fail explicitly; vectors must not be
  silently truncated or padded.

## Verification Strategy

Verification is layered so a failure identifies its source:

1. static import and compilation checks;
2. vector-store protocol and fake-client unit tests;
3. semantic-memory cleanup and fallback tests;
4. JSON and Qdrant RAG contract tests;
5. Tool and document-scope integration tests;
6. live Qdrant integration tests;
7. the broader affected Memory/RAG/Tool regression suite.

Tests that create temporary files must use a repository-local `--basetemp`
because the default Windows pytest temp root is not readable in this
environment.

The live-test runner must report the Qdrant server version, client version,
health-check result, exact test command, and cleanup result.

## Acceptance Criteria

- All focused Qdrant, vector-store, semantic-memory, RAG contract, and Tool
  tests pass.
- Previously skipped live integration tests execute and pass against Qdrant
  v1.18.2.
- The active client version is exactly 1.18.0.
- JSON remains the default backend and its affected regression tests pass.
- Namespace, user, and document isolation tests pass.
- No credentials appear in captured errors or logs.
- The temporary Qdrant process is stopped after verification.
- Runtime downloads and data are untracked.
- Unrelated staged and unstaged changes are preserved.
- Any residual production limitation is documented with evidence.

## Non-Goals

- No JSON-to-Qdrant migration or double-write.
- No runtime hot switching or automatic failover.
- No Qdrant Cloud provisioning.
- No Docker Desktop or WSL installation when the official Windows binary
  satisfies live verification.
- No Neo4j implementation or modification.
- No broad multi-user redesign.
- No commit, push, or pull request unless separately requested by the user.
