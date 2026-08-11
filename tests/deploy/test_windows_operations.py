from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import time

import pytest


ROOT = Path(__file__).parents[2]
MODULE = ROOT / "deploy" / "windows" / "Operations.Common.psm1"
START = ROOT / "deploy" / "windows" / "Start-Deployment.ps1"
HEALTH = ROOT / "deploy" / "windows" / "Test-DeploymentHealth.ps1"
INSTALL = ROOT / "deploy" / "windows" / "Install-Operations.ps1"
UNINSTALL = ROOT / "deploy" / "windows" / "Uninstall-Operations.ps1"


def run_ps(
    script: str, *, timeout: float = 60, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        cwd=ROOT,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=timeout,
        env=env,
    )


def ps_quote(path: Path) -> str:
    return str(path).replace("'", "''")


def import_module() -> str:
    return f"Import-Module '{ps_quote(MODULE)}' -Force; "


@pytest.fixture
def trusted_fallback_state():
    state = ROOT / "deploy-state"
    if state.exists():
        pytest.skip("trusted fallback state exists and must not be disturbed")
    try:
        yield state
    finally:
        shutil.rmtree(state, ignore_errors=True)


def test_common_module_redacts_named_secrets_and_url_credentials():
    result = run_ps(
        import_module()
        + "Protect-LogText 'LLM_API_KEY=secret https://user:pass@example.test/v1'"
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
        import_module()
        + f"Assert-SafePath -Path '{ps_quote(sibling)}' -AllowedRoot '{ps_quote(root)}'"
    )

    assert result.returncode != 0
    assert "outside allowed root" in result.stderr


