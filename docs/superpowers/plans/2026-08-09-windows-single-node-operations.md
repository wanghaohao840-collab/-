# Windows Single-Node Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a repository-managed Windows operations package that restores the Docker Desktop deployment after login, monitors it, backs it up safely, rehearses recovery, restricts intranet access, and performs scanned upgrades with rollback.

**Architecture:** Keep the existing single-replica Compose application unchanged at the business layer. Add small PowerShell 5.1 modules and entry scripts under `deploy/windows/`; every script consumes an explicit repository root and `deploy/.env`, shares common logging/path safety helpers, and fails closed before system or data changes. Repository changes are implemented and tested first; Task Scheduler, firewall, ACL, and real backup installation happen only in the final rollout task.

**Tech Stack:** Windows PowerShell 5.1, Windows Task Scheduler, Windows Defender Firewall, Docker Desktop 4.84+, Docker Engine 29+, Docker Compose v5, Docker Scout 1.24+, Windows `tar.exe`, Python 3.11, pytest, GitHub Actions, Trivy 0.72.0.

## Global Constraints

- Target the current Windows machine and Docker Desktop; Docker may start only after the current user logs in.
- Keep exactly one `app` container and one worker; do not change authentication, Session, Memory, RAG, Storage, user isolation, or `document_id` contracts.
- Allow inbound TCP 7860 only for the Windows `Private` profile and `LocalSubnet`; publish no Qdrant or Neo4j host ports.
- Use `D:\python_self_agent_backups` outside `DEPLOY_DATA_ROOT` for backups.
- Run a cold backup daily at 03:00; retain 7 daily sets and 4 weekly sets.
- Run an isolated restore drill on the first Sunday of each month at 04:00.
- Run health checks every 5 minutes and bound login recovery to 180 seconds.
- Never commit, archive, print, or copy `deploy/.env` outside a temporary ACL-restricted drill file that is always deleted.
- Do not run `git pull`, clean user changes, delete rollback data, or use `docker compose down --volumes`.
- Use project `venv` for Python validation: `.\venv\Scripts\python.exe -m pytest`.
- Preserve the four pre-existing modified GraphRAG task-packet files and every unrelated change.
- System installation must stop before writes when the active Windows network category is not `Private` or the shell is not elevated.

---

## File map

- `deploy/windows/Operations.Common.psm1`: configuration, path validation, process execution, logging, state, redaction, notification throttling, and Docker health primitives.
- `deploy/windows/Backup.Common.psm1`: archive validation, backup set discovery, retention selection, and safe file removal.
- `deploy/windows/Start-Deployment.ps1`: bounded login recovery.
- `deploy/windows/Test-DeploymentHealth.ps1`: five-minute health check and one recovery attempt.
- `deploy/windows/Backup-Deployment.ps1`: Windows cold backup and retention.
- `deploy/windows/Restore-Deployment.ps1`: checksum-validated restore with rollback preservation.
- `deploy/windows/Invoke-RestoreDrill.ps1`: isolated monthly restore rehearsal.
- `deploy/windows/Install-Operations.ps1`: idempotent ACL, firewall, and scheduled-task registration.
- `deploy/windows/Uninstall-Operations.ps1`: narrowly scoped task/rule removal.
- `deploy/windows/Update-Deployment.ps1`: preflight, backup, build, scan, smoke, and rollback.
- `deploy/windows/README.md`: Windows operator guide.
- `tests/deploy/test_windows_operations.py`: executable PowerShell and static system-contract tests.
- `tests/deploy/test_windows_backup.py`: backup/restore/retention tests using temporary directories and fake Docker commands.
- `tests/deploy/test_windows_release.py`: image pinning, scanning, and upgrade rollback contracts.
- `.github/workflows/deployment.yml`: deployment tests, builds, and pinned Trivy scan.
- `compose.yaml`: bounded local log driver configuration and stable local image names.
- `Dockerfile`, `deploy/qdrant.Dockerfile`: version-plus-digest base images.
- `.gitignore`: ignore `deploy-state/` and local operations test output.
- `deploy/.env.example`, `deploy/README.md`, `README.md`: Windows operations configuration and documentation.

---

### Task 1: Add the common Windows operations foundation

**Files:**
- Create: `deploy/windows/Operations.Common.psm1`
- Create: `tests/deploy/test_windows_operations.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces `Get-OperationsConfig -RepositoryRoot <path> -EnvFile <path> -StateRoot <path> -BackupRoot <path> -> PSCustomObject`.
- Produces `Assert-SafePath -Path <path> -AllowedRoot <path> [-AllowRoot] -> absolute string`.
- Produces `Invoke-External -FilePath <exe> -ArgumentList <string[]> [-AllowExitCodes <int[]>] -> string[]`.
- Produces `Protect-LogText`, `Write-OperationsLog`, `Write-OperationsStatus`, `Send-OperationsNotification`, `Test-DockerReady`, and `Test-ComposeHealth`.

- [ ] **Step 1: Write failing executable tests for redaction and path boundaries**

Add these tests to `tests/deploy/test_windows_operations.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).parents[2]
MODULE = ROOT / "deploy" / "windows" / "Operations.Common.psm1"


