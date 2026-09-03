from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[2]
MODULE = ROOT / "deploy" / "windows" / "QdrantVolume.Common.psm1"
OPERATIONS = ROOT / "deploy" / "windows" / "Operations.Common.psm1"
BACKUP_COMMON = ROOT / "deploy" / "windows" / "Backup.Common.psm1"
BACKUP = ROOT / "deploy" / "windows" / "Backup-Deployment.ps1"
RESTORE = ROOT / "deploy" / "windows" / "Restore-Deployment.ps1"
DRILL = ROOT / "deploy" / "windows" / "Invoke-RestoreDrill.ps1"
UPDATE = ROOT / "deploy" / "windows" / "Update-Deployment.ps1"


def ps_quote(value: Path | str) -> str:
    return str(value).replace("'", "''")


def run_powershell(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def test_module_rejects_unsafe_docker_resource_names():
    script = (
        f"Import-Module '{ps_quote(OPERATIONS)}' -Force; "
        f"Import-Module '{ps_quote(MODULE)}' -Force; "
        "$bad=@('/','..','bad name','bad;name','-leading'); "
        "foreach($name in $bad){ try { Assert-SafeDockerResourceName -Name $name | Out-Null; throw 'accepted' } catch { if ($_.Exception.Message -eq 'accepted') { throw } } }; "
        "Assert-SafeDockerResourceName -Name 'zhiyan_qdrant_data'"
    )
    result = run_powershell(script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "zhiyan_qdrant_data" in result.stdout


def test_operations_config_defaults_and_validates_qdrant_volume(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (repository / "data").mkdir()
    env_file = repository / ".env"
    env_file.write_text("DEPLOY_DATA_ROOT=data\n", encoding="utf-8")
    script = (
        f"Import-Module '{ps_quote(OPERATIONS)}' -Force; "
        f"(Get-OperationsConfig -RepositoryRoot '{ps_quote(repository)}' -EnvFile '{ps_quote(env_file)}').QdrantVolumeName"
    )
    result = run_powershell(script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "zhiyan_qdrant_data"

    env_file.write_text(
        "DEPLOY_DATA_ROOT=data\nQDRANT_VOLUME_NAME=bad name\n", encoding="utf-8"
    )
    result = run_powershell(script)
    assert result.returncode != 0
    assert "safe Docker volume name" in result.stderr


def test_volume_commands_use_read_only_source_mounts():
    source = MODULE.read_text(encoding="utf-8")
    assert "type=volume,source=$safeName,target=/source,readonly" in source
    assert "type=bind,source=$sourcePath,target=/source,readonly" in source
    assert "type=bind,source=$archiveParent,target=/backup,readonly" in source
    assert "RequiredPrefix" in source


def test_backup_set_tracks_complete_qdrant_volume_sidecars(tmp_path: Path):
    archive = tmp_path / "assistant-20260903T030000Z.tar.gz"
    for path in (
        archive,
        Path(f"{archive}.sha256"),
        Path(f"{archive}.meta"),
        Path(f"{archive}.qdrant-volume.tar.gz"),
        Path(f"{archive}.qdrant-volume.tar.gz.sha256"),
    ):
        path.write_text("evidence", encoding="utf-8")
    script = (
        f"Import-Module '{ps_quote(OPERATIONS)}' -Force; "
        f"Import-Module '{ps_quote(BACKUP_COMMON)}' -Force; "
        f"$set=@(Get-CompleteBackupSets -Directory '{ps_quote(tmp_path)}' -Prefix 'assistant-'); "
        "if($set.Count -ne 1){throw 'set missing'}; "
        "$set[0] | Select-Object Archive,QdrantArchive,QdrantChecksum | ConvertTo-Json -Compress"
    )
    result = run_powershell(script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "qdrant-volume.tar.gz" in result.stdout


def test_backup_restore_and_drill_treat_volume_payload_as_first_class_state():
    backup = BACKUP.read_text(encoding="utf-8")
    restore = RESTORE.read_text(encoding="utf-8")
    drill = DRILL.read_text(encoding="utf-8")
    update = UPDATE.read_text(encoding="utf-8")

    assert "Export-QdrantVolume" in backup
    assert "qdrant_volume_name" in backup
    assert "qdrant-volume.tar.gz" in backup
    assert "Clear-QdrantVolume" in restore
    assert "Import-QdrantVolume" in restore
    assert "qdrant-rollback-" in restore
    assert "QDRANT_VOLUME_NAME" in drill
    assert "zhiyan-drill-" in drill
    assert "Remove-QdrantVolume" in drill
    assert "missing Qdrant volume evidence" in update
    assert "Qdrant backup checksum" in update
