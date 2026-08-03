---
id: "qdrant-follow-up-hardening-03"
title: "Push Episodic range filters into Qdrant"
status: "done"
parallel-safe: false
depends-on: ["qdrant-follow-up-hardening-02"]
base-commit: "9de3c35a8ee933011b71973400519fbf5f0f6dfc"
owner: "Codex"
---

# Task Packet: Push Episodic range filters into Qdrant

## Goal

Typed numeric/datetime ranges work identically in memory and Qdrant, and
EpisodicMemory pushes importance and supplied timestamp bounds before top-k.

## Non-goals

- Arbitrary boolean filter DSL, payload migration, or other consumers.

## Change boundary

- Allowed: vector store, EpisodicMemory, vector-store tests, episodic protocol
  test, live Qdrant test, this handoff.
- Preserve all equality/list behavior and concurrent changes.

## Interface contract

- Immutable `VectorRange` with `lt/lte/gt/gte` optional bounds.
- Datetime bounds are Python `datetime`/`date`; numeric bounds are numbers.
- Supported schemas include keyword, integer, float, datetime.

## Acceptance criteria

- [ ] In-memory ranges and Qdrant model mapping match.
- [ ] Episodic query sends importance and optional timestamp ranges.
- [ ] Live Qdrant schema and range retrieval pass.
- [ ] Affected and full regressions pass.

## Verification

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/storage/test_vector_store_contract.py tests/memory/storage/test_qdrant_vector_store.py tests/memory/test_episodic_vector_store_protocol.py -q --basetemp=.runtime/pytest-vector-ranges
powershell -ExecutionPolicy Bypass -File scripts/run_qdrant_integration.ps1
.\venv\Scripts\python.exe -m pytest -q --basetemp=.runtime/pytest-qdrant-hardening-full
```

## Implementation handoff

- Packet: `qdrant-follow-up-hardening-03`
- Status: `done`
- Delivered: typed numeric/datetime VectorRange filters and Episodic Qdrant pushdown
- Files changed: vector_store.py, episodic.py, vector-store/episodic/live tests
- Verification: focused PASS (11 passed); live PASS (6 total: helper plus 5 live); affected PASS (162 passed, 5 skipped); full rerun PASS (610 passed, 6 skipped)
- Scope confirmation: equality filters, local post-filters, isolation, and concurrent import changes preserved
- Deviations: none
- Residual risks/follow-ups: existing non-ISO legacy timestamps may need migration
- Commit: not committed
