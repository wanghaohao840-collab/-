# Windows Operations and Qdrant POSIX Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the hardened Windows operations suite to the Notes branch, move Qdrant durability to a Docker-managed POSIX named volume, and prove backup, isolated restore, health recovery, and rollback before handing real LLM configuration to the operator.

**Architecture:** Selectively restore the finalized Windows-only implementation from `codex/port-windows-ops-main@a25ed2a`, then adapt its state model around a stable `QDRANT_VOLUME_NAME`. Compose owns the production volume; a focused PowerShell volume module exports, imports, inventories, and stages Qdrant state without accessing Docker Desktop internals. Scheduled-task installation remains non-mutating until this branch reaches the stable `D:\python_self_agent` checkout.

**Tech Stack:** PowerShell 5.1, Python 3.11, pytest 8.4.1, Docker Desktop Linux/WSL2 backend, Docker Compose v2, Qdrant 1.18.2, FastAPI/Uvicorn.

## Global Constraints

- Work only in `D:\python_self_agent\.worktrees\release-document-library` on `codex/notes-vertical-slice`.
- Preserve the controller-owned modification to `.superpowers/sdd/progress.md`; do not stage or commit it.
- Use `D:\python_self_agent\venv\Scripts\python.exe` for Python validation and a repository-local `--basetemp`.
- Keep `app` and optional `neo4j` host data under `DEPLOY_DATA_ROOT`; mount Qdrant only through the stable default volume `zhiyan_qdrant_data`, overridable by `QDRANT_VOLUME_NAME`.
- Do not delete or overwrite the legacy NTFS Qdrant directory during migration.
- Never archive `deploy/.env`, secrets, operation logs, databases, uploaded documents, or backup payloads into Git.
- Serialize backup, restore, restore drill, migration, and update through one operations lock.
- Keep updates operator-triggered; do not register an unattended update task.
- Do not register production scheduled tasks until the implementation is integrated into `D:\python_self_agent`.
- Preserve FastAPI/Uvicorn, compiled React assets, `/healthz`, `/legacy/config`, current user/document isolation, and the existing public port contract.

---

### Task 1: Port the Windows recovery and health foundation

**Files:**
- Modify: `.gitignore`
- Create: `deploy/windows/Operations.Common.psm1`
- Create: `deploy/windows/Start-Deployment.ps1`
- Create: `deploy/windows/Test-DeploymentHealth.ps1`
- Create: `deploy/windows/Install-Operations.ps1`
- Create: `deploy/windows/Uninstall-Operations.ps1`
- Create: `tests/deploy/test_windows_operations.py`

**Interfaces:**
- Consumes: `Get-OperationsConfig -RepositoryRoot <string> -EnvFile <string> [-StateRoot <string>] [-BackupRoot <string>]` and Docker Compose.
- Produces: safe configuration/path/process helpers, login recovery, five-minute health recovery, and idempotent installer/uninstaller entry points.

- [ ] **Step 1: Restore the final Windows operations contract test**

```powershell
git restore --source=a25ed2a -- tests/deploy/test_windows_operations.py
```

- [ ] **Step 2: Run the focused contract and confirm the implementation is absent**

Run:

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/deploy/test_windows_operations.py -q --basetemp=.pytest-tmp-winops-red
```

Expected: FAIL because `deploy/windows` files do not exist.

- [ ] **Step 3: Restore the finalized foundation scripts without importing unrelated branch history**

```powershell
git restore --source=a25ed2a -- deploy/windows/Operations.Common.psm1 deploy/windows/Start-Deployment.ps1 deploy/windows/Test-DeploymentHealth.ps1 deploy/windows/Install-Operations.ps1 deploy/windows/Uninstall-Operations.ps1
```

- [ ] **Step 4: Extend generated-state ignore rules**

Append these exact entries without replacing current rules:

```gitignore
deploy-state/
.operations-test/
.operations-test-*/
```

- [ ] **Step 5: Run the focused test and PowerShell parser checks**

Run:

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/deploy/test_windows_operations.py -q --basetemp=.pytest-tmp-winops-green
Get-ChildItem deploy\windows\*.ps1,deploy\windows\*.psm1 | ForEach-Object { $tokens=$null; $errors=$null; [void][Management.Automation.Language.Parser]::ParseFile($_.FullName,[ref]$tokens,[ref]$errors); if ($errors.Count) { throw ($errors | Out-String) } }
```

