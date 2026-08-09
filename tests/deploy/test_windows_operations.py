from __future__ import annotations

import json
from pathlib import Path
import subprocess


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
