# Qdrant Follow-up Hardening Design

## Goal

Close the verified Qdrant follow-ups in strict order: make Windows live-test
cleanup resilient, synchronize EpisodicMemory SQLite deletion, then push
importance/time ranges into Qdrant without weakening local isolation checks.

## Stage 1: live cleanup reliability

The official Windows server can briefly retain a collection directory handle
and return HTTP 500 while renaming it into `.deleted`. Live-test cleanup will
use a bounded helper that retries collection existence/deletion after 0.25,
0.5, and 1 second. It returns immediately when the collection is absent and
re-raises the final error. Product retry policy is unchanged.

## Stage 2: Episodic SQLite deletion consistency

`forget()` and `clear()` already remove exact vector IDs but leave matching
SQLite documents. They will delete the same IDs through the existing
`SQLiteDocumentStore.delete_document`. Backend deletions happen before local
maps are committed, so an early failure does not falsely report an empty local
state. No cross-backend transaction or rollback protocol is introduced.

## Stage 3: range-filter pushdown

Add a typed `VectorRange` value to the existing `VectorFilter` mapping. The
in-memory store evaluates it directly; Qdrant maps numeric bounds to `Range`
and datetime bounds to `DatetimeRange`. Payload-index validation gains `float`
and `datetime`.

EpisodicMemory declares `importance` and `timestamp` indexes and includes:

- `importance >= min_importance`;
- `timestamp >= start_time` when supplied; and
- `timestamp <= end_time` when supplied.

Existing structured/local filtering remains as defense in depth. Range
pushdown narrows candidates before the bounded vector top-k, preventing other
importance/time ranges from displacing valid episodes.

## Compatibility and non-goals

- Existing scalar/list filters and public memory methods remain compatible.
- No JSON migration, collection recreation, payload rewrite, distributed lock,
  or multi-store transaction is added.
- Cleanup retry stays test-only.
- Qdrant 1.18.2 and qdrant-client 1.18.0 remain pinned verification targets.

## Success criteria

Each stage has a failing test first, passes its focused suite, and is accepted
before the next begins. Final verification includes the real Qdrant runner,
affected regressions, and the full repository suite.
