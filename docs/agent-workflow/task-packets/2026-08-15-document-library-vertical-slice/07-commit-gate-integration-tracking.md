---
id: "document-library-vertical-slice-07"
title: "Track atomic committing transitions in import acceptance"
status: "ready"
parallel-safe: true
depends-on: ["document-library-vertical-slice-03"]
base-commit: "14a990ed6e5260760411b2d7fad0c2ead7dda342"
owner: "unassigned"
---

# Corrective Task Packet: Track atomic committing transitions in import acceptance

## Goal

Restore the repository-wide import acceptance test so its stage trace observes the successful Task 03 atomic commit-gate transition and the complete stage order passes.

## Non-goals

- Do not change repository/worker/RAG production behavior or expected stage order.
- Do not weaken, delete or skip the five-attempt batch-retry assertion.

## Delivery context

Task 03 moved `committing` from ordinary `update_progress()` to `try_begin_committing()` so cancellation and commit arbitration are atomic. `TrackingImportTaskRepository` records only `update_progress()`, making its stage trace blind to the valid commit transition and failing the full suite.

## Relevant files and current interfaces

- `tests/integration/test_batch_import_acceptance.py:134` — `TrackingImportTaskRepository` records ordinary progress only.
- `tests/integration/test_batch_import_acceptance.py:383` — acceptance requires `staged → parsing → chunking → embedding → persisting → committing`.
- `app/import_repository.py:203` — authoritative `try_begin_committing(user_id, task_id, now=None) -> bool`; read-only in this packet.
- Existing changes to preserve: corrective review/packets and all completed feature commits.

## Prerequisites

### Packet dependencies

- `document-library-vertical-slice-03` is `done`.

### Repository/base state

- Base commit: `14a990ed6e5260760411b2d7fad0c2ead7dda342`.
- The focused test currently fails only because the tracking subclass misses the commit-gate method.

### External prerequisites

- Project venv; no service or network.

## Explicit change boundary

### Allowed files

- Modify: `tests/integration/test_batch_import_acceptance.py`
- Modify: this packet for handoff.

### Allowed behavior changes

- Test instrumentation may record `committing` only when `super().try_begin_committing(...)` succeeds.

### Forbidden changes

- No production, fixture lifecycle, retry timing, stage expectation or other test file changes.

## Interface contract

### Consumes

- `ImportTaskRepository.try_begin_committing(...) -> bool`.

### Produces

- `TrackingImportTaskRepository` records exactly one `(task_id, "committing", 0)` entry for a winning gate and none for a rejected/failed gate.

### Invariants

- The real atomic transaction remains authoritative; instrumentation occurs only after success.
- Existing ordinary progress entries and retry counts are unchanged.

## Required behavior

- Delegate all arguments to `super()`.
- Append only when the returned value is true.
- Preserve exceptions and false results without fabricating a stage.

## Implementation guidance

Override `try_begin_committing()` beside `update_progress()` in the tracking subclass. Call `super()` first, then append on true, and return the exact result.

## Acceptance criteria

- [ ] The five-attempt retry acceptance test passes with the exact six-stage suffix.
- [ ] Rejected/exceptional gates are not logged as committed.
- [ ] No production file or assertion is changed.
- [ ] Focused import integration regression passes.

## Test and verification commands

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/integration/test_batch_import_acceptance.py --basetemp=.runtime/pytest-commit-gate-integration
git diff --check
```

Expected: all selected tests pass and the diff is test-only.

## Stop conditions

Stop on any changed production signature, need to relax stage order, or required edit outside the owned test/packet.

## Implementation handoff

Replace with the workflow handoff template, including focused counts, scope and commit.
