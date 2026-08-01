---
id: "multi-user-integrity-05"
title: "Persist and restore all supported Memory container shapes"
status: "done"
parallel-safe: false
depends-on:
  - "multi-user-integrity-04"
base-commit: "614f84e9d01179ce1272281f77e15550c1dcd764"
owner: "codex"
---

# Corrective Task Packet: Memory snapshot container integrity

## Goal

Make `MemorySnapshotRepository` persist and restore the actual container shapes
used by working, episodic, and semantic memory so all three supported types
survive a runtime restart without changing ownership or fail-closed rules.

## Non-goals

- No changes to RAG, graph, authentication, UI, migration, or report behavior.
- No new snapshot schema and no migration of ambiguous records.
- No external Qdrant or Neo4j dependency in tests.

## Repository facts

- `WorkingMemory.memories` is `list[MemoryItem]`.
- `SemanticMemory.memories` is `dict[str, MemoryItem]`.
- `EpisodicMemory` stores `Episode` objects in `_episodes` and has no
  `memories` attribute.
- `MemorySnapshotRepository.save_from_manager()` currently iterates every
  `memories` container as a list, so semantic iteration yields string keys and
  episodic entries are omitted.
- `restore_to_manager()` currently assigns `list` to every `memories`
  attribute, changing semantic memory's required container type.

## Change boundary

### Allowed files

- `app/memory_repository.py`
- `tests/test_memory_repository.py`
- `tests/integration/test_multi_user_acceptance.py`
- this packet and the final integration review

### Forbidden changes

- Do not edit Memory implementations merely to accommodate the repository.
- Do not weaken strict `user_id` filtering or corruption validation.
- Do not touch unrelated Neo4j/Qdrant worktree changes.

## Required behavior

- Save working list values, semantic dict values, and episodic `_episodes`.
- Preserve the existing JSON item shape and explicit `user_id` filter.
- Restore working as a list, semantic as an ID-keyed dict, and episodic as
  `_episodes` plus its `sessions` index.
- If restore fails, clear supported in-memory containers using their native
  empty shapes and raise; never leave semantic memory as a list.

## Acceptance criteria

- [x] A mixed working/episodic/semantic manager round-trips all three types.
- [x] Semantic memory remains a dict after successful and failed restore.
- [x] Episodic sessions are rebuilt from the restored `_episodes` index.
- [x] Cross-user items remain excluded.
- [x] The restart acceptance test asserts the explicit working item, not only
  a History-backed note.

## Verification

```powershell
D:\Anaconda\python.exe -m pytest tests/test_memory_repository.py tests/integration/test_multi_user_acceptance.py -q
D:\Anaconda\python.exe -m pytest tests/test_user_mutation_coordination.py tests/test_p0_data_integrity.py tests/test_legacy_migration_recovery.py tests/test_corruption_recovery.py tests/ui/test_authenticated_handlers.py -q
python -m compileall -q app assistants hello_agents ui tests
```

## Implementation handoff

- Status: done
- Files changed:
  - `app/memory_repository.py`
  - `tests/test_memory_repository.py`
  - `tests/integration/test_multi_user_acceptance.py`
- Acceptance criteria:
  - [x] Native list/dict/episode containers persist and restore.
  - [x] Ownership filtering and fail-closed recovery remain intact.
  - [x] Restart acceptance retrieves the explicit working-memory item.
- Verification:
  - focused repository/recovery tests — 29 passed
  - restart acceptance test — 1 passed
  - combined multi-user suite — 269 passed
  - full repository suite — 438 passed, 4 skipped
  - `python -m compileall -q app assistants hello_agents ui tests` — PASS
- Deviations: none
- Residual risks: process-local locking remains an intentional first-version limit
- Commit: not committed
