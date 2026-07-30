import json
from pathlib import Path

from deploy.smoke_test import parse_compose_status, parse_env_file


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