def run_ps(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        cwd=ROOT,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def ps_quote(path: Path) -> str:
    return str(path).replace("'", "''")


def test_common_module_redacts_named_secrets_and_url_credentials():
    result = run_ps(
        f"Import-Module '{ps_quote(MODULE)}' -Force; "
        "Protect-LogText 'LLM_API_KEY=secret https://user:pass@example.test/v1'"
    )
    assert result.returncode == 0, result.stderr
    assert "secret" not in result.stdout
    assert "user:pass" not in result.stdout
    assert "[REDACTED]" in result.stdout


def test_safe_path_rejects_sibling_prefix(tmp_path: Path):
    root = tmp_path / "data"
    sibling = tmp_path / "data-escape" / "file.txt"
    root.mkdir()
    sibling.parent.mkdir()
    result = run_ps(
        f"Import-Module '{ps_quote(MODULE)}' -Force; "
        f"Assert-SafePath -Path '{ps_quote(sibling)}' -AllowedRoot '{ps_quote(root)}'"
    )
    assert result.returncode != 0
    assert "outside allowed root" in result.stderr


def test_status_json_contains_no_secret_values(tmp_path: Path):
    state = tmp_path / "state"
    result = run_ps(
        f"Import-Module '{ps_quote(MODULE)}' -Force; "
        f"Write-OperationsStatus -StateRoot '{ps_quote(state)}' "
        "-Status @{ status='failed'; detail='TOKEN=hidden' }"
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads((state / "status.json").read_text(encoding="utf-8-sig"))
    assert "hidden" not in json.dumps(payload)
```

- [ ] **Step 2: Run the focused tests and verify the module is missing**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/deploy/test_windows_operations.py -q --basetemp=.runtime/pytest-ops-common-red
```

Expected: three failures because `deploy/windows/Operations.Common.psm1` does not exist.

- [ ] **Step 3: Implement the common module with strict, exported interfaces**

The module must begin with `Set-StrictMode -Version Latest` and implement these exact rules:

```powershell
function Protect-LogText {
    [CmdletBinding()]
    param([AllowEmptyString()][string]$Text)
    $clean = $Text -replace '(?i)(KEY|TOKEN|PASSWORD|SECRET)=([^\s;]+)', '$1=[REDACTED]'
    $clean = $clean -replace '(?i)(https?://)[^/@\s]+:[^/@\s]+@', '$1[REDACTED]@'
    return $clean
}

function Assert-SafePath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$AllowedRoot,
        [switch]$AllowRoot
    )
    $candidate = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    $root = [IO.Path]::GetFullPath($AllowedRoot).TrimEnd('\', '/')
    if ($candidate -eq $root) {
        if ($AllowRoot) { return $candidate }
        throw "Path must be below allowed root: $candidate"
    }
    $prefix = $root + [IO.Path]::DirectorySeparatorChar
    if (-not $candidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path is outside allowed root: $candidate"
    }
    return $candidate
}

function Invoke-External {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [int[]]$AllowExitCodes = @(0)
    )
    $output = @(& $FilePath @ArgumentList 2>&1 | ForEach-Object { Protect-LogText ([string]$_) })
    $exitCode = $LASTEXITCODE
    if ($AllowExitCodes -notcontains $exitCode) {
        throw "$FilePath failed with exit code $exitCode`: $($output -join [Environment]::NewLine)"
    }
    return $output
}
```

`Get-OperationsConfig` must resolve the repository root, require `compose.yaml` and the env file, default state to `<repo>\deploy-state`, default backup root to `D:\python_self_agent_backups`, and reject state/data/backup overlaps. `Write-OperationsLog` must rotate a 10 MiB log to `.1` through `.7`. `Write-OperationsStatus` must redact each string value before serializing UTF-8 JSON. `Send-OperationsNotification` must store per-category timestamps under the state root and enforce a 30-minute cooldown before invoking the Windows toast API. `Test-DockerReady` must run `docker info`; `Test-ComposeHealth` must inspect only `app` and `qdrant` and return an object containing `Healthy`, `Services`, and `Reason`.

Export only the functions listed in the Interfaces block plus `Read-DeployEnvValue`, `Get-FreeTcpPort`, and `Wait-Until`.

- [ ] **Step 4: Extend the tests with module export and log rotation contracts**

Add tests that import the module, enumerate `Get-Command -Module Operations.Common`, assert the exact public function set, write an 11 MiB log followed by one line, and assert `operations.log.1` exists while `.8` does not.

- [ ] **Step 5: Ignore local operations state and run tests**

Append exactly these entries to `.gitignore`:

```gitignore
deploy-state/
.operations-test/
```

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/deploy/test_windows_operations.py -q --basetemp=.runtime/pytest-ops-common-green
git diff --check
```

