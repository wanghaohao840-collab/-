from pathlib import Path


def test_root_readme_exposes_the_supported_deployment_path():
    source = (Path(__file__).parents[2] / "README.md").read_text(encoding="utf-8")

    assert "Docker 单节点部署" in source
    assert "docker compose --env-file deploy/.env up -d --build" in source
    assert "deploy/README.md" in source
    assert "Neo4j" in source
    assert "单副本" in source


def test_operator_docs_describe_windows_operations_contract():
    root = Path(__file__).parents[2]
    windows = (root / "deploy" / "windows" / "README.md").read_text(encoding="utf-8")
    deploy = (root / "deploy" / "README.md").read_text(encoding="utf-8")
    env_example = (root / "deploy" / ".env.example").read_text(encoding="utf-8")

    for value in (
        "Install-Operations.ps1",
        "Start-ScheduledTask",
        "PythonSelfAgent-LoginRecovery",
        "PythonSelfAgent-Health",
        "PythonSelfAgent-DailyBackup",
        "PythonSelfAgent-MonthlyRestoreDrill",
        "03:00",
        "7 daily",
        "4 weekly",
        "first Sunday",
        "04:00",
        "D:\\python_self_agent_backups",
        "Backup-Deployment.ps1",
        "Restore-Deployment.ps1",
        "Invoke-RestoreDrill.ps1",
        "Update-Deployment.ps1",
        "Uninstall-Operations.ps1",
        "Get-NetFirewallRule",
        "Get-ScheduledTask",
        "Private",
        "LocalSubnet",
        "Public",
        "refus",
        "operations.log",
        "status.json",
        "reports",
        "rollback",
        "does not delete",
    ):
        assert value.lower() in windows.lower()

    assert "docker compose --env-file deploy/.env up -d --build" in deploy
    assert "deploy/windows/README.md" in deploy
    assert "DEPLOY_STATE_ROOT=./deploy-state" in env_example
    assert "DEPLOY_BACKUP_ROOT=D:/python_self_agent_backups" in env_example
    assert "OPERATIONS_NOTIFY_COOLDOWN_MINUTES=30" in env_example
