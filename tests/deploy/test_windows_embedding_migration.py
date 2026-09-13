from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "deploy" / "windows" / "Invoke-EmbeddingMigration.ps1"
INSTALL = ROOT / "deploy" / "windows" / "Install-Operations.ps1"


def quote(value: Path) -> str:
    return str(value).replace("'", "''")


def test_controller_contract_keeps_activation_order_and_deep_smoke() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "SupportsShouldProcess" in source
    assert "Backup-Deployment.ps1" in source
    assert source.index("'stop', 'app'") < source.index("deploy.embedding_migrate")
    assert source.index("deploy.embedding_migrate") < source.index("deploy.embedding_cutover', 'begin")
    provider_change = source.rindex("Set-EmbeddingProvider")
    assert source.index("deploy.embedding_cutover', 'apply") < provider_change
    assert provider_change < source.index("deploy.embedding_cutover', 'complete")
    assert "'deploy\\smoke_test.py')" in source
    assert "'--env-file', $config.EnvFile, '--deep'" in source
    assert "'--entrypoint', 'python'" in source
    assert source.index("'--env-file', $config.EnvFile, '--deep'") < source.index(
        "Remove-Item -LiteralPath $environmentBackup"
    )
    assert "Post-activation verification failed; application remains stopped" in source
    assert "'graph'" not in source
    assert "neo4j" not in source.lower()


def test_scheduled_operations_run_with_hidden_windows() -> None:
    source = INSTALL.read_text(encoding="utf-8")
    assert "-WindowStyle Hidden" in source


def test_health_and_login_recovery_respect_the_shared_maintenance_lock() -> None:
    health = (ROOT / "deploy" / "windows" / "Test-DeploymentHealth.ps1").read_text(
        encoding="utf-8"
    )
    startup = (ROOT / "deploy" / "windows" / "Start-Deployment.ps1").read_text(
        encoding="utf-8"
    )
    for source in (health, startup):
        assert "Enter-OperationsLock" in source
        assert "Exit-OperationsLock" in source
        assert "maintenance" in source


def test_whatif_is_non_mutating_and_does_not_require_docker(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    env_file = repository / ".env"
    env_file.write_text("DEPLOY_DATA_ROOT=data\n", encoding="utf-8")
    quality = repository / "quality.json"
    quality.write_text("{}", encoding="utf-8")
    command = (
        f"& '{quote(SCRIPT)}' -RepositoryRoot '{quote(repository)}' "
        f"-EnvFile '{quote(env_file)}' -QualityReport '{quote(quality)}' "
        "-MigrationId test-migration -WhatIf | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert '"Changed":false' in result.stdout
    assert env_file.read_text(encoding="utf-8") == "DEPLOY_DATA_ROOT=data\n"
