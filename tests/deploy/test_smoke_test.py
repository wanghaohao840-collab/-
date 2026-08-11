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


def test_compose_command_accepts_isolated_project_name():
    command = smoke_test._compose_command(
        Path("drill.env"), "assistant-drill-123"
    )

    assert command[:4] == [
        "docker",
        "compose",
        "--project-name",
        "assistant-drill-123",
    ]
    assert command[-2:] == ["--env-file", "drill.env"]


def test_deep_command_propagates_isolated_project_name(tmp_path: Path):
    command = _deep_command(tmp_path / "drill.env", "assistant-drill-123")

    assert command[:4] == [
        "docker",
        "compose",
        "--project-name",
        "assistant-drill-123",
    ]


@pytest.mark.parametrize(
    "project_name",
    ["UPPER", "-leading", "has.dot", "has space", "a" * 64],
)
def test_project_name_rejects_values_outside_exact_contract(project_name: str):
    with pytest.raises(SystemExit):
        smoke_test._parse_args(["--project-name", project_name])


def test_host_smoke_propagates_project_name_to_every_compose_call(
    tmp_path: Path, monkeypatch
):
    env_file = tmp_path / "drill.env"
    env_file.write_text(
        "APP_BIND_ADDRESS=127.0.0.1\nAPP_PORT=17860\n", encoding="utf-8"
    )
    commands: list[list[str]] = []

    def fake_run(command, label):
        commands.append(command)
        if "ps" in command:
            status = json.dumps(
                [
                    {"Service": "app", "State": "running", "Health": "healthy"},
                    {
                        "Service": "qdrant",
                        "State": "running",
                        "Health": "healthy",
                    },
                ]
            )
            return subprocess.CompletedProcess(command, 0, stdout=status, stderr="")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="/app/hello_agents/__init__.py\n",
            stderr="",
        )

    monkeypatch.setattr(smoke_test, "_run_command", fake_run)
    monkeypatch.setattr(smoke_test, "_check_app_http", lambda *_: None)

    assert (
        smoke_test.main(
            [
                "--env-file",
                str(env_file),
                "--project-name",
                "assistant-drill-123",
                "--deep",
            ]
        )
        == 0
    )
    assert len(commands) == 3
    assert all(
        command[:4]
        == ["docker", "compose", "--project-name", "assistant-drill-123"]
        for command in commands
    )


def test_search_marker_check_ignores_displayed_filename():
    marker = "Deployment smoke marker unique-123"
    search = f"来源: 文件: smoke-generated-id.txt\n内容摘要:\n{marker}"

    _require_search_marker(search, marker)


def test_search_marker_check_rejects_missing_content():
    with pytest.raises(SmokeFailure, match="marker missing from search"):
        _require_search_marker("unrelated retrieval", "unique marker")
