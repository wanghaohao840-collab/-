from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[2]
MODULE = ROOT / "deploy" / "windows" / "QdrantVolume.Common.psm1"
OPERATIONS = ROOT / "deploy" / "windows" / "Operations.Common.psm1"


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
