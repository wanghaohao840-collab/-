from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SERVICES = ("app", "qdrant")


class SmokeFailure(RuntimeError):
    pass


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {'"', "'"}
        ):
            value = value[1:-1]
        values[key.strip()] = value
    return values


def parse_compose_status(raw: str) -> dict[str, tuple[str, str]]:
    stripped = raw.strip()
    if not stripped:
        return {}

    try:
        decoded = json.loads(stripped)
        items = decoded if isinstance(decoded, list) else [decoded]
    except json.JSONDecodeError:
        items = [json.loads(line) for line in stripped.splitlines() if line.strip()]

    status: dict[str, tuple[str, str]] = {}
    for item in items:
        service = item.get("Service") or item.get("service")
        state = item.get("State") or item.get("state") or ""
        health = item.get("Health") or item.get("health") or ""
        if service:
            status[str(service)] = (str(state), str(health))
    return status


def _compose_command(env_file: Path, *args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        *args,
    ]


def _run_command(command: list[str], label: str) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SmokeFailure("docker executable not found") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise SmokeFailure(f"{label}: {detail}")
    return result


def _service_status(env_file: Path) -> dict[str, tuple[str, str]]:
    result = _run_command(
        _compose_command(env_file, "ps", "--format", "json"),
        "compose status",
    )
    return parse_compose_status(result.stdout)


def _wait_for_services(env_file: Path, timeout: float = 180.0) -> None:
    deadline = time.monotonic() + timeout
    last_status: dict[str, tuple[str, str]] = {}
    while time.monotonic() < deadline:
        last_status = _service_status(env_file)
        if all(
            last_status.get(service) == ("running", "healthy")
            for service in REQUIRED_SERVICES
        ):
            return
        time.sleep(2)
    raise SmokeFailure(f"services did not become healthy: {last_status}")


def _host_for_request(bind_address: str) -> str:
    if bind_address in {"", "0.0.0.0"}:
        return "127.0.0.1"
    if bind_address in {"::", "[::]"}:
        return "[::1]"
    return bind_address


def _check_app_http(bind_address: str, port: str) -> None:
    host = _host_for_request(bind_address)
    for path in ("/", "/config"):
        url = f"http://{host}:{port}{path}"
        try:
            with urlopen(url, timeout=5) as response:
                if not 200 <= response.status < 400:
                    raise SmokeFailure(
                        f"application returned HTTP {response.status} for {path}"
                    )
        except (OSError, URLError, ValueError) as exc:
            raise SmokeFailure(
                f"application HTTP check failed for {path}: {exc}"
            ) from exc


def _check_inside_container(env_file: Path) -> None:
    code = """
from pathlib import Path
from urllib.request import urlopen
import hello_agents
import uuid

with urlopen("http://qdrant:6333/readyz", timeout=5) as response:
    assert response.status == 200, response.status

data_root = Path("/app/data")
probe = data_root / f".smoke-{uuid.uuid4().hex}.tmp"
probe.write_text("ok", encoding="utf-8")
assert probe.read_text(encoding="utf-8") == "ok"
probe.unlink()

source = Path(hello_agents.__file__).resolve()
assert source == Path("/app/hello_agents/__init__.py"), source
print(source)
""".strip()
    result = _run_command(
        _compose_command(
            env_file,
            "exec",
            "-T",
            "app",
            "python",
            "-c",
            code,
        ),
        "container checks",
    )
    if "/app/hello_agents/__init__.py" not in result.stdout.replace("\\", "/"):
        raise SmokeFailure("container imported hello_agents from an unexpected path")


def run_deep_inside() -> int:
    from app.database import initialize_database
    from app.session import SessionRegistry
    from app.storage import UserStorage

    temp_root = Path(tempfile.mkdtemp(prefix="deployment-smoke-"))
    registry = None
    token = None
    assistant = None
    failure: Exception | None = None
    try:
        db_path = temp_root / "app.db"
        data_root = temp_root / "data"
        initialize_database(db_path)
        storage = UserStorage(data_root)
        registry = SessionRegistry(db_path=db_path, storage=storage)
        username = f"smoke-{uuid.uuid4().hex[:12]}"
        token = registry.register(username, "Smoke password 123")
        session = registry.get_session(token)
        assistant = session.assistant
        document_id = f"smoke-{uuid.uuid4().hex}"
        document_path = storage.document_path(
            session.user_id,
            document_id,
            ".txt",
        )
        document_path.write_text(
            "Deployment smoke marker: qdrant persistence and source citations work.",
            encoding="utf-8",
        )
        loaded = assistant.load_document(
            str(document_path),
            document_id=document_id,
            original_name="deployment-smoke.txt",
        )
        if loaded.startswith("❌"):
            raise SmokeFailure(loaded)
        search = assistant.search("qdrant persistence", limit=5)
        if "deployment-smoke.txt" not in search:
            raise SmokeFailure(f"smoke source missing from search: {search}")
        answer = assistant.ask("What is the deployment smoke marker?")
        if answer.startswith("❌"):
            raise SmokeFailure(answer)
    except Exception as exc:
        failure = exc
    finally:
        if assistant is not None:
            try:
                cleanup_result = assistant.clear_all_documents()
                if cleanup_result.startswith("❌") and failure is None:
                    failure = SmokeFailure(cleanup_result)
            except Exception as exc:
                if failure is None:
                    failure = exc
        if registry is not None and token is not None:
            registry.logout(token)
        shutil.rmtree(temp_root, ignore_errors=True)

    if failure is not None:
        raise failure
    return 0


def _sensitive_values(env: dict[str, str]) -> list[str]:
    values = []
    for key, value in env.items():
        upper = key.upper()
        if value and any(word in upper for word in ("KEY", "PASSWORD", "TOKEN", "SECRET")):
            values.append(value)
    return sorted(values, key=len, reverse=True)


def _sanitize(text: str, env: dict[str, str]) -> str:
    sanitized = text
    for value in _sensitive_values(env):
        sanitized = sanitized.replace(value, "***")
    sanitized = re.sub(r"://[^/@\s]+@", "://***@", sanitized)
    return sanitized[:2000]


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the Docker deployment")
    parser.add_argument("--env-file", default="deploy/.env")
    parser.add_argument("--deep", action="store_true")
    parser.add_argument("--inside-deep", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.inside_deep:
        try:
            return run_deep_inside()
        except Exception as exc:
            print(
                f"FAIL: deep smoke: {_sanitize(str(exc), dict(os.environ))}",
                file=sys.stderr,
            )
            return 1

    env_file = Path(args.env_file).resolve()
    env: dict[str, str] = {}
    try:
        if not env_file.is_file():
            raise SmokeFailure(f"environment file not found: {env_file}")
        env = parse_env_file(env_file)
        _wait_for_services(env_file)
        print("PASS: app and qdrant are running and healthy")

        _check_app_http(
            env.get("APP_BIND_ADDRESS", "0.0.0.0"),
            env.get("APP_PORT", "7860"),
        )
        print("PASS: Gradio HTTP endpoint")

        _check_inside_container(env_file)
        print("PASS: Qdrant readiness, data write, and local import")

        if args.deep:
            _run_command(
                _compose_command(
                    env_file,
                    "exec",
                    "-T",
                    "app",
                    "python",
                    "/app/deploy/smoke_test.py",
                    "--inside-deep",
                ),
                "deep smoke",
            )
            print("PASS: temporary document import, retrieval, and LLM answer")
        return 0
    except Exception as exc:
        print(f"FAIL: {_sanitize(str(exc), env)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
