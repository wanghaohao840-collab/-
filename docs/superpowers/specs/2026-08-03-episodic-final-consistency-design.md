# Episodic Final Consistency Design

## Goal

Close the final Episodic/Qdrant residuals with compensating cross-store cleanup
and scoped legacy timestamp normalization.

## Compensating cleanup

For exact episode IDs, snapshot existing SQLite rows, delete SQLite first, then
delete vectors. If any SQLite or vector operation fails, restore every
snapshotted SQLite row and leave local episode/session maps unchanged. If
rollback itself fails, raise a combined runtime error rather than reporting
success. This is compensating atomicity, not a distributed transaction.

## Timestamp normalization

New episodes store canonical ISO timestamps. Before a user-scoped time-range
query runs for the first time, EpisodicMemory scrolls that user's episodic
vectors with vectors included, converts recognized legacy formats to ISO, and
upserts only changed points. Recognized forms include ISO, `YYYY/MM/DD`,
`YYYY/MM/DD HH:MM:SS`, and `YYYY-MM-DD HH:MM:SS`.

Unparseable values are not guessed. Existing local structured filtering and
keyword fallback remain available, while valid legacy values become eligible
for Qdrant DATETIME range filtering.

## Constraints

- Scope timestamp migration by `memory_type=episodic` and exact `user_id`.
- Run migration once per user per EpisodicMemory instance.
- Preserve public APIs, payload keys, concurrent import idempotency, and shared
  collection isolation.
- Do not add schema migrations, dependencies, or collection recreation.

## Verification

- Failure-injection tests prove SQLite rollback and unchanged local state.
- Protocol tests prove legacy timestamps are normalized before range search.
- Live Qdrant proves a legacy timestamp becomes range-queryable.
- Affected and full regressions must pass.