Expected: all common-module tests pass; `git diff --check` exits 0.

- [ ] **Step 6: Commit the foundation**

```powershell
git add .gitignore deploy/windows/Operations.Common.psm1 tests/deploy/test_windows_operations.py
git commit -m "feat: add Windows operations foundation"
```

---

### Task 2: Add bounded startup and health recovery

**Files:**
- Create: `deploy/windows/Start-Deployment.ps1`
- Create: `deploy/windows/Test-DeploymentHealth.ps1`
- Modify: `tests/deploy/test_windows_operations.py`
- Modify: `compose.yaml`

**Interfaces:**
- Consumes the Task 1 module.
- `Start-Deployment.ps1` accepts `-RepositoryRoot`, `-EnvFile`, `-StateRoot`, and `-TimeoutSeconds` (default 180).
- `Test-DeploymentHealth.ps1` accepts the same paths and `-AttemptRecovery` (default true).

- [ ] **Step 1: Write failing contracts for bounded retries and one recovery attempt**

Add tests that assert:

```python
START = ROOT / "deploy" / "windows" / "Start-Deployment.ps1"
HEALTH = ROOT / "deploy" / "windows" / "Test-DeploymentHealth.ps1"


def test_startup_is_bounded_and_runs_default_smoke():
    source = START.read_text(encoding="utf-8")
    assert "TimeoutSeconds = 180" in source
    assert "Wait-Until" in source
    assert "deploy/smoke_test.py" in source.replace("\\", "/")
    assert "--deep" not in source


def test_health_attempts_at_most_one_compose_recovery():
    source = HEALTH.read_text(encoding="utf-8")
    assert source.count("up', '-d") == 1
    assert "AttemptRecovery" in source
    assert "Send-OperationsNotification" in source
```

Also assert every Compose service contains `logging:`, `driver: local`, `max-size: 10m`, and `max-file: 5`.

- [ ] **Step 2: Verify the contracts fail**

Run the two new test functions and expect missing-file failures.

- [ ] **Step 3: Implement startup with one bounded state machine**

`Start-Deployment.ps1` must:

```powershell
#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$EnvFile = 'deploy\.env',
    [string]$StateRoot = 'deploy-state',
    [ValidateRange(30, 600)][int]$TimeoutSeconds = 180
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Operations.Common.psm1') -Force
$config = Get-OperationsConfig -RepositoryRoot $RepositoryRoot -EnvFile $EnvFile -StateRoot $StateRoot
try {
    if (-not (Wait-Until -TimeoutSeconds $TimeoutSeconds -IntervalSeconds 5 -Condition { Test-DockerReady })) {
        throw "Docker Engine was not ready within $TimeoutSeconds seconds"
    }
    Invoke-External docker @('compose', '--env-file', $config.EnvFile, 'up', '-d') | Out-Null
    if (-not (Wait-Until -TimeoutSeconds $TimeoutSeconds -IntervalSeconds 5 -Condition {
        (Test-ComposeHealth -Config $config).Healthy
    })) { throw 'Compose services did not become healthy' }
    Invoke-External $config.Python @((Join-Path $config.RepositoryRoot 'deploy\smoke_test.py'), '--env-file', $config.EnvFile) | Out-Null
    Write-OperationsStatus $config.StateRoot @{ status='healthy'; category='startup'; checked_at=(Get-Date).ToUniversalTime().ToString('o') }
} catch {
    Write-OperationsLog $config.StateRoot 'startup' $_.Exception.Message 'ERROR'
    Write-OperationsStatus $config.StateRoot @{ status='failed'; category='startup'; detail=$_.Exception.Message }
    Send-OperationsNotification $config.StateRoot 'startup' '部署启动失败' $_.Exception.Message
    throw
}
```

- [ ] **Step 4: Implement health with exactly one recovery**

The script performs the three checks in order: Docker, Compose, HTTP. When any check fails and `-AttemptRecovery` is true, it runs one `docker compose ... up -d`, waits up to 90 seconds, and reruns all checks once. It writes `status.json`; on failure it notifies category `health`, and after a previous failure becomes healthy it notifies category `health-recovered`.

- [ ] **Step 5: Add bounded Compose logging**

Add this block to `app`, `qdrant`, and `neo4j`:

```yaml
    logging:
      driver: local
      options:
        max-size: "10m"
        max-file: "5"
```

- [ ] **Step 6: Run focused tests and Compose validation**

```powershell
.\venv\Scripts\python.exe -m pytest tests/deploy/test_windows_operations.py tests/deploy/test_compose_contract.py -q --basetemp=.runtime/pytest-ops-health
docker compose --env-file deploy/.env config --quiet
```

