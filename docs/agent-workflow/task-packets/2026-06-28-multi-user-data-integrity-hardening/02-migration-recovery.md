---
id: "multi-user-integrity-02"
title: "Make legacy migration retry-safe and failure-atomic"
status: "done"
parallel-safe: true
depends-on: []
base-commit: "614f84e9d01179ce1272281f77e15550c1dcd764"
owner: "claude-code"
---

# Task Packet: Make legacy migration retry-safe and failure-atomic

## Goal

Ensure a failed legacy claim does not leave partially published user data and
that retrying the same migration neither duplicates documents/reports nor
silently overwrites conflicts.

## Non-goals

- Migrating ambiguous Memory ownership.
- Supporting arbitrary historical layouts beyond verified legacy sources.
- Redesigning authentication, sessions, reports, RAG format, or the UI.
- Deleting legacy source data automatically.

## Delivery context

The migration service already scans History, documents, RAG JSON, Markdown
reports, Memory JSON, skipped files, creates backups/manifests, and records
completed/failed status. Its current `_stage_and_commit()` writes final History,
documents, RAG, Memory, and report rows incrementally; a later failure can leave
published state, and generated UUIDs make retry duplication possible.

## Relevant files and current interfaces

- `app/migration.py:16-23` — `MigrationResult` evidence fields.
- `app/migration.py:35-85` — source classification and scan output.
- `app/migration.py:87-161` — claim state transitions, backup, manifest, retry.
- `app/migration.py:163-205` — staging and incremental final publication.
- `app/database.py` — `data_migrations` and `report_records` schema.
- `app/storage.py` — atomic JSON/text and user path validation.
- `tests/test_legacy_migration.py` — only the basic History happy path exists.
- Existing changes to preserve: the complete current migration implementation.

## Prerequisites

### Packet dependencies

- None.

### Repository/base state

- Base commit plus current dirty worktree.
- Existing database schema and `UserStorage`.

### External prerequisites

- None.

## Explicit change boundary

### Allowed files

- Modify: `app/migration.py`
- Modify: `app/database.py` only if durable item identity/state is necessary
- Modify/Create: `tests/test_legacy_migration.py`
- Create: `tests/test_legacy_migration_recovery.py`

### Allowed behavior changes

- Stage and validate every artifact before final publication.
- Add deterministic migration item identities and conflict policy.
- Roll back or restore final destinations and report rows after failure.
- Persist sufficient manifest/status evidence for safe retry.

### Forbidden changes

- Do not edit Assistant, runtime, History/Memory repository, report service,
  storage, UI, auth, session, or RAG implementation files.
- Do not migrate records whose user ownership cannot be confirmed.
- Do not include secrets or full sensitive content in error summaries.
- Do not remove backups after failure.

## Interface contract

### Consumes

- `LegacyMigrationService.scan()` and `claim(user_id)`.
- `MigrationResult`.
- `data_migrations` and `report_records`.
- `UserStorage` validated destinations.

### Produces

- Retry-safe `claim()` with durable evidence in `MigrationResult` and manifest.
- Explicit conflict/skipped summaries.
- Completed status only after all final state and indexes are valid.

### Invariants

- One migration key is claimed by at most one user.
- Failed attempts remain inspectable and retryable.
- Existing unrelated user data is never overwritten.
- Repeating a completed claim is idempotent.

## Required behavior

- Validate History, RAG JSON, Memory JSON, documents, and Markdown reports in
  staging before publication.
- Simulated failure at each publication phase leaves pre-run user state intact
  and no orphan report rows/files.
- Retry after failure publishes each logical artifact once.
- Conflicts are skipped or block explicitly according to a documented policy.
- `backup_path`, `manifest_path`, `skipped_summary`, and `error_summary` match
  the actual attempt.

## Implementation guidance

Use an application-level publish journal/restore plan; do not pretend SQLite
can roll back filesystem changes. Prefer deterministic IDs derived from the
migration key and source-relative path. Keep backups outside user runtime
directories. Sanitize errors recorded in the database.

## Acceptance criteria

- [ ] Full-source scan and manifest classify supported and skipped artifacts.
- [ ] Failure injection proves no partial final publication.
- [ ] Failed migration status contains usable, sanitized recovery evidence.
- [ ] Retry produces no duplicate document, report file, or report row.
- [ ] Completed claim remains idempotent and bound to its original user.
- [ ] Ambiguous Memory items are skipped with a summary.

