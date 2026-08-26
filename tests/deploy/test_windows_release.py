from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).parents[2]
UPDATE = ROOT / "deploy" / "windows" / "Update-Deployment.ps1"
APP_IMAGE = "python_self_agent-app:local"
QDRANT_IMAGE = "python_self_agent-qdrant:local"
SECRET = "release-test-secret"


def run_ps(script: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
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


def make_repository(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    data = tmp_path / "deployment-data"
    data.mkdir()
    env_file = repository / "deploy.env"
    env_file.write_text(f"DEPLOY_DATA_ROOT={data}\n", encoding="utf-8")
    backups = tmp_path / "backups"
    archive = backups / "daily" / "assistant-20260811T030000Z.tar.gz"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"verified pre-upgrade backup")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    Path(f"{archive}.sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="ascii"
    )
    Path(f"{archive}.meta").write_text("{}", encoding="utf-8")
    return repository, env_file, backups, archive


def write_harness(
    path: Path,
    repository: Path,
    env_file: Path,
    state: Path,
    backups: Path,
    archive: Path,
    calls: Path,
    failure: str,
) -> None:
    path.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"$global:releaseCalls = '{ps_quote(calls)}'\n"
        f"$global:releaseArchive = '{ps_quote(archive)}'\n"
        f"$global:releaseFailure = '{failure}'\n"
        "$global:candidateUpFailed = $false\n"
        "$global:candidateDeployed = $false\n"
        "$global:appExists = $true\n"
        "$global:qdrantExists = $true\n"
        "$global:appRunning = $true\n"
        "$global:qdrantRunning = $true\n"
        "$global:appImage = 'old'\n"
        "$global:qdrantImage = 'old'\n"
        "$global:dataMutated = $false\n"
        "$runner = {\n"
        "  param([string]$FilePath, [string[]]$ArgumentList)\n"
        "  [PSCustomObject]@{ FilePath=$FilePath; Args=@($ArgumentList); AppExists=$global:appExists; QdrantExists=$global:qdrantExists; AppRunning=$global:appRunning; QdrantRunning=$global:qdrantRunning; AppImage=$global:appImage; QdrantImage=$global:qdrantImage; DataMutated=$global:dataMutated } | ConvertTo-Json -Compress | Add-Content -LiteralPath $global:releaseCalls\n"
        "  $joined = @($ArgumentList) -join ' '\n"
        "  if ($FilePath -eq 'git') { ' M deploy/.env'; return }\n"
        "  if ($FilePath -eq 'docker' -and $ArgumentList -contains 'ps') {\n"
        "    if ($global:releaseFailure -eq 'baseline') { 'app|running|unhealthy'; 'qdrant|running|healthy' }\n"
        "    elseif (@('candidate-health','candidate-recreate') -contains $global:releaseFailure -and $global:candidateDeployed -and $global:appImage -eq 'candidate') { 'app|running|unhealthy'; 'qdrant|running|unhealthy' }\n"
        "    else {\n"
        "      if ($global:appExists) { if ($global:appRunning) { 'app|running|healthy' } else { 'app|exited|' } }\n"
        "      if ($global:qdrantExists) { if ($global:qdrantRunning) { 'qdrant|running|healthy' } else { 'qdrant|exited|' } }\n"
        "    }; return\n"
        "  }\n"
        "  if ($FilePath -eq 'docker' -and $ArgumentList -contains 'inspect') {\n"
        "    if ($ArgumentList[-1] -eq 'python_self_agent-app:local') { 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' }\n"
        "    else { 'sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' }; return\n"
        "  }\n"
        "  if ($joined -match 'Backup-Deployment\\.ps1') { [PSCustomObject]@{ Archive=$global:releaseArchive }; return }\n"
        f"  if ($FilePath -eq 'docker' -and $ArgumentList -contains 'tag' -and (($global:releaseFailure -eq 'retag-app' -and $ArgumentList[-1] -eq '{APP_IMAGE}') -or ($global:releaseFailure -eq 'retag-qdrant' -and $ArgumentList[-1] -eq '{QDRANT_IMAGE}'))) {{ throw 'LLM_API_KEY={SECRET} forced rollback retag failure' }}\n"
        "  if ($joined -match 'Restore-Deployment\\.ps1') {\n"
        "    if (-not $global:appExists -or -not $global:qdrantExists -or -not $global:appRunning -or -not $global:qdrantRunning) { throw 'restore invoked without running services' }\n"
        "    if ($global:appImage -ne 'old' -or $global:qdrantImage -ne 'old') { throw 'restore invoked before old images were recreated' }\n"
        f"    if ($global:releaseFailure -eq 'restore') {{ throw 'LLM_API_KEY={SECRET} forced restore failure' }}\n"
        "    $global:appRunning = $true; $global:qdrantRunning = $true; $global:dataMutated = $false; return\n"
        "  }\n"
        f"  if ($global:releaseFailure -eq 'scan' -and $FilePath -eq 'docker' -and $ArgumentList -contains 'scout') {{ throw 'LLM_API_KEY={SECRET} forced scan failure' }}\n"
        f"  if ($global:releaseFailure -eq 'candidate-up' -and $FilePath -eq 'docker' -and $ArgumentList -contains 'up' -and -not $global:candidateUpFailed) {{ $global:candidateUpFailed=$true; $global:candidateDeployed=$true; $global:appExists=$true; $global:appRunning=$true; $global:appImage='candidate'; $global:qdrantExists=$false; $global:qdrantRunning=$false; $global:qdrantImage=$null; $global:dataMutated=$true; throw 'LLM_API_KEY={SECRET} forced candidate up failure' }}\n"
        f"  if (@('deep-smoke','retag-app','retag-qdrant','restore') -contains $global:releaseFailure -and $FilePath -like '*python.exe' -and $ArgumentList -contains '--deep') {{ throw 'LLM_API_KEY={SECRET} forced deep smoke failure' }}\n"
        "  if ($FilePath -eq 'docker' -and $ArgumentList -contains 'stop') { $global:appRunning = $false; $global:qdrantRunning = $false; return }\n"
        f"  if ($FilePath -eq 'docker' -and $ArgumentList -contains 'up' -and $ArgumentList -contains '--force-recreate') {{ if ($global:releaseFailure -eq 'candidate-recreate') {{ throw 'LLM_API_KEY={SECRET} forced old-image recreation failure' }}; $global:appExists=$true; $global:qdrantExists=$true; $global:appRunning=$true; $global:qdrantRunning=$true; $global:appImage='old'; $global:qdrantImage='old'; return }}\n"
        "  if ($FilePath -eq 'docker' -and $ArgumentList -contains 'up') { $global:candidateDeployed=$true; $global:appExists=$true; $global:qdrantExists=$true; $global:appRunning=$true; $global:qdrantRunning=$true; $global:appImage='candidate'; $global:qdrantImage='candidate'; $global:dataMutated=$true; return }\n"
        "}\n"
        "try {\n"
        f"  & '{ps_quote(UPDATE)}' -RepositoryRoot '{ps_quote(repository)}' -EnvFile '{ps_quote(env_file)}' -StateRoot '{ps_quote(state)}' -BackupRoot '{ps_quote(backups)}' -HealthTimeoutSeconds 1 -CommandRunner $runner -SkipNotification\n"
        "} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 91 }\n",
        encoding="utf-8",
    )