Expected: pytest PASS and no parser errors.

- [ ] **Step 6: Commit the recovery foundation**

```powershell
git add .gitignore deploy/windows/Operations.Common.psm1 deploy/windows/Start-Deployment.ps1 deploy/windows/Test-DeploymentHealth.ps1 deploy/windows/Install-Operations.ps1 deploy/windows/Uninstall-Operations.ps1 tests/deploy/test_windows_operations.py
git commit -m "feat: port Windows recovery operations"
```

---

### Task 2: Establish the Qdrant named-volume contract

**Files:**
- Modify: `compose.yaml`
- Modify: `deploy/.env.example`
- Modify: `tests/deploy/test_compose_contract.py`
- Create: `deploy/windows/QdrantVolume.Common.psm1`
- Create: `tests/deploy/test_windows_qdrant_volume.py`

**Interfaces:**
- Consumes: `QDRANT_VOLUME_NAME` from the deployment environment, defaulting to `zhiyan_qdrant_data`.
- Produces: `Get-QdrantVolumeName`, `Assert-SafeDockerResourceName`, `New-QdrantVolume`, `Export-QdrantVolume`, `Import-QdrantVolume`, and `Get-QdrantInventory`.

- [ ] **Step 1: Add failing Compose and module contracts**

The Compose assertions must require:

```python
assert 'source: ${QDRANT_VOLUME_NAME:-zhiyan_qdrant_data}' in source
assert "target: /qdrant/storage" in source
assert "type: volume" in source
assert "QDRANT_VOLUME_NAME=zhiyan_qdrant_data" in env_source
assert '"${DEPLOY_DATA_ROOT:-./deploy-data}/qdrant:/qdrant/storage"' not in source
```

The PowerShell module test must reject `/`, `..`, whitespace, shell metacharacters, and names outside `^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`.

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run:

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/deploy/test_compose_contract.py tests/deploy/test_windows_qdrant_volume.py -q --basetemp=.pytest-tmp-volume-red
```

Expected: FAIL on the old NTFS bind mount and missing volume module.

- [ ] **Step 3: Replace only the Qdrant mount with a named volume**

Use Compose long syntax:

```yaml
    volumes:
      - type: volume
        source: ${QDRANT_VOLUME_NAME:-zhiyan_qdrant_data}
        target: /qdrant/storage
```

Keep `app` and `neo4j` mounts unchanged. Add to `deploy/.env.example`:

```dotenv
QDRANT_VOLUME_NAME=zhiyan_qdrant_data
```

- [ ] **Step 4: Implement the focused volume module**

Use the exact public signatures:

```powershell
function Get-QdrantVolumeName {
    param([Parameter(Mandatory)]$Config)
}
function New-QdrantVolume {
    param([Parameter(Mandatory)][string]$Name)
}
function Export-QdrantVolume {
    param([Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)][string]$Archive, [Parameter(Mandatory)][string]$HelperImage)
}
function Import-QdrantVolume {
    param([Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)][string]$Archive, [Parameter(Mandatory)][string]$HelperImage, [switch]$RequireEmpty)
}
function Get-QdrantInventory {
    param([Parameter(Mandatory)][string]$BaseUrl)
}
```

Every Docker invocation must use argument arrays through `Invoke-External`; volume sources are read-only during export; archive and staging paths are validated before bind mounting.

- [ ] **Step 5: Run focused tests and render Compose configuration**

Run:

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/deploy/test_compose_contract.py tests/deploy/test_windows_qdrant_volume.py -q --basetemp=.pytest-tmp-volume-green
docker compose --env-file deploy/.env config
```

