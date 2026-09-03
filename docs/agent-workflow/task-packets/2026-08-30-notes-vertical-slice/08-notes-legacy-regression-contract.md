---
id: "notes-vertical-slice-08"
title: "迁移旧 Notes 回归测试到权威事实源"
status: "done"
parallel-safe: false
depends-on: ["notes-vertical-slice-02", "notes-vertical-slice-03", "notes-vertical-slice-05", "notes-vertical-slice-07"]
base-commit: "31fd82ae5b322f658e34ba4dbf8d263754680e15"
correction-base-commit: "695f22277c0e30e7bc174dab43df92f9fc5f5b2b"
owner: "Codex /root/notes_packet_06_gates_finalize"
---

# Task Packet: 迁移旧 Notes 回归测试到权威事实源

## Goal

Make the eight full-suite legacy regression scenarios verify their original
concurrency, restart, clear-scope, backup-isolation and rejected-auth
invariants through the supported `ApplicationServices` + `NoteService` path,
so the complete Python suite is green without restoring writes to
`history.json.notes` or direct random-ID Memory writes.

## Non-goals

- Do not change product code, public APIs, SQLite schemas, migrations or
  persisted Note formats.
- Do not delete or weaken the original concurrency, restart, user-isolation,
  clear-scope, backup-ownership or authorization invariants.
- Do not broaden this packet into release acceptance, visual work, E2E or
  distributed coordination.

## Delivery context

Packet 05 deliberately cut every supported legacy Note caller over to the
SQLite/FTS5-backed `NoteService`. The accepted plan requires legacy JSON Notes
to migrate idempotently and never receive new writes; Memory is a rebuildable
projection. Packet 06's mandatory full regression found eight older tests that
still instantiate a bare `SessionRegistry`/minimal runtime or inspect
`history.json.notes` after calling the now-supported Note path. Their product
invariants remain required, but their observation seam contradicts the
accepted architecture and causes `1126 passed, 8 failed, 7 skipped`.

## Relevant files and current interfaces

- `app/bootstrap.py:75-193` — `ApplicationServices.create(data_root)` builds
  the supported registry, Note repository/migration/projection/service and
  injects the shared service into current/future user runtimes.
- `app/note_service.py:177-272` — trusted internal `*_for_user` operations
  provide user-scoped create/list/count/clear behavior backed by SQLite.
- `assistants/pdf_learning_assistant.py:528-558` — `add_note()` and
  `clear_all_notes()` fail closed without a service and never fall back to
  JSON/Memory writes.
- `tests/integration/test_multi_user_acceptance.py:36-46,194-215,277-322,376-400,439-476`
  — bare registries and JSON Note assertions are the source of five failures.
- `tests/test_user_mutation_coordination.py:54-91,193-206` — the coordinator
  unit fixture intentionally has no Note service; only the assistant-level
  concurrency test must use the supported services and authoritative store.
- `tests/ui/test_authenticated_handlers.py:26-34,210-258` — the helper builds
  a bare registry and the two rejected-token tests seed/observe only history
  bytes, which no longer represent Note state.
- Existing changes to preserve: controller-owned
  `.superpowers/sdd/progress.md`, `REVIEW.md`, Packet 06 acceptance evidence,
  `web/e2e/notes.spec.ts` and the four reviewed Notes snapshots.

## Prerequisites

### Packet dependencies

- Packets 02, 03, 05 and 07 must remain `done`.

### Repository/base state

- Base commit: `31fd82ae5b322f658e34ba4dbf8d263754680e15`.
- Packet 05's no-dual-write behavior and Packet 06's exact eight-test failure
  evidence must remain unchanged before this packet starts.

### External prerequisites

- Repository venv at `D:\python_self_agent\venv\Scripts\python.exe`.
- No Penpot, browser, network or Docker service is required.

## Explicit change boundary

### Allowed files

- Modify: `tests/integration/test_multi_user_acceptance.py`
- Modify: `tests/test_user_mutation_coordination.py`
- Modify: `tests/ui/test_authenticated_handlers.py`
- Modify: this packet, for status and implementation handoff only.

### Allowed behavior changes

- Test fixtures may construct `ApplicationServices` instead of a bare
  `SessionRegistry` when testing supported Note behavior.
- Note assertions may query `NoteService`/`NoteRepository` by trusted user ID.
- History backup tests may seed a real non-Note history artifact before
  quarantine; rejected-auth tests must compare both Note fact rows and any
  pre-existing history bytes/state.

### Forbidden changes

- Do not modify `app/`, `api/`, `assistants/`, `ui/`, `web/`, schemas,
  migrations, dependency manifests or release snapshots.