def run_update(tmp_path: Path, failure: str = "none"):
    repository, env_file, backups, archive = make_repository(tmp_path)
    state = tmp_path / "state"
    calls = tmp_path / "calls.jsonl"
    harness = tmp_path / "harness.ps1"
    write_harness(
        harness, repository, env_file, state, backups, archive, calls, failure
    )
    result = run_ps(f"& '{ps_quote(harness)}'", timeout=90)
    recorded = []
    if calls.exists():
        for line in calls.read_text(encoding="utf-8-sig").splitlines():
            item = json.loads(line)
            if isinstance(item["Args"], str):
                item["Args"] = [item["Args"]]
            recorded.append(item)
    return result, recorded, state, archive


def actions(calls: list[dict[str, object]]) -> list[str]:
    return [" ".join([str(call["FilePath"]), *map(str, call["Args"])]) for call in calls]


def find_action(items: list[str], *tokens: str, start: int = 0) -> int:
    return next(
        index
        for index, item in enumerate(items[start:], start=start)
        if all(token in item for token in tokens)
    )


def test_release_inputs_are_digest_pinned_and_stably_named():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    qdrant = (ROOT / "deploy" / "qdrant.Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert (
        "python:3.11-slim-bookworm@sha256:"
        "d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3"
        in dockerfile
    )
    assert (
        "qdrant/qdrant:v1.18.3@sha256:"
        "0bd98fa7977f1e75694779359ca4e212822e5a71334e28421182f72f209d5286"
        in qdrant
    )
    assert (
        "neo4j:5.26.28-community@sha256:"
        "ff32db30b2baff97971e441b46bfd9c832c1b62c970398ef579244c06b21d357"
        in compose
    )
    assert "image: python_self_agent-app:local" in compose
    assert "image: python_self_agent-qdrant:local" in compose