Expected: PASS; resolved Qdrant mount type is `volume` and source is `zhiyan_qdrant_data` unless explicitly overridden.

- [ ] **Step 6: Commit the storage contract**

```powershell
git add compose.yaml deploy/.env.example deploy/windows/QdrantVolume.Common.psm1 tests/deploy/test_compose_contract.py tests/deploy/test_windows_qdrant_volume.py
git commit -m "feat: move Qdrant to a POSIX named volume"
```

---

### Task 3: Add a reversible NTFS-to-volume migration

**Files:**
- Create: `deploy/windows/Move-QdrantToVolume.ps1`
- Create: `tests/deploy/test_windows_qdrant_migration.py`
- Modify: `deploy/windows/README.md`

**Interfaces:**
- Consumes: `-RepositoryRoot`, `-EnvFile`, `-LegacyQdrantRoot`, `-StateRoot`, `-BackupRoot`, and optional `-WhatIf`.
- Produces: a checksummed pre-migration archive, a populated target volume, inventory evidence, and an unchanged legacy directory.

- [ ] **Step 1: Write the failing migration contract**

Tests must assert the script:

```python
assert "SupportsShouldProcess" in source
assert "LegacyQdrantRoot" in source
assert "Export-LegacyQdrantDirectory" in source
assert "Get-QdrantInventory" in source
assert "Remove-Item -LiteralPath $legacy" not in source
assert "docker volume rm" not in source.lower()
```

The behavioral harness must mock Docker and prove that a failed target health check preserves both the legacy directory and destination volume.

- [ ] **Step 2: Run the migration test and confirm it fails**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/deploy/test_windows_qdrant_migration.py -q --basetemp=.pytest-tmp-migrate-red
```

Expected: FAIL because the migration entry point is missing.

- [ ] **Step 3: Implement preflight, export, import, and verification**

The script must execute this ordered state machine:

```text
preflight -> acquire lock -> record running services -> stop services
-> archive legacy directory -> create/import named volume -> start qdrant
-> wait ready -> compare inventories -> start prior services -> shallow smoke
-> record success -> release lock
```

On every failure after the stop, restart the prior service set when safe, retain the legacy directory, archive, destination volume, and structured diagnostic status, then return non-zero.

- [ ] **Step 4: Run focused tests including `-WhatIf`**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/deploy/test_windows_qdrant_migration.py -q --basetemp=.pytest-tmp-migrate-green
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy\windows\Move-QdrantToVolume.ps1 -RepositoryRoot $PWD -EnvFile deploy\.env -LegacyQdrantRoot deploy-data\qdrant -WhatIf
```

Expected: PASS; `-WhatIf` reports actions without creating or changing a Docker volume.

- [ ] **Step 5: Commit the migration workflow**

```powershell
git add deploy/windows/Move-QdrantToVolume.ps1 deploy/windows/README.md tests/deploy/test_windows_qdrant_migration.py
git commit -m "feat: add reversible Qdrant volume migration"
```

---

### Task 4: Port and adapt cold backup, restore, and isolated drills

**Files:**
- Create: `deploy/windows/Backup.Common.psm1`
- Create: `deploy/windows/Backup-Deployment.ps1`
- Create: `deploy/windows/Restore-Deployment.ps1`
- Create: `deploy/windows/Invoke-RestoreDrill.ps1`
- Create: `tests/deploy/test_windows_backup.py`
- Modify: `tests/deploy/test_backup_restore_contract.py`

**Interfaces:**
- Consumes: `QdrantVolume.Common.psm1`, the global operations lock, current Compose project state, and the latest valid backup bundle.
- Produces: `New-DeploymentBackup`, `Restore-DeploymentBackup`, and isolated drill results covering host data plus the Qdrant volume.

- [ ] **Step 1: Restore the final pre-volume backup contract and implementation**

