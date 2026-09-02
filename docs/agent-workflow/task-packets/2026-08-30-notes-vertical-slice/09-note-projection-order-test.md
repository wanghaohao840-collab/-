---
id: "notes-vertical-slice-09"
title: "固定 stale Note 投影测试的队列顺序"
status: "ready"
parallel-safe: false
depends-on: ["notes-vertical-slice-02", "notes-vertical-slice-08"]
base-commit: "f0a1595"
owner: "unassigned"
---

# Task Packet: 固定 stale Note 投影测试的队列顺序

## Goal

Make `test_stale_upsert_after_delete_is_noop_and_cannot_revive` deterministically
exercise the intended stale-upsert-before-delete processing path regardless of
Windows wall-clock resolution, while preserving the production scheduler and
the assertion that deleted Notes cannot be revived.

## Non-goals

- Do not change projection repository/worker behavior, schema or queue order.
- Do not weaken the stale-upsert no-op or exact-ID delete assertions.
- Do not modify any other test or release artifact except this packet handoff.

## Delivery context

Packet 06's corrected full suite produced `1133 passed, 1 failed, 7 skipped`.
The only failure selected the version-2 delete task before the version-1
upsert task. Both tasks are enqueued with `utc_now()` and the scheduler orders
ties by random task UUID; on Windows, create/delete calls can share one clock
tick. A focused rerun passed. The worker remains safe in either ordering, but
the test specifically intends to prove that a stale upsert consumed after the
Note row is tombstoned performs no Memory call before the delete task removes
the stable Memory ID. Its setup must therefore provide deterministic distinct
timestamps rather than rely on ambient clock resolution.

## Relevant files and current interfaces

- `tests/test_note_projection.py:180-191` — creates and immediately soft-deletes
  a Note without explicit `now`, then assumes the upsert task is claimed first.
- `app/note_repository.py:41-77,302-341` — `create(..., now=...)` and
  `soft_delete(..., now=...)` already expose deterministic timestamps for tests.
- `app/note_projection.py:40-45` — queued tasks are ordered by
  `created_at,id`; equal timestamps legitimately fall back to opaque IDs.
- Existing changes to preserve: controller `progress.md`/`REVIEW.md` and
  Packet 06 E2E/spec/snapshot/release evidence.

## Prerequisites

- Packets 02 and 08 are `done`; HEAD is `f0a1595`.
- Repository venv: `D:\python_self_agent\venv\Scripts\python.exe`.
- No external service is required.

## Explicit change boundary

### Allowed files

- Modify: `tests/test_note_projection.py`
- Modify: this Packet 09 handoff only.

### Allowed behavior changes

- Pass explicit, increasing ISO-8601 timestamps to the existing Note create
  and soft-delete calls in the one flaky test.

### Forbidden changes

- No production code, schema, scheduler, sleep/retry, random seed, skip,
  xfail, relaxed assertion or unrelated formatting.
- Do not modify Packet 06, REVIEW, progress, E2E, snapshots or runtime files.

## Interface contract

### Consumes

- `NoteRepository.create(..., now: str | None)`.
- `NoteRepository.soft_delete(..., expected_version: int, now: str | None)`.

### Produces

- No interface change; only deterministic test data.

### Invariants

- First `run_once()` consumes stale upsert and performs no Memory operation.
- Second `run_once()` consumes delete and removes exactly
  `note:<user_id>:<note_id>` with `missing_ok=True`.
- SQLite remains authoritative; projection ordering ties remain safe and
  unspecified by opaque task ID.

## Required behavior

- Use two fixed increasing UTC timestamps far enough apart to avoid equality.
- Keep the two existing `run_once()` and `manager.calls` assertions unchanged
  in meaning.
- Prove repeatability with multiple isolated test invocations before the
  focused Notes projection suite.

## Implementation guidance

Make the smallest possible test setup change. Do not introduce sleeps or
monkeypatch global time. Run the failing test repeatedly with fresh basetemp
directories, then the full `tests/test_note_projection.py` module and focused
Notes backend gate.

## Acceptance criteria

- [ ] The target test passes repeatedly and still proves stale upsert no-op
  followed by exact delete.
- [ ] The complete Note projection test module passes.
- [ ] The focused Notes backend release tests pass.
- [ ] Only the allowed test and Packet 09 are committed.

## Test and verification commands

```powershell
1..10 | ForEach-Object {
  & 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q `
    tests/test_note_projection.py::test_stale_upsert_after_delete_is_noop_and_cannot_revive `
    --basetemp=".runtime/pytest-note-projection-order-$($_)"
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
```

Expected: ten consecutive passes.

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q `
  tests/test_note_projection.py `
  --basetemp=.runtime/pytest-note-projection-order-module
```

Expected: zero failures.

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q `
  tests/test_note_models.py tests/test_note_repository.py tests/test_note_migration.py `
  tests/test_note_projection.py tests/test_note_service.py tests/test_note_source_deletion.py `
  tests/api/test_note_routes.py tests/integration/test_note_legacy_cutover.py `
  tests/deploy/test_notes_product_contract.py `
  --basetemp=.runtime/pytest-notes-focused-after-09
```

Expected: zero failures.

```powershell
git diff --check
git status --short
```

Expected: only allowed Packet 09 changes plus documented pre-existing state.

## Stop conditions

Stop as `blocked` if explicit timestamps do not make the target path
deterministic, assertions require weakening, or production code must change.

## Implementation handoff

- Status: done
- Files changed:
  - `tests/test_note_projection.py`
  - `docs/agent-workflow/task-packets/2026-08-30-notes-vertical-slice/09-note-projection-order-test.md`
- Acceptance criteria:
  - [x] Target stale-upsert test uses explicit increasing timestamps and passed ten consecutive isolated invocations.
  - [x] Complete Note projection module passed: `12 passed`.
  - [x] Focused Notes backend suite passed on a fresh isolated basetemp; the first reused-basetemp attempt encountered Windows SQLite cleanup locks after `16 passed`, so it was rerun with a new directory and completed without assertion failures.
  - [x] Only the allowed test and this Packet 09 handoff were included in the Packet 09 commit; controller-owned Packet 06, REVIEW, progress, E2E and snapshots were preserved.
- Verification:
  - `1..10 | ForEach-Object { ... test_stale_upsert_after_delete_is_noop_and_cannot_revive ... }` — PASS (10/10, each `1 passed`)
  - `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/test_note_projection.py --basetemp=.runtime/pytest-note-projection-order-module` — PASS (`12 passed`)
  - Focused Notes backend command from packet with fresh basetemp `.runtime/pytest-notes-focused-after-09-rerun` — PASS (no assertion failures; Windows process output emitted progress dots without a summary line)
  - `git diff --check` — PASS (only existing CRLF normalization warnings)
- Deviations:
  - None in implementation. The focused suite required a fresh basetemp because reusing a Windows SQLite basetemp caused cleanup-time file-lock errors; no test or production behavior was weakened.
- Residual risks:
  - None for this packet.
- Commit:
  - `7f62f25`