def test_update_script_exists():
    assert UPDATE.is_file()


def test_deployment_workflow_is_pinned_and_offline_from_secrets():
    workflow = (ROOT / ".github" / "workflows" / "deployment.yml").read_text(
        encoding="utf-8"
    )

    assert "ubuntu-24.04" in workflow
    assert '"requirements.txt"' in workflow
    assert "permissions:" in workflow
    assert "contents: read" in workflow
    assert "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd" in workflow
    assert (
        "aquasec/trivy:0.72.0@sha256:"
        "cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f"
        in workflow
    )
    assert "pytest tests/deploy" in workflow
    assert 'sudo ln -s "$(command -v pwsh)" /usr/local/bin/powershell.exe' in workflow
    assert workflow.count('- "web/**"') == 2
    assert workflow.count('- "server.py"') == 2
    assert workflow.count('- "api/**"') == 2
    assert workflow.count('- ".dockerignore"') == 2
    assert workflow.count('- "app/**"') == 2
    assert workflow.count('- "assistants/**"') == 2
    assert workflow.count('- "hello_agents/**"') == 2
    assert workflow.count('- "ui/**"') == 2
    assert "name: Verify React application" in workflow
    assert "working-directory: web" in workflow
    assert "npm ci" in workflow
    assert "npm test" in workflow
    assert "npm run build:app" in workflow
    assert "docker compose --env-file deploy/.env config --quiet" in workflow
    assert "docker compose --env-file deploy/.env build app qdrant" in workflow
    assert "python_self_agent-app:local" in workflow
    assert "python_self_agent-qdrant:local" in workflow
    assert workflow.count("--ignore-unfixed --severity CRITICAL --exit-code 1") == 2
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in workflow
    assert ".trivy-cache" in workflow
    assert workflow.count('--user "$(id -u):$(id -g)"') == 2
    assert workflow.count("--group-add") == 2
    assert workflow.count("--cache-dir /tmp/trivy-cache") == 2
    assert "trap 'rm -rf .trivy-cache' EXIT" in workflow
    assert "actions/cache" not in workflow
    assert "smoke_test.py" not in workflow
    assert "--deep" not in workflow
    assert "docker compose --env-file deploy/.env up" not in workflow
    for forbidden in ("secrets.", "LLM_", "OPENAI_", "NEO4J_"):
        assert forbidden not in workflow


