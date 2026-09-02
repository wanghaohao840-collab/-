# Windows Deployment File-Hash Module Loading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all Windows deployment entry points that hash backup archives load `Microsoft.PowerShell.Utility` deterministically so the 21 checksum-related deployment test failures pass.

**Architecture:** Keep the existing `Get-FileHash` calls and checksum workflow unchanged. Add an explicit fail-fast import to each script that directly calls the cmdlet, and protect the dependency with static deployment contract tests.

**Tech Stack:** Windows PowerShell 5.1, PowerShell 7, Python 3.12, pytest 8.4.1

## Global Constraints

- Preserve compatibility with Windows PowerShell 5.1 and PowerShell 7.
- Preserve the existing SHA-256 sidecar format and validation order.
- Do not change backup retention, archive validation, restore swapping, release gates, or rollback behavior.
- Do not add a new runtime dependency.
- Keep the fix limited to deployment entry points that directly invoke `Get-FileHash` and their deployment contract tests.
- Run tests with `D:\python_self_agent\venv\Scripts\python.exe` and a fresh repository-local `--basetemp` path.
- Do not stage or modify the unrelated GraphRAG task-packet edits or historical test artifacts already present in the working tree.

---

### Task 1: Make file-hash module loading deterministic

**Files:**
- Modify: `tests/deploy/test_windows_backup.py`
- Modify: `tests/deploy/test_windows_release.py`
- Modify: `deploy/windows/Backup-Deployment.ps1`
- Modify: `deploy/windows/Restore-Deployment.ps1`
- Modify: `deploy/windows/Invoke-RestoreDrill.ps1`
- Modify: `deploy/windows/Update-Deployment.ps1`

**Interfaces:**
- Consumes: the built-in PowerShell module `Microsoft.PowerShell.Utility`, which exports `Get-FileHash` on Windows PowerShell 5.1 and PowerShell 7.
- Produces: each hash-using entry point imports that module with `-ErrorAction Stop` before importing repository-local modules or performing deployment work.

- [ ] **Step 1: Add failing static dependency-contract tests**

In `tests/deploy/test_windows_backup.py`, add this test after `test_backup_and_restore_scripts_have_static_safety_contracts`:

```python
@pytest.mark.parametrize("script", [BACKUP, RESTORE, DRILL])
def test_backup_entry_points_import_file_hash_module(script: Path):
    source = script.read_text(encoding="utf-8")

    assert "Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop" in source
```

In `tests/deploy/test_windows_release.py`, extend `test_update_script_exists`:

```python
def test_update_script_exists():
    assert UPDATE.is_file()
    source = UPDATE.read_text(encoding="utf-8")
    assert "Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop" in source
```

- [ ] **Step 2: Run the new contracts and verify that they fail for the intended reason**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest -q `
  tests\deploy\test_windows_backup.py::test_backup_entry_points_import_file_hash_module `
  tests\deploy\test_windows_release.py::test_update_script_exists `
  --basetemp=.pytest-tmp-file-hash-red
```

Expected: four assertion failures because none of the four scripts contains the explicit import.

- [ ] **Step 3: Add the minimal fail-fast module import to all four entry points**

In each of the following files, insert the explicit module import immediately after `$ErrorActionPreference = 'Stop'` and before repository-local `Import-Module` statements:

- `deploy/windows/Backup-Deployment.ps1`
- `deploy/windows/Restore-Deployment.ps1`
- `deploy/windows/Invoke-RestoreDrill.ps1`
- `deploy/windows/Update-Deployment.ps1`

The resulting startup block must contain:

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop
```

Do not change any `Get-FileHash` call or checksum logic.

- [ ] **Step 4: Run the new contracts and the original isolated reproduction**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest -q `
  tests\deploy\test_windows_backup.py::test_backup_entry_points_import_file_hash_module `
  tests\deploy\test_windows_release.py::test_update_script_exists `
  tests\deploy\test_windows_backup.py::test_backup_restore_round_trip_uses_fake_docker_and_retains_rollback `
  --basetemp=.pytest-tmp-file-hash-green
```

Expected: all five collected cases pass (three parametrized import cases, one update import case, and one round-trip case).

- [ ] **Step 5: Run both previously failing test modules**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest -q `
  tests\deploy\test_windows_backup.py `
  tests\deploy\test_windows_release.py `
  --basetemp=.pytest-tmp-file-hash-modules
```

Expected: no failures. Any existing capability-dependent skips must retain their documented reasons.

- [ ] **Step 6: Run all deployment tests**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest -q tests\deploy `
  --basetemp=.pytest-tmp-file-hash-deploy
```

Expected: no failures.

- [ ] **Step 7: Run the complete project test suite**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest -q `
  --basetemp=.pytest-tmp-file-hash-full
```

Expected: all tests pass except existing documented skips. In particular, the prior result of 21 failures must be reduced to zero failures.

- [ ] **Step 8: Review and commit only task-owned files**

Run:

```powershell
git diff --check -- `
  tests/deploy/test_windows_backup.py `
  tests/deploy/test_windows_release.py `
  deploy/windows/Backup-Deployment.ps1 `
  deploy/windows/Restore-Deployment.ps1 `
  deploy/windows/Invoke-RestoreDrill.ps1 `
  deploy/windows/Update-Deployment.ps1
git diff -- `
  tests/deploy/test_windows_backup.py `
  tests/deploy/test_windows_release.py `
  deploy/windows/Backup-Deployment.ps1 `
  deploy/windows/Restore-Deployment.ps1 `
  deploy/windows/Invoke-RestoreDrill.ps1 `
  deploy/windows/Update-Deployment.ps1
git add -- `
  tests/deploy/test_windows_backup.py `
  tests/deploy/test_windows_release.py `
  deploy/windows/Backup-Deployment.ps1 `
  deploy/windows/Restore-Deployment.ps1 `
  deploy/windows/Invoke-RestoreDrill.ps1 `
  deploy/windows/Update-Deployment.ps1
git commit -m "fix: load PowerShell file hash module explicitly"
```

Expected: the commit contains only the two deployment test files and four PowerShell entry points. If any verification fails, do not commit; diagnose within this task's scope and repeat the relevant checks.
