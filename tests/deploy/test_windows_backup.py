from __future__ import annotations

import json
import hashlib
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest


ROOT = Path(__file__).parents[2]
WINDOWS = ROOT / "deploy" / "windows"
COMMON = WINDOWS / "Backup.Common.psm1"
BACKUP = WINDOWS / "Backup-Deployment.ps1"
RESTORE = WINDOWS / "Restore-Deployment.ps1"


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


def ps_quote(value: Path | str) -> str:
    return str(value).replace("'", "''")


def make_set(directory: Path, archive_name: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for suffix in ("", ".sha256", ".meta"):
        (directory / f"{archive_name}{suffix}").write_text("test", encoding="ascii")


def make_repository(tmp_path: Path) -> tuple[Path, Path, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    shutil.copyfile(ROOT / "compose.yaml", repository / "compose.yaml")
    data = tmp_path / "deployment-data"
    (data / "app").mkdir(parents=True)
    (data / "qdrant" / "collections").mkdir(parents=True)
    env_file = repository / "deploy.env"
    env_file.write_text(f"DEPLOY_DATA_ROOT={data}\n", encoding="utf-8")
    return repository, data, env_file


def write_checksum(archive: Path) -> None:
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (archive.parent / f"{archive.name}.sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="ascii"
    )


@pytest.mark.parametrize(
    ("prefix", "archive_names", "keep"),
    [
        (
            "assistant-",
            [f"assistant-202607{day:02d}T030000Z.tar.gz" for day in range(1, 11)],
            7,
        ),
        (
            "assistant-week-",
            [f"assistant-week-2026-W{week:02d}.tar.gz" for week in range(1, 7)],
            4,
        ),
    ],
)
def test_retention_keeps_newest_complete_sets(
    tmp_path: Path, prefix: str, archive_names: list[str], keep: int
):
    for archive_name in archive_names:
        make_set(tmp_path, archive_name)

    result = run_ps(
        f"Import-Module '{ps_quote(COMMON)}' -Force; "
        f"$plan = Get-RetentionPlan -Directory '{ps_quote(tmp_path)}' "
        f"-Prefix '{prefix}' -Keep {keep}; "
        "[PSCustomObject]@{ "
        "Keep=@($plan.Keep | ForEach-Object { [IO.Path]::GetFileName($_.Archive) }); "
        "Remove=@($plan.Remove | ForEach-Object { [IO.Path]::GetFileName($_.Archive) }) "
        "} | ConvertTo-Json -Compress"
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["Keep"] == list(reversed(archive_names[-keep:]))
    assert plan["Remove"] == list(reversed(archive_names[:-keep]))


def test_retention_ignores_incomplete_and_inexact_sets(tmp_path: Path):
    make_set(tmp_path, "assistant-20260701T030000Z.tar.gz")
    (tmp_path / "assistant-20260702T030000Z.tar.gz").write_text("partial")
    make_set(tmp_path, "assistant-20260703T030000Z.tar.gz.extra")

    result = run_ps(
        f"Import-Module '{ps_quote(COMMON)}' -Force; "
        f"$plan = Get-RetentionPlan -Directory '{ps_quote(tmp_path)}' "
        "-Prefix 'assistant-' -Keep 0; "
        "@($plan.Remove).Count"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"


def test_retention_rejects_reparse_point_sidecars(tmp_path: Path):
    target = tmp_path / "real-checksum"
    target.write_text("test", encoding="ascii")
    archive = "assistant-20260701T030000Z.tar.gz"
    (tmp_path / archive).write_text("test", encoding="ascii")
    (tmp_path / f"{archive}.meta").write_text("test", encoding="ascii")
    link = tmp_path / f"{archive}.sha256"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")

    result = run_ps(
        f"Import-Module '{ps_quote(COMMON)}' -Force; "
        f"$plan = Get-RetentionPlan -Directory '{ps_quote(tmp_path)}' "
        "-Prefix 'assistant-' -Keep 0; @($plan.Remove).Count"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0"


@pytest.mark.parametrize(
    ("date", "expected"),
    [
        ("2018-12-31T12:00:00Z", {"Year": 2019, "Week": 1}),
        ("2019-01-01T12:00:00Z", {"Year": 2019, "Week": 1}),
        ("2020-12-31T12:00:00Z", {"Year": 2020, "Week": 53}),
        ("2021-01-01T12:00:00Z", {"Year": 2020, "Week": 53}),
    ],
)
def test_iso_week_year_and_number_come_from_adjusted_thursday(
    date: str, expected: dict[str, int]
):
    result = run_ps(
        f"Import-Module '{ps_quote(COMMON)}' -Force; "
        f"Get-IsoWeekInfo -UtcDate ([DateTime]::Parse('{date}').ToUniversalTime()) "
        "| ConvertTo-Json -Compress"
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected


def test_backup_and_restore_scripts_have_static_safety_contracts():
    common = COMMON.read_text(encoding="utf-8")
    backup = BACKUP.read_text(encoding="utf-8")
    restore = RESTORE.read_text(encoding="utf-8")

    assert "Assert-SafePath" in common
    assert "ReparsePoint" in common
    assert "Remove-Item -LiteralPath" in common
    assert "Remove-Item -Recurse" not in common

    assert "finally" in backup
    assert "Get-FileHash -LiteralPath $archive -Algorithm SHA256" in backup
    assert "Test-PathOverlap" in backup
    assert "Get-RetentionPlan" in backup

    assert "OrdinalIgnoreCase" in restore
    assert "'-tzf'" in restore
    assert "'-tvzf'" in restore
    assert "ReparsePoint" in restore
    assert ".." in restore
    assert ".rollback-" in restore
    assert ".failed-" in restore
    assert "Rename-Item" in restore
    assert "Remove-Item -Recurse" not in restore


def test_backup_restore_round_trip_uses_fake_docker_and_retains_rollback(tmp_path: Path):
    if shutil.which("tar.exe") is None:
        pytest.skip("tar.exe is unavailable")

    repository, data, env_file = make_repository(tmp_path)
    (data / "app" / "marker.txt").write_text("original", encoding="utf-8")
    (data / "qdrant" / "collections" / "marker.txt").write_text(
        "original-qdrant", encoding="utf-8"
    )
    state = tmp_path / "state"
    backups = tmp_path / "backups"
    calls = tmp_path / "external-calls.jsonl"

    harness = tmp_path / "round-trip.ps1"
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$runner = {\n"
        "  param([string]$FilePath, [string[]]$ArgumentList)\n"
        f"  [PSCustomObject]@{{ FilePath=$FilePath; Args=$ArgumentList }} | ConvertTo-Json -Compress | Add-Content -LiteralPath '{ps_quote(calls)}'\n"
        "  if ($FilePath -eq 'docker') {\n"
        "    if ($ArgumentList -contains 'ps') { 'app'; 'qdrant' }\n"
        "    return\n"
        "  }\n"
        "  & $FilePath @ArgumentList\n"
        "  if ($LASTEXITCODE -ne 0) { throw \"$FilePath failed: $LASTEXITCODE\" }\n"
        "}\n"
        "$healthy = { param($Config) $true }\n"
        f"& '{ps_quote(BACKUP)}' -RepositoryRoot '{ps_quote(repository)}' -EnvFile '{ps_quote(env_file)}' -StateRoot '{ps_quote(state)}' -BackupRoot '{ps_quote(backups)}' -ExternalInvoker $runner -HealthProbe $healthy\n"
        "$archive = Get-ChildItem -LiteralPath "
        f"'{ps_quote(backups / 'daily')}' -Filter '*.tar.gz' | Select-Object -First 1\n"
        "if ($null -eq $archive) { throw 'backup archive missing' }\n"
        f"Set-Content -LiteralPath '{ps_quote(data / 'app' / 'marker.txt')}' -Value 'mutated'\n"
        f"& '{ps_quote(RESTORE)}' -Archive $archive.FullName -RepositoryRoot '{ps_quote(repository)}' -EnvFile '{ps_quote(env_file)}' -StateRoot '{ps_quote(state)}' -BackupRoot '{ps_quote(backups)}' -ExternalInvoker $runner -HealthProbe $healthy\n",
        encoding="utf-8",
    )

    result = run_ps(f"& '{ps_quote(harness)}'", timeout=90)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (data / "app" / "marker.txt").read_text(encoding="utf-8-sig").strip() == "original"
    assert (data / "qdrant" / "collections" / "marker.txt").read_text(
        encoding="utf-8-sig"
    ).strip() == "original-qdrant"
    rollbacks = list(tmp_path.glob("deployment-data.rollback-*"))
    assert len(rollbacks) == 1
    assert (rollbacks[0] / "app" / "marker.txt").read_text(
        encoding="utf-8-sig"
    ).strip() == "mutated"
    recorded = [json.loads(line) for line in calls.read_text().splitlines()]
    docker = [call["Args"] for call in recorded if call["FilePath"] == "docker"]
    assert any("stop" in call for call in docker)
    assert any("start" in call for call in docker)


def test_backup_archive_failure_restarts_prior_services_and_skips_retention(
    tmp_path: Path,
):
    repository, data, env_file = make_repository(tmp_path)
    (data / "app" / "marker.txt").write_text("live", encoding="utf-8")
    backups = tmp_path / "backups"
    daily = backups / "daily"
    for day in range(1, 9):
        make_set(daily, f"assistant-202606{day:02d}T030000Z.tar.gz")
    calls = tmp_path / "calls.jsonl"

    result = run_ps(
        "$runner = { param([string]$FilePath, [string[]]$ArgumentList) "
        f"[PSCustomObject]@{{FilePath=$FilePath;Args=$ArgumentList}} | ConvertTo-Json -Compress | Add-Content -LiteralPath '{ps_quote(calls)}'; "
        "if ($FilePath -eq 'docker') { if ($ArgumentList -contains 'ps') { 'app'; 'qdrant' }; return }; "
        "throw 'forced archive failure' }; "
        "try { "
        f"& '{ps_quote(BACKUP)}' -RepositoryRoot '{ps_quote(repository)}' "
        f"-EnvFile '{ps_quote(env_file)}' -StateRoot '{ps_quote(tmp_path / 'state')}' "
        f"-BackupRoot '{ps_quote(backups)}' -ExternalInvoker $runner -HealthProbe {{ $true }}; exit 92 "
        "} catch { exit 0 }"
    )

    assert result.returncode == 0, result.stderr
    recorded = [json.loads(line) for line in calls.read_text().splitlines()]
    docker = [call["Args"] for call in recorded if call["FilePath"] == "docker"]
    stop = next(call for call in docker if "stop" in call)
    start = next(call for call in docker if "start" in call)
    assert stop[stop.index("stop") + 1 :] == ["app", "qdrant"]
    assert start[start.index("start") + 1 :] == ["app", "qdrant"]
    assert len(list(daily.glob("*.tar.gz"))) == 8


def test_backup_rejects_reparse_ancestor_before_creating_backup_root(tmp_path: Path):
    repository, data, env_file = make_repository(tmp_path)
    (data / "app" / "marker.txt").write_text("live", encoding="utf-8")
    target = tmp_path / "real-backup-parent"
    target.mkdir()
    junction = tmp_path / "backup-parent-link"
    created = run_ps(
        f"New-Item -ItemType Junction -Path '{ps_quote(junction)}' "
        f"-Target '{ps_quote(target)}' | Out-Null"
    )
    if created.returncode != 0:
        pytest.skip(f"junctions are unavailable: {created.stderr}")
    calls = tmp_path / "external-called"
    backup_root = junction / "must-not-exist"

    try:
        result = run_ps(
            "$runner = { param($FilePath, $ArgumentList) "
            f"Set-Content -LiteralPath '{ps_quote(calls)}' -Value called }}; "
            f"& '{ps_quote(BACKUP)}' -RepositoryRoot '{ps_quote(repository)}' "
            f"-EnvFile '{ps_quote(env_file)}' -StateRoot '{ps_quote(tmp_path / 'state')}' "
            f"-BackupRoot '{ps_quote(backup_root)}' -ExternalInvoker $runner "
            "-HealthProbe { $true }"
        )

        assert result.returncode != 0
        assert "reparse" in (result.stdout + result.stderr).lower()
        assert not (target / "must-not-exist").exists()
        assert not calls.exists()
    finally:
        run_ps(f"Remove-Item -LiteralPath '{ps_quote(junction)}' -Force")


@pytest.mark.parametrize(
    ("members", "verbose"),
    [
        (["../escape"], ["-rw-r--r-- user/group 1 2026-01-01 00:00 ../escape"]),
        (["/absolute"], ["-rw-r--r-- user/group 1 2026-01-01 00:00 /absolute"]),
        ([r"C:\escape"], [r"-rw-r--r-- user/group 1 2026-01-01 00:00 C:\escape"]),
        (["./app/link"], ["lrwxrwxrwx user/group 0 2026-01-01 00:00 ./app/link -> ../../escape"]),
        (["./app/link"], ["hrw-r--r-- user/group 0 2026-01-01 00:00 ./app/link link to ./other"]),
    ],
)
def test_restore_rejects_traversal_and_links_before_stopping_services(
    tmp_path: Path, members: list[str], verbose: list[str]
):
    repository, _, env_file = make_repository(tmp_path)
    backups = tmp_path / "backups"
    archive = backups / "daily" / "assistant-20260701T030000Z.tar.gz"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"synthetic archive")
    write_checksum(archive)
    calls = tmp_path / "calls.jsonl"
    members_json = json.dumps(members).replace("'", "''")
    verbose_json = json.dumps(verbose).replace("'", "''")

    result = run_ps(
        f"$members = ConvertFrom-Json '{members_json}'; $verbose = ConvertFrom-Json '{verbose_json}'; "
        "$runner = { param([string]$FilePath, [string[]]$ArgumentList) "
        f"[PSCustomObject]@{{FilePath=$FilePath;Args=$ArgumentList}} | ConvertTo-Json -Compress | Add-Content -LiteralPath '{ps_quote(calls)}'; "
        "if ($ArgumentList -contains '-tzf') { $members; return }; "
        "if ($ArgumentList -contains '-tvzf') { $verbose; return }; throw 'extraction must not run' }; "
        "try { "
        f"& '{ps_quote(RESTORE)}' -Archive '{ps_quote(archive)}' "
        f"-RepositoryRoot '{ps_quote(repository)}' -EnvFile '{ps_quote(env_file)}' "
        f"-StateRoot '{ps_quote(tmp_path / 'state')}' -BackupRoot '{ps_quote(backups)}' "
        "-ExternalInvoker $runner -HealthProbe { $true }; exit 93 } catch { exit 0 }"
    )

    assert result.returncode == 0, result.stderr
    recorded = [json.loads(line) for line in calls.read_text().splitlines()]
    assert all(call["FilePath"] != "docker" for call in recorded)
    assert all("-xzf" not in call["Args"] for call in recorded)


def test_failed_post_swap_health_restores_old_data_and_retains_diagnostics(
    tmp_path: Path,
):
    if shutil.which("tar.exe") is None:
        pytest.skip("tar.exe is unavailable")
    repository, data, env_file = make_repository(tmp_path)
    (data / "app" / "marker.txt").write_text("current", encoding="utf-8")
    source = tmp_path / "source"
    (source / "app").mkdir(parents=True)
    (source / "qdrant").mkdir(parents=True)
    (source / "app" / "marker.txt").write_text("candidate", encoding="utf-8")
    backups = tmp_path / "backups"
    archive = backups / "daily" / "assistant-20260701T030000Z.tar.gz"
    archive.parent.mkdir(parents=True)
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(source / "app", arcname="app")
        bundle.add(source / "qdrant", arcname="qdrant")
    write_checksum(archive)

    result = run_ps(
        "$script:healthCalls = 0; "
        "$runner = { param([string]$FilePath, [string[]]$ArgumentList) "
        "if ($FilePath -eq 'docker') { if ($ArgumentList -contains 'ps') { 'app'; 'qdrant' }; return }; "
        "& $FilePath @ArgumentList; if ($LASTEXITCODE -ne 0) { throw 'tar failed' } }; "
        "$health = { param($Config) $script:healthCalls++; return ($script:healthCalls -gt 1) }; "
        "try { "
        f"& '{ps_quote(RESTORE)}' -Archive '{ps_quote(archive)}' "
        f"-RepositoryRoot '{ps_quote(repository)}' -EnvFile '{ps_quote(env_file)}' "
        f"-StateRoot '{ps_quote(tmp_path / 'state')}' -BackupRoot '{ps_quote(backups)}' "
        "-ExternalInvoker $runner -HealthProbe $health; exit 94 } catch { exit 0 }"
    )

    assert result.returncode == 0, result.stderr
    assert (data / "app" / "marker.txt").read_text(encoding="utf-8-sig") == "current"
    failed = [path for path in tmp_path.glob("deployment-data.failed-*") if path.is_dir()]
    diagnostics = list(tmp_path.glob("deployment-data.failed-*.diagnostic"))
    assert len(failed) == 1, result.stdout + result.stderr
    assert len(diagnostics) == 1
    assert (failed[0] / "app" / "marker.txt").read_text(encoding="utf-8-sig") == "candidate"


def test_second_stop_failure_does_not_gate_post_swap_filesystem_rollback(
    tmp_path: Path,
):
    if shutil.which("tar.exe") is None:
        pytest.skip("tar.exe is unavailable")
    repository, data, env_file = make_repository(tmp_path)
    (data / "app" / "marker.txt").write_text("current", encoding="utf-8")
    source = tmp_path / "source"
    (source / "app").mkdir(parents=True)
    (source / "qdrant").mkdir(parents=True)
    (source / "app" / "marker.txt").write_text("candidate", encoding="utf-8")
    backups = tmp_path / "backups"
    archive = backups / "daily" / "assistant-20260701T030000Z.tar.gz"
    archive.parent.mkdir(parents=True)
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(source / "app", arcname="app")
        bundle.add(source / "qdrant", arcname="qdrant")
    write_checksum(archive)

    result = run_ps(
        "$global:task4HealthCalls = 0; $global:task4StopCalls = 0; "
        "$runner = { param([string]$FilePath, [string[]]$ArgumentList) "
        "if ($FilePath -eq 'docker') { "
        "if ($ArgumentList -contains 'ps') { 'app'; 'qdrant'; return }; "
        "if ($ArgumentList -contains 'stop') { $global:task4StopCalls++; "
        "if ($global:task4StopCalls -eq 2) { throw 'forced second stop failure' } }; return }; "
        "& $FilePath @ArgumentList; if ($LASTEXITCODE -ne 0) { throw 'tar failed' } }; "
        "$health = { param($Config) $global:task4HealthCalls++; return ($global:task4HealthCalls -gt 1) }; "
        f"& '{ps_quote(RESTORE)}' -Archive '{ps_quote(archive)}' "
        f"-RepositoryRoot '{ps_quote(repository)}' -EnvFile '{ps_quote(env_file)}' "
        f"-StateRoot '{ps_quote(tmp_path / 'state')}' -BackupRoot '{ps_quote(backups)}' "
        "-ExternalInvoker $runner -HealthProbe $health"
    )

    assert result.returncode != 0
    assert (data / "app" / "marker.txt").read_text(encoding="utf-8-sig") == "current"
    failed = [path for path in tmp_path.glob("deployment-data.failed-*") if path.is_dir()]
    diagnostics = list(tmp_path.glob("deployment-data.failed-*.diagnostic"))
    assert len(failed) == 1, result.stdout + result.stderr
    assert len(diagnostics) == 1
    assert (failed[0] / "app" / "marker.txt").read_text(encoding="utf-8-sig") == "candidate"
    assert "forced second stop failure" in diagnostics[0].read_text(encoding="utf-8-sig")


def test_restore_rejects_checksum_mismatch_before_external_commands(tmp_path: Path):
    backups = tmp_path / "backups"
    archive = backups / "daily" / "assistant-20260701T030000Z.tar.gz"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"not an archive")
    (archive.parent / f"{archive.name}.sha256").write_text(
        f"{'0' * 64}  {archive.name}\n", encoding="ascii"
    )
    repository, _, env_file = make_repository(tmp_path)
    calls = tmp_path / "called"

    result = run_ps(
        "$runner = { param($FilePath, $ArgumentList) "
        f"Set-Content -LiteralPath '{ps_quote(calls)}' -Value called }}; "
        f"& '{ps_quote(RESTORE)}' -Archive '{ps_quote(archive)}' "
        f"-RepositoryRoot '{ps_quote(repository)}' -EnvFile '{ps_quote(env_file)}' "
        f"-StateRoot '{ps_quote(tmp_path / 'state')}' -BackupRoot '{ps_quote(backups)}' "
        "-ExternalInvoker $runner -HealthProbe { $true }"
    )

    assert result.returncode != 0
    assert "checksum mismatch" in (result.stdout + result.stderr).lower()
    assert not calls.exists()
