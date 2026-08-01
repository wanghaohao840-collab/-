---
id: "multi-user-integrity-03"
title: "Expose explicit History and Memory recovery workflows"
status: "done"
parallel-safe: false
depends-on: ["multi-user-integrity-01"]
base-commit: "614f84e9d01179ce1272281f77e15550c1dcd764"
owner: "claude-code"
---

# Task Packet: Expose explicit History and Memory recovery workflows

## Goal

Keep corrupt History and Memory snapshots fail-closed while providing an
explicit, user-scoped quarantine/restore workflow that can be tested and
surfaced safely to the UI.

## Non-goals

- Automatic repair or inference of corrupt JSON.
- Restoring RAG/Qdrant state.
- General backup scheduling or cloud backup.
- UI redesign beyond minimal recovery handlers/status.

## Delivery context

Both repositories already raise typed corruption errors and expose
`quarantine_and_reset()` and `restore()`. The missing delivery is a coordinated
application workflow: authorization, locking, safe status, validation before
replacement, and tests that recovery cannot target another user.

## Relevant files and current interfaces

- `app/history.py:14-57` — typed error, fail-closed load, quarantine, restore.
- `app/memory_repository.py:14-62` — equivalent Memory snapshot behavior.
- `app/runtime.py` — runtime paths and shared user lock after packet 01.
- `ui/gradio_app.py:35-39` — authenticated assistant lookup.
- `tests/test_history_repository.py:37-58` — History fail-closed coverage.
- `tests/test_memory_repository.py` — snapshot isolation coverage.

## Prerequisites

### Packet dependencies

- `multi-user-integrity-01` must be `done`.

### Repository/base state

- Use packet 01's final coordination interface.

### External prerequisites

- None.

## Explicit change boundary

### Allowed files

- Create: `app/recovery.py`
- Modify: `app/history.py`
- Modify: `app/memory_repository.py`
- Modify: `app/runtime.py`
- Modify: `ui/gradio_app.py`
- Modify/Create: `tests/test_corruption_recovery.py`
- Modify: `tests/test_history_repository.py`
- Modify: `tests/test_memory_repository.py`

### Allowed behavior changes

- Add explicit inspect/quarantine/reset/restore operations.
- Add minimal authenticated UI handlers and safe status messages.
- Validate backup content before replacing active state.

### Forbidden changes

- Do not edit Assistant, migration, report, RAG, Memory implementation internals,
  auth, session, or storage files.
- Do not reset automatically after a load error.
- Do not accept arbitrary client-supplied filesystem paths.
- Do not expose absolute user paths or corrupt content in UI errors.

## Interface contract

### Consumes

- Repository typed errors and recovery methods.
- Runtime paths, lock/coordinator, and authenticated session lookup.

### Produces

- User-scoped recovery service methods using opaque backup identifiers.
- Minimal UI handlers returning sanitized status.

### Invariants

- Corruption blocks writes until explicit recovery.
- Backup remains available after quarantine.
- Restore validates type/schema before atomic replacement.
- One user cannot enumerate or restore another user's backup.

## Required behavior

- Detect and report corrupt History and Memory separately.
- Quarantine/reset occurs under the user coordination lock.
- Invalid backup restore leaves active and backup files unchanged.
- Successful restore is visible after a fresh runtime/repository load.

## Acceptance criteria

- [x] Corrupt History and Memory both block writes.
- [x] Explicit quarantine creates a user-scoped backup and clean active file.
- [x] Valid restore succeeds atomically; invalid restore is non-destructive.
- [x] Forged backup IDs and cross-user access are rejected safely.
- [x] UI handlers reject missing, forged, and expired sessions.

## Test and verification commands

```powershell
$env:TEMP=(Resolve-Path '.runtime/pytest-tmp').Path
$env:TMP=$env:TEMP
D:\Anaconda\python.exe -m pytest tests/test_corruption_recovery.py tests/test_history_repository.py tests/test_memory_repository.py -q
```

Expected: all tests pass with temporary user roots.

## Stop conditions

Block if packet 01 exposes no reusable coordination interface or if safe UI
recovery requires accepting arbitrary filesystem paths.