def test_status_json_contains_no_secret_values(tmp_path: Path):
    state = tmp_path / "state"
    result = run_ps(
        import_module()
        + f"Write-OperationsStatus -StateRoot '{ps_quote(state)}' "
        + "-Status @{ status='failed'; detail='TOKEN=hidden' }"
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads((state / "status.json").read_text(encoding="utf-8-sig"))
    assert "hidden" not in json.dumps(payload)
    assert "[REDACTED]" in json.dumps(payload)


def test_notification_types_resolve_in_clean_windows_powershell():
    source = MODULE.read_text(encoding="utf-8")
    manager_type = (
        "[Windows.UI.Notifications.ToastNotificationManager, "
        "Windows.UI.Notifications, ContentType=WindowsRuntime]"
    )
    toast_type = (
        "[Windows.UI.Notifications.ToastNotification, "
        "Windows.UI.Notifications, ContentType=WindowsRuntime]"
    )
    assert manager_type in source
    assert toast_type in source

    result = run_ps(f"[void]{manager_type}; [void]{toast_type}; 'resolved'")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "resolved"


def test_notification_failure_is_best_effort_and_does_not_start_cooldown(
    tmp_path: Path,
):
    state = tmp_path / "state"
    result = run_ps(
        import_module()
        + f"$state='{ps_quote(state)}'; "
        + "$failed = Send-OperationsNotification -StateRoot $state "
        + "-Category 'failure' -Title 'title' -Message 'message' "
        + "-NotificationAction { param($title, $message) throw 'unavailable' }; "
        + "if ($failed) { exit 11 }; "
        + "if (Test-Path (Join-Path $state 'notifications\\failure.json')) { exit 12 }; "
        + "$sent = Send-OperationsNotification -StateRoot $state "
        + "-Category 'success' -Title 'title' -Message 'message' "
        + "-NotificationAction { param($title, $message) }; "
        + "if (-not $sent) { exit 13 }; "
        + "if (-not (Test-Path (Join-Path $state 'notifications\\success.json'))) "
        + "{ exit 14 }"
    )

    assert result.returncode == 0, result.stderr


def test_common_module_exports_exact_public_command_set():
    result = run_ps(
        import_module()
        + "(Get-Command -Module Operations.Common -CommandType Function | "
        + "Select-Object -ExpandProperty Name | Sort-Object) -join ','"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().split(",") == [
        "Assert-SafePath",
        "Get-FreeTcpPort",
        "Get-OperationsConfig",
        "Invoke-External",
        "Protect-LogText",
        "Read-DeployEnvValue",
        "Send-OperationsNotification",
        "Test-ComposeHealth",
        "Test-DockerReady",
        "Wait-Until",
        "Write-OperationsLog",
        "Write-OperationsStatus",
    ]


def test_operations_log_rotates_at_ten_mebibytes_and_discards_eighth_archive(
    tmp_path: Path,
):
    state = tmp_path / "state"
    result = run_ps(
        import_module()
        + f"$state='{ps_quote(state)}'; "
        + "Write-OperationsLog -StateRoot $state -Category 'test' "
        + "-Message ('x' * (11MB)) -Level 'INFO'; "
        + "Write-OperationsLog -StateRoot $state -Category 'test' "
        + "-Message 'next' -Level 'INFO'; "
        + "if (-not (Test-Path (Join-Path $state 'operations.log.1'))) { exit 11 }; "
        + "if (Test-Path (Join-Path $state 'operations.log.8')) { exit 12 }"
    )

    assert result.returncode == 0, result.stderr
    assert (state / "operations.log.1").exists()
    assert not (state / "operations.log.8").exists()


def test_configuration_rejects_missing_inputs_and_overlapping_roots_without_env_values(
    tmp_path: Path,
):
    missing_repo = tmp_path / "missing-compose"
    missing_repo.mkdir()
    secret_env = missing_repo / "deploy.env"
    secret_env.write_text("LLM_API_KEY=must-not-leak\n", encoding="utf-8")
    missing = run_ps(
        import_module()
        + f"Get-OperationsConfig -RepositoryRoot '{ps_quote(missing_repo)}' "
        + f"-EnvFile '{ps_quote(secret_env)}'"
    )

    assert missing.returncode != 0
    assert "must-not-leak" not in missing.stdout + missing.stderr

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    env_file = repo / "deploy.env"
    env_file.write_text("DEPLOY_DATA_ROOT=data\n", encoding="utf-8")
    overlap = run_ps(
        import_module()
        + f"Get-OperationsConfig -RepositoryRoot '{ps_quote(repo)}' "
        + f"-EnvFile '{ps_quote(env_file)}' -StateRoot '{ps_quote(repo / 'data')}' "
        + f"-BackupRoot '{ps_quote(repo / 'backups')}'"
    )

    assert overlap.returncode != 0
    assert "overlap" in (overlap.stdout + overlap.stderr).lower()


def test_configuration_rejects_missing_env_when_compose_exists(tmp_path: Path):
    repo = tmp_path / "missing-env"
    repo.mkdir()
    (repo / "compose.yaml").write_text("services: {}\n", encoding="utf-8")

    result = run_ps(
        import_module()
        + f"Get-OperationsConfig -RepositoryRoot '{ps_quote(repo)}' "
        + f"-EnvFile '{ps_quote(repo / 'absent.env')}'"
    )

    assert result.returncode != 0
    assert "environment file was not found" in result.stderr.lower()


def test_compose_health_uses_config_context_and_only_required_services(
    tmp_path: Path,
):
    args_file = tmp_path / "docker-args.json"
    repository = tmp_path / "repository"
    repository.mkdir()
    compose_file = repository / "compose.yaml"
    env_file = repository / "deploy.env"
    result = run_ps(
        "function global:docker { "
        + f"$args | ConvertTo-Json | Set-Content '{ps_quote(args_file)}'; "
        + "$global:LASTEXITCODE = 0; "
        + "'app|running|healthy'; 'qdrant|running|healthy' }; "
        + import_module()
        + "$config = [PSCustomObject]@{ "
        + f"RepositoryRoot='{ps_quote(repository)}'; "
        + f"ComposeFile='{ps_quote(compose_file)}'; "
        + f"EnvFile='{ps_quote(env_file)}' }}; "
        + "$health = Test-ComposeHealth -Config $config; "
        + "if (-not $health.Healthy) { exit 15 }"
    )

    assert result.returncode == 0, result.stderr
    arguments = json.loads(args_file.read_text(encoding="utf-8-sig"))
    assert arguments == [
        "compose",
        "--project-directory",
        str(repository),
        "--file",
        str(compose_file),
        "--env-file",
        str(env_file),
        "ps",
        "--format",
        "{{.Service}}|{{.State}}|{{.Health}}",
        "app",
        "qdrant",
    ]


def test_wait_until_caps_sleep_to_remaining_timeout():
    result = run_ps(
        import_module()
        + "$watch = [Diagnostics.Stopwatch]::StartNew(); "
        + "$matched = Wait-Until -Condition { $false } "
        + "-TimeoutSeconds 1 -IntervalSeconds 300; "
        + "$watch.Stop(); "
        + "[PSCustomObject]@{ Matched=$matched; Elapsed=$watch.Elapsed.TotalSeconds } "
        + "| ConvertTo-Json -Compress",
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["Matched"] is False
    assert payload["Elapsed"] < 2.5


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


def test_health_runs_one_recovery_before_reporting_persistent_http_failure(
    tmp_path: Path,
):
    docker_calls = tmp_path / "docker-calls.jsonl"
    state = tmp_path / "state"
    result = run_ps(
        "function global:docker { "
        + f"$args | ConvertTo-Json -Compress | Add-Content '{ps_quote(docker_calls)}'; "
        + "$global:LASTEXITCODE = 0; "
        + "'app|running|healthy'; 'qdrant|running|healthy' }; "
        + "function global:Invoke-WebRequest { throw 'forced HTTP failure' }; "
        + "try { "
        + f"& '{ps_quote(HEALTH)}' -RepositoryRoot '{ps_quote(ROOT)}' "
        + "-EnvFile 'deploy/.env' "
        + f"-StateRoot '{ps_quote(state)}' -AttemptRecovery $true "
        + "} catch { }; "
        + "if (-not (Test-Path (Join-Path '"
        + ps_quote(state)
        + "' 'status.json'))) { exit 21 }"
    )

    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in docker_calls.read_text().splitlines()]
    assert sum(call[-2:] == ["up", "-d"] for call in calls) == 1
    status = json.loads((state / "status.json").read_text(encoding="utf-8-sig"))
    assert status["status"] == "failed"
    assert status["category"] == "health"


def test_every_compose_service_has_bounded_local_logging():
    source = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    for service in ("app", "qdrant", "neo4j"):
        match = re.search(
            rf"^  {service}:\n(?P<body>.*?)(?=^  \S|\Z)",
            source,
            flags=re.MULTILINE | re.DOTALL,
        )
        assert match is not None
        service_block = match.group("body")
        assert "logging:" in service_block
        assert "driver: local" in service_block
        assert 'max-size: "10m"' in service_block
        assert 'max-file: "5"' in service_block


def test_startup_uses_one_global_deadline_for_all_startup_work():
    source = START.read_text(encoding="utf-8")

    assert "Start-Job" in source
    assert "Wait-Job -Job $startupJob -Timeout $TimeoutSeconds" in source
    assert "Stop-Job -Job $startupJob" in source
    assert "Get-RemainingSeconds" in source
    assert "Wait-Until -TimeoutSeconds $TimeoutSeconds" not in source


def test_health_first_run_uses_loopback_and_preserves_configured_port(
    tmp_path: Path,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    env_file = repository / "deploy.env"
    env_file.write_text(
        "DEPLOY_DATA_ROOT=data\nAPP_BIND_ADDRESS=192.0.2.10\nAPP_PORT=9988\n",
        encoding="utf-8",
    )
    state = tmp_path / "state"
    uri_file = tmp_path / "http-uri.txt"
    result = run_ps(
        "function global:docker { "
        + "$global:LASTEXITCODE = 0; "
        + "'app|running|healthy'; 'qdrant|running|healthy' }; "
        + "function global:Invoke-WebRequest { param($Uri) "
        + f"$Uri | Set-Content '{ps_quote(uri_file)}'; "
        + "[PSCustomObject]@{ StatusCode = 200 } }; "
        + f"& '{ps_quote(HEALTH)}' -RepositoryRoot '{ps_quote(repository)}' "
        + f"-EnvFile '{ps_quote(env_file)}' -StateRoot '{ps_quote(state)}'"
    )

    assert result.returncode == 0, result.stderr
    assert uri_file.read_text(encoding="utf-8-sig").strip() == "http://127.0.0.1:9988/"
    status = json.loads((state / "status.json").read_text(encoding="utf-8-sig"))
    assert status["status"] == "healthy"
    assert status["category"] == "health"


def test_scripts_record_missing_env_failures_without_leaking_secret(
    tmp_path: Path, trusted_fallback_state: Path
):
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (repository / "present.env").write_text(
        "LLM_API_KEY=must-not-leak\n", encoding="utf-8"
    )

    for script in (START, HEALTH):
        state = tmp_path / script.stem
        result = run_ps(
            "try { "
            + f"& '{ps_quote(script)}' -RepositoryRoot '{ps_quote(repository)}' "
            + f"-EnvFile '{ps_quote(repository / 'missing.env')}' "
            + f"-StateRoot '{ps_quote(state)}' "
            + "} catch { $_.Exception.Message }; "
            + "if (-not (Test-Path (Join-Path '"
            + ps_quote(trusted_fallback_state)
            + "' 'status.json'))) { exit 31 }"
        )

        output = result.stdout + result.stderr
        assert result.returncode == 0, output
        assert "must-not-leak" not in output
        assert not state.exists()
        status_text = (trusted_fallback_state / "status.json").read_text(
            encoding="utf-8-sig"
        )
        log_text = (trusted_fallback_state / "operations.log").read_text(
            encoding="utf-8-sig"
        )
        assert "must-not-leak" not in status_text + log_text
        assert json.loads(status_text)["status"] == "failed"


def test_health_reruns_checks_once_when_compose_recovery_command_fails(
    tmp_path: Path,
):
    docker_calls = tmp_path / "docker-calls.jsonl"
    state = tmp_path / "state"
    result = run_ps(
        "function global:docker { "
        + f"$args | ConvertTo-Json -Compress | Add-Content '{ps_quote(docker_calls)}'; "
        + "if ($args.Count -ge 2 -and $args[-2] -eq 'up' -and $args[-1] -eq '-d') { "
        + "$global:LASTEXITCODE = 1; 'recovery command failed'; return }; "
        + "$global:LASTEXITCODE = 0; "
        + "'app|running|healthy'; 'qdrant|running|healthy' }; "
        + "function global:Invoke-WebRequest { throw 'forced HTTP failure' }; "
        + "try { "
        + f"& '{ps_quote(HEALTH)}' -RepositoryRoot '{ps_quote(ROOT)}' "
        + "-EnvFile 'deploy/.env' "
        + f"-StateRoot '{ps_quote(state)}' -AttemptRecovery $true "
        + "} catch { }; exit 0"
    )

    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in docker_calls.read_text().splitlines()]
    assert sum(call[-2:] == ["up", "-d"] for call in calls) == 1
    assert calls[-2] == "info"
    assert calls[-1] == [
        "compose",
        "--project-directory",
        str(ROOT),
        "--file",
        str(ROOT / "compose.yaml"),
        "--env-file",
        str(ROOT / "deploy/.env"),
        "ps",
        "--format",
        "{{.Service}}|{{.State}}|{{.Health}}",
        "app",
        "qdrant",
    ]
    assert "Compose recovery command failed" in (state / "operations.log").read_text(
        encoding="utf-8-sig"
    )


def test_rejected_overlapping_state_root_uses_trusted_fallback_without_secret(
    tmp_path: Path, trusted_fallback_state: Path
):
    repository = tmp_path / "caller-repository"
    repository.mkdir()
    (repository / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    env_file = repository / "deploy.env"
    env_file.write_text(
        "DEPLOY_DATA_ROOT=data\nLLM_API_KEY=must-not-leak\n", encoding="utf-8"
    )
    rejected_state = repository / "data"

    for script in (START, HEALTH):
        result = run_ps(
            "try { "
            + f"& '{ps_quote(script)}' -RepositoryRoot '{ps_quote(repository)}' "
            + f"-EnvFile '{ps_quote(env_file)}' -StateRoot '{ps_quote(rejected_state)}' "
            + "} catch { }; exit 0"
        )

        assert result.returncode == 0, result.stderr
        assert not rejected_state.exists()
        status_text = (trusted_fallback_state / "status.json").read_text(
            encoding="utf-8-sig"
        )
        log_text = (trusted_fallback_state / "operations.log").read_text(
            encoding="utf-8-sig"
        )
        assert json.loads(status_text)["status"] == "failed"
        assert "must-not-leak" not in result.stdout + result.stderr + status_text + log_text


def test_module_import_failure_uses_the_script_repository_fallback(tmp_path: Path):
    repository = tmp_path / "script-repository"
    windows_dir = repository / "deploy" / "windows"
    windows_dir.mkdir(parents=True)
    rejected_state = tmp_path / "caller-state"

    for script in (START, HEALTH):
        copied_script = windows_dir / script.name
        shutil.copyfile(script, copied_script)
        result = run_ps(
            "try { "
            + f"& '{ps_quote(copied_script)}' "
            + f"-RepositoryRoot '{ps_quote(tmp_path / 'caller-repository')}' "
            + f"-EnvFile '{ps_quote(tmp_path / 'missing.env')}' "
            + f"-StateRoot '{ps_quote(rejected_state)}' "
            + "} catch { }; exit 0"
        )

        assert result.returncode == 0, result.stderr
        assert not rejected_state.exists()
        fallback = repository / "deploy-state"
        status_text = (fallback / "status.json").read_text(encoding="utf-8-sig")
        log_text = (fallback / "operations.log").read_text(encoding="utf-8-sig")
        assert json.loads(status_text)["status"] == "failed"
        assert "caller-state" not in status_text + log_text


def test_startup_global_deadline_stops_a_hanging_docker_command(
    tmp_path: Path, trusted_fallback_state: Path
):
    repository = tmp_path / "valid-repository"
    repository.mkdir()
    (repository / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    env_file = repository / "deploy.env"
    env_file.write_text("DEPLOY_DATA_ROOT=data\n", encoding="utf-8")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    child_pid = tmp_path / "hanging-docker.pid"
    fake_docker = fake_bin / "docker.cmd"
    fake_docker.write_text(
        "@echo off\r\n"
        + "powershell.exe -NoProfile -NonInteractive -Command \"$PID | Set-Content -NoNewline -LiteralPath '"
        + ps_quote(child_pid)
        + "'; Start-Sleep -Seconds 60\" >nul 2>&1\r\n",
        encoding="utf-8",
    )

    started = time.monotonic()
    try:
        result = run_ps(
            "$env:PATH = '"
            + ps_quote(fake_bin)
            + ";' + $env:PATH; "
            + f"& '{ps_quote(START)}' -RepositoryRoot '{ps_quote(repository)}' "
            + f"-EnvFile '{ps_quote(env_file)}' -StateRoot '{ps_quote(tmp_path / 'caller-state')}' "
            + "-TimeoutSeconds 30; exit 0",
            timeout=50,
        )
    finally:
        if child_pid.exists():
            subprocess.run(
                ["taskkill", "/PID", child_pid.read_text().strip(), "/T", "/F"],
                capture_output=True,
                check=False,
            )
    elapsed = time.monotonic() - started

    assert result.returncode != 0, result.stderr
    assert 25 <= elapsed < 45
    status = json.loads(
        (trusted_fallback_state / "status.json").read_text(encoding="utf-8-sig")
    )
    assert status["status"] == "failed"
    assert status["category"] == "startup"


def test_operations_installer_has_private_intranet_and_exact_task_contracts():
    source = INSTALL.read_text(encoding="utf-8")

    for task_name in (
        "PythonSelfAgent-LoginRecovery",
        "PythonSelfAgent-Health",
        "PythonSelfAgent-DailyBackup",
        "PythonSelfAgent-MonthlyRestoreDrill",
    ):
        assert task_name in source

    assert "#Requires -Version 5.1" in source
    assert "#Requires -RunAsAdministrator" in source
    assert "Get-NetConnectionProfile" in source
    assert "NetworkCategory -ne 'Private'" in source
    assert "-Profile Private" in source
    assert "-RemoteAddress LocalSubnet" in source
    assert "-Protocol TCP" in source
    assert "-LocalPort 7860" in source
    assert source.index("Get-NetConnectionProfile") < source.index("New-NetFirewallRule")
    assert "MultipleInstances = 'IgnoreNew'" in source
    assert "StartWhenAvailable = $true" in source
    assert "-LogonType Interactive" in source
    assert "-RunLevel Highest" in source
    assert "-AtLogOn" in source
    assert "RepetitionInterval (New-TimeSpan -Minutes 5)" in source
    assert "-Daily -At '03:00'" in source
    assert "$monthlyTrigger.DaysOfWeek = 1" in source
    assert "$monthlyTrigger.WeeksOfMonth = 1" in source
    assert "New-ScheduledTaskAction -Execute 'powershell.exe'" in source
    assert "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File" in source
    assert "Register-ScheduledTask" in source
    assert "-Force" in source
    assert '"${currentUser}:(M)"' in source
    assert "'SYSTEM:(F)'" in source
    assert "'Administrators:(F)'" in source
    assert "[Diagnostics.Process]::GetCurrentProcess().SessionId" in source
    assert source.count("WTSQuerySessionInformation") >= 2
    assert "WTSFreeMemory" in source
    assert "$monthlyTrigger.StartBoundary" in source
    assert "-Hour 4 -Minute 0" in source
    assert source.count("Settings = New-OperationsTaskSettings -WakeToRun $true") == 2
    assert "$env:USERDOMAIN" not in source
    assert "$env:USERNAME" not in source
    assert "Win32_ComputerSystem" not in source


def test_operations_installer_preflights_before_system_mutations():
    source = INSTALL.read_text(encoding="utf-8")

    preflight_end = source.index("# All preflight checks above this line")
    for preflight in (
        "Get-OperationsConfig",
        "'compose', 'version'",
        "'scout', 'version'",
        "Assert-PrivateInternetProfiles",
        "Assert-Tcp7860IsFreeOrOwnedByComposeApp -Config $config",
    ):
        assert source.index(preflight) < preflight_end
    for mutation in (
        "New-Item -ItemType Directory",
        "icacls.exe",
        "Remove-NetFirewallRule",
        "New-NetFirewallRule",
        "Register-ScheduledTask",
    ):
        assert source.index(mutation) > preflight_end


def test_operations_uninstaller_removes_only_its_exact_tasks_and_rule():
    source = UNINSTALL.read_text(encoding="utf-8")

    for task_name in (
        "PythonSelfAgent-LoginRecovery",
        "PythonSelfAgent-Health",
        "PythonSelfAgent-DailyBackup",
        "PythonSelfAgent-MonthlyRestoreDrill",
    ):
        assert task_name in source
    assert "Python Self Agent - Private Intranet 7860" in source
    assert "Unregister-ScheduledTask -TaskName $taskName" in source
    assert "Remove-NetFirewallRule" in source
    assert "Preserved environment file:" in source
    assert "Preserved containers and images." in source
    for forbidden in (
        "Remove-Item",
        "-Recurse",
        "docker ",
        "docker.exe",
        "rmdir",
        "del /",
    ):
        assert forbidden not in source


def installer_prelude() -> str:
    source = INSTALL.read_text(encoding="utf-8")
    helpers = source.split("$modulePath = Join-Path", maxsplit=1)[0]
    return helpers[helpers.index("function Assert-RequiredCommand") :]


def test_installer_listener_ownership_rejects_mixed_or_unrecognized_listener():
    prelude = installer_prelude()
    result = run_ps(
        "& { "
        + prelude
        + "; $bindings = @([PSCustomObject]@{ HostIp='0.0.0.0'; HostPort='7860' }); "
        + "$listeners = @("
        + "[PSCustomObject]@{ LocalAddress='0.0.0.0'; OwningProcess=101 }, "
        + "[PSCustomObject]@{ LocalAddress='0.0.0.0'; OwningProcess=202 }); "
        + "$processes = @{ 101=[PSCustomObject]@{ ProcessName='com.docker.backend' }; "
        + "202=[PSCustomObject]@{ ProcessName='unexpected-listener' } }; "
        + "if (Test-ComposePortListenerOwnership -Listeners $listeners "
        + "-PortBindings $bindings -ProcessesById $processes) { exit 61 } }"
    )

    assert result.returncode == 0, result.stderr


def test_installer_listener_ownership_rejects_mismatched_listener_address():
    prelude = installer_prelude()
    result = run_ps(
        "& { "
        + prelude
        + "; $bindings = @([PSCustomObject]@{ HostIp='0.0.0.0'; HostPort='7860' }); "
        + "$listeners = @([PSCustomObject]@{ LocalAddress='127.0.0.1'; OwningProcess=101 }); "
        + "$processes = @{ 101=[PSCustomObject]@{ ProcessName='com.docker.backend' } }; "
        + "if (Test-ComposePortListenerOwnership -Listeners $listeners "
        + "-PortBindings $bindings -ProcessesById $processes) { exit 64 } }"
    )

    assert result.returncode == 0, result.stderr


def test_installer_listener_ownership_accepts_only_matching_docker_forwarder():
    prelude = installer_prelude()
    result = run_ps(
        "& { "
        + prelude
        + "; $bindings = @([PSCustomObject]@{ HostIp='0.0.0.0'; HostPort='7860' }); "
        + "$listeners = @([PSCustomObject]@{ LocalAddress='0.0.0.0'; OwningProcess=101 }); "
        + "$processes = @{ 101=[PSCustomObject]@{ ProcessName='com.docker.backend' } }; "
        + "if (-not (Test-ComposePortListenerOwnership -Listeners $listeners "
        + "-PortBindings $bindings -ProcessesById $processes)) { exit 62 } }"
    )

    assert result.returncode == 0, result.stderr


def test_installer_rejects_absent_interactive_identity():
    prelude = installer_prelude()
    result = run_ps(
        "& { "
        + prelude
        + "; function Get-WtsSessionValue { param($SessionId, $InfoClass) return $null }; "
        + "try { Get-OperationsInteractiveUserName | Out-Null; exit 65 } catch { exit 0 } }"
    )

    assert result.returncode == 0, result.stderr


def test_installer_uses_current_process_session_for_wts_identity():
    prelude = installer_prelude()
    result = run_ps(
        "& { "
        + prelude
        + "; $script:calls = @(); "
        + "function Get-WtsSessionValue { param($SessionId, $InfoClass) "
        + "$script:calls += \"$SessionId/$InfoClass\"; "
        + "if ($InfoClass -eq 5) { return 'alice' }; return 'WORKSTATION' }; "
        + "$expectedSessionId = [Diagnostics.Process]::GetCurrentProcess().SessionId; "
        + "$identity = Get-OperationsInteractiveUserName; "
        + "if ($identity -ne 'WORKSTATION\\alice' -or "
        + "$script:calls -notcontains \"$expectedSessionId/5\" -or "
        + "$script:calls -notcontains \"$expectedSessionId/7\") { exit 63 } }"
    )

    assert result.returncode == 0, result.stderr
