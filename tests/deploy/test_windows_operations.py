from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).parents[2]
MODULE = ROOT / "deploy" / "windows" / "Operations.Common.psm1"
START = ROOT / "deploy" / "windows" / "Start-Deployment.ps1"
HEALTH = ROOT / "deploy" / "windows" / "Test-DeploymentHealth.ps1"


def run_ps(script: str, *, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        cwd=ROOT,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def ps_quote(path: Path) -> str:
    return str(path).replace("'", "''")


def import_module() -> str:
    return f"Import-Module '{ps_quote(MODULE)}' -Force; "


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


def test_scripts_record_missing_env_failures_without_leaking_secret(tmp_path: Path):
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
            + ps_quote(state)
            + "' 'status.json'))) { exit 31 }"
        )

        output = result.stdout + result.stderr
        assert result.returncode == 0, output
        assert "must-not-leak" not in output
        status_text = (state / "status.json").read_text(encoding="utf-8-sig")
        log_text = (state / "operations.log").read_text(encoding="utf-8-sig")
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