```powershell
git restore --source=a25ed2a -- tests/deploy/test_windows_backup.py deploy/windows/Backup.Common.psm1 deploy/windows/Backup-Deployment.ps1 deploy/windows/Restore-Deployment.ps1 deploy/windows/Invoke-RestoreDrill.ps1
```

- [ ] **Step 2: Extend tests before adapting implementation**

Require the bundle manifest to include these exact fields:

```json
{
  "schema_version": 2,
  "qdrant_volume_name": "zhiyan_qdrant_data",
  "qdrant_archive": "qdrant-volume.tar.gz",
  "host_data_archive": "host-data.tar.gz"
}
```

Require checksum validation for both payloads, staging-volume restore, rollback-volume capture, unique drill volume names, a non-production port, and cleanup restricted to names generated by the current operation.

- [ ] **Step 3: Run tests and confirm the old directory-only model fails**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/deploy/test_windows_backup.py tests/deploy/test_backup_restore_contract.py -q --basetemp=.pytest-tmp-backup-red
```

Expected: FAIL on missing named-volume bundle behavior.

- [ ] **Step 4: Adapt backup and restore around schema version 2**

Implement the exact payload layout:

```text
assistant-<UTC timestamp>/
  manifest.json
  host-data.tar.gz
  host-data.tar.gz.sha256
  qdrant-volume.tar.gz
  qdrant-volume.tar.gz.sha256
```

Production restore imports Qdrant into a staging volume first. Before activation it exports the current production volume to the rollback area. Health or smoke failure restores both the host-data rollback directory and Qdrant rollback archive.

- [ ] **Step 5: Adapt the monthly drill to isolated resources**

Use a unique Compose project, unique `DEPLOY_DATA_ROOT`, unique Qdrant volume, and an available non-7860 host port. Do not mount the production data root or production volume into the drill project.

- [ ] **Step 6: Run backup/restore tests and parser checks**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/deploy/test_windows_backup.py tests/deploy/test_backup_restore_contract.py -q --basetemp=.pytest-tmp-backup-green
Get-ChildItem deploy\windows\*.ps1,deploy\windows\*.psm1 | ForEach-Object { $tokens=$null; $errors=$null; [void][Management.Automation.Language.Parser]::ParseFile($_.FullName,[ref]$tokens,[ref]$errors); if ($errors.Count) { throw ($errors | Out-String) } }
```

Expected: PASS and no parser errors.

- [ ] **Step 7: Commit the volume-aware continuity workflows**

```powershell
git add deploy/windows/Backup.Common.psm1 deploy/windows/Backup-Deployment.ps1 deploy/windows/Restore-Deployment.ps1 deploy/windows/Invoke-RestoreDrill.ps1 tests/deploy/test_windows_backup.py tests/deploy/test_backup_restore_contract.py
git commit -m "feat: back up and restore Qdrant named volumes"
```

---

### Task 5: Port secure updates and preserve rollback guarantees

**Files:**
- Create: `deploy/windows/Update-Deployment.ps1`
- Create: `tests/deploy/test_windows_release.py`
- Modify: `deploy/windows/Install-Operations.ps1`
- Modify: `deploy/windows/Operations.Common.psm1`

**Interfaces:**
- Consumes: schema-version-2 backup, stable image identities, Docker Scout, Compose health, and shallow smoke.
- Produces: operator-triggered update with image/data rollback and four task definitions that omit unattended updates.

- [ ] **Step 1: Restore the final hardened release workflow and tests**

```powershell
git restore --source=a25ed2a -- deploy/windows/Update-Deployment.ps1 tests/deploy/test_windows_release.py
```

- [ ] **Step 2: Add failing volume-aware rollback assertions**

Tests must require update preflight to call the schema-version-2 backup entry point before build/recreate, retain prior image IDs, restore Qdrant volume state after a failed candidate health gate, and report compensation errors with high priority.