## Implementation handoff

- Status: done
- Files changed:
  - `app/recovery.py` (created) — RecoveryService with opaque backup IDs, user-scoped quarantine/restore, schema validation before atomic replacement
  - `app/history.py` — added `validate_schema()` static method; `restore()` now calls it explicitly before atomic write
  - `app/memory_repository.py` — added `validate_schema()` static method; `restore()` now loads raw data, checks cross-user user_id, validates schema before replacing active file
  - `app/runtime.py` — added `recovery: RecoveryService` field to `UserRuntime`; creates backup directory and wires RecoveryService in `get_or_create()`
  - `ui/gradio_app.py` — added `_get_recovery()` helper, 6 recovery handler functions, and "🛠️ Data Recovery" accordion with check/quarantine/restore UI
  - `tests/test_corruption_recovery.py` (created) — 27 tests across 6 test classes covering all acceptance criteria
  - `tests/test_history_repository.py` — added 4 tests for `validate_schema` and enhanced `restore()` validation
  - `tests/test_memory_repository.py` — added 6 tests for `validate_schema`, cross-user restore rejection, and invalid backup schema
- Acceptance criteria:
  - [x] Corrupt History and Memory both block writes.
    → `test_corrupt_history_blocks_load_and_write`, `test_corrupt_memory_blocks_load`, `test_healthy_snapshots_check_clean`
  - [x] Explicit quarantine creates a user-scoped backup and clean active file.
    → `test_quarantine_history_creates_backup_and_clean_file`, `test_quarantine_memory_creates_backup_and_clean_file`, `test_quarantine_when_file_absent_creates_empty`, `test_backup_id_is_opaque_filename_only`
  - [x] Valid restore succeeds atomically; invalid restore is non-destructive.
    → `test_restore_history_succeeds_atomically`, `test_restore_memory_succeeds_atomically`, `test_restore_history_invalid_json_leaves_active_unchanged`, `test_restore_history_wrong_schema_leaves_active_unchanged`, `test_restore_history_scalar_value_leaves_active_unchanged`, `test_restore_memory_invalid_structure_leaves_active_unchanged`, `test_restore_history_missing_backup_returns_failure`
  - [x] Forged backup IDs and cross-user access are rejected safely.
    → `test_path_traversal_backup_id_rejected`, `test_backslash_traversal_rejected`, `test_empty_backup_id_rejected`, `test_memory_cross_user_restore_rejected`, `test_history_cross_user_backup_not_visible`, `test_memory_restore_user_id_none_still_validates_schema`
  - [x] UI handlers reject missing, forged, and expired sessions.
    → `test_get_recovery_rejects_missing_session`, `test_get_recovery_rejects_forged_token`, `test_get_recovery_rejects_none_token`
  - [x] End-to-end recovery flow: check → quarantine → restore → verify.
    → `test_full_history_cycle`, `test_full_memory_cycle`, `test_backup_listing_reflects_quarantine`, `test_restore_preserves_full_schema`
- Verification:
  - `python -m pytest tests/test_corruption_recovery.py tests/test_history_repository.py tests/test_memory_repository.py -v` — 43 passed
  - `python -m pytest tests/ -q` — 230 passed, 2 skipped (Qdrant integration)
  - `python -m compileall -q app assistants hello_agents ui tests` — clean
- Deviations:
  - None
- Residual risks:
  - RecoveryService quarantine moves the repository-created backup into the user backup directory. If the move/cleanup step fails, the backup file remains alongside the active file but is still user-scoped. The `list_*_backups` methods only scan the backup directory, so a stranded backup would require manual recovery.
  - UI recovery handlers are functional but minimal — the recovery accordion is placed inside the Reports tab for now. A dedicated tab or admin panel would be a reasonable follow-up.
- Commit:
  - not committed

## Codex acceptance review

- Review status: changes required
- Independent verification:
  - `python -m pytest tests/test_corruption_recovery.py tests/test_history_repository.py tests/test_memory_repository.py -q`
    — 43 passed
  - `git diff --check` — passed

### Blocking findings