Expected: tests and Compose validation pass.

- [ ] **Step 7: Commit startup and monitoring**

```powershell
git add compose.yaml deploy/windows/Start-Deployment.ps1 deploy/windows/Test-DeploymentHealth.ps1 tests/deploy/test_windows_operations.py
git commit -m "feat: add Windows deployment recovery"
```

---

### Task 3: Add idempotent installation, firewall, ACL, and uninstall

**Files:**
- Create: `deploy/windows/Install-Operations.ps1`
- Create: `deploy/windows/Uninstall-Operations.ps1`
- Modify: `tests/deploy/test_windows_operations.py`

**Interfaces:**
- Scheduled task prefix: `PythonSelfAgent`.
- Firewall display name: `Python Self Agent - Private Intranet 7860`.
- Four task names: `PythonSelfAgent-LoginRecovery`, `PythonSelfAgent-Health`, `PythonSelfAgent-DailyBackup`, `PythonSelfAgent-MonthlyRestoreDrill`.

- [ ] **Step 1: Write failing static contracts**

Tests must assert the installer contains all four exact task names, `Private`, `LocalSubnet`, TCP 7860, `MultipleInstances IgnoreNew`, and a Public-network rejection before `New-NetFirewallRule`. Tests must assert the uninstaller removes only exact names and contains no data, backup, image, or recursive deletion command.

- [ ] **Step 2: Verify missing-file failures**

Run the installer/uninstaller contract tests and expect failure because both scripts are absent.

- [ ] **Step 3: Implement installer preflight and ACL**

The installer starts with:

```powershell
#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$EnvFile = 'deploy\.env',
    [string]$StateRoot = 'deploy-state',
    [string]$BackupRoot = 'D:\python_self_agent_backups'
)
```

Before any mutation it resolves all paths, confirms Docker/Compose/Scout/tar commands, requires every active Internet-connected profile to be `Private`, confirms TCP 7860 is either free or owned by the current Compose app mapping, and validates the env file without printing values.

Use `icacls.exe <env> /inheritance:r`, then grant `"$env:USERDOMAIN\$env:USERNAME:(M)"`, `SYSTEM:(F)`, and `Administrators:(F)`. Check every exit code.

- [ ] **Step 4: Implement firewall and four tasks idempotently**

Remove and recreate only the exact firewall rule. Register the rule with:

```powershell
New-NetFirewallRule -DisplayName $firewallName -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort 7860 -Profile Private -RemoteAddress LocalSubnet
```

Create task actions using `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File <absolute-script> ...`. Use an interactive current-user principal, highest run level, `StartWhenAvailable = $true`, `WakeToRun = $true` for backup/drill, and `MultipleInstances = IgnoreNew`. Use logon, five-minute repetition, daily 03:00, and first-Sunday 04:00 triggers. Register with `-Force` so reruns update instead of duplicate.

- [ ] **Step 5: Implement narrow uninstall**

Uninstall removes the four exact tasks and one exact firewall rule. It prints, but never deletes, the env file, `deploy-data`, `D:\python_self_agent_backups`, `deploy-state`, containers, and images.

- [ ] **Step 6: Run contracts and PowerShell parser validation**

```powershell
.\venv\Scripts\python.exe -m pytest tests/deploy/test_windows_operations.py -q --basetemp=.runtime/pytest-ops-install
$files = Get-ChildItem deploy/windows/*.ps1
foreach ($file in $files) { [void][scriptblock]::Create((Get-Content -Raw $file.FullName)) }
```

Expected: tests pass and every script parses under Windows PowerShell 5.1.

- [ ] **Step 7: Commit system configuration scripts**

```powershell
git add deploy/windows/Install-Operations.ps1 deploy/windows/Uninstall-Operations.ps1 tests/deploy/test_windows_operations.py
git commit -m "feat: install Windows deployment operations"
```

---

### Task 4: Add Windows backup, retention, and rollback-preserving restore

**Files:**
- Create: `deploy/windows/Backup.Common.psm1`
- Create: `deploy/windows/Backup-Deployment.ps1`
- Create: `deploy/windows/Restore-Deployment.ps1`
- Create: `tests/deploy/test_windows_backup.py`

**Interfaces:**
- Backup output: `daily/assistant-<UTC>.tar.gz`, `.sha256`, `.meta`.
- Weekly output: `weekly/assistant-week-<YYYY>-W<ww>.tar.gz` and matching sidecars.
- Restore output: data swap plus `<data-root>.rollback-<UTC>` retained after success.
- `Get-RetentionPlan -Directory -Prefix -Keep -> PSCustomObject` with `Keep` and `Remove` arrays.

- [ ] **Step 1: Write failing pure retention and archive-contract tests**

