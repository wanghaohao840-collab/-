---
id: "qdrant-follow-up-hardening-02"
title: "Synchronize Episodic SQLite cleanup"
status: "done"
parallel-safe: false
depends-on: ["qdrant-follow-up-hardening-01"]
base-commit: "9de3c35a8ee933011b71973400519fbf5f0f6dfc"
owner: "Codex"
---

# Task Packet: Synchronize Episodic SQLite cleanup

## Goal

Episodic forget and clear remove the same owned IDs from the vector store and
SQLite document store before committing local map changes.

## Non-goals

- Distributed transactions, rollback, schema changes, or other memory types.

## Change boundary

- Allowed: `hello_agents/memory/types/episodic.py`,
  `tests/memory/test_episodic_vector_cleanup.py`, live episodic assertion, this
  handoff.
- Preserve concurrent `add()` idempotency and accepted Qdrant work.

## Acceptance criteria

- [ ] Recording store sees the low ID deleted by forget and remaining high ID
  deleted by clear.
- [ ] Live SQLite row is absent after episodic clear.
- [ ] Packet 01 and live tests remain green.

## Verification

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/test_episodic_vector_cleanup.py tests/memory/test_episodic_vector_store_protocol.py -q --basetemp=.runtime/pytest-episodic-sqlite
powershell -ExecutionPolicy Bypass -File scripts/run_qdrant_integration.ps1
```

## Implementation handoff

- Packet: `qdrant-follow-up-hardening-02`
- Status: `done`
- Delivered: forget/clear delete matching owned IDs from vector and SQLite stores before local map commit
- Files changed: `hello_agents/memory/types/episodic.py`, cleanup test, live episodic assertion
- Verification: focused PASS (2 passed); real Qdrant PASS (6 total: helper plus 5 live; process stopped)
- Scope confirmation: public signatures and concurrent `add()` idempotency preserved
- Deviations: none
- Residual risks/follow-ups: distributed atomicity remains out of scope
- Commit: not committed
