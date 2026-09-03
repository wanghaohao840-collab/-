from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[2]
MIGRATION = ROOT / "deploy" / "windows" / "Move-QdrantToVolume.ps1"


def ps_quote(value: Path | str) -> str:
    return str(value).replace("'", "''")


def test_migration_preserves_legacy_and_records_evidence_contract():
    source = MIGRATION.read_text(encoding="utf-8")
    assert "SupportsShouldProcess" in source
    assert "LegacyQdrantRoot" in source
    assert "Export-LegacyQdrantDirectory" in source
    assert "Get-ComposeQdrantInventory" in source
    assert "legacy_retained = $true" in source
    assert "Remove-Item -LiteralPath $legacy" not in source
    assert "docker volume rm" not in source.lower()


def test_migration_whatif_does_not_call_docker(tmp_path: Path):
    repository = tmp_path / "repo"
    data = repository / "data"
    legacy = data / "qdrant"
    legacy.mkdir(parents=True)
    (repository / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    env_file = repository / ".env"
    env_file.write_text(
        "DEPLOY_DATA_ROOT=data\nQDRANT_VOLUME_NAME=zhiyan_qdrant_data\n",
        encoding="utf-8",
    )
    script = (
        f"& '{ps_quote(MIGRATION)}' "
        f"-RepositoryRoot '{ps_quote(repository)}' "
        f"-EnvFile '{ps_quote(env_file)}' "
        f"-LegacyQdrantRoot '{ps_quote(legacy)}' -WhatIf | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"Changed":false' in result.stdout
    assert legacy.exists()