def test_success_orders_all_release_gates_and_writes_report(tmp_path: Path):
    result, calls, state, archive = run_update(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    items = actions(calls)
    test_at = find_action(items, "pytest", "tests", "deploy")
    config_at = find_action(items, "docker compose", "config", "--quiet")
    backup_at = find_action(items, "Backup-Deployment.ps1")
    tag_at = find_action(items, "docker image tag", "rollback-")
    build_at = find_action(items, "docker compose", "build", "app", "qdrant")
    scout_at = find_action(
        items,
        "docker scout cves",
        "--only-severity critical",
        "--exit-code",
        "--only-fixed",
    )
    up_at = find_action(items, "docker compose", "up", "-d", "--no-build")
    default_at = find_action(items, "deploy\\smoke_test.py")
    deep_at = find_action(items, "deploy\\smoke_test.py", "--deep")
    assert test_at < config_at < backup_at < tag_at < build_at
    assert build_at < scout_at < up_at < default_at < deep_at
    assert all("--ignore-base" not in item for item in items)

    report_file = next((state / "reports").glob("update-*.json"))
    report = json.loads(report_file.read_text(encoding="utf-8-sig"))
    assert report["status"] == "succeeded"
    assert report["backup_archive"] == str(archive)
    assert report["rollback_images"]["app"].startswith(
        "python_self_agent-app:rollback-"
    )
    assert report["rollback_images"]["qdrant"].startswith(
        "python_self_agent-qdrant:rollback-"
    )


def test_scan_failure_restores_images_only_and_redacts(tmp_path: Path):
    result, calls, state, _ = run_update(tmp_path, "scan")

    assert result.returncode != 0
    items = actions(calls)
    retags = [
        item
        for item in items
        if "docker image tag" in item
        and "sha256:" in item
        and item.endswith(":local")
    ]
    assert len(retags) == 2
    assert all("Restore-Deployment.ps1" not in item for item in items)
    assert not any("docker compose" in item and " stop " in item for item in items)
    assert SECRET not in result.stdout + result.stderr
    report_text = next((state / "reports").glob("update-*.json")).read_text(
        encoding="utf-8-sig"
    )
    assert SECRET not in report_text
    assert json.loads(report_text)["failure_stage"] == "scan-app"


def test_unhealthy_baseline_stops_before_any_mutation(tmp_path: Path):
    result, calls, state, _ = run_update(tmp_path, "baseline")

    assert result.returncode != 0
    items = actions(calls)
    assert len(items) == 1
    assert "docker compose" in items[0] and " ps " in items[0]
    assert not (state / "reports").exists()
    source = UPDATE.read_text(encoding="utf-8")
    assert "10GB" in source


def test_partial_candidate_up_failure_runs_conservative_full_rollback(tmp_path: Path):
    result, calls, state, archive = run_update(tmp_path, "candidate-up")

    assert result.returncode != 0
    items = actions(calls)
    candidate = find_action(items, "docker compose", "up", "-d", "--no-build")
    app_tag = find_action(
        items,
        "docker image tag",
        "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        APP_IMAGE,
        start=candidate + 1,
    )
    qdrant_tag = find_action(
        items,
        "docker image tag",
        "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        QDRANT_IMAGE,
        start=app_tag + 1,
    )
    recreate = find_action(
        items,
        "docker compose",
        "up",
        "-d",
        "--no-build",
        "--force-recreate",
        "app",
        "qdrant",
        start=qdrant_tag + 1,
    )
    restore = find_action(
        items,
        "Restore-Deployment.ps1",
        "-Archive",
        str(archive),
        start=recreate + 1,
    )
    smoke = find_action(items, "deploy\\smoke_test.py", start=restore + 1)
    assert candidate < app_tag < qdrant_tag < recreate < restore < smoke
    assert calls[recreate]["AppExists"] is True
    assert calls[recreate]["QdrantExists"] is False
    assert calls[restore]["AppRunning"] is True
    assert calls[restore]["QdrantRunning"] is True
    assert calls[restore]["AppExists"] is True
    assert calls[restore]["QdrantExists"] is True
    assert calls[restore]["AppImage"] == "old"
    assert calls[restore]["QdrantImage"] == "old"
    assert calls[restore]["DataMutated"] is True
    assert not any("docker compose" in item and " stop " in item for item in items)
    assert "--deep" not in items[smoke]
    assert calls[smoke]["AppImage"] == "old"
    assert calls[smoke]["QdrantImage"] == "old"
    assert calls[smoke]["DataMutated"] is False
    report_text = next((state / "reports").glob("update-*.json")).read_text(
        encoding="utf-8-sig"
    )
    assert SECRET not in report_text
    report = json.loads(report_text)
    assert report["failure_stage"] == "candidate-up"
    assert report["rollback_succeeded"] is True


def test_old_image_recreation_failure_stops_before_data_restore(
    tmp_path: Path,
):
    result, calls, state, _ = run_update(tmp_path, "candidate-recreate")

    assert result.returncode != 0
    items = actions(calls)
    candidate = find_action(items, "docker compose", "up", "-d", "--no-build")
    recreate = find_action(
        items,
        "docker compose",
        "up",
        "--force-recreate",
        "app",
        "qdrant",
        start=candidate + 1,
    )
    assert all("Restore-Deployment.ps1" not in item for item in items[recreate + 1 :])
    assert not any("deploy\\smoke_test.py" in item for item in items[recreate + 1 :])
    report_text = next((state / "reports").glob("update-*.json")).read_text(
        encoding="utf-8-sig"
    )
    assert SECRET not in report_text
    report = json.loads(report_text)
    assert report["rollback_succeeded"] is False
    assert report["rollback_failure_priority"] == "high"


def test_candidate_health_failure_recreates_old_images_before_restoring_data(
    tmp_path: Path,
):
    result, calls, state, archive = run_update(tmp_path, "candidate-health")

    assert result.returncode != 0
    items = actions(calls)
    candidate = find_action(items, "docker compose", "up", "-d", "--no-build")
    app_tag = find_action(items, "docker image tag", "sha256:aaaaaaaa", APP_IMAGE, start=candidate + 1)
    qdrant_tag = find_action(items, "docker image tag", "sha256:bbbbbbbb", QDRANT_IMAGE, start=app_tag + 1)
    recreate = find_action(items, "docker compose", "up", "--force-recreate", start=qdrant_tag + 1)
    restore = find_action(items, "Restore-Deployment.ps1", "-Archive", str(archive), start=recreate + 1)
    smoke = find_action(items, "deploy\\smoke_test.py", start=restore + 1)
    assert candidate < app_tag < qdrant_tag < recreate < restore < smoke
    assert calls[restore]["AppImage"] == "old"
    assert calls[restore]["QdrantImage"] == "old"
    assert calls[restore]["DataMutated"] is True
    assert calls[smoke]["AppImage"] == "old"
    assert calls[smoke]["QdrantImage"] == "old"
    assert calls[smoke]["DataMutated"] is False
    report = json.loads(
        next((state / "reports").glob("update-*.json")).read_text(encoding="utf-8-sig")
    )
    assert report["failure_stage"] == "candidate-health"
    assert report["rollback_succeeded"] is True


def test_deep_smoke_failure_retages_restores_then_force_recreates(tmp_path: Path):
    result, calls, state, archive = run_update(tmp_path, "deep-smoke")

    assert result.returncode != 0
    items = actions(calls)
    deep = find_action(items, "deploy\\smoke_test.py", "--deep")
    app_tag = find_action(
        items,
        "docker image tag",
        "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        APP_IMAGE,
        start=deep + 1,
    )
    qdrant_tag = find_action(
        items,
        "docker image tag",
        "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        QDRANT_IMAGE,
        start=app_tag + 1,
    )
    recreate = find_action(
        items, "docker compose", "up", "--force-recreate", start=qdrant_tag + 1
    )
    restore = find_action(
        items, "Restore-Deployment.ps1", "-Archive", str(archive), start=recreate + 1
    )
    smoke = find_action(items, "deploy\\smoke_test.py", start=restore + 1)
    assert app_tag < qdrant_tag < recreate < restore < smoke
    assert calls[restore]["AppRunning"] is True
    assert calls[restore]["QdrantRunning"] is True
    assert not any("docker compose" in item and " stop " in item for item in items)
    assert "--deep" not in items[smoke]
    report_text = next((state / "reports").glob("update-*.json")).read_text(
        encoding="utf-8-sig"
    )
    assert SECRET not in report_text
    assert json.loads(report_text)["rollback_succeeded"] is True


@pytest.mark.parametrize("failure", ["retag-app", "retag-qdrant", "restore"])
def test_failed_rollback_prerequisite_stops_compensation(
    tmp_path: Path, failure: str
):
    result, calls, state, _ = run_update(tmp_path, failure)

    assert result.returncode != 0
    items = actions(calls)
    deep = find_action(items, "deploy\\smoke_test.py", "--deep")
    after_failure = items[deep + 1 :]
    if failure.startswith("retag-"):
        assert all("Restore-Deployment.ps1" not in item for item in after_failure)
        assert not any("--force-recreate" in item for item in after_failure)
    else:
        restore = find_action(items, "Restore-Deployment.ps1", start=deep + 1)
        assert any("--force-recreate" in item for item in after_failure[:restore])
        assert not any("docker compose" in item and " up " in item for item in items[restore + 1 :])
    assert not any("docker compose" in item and " stop " in item for item in after_failure)
    report_text = next((state / "reports").glob("update-*.json")).read_text(
        encoding="utf-8-sig"
    )
    assert SECRET not in report_text
    report = json.loads(report_text)
    assert report["rollback_succeeded"] is False
    assert report["rollback_failure_priority"] == "high"


def test_update_records_git_status_without_git_writes(tmp_path: Path):
    result, calls, _, _ = run_update(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    git_calls = [call["Args"] for call in calls if call["FilePath"] == "git"]
    assert len(git_calls) == 1
    assert git_calls[0][-2:] == ["status", "--short"]
    source = UPDATE.read_text(encoding="utf-8").lower()
    for forbidden in ("git pull", "git reset", "git checkout", "git clean"):
        assert forbidden not in source


def test_overlapping_update_fails_closed_before_release_commands(tmp_path: Path):
    repository, env_file, backups, _ = make_repository(tmp_path)
    state = tmp_path / "state"
    calls = tmp_path / "external-called"
    operations = ROOT / "deploy" / "windows" / "Operations.Common.psm1"

    result = run_ps(
        f"Import-Module '{ps_quote(operations)}' -Force; "
        f"$held = Enter-OperationsLock -StateRoot '{ps_quote(state)}'; "
        "try { "
        "$runner = { param($FilePath, $ArgumentList) "
        f"Set-Content -LiteralPath '{ps_quote(calls)}' -Value called }}; "
        "try { "
        f"& '{ps_quote(UPDATE)}' -RepositoryRoot '{ps_quote(repository)}' "
        f"-EnvFile '{ps_quote(env_file)}' -StateRoot '{ps_quote(state)}' "
        f"-BackupRoot '{ps_quote(backups)}' -CommandRunner $runner -SkipNotification; "
        "exit 92 } catch { if ($_.Exception.Message -notmatch 'already in progress') "
        "{ throw }; 'blocked' } "
        "} finally { Exit-OperationsLock -Lock $held }"
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "blocked"
    assert not calls.exists()
    assert not (state / "reports").exists()


def test_update_has_powershell_51_parser_contract():
    result = run_ps(
        "$errors=$null; "
        f"[void][Management.Automation.Language.Parser]::ParseFile('{ps_quote(UPDATE)}',[ref]$null,[ref]$errors); "
        "if ($errors.Count -gt 0) { $errors | ForEach-Object { $_.Message }; exit 1 }"
    )

    assert result.returncode == 0, result.stdout + result.stderr