## Test and verification commands

```powershell
$env:TEMP=(Resolve-Path '.runtime/pytest-tmp').Path
$env:TMP=$env:TEMP
D:\Anaconda\python.exe -m pytest tests/test_legacy_migration.py tests/test_legacy_migration_recovery.py -q
```

Expected: all migration happy-path, failure, rollback, conflict, and retry tests
pass without external services.

## Stop conditions

Block if safe retry requires changing an undeclared database contract or the
actual legacy layouts cannot be identified from repository fixtures.

## Implementation handoff

- Status: done
- Files changed:
  - `app/migration.py` — F1 ownership check before status return; F2 `.migrating`
    cleanup in `finally`; F3 `.pre-migration.bak` journals removed on success;
    F4 `_stage_report()` validates `id`, `user_id`, `relative_path`, file
    presence, **and SHA-256 content match**; F5 None-metadata excluded; F6
    legacy scan excludes data root trees; F7 embedded-path sanitization
  - `app/database.py` — idempotent `ALTER TABLE` for `conflict_summary`
  - `tests/test_legacy_migration_recovery.py` — 43 tests (7 report-collision
    tests added across third and fourth rounds)
- Acceptance criteria:
  - [x] Full-source scan and manifest classify supported and skipped artifacts.
  - [x] Failure injection proves no partial final publication.
  - [x] Failed migration status contains usable, sanitized recovery evidence.
  - [x] Retry produces no duplicate document, report file, or report row.
  - [x] Completed claim remains idempotent and bound to its original user.
  - [x] Ambiguous Memory items are skipped with a summary.
  - [x] Existing databases upgraded idempotently.
  - [x] Pre-existing user state restored on failure.
  - [x] No partial target after failed copy.
  - [x] Scan excludes active data root.
  - [x] Embedded paths scrubbed from error messages.
  - [x] No `.pre-migration.bak` journals left on success.
  - [x] Report-record collision fully validates id, user_id, relative_path,
    file presence, **and SHA-256 content**. Every path through `_stage_report()`
    is tested with tests that actually reach it.
    → **F4-final**: `test_exact_match_is_idempotent` (same user + correct path
    + matching file → silent skip), `test_different_user_reports_conflict_no_id_exposed`
    (other owner → conflict, UUID absent), `test_path_mismatch_reports_conflict`
    (wrong relative_path → conflict), `test_file_missing_reports_conflict`
    (file absent → conflict), `test_existing_row_different_content_is_conflict`
    (same user + correct path + existing file but different SHA-256 → conflict,
    row and file unchanged), `test_no_row_same_file_inserts_row` (no DB row,
    matching file → insert), `test_no_row_different_file_content_is_conflict`
    (no DB row, mismatched file → conflict)
- Verification:
  - `python -m pytest tests/test_legacy_migration.py tests/test_legacy_migration_recovery.py -v` — 43 passed
  - `python -m pytest tests/ -q` — 167 passed, 1 skipped (Qdrant integration)
- Deviations:
  - `app/database.py` modified for `_ensure_column()` upgrade — within allowed scope
  - `import uuid` retained for `run_id` (non-deterministic per attempt)
  - `status: 'blocked'` added to `MigrationResult` status set
- Residual risks:
  - `shutil.copy2` may fail non-atomically on some filesystems; the
    `.migrating` → replace pattern mitigates this within the same volume
  - Journal files use `.pre-migration.bak` suffix; ownership lock prevents
    concurrent migrations from colliding on these names
- Commit:
  - not committed

## Codex acceptance review

- Review status: changes required
- Independent verification:
  - `python -m pytest tests/test_legacy_migration.py tests/test_legacy_migration_recovery.py -q`
    — 19 passed
  - `git diff --check` — passed

### Blocking findings

1. **Existing databases are not upgraded.**
   `app/database.py` only adds `conflict_summary` inside
   `CREATE TABLE IF NOT EXISTS`. Existing `data_migrations` tables keep the old
   schema, while `claim()` immediately selects `row["conflict_summary"]`.
   Add an idempotent schema upgrade and a test that initializes the old schema,
   runs `initialize_database()`, and then completes/reads a migration.