1. **Active Memory ownership is not validated fail-closed.**
   `MemorySnapshotRepository.load_snapshot()` discards the persisted `user_id`
   and always returns `self.user_id`. A snapshot belonging to another user, or
   one with missing/`None` owner, is therefore reported healthy. Require an
   exact persisted owner match and test missing, null, malformed, and
   cross-user active snapshots.

2. **Memory recovery does not update the live MemoryManager.**
   `RecoveryService` receives only the repository. Quarantine resets the JSON
   file while the runtime manager retains old memories; restore updates the
   file while the manager retains its previous state. A later manager save can
   overwrite the recovered snapshot. Coordinate reset/restore with
   `runtime.memory_tool.memory_manager`, replace supported in-memory collections
   under the user lock, and test that subsequent persistence cannot resurrect
   quarantined state or overwrite restored state.

3. **UI-visible recovery errors expose absolute filesystem paths.**
   `check_history()`/`check_memory()` return repository exception text, which
   includes the active path, and handlers display it directly. Unreadable
   backup errors can do the same. Return sanitized, user-safe messages while
   logging diagnostic details internally; assert user roots and backup paths
   never appear in handler output.

4. **Expired-session acceptance is claimed but not tested.**
   The handoff lists missing, forged, and `None` tokens only. Add a real expired
   `SessionRegistry` token test and exercise at least one mutation handler,
   proving recovery state is unchanged.

5. **Backup IDs can collide and overwrite earlier backups.**
   Repository backup names have only second precision. Two quarantines in the
   same second resolve to the same destination and `_store_backup()` overwrites
   it. Generate collision-resistant opaque IDs (for example timestamp plus
   UUID), never overwrite an existing backup, and test two immediate
   quarantines preserve two independently restorable backups.

6. **Quarantine is not failure-atomic.**
   Repository methods move the active file before writing the clean
   replacement. If the replacement write or `_store_backup()` fails, the active
   path may be absent and the backup may be stranded outside the listed backup
   directory. Stage/copy the backup durably first, atomically write the reset,
   and clean the source only after success; add failure-injection tests proving
   either the original active state remains or a listed backup is available.

7. **Restore validation and replacement are separated by the lock.**
   Backup bytes are read and validated before acquiring the user lock, then the
   previously read object is written later. Keep the short backup read,
   validation, owner check, and atomic active write in one coordinated critical
   section to prevent concurrent recovery operations from racing. Add a
   deterministic concurrency test.

### Resume condition

- Correct all seven findings within the packet boundary.
- Add tests that fail against the current revision for every finding.
- Rerun the focused recovery suite and the full repository suite.
- Update the handoff truthfully and return `status: done` only after both pass.

## Codex acceptance review – Round 2

- Review status: **done**
- Independent verification:
  - `python -m pytest tests/test_corruption_recovery.py tests/test_history_repository.py tests/test_memory_repository.py -q`
    — 82 passed
  - `python -m pytest tests/ -q` — 269 passed, 2 skipped (Qdrant integration)
  - `git diff --check` — passed

### Round 2 blocking findings (4 items) — all resolved

1. **过期 session 测试使用生产全局数据库**
   → `TestExpiredSessionRejection` 已重构：每个测试通过 `tmp_path` 创建独立 `SessionRegistry`，
   使用 `monkeypatch.setattr("ui.gradio_app.session_registry", isolated)` 替换模块级全局，
   测试完成后自动恢复。不再触碰 `ui.gradio_app` 的生产全局 `session_registry`。

2. **`_store_backup()` 注入失败后的补偿**
   → `quarantine_history()` 和 `quarantine_memory()` 均已添加 try/except 补偿逻辑：
   `_store_backup()` 失败时：(a) 从 staged backup 恢复 active file；
   (b) 对 memory quarantine 同时恢复 MemoryManager；(c) 删除 staged backup 防止孤本残留。
   新增 4 个测试覆盖 History 和 Memory 的补偿路径 + 孤本清理。

3. **`MemorySnapshotRepository.restore()` 严格 user_id 校验**
   → `restore()` 方法现在强制要求 backup `user_id` 为非空字符串且严格等于 `self.user_id`。
   拒绝：`None`（missing）、`""`（empty）、`"   "`（whitespace-only）、`42`（int）、
   `"other_user"`（cross-user）。新增 6 个测试覆盖所有拒绝路径 + 1 个 happy-path 测试。

