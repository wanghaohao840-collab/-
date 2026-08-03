---
id: "episodic-final-consistency-02"
title: "Normalize scoped legacy timestamps"
status: "done"
parallel-safe: false
depends-on: ["episodic-final-consistency-01"]
base-commit: "de321b2309b281623bc7f9c0d898810403319261"
owner: "Codex"
---

# Task Packet: Normalize scoped legacy timestamps

## Goal

Recognized legacy timestamps are normalized once per user before time-range
Qdrant queries and new writes are canonical ISO.

## Change boundary

- Modify EpisodicMemory, episodic protocol/live tests, and this handoff.
- Do not migrate other users, memory types, or unparseable timestamps.

## Acceptance criteria

- [x] Protocol test proves filtered scroll, vector-preserving upsert, and ISO payload.
- [x] Migration runs once per user per instance.
- [x] Real Qdrant retrieves a normalized legacy point through datetime bounds.
- [x] Affected and full regressions pass.

## Verification

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/test_episodic_vector_store_protocol.py -q --basetemp=.runtime/pytest-episodic-timestamps
powershell -ExecutionPolicy Bypass -File scripts/run_qdrant_integration.ps1
```

## Implementation handoff

- Status: done
- Delivered: new timestamps are canonical ISO; recognized legacy timestamps
  are normalized once per user through scoped, vector-preserving scroll/upsert.
- Verification: focused consistency suite PASS (`5 passed`); real Qdrant PASS
  (`6 passed`); memory/Qdrant regression PASS (`171 passed, 5 skipped`);
  complete repository suite PASS (`664 passed, 6 skipped`).
- Scope confirmation: other users/types and unparseable timestamps are untouched.
- Residual risks: unparseable legacy timestamps remain outside datetime filters.
- Commit: included in `26e9045`
