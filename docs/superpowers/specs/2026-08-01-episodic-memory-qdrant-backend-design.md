# EpisodicMemory Qdrant Backend Design

## Goal

Make `EpisodicMemory` use the repository's unified `VectorStore` boundary so a
configured Qdrant service is actually used, while preserving shared-collection
data isolation.

## Current problem

`EpisodicMemory` currently creates `InMemoryVectorStore` whenever no backend is
injected, even when `MemoryConfig.qdrant_url` is configured. It also consumes
legacy convenience methods that are outside the `VectorStore` protocol and
calls unfiltered `clear()`. If the implementation is switched to a shared
Qdrant collection without correcting cleanup, clearing episodic memory can
delete semantic or other users' vectors.

Session filtering is applied only after a bounded vector query. Highly ranked
episodes from other sessions can therefore displace valid candidates before
the local filter runs.

## Design

Initialization will follow the established SemanticMemory pattern:

1. select `config.qdrant_collection`;
2. use an injected `VectorStore`, or obtain one from
   `QdrantConnectionManager` using the configured URL, API key, dimension and
   isolation hints;
3. ensure the configured collection and vector dimension; and
4. declare keyword indexes for `memory_type`, `user_id`, and `session_id`.

Episode writes, searches, and deletion will consume only the public
`VectorStore` methods: `upsert`, `search`, and `delete_by_filter`.

`retrieve()` will pass `session_id` into the Qdrant equality filter together
with `memory_type` and optional `user_id`. The existing in-memory structured
filter remains as defense in depth and continues to enforce importance/time
conditions.

`forget()` and `clear()` will delete only the exact episode IDs owned by the
current instance. They will never call an unfiltered collection clear.

## Failure behavior

- Collection and index preparation errors remain visible during
  initialization.
- Existing episode write/search fallback behavior remains unchanged: vector
  operation failures are logged and retrieval can use the current keyword
  fallback.
- No Qdrant-to-in-memory automatic failover is introduced when a Qdrant URL is
  explicitly configured.

## Compatibility and isolation

- Public `EpisodicMemory` methods and payload keys remain unchanged.
- Existing `MemoryConfig` fields are reused.
- SQLite document persistence remains unchanged and outside this phase.
- Semantic and RAG vectors in a shared collection must survive episodic
  `forget()` and `clear()`.
- User and session equality scopes are applied remotely and rechecked locally.

## Verification

- Recording-store tests prove collection/index initialization and exact remote
  filters.
- An in-memory shared-collection test proves episodic clear preserves semantic
  points.
- A live Qdrant test proves schema creation, session/user retrieval, and
  cross-type cleanup isolation.
- Focused, affected, live, and full regression suites must pass.

## Non-goals

- Numeric/range Qdrant filters for importance or timestamps.
- SQLite deletion or transaction redesign.
- Changes to `QdrantConnectionManager` caching.
- Collection-per-user or collection-per-memory-type redesign.
- New retry, migration, double-write, or failover behavior.
