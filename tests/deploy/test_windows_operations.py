from __future__ import annotations

import json
import os
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
BACKUP = ROOT / "deploy" / "windows" / "Backup-Deployment.ps1"
RESTORE = ROOT / "deploy" / "windows" / "Restore-Deployment.ps1"
DRILL = ROOT / "deploy" / "windows" / "Invoke-RestoreDrill.ps1"
UPDATE = ROOT / "deploy" / "windows" / "Update-Deployment.ps1"


def run_ps(
    script: str, *, timeout: float = 60, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    if len(script) > 8_000:
        command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$sourceText=[Console]::In.ReadToEnd(); "
            "& ([scriptblock]::Create($sourceText))",
        ]
        script_input = script
    else:
        command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ]
        script_input = None
    return subprocess.run(
        command,
        cwd=ROOT,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=timeout,
        env=env,
        input=script_input,
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


@pytest.mark.skipif(
    os.name != "nt", reason="Windows Runtime toast types exist only on Windows"
)
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


def test_configuration_uses_safe_operations_defaults_when_env_values_are_absent(
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    env_file = repo / "deploy.env"
    env_file.write_text("DEPLOY_DATA_ROOT=data\n", encoding="utf-8")

    result = run_ps(
        import_module()
        + f"Get-OperationsConfig -RepositoryRoot '{ps_quote(repo)}' "
        + f"-EnvFile '{ps_quote(env_file)}' | ConvertTo-Json -Compress"
    )

    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    assert Path(config["StateRoot"]) == repo / "deploy-state"
    assert config["NotificationCooldownMinutes"] == 30


@pytest.mark.skipif(os.name != "nt", reason="asserts Windows drive-path semantics")
def test_configuration_uses_safe_windows_backup_default(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    env_file = repo / "deploy.env"
    env_file.write_text("DEPLOY_DATA_ROOT=data\n", encoding="utf-8")

    result = run_ps(
        import_module()
        + f"Get-OperationsConfig -RepositoryRoot '{ps_quote(repo)}' "
        + f"-EnvFile '{ps_quote(env_file)}' | ConvertTo-Json -Compress"
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["BackupRoot"] == "D:\\python_self_agent_backups"


def test_configuration_uses_validated_environment_defaults_and_explicit_overrides(
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    env_file = repo / "deploy.env"
    env_file.write_text(
        "DEPLOY_DATA_ROOT=data\n"
        "DEPLOY_STATE_ROOT=environment-state\n"
        f"DEPLOY_BACKUP_ROOT={tmp_path / 'environment-backups'}\n"
        "OPERATIONS_NOTIFY_COOLDOWN_MINUTES=45\n",
        encoding="utf-8",
    )

    defaults = run_ps(
        import_module()
        + f"Get-OperationsConfig -RepositoryRoot '{ps_quote(repo)}' "
        + f"-EnvFile '{ps_quote(env_file)}' | ConvertTo-Json -Compress"
    )
    assert defaults.returncode == 0, defaults.stderr
    default_config = json.loads(defaults.stdout)
    assert Path(default_config["StateRoot"]) == repo / "environment-state"
    assert Path(default_config["BackupRoot"]) == tmp_path / "environment-backups"
    assert default_config["NotificationCooldownMinutes"] == 45

    explicit_state = tmp_path / "explicit-state"
    explicit_backups = tmp_path / "explicit-backups"
    explicit = run_ps(
        import_module()
        + f"Get-OperationsConfig -RepositoryRoot '{ps_quote(repo)}' "
        + f"-EnvFile '{ps_quote(env_file)}' "
        + f"-StateRoot '{ps_quote(explicit_state)}' "
        + f"-BackupRoot '{ps_quote(explicit_backups)}' | ConvertTo-Json -Compress"
    )
    assert explicit.returncode == 0, explicit.stderr
    explicit_config = json.loads(explicit.stdout)
    assert Path(explicit_config["StateRoot"]) == explicit_state
    assert Path(explicit_config["BackupRoot"]) == explicit_backups

    fallback_collision = run_ps(
        import_module()
        + f"Get-OperationsConfig -RepositoryRoot '{ps_quote(repo)}' "
        + f"-EnvFile '{ps_quote(env_file)}' -StateRoot 'deploy-state' "
        + f"-BackupRoot '{ps_quote(explicit_backups)}' | ConvertTo-Json -Compress"
    )
    assert fallback_collision.returncode == 0, fallback_collision.stderr
    collision_config = json.loads(fallback_collision.stdout)
    assert Path(collision_config["StateRoot"]) == repo / "deploy-state"


@pytest.mark.skipif(os.name != "nt", reason="asserts Windows drive-path semantics")
def test_explicit_windows_backup_fallback_value_overrides_environment(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    env_file = repo / "deploy.env"
    env_file.write_text(
        "DEPLOY_DATA_ROOT=data\n"
        f"DEPLOY_BACKUP_ROOT={tmp_path / 'environment-backups'}\n",
        encoding="utf-8",
    )

    result = run_ps(
        import_module()
        + f"Get-OperationsConfig -RepositoryRoot '{ps_quote(repo)}' "
        + f"-EnvFile '{ps_quote(env_file)}' "
        + "-BackupRoot 'D:\\python_self_agent_backups' | ConvertTo-Json -Compress"
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["BackupRoot"] == "D:\\python_self_agent_backups"


@pytest.mark.parametrize("cooldown", ["zero", "0", "1441"])
def test_configuration_rejects_invalid_notification_cooldown(
    tmp_path: Path, cooldown: str
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    env_file = repo / "deploy.env"
    env_file.write_text(
        "DEPLOY_DATA_ROOT=data\n"
        f"DEPLOY_BACKUP_ROOT={tmp_path / 'backups'}\n"
        f"OPERATIONS_NOTIFY_COOLDOWN_MINUTES={cooldown}\n",
        encoding="utf-8",
    )

    result = run_ps(
        import_module()
        + f"Get-OperationsConfig -RepositoryRoot '{ps_quote(repo)}' "
        + f"-EnvFile '{ps_quote(env_file)}'"
    )

    assert result.returncode != 0
    assert "OPERATIONS_NOTIFY_COOLDOWN_MINUTES" in result.stderr


def test_configured_notification_cooldown_is_applied(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    env_file = repo / "deploy.env"
    env_file.write_text(
        "DEPLOY_DATA_ROOT=data\n"
        f"DEPLOY_BACKUP_ROOT={tmp_path / 'backups'}\n"
        "OPERATIONS_NOTIFY_COOLDOWN_MINUTES=1\n",
        encoding="utf-8",
    )
    state = tmp_path / "state"
    result = run_ps(
        import_module()
        + f"Get-OperationsConfig -RepositoryRoot '{ps_quote(repo)}' "
        + f"-EnvFile '{ps_quote(env_file)}' | Out-Null; "
        + f"$state='{ps_quote(state)}'; "
        + "$sent = Send-OperationsNotification -StateRoot $state "
        + "-Category 'test' -Title 'title' -Message 'message' "
        + "-NotificationAction { param($title, $message) }; "
        + "if (-not $sent) { exit 11 }; "
        + "$stamp = Join-Path $state 'notifications\\test.json'; "
        + "$payload = Get-Content -LiteralPath $stamp -Raw | ConvertFrom-Json; "
        + "$payload.last_sent_at = (Get-Date).ToUniversalTime().AddMinutes(-2).ToString('o'); "
        + "$payload | ConvertTo-Json | Set-Content -LiteralPath $stamp; "
        + "$sent = Send-OperationsNotification -StateRoot $state "
        + "-Category 'test' -Title 'title' -Message 'message' "
        + "-NotificationAction { param($title, $message) }; "
        + "if (-not $sent) { exit 12 }"
    )

    assert result.returncode == 0, result.stderr


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


@pytest.mark.skipif(
    os.name != "nt",
    reason="uses a Windows .cmd shim, PATH separator, and taskkill process cleanup",
)
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
    assert 25 <= elapsed < 55
    status = json.loads(
        (trusted_fallback_state / "status.json").read_text(encoding="utf-8-sig")
    )
    assert status["status"] == "failed"
    assert status["category"] == "startup"


def test_operations_installer_has_private_intranet_and_exact_task_contracts():
    source = INSTALL.read_text(encoding="utf-8")
    assert_installer_monthly_static_contracts(source)

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
    assert "New-ScheduledTaskAction -Execute 'powershell.exe'" in source
    assert (
        "-NoProfile -NonInteractive -WindowStyle Hidden "
        "-ExecutionPolicy Bypass -File"
    ) in source
    assert "Register-ScheduledTask" in source
    assert "-Force" in source
    assert '"${currentUser}:(M)"' in source
    assert "'SYSTEM:(F)'" in source
    assert "'Administrators:(F)'" in source
    assert "[Diagnostics.Process]::GetCurrentProcess().SessionId" in source
    assert source.count("WTSQuerySessionInformation") >= 2
    assert "WTSFreeMemory" in source
    assert "-Hour 4 -Minute 0" in source
    assert source.count("Settings = New-OperationsTaskSettings -WakeToRun $true") == 2
    assert "$env:USERDOMAIN" not in source
    assert "$env:USERNAME" not in source
    assert "Win32_ComputerSystem" not in source


def test_operations_installer_preflights_before_system_mutations():
    source = INSTALL.read_text(encoding="utf-8")

    preflight_end = source.index(
        "# All preflight checks and in-memory task validation are above this line."
    )
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
    ):
        assert source.index(mutation) > preflight_end
    assert source.index("Register-OperationsTask -Task $task", preflight_end) > preflight_end


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


def test_operations_entry_points_forward_only_honest_optional_root_values():
    for script in (START, HEALTH, BACKUP, RESTORE, DRILL, UPDATE, INSTALL, UNINSTALL):
        source = script.read_text(encoding="utf-8")
        assert "[string]$StateRoot = $null" in source
        assert "Get-OperationsConfig" in source
    for script in (BACKUP, RESTORE, DRILL, UPDATE, INSTALL, UNINSTALL):
        source = script.read_text(encoding="utf-8")
        assert "[string]$BackupRoot = $null" in source

    installer = INSTALL.read_text(encoding="utf-8")
    assert "-StateRoot \"{3}\"" in installer
    assert "$Config.StateRoot" in installer
    assert "-BackupRoot \"{0}\"" in installer
    assert "$Config.BackupRoot" in installer


def test_uninstall_uses_environment_roots_and_reports_effective_preserved_paths(
    tmp_path: Path,
):
    copied_windows = tmp_path / "copied" / "deploy" / "windows"
    copied_windows.mkdir(parents=True)
    copied_uninstall = copied_windows / UNINSTALL.name
    copied_uninstall.write_text(
        UNINSTALL.read_text(encoding="utf-8").replace(
            "#Requires -RunAsAdministrator\n", ""
        ),
        encoding="utf-8",
    )
    shutil.copyfile(MODULE, copied_windows / MODULE.name)

    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    data = tmp_path / "environment-data"
    state = tmp_path / "environment-state"
    backups = tmp_path / "environment-backups"
    env_file = repository / "deploy.env"
    env_file.write_text(
        f"DEPLOY_DATA_ROOT={data}\n"
        f"DEPLOY_STATE_ROOT={state}\n"
        f"DEPLOY_BACKUP_ROOT={backups}\n",
        encoding="utf-8",
    )
    result = run_ps(
        "function global:Get-ScheduledTask { @() }; "
        "function global:Get-NetFirewallRule { @() }; "
        + f"& '{ps_quote(copied_uninstall)}' "
        + f"-RepositoryRoot '{ps_quote(repository)}' "
        + f"-EnvFile '{ps_quote(env_file)}'"
    )

    assert result.returncode == 0, result.stderr
    assert f"Preserved environment file: {env_file}" in result.stdout
    assert f"Preserved deployment data: {data}" in result.stdout
    assert f"Preserved operations state: {state}" in result.stdout
    assert f"Preserved backup location: {backups}" in result.stdout


@pytest.mark.parametrize(
    "config_failure",
    (
        "missing-repository",
        "missing-compose",
        "missing-env",
        "unreadable-env",
        "missing-data-root",
        "invalid-cooldown",
        "overlapping-roots",
    ),
)
def test_uninstall_configuration_failure_still_removes_only_exact_objects(
    tmp_path: Path, config_failure: str
):
    copied_windows = tmp_path / "copied" / "deploy" / "windows"
    copied_windows.mkdir(parents=True)
    copied_uninstall = copied_windows / UNINSTALL.name
    copied_uninstall.write_text(
        UNINSTALL.read_text(encoding="utf-8").replace(
            "#Requires -RunAsAdministrator\n", ""
        ),
        encoding="utf-8",
    )
    shutil.copyfile(MODULE, copied_windows / MODULE.name)

    repository = tmp_path / "repository"
    repository.mkdir()
    compose = repository / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    env_file = repository / "deploy.env"
    env_file.write_text("DEPLOY_DATA_ROOT=data\n", encoding="utf-8")
    repository_argument = repository
    env_argument: Path | str = env_file
    explicit_arguments = ""
    explicit_state = tmp_path / "explicit-state"
    explicit_backups = tmp_path / "explicit-backups"
    if config_failure == "missing-repository":
        repository_argument = tmp_path / "missing-repository"
    elif config_failure == "missing-compose":
        compose.unlink()
    elif config_failure == "missing-env":
        env_file.unlink()
        env_argument = repository / "TOKEN=must-not-leak"
        explicit_arguments = (
            f" -StateRoot '{ps_quote(explicit_state)}'"
            f" -BackupRoot '{ps_quote(explicit_backups)}'"
        )
    elif config_failure == "unreadable-env":
        env_file.unlink()
        env_file.mkdir()
    elif config_failure == "missing-data-root":
        env_file.write_text("OPERATIONS_NOTIFY_COOLDOWN_MINUTES=30\n", encoding="utf-8")
    elif config_failure == "invalid-cooldown":
        env_file.write_text(
            "DEPLOY_DATA_ROOT=data\nOPERATIONS_NOTIFY_COOLDOWN_MINUTES=invalid\n",
            encoding="utf-8",
        )
    elif config_failure == "overlapping-roots":
        env_file.write_text(
            "DEPLOY_DATA_ROOT=shared\nDEPLOY_STATE_ROOT=shared\n",
            encoding="utf-8",
        )

    calls = tmp_path / "removals.txt"
    marker = tmp_path / "preserve-me.txt"
    marker.write_text("unchanged", encoding="utf-8")
    result = run_ps(
        f"$global:removalCalls='{ps_quote(calls)}'; "
        "function global:Get-ScheduledTask { param([string]$TaskName) "
        "[PSCustomObject]@{ TaskName=$TaskName } }; "
        "function global:Unregister-ScheduledTask { [CmdletBinding(SupportsShouldProcess)] "
        "param([string]$TaskName) "
        "Add-Content -LiteralPath $global:removalCalls -Value \"task:$TaskName\" }; "
        "function global:Get-NetFirewallRule { param([string]$DisplayName) "
        "[PSCustomObject]@{ DisplayName=$DisplayName } }; "
        "function global:Remove-NetFirewallRule { [CmdletBinding()] param("
        "[Parameter(ValueFromPipeline=$true)]$InputObject) process { "
        "Add-Content -LiteralPath $global:removalCalls -Value "
        "\"rule:$($InputObject.DisplayName)\" } }; "
        + f"& '{ps_quote(copied_uninstall)}' "
        + f"-RepositoryRoot '{ps_quote(repository_argument)}' "
        + f"-EnvFile '{ps_quote(env_argument)}'{explicit_arguments}"
    )

    assert result.returncode == 0, result.stderr
    assert "Configuration warning:" in result.stdout
    assert "must-not-leak" not in result.stdout + result.stderr
    assert "Preserved environment file label:" in result.stdout
    assert "Preserved deployment data label:" in result.stdout
    assert "Preserved operations state label:" in result.stdout
    assert "Preserved backup location label:" in result.stdout
    if config_failure == "missing-env":
        assert f"Preserved operations state label: {explicit_state}" in result.stdout
        assert f"Preserved backup location label: {explicit_backups}" in result.stdout
    assert marker.read_text(encoding="utf-8") == "unchanged"
    assert not (repository / "deploy-state").exists()
    assert not (repository / "deploy-data").exists()
    assert not (tmp_path / "environment-state").exists()
    assert not (tmp_path / "environment-backups").exists()
    assert not explicit_state.exists()
    assert not explicit_backups.exists()

    removals = calls.read_text(encoding="utf-8-sig").splitlines()
    assert removals == [
        "task:PythonSelfAgent-LoginRecovery",
        "task:PythonSelfAgent-Health",
        "task:PythonSelfAgent-DailyBackup",
        "task:PythonSelfAgent-MonthlyRestoreDrill",
        "rule:Python Self Agent - Private Intranet 7860",
    ]


def installer_prelude() -> str:
    source = INSTALL.read_text(encoding="utf-8")
    helpers = source.split("$modulePath = Join-Path", maxsplit=1)[0]
    return helpers[helpers.index("function Assert-RequiredCommand") :]


def assert_installer_monthly_static_contracts(source: str) -> None:
    for helper_name in (
        "Resolve-OperationsMonthlyTriggerSchema",
        "Assert-OperationsMonthlyRestoreDrillTrigger",
        "New-OperationsMonthlyRestoreDrillTrigger",
        "Add-OperationsTaskXmlElement",
        "Assert-OperationsMonthlyRestoreDrillXml",
        "New-OperationsMonthlyRestoreDrillXml",
        "Get-OperationsSchtasksXml",
        "Register-OperationsTask",
        "Assert-OperationsPersistedTask",
        "Assert-OperationsInstalledState",
    ):
        assert helper_name in source
    for exact_contract in (
        "Enabled = $true",
        "DaysOfWeek = [uint16]1",
        "WeeksOfMonth = [uint16]1",
        "$properties[$monthPropertyName] = [uint16]4095",
        "-TaskPath '\\' -Xml $MonthlyXml -Force -ErrorAction Stop",
        "-TaskPath '\\' -InputObject $Task.Definition -Force -ErrorAction Stop",
    ):
        assert exact_contract in source


def test_installer_monthly_xml_has_static_registration_and_hidden_window_contracts():
    source = INSTALL.read_text(encoding="utf-8")

    assert "New-Object Xml.XmlDocument" in source
    assert ".CreateElement(" in source
    assert "[xml]$document = $XmlText" in source
    assert "$document.OuterXml" in source
    assert "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File" in source
    assert source.count("-WindowStyle Hidden") == 1
    assert "-LogonType S4U" not in source
    assert "Schedule.Service" not in source
    assert "schtasks.exe /Create" not in source
    assert "RegisterByPrincipal" not in source

    write_boundary = source.index(
        "# All preflight checks and in-memory task validation are above this line."
    )
    xml_build = source.index("$monthlyXml = New-OperationsMonthlyRestoreDrillXml")
    xml_validation = source.index(
        "Assert-OperationsMonthlyRestoreDrillXml -XmlText $xmlText"
    )
    assert xml_build < write_boundary
    assert xml_validation < write_boundary


def test_installer_monthly_xml_escapes_action_text_and_validates_semantics():
    result = run_ps(
        "& { "
        + installer_prelude()
        + "; $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name; "
        + "$principal = [PSCustomObject]@{ UserId=$user }; "
        + "$arguments = '-NoProfile -NonInteractive -WindowStyle Hidden "
        + "-ExecutionPolicy Bypass -File \"C:\\A&B\\<drill>.ps1\"'; "
        + "$task = [PSCustomObject]@{ Name='PythonSelfAgent-MonthlyRestoreDrill'; "
        + "Action=[PSCustomObject]@{ Execute='powershell.exe'; Arguments=$arguments }; "
        + "Trigger=[PSCustomObject]@{ StartBoundary='2026-08-02T04:00:00' } }; "
        + "$xml = New-OperationsMonthlyRestoreDrillXml -Task $task -Principal $principal; "
        + "if ($xml -notmatch '&amp;' -or $xml -notmatch '&lt;drill&gt;') { exit 101 }; "
        + "[xml]$doc=$xml; $ns=New-Object Xml.XmlNamespaceManager($doc.NameTable); "
        + "$ns.AddNamespace('t','http://schemas.microsoft.com/windows/2004/02/mit/task'); "
        + "$actual=$doc.SelectSingleNode('/t:Task/t:Actions/t:Exec/t:Arguments',$ns).InnerText; "
        + "if ($actual -cne $arguments) { exit 102 } }"
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "mutation",
    (
        "$months=$doc.SelectSingleNode('//t:Months',$ns); "
        "$months.AppendChild($months.FirstChild.CloneNode($true)) | Out-Null",
        "$days=$doc.SelectSingleNode('//t:DaysOfWeek',$ns); "
        "$days.RemoveAll(); $days.AppendChild("
        "$doc.CreateElement('Saturday',$days.NamespaceURI)) | Out-Null",
        "$doc.SelectSingleNode('//t:CalendarTrigger/t:Enabled',$ns).InnerText='false'",
        "$actions=$doc.SelectSingleNode('//t:Actions',$ns); "
        "$actions.AppendChild($actions.FirstChild.CloneNode($true)) | Out-Null",
        "$doc.SelectSingleNode('//t:Settings/t:WakeToRun',$ns).InnerText='false'",
        "$doc.SelectSingleNode('//t:Actions/t:Exec/t:Arguments',$ns).InnerText="
        "'-NoProfile -NonInteractive -ExecutionPolicy Bypass -File x'",
    ),
)
def test_installer_monthly_xml_rejects_semantic_drift(mutation: str):
    result = run_ps(
        "& { "
        + installer_prelude()
        + "; $user=[Security.Principal.WindowsIdentity]::GetCurrent().Name; "
        + "$sid=([Security.Principal.NTAccount]$user).Translate("
        + "[Security.Principal.SecurityIdentifier]).Value; "
        + "$principal=[PSCustomObject]@{ UserId=$user }; "
        + "$task=[PSCustomObject]@{ Name='PythonSelfAgent-MonthlyRestoreDrill'; "
        + "Action=[PSCustomObject]@{ Execute='powershell.exe'; Arguments="
        + "'-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File x' }; "
        + "Trigger=[PSCustomObject]@{ StartBoundary='2026-08-02T04:00:00' } }; "
        + "$xml=New-OperationsMonthlyRestoreDrillXml -Task $task -Principal $principal; "
        + "[xml]$doc=$xml; $ns=New-Object Xml.XmlNamespaceManager($doc.NameTable); "
        + "$ns.AddNamespace('t','http://schemas.microsoft.com/windows/2004/02/mit/task'); "
        + mutation
        + "; try { Assert-OperationsMonthlyRestoreDrillXml -XmlText $doc.OuterXml "
        + "-ExpectedTask $task -ExpectedSid $sid; exit 103 } catch { exit 0 } }"
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("output_count", [0, 2])
def test_installer_registration_error_rejects_wrong_result_count(output_count: int):
    objects = ",".join(
        "[PSCustomObject]@{ TaskName='PythonSelfAgent-Health'; TaskPath='\\' }"
        for _ in range(output_count)
    )
    result = run_ps(
        "& { "
        + installer_prelude()
        + "; function Register-ScheduledTask { [CmdletBinding()] param("
        + "$TaskName,$TaskPath,$InputObject,$Xml,[switch]$Force); "
        + (f"@({objects})" if objects else "@()")
        + " }; $task=[PSCustomObject]@{ Name='PythonSelfAgent-Health'; Definition='d' }; "
        + "try { Register-OperationsTask -Task $task -MonthlyXml $null | Out-Null; "
        + "exit 104 } catch { if ($_.Exception.Message -notlike "
        + "'*registration returned an invalid result*') { exit 105 }; exit 0 } }"
    )

    assert result.returncode == 0, result.stderr


def test_installer_registration_routes_only_monthly_xml_and_others_input_object():
    result = run_ps(
        "& { "
        + installer_prelude()
        + "; $script:calls=@(); function Register-ScheduledTask { [CmdletBinding()] param("
        + "$TaskName,$TaskPath,$InputObject,$Xml,[switch]$Force); "
        + "$script:calls += [PSCustomObject]@{ Name=$TaskName; Path=$TaskPath; "
        + "HasInput=$PSBoundParameters.ContainsKey('InputObject'); "
        + "HasXml=$PSBoundParameters.ContainsKey('Xml'); "
        + "EA=[string]$PSBoundParameters['ErrorAction'] }; "
        + "[PSCustomObject]@{ TaskName=$TaskName; TaskPath=$TaskPath } }; "
        + "$normal=[PSCustomObject]@{ Name='PythonSelfAgent-Health'; Definition='d' }; "
        + "$monthly=[PSCustomObject]@{ Name='PythonSelfAgent-MonthlyRestoreDrill'; Definition='d' }; "
        + "Register-OperationsTask -Task $normal -MonthlyXml $null | Out-Null; "
        + "Register-OperationsTask -Task $monthly -MonthlyXml '<Task />' | Out-Null; "
        + "if ($script:calls.Count -ne 2 -or $script:calls[0].HasXml -or "
        + "-not $script:calls[0].HasInput -or -not $script:calls[1].HasXml -or "
        + "$script:calls[1].HasInput -or $script:calls[0].EA -ne 'Stop' -or "
        + "$script:calls[1].EA -ne 'Stop' -or $script:calls[0].Path -ne '\\' -or "
        + "$script:calls[1].Path -ne '\\') { exit 106 } }"
    )

    assert result.returncode == 0, result.stderr


def run_installer_fake_harness(
    tmp_path: Path,
    *,
    scenario: str = "success",
    initial_count: int = 0,
    passes: int = 1,
) -> subprocess.CompletedProcess[str]:
    copied_windows = tmp_path / "fake-installer" / "deploy" / "windows"
    copied_windows.mkdir(parents=True)
    copied_installer = copied_windows / INSTALL.name
    source = INSTALL.read_text(encoding="utf-8")
    source = source.replace("#Requires -Version 5.1\n", "")
    source = source.replace("#Requires -RunAsAdministrator\n", "")
    source = source.replace("Import-Module $modulePath -Force", "")
    source = source.replace(
        "$currentUser = Get-OperationsInteractiveUserName",
        "$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name",
    )
    source = source.replace(
        "$monthlyTrigger = New-OperationsMonthlyRestoreDrillTrigger `\n"
        "    -StartBoundary (Get-Date -Hour 4 -Minute 0 -Second 0)",
        "$monthlyTrigger = [PSCustomObject]@{ StartBoundary = "
        "(Get-Date -Hour 4 -Minute 0 -Second 0).ToString('s') }",
    )
    copied_installer.write_text(source, encoding="utf-8")
    for script_name in (
        "Start-Deployment.ps1",
        "Test-DeploymentHealth.ps1",
        "Backup-Deployment.ps1",
        "Invoke-RestoreDrill.ps1",
    ):
        (copied_windows / script_name).write_text("# fake\n", encoding="utf-8")

    repository = tmp_path / "repository"
    repository.mkdir()
    env_file = repository / "deploy.env"
    env_file.write_text("DEPLOY_DATA_ROOT=data\n", encoding="utf-8")
    state = tmp_path / "state"
    backup = tmp_path / "backup"
    script = (
        f"$global:harnessScenario='{scenario}'; "
        f"$global:initialCount={initial_count}; "
        + r"""
$global:harnessUser=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$global:harnessSid=([Security.Principal.NTAccount]$global:harnessUser).Translate(
  [Security.Principal.SecurityIdentifier]).Value
$global:taskStore=@{}
$global:registerCalls=@()
$global:firewallRule=$null
$global:monthlyXml=$null
$names=@(
  'PythonSelfAgent-LoginRecovery','PythonSelfAgent-Health',
  'PythonSelfAgent-DailyBackup','PythonSelfAgent-MonthlyRestoreDrill'
)
for($i=0; $i -lt $global:initialCount; $i++) {
  $global:taskStore[$names[$i]]=[PSCustomObject]@{
    TaskName=$names[$i]; TaskPath='\';
    Settings=[PSCustomObject]@{ Enabled=($global:initialCount -ne 2) }
  }
}
if($global:harnessScenario -eq 'extra_task') {
  $global:taskStore['PythonSelfAgent-Unexpected']=[PSCustomObject]@{
    TaskName='PythonSelfAgent-Unexpected'; TaskPath='\';
    Settings=[PSCustomObject]@{ Enabled=$true }
  }
}
function global:Get-OperationsConfig {
  param($RepositoryRoot,$EnvFile,$StateRoot,$BackupRoot)
  [PSCustomObject]@{
    RepositoryRoot=$RepositoryRoot; EnvFile=$EnvFile; StateRoot=$StateRoot;
    BackupRoot=$BackupRoot; ComposeFile=(Join-Path $RepositoryRoot 'compose.yaml')
  }
}
function global:Get-Command { param($Name,$CommandType) [PSCustomObject]@{ Name=$Name } }
function global:Invoke-External { param($FilePath,$ArgumentList) @() }
function global:Get-NetConnectionProfile { @() }
function global:Get-NetTCPConnection { @() }
function global:New-ScheduledTaskPrincipal {
  param($UserId,$LogonType,$RunLevel)
  [PSCustomObject]@{ UserId=$UserId; LogonType=[string]$LogonType; RunLevel=[string]$RunLevel }
}
function global:New-ScheduledTaskAction {
  param($Execute,$Argument)
  [PSCustomObject]@{ Execute=$Execute; Arguments=$Argument }
}
function global:New-ScheduledTaskSettingsSet {
  param([switch]$StartWhenAvailable,$MultipleInstances)
  [PSCustomObject]@{ Enabled=$true; StartWhenAvailable=$true;
    MultipleInstances='IgnoreNew'; WakeToRun=$false }
}
function global:New-ScheduledTaskTrigger {
  param([switch]$AtLogOn,[switch]$Daily,[switch]$Once,$At,
    $RepetitionInterval,$RepetitionDuration)
  $className=if($AtLogOn){'MSFT_TaskLogonTrigger'}elseif($Daily){'MSFT_TaskDailyTrigger'}else{'MSFT_TaskTimeTrigger'}
  [PSCustomObject]@{ Enabled=$true;
    StartBoundary=if($At){([datetime]$At).ToString('s')}else{''};
    DaysInterval=if($Daily){1}else{$null};
    UserId=$null; Delay=$null;
    Repetition=if($Once){[PSCustomObject]@{ Interval='PT5M'; Duration='P1D';
      StopAtDurationEnd=$true }}else{$null};
    CimClass=[PSCustomObject]@{ CimClassName=$className } }
}
function global:New-ScheduledTask {
  [CmdletBinding()] param($Action,$Trigger,$Settings,$Principal)
  [PSCustomObject]@{ Actions=@($Action); Triggers=@($Trigger);
    Settings=$Settings; Principal=$Principal }
}
function global:Register-ScheduledTask {
  [CmdletBinding()] param($TaskName,$TaskPath,$InputObject,$Xml,[switch]$Force)
  $global:registerCalls += [PSCustomObject]@{
    Name=$TaskName; HasInput=$PSBoundParameters.ContainsKey('InputObject');
    HasXml=$PSBoundParameters.ContainsKey('Xml');
    ErrorAction=[string]$PSBoundParameters['ErrorAction']
  }
  if($global:harnessScenario -eq 'provider_error') {
    Write-Error 'synthetic provider failure'
  }
  if($global:harnessScenario -eq 'zero_output') { return @() }
  if($PSBoundParameters.ContainsKey('Xml')) {
    [xml]$xmlDocument=$Xml
    $manager=New-Object Xml.XmlNamespaceManager($xmlDocument.NameTable)
    $manager.AddNamespace('t','http://schemas.microsoft.com/windows/2004/02/mit/task')
    $command=$xmlDocument.SelectSingleNode('//t:Exec/t:Command',$manager).InnerText
    $arguments=$xmlDocument.SelectSingleNode('//t:Exec/t:Arguments',$manager).InnerText
    $wake=$true
    $global:monthlyXml=$Xml
  } else {
    $command=[string]$InputObject.Actions[0].Execute
    $arguments=[string]$InputObject.Actions[0].Arguments
    $wake=[bool]$InputObject.Settings.WakeToRun
  }
  $enabled=($global:harnessScenario -ne 'provider_disabled')
  $actual=[PSCustomObject]@{
    TaskName=$TaskName; TaskPath=$TaskPath;
    Principal=[PSCustomObject]@{ UserId=$global:harnessUser;
      LogonType='Interactive'; RunLevel='Highest' };
    Actions=@([PSCustomObject]@{ Execute=$command; Arguments=$arguments });
    Triggers=if($PSBoundParameters.ContainsKey('InputObject')){@($InputObject.Triggers)}else{@([PSCustomObject]@{ Kind='monthly' })};
    Settings=[PSCustomObject]@{ Enabled=$enabled; StartWhenAvailable=$true;
      MultipleInstances='IgnoreNew'; WakeToRun=$wake;
      Hidden=($global:harnessScenario -eq 'provider_hidden') }
  }
  if($global:harnessScenario -eq 'wrong_trigger' -and
     $TaskName -eq 'PythonSelfAgent-Health') {
    $actual.Triggers=@([PSCustomObject]@{ Enabled=$true;
      StartBoundary='2026-08-02T12:00:00'; DaysInterval=$null; Repetition=$null;
      CimClass=[PSCustomObject]@{ CimClassName='MSFT_TaskTimeTrigger' } })
  }
  $global:taskStore[$TaskName]=$actual
  if($global:harnessScenario -eq 'two_output') { return @($actual,$actual.PSObject.Copy()) }
  if($global:harnessScenario -eq 'wrong_result_identity') {
    return [PSCustomObject]@{ TaskName='Wrong'; TaskPath='\' }
  }
  return $actual
}
function global:Get-ScheduledTask {
  [CmdletBinding()] param([string]$TaskName)
  if($PSBoundParameters.ContainsKey('TaskName')) {
    if($global:harnessScenario -eq 'missing_monthly' -and
       $TaskName -eq 'PythonSelfAgent-MonthlyRestoreDrill') { return @() }
    if($global:taskStore.ContainsKey($TaskName)) { return $global:taskStore[$TaskName] }
    return @()
  }
  return @($global:taskStore.Values)
}
function global:Export-ScheduledTask {
  [CmdletBinding()] param($TaskName,$TaskPath)
  if($TaskName -eq 'PythonSelfAgent-MonthlyRestoreDrill') {
    $text=[string]$global:monthlyXml
  } else {
    $text='<?xml version="1.0"?><Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><Settings><Enabled>true</Enabled></Settings></Task>'
  }
  if($global:harnessScenario -eq 'export_disabled') {
    $text=$text.Replace('<Enabled>true</Enabled>','<Enabled>false</Enabled>')
  }
  return $text
}
function global:schtasks.exe {
  param([Parameter(ValueFromRemainingArguments=$true)]$Arguments)
  if($global:harnessScenario -eq 'schtasks_nonzero') {
    $global:LASTEXITCODE=1; return @()
  }
  $global:LASTEXITCODE=0
  if($global:harnessScenario -eq 'schtasks_stderr') {
    Write-Error 'synthetic native stderr' -ErrorAction Continue
  }
  $text=[string]$global:monthlyXml
  if($global:harnessScenario -eq 'schtasks_wrong_xml') {
    $text=$text.Replace('<Sunday />','<Saturday />')
  }
  return $text
}
function global:Get-NetFirewallRule {
  [CmdletBinding()] param($DisplayName)
  if($null -ne $global:firewallRule) { return $global:firewallRule }
  return @()
}
function global:Remove-NetFirewallRule {
  [CmdletBinding()] param([Parameter(ValueFromPipeline=$true)]$InputObject)
  process { $global:firewallRule=$null }
}
function global:New-NetFirewallRule {
  [CmdletBinding()] param($DisplayName,$Direction,$Action,$Protocol,$LocalPort,$Profile,$RemoteAddress)
  $global:firewallRule=[PSCustomObject]@{ DisplayName=$DisplayName; Enabled='True';
    Direction=[string]$Direction; Action=[string]$Action; Profile=[string]$Profile;
    Protocol=[string]$Protocol; LocalPort=if($global:harnessScenario -eq 'wrong_firewall'){'9999'}else{[string]$LocalPort};
    RemoteAddress=[string]$RemoteAddress }
  return $global:firewallRule
}
function global:Get-NetFirewallPortFilter {
  [CmdletBinding()] param([Parameter(ValueFromPipeline=$true)]$InputObject)
  process { [PSCustomObject]@{ Protocol=$InputObject.Protocol; LocalPort=$InputObject.LocalPort } }
}
function global:Get-NetFirewallAddressFilter {
  [CmdletBinding()] param([Parameter(ValueFromPipeline=$true)]$InputObject)
  process { [PSCustomObject]@{ RemoteAddress=$InputObject.RemoteAddress } }
}
function global:Get-Acl {
  [CmdletBinding()] param($LiteralPath)
  $userSid=[Security.Principal.SecurityIdentifier]$global:harnessSid
  $systemSid=[Security.Principal.SecurityIdentifier]'S-1-5-18'
  $adminSid=[Security.Principal.SecurityIdentifier]'S-1-5-32-544'
  $modify=[Security.AccessControl.FileSystemRights]::Modify
  $modifyProvider=$modify -bor [Security.AccessControl.FileSystemRights]::Synchronize
  $full=[Security.AccessControl.FileSystemRights]::FullControl
  $userRights=if($global:harnessScenario -eq 'acl_excess_rights'){$full}else{$modifyProvider}
  $userType=if($global:harnessScenario -eq 'acl_deny'){'Deny'}else{'Allow'}
  $rules=@(
    [PSCustomObject]@{ IdentityReference=$userSid; IsInherited=$false;
      AccessControlType=$userType; FileSystemRights=$userRights },
    [PSCustomObject]@{ IdentityReference=$systemSid; IsInherited=$false;
      AccessControlType='Allow'; FileSystemRights=$full },
    [PSCustomObject]@{ IdentityReference=$adminSid; IsInherited=$false;
      AccessControlType='Allow'; FileSystemRights=$full }
  )
  [PSCustomObject]@{ AreAccessRulesProtected=($global:harnessScenario -ne 'wrong_acl'); Access=$rules }
}
"""
        + f"\n$scriptPath='{ps_quote(copied_installer)}'\n"
        + f"$repo='{ps_quote(repository)}'\n"
        + f"$envFile='{ps_quote(env_file)}'\n"
        + f"$state='{ps_quote(state)}'\n"
        + f"$backup='{ps_quote(backup)}'\n"
        + r"""
try {
  $allOutput=@(& $scriptPath -RepositoryRoot $repo -EnvFile $envFile `
    -StateRoot $state -BackupRoot $backup -Confirm:$false)
"""
        + (
            r"""
  $allOutput += @(& $scriptPath -RepositoryRoot $repo -EnvFile $envFile `
    -StateRoot $state -BackupRoot $backup -Confirm:$false)
"""
            if passes == 2
            else ""
        )
        + r"""
  $payload=[ordered]@{
    TaskCount=$global:taskStore.Count
    EnabledCount=@($global:taskStore.Values | Where-Object { $_.Settings.Enabled }).Count
    RegisterCount=$global:registerCalls.Count
    XmlRegisterCount=@($global:registerCalls | Where-Object HasXml).Count
    InputRegisterCount=@($global:registerCalls | Where-Object HasInput).Count
    CompletionCount=@($allOutput | Where-Object {
      $_ -eq 'Windows deployment operations installation completed.' }).Count
  }
  Write-Output ('HARNESS:' + ($payload | ConvertTo-Json -Compress))
} catch {
  [Console]::Error.WriteLine('HARNESS_ERROR:' + $_.Exception.Message)
  exit 91
}
"""
    )
    return run_ps(script, timeout=90)


@pytest.mark.parametrize("initial_count", [0, 2, 3, 4])
def test_installer_partial_state_converges_to_four_enabled_tasks(
    tmp_path: Path, initial_count: int
):
    result = run_installer_fake_harness(tmp_path, initial_count=initial_count)

    assert result.returncode == 0, result.stderr
    marker = next(line for line in result.stdout.splitlines() if line.startswith("HARNESS:"))
    payload = json.loads(marker.removeprefix("HARNESS:"))
    assert payload == {
        "TaskCount": 4,
        "EnabledCount": 4,
        "RegisterCount": 4,
        "XmlRegisterCount": 1,
        "InputRegisterCount": 3,
        "CompletionCount": 1,
    }


def test_installer_partial_state_second_pass_remains_exact_and_idempotent(tmp_path: Path):
    result = run_installer_fake_harness(tmp_path, initial_count=2, passes=2)

    assert result.returncode == 0, result.stderr
    marker = next(line for line in result.stdout.splitlines() if line.startswith("HARNESS:"))
    payload = json.loads(marker.removeprefix("HARNESS:"))
    assert payload == {
        "TaskCount": 4,
        "EnabledCount": 4,
        "RegisterCount": 8,
        "XmlRegisterCount": 2,
        "InputRegisterCount": 6,
        "CompletionCount": 2,
    }


@pytest.mark.parametrize(
    ("scenario", "message"),
    (
        ("provider_error", "synthetic provider failure"),
        ("zero_output", "registration returned an invalid result"),
        ("two_output", "registration returned an invalid result"),
        ("wrong_result_identity", "registration returned an invalid result"),
        ("missing_monthly", "Persisted task count is invalid"),
        ("schtasks_nonzero", "schtasks XML query failed"),
        ("schtasks_stderr", "schtasks XML query failed"),
        ("schtasks_wrong_xml", "Monthly XML schedule is invalid"),
        ("provider_disabled", "Persisted settings are invalid"),
        ("provider_hidden", "Persisted settings are invalid"),
        ("export_disabled", "Persisted task is disabled"),
        ("wrong_trigger", "Persisted trigger is invalid"),
        ("extra_task", "Persisted operations task set is invalid"),
        ("wrong_firewall", "Persisted firewall rule is invalid"),
        ("wrong_acl", "Persisted environment ACL is invalid"),
        ("acl_deny", "Persisted environment ACL rights are invalid"),
        ("acl_excess_rights", "Persisted environment ACL rights are invalid"),
    ),
)
def test_installer_persisted_postcondition_failure_is_fatal(
    tmp_path: Path, scenario: str, message: str
):
    result = run_installer_fake_harness(tmp_path, scenario=scenario)

    assert result.returncode != 0
    assert message in result.stderr
    assert "Windows deployment operations installation completed." not in result.stdout


def fake_monthly_cim_class(
    month_members: tuple[str, ...],
    *,
    enabled_type: str = "Boolean",
    days_type: str = "UInt16",
) -> str:
    properties = [
        ("Enabled", enabled_type),
        ("StartBoundary", "String"),
        ("DaysOfWeek", days_type),
        ("WeeksOfMonth", "UInt16"),
        *((name, "UInt16") for name in month_members),
    ]
    property_script = ",".join(
        f"[PSCustomObject]@{{ Name='{name}'; CimType='{cim_type}' }}"
        for name, cim_type in properties
    )
    return (
        "[PSCustomObject]@{ "
        "CimClassName='MSFT_TaskMonthlyDOWTrigger'; "
        "CimSystemProperties=[PSCustomObject]@{ "
        "Namespace='Root/Microsoft/Windows/TaskScheduler' }; "
        f"CimClassProperties=@({property_script}) }}"
    )


@pytest.mark.parametrize("month_name", ["MonthsOfYear", "MonthOfYear"])
def test_installer_monthly_schema_accepts_documented_or_local_month_member(
    month_name: str,
):
    result = run_ps(
        "& { "
        + installer_prelude()
        + f"; $class = {fake_monthly_cim_class((month_name,))}; "
        + "$actual = Resolve-OperationsMonthlyTriggerSchema -CimClass $class; "
        + f"if ($actual -cne '{month_name}') {{ exit 80 }} }}"
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "month_members",
    [(), ("MonthsOfYear", "MonthOfYear"), ("monthsofyear",)],
)
def test_installer_monthly_schema_rejects_neither_or_both_month_members(
    month_members: tuple[str, ...],
):
    result = run_ps(
        "& { "
        + installer_prelude()
        + f"; $class = {fake_monthly_cim_class(month_members)}; "
        + "try { Resolve-OperationsMonthlyTriggerSchema -CimClass $class | Out-Null; exit 81 } "
        + "catch { if ($_.Exception.Message -notlike "
        + "'*exactly one MonthOfYear/MonthsOfYear*') { exit 82 }; exit 0 } }"
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("class_script", "expected_message"),
    [
        (
            fake_monthly_cim_class(("MonthOfYear",)).replace(
                "[PSCustomObject]@{ Name='Enabled'; CimType='Boolean' },", ""
            ),
            "*missing required property: Enabled*",
        ),
        (
            fake_monthly_cim_class(("MonthOfYear",), days_type="String"),
            "*invalid CIM type for DaysOfWeek*",
        ),
    ],
)
def test_installer_monthly_schema_rejects_missing_or_wrong_typed_required_property(
    class_script: str, expected_message: str
):
    result = run_ps(
        "& { "
        + installer_prelude()
        + f"; $class = {class_script}; "
        + "try { Resolve-OperationsMonthlyTriggerSchema -CimClass $class | Out-Null; exit 83 } "
        + f"catch {{ if ($_.Exception.Message -notlike '{expected_message}') {{ exit 84 }}; exit 0 }} }}"
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("omit_enabled", "insert_base_type", "expected_message"),
    [
        (True, True, "*missing materialized property: Enabled*"),
        (False, False, "*missing required MSFT_TaskTrigger type*"),
    ],
)
def test_installer_monthly_trigger_rejects_missing_enabled_or_inherited_type(
    omit_enabled: bool, insert_base_type: bool, expected_message: str
):
    result = run_ps(
        "& { "
        + installer_prelude()
        + "; $properties = @{ StartBoundary='2026-08-02T04:00:00'; "
        + "DaysOfWeek=[uint16]1; WeeksOfMonth=[uint16]1; "
        + "MonthOfYear=[uint16]4095 }; "
        + ("" if omit_enabled else "$properties.Enabled = $true; ")
        + "$trigger = New-CimInstance -Namespace "
        + "'Root/Microsoft/Windows/TaskScheduler' "
        + "-ClassName MSFT_TaskMonthlyDOWTrigger -ClientOnly -Property $properties; "
        + (
            "$trigger.PSTypeNames.Insert(0, "
            "'Microsoft.Management.Infrastructure.CimInstance#MSFT_TaskTrigger'); "
            if insert_base_type
            else ""
        )
        + "try { Assert-OperationsMonthlyRestoreDrillTrigger -Trigger $trigger "
        + "-MonthPropertyName MonthOfYear "
        + "-ExpectedStartBoundary '2026-08-02T04:00:00'; exit 87 } "
        + f"catch {{ if ($_.Exception.Message -notlike '{expected_message}') "
        + "{ exit 88 }; exit 0 } }"
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("mutation", "field_name"),
    [
        ("$trigger.Enabled = $false", "Enabled"),
        ("$trigger.DaysOfWeek = [uint16]2", "DaysOfWeek"),
        ("$trigger.WeeksOfMonth = [uint16]2", "WeeksOfMonth"),
        ("$trigger.$monthName = [uint16]2047", "month"),
        ("$trigger.StartBoundary = '2026-08-02T05:00:00'", "StartBoundary"),
    ],
)
def test_installer_monthly_trigger_rejects_invalid_materialized_contract(
    mutation: str, field_name: str
):
    result = run_ps(
        "& { "
        + installer_prelude()
        + "; $trigger = New-OperationsMonthlyRestoreDrillTrigger "
        + "-StartBoundary ([datetime]'2026-08-02T04:00:00'); "
        + "$monthNames = @(@('MonthsOfYear','MonthOfYear') | Where-Object { "
        + "$null -ne $trigger.PSObject.Properties[$_] }); "
        + "if ($monthNames.Count -ne 1) { exit 89 }; $monthName = $monthNames[0]; "
        + mutation
        + "; $expectedField = if ('"
        + field_name
        + "' -eq 'month') { $monthName } else { '"
        + field_name
        + "' }; try { Assert-OperationsMonthlyRestoreDrillTrigger "
        + "-Trigger $trigger -MonthPropertyName $monthName "
        + "-ExpectedStartBoundary '2026-08-02T04:00:00'; exit 90 } "
        + "catch { if ($_.Exception.Message -cne "
        + "\"Invalid monthly trigger value: $expectedField.\") { exit 91 }; exit 0 } }"
    )

    assert result.returncode == 0, result.stderr


def test_installer_monthly_trigger_uses_exact_first_sunday_0400_contract_on_current_host():
    result = run_ps(
        "& { "
        + installer_prelude()
        + "; $boundary = '2026-08-02T04:00:00'; "
        + "$trigger = New-OperationsMonthlyRestoreDrillTrigger "
        + "-StartBoundary ([datetime]$boundary); "
        + "$monthNames = @(@('MonthsOfYear','MonthOfYear') | Where-Object { "
        + "$null -ne $trigger.PSObject.Properties[$_] }); "
        + "if ($trigger.GetType().FullName -cne "
        + "'Microsoft.Management.Infrastructure.CimInstance' -or "
        + "$trigger.CimClass.CimClassName -cne 'MSFT_TaskMonthlyDOWTrigger' -or "
        + "$trigger.PSTypeNames -notcontains "
        + "'Microsoft.Management.Infrastructure.CimInstance#MSFT_TaskTrigger' -or "
        + "$monthNames.Count -ne 1 -or -not $trigger.Enabled -or "
        + "[int]$trigger.DaysOfWeek -ne 1 -or "
        + "[int]$trigger.WeeksOfMonth -ne 1 -or "
        + "[int]$trigger.PSObject.Properties[$monthNames[0]].Value -ne 4095 -or "
        + "[string]$trigger.StartBoundary -cne $boundary) { exit 92 }; "
        + "$action = New-ScheduledTaskAction -Execute 'powershell.exe'; "
        + "New-ScheduledTask -Action $action -Trigger $trigger | Out-Null }"
    )

    assert result.returncode == 0, result.stderr


def test_installer_monthly_trigger_rejects_non_0400_start_boundary():
    result = run_ps(
        "& { "
        + installer_prelude()
        + "; try { New-OperationsMonthlyRestoreDrillTrigger "
        + "-StartBoundary ([datetime]'2026-08-02T05:00:00') | Out-Null; exit 93 } "
        + "catch { if ($_.Exception.Message -cne "
        + "'Monthly restore drill StartBoundary must be 04:00:00.') "
        + "{ exit 94 }; exit 0 } }"
    )

    assert result.returncode == 0, result.stderr


def test_operations_installer_builds_all_task_definitions_before_system_mutations():
    source = INSTALL.read_text(encoding="utf-8")
    definition_start = source.index("$taskDefinitions = @(")
    validation = source.index("New-ScheduledTask -Action $task.Action", definition_start)
    write_boundary = source.index(
        "# All preflight checks and in-memory task validation are above this line."
    )
    registration = source.index("Register-OperationsTask -Task $task", write_boundary)

    assert definition_start < validation < write_boundary < registration
    for mutation in (
        "New-Item -ItemType Directory",
        "icacls.exe",
        "Remove-NetFirewallRule",
        "New-NetFirewallRule",
    ):
        assert source.index(mutation) > write_boundary
    assert "-Action $task.Action -Trigger $task.Trigger" not in source[registration:]


def test_installer_port_ownership_rejects_wrong_host_port_after_exact_one_container():
    result = run_ps(
        "& { "
        + installer_prelude()
        + "; function Get-NetTCPConnection { "
        + "@([PSCustomObject]@{ LocalAddress='0.0.0.0'; OwningProcess=101 }) }; "
        + "function Invoke-External { param($FilePath, $ArgumentList) "
        + "$signature = $ArgumentList -join '|'; "
        + "if ($signature -eq "
        + "'compose|--project-directory|R|--file|C|--env-file|E|ps|-q|app') "
        + "{ return @('only-app-id') }; "
        + "if ($signature -eq "
        + "'inspect|--format|{{json .HostConfig.PortBindings}}|only-app-id') "
        + "{ return '{\"7860/tcp\":[{\"HostIp\":\"0.0.0.0\",\"HostPort\":\"17860\"}]}' }; "
        + "throw \"unexpected Docker path: $signature\" }; "
        + "$config = [PSCustomObject]@{ RepositoryRoot='R'; ComposeFile='C'; EnvFile='E' }; "
        + "try { Assert-Tcp7860IsFreeOrOwnedByComposeApp -Config $config; exit 85 } "
        + "catch { if ($_.Exception.Message -notlike "
        + "'*outside the current Compose app mapping*') { exit 86 }; exit 0 } }"
    )

    assert result.returncode == 0, result.stderr


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


@pytest.mark.parametrize(
    ("wildcard_address", "loopback_address"),
    [("0.0.0.0", "127.0.0.1"), ("::", "::1")],
)
def test_installer_listener_ownership_accepts_wildcard_and_loopback_forwarders(
    wildcard_address: str, loopback_address: str
):
    prelude = installer_prelude()
    result = run_ps(
        "& { "
        + prelude
        + "; $bindings = @([PSCustomObject]@{ HostIp='0.0.0.0'; HostPort='7860' }); "
        + "$listeners = @("
        + f"[PSCustomObject]@{{ LocalAddress='{wildcard_address}'; OwningProcess=101 }}, "
        + f"[PSCustomObject]@{{ LocalAddress='{loopback_address}'; OwningProcess=202 }}); "
        + "$processes = @{ 101=[PSCustomObject]@{ ProcessName='com.docker.backend' }; "
        + "202=[PSCustomObject]@{ ProcessName='wslrelay' } }; "
        + "if (-not (Test-ComposePortListenerOwnership -Listeners $listeners "
        + "-PortBindings $bindings -ProcessesById $processes)) { exit 66 } }"
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("owning_process", "process_table"),
    [
        (4, "@{ 4=[PSCustomObject]@{ ProcessName='System' } }"),
        (303, "@{}"),
    ],
)
def test_installer_listener_ownership_rejects_system_or_unresolved_process(
    owning_process: int, process_table: str
):
    prelude = installer_prelude()
    result = run_ps(
        "& { "
        + prelude
        + "; $bindings = @([PSCustomObject]@{ HostIp='0.0.0.0'; HostPort='7860' }); "
        + "$listeners = @("
        + "[PSCustomObject]@{ LocalAddress='0.0.0.0'; OwningProcess=101 }, "
        + f"[PSCustomObject]@{{ LocalAddress='127.0.0.1'; OwningProcess={owning_process} }}); "
        + "$processes = @{ 101=[PSCustomObject]@{ ProcessName='com.docker.backend' } }; "
        + f"$candidate = {process_table}; foreach ($key in $candidate.Keys) {{ $processes[$key] = $candidate[$key] }}; "
        + "if (Test-ComposePortListenerOwnership -Listeners $listeners "
        + "-PortBindings $bindings -ProcessesById $processes) { exit 67 } }"
    )

    assert result.returncode == 0, result.stderr


def test_installer_listener_ownership_does_not_expand_ipv6_hostip_wildcard_semantics():
    prelude = installer_prelude()
    result = run_ps(
        "& { "
        + prelude
        + "; $bindings = @([PSCustomObject]@{ HostIp='::'; HostPort='7860' }); "
        + "$listeners = @("
        + "[PSCustomObject]@{ LocalAddress='::'; OwningProcess=101 }, "
        + "[PSCustomObject]@{ LocalAddress='::1'; OwningProcess=202 }); "
        + "$processes = @{ 101=[PSCustomObject]@{ ProcessName='com.docker.backend' }; "
        + "202=[PSCustomObject]@{ ProcessName='wslrelay' } }; "
        + "if (Test-ComposePortListenerOwnership -Listeners $listeners "
        + "-PortBindings $bindings -ProcessesById $processes) { exit 72 } }"
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("container_ids", ["@()", "@('first','second')"])
def test_installer_port_ownership_rejects_missing_or_multiple_app_containers(
    container_ids: str,
):
    prelude = installer_prelude()
    result = run_ps(
        "& { "
        + prelude
        + "; function Get-NetTCPConnection { "
        + "@([PSCustomObject]@{ LocalAddress='0.0.0.0'; OwningProcess=101 }) }; "
        + "function Invoke-External { param($FilePath, $ArgumentList) "
        + f"if ($ArgumentList -contains 'ps') {{ return {container_ids} }}; "
        + "throw 'docker inspect must not run without exactly one app container' }; "
        + "$config = [PSCustomObject]@{ RepositoryRoot='R'; ComposeFile='C'; EnvFile='E' }; "
        + "try { Assert-Tcp7860IsFreeOrOwnedByComposeApp -Config $config; exit 68 } "
        + "catch { if ($_.Exception.Message -notlike '*exactly one container*') { exit 69 }; exit 0 } }"
    )

    assert result.returncode == 0, result.stderr


def test_installer_port_ownership_queries_only_expected_compose_app():
    prelude = installer_prelude()
    result = run_ps(
        "& { "
        + prelude
        + "; function Get-NetTCPConnection { "
        + "@([PSCustomObject]@{ LocalAddress='0.0.0.0'; OwningProcess=101 }) }; "
        + "function Invoke-External { param($FilePath, $ArgumentList) "
        + "$signature = $ArgumentList -join '|'; "
        + "if ($signature -eq 'compose|--project-directory|R|--file|C|--env-file|E|ps|-q|app') { return @() }; "
        + "throw \"wrong Compose service selector: $signature\" }; "
        + "$config = [PSCustomObject]@{ RepositoryRoot='R'; ComposeFile='C'; EnvFile='E' }; "
        + "try { Assert-Tcp7860IsFreeOrOwnedByComposeApp -Config $config; exit 70 } "
        + "catch { if ($_.Exception.Message -notlike '*exactly one container*') { exit 71 }; exit 0 } }"
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