- [ ] **Step 3: Run focused release tests and confirm adaptation is required**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/deploy/test_windows_release.py -q --basetemp=.pytest-tmp-release-red
```

Expected: FAIL on the old backup/rollback interface.

- [ ] **Step 4: Adapt update orchestration to the new backup interface**

Preserve this order:

```text
preflight -> lock -> consistent backup -> record image IDs -> build candidate
-> Docker Scout gate -> recreate -> health -> shallow smoke -> success
```

Any failure after candidate activation invokes data and image rollback. Aggregate rollback failures without hiding the original stage and failure detail.

- [ ] **Step 5: Verify only four scheduled tasks are defined**

The installer must define exactly:

```text
PythonSelfAgent-LoginRecovery
PythonSelfAgent-Health
PythonSelfAgent-DailyBackup
PythonSelfAgent-MonthlyRestoreDrill
```

It must not register `Update-Deployment.ps1`.

- [ ] **Step 6: Run release and operations tests**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/deploy/test_windows_release.py tests/deploy/test_windows_operations.py -q --basetemp=.pytest-tmp-release-green
```

Expected: PASS.

- [ ] **Step 7: Commit secure update support**

```powershell
git add deploy/windows/Update-Deployment.ps1 deploy/windows/Install-Operations.ps1 deploy/windows/Operations.Common.psm1 tests/deploy/test_windows_release.py tests/deploy/test_windows_operations.py
git commit -m "feat: integrate secure Windows deployment updates"
```

---

### Task 6: Document installation, migration, backup, and LLM handoff

**Files:**
- Create: `deploy/windows/README.md`
- Modify: `deploy/README.md`
- Modify: `README.md`
- Modify: `deploy/.env.example`

**Interfaces:**
- Consumes: all preceding commands and environment variables.
- Produces: operator procedures with exact preflight, migration, verification, stable-root installation, rollback, and LLM configuration commands.

- [ ] **Step 1: Restore the operations README as a factual base**

```powershell
git restore --source=a25ed2a -- deploy/windows/README.md
```

- [ ] **Step 2: Update documentation for the named-volume contract**

Document these exact operator checkpoints:

```powershell
docker volume inspect zhiyan_qdrant_data
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy\windows\Move-QdrantToVolume.ps1 -RepositoryRoot $PWD -EnvFile deploy\.env -LegacyQdrantRoot deploy-data\qdrant
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy\windows\Backup-Deployment.ps1 -RepositoryRoot $PWD -EnvFile deploy\.env
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy\windows\Invoke-RestoreDrill.ps1 -RepositoryRoot $PWD -EnvFile deploy\.env
```

State explicitly that `Install-Operations.ps1` runs only after integration into `D:\python_self_agent`, and that `deploy/.env` is ignored and must contain the real `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL_ID` before deep smoke.

- [ ] **Step 3: Verify documentation and secret hygiene**

```powershell
rg -n "QDRANT_VOLUME_NAME|Move-QdrantToVolume|Install-Operations|smoke_test.py --deep" README.md deploy/README.md deploy/windows/README.md deploy/.env.example
git grep -n -E "sk-[A-Za-z0-9]{16,}|api[_-]?key\s*=\s*[^r$]" -- . ':(exclude)deploy/.env.example'
```

Expected: operational terms are present and the secret scan prints no credential.

- [ ] **Step 4: Commit operator documentation**

```powershell
git add README.md deploy/README.md deploy/.env.example deploy/windows/README.md
git commit -m "docs: document Windows continuity operations"
```

---

### Task 7: Execute the real Qdrant migration and continuity acceptance

**Files:**
- Runtime only: `deploy/.env`
- Runtime only: `deploy-state/`
- Runtime only: configured external backup root
- Runtime only: Docker volume `zhiyan_qdrant_data`

**Interfaces:**
- Consumes: Docker Desktop, current legacy `deploy-data/qdrant`, current Compose images, and all repository scripts.
- Produces: a healthy deployment on POSIX storage, verified backup bundle, successful isolated drill, and retained rollback evidence.

- [ ] **Step 1: Start Docker Desktop and wait for the Linux daemon**

