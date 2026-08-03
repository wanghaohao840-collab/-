---
id: "qdrant-follow-up-hardening-01"
title: "Retry transient live collection cleanup"
status: "done"
parallel-safe: false
depends-on: []
base-commit: "9de3c35a8ee933011b71973400519fbf5f0f6dfc"
owner: "Codex"
---

# Task Packet: Retry transient live collection cleanup

## Goal

Every live Qdrant test uses a bounded cleanup helper that tolerates temporary
Windows collection-directory locks and still surfaces persistent failures.

## Non-goals

- Product retry behavior, server configuration, or silent cleanup failures.

## Delivery context

A real Qdrant 1.18.2 run completed its business assertions but returned HTTP
500 while renaming a collection directory during `finally`; an unchanged rerun
passed. The fix belongs only to test teardown.

## Relevant files and current interfaces

- `tests/integration/test_qdrant_document_scope.py` owns all five direct live
  collection deletions and is already dirty with accepted memory tests.
- Preserve all existing tests and unrelated concurrent changes.

## Prerequisites

- Base commit above plus current accepted uncommitted Qdrant memory work.
- Local Qdrant 1.18.2 runner.

## Explicit change boundary

- Allowed: only `tests/integration/test_qdrant_document_scope.py` and this handoff.
- Forbidden: production code, runner behavior, skipped errors, infinite retry.

## Interface contract

- `_delete_collection_with_retry(client, collection_name, retry_delays)` checks
  existence, deletes, retries bounded exceptions, and re-raises the last one.

## Acceptance criteria

- [ ] Deterministic fake test proves transient retry and success.
- [ ] All live `finally` blocks use the helper.
- [ ] Five real Qdrant tests pass and owned process stops.

## Verification

```powershell
.\venv\Scripts\python.exe -m pytest tests/integration/test_qdrant_document_scope.py -q --basetemp=.runtime/pytest-qdrant-cleanup-helper
powershell -ExecutionPolicy Bypass -File scripts/run_qdrant_integration.ps1
```

## Stop conditions

Stop on overlapping edits or if reliable cleanup requires production/runner
changes beyond this boundary.

## Implementation handoff

- Packet: `qdrant-follow-up-hardening-01`
- Status: `done`
- Delivered: bounded collection cleanup retry for all live Qdrant tests
- Files changed: `tests/integration/test_qdrant_document_scope.py`
- Verification:
  - cleanup fake: PASS (1 passed)
  - real Qdrant 1.18.2: PASS (6 total: helper plus 5 live; process stopped)
- Scope confirmation: test-only; product and runner behavior untouched
- Deviations: none
- Residual risks/follow-ups: persistent cleanup errors still fail visibly
- Commit: not committed