Create 10 synthetic daily sets and 6 weekly sets in a temporary directory. Invoke `Get-RetentionPlan` through PowerShell and assert 7 and 4 complete sets remain. Add static tests requiring checksum validation, archive member validation, service `finally` restart, rollback rename, and absence of `Remove-Item -Recurse` from the restore script.

- [ ] **Step 2: Verify failures before implementation**

Run `tests/deploy/test_windows_backup.py`; expect module/script missing failures.

- [ ] **Step 3: Implement backup-set discovery and safe retention**

`Backup.Common.psm1` must group only exact filenames matching:

```powershell
$DailyPattern = '^assistant-(?<stamp>\d{8}T\d{6}Z)\.tar\.gz$'
$WeeklyPattern = '^assistant-week-(?<year>\d{4})-W(?<week>\d{2})\.tar\.gz$'
```

A set is eligible only when archive, `.sha256`, and `.meta` all exist as regular files and none has the `ReparsePoint` attribute. `Remove-BackupSet` calls `Assert-SafePath` on every sidecar immediately before `Remove-Item -LiteralPath` and never performs recursive deletion.

- [ ] **Step 4: Implement cold backup with unconditional service restart**

`Backup-Deployment.ps1` resolves `DEPLOY_DATA_ROOT` from the env file, rejects unsafe/overlapping roots, records running services, stops only those services, runs:

```powershell
Invoke-External tar.exe @('-C', $dataRoot, '-czf', $archive, '.') | Out-Null
$hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$archive.sha256" -Encoding ASCII -Value "$hash  $([IO.Path]::GetFileName($archive))"
```

It writes safe metadata, restarts the recorded services in `finally`, verifies health, then applies daily retention. It creates the weekly set when the ISO week has no complete set, and applies weekly retention. A failed archive/checksum/service restart exits nonzero and skips deletion.

- [ ] **Step 5: Implement validated restore with rollback preservation**

`Restore-Deployment.ps1` must require the matching checksum, compare it with `Get-FileHash` using ordinal-insensitive equality, list members with `tar -tzf`, reject absolute paths, drive-qualified paths, `..` segments, and link entries from `tar -tvzf`. Extract into a sibling staging directory, require `app` and `qdrant`, stop running services, rename current data to `.rollback-<UTC>`, rename staging into place, restart and wait healthy. On post-swap failure, move the failed candidate aside, rename rollback back, restart, and retain both failed/diagnostic paths.

- [ ] **Step 6: Run unit tests and a fake-data round trip**

Use a temporary data root with `app/marker.txt` and `qdrant/collections/marker.txt`, configure command injection so Docker calls are recorded but not executed, run backup, mutate the marker, restore, and assert the original marker returns and a rollback directory remains.

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/deploy/test_windows_backup.py tests/deploy/test_backup_restore_contract.py -q --basetemp=.runtime/pytest-ops-backup
```

- [ ] **Step 7: Commit data safety tooling**

```powershell
git add deploy/windows/Backup.Common.psm1 deploy/windows/Backup-Deployment.ps1 deploy/windows/Restore-Deployment.ps1 tests/deploy/test_windows_backup.py
git commit -m "feat: add Windows deployment backups"
```

---

### Task 5: Add isolated monthly restore rehearsal

**Files:**
- Create: `deploy/windows/Invoke-RestoreDrill.ps1`
- Modify: `deploy/smoke_test.py`
- Modify: `tests/deploy/test_smoke_test.py`
- Modify: `tests/deploy/test_windows_backup.py`

**Interfaces:**
- `deploy/smoke_test.py` adds optional `--project-name`; all Compose invocations include `--project-name <value>` when supplied.
- Drill report: `deploy-state/reports/restore-drill-<UTC>.json`.

- [ ] **Step 1: Write failing smoke project-name tests**

Add:

```python
def test_compose_command_accepts_isolated_project_name():
    command = smoke_test._compose_command(Path("drill.env"), "assistant-drill-123")
    assert command[:4] == ["docker", "compose", "--project-name", "assistant-drill-123"]
    assert command[-2:] == ["--env-file", "drill.env"]
