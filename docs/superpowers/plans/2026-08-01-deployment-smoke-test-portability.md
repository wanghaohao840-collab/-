# Deployment Smoke Test Portability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the public Docker smoke-test command complete successfully on Windows and Linux while preserving secret sanitization and cleanup.

**Architecture:** Keep the existing host orchestrator and private container runner. Make subprocess decoding explicitly UTF-8, isolate `/app` import configuration to the deep child command, and validate retrieval with a unique content marker instead of a presentation-layer filename.

**Tech Stack:** Python 3.11+, `subprocess`, pytest 8.4.1, Docker Desktop/Engine, Docker Compose, Qdrant.

## Global Constraints

- Modify only `deploy/smoke_test.py` and `tests/deploy/test_smoke_test.py` during implementation.
- Do not change application retrieval behavior, Docker topology, secrets, or persisted user data.
- Keep `--inside-deep` private and keep all existing cleanup and secret-sanitization behavior.
- The unchanged public acceptance command must pass from Windows without `python -X utf8` or a caller-supplied `PYTHONPATH`.
- Preserve unrelated dirty-worktree changes and stage files explicitly.

---

### Task 1: Decode Docker CLI Output Portably

**Files:**
- Modify: `deploy/smoke_test.py:56-67`
- Test: `tests/deploy/test_smoke_test.py`

**Interfaces:**
- Consumes: `_run_command(command: list[str], label: str)` and `PROJECT_ROOT`.
- Produces: `_run_command(...) -> subprocess.CompletedProcess[str]` with UTF-8-decoded `stdout` and `stderr`, replacing isolated malformed bytes without changing the exit code.

- [ ] **Step 1: Write the failing subprocess-decoding test**

Add these imports and test to `tests/deploy/test_smoke_test.py`:

```python
import subprocess

from deploy import smoke_test


def test_run_command_decodes_output_as_utf8(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="容器 healthy",
            stderr="",
        )

    monkeypatch.setattr(smoke_test.subprocess, "run", fake_run)

    result = smoke_test._run_command(["docker", "version"], "docker")

    assert result.stdout == "容器 healthy"
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"
```

- [ ] **Step 2: Run the test and verify the locale bug is represented**

Run:

```powershell
python -m pytest tests/deploy/test_smoke_test.py::test_run_command_decodes_output_as_utf8 -q
```

Expected: FAIL with `KeyError: 'encoding'`.

- [ ] **Step 3: Make subprocess decoding explicit**

In `_run_command()`, replace `text=True` with:

```python
            encoding="utf-8",
            errors="replace",
```

The complete call remains:

```python
        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
```

- [ ] **Step 4: Run the focused test**

Run:

```powershell
python -m pytest tests/deploy/test_smoke_test.py::test_run_command_decodes_output_as_utf8 -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit the portable decoding change**

```powershell
git add -- deploy/smoke_test.py tests/deploy/test_smoke_test.py
git commit -m "fix: decode Docker smoke output as UTF-8"
```

---

### Task 2: Make the Deep Runner Importable and Content-Based

**Files:**
- Modify: `deploy/smoke_test.py:143-206,269-282`
- Test: `tests/deploy/test_smoke_test.py`

**Interfaces:**
- Consumes: `_compose_command(env_file: Path, *args: str) -> list[str]`, `SmokeFailure`, and `uuid.uuid4()`.
- Produces: `_deep_command(env_file: Path) -> list[str]` and `_require_search_marker(search: str, marker: str) -> None`.

- [ ] **Step 1: Write failing tests for the deep child command and marker check**

Extend the imports from `deploy.smoke_test`:

```python
import pytest

from deploy.smoke_test import (
    SmokeFailure,
    _deep_command,
    _require_search_marker,
    parse_compose_status,
    parse_env_file,
)
```

Add:

```python
def test_deep_command_sets_container_project_root(tmp_path: Path):
    env_file = tmp_path / ".env"

    command = _deep_command(env_file)

    assert command[-8:] == [
        "exec",
        "-T",
        "-e",
        "PYTHONPATH=/app",
        "app",
        "python",
        "/app/deploy/smoke_test.py",
        "--inside-deep",
    ]


def test_search_marker_check_ignores_displayed_filename():
    marker = "Deployment smoke marker unique-123"
    search = f"来源: 文件: smoke-generated-id.txt\n内容摘要:\n{marker}"

    _require_search_marker(search, marker)


def test_search_marker_check_rejects_missing_content():
    with pytest.raises(SmokeFailure, match="marker missing from search"):
        _require_search_marker("unrelated retrieval", "unique marker")