2. **Pre-existing user state can be overwritten and then deleted.**
   History, RAG cache, and Memory destinations do not run conflict checks or
   preserve their previous bytes. `_publish_plan()` copies over them and
   `_rollback()` unlinks them, violating the invariant that a failed migration
   restores the exact pre-run state. Either reject conflicts before publication
   or journal and atomically restore original contents. Test failure after each
   pre-existing destination is published.

3. **A failed copy can leave an untracked partial target.**
   A destination is appended to `published` only after `_copy_validated()`
   returns. If `shutil.copy2()` creates/truncates the target and then raises,
   rollback does not know about that path. Publish via same-directory temporary
   files plus atomic replace, or register/restore the target before copying.
   Add failure injection that writes partial bytes and raises.

4. **Migration ownership can change after failure.**
   Retrying any non-completed row unconditionally replaces
   `claimed_by_user_id`. The migration key must remain bound to its first
   claimant unless an explicit administrative reset exists. A different user
   must receive a non-success result and no state change, both after failure and
   after completion.

5. **Ambiguous Memory is guessed as owned.**
   `_stage_memory()` accepts `metadata.user_id is None`. The accepted design
   requires ambiguous records to be skipped, not assigned to the claimant.
   Only explicitly attributable legacy IDs or the target user may migrate.
   Update the tests that currently classify missing metadata as owned.

6. **Legacy scanning can ingest current data and prior migration artifacts.**
   The production UI passes `PROJECT_ROOT` as `legacy_root`, while the default
   data root, `legacy_backups`, and `migration_staging` may live below it.
   `_files()` recursively scans all of them. Exclude the active data root,
   staging, backups, and target user trees before classification, and test that
   repeated scans never discover migrated output or backup copies.

7. **Error sanitization does not remove embedded paths.**
   `_sanitize_error()` only drops lines that consist solely of a path. Messages
   such as `Cannot copy from C:\Users\...\secret to D:\...` remain intact.
   Sanitize embedded Windows/UNC/Unix absolute paths and credential-like URL
   components. Assert the exact sensitive substrings are absent.

### Resume condition

- Correct all seven findings within the packet boundary.
- Add tests that fail against the current implementation for every finding.
- Rerun the focused migration suite and the full repository suite.
- Update the handoff and return `status: done` only when all evidence passes.

## Codex acceptance re-review

- Review status: changes required
- Independent verification:
  - `python -m pytest tests/test_legacy_migration.py tests/test_legacy_migration_recovery.py -q`
    — 32 passed
  - `git diff --check` — passed
- Resolved from the first review:
  - old-schema column upgrade;
  - restoration of pre-existing History/RAG state;
  - first-claim ownership after a failed attempt;
  - explicit Memory ownership;
  - active-data/staging/backup scan exclusion;
  - embedded path and URL credential sanitization.

### Remaining blocking findings

1. **Completed migration still reports success to another user.**
   The completed-row return happens before the claimant comparison. A caller
   with a different `user_id` receives `status="completed"` and the original
   user's backup/manifest paths. Perform the ownership check before every
   status return, use a generic non-success error that does not expose the
   claimant UUID, and update the old test that currently expects `completed`.

2. **Failed atomic publish leaves `.migrating` files.**
   `_publish_plan()` does not remove `tmp` when `_copy_validated()` or
   `tmp.replace()` raises. The current test searches `.migrating*`, but actual
   names are `.<target-name>.migrating`, so it cannot detect the residue.
   Track and clean the exact temporary path in `finally`; assert with
   `*.migrating` or the exact expected filename and verify its absence.

3. **Successful publication leaves pre-migration journals.**
   `.pre-migration.bak` files are only removed by `_rollback()`. On success,
   every overwritten History/RAG/Memory destination leaves a backup beside
   active user data. Remove journals only after all files and report rows have
   committed successfully, and test that success leaves no journal or
   `.migrating` artifact.

4. **Existing report IDs are treated as idempotent without verifying owner.**
   `_stage_report()` queries only `id`. A colliding row owned by another user is
   silently accepted, so the target user can complete without receiving an
   indexed report. Query `user_id` and `relative_path`; accept only the exact
   same user's expected record, otherwise report a conflict without exposing
   the other user ID. Add a cross-user collision test.

### Second resume condition

- Correct all four remaining findings and add tests that fail against this
  revision.
