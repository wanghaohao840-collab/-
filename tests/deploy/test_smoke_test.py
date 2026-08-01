import json
import subprocess
from pathlib import Path

import pytest

from deploy import smoke_test
from deploy.smoke_test import (
    SmokeFailure,
    _deep_command,
    _require_search_marker,
    parse_compose_status,
    parse_env_file,
)


def test_parse_env_file_ignores_comments_and_strips_quotes(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        'APP_PORT=17860\n# ignored\nLLM_BASE_URL="http://llm.local/v1"\n',
        encoding="utf-8",
    )

    assert parse_env_file(env_file) == {
        "APP_PORT": "17860",
        "LLM_BASE_URL": "http://llm.local/v1",
    }


def test_parse_compose_status_accepts_json_lines():
    raw = "\n".join(
        [
            json.dumps(
                {"Service": "app", "State": "running", "Health": "healthy"}
            ),
            json.dumps(
                {"Service": "qdrant", "State": "running", "Health": "healthy"}
            ),
        ]
    )

    assert parse_compose_status(raw) == {
        "app": ("running", "healthy"),
        "qdrant": ("running", "healthy"),
    }


def test_parse_compose_status_accepts_json_array():
    raw = json.dumps(
        [
            {"Service": "app", "State": "running", "Health": "healthy"},
            {"Service": "qdrant", "State": "running", "Health": "healthy"},
        ]
    )

    assert parse_compose_status(raw)["qdrant"] == ("running", "healthy")


def test_parse_compose_status_exposes_unhealthy_required_service():
    raw = json.dumps(
        {"Service": "qdrant", "State": "running", "Health": "unhealthy"}
    )

    status = parse_compose_status(raw)
    assert status["qdrant"] == ("running", "unhealthy")


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
