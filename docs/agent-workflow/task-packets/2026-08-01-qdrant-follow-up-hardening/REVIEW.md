# Plan Review: qdrant-follow-up-hardening

- Source plan: `docs/superpowers/plans/2026-08-01-qdrant-follow-up-hardening.md`
- Reviewed commit: `9de3c35a8ee933011b71973400519fbf5f0f6dfc`
- Review date: `2026-08-01`
- Verdict: `accepted`

## Repository evidence

- `tests/integration/test_qdrant_document_scope.py` directly deletes five live
  collections; a prior real run returned Qdrant HTTP 500 / Windows access
  denied during the final rename and passed unchanged on rerun.
- `SQLiteDocumentStore.delete_document` exists, while Episodic `forget/clear`
  currently delete only vectors and local maps.
- `VectorFilter` currently supports scalar/list equality only;
  qdrant-client 1.18.0 exposes `Range`, `DatetimeRange`, FLOAT and DATETIME
  payload schema types.
- Episodic local filtering already parses importance and timestamps, providing
  the compatibility backstop for remote pushdown.
- Current owned files contain accepted uncommitted Episodic Qdrant work and
  must retain it. Concurrent import idempotency in `EpisodicMemory.add()` is
  excluded and preserved.

## Findings

### Blocking

- None.

### Required revisions

- None.

### Non-blocking notes

- SQLite/vector deletion is not an atomic distributed transaction; this plan
  improves matching deletion but does not claim rollback guarantees.
- Existing remote points must already carry parseable ISO timestamps for
  datetime range matching; current MemoryItem payloads do.

## Accepted scope

- Three serial packets: test cleanup retry, SQLite deletion, range pushdown.
- No production retry change, migration, collection recreation, dependency
  addition, or unrelated refactor.
- Preserve user/session/type isolation and local post-filters.

## Packet graph

| Packet | Depends on | Parallel-safe | Owned files | Outcome |
|---|---|---:|---|---|
| `01-live-cleanup-retry.md` | none | no | live integration test | transient cleanup is bounded and repeatable |
| `02-episodic-sqlite-cleanup.md` | 01 | no | episodic and cleanup/live tests | vector and SQLite IDs stay aligned |
| `03-vector-range-filters.md` | 02 | no | vector store, episodic, focused/live tests | numeric/time bounds are remotely filtered |

## Packet readiness audit

| Packet | Goal/non-goals | Context/interfaces | Prerequisites | Change boundary | Acceptance/tests | Forbidden changes | Handoff format | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `01-live-cleanup-retry.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `02-episodic-sqlite-cleanup.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `03-vector-range-filters.md` | yes | yes | yes | yes | yes | yes | yes | yes |

## Integration verification

- `powershell -ExecutionPolicy Bypass -File scripts/run_qdrant_integration.ps1`
- `\.\venv\Scripts\python.exe -m pytest tests/memory tests/integration/test_qdrant_document_scope.py -q --basetemp=.runtime/pytest-qdrant-hardening`
- `\.\venv\Scripts\python.exe -m pytest -q --basetemp=.runtime/pytest-qdrant-hardening-full`

## Final integration review requirement

- Output: `docs/agent-workflow/task-packets/2026-08-01-qdrant-follow-up-hardening/FINAL_INTEGRATION_REVIEW.md`
- Required after all packets are `done`; result must be `accepted`,
  `changes-required`, or `blocked`.

## Open decisions

- None.