- Do not append Notes to `history.json`, add a JSON fallback, dual-write,
  bypass `NoteService`, or weaken fail-closed behavior.
- Do not replace user-scoped assertions with global counts or remove negative
  ownership checks.
- Do not stage runtime data, databases, uploads, reports, traces, secrets or
  ignored test output.

## Interface contract

### Consumes

- `ApplicationServices.create(Path)` and its `session_registry`,
  `note_service`, and `note_repository` fields.
- `NoteService.list_for_user(user_id, NoteFilters())`,
  `count_for_user(user_id)` and the assistant's supported `add_note()` path.
- Existing history/recovery operations for documents, questions and backup
  ownership; Notes are not used to manufacture a history backup.

### Produces

- No production interface changes.
- The exact eight regression tests observe the current authoritative Note
  store while retaining their original product invariants.

### Invariants

- SQLite/FTS5 remains the only Note fact source.
- Legacy JSON Notes are migration input only and receive no new writes.
- Same-user concurrent writes are lossless; restart recovery and cross-user
  isolation remain proven.
- Clearing documents preserves Notes; rejected tokens mutate neither Notes nor
  legacy history; backup restore remains owner-scoped.

## Required behavior

- Concurrent Notes created through supported sessions are all present in the
  current user's authoritative Note rows.
- A fresh supported application service over the same data root restores Note
  rows and cannot expose Alice's rows to Bob.
- `clear_all_documents()` removes document/question history while leaving the
  user's active SQLite Notes unchanged.
- The history-backup ownership test creates an actual history backup without
  relying on a new JSON Note write, then proves another user cannot restore it.
- The assistant-level same-user concurrency test uses a real injected
  `NoteService`; coordinator-only unit tests keep their narrow JSON fixture.
- Forged and expired tokens leave both the seeded Note rows and legacy history
  state unchanged.

## Implementation guidance

1. Run the exact eight tests first and retain their RED evidence.
2. Prefer a small supported-services helper over hand-assembling Note
   repositories in multiple tests. Keep service/data roots isolated per test.
3. For restart tests, construct a second `ApplicationServices` instance on the
   same data root and query Notes through the new instance. Stop created
   services in `finally` blocks where workers/resources may be owned.
4. Preserve the history document/question assertions separately from Note
   assertions; do not rename SQLite Notes as history Notes.
5. For rejected-token tests, capture user-scoped Note IDs/count/content before
   the call and compare after the rejection. Compare history bytes only when a
   history file exists; absence-before/absence-after is also valid evidence.
6. Run the focused files, the Packet 05 no-dual-write regression, then the full
   suite. Stop and report if any production edit appears necessary.

## Acceptance criteria

- [ ] All eight formerly failing tests pass through supported services and
  authoritative Note queries.
- [ ] Tests still prove concurrency, restart, clear, backup and rejected-auth
  isolation rather than merely avoiding exceptions.
- [ ] Packet 05 no-dual-write tests remain green and no production file is
  changed.
- [ ] The complete Python suite passes with zero failures.

## Test and verification commands

Run from repository root:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q `
  tests/integration/test_multi_user_acceptance.py::TestSameUserConcurrency::test_concurrent_notes_all_persisted `
  tests/integration/test_multi_user_acceptance.py::TestRestartRestoration::test_full_restart_restores_all_artifacts `
  tests/integration/test_multi_user_acceptance.py::TestRestartRestoration::test_restart_preserves_user_scoped_isolation `
  tests/integration/test_multi_user_acceptance.py::TestDeleteClearScope::test_clear_all_documents_keeps_notes `
  tests/integration/test_multi_user_acceptance.py::TestBackupCrossUserDenial::test_cross_user_restore_history_backup_denied `
  tests/test_user_mutation_coordination.py::TestAssistantCoordination::test_concurrent_notes_merge_without_loss `
  tests/ui/test_authenticated_handlers.py::TestRejectedTokenNoStateChange::test_forged_token_does_not_modify_history `
  tests/ui/test_authenticated_handlers.py::TestRejectedTokenNoStateChange::test_expired_token_does_not_modify_history `
  --basetemp=.runtime/pytest-notes-legacy-contract-eight
```

Expected: `8 passed`.

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q `
  tests/integration/test_multi_user_acceptance.py `
  tests/test_user_mutation_coordination.py `
  tests/ui/test_authenticated_handlers.py `
  tests/integration/test_note_legacy_cutover.py `
  tests/assistants/test_pdf_learning_assistant_notes.py `
  --basetemp=.runtime/pytest-notes-legacy-contract-focused
```

Expected: all selected tests pass.

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q --basetemp=.runtime/pytest-notes-full-after-08
```

