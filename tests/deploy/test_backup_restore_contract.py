from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_backup_script_is_cold_and_excludes_secrets():
    source = (ROOT / "deploy" / "backup.sh").read_text(encoding="utf-8")

    assert "docker compose" in source
    assert "stop" in source
    assert "trap" in source
    assert "sha256sum" in source
    assert "tar -C" in source
    assert "--volumes" not in source


def test_restore_script_validates_and_keeps_a_rollback():
    source = (ROOT / "deploy" / "restore.sh").read_text(encoding="utf-8")

    assert "sha256sum -c" in source
    assert "tar -tzf" in source
    assert "rollback" in source
    assert "rm -rf" not in source
    assert "docker compose" in source


def test_deployment_readme_documents_restart_and_restore_commands():
    source = (ROOT / "deploy" / "README.md").read_text(encoding="utf-8")

    assert "docker compose" in source
    assert "backup.sh" in source
    assert "restore.sh" in source
    assert "单副本" in source
    assert "防火墙" in source