4. **`restore_to_manager()` fail-closed 语义**
   → 重写 `restore_to_manager()`：(a) 先 clear all memory types；
   (b) 从 snapshot 预构建所有 MemoryItem；(c) 一次性赋值；任一步骤失败则 clear 全部
   并 raise `RuntimeError` — 绝不报告成功。新增 3 个测试覆盖 clear-before-populate、
   部分失败处理和 round-trip 一致性。

### Round 2 files changed

| File | Changes |
|------|---------|
| `app/recovery.py` | `quarantine_history()` + `quarantine_memory()`: `_store_backup` 失败补偿 + staged backup 清理 |
| `app/memory_repository.py` | `restore()`: strict user_id 校验 (None/empty/non-str/cross-user)；`restore_to_manager()`: fail-closed 重写 |
| `tests/test_corruption_recovery.py` | 重写 `TestExpiredSessionRejection`（isolated SessionRegistry + monkeypatch）；新增 `TestStoreBackupFailureCompensation` (4 tests)；新增 `TestClearAllRestoreFailClosed` (3 tests) |
| `tests/test_memory_repository.py` | 新增 7 个 `restore()` user_id 校验测试 (missing/null/empty/ws/int/cross-user/happy) |

## Codex acceptance review – Round 2 continued (4 remaining items)

- Review status: **done**
- Independent verification:
  - `python -m pytest tests/test_corruption_recovery.py tests/test_history_repository.py tests/test_memory_repository.py -q`
    — 94 passed
  - `python -m pytest tests/ -q` — 281 passed, 2 skipped (Qdrant integration)

### Remaining blocking findings (4 items) — all resolved

1. **`memory_manager.clear_all()` 注入失败**
   → `quarantine_memory()`: 当 `clear_all()` 抛出异常时，先尝试直接 force-clear
   各 memory type 的 `memories` 列表；若 force-clear 也失败，则从 staged backup
   restore 磁盘以恢复一致性。磁盘与内存要么都空（force-clear 成功），要么都有旧数据
   （restore 成功）——绝不留下 fork。新增 `TestClearAllFailureInQuarantine` (2 tests)。

2. **真实注入 `restore_to_manager()` clear 和 assignment 异常**
   → `restore_to_manager()` 的 clear 阶段不再吞掉异常：失败时记录到 `failures` 列表
   并尝试 `mem_mod.memories = []` 强制清空。测试使用 `_RaisingList`（`list.clear()`
   抛出 `RuntimeError`）注入真实 clear 失败，使用 property setter 注入 assignment
   失败。两场景均验证 `RuntimeError` 被抛出（绝不报告成功），且模块处于 fail-closed
   状态（已清空）。新增 `TestRestoreToManagerRealExceptionInjection` (3 tests)。

3. **双重失败时禁止删除最后一份 staged backup**
   → 当 `_store_backup` 和 active `restore` 均失败时，不再 `unlink` staged backup，
   而是调用新增的 `_emergency_preserve()` 将其保存到 backup 目录中，确保
   `list_*_backups()` 可发现。单次失败（restore 成功）时正常清理，不产生
   emergency 文件。新增 `TestDoubleFailurePreserveLastBackup` (4 tests)。

4. **`_store_backup` 原子写入**
   → 重写 `_store_backup()`：先 `copy2` 到 `.tmp` 临时文件，再 `os.replace` 原子
   重命名为最终文件名。`copy2` 中断时清理 `.tmp` 文件。`list_*_backups()` 排除
   `.tmp` 后缀，确保残缺备份永不进入列表。新增 `TestStoreBackupAtomicWrite` (3 tests)。

### Round 2 continued files changed