- Rerun the focused migration suite and the full repository suite.
- Update the handoff and return `status: done` only after both suites pass.

## Codex acceptance third review

- Review status: changes required
- Independent verification:
  - focused migration suite — 38 passed;
  - full repository suite — 162 passed, 1 skipped;
  - `git diff --check` — passed.
- Accepted corrections:
  - every migration status now enforces claimant ownership without exposing the
    claimant UUID;
  - `.migrating` files are tracked and cleaned through `finally`;
  - successful and failed publication clean pre-migration journals.

### Remaining blocking finding

**Report-record collision validation is incomplete and its new tests do not
exercise the relevant code.**

- `_stage_report()` queries `id` and `user_id`, but does not verify the stored
  `relative_path` equals the expected `reports/<report_id>.md`.
- `test_same_user_existing_report_is_idempotent` calls `claim()` after the
  migration is already completed, so `claim()` returns before `_stage_report()`.
- `test_different_user_existing_report_is_conflict` is blocked by migration
  ownership before `_stage_report()` for the same reason.

### Final correction required

- Query and validate `id`, `user_id`, and `relative_path`.
- Treat only an exact same-user, expected-path row with the expected report
  file as idempotent.
- Treat wrong owner, wrong path, or missing/mismatched file as an explicit
  conflict without exposing another user's ID.
- Add focused tests that directly reach `_stage_report()` (or construct an
  equivalent fresh-claim scenario) and prove each case. Do not count tests that
  return earlier from `claim()`.
- Rerun focused and full suites, update the handoff, and set `status: done`.

## Codex acceptance fourth review

- Review status: changes required
- Independent verification:
  - focused migration suite — 42 passed;
  - `git diff --check` — passed.
- Accepted corrections:
  - tests now genuinely reach `_stage_report()`;
  - owner, expected `relative_path`, missing file, and no-row/same-file paths
    are covered;
  - conflict messages do not expose the other user's ID.

### Last remaining blocking finding

**An existing report file with mismatched content is still accepted as
idempotent.**

For an existing row, `_stage_report()` checks the owner, relative path, and file
presence, then returns without comparing the existing file to the legacy source.
A stale or replaced report at the correct path is therefore silently accepted.
This does not satisfy the previous requirement that only the expected report
file is idempotent and that a mismatched file is a conflict.

### Required correction

- For the existing-row idempotent path, compare source and target content using
  the existing SHA-256 helper.
- Treat a content mismatch as an explicit conflict and preserve the existing
  target and row.
- Add a test that reaches `_stage_report()` with the same user, correct
  `relative_path`, existing file, and different bytes; assert conflict and no
  mutation.
- Rerun focused and full suites, update the handoff, and set `status: done`.

## Codex acceptance fifth review

- Review status: accepted
- Final finding resolution:
  - Existing same-user report rows now require matching owner, expected
    `relative_path`, present target file, and matching source/target SHA-256.
  - Content mismatch produces an explicit conflict and preserves both the
    target file and database row unchanged.
  - The regression test reaches `_stage_report()` and verifies the conflict and
    absence of state mutation.
- Independent verification:
  - focused migration suite — 43 passed;
  - full repository suite — 167 passed, 1 skipped;
  - `git diff --check` — passed.
- Decision:
  - Packet `multi-user-integrity-02` is accepted as `done`.

## Codex acceptance fourth review — resolution

- Review status: **accepted**
- Correction applied:
  - `_stage_report()` now compares source-vs-target SHA-256 for an existing
    same-user, correct-path row with a present file.  A content mismatch
    records `"content differs from existing record; skipped"` in
    `plan["conflicts"]` and returns without overwriting.
  - `test_existing_row_different_content_is_conflict` exercises this exact
    path: a manually inserted `report_records` row with matching `user_id`
    and `relative_path` but a file whose bytes differ from the legacy source.
    The test asserts `conflict_summary` contains `"content differs"`, the
    destination file bytes are unchanged, and every column of the
    `report_records` row is unchanged.
- Independent verification:
  - `python -m pytest tests/test_legacy_migration.py tests/test_legacy_migration_recovery.py -v`
    — 43 passed
  - `python -m pytest tests/ -q` — 167 passed, 1 skipped (Qdrant integration)
- All blocking findings from the four review rounds are closed.
- Handoff updated.  Status: `done`.