```

Update `_deep_command` tests to require the same project-name propagation.

- [ ] **Step 2: Verify the focused test fails on the old signature**

Run the new test and expect `TypeError` for the extra argument.

- [ ] **Step 3: Implement project-name propagation surgically**

Add `project_name: str | None = None` to `_compose_command`, `_deep_command`, and the host smoke path. Parse `--project-name`; validate with `^[a-z0-9][a-z0-9_-]{0,62}$`; insert it before `--env-file`. Keep behavior byte-for-byte equivalent when omitted.

- [ ] **Step 4: Implement the isolated drill**

The drill selects the latest complete daily backup, validates checksum/members through `Backup.Common`, extracts under `D:\python_self_agent_backups\.drills\<uuid>`, chooses a free loopback port, and creates an ACL-restricted temporary env file with these overrides:

```dotenv
APP_BIND_ADDRESS=127.0.0.1
APP_PORT=<free-port>
DEPLOY_DATA_ROOT=<forward-slash-absolute-drill-data-root>
DEPLOY_ENV_FILE=<forward-slash-absolute-temporary-env-file>
```

It starts `docker compose --project-name assistant-drill-<short-id> --env-file <temp> up -d`, waits healthy, runs the default smoke with `--project-name`, and always runs `docker compose ... down` without `--volumes`. It always deletes the temporary env file. On success it removes only the validated drill directory; on failure it retains extracted data and a redacted report.

- [ ] **Step 5: Test isolation and cleanup with fake external commands**

Assert the generated project name is not the production project, bind address is loopback, port differs from 7860, production data path is absent from mutation targets, `down --volumes` is absent, temporary env is deleted on success and failure, and failure report contains no configured key value.

- [ ] **Step 6: Run focused suites and commit**

```powershell
.\venv\Scripts\python.exe -m pytest tests/deploy/test_smoke_test.py tests/deploy/test_windows_backup.py -q --basetemp=.runtime/pytest-ops-drill
git add deploy/smoke_test.py deploy/windows/Invoke-RestoreDrill.ps1 tests/deploy/test_smoke_test.py tests/deploy/test_windows_backup.py
git commit -m "feat: rehearse Windows deployment restores"
```

---

### Task 6: Pin images and add scanned upgrade with rollback

**Files:**
- Modify: `Dockerfile`
- Modify: `deploy/qdrant.Dockerfile`
- Modify: `compose.yaml`
- Create: `deploy/windows/Update-Deployment.ps1`
- Create: `tests/deploy/test_windows_release.py`

**Interfaces:**
- Stable images: `python_self_agent-app:local` and `python_self_agent-qdrant:local`.
- Rollback tags: `python_self_agent-app:rollback-<UTC>` and `python_self_agent-qdrant:rollback-<UTC>`.
- Pinned base manifests:
  - `python:3.11-slim-bookworm@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3`
  - `qdrant/qdrant:v1.18.2@sha256:75eab8c4ba42096724fdcfde8b4de0b5713d529dde32f285a1f86fdcb2c9e50c`
  - `neo4j:5.26.28-community@sha256:ff32db30b2baff97971e441b46bfd9c832c1b62c970398ef579244c06b21d357`

- [ ] **Step 1: Write failing pinning and release-flow tests**

Tests assert every base image contains both version and `@sha256`, Compose has explicit stable `image:` names, and the update script orders tokens as: test → config → backup → rollback tag → build → scout → up → default smoke → deep smoke. Assert failure handling retags both images, invokes restore only after candidate start, and never performs Git writes.

- [ ] **Step 2: Verify tests fail against floating tags and missing script**

Run `tests/deploy/test_windows_release.py`; expect image pin and missing-script failures.

- [ ] **Step 3: Pin base images and stable Compose output names**

Use the three exact manifest references above. Add:

```yaml
  app:
    image: python_self_agent-app:local
  qdrant:
    image: python_self_agent-qdrant:local