Expected: zero failures.

```powershell
git diff --check
git status --short
```

Expected: diff check is silent and only allowed test/packet files plus the
documented pre-existing controller/Packet 06 files are changed.

## Stop conditions

Stop and report `blocked` if any verified repository fact above is false, a
production edit is required, a test invariant cannot be preserved through the
supported service, or full-suite failures reveal a different implementation
defect. Append the reality-conflict report from the workflow README; do not
restore legacy persistence to make a test green.

## Implementation handoff

- Packet: `notes-vertical-slice-08`
- Status: `done`
- Delivered:
  - Migrated the eight legacy Note regression scenarios to supported
    `ApplicationServices` sessions and authoritative SQLite/FTS Note queries.
  - Preserved document/question history checks, restart restoration,
    same-user concurrency, clear scope, backup ownership and rejected-token
    no-mutation invariants without restoring legacy JSON/Memory Note writes.
- Files changed:
  - `tests/integration/test_multi_user_acceptance.py` — supported service
    fixtures and NoteService assertions for five legacy acceptance cases.
  - `tests/test_user_mutation_coordination.py` — supported same-user Note
    concurrency fixture while leaving coordinator-only fixtures unchanged.
  - `tests/ui/test_authenticated_handlers.py` — supported service fixture and
    Note/history state snapshots for forged/expired token rejection.
  - `docs/agent-workflow/task-packets/2026-08-30-notes-vertical-slice/08-notes-legacy-regression-contract.md` — status and handoff.
- Interfaces added or changed:
  - `none` (tests now consume existing `ApplicationServices.create()`,
    `session_registry`, `note_service.list_for_user()` and `NoteFilters`).
- Acceptance evidence:
  - [x] All eight formerly failing tests use supported services and
    authoritative Note queries — exact focused GREEN: `8 passed in 72.64s`.
  - [x] Concurrency, restart, clear, backup and rejected-auth invariants stay
    asserted — focused suite passed with no skipped or weakened assertions.
  - [x] Packet 05 no-dual-write coverage remains green — focused combined
    command: `163 passed in 453.71s`.
  - [x] Complete Python suite is green — `1134 passed, 7 skipped in 1044.14s`
    (`17:24.14`).
- Verification:
  - Exact eight-test RED before edits — `8 failed in 60.61s` (the recorded
    legacy JSON/unsupported-fixture failures).
  - Exact eight-test GREEN — `8 passed in 72.64s`.
  - Focused Packet08 + Packet05 suite — `163 passed in 453.71s`.
  - Full suite — `1134 passed, 7 skipped in 1044.14s`.
  - `git diff --check` — PASS.
- Scope confirmation:
  - changed only allowed files: yes (three allowed test modules and this
    packet; pre-existing worktree changes were preserved).
  - forbidden areas untouched: yes (no `app/`, `api/`, `assistants/`, `ui/`,
    `web/`, schema, migration, dependency, Packet06, `REVIEW.md` or
    `progress.md` edits).
- Deviations:
  - none.
- Residual risks/follow-ups:
  - The seven skipped tests are pre-existing conditional skips from the full
    suite; no new skip was added by Packet08.
- Commit:
  - `695f222` — `test: migrate notes legacy regression contract` (initial
    functional implementation; superseded by the lifecycle/ownership review
    correction below).

## Review correction handoff

- Status: done
- Review base:
  - `695f22277c0e30e7bc174dab43df92f9fc5f5b2b`
- Files changed:
  - `tests/integration/test_multi_user_acceptance.py`
  - `tests/test_user_mutation_coordination.py`
  - `tests/ui/test_authenticated_handlers.py`
- Corrections:
  - Restart isolation now asserts Alice's restored Note before asserting Bob
    has no Notes, with both service instances and tokens closed in
    `try/finally`.
  - Backup ownership now proves quarantine success, opaque backup ID validity,
    Alice's successful restoration of that exact backup, and Bob's rejection.
  - Every new `ApplicationServices` test tracks tokens, logs them out, and
    stops services in `finally` blocks.
- Verification:
  - Exact eight-test regression command — PASS: `8 passed in 76.39s`.
  - Packet08 + Packet05 focused command — PASS: `163 passed in 487.54s`.
  - Python compilation of the three changed test modules — PASS.
  - `git diff --check` before commit — PASS.
  - No production files changed.
- Functional head:
  - `2924233` — `test: harden notes lifecycle and backup assertions`.
- Scope confirmation:
  - Controller-owned files were not included in the functional commit.
- Documentation commit:
  - This handoff is committed separately as docs-only and intentionally does
    not embed its own commit hash.