```

- [ ] **Step 2: Run the new tests and verify the missing interfaces**

Run:

```powershell
python -m pytest tests/deploy/test_smoke_test.py -q
```

Expected: collection ERROR because `_deep_command` and `_require_search_marker` do not exist.

- [ ] **Step 3: Add the deep command helper**

Immediately after `_compose_command()`, add:

```python
def _deep_command(env_file: Path) -> list[str]:
    return _compose_command(
        env_file,
        "exec",
        "-T",
        "-e",
        "PYTHONPATH=/app",
        "app",
        "python",
        "/app/deploy/smoke_test.py",
        "--inside-deep",
    )
```

- [ ] **Step 4: Add the content-marker helper**

Immediately before `run_deep_inside()`, add:

```python
def _require_search_marker(search: str, marker: str) -> None:
    if marker not in search:
        raise SmokeFailure(f"marker missing from search: {search}")
```

- [ ] **Step 5: Use a unique marker in the internal deep run**

Replace the fixed document text, search query, filename assertion, and LLM question with:

```python
        marker = f"Deployment smoke marker {uuid.uuid4().hex}"
        document_path.write_text(
            f"{marker}: qdrant persistence and source citations work.",
            encoding="utf-8",
        )
        loaded = assistant.load_document(
            str(document_path),
            document_id=document_id,
            original_name="deployment-smoke.txt",
        )
        if loaded.startswith("❌"):
            raise SmokeFailure(loaded)
        search = assistant.search(marker, limit=5)
        _require_search_marker(search, marker)
        answer = assistant.ask(
            f"Repeat this exact deployment smoke marker: {marker}"
        )
        if answer.startswith("❌"):
            raise SmokeFailure(answer)
```

Keep the existing `finally` block unchanged, including document cleanup,
logout, and temporary-directory removal.

- [ ] **Step 6: Route the public deep mode through the helper**

Replace the inline deep Compose command in `main()` with:

```python
            _run_command(
                _deep_command(env_file),
                "deep smoke",
            )
```

- [ ] **Step 7: Run all smoke-test unit tests**

Run:

```powershell
python -m pytest tests/deploy/test_smoke_test.py -q
```

Expected: `8 passed`.

- [ ] **Step 8: Run all deployment contract tests**

Run:

```powershell
python -m pytest tests/deploy -q
```

Expected: all tests pass.

- [ ] **Step 9: Commit the deep-runner fix**

```powershell
git add -- deploy/smoke_test.py tests/deploy/test_smoke_test.py
git commit -m "fix: make deep deployment smoke test portable"
```

---

### Task 3: Rebuild and Run the Public Acceptance Command

**Files:**
- Verify: `Dockerfile`
- Verify: `compose.yaml`
- Verify: `deploy/.env` (ignored; never print or stage)
- Verify: `deploy/smoke_test.py`

**Interfaces:**
- Consumes: the updated application image, the running `app` and `qdrant` services, and the configured ignored `deploy/.env`.
- Produces: a zero-exit live acceptance result from the unchanged public command.

- [ ] **Step 1: Confirm the secret file remains ignored**

Run:

```powershell
git check-ignore -v deploy/.env
```

Expected: `.gitignore` reports `deploy/.env` as ignored.

- [ ] **Step 2: Rebuild and recreate the application container**

Run:

```powershell
$env:DEPLOY_ENV_FILE='deploy/.env'
docker compose --env-file deploy/.env up -d --build app
```

Expected: `python_self_agent-app` builds and the app container starts after healthy Qdrant.

- [ ] **Step 3: Run the public basic smoke command without locale flags**

Run:

```powershell
python deploy/smoke_test.py --env-file deploy/.env
```

Expected output includes:

```text
PASS: app and qdrant are running and healthy
PASS: Gradio HTTP endpoint
PASS: Qdrant readiness, data write, and local import
```

- [ ] **Step 4: Run the unchanged public deep command**

Run:

```powershell
python deploy/smoke_test.py --env-file deploy/.env --deep
```

Expected output additionally includes:

```text
PASS: temporary document import, retrieval, and LLM answer
```

Expected exit code: `0`.

- [ ] **Step 5: Verify service health after cleanup**

Run:

```powershell
docker compose --env-file deploy/.env ps
```

Expected: `app` and `qdrant` are both `healthy` and no smoke-test container remains.

- [ ] **Step 6: Audit the implementation diff**

Run:

```powershell
git diff --check HEAD~2..HEAD
git status --short
```

Expected: no whitespace errors; only the user's pre-existing unrelated working-tree changes remain.