```

Keep `build:` for both services. Pin the optional Neo4j Compose image to its digest.

- [ ] **Step 4: Implement upgrade preflight and rollback stages**

`Update-Deployment.ps1` must:

1. Require a healthy baseline and at least 10 GiB free on the backup drive.
2. Record `git status --short` but perform no Git writes.
3. Run `tests/deploy`, `docker compose config --quiet`, and `Backup-Deployment.ps1`.
4. Resolve current stable image IDs and tag them with the UTC rollback suffix.
5. Build the two stable images.
6. Run `docker scout cves --only-severity critical --exit-code --only-fixed` for both stable images; missing Scout or nonzero scan blocks rollout. Do not exclude base-image findings.
7. Run Compose up, wait healthy, then default and deep smoke.
8. On success, write a redacted JSON report and retain backup/rollback tags.
9. On failure before candidate `up`, retag old images only. On failure after candidate `up`, stop services, retag old images, invoke `Restore-Deployment.ps1` with the verified pre-upgrade archive, recreate without build, and verify default smoke.

The script records its current stage before each mutation so the catch block performs only valid compensations.

- [ ] **Step 5: Test success and injected failures without touching Docker**

Expose `-CommandRunner` and `-SkipNotification` only for tests. Feed deterministic fake outputs for image IDs and inject failures at `scan`, `candidate-up`, and `deep-smoke`. Assert exact rollback calls and that no restore occurs for a scan failure.

- [ ] **Step 6: Run release, Compose, and image contract tests**

```powershell
.\venv\Scripts\python.exe -m pytest tests/deploy/test_windows_release.py tests/deploy/test_compose_contract.py tests/deploy/test_image_contract.py -q --basetemp=.runtime/pytest-ops-release
docker compose --env-file deploy/.env config --quiet
```

- [ ] **Step 7: Commit release safety**

```powershell
git add Dockerfile compose.yaml deploy/qdrant.Dockerfile deploy/windows/Update-Deployment.ps1 tests/deploy/test_windows_release.py
git commit -m "feat: add scanned deployment upgrades"
```

---

### Task 7: Add deployment CI and operator documentation

**Files:**
- Create: `.github/workflows/deployment.yml`
- Create: `deploy/windows/README.md`
- Modify: `deploy/.env.example`
- Modify: `deploy/README.md`
- Modify: `README.md`
- Modify: `tests/deploy/test_readme_deployment_section.py`
- Modify: `tests/deploy/test_windows_release.py`

**Interfaces:**
- CI Trivy image: `aquasec/trivy:0.72.0@sha256:cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f`.
- Checkout action: `actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd`.

- [ ] **Step 1: Write failing documentation and workflow contracts**

Assert the workflow uses the two exact immutable references above, has `permissions: contents: read`, runs deployment tests and Compose config, builds both images, scans both with `--ignore-unfixed --severity CRITICAL --exit-code 1`, and contains no secret names. Assert the READMEs document Private/LocalSubnet, four task names, 03:00, 7 daily, 4 weekly, first Sunday 04:00, backup location, installation, uninstall, upgrade, logs, and Public-network refusal.

- [ ] **Step 2: Verify failures before files/documentation exist**

Run the two contract files and expect missing workflow/README assertions.

- [ ] **Step 3: Create the pinned CI workflow**

The job runs on `ubuntu-24.04`, checks out by full SHA, copies `.env.example`, runs the deployment pytest suite, validates Compose, builds `python_self_agent-app:local` and `python_self_agent-qdrant:local`, then runs the pinned Trivy container against each local image with the Docker socket mounted read-only where supported and a repository-local cache directory.

Do not run the app, deep smoke, or use LLM secrets in CI.

- [ ] **Step 4: Document exact Windows operator commands**

Document:

```powershell
Start-Process powershell.exe -Verb RunAs -ArgumentList @(
  '-NoProfile', '-ExecutionPolicy', 'Bypass',
  '-File', 'D:\python_self_agent\deploy\windows\Install-Operations.ps1'
)
```

Also document manual task starts with `Start-ScheduledTask`, backup/restore/drill/upgrade commands, state and report paths, exact firewall inspection, task inspection, rollback artifacts, and uninstall non-deletion behavior.

- [ ] **Step 5: Add configurable operations defaults without secrets**

Append to `deploy/.env.example`:

```dotenv
DEPLOY_STATE_ROOT=./deploy-state
DEPLOY_BACKUP_ROOT=D:/python_self_agent_backups
OPERATIONS_NOTIFY_COOLDOWN_MINUTES=30
```

The scripts continue to accept explicit parameters that override these values.

- [ ] **Step 6: Run docs/workflow contracts and full deploy suite**

```powershell
.\venv\Scripts\python.exe -m pytest tests/deploy -q --basetemp=.runtime/pytest-ops-docs
git diff --check
```

- [ ] **Step 7: Commit documentation and CI**

```powershell
git add .github/workflows/deployment.yml deploy/.env.example deploy/README.md deploy/windows/README.md README.md tests/deploy/test_readme_deployment_section.py tests/deploy/test_windows_release.py
git commit -m "ci: verify hardened Docker deployment"
```

---

### Task 8: Validate, install, and exercise the complete operations package

**Files:**
- Modify only if test-discovered defects require surgical fixes in files from Tasks 1–7.
- Runtime writes outside Git: `D:\python_self_agent_backups`, Windows Task Scheduler, Windows Firewall, ACLs, and ignored `deploy-state`.

**Interfaces:**
- No new code interface; this task proves the approved completion criteria.

- [ ] **Step 1: Run repository verification before system writes**

```powershell
$env:TEMP = (Resolve-Path '.runtime').Path
$env:TMP = $env:TEMP
.\venv\Scripts\python.exe -m pytest tests/deploy -q --basetemp=.runtime/pytest-ops-deploy
.\venv\Scripts\python.exe -m pytest -q --basetemp=.runtime/pytest-ops-full
docker compose --env-file deploy/.env config --quiet
docker compose --env-file deploy/.env build app qdrant
python deploy/smoke_test.py --env-file deploy/.env
python deploy/smoke_test.py --env-file deploy/.env --deep
```

Expected: deployment tests and full regression pass; Compose validates/builds; default and deep smoke pass. Record any demonstrably unrelated pre-existing failure rather than masking it.

- [ ] **Step 2: Scan the actual local images before installation**

```powershell
docker scout cves python_self_agent-app:local --only-severity critical --only-fixed --exit-code
docker scout cves python_self_agent-qdrant:local --only-severity critical --only-fixed --exit-code
```

Expected: both commands exit 0. If a fixed Critical vulnerability exists, update only the responsible pinned base or dependency, rebuild, rerun tests, and record the exact remediation in the relevant commit.

- [ ] **Step 3: Resolve the real network-category gate**

Inspect `Get-NetConnectionProfile`. Because the current `TP-LINK_AEC0 2` profile is Public, stop and obtain the user's visible confirmation immediately before changing that trusted profile to Private. Perform the change only in an elevated PowerShell session, then re-read the exact profile and require `NetworkCategory = Private`.

- [ ] **Step 4: Install idempotently as administrator**

Run `Install-Operations.ps1` elevated twice. Verify the second run succeeds and there is exactly one of each task and one firewall rule. Inspect task principals/triggers/settings and firewall fields rather than trusting installer output.

- [ ] **Step 5: Exercise startup, health, notification, and backup**

Manually start login recovery and health tasks and wait for completion. Require `app`/`qdrant` healthy and default smoke passing within 180 seconds. Trigger a test notification. Run the backup task and require one complete set under `D:\python_self_agent_backups\daily`, valid SHA-256, safe metadata, restarted healthy services, and a successful status/report entry.

- [ ] **Step 6: Exercise retention and isolated restore drill**

Use a dedicated test backup root beneath `D:\python_self_agent_backups\.acceptance` with synthetic complete sets to prove 7/4 retention without touching real backups. Run the restore drill against the real latest backup, confirm its project name and port differ from production, default smoke passes, production data timestamps/hashes are unchanged, temporary env is absent, and the report is successful.

- [ ] **Step 7: Exercise upgrade success and rollback failure branch**

Run a normal `Update-Deployment.ps1` and require backup, tests, scans, default/deep smoke, and success report. Then invoke its documented test-only failure injection immediately after candidate startup; require old image IDs restored, pre-upgrade data restored, default smoke passing, and rollback artifacts retained.

- [ ] **Step 8: Verify intranet exposure and port isolation**

Inspect Docker mappings and local listeners. Confirm only TCP 7860 is published, the firewall rule is Private/LocalSubnet only, and 6333/6334/7474/7687 have no host mapping. Obtain the machine's current private IPv4 address and test port 7860 locally; ask the user to confirm access from one other trusted LAN device because the local machine cannot prove an external firewall path by itself.

- [ ] **Step 9: Verify repository hygiene and final state**

```powershell
git diff --check
git status --short
git log -10 --oneline
docker compose --env-file deploy/.env ps
```

Confirm `deploy/.env`, `deploy-state`, `deploy-data`, backups, temporary drill data, and unrelated GraphRAG changes are not staged or committed. Confirm all intended commits exist and services remain healthy.

- [ ] **Step 10: Complete the real reboot acceptance**

Request a user-controlled Windows restart. After the user logs back in, measure from task history and logs that login recovery completed within 180 seconds; rerun default smoke and inspect task/firewall/backup state. Do not restart Windows automatically.

---

## Plan self-review

### Spec coverage

| Requirement | Task |
|---|---|
| Login recovery within 180 seconds | 2, 3, 8 |
| Five-minute health and one recovery attempt | 2, 3, 8 |
| Private/LocalSubnet TCP 7860 only | 3, 8 |
| Env ACL and secret redaction | 1, 3, 8 |
| Daily cold backup and 7/4 retention | 4, 8 |
| Monthly isolated restore drill | 5, 8 |
| 10 MiB/7-file operations logs and bounded Docker logs | 1, 2 |
| Desktop notification with 30-minute cooldown | 1, 2, 8 |
| Digest-pinned images and Critical scan gate | 6, 7, 8 |
| Manual one-click upgrade and data/image rollback | 4, 6, 8 |
| Deployment CI | 7 |
| Idempotent install and narrow uninstall | 3, 8 |
| Preserve single replica and business contracts | Global constraints, 8 |
| Real reboot and LAN acceptance | 8 |

### Consistency checks

- All scripts target Windows PowerShell 5.1 and import only the two named modules.
- Every destructive file operation is limited to an exact validated child path; formal data is swapped by rename and preserved.
- Task names, firewall name, paths, ports, schedules, retention counts, timeouts, image names, digests, and scan thresholds are identical across tasks.
- The restore drill and deep smoke share an explicit Compose project-name interface and do not target production.
- CI uses a full checkout SHA and a digest-pinned Trivy image, avoiding mutable action/scanner tags.
- System changes occur only after repository tests and explicit network/admin gates.
- No task authorizes touching the existing GraphRAG task-packet modifications or committing secrets/runtime data.

**Plan complete and saved to `docs/superpowers/plans/2026-08-09-windows-single-node-operations.md`.**