```powershell
Start-Process -FilePath "$Env:ProgramFiles\Docker\Docker\Docker Desktop.exe" -WindowStyle Hidden
$deadline=(Get-Date).AddMinutes(3); do { docker version *> $null; if ($LASTEXITCODE -eq 0) { break }; Start-Sleep -Seconds 3 } while ((Get-Date) -lt $deadline); if ($LASTEXITCODE -ne 0) { throw 'Docker daemon did not become ready' }
```

Expected: `docker version` returns zero before the deadline.

- [ ] **Step 2: Capture the legacy state and run migration**

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy\windows\Move-QdrantToVolume.ps1 -RepositoryRoot $PWD -EnvFile deploy\.env -LegacyQdrantRoot deploy-data\qdrant
```

Expected: exit zero, original directory still exists, migration backup checksum verifies, and volume `zhiyan_qdrant_data` exists.

- [ ] **Step 3: Verify POSIX-backed runtime and shallow smoke**

```powershell
docker compose --env-file deploy/.env up -d --build
docker inspect (docker compose --env-file deploy/.env ps -q qdrant) --format '{{json .Mounts}}'
D:\python_self_agent\venv\Scripts\python.exe deploy\smoke_test.py --env-file deploy\.env
```

Expected: Qdrant mount type is `volume`, source is `zhiyan_qdrant_data`, all services are healthy, and shallow smoke passes.

- [ ] **Step 4: Create and verify a real cold backup**

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy\windows\Backup-Deployment.ps1 -RepositoryRoot $PWD -EnvFile deploy\.env
```

Expected: exit zero; bundle and all nested SHA-256 checksums verify; the prior running service set is restored.

- [ ] **Step 5: Execute the isolated monthly restore drill manually**

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy\windows\Invoke-RestoreDrill.ps1 -RepositoryRoot $PWD -EnvFile deploy\.env
```

Expected: exit zero; temporary project, port, host data, and Qdrant volume differ from production; health and smoke pass; validated temporary resources are removed.

- [ ] **Step 6: Validate scheduled-task definitions without installing them**

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy\windows\Install-Operations.ps1 -RepositoryRoot $PWD -EnvFile deploy\.env -WhatIf
```

Expected: all preflight and in-memory definitions pass; output shows prospective changes only; no production task is registered from the worktree.

---

### Task 8: Run repository acceptance and open LLM configuration

**Files:**
- Runtime only: `deploy/.env`
- Modify only on regression: files already owned by Tasks 1-6

**Interfaces:**
- Consumes: the integrated branch and healthy migrated deployment.
- Produces: final phase-one/phase-two acceptance evidence and an open environment file awaiting operator configuration.

- [ ] **Step 1: Run the complete deployment contract suite**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/deploy -q --basetemp=.pytest-tmp-ops-final
```

Expected: PASS.

- [ ] **Step 2: Run current backend regression tests relevant to Qdrant and Notes**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/memory tests/integration/test_qdrant_document_scope.py tests/test_notes_api.py tests/test_note_service.py -q --basetemp=.pytest-tmp-ops-regression
```

Expected: PASS, with only explicitly documented environment skips.

- [ ] **Step 3: Verify repository hygiene and commit only corrective code if required**

```powershell
git diff --check
git status --short
git log --oneline -8
```

Expected: only the pre-existing `.superpowers/sdd/progress.md` modification and ignored runtime artifacts remain; every implementation task has a focused commit.

- [ ] **Step 4: Open the ignored deployment environment file for the operator**

Open:

```text
D:\python_self_agent\.worktrees\release-document-library\deploy\.env
```

Do not run deep smoke yet. Wait for explicit confirmation that `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL_ID` contain real values.

- [ ] **Step 5: After operator confirmation, execute deep smoke**

```powershell
D:\python_self_agent\venv\Scripts\python.exe deploy\smoke_test.py --env-file deploy\.env --deep
```

Expected: authentication, document ingestion, retrieval, and a real LLM answer complete successfully with grounded evidence.