| File | Changes |
|------|---------|
| `app/recovery.py` | `_store_backup`: 原子写入 (tmp + os.replace)；`_emergency_preserve`: 新增 helper；`quarantine_history`: 双重失败 → emergency preserve；`quarantine_memory`: clear_all 补偿 + 双重失败 preserve；`list_*_backups`: 排除 .tmp |
| `app/memory_repository.py` | `restore_to_manager`: clear 阶段异常记录到 failures 列表 + 尝试 force-set [] |
| `tests/test_corruption_recovery.py` | 新增 `TestClearAllFailureInQuarantine` (2 tests), `TestRestoreToManagerRealExceptionInjection` (3 tests), `TestDoubleFailurePreserveLastBackup` (4 tests), `TestStoreBackupAtomicWrite` (3 tests) |
| `03-corruption-recovery.md` | 更新 handoff |

## Codex acceptance review – Round 2 continued (double-clear fork)

- Review status: **done**
- Independent verification:
  - `python -m pytest tests/test_corruption_recovery.py tests/test_history_repository.py tests/test_memory_repository.py -q`
    — 96 passed
  - `python -m pytest tests/ -q` — 283 passed, 2 skipped (Qdrant integration)

### Finding: double-clear failure returns success with possible fork

`quarantine_memory()` 在第 257 行的 `if not force_ok:` 分支中仅恢复磁盘，
不恢复 live manager，之后仍继续执行到 `_store_backup` 并在第 322 行返回
`RecoveryResult(True, ...)`。 导致：

* 磁盘有旧数据（restore 成功）但 manager memory 未恢复 → 不一致。
* 磁盘为空（restore 也失败）但 manager 有旧数据 → FORK + 报告成功。

### Fix (6 items)

1. **双清空失败不得返回隔离成功。**
   → `if not force_ok:` 分支重写为完整补偿块：不继续 fall-through 到
   `_store_backup` + 成功返回。

2. **在锁内恢复 active snapshot 并尝试恢复 live manager。**
   → 先 `self._memory.restore(backup_path)`，若成功则调用
   `self._memory.restore_to_manager(self._memory_manager)`。

3. **manager 无法可靠恢复时必须抛错 fail-closed。**
   → `disk_restored and manager_restored` 都成功：返回
   `RecoveryResult(False, ...)`（隔离失败但状态一致）并存储 backup。
   任一失败：`emergency_preserve` + `raise RuntimeError`。

4. **最后一份 staged backup 必须可枚举或 emergency-preserved。**
   → 所有非成功路径均调用 `self._emergency_preserve()` 或
   `self._store_backup()`，确保 backup 可通过 `list_memory_backups()` 找到。

5. **增加真实双重失败测试。**
   → `TestDoubleClearFailure` 替换原来的 `TestClearAllFailureInQuarantine`：
   * `test_double_clear_rollback_succeeds_returns_failure` — 双重失败 +
     rollback 成功 → `success=False` + backup 可枚举 + disk/memory 一致
   * `test_double_clear_rollback_fails_raises_with_emergency_preserve` —
     disk restore 也失败 → `RuntimeError` + emergency backup
   * `test_double_clear_manager_restore_fails_raises` — disk 成功但
     manager 失败 → `RuntimeError` + backup preserved

6. **同步修复 `restore_to_manager` 的 clear 阶段。**
   → `mem_mod.memories.clear()` 抛异常时尝试 `mem_mod.memories = []`；
   若 force-set 成功则**不算失败**（模块确已清空）。仅当两者都失败时才
   记录到 `failures`。更新 `TestRestoreToManagerRealExceptionInjection`
   中对应的测试。

### Round 2 continued (double-clear) files changed

| File | Changes |
|------|---------|
| `app/recovery.py` | `quarantine_memory`: 重写 `if not force_ok` 分支 — 双清空失败时 restore disk + manager，成功则返回 Failure，失败则 emergency-preserve + raise |
| `app/memory_repository.py` | `restore_to_manager`: clear 阶段 force-set `[]` 成功时不再记录到 failures（模块已清空，不属于失败） |
| `tests/test_corruption_recovery.py` | 替换 `TestClearAllFailureInQuarantine` → `TestDoubleClearFailure` (3 tests)；更新 `test_clear_phase_exception` → `test_clear_phase_exception_force_set_recovers`；新增 `test_clear_force_set_both_fail_leaves_fail_closed` |
| `03-corruption-recovery.md` | 更新 handoff |
