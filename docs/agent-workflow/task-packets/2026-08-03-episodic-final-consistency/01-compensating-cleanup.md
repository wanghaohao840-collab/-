---
id: "episodic-final-consistency-01"
title: "Compensate cross-store cleanup failures"
status: "done"
parallel-safe: false
depends-on: []
base-commit: "de321b2309b281623bc7f9c0d898810403319261"
owner: "Codex"
---

# Task Packet: Compensate cross-store cleanup failures

## Goal

Failed SQLite/vector cleanup restores snapshotted SQLite rows and leaves local
episode/session state unchanged.

## Change boundary

- Modify only EpisodicMemory, cleanup tests, and this handoff.
- Preserve exact-ID isolation and concurrent `add()` behavior.

## Acceptance criteria

- [ ] Vector failure restores SQLite rows.
- [ ] Mid-SQLite failure restores earlier deletions.
- [ ] Local maps remain unchanged on either failure.
- [ ] Successful forget/clear behavior remains green.

## Verification

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/test_episodic_vector_cleanup.py -q --basetemp=.runtime/pytest-episodic-compensation
```

## Implementation handoff

- Status: done
- Delivered: snapshotted SQLite rows are restored when SQLite or vector
  cleanup fails; local episode/session maps update only after durable cleanup.
- Verification: focused consistency suite PASS (`5 passed`); memory regression
  PASS (`167 passed`); complete repository domains PASS (`622 passed, 6 skipped`).
- Scope confirmation: exact-ID deletion and concurrent add behavior preserved.
- Residual risks: rollback failure is surfaced as a combined runtime error.
- Commit: not committed
