# Single-Node Docker Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a reproducible single-Linux-server Docker Compose deployment for the Gradio document-learning assistant, with Qdrant by default, optional Neo4j, health checks, smoke tests, and cold backup/restore.

**Architecture:** Keep the existing UI → Session/Runtime → Assistant → Tool → Storage boundaries. Add a small environment-driven launch configuration, a non-root application image, a Qdrant derivative image that adds only `wget` for `/readyz`, and one Compose bridge network with only the application port published. Persist application, Qdrant, and optional Neo4j data under a host-controlled directory; keep the app at one process/one replica.

**Tech Stack:** Python 3.11 slim-bookworm, existing `requirements.txt`, Gradio, Docker Compose v2, Qdrant `v1.18.2`, Neo4j `5.26.28-community`, POSIX shell, Python standard library, pytest.

## Global Constraints

- Default local launch remains `127.0.0.1:7860`; container launch uses `0.0.0.0:7860`.
- The default Compose stack is `app` + Qdrant; Neo4j is only enabled by the `graph` profile.
- Only the application port is published to the host; Qdrant and Neo4j use the Compose network only.
- `PDF_ASSISTANT_DATA_DIR=/app/data`, `RAG_BACKEND=qdrant`, and `QDRANT_URL=http://qdrant:6333` are fixed in the app service.
- The app runs as a non-root user and remains a single process/one replica.
- Real secrets stay in ignored `deploy/.env`; no secret is copied into an image, log, health check, or backup archive.
- Cold backup stops all active services and preserves a rollback directory during restore.
- Do not change authentication, session, user-storage, `document_id`, source metadata, Memory, or RAG contracts.
- Docker build, Compose startup, and runtime smoke verification must be run on a Linux host with Docker Engine/Compose v2; the current Windows workstation has no Docker CLI.

---

### Task 1: Make the Gradio launch configuration environment-driven

**Files:**
- Create: `ui/launch_config.py`
- Create: `tests/ui/test_launch_config.py`
- Modify: `ui/gradio_app.py:1-20,996-1001`

**Interfaces:**
- Produces `LaunchConfig(server_name: str, server_port: int, root_path: str | None, share: bool)`.
- Produces `load_launch_config(environ: Mapping[str, str] | None = None) -> LaunchConfig`.
- `LaunchConfig.as_gradio_kwargs() -> dict[str, object]` returns the exact keyword arguments passed to `demo.launch`.
- `ui/gradio_app.py` imports `load_launch_config` only after inserting the project root into `sys.path`.

- [ ] **Step 1: Write failing tests for defaults, overrides, and validation**

```python
# tests/ui/test_launch_config.py
import unittest

from ui.launch_config import load_launch_config


class LaunchConfigTests(unittest.TestCase):
    def test_defaults_keep_local_development_behavior(self):
        config = load_launch_config({})

        self.assertEqual(config.server_name, "127.0.0.1")
        self.assertEqual(config.server_port, 7860)
        self.assertIsNone(config.root_path)
        self.assertEqual(
            config.as_gradio_kwargs(),
            {
                "server_name": "127.0.0.1",
                "server_port": 7860,
                "share": False,
            },
        )

    def test_environment_overrides_are_converted_once(self):
        config = load_launch_config(
            {
                "GRADIO_SERVER_NAME": "0.0.0.0",
                "GRADIO_SERVER_PORT": "18080",
                "GRADIO_ROOT_PATH": "/learning",
            }
        )

        self.assertEqual(config.server_name, "0.0.0.0")
        self.assertEqual(config.server_port, 18080)
        self.assertEqual(config.root_path, "/learning")
        self.assertEqual(
            config.as_gradio_kwargs(),
            {
                "server_name": "0.0.0.0",
                "server_port": 18080,
                "root_path": "/learning",
                "share": False,
            },
        )

    def test_port_must_be_an_integer_in_range(self):
        for value in ("", "abc", "0", "65536"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "GRADIO_SERVER_PORT"):
                    load_launch_config({"GRADIO_SERVER_PORT": value})

    def test_root_path_must_be_absolute_when_present(self):
        with self.assertRaisesRegex(ValueError, "GRADIO_ROOT_PATH"):
            load_launch_config({"GRADIO_ROOT_PATH": "learning"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python -m pytest tests/ui/test_launch_config.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'ui.launch_config'`.

- [ ] **Step 3: Implement the minimal configuration module**

```python
# ui/launch_config.py
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


def _parse_port(value: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("GRADIO_SERVER_PORT must be an integer from 1 to 65535") from exc
    if not 1 <= port <= 65535:
        raise ValueError("GRADIO_SERVER_PORT must be an integer from 1 to 65535")
    return port


def _parse_root_path(value: str) -> str | None:
    root_path = value.strip()
    if not root_path:
        return None
    if not root_path.startswith("/"):
        raise ValueError("GRADIO_ROOT_PATH must start with '/'")
    return root_path.rstrip("/") or "/"


@dataclass(frozen=True)
class LaunchConfig:
    server_name: str
    server_port: int
    root_path: str | None
    share: bool = False

    def as_gradio_kwargs(self) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "server_name": self.server_name,
            "server_port": self.server_port,
            "share": self.share,
        }
        if self.root_path is not None:
            kwargs["root_path"] = self.root_path
        return kwargs


def load_launch_config(
    environ: Mapping[str, str] | None = None,
) -> LaunchConfig:
    env = os.environ if environ is None else environ
    server_name = (env.get("GRADIO_SERVER_NAME") or "127.0.0.1").strip()
    if not server_name:
        raise ValueError("GRADIO_SERVER_NAME cannot be empty")
    port = _parse_port(env.get("GRADIO_SERVER_PORT") or "7860")
    root_path = _parse_root_path(env.get("GRADIO_ROOT_PATH") or "")
    return LaunchConfig(
        server_name=server_name,
        server_port=port,
        root_path=root_path,
    )
```

- [ ] **Step 4: Replace only the hard-coded launch arguments**

Add the import after the existing project-root `sys.path.insert`:

```python
from ui.launch_config import load_launch_config
```

Replace the current `__main__` block with:

```python
if __name__ == "__main__":
    demo.launch(**load_launch_config().as_gradio_kwargs())
```

- [ ] **Step 5: Run focused and existing UI tests**

Run: `python -m pytest tests/ui/test_launch_config.py tests/ui/test_document_selection.py -q`

Expected: all tests pass. No existing Gradio handler code changes.

- [ ] **Step 6: Commit the isolated launch change**

```sh
git add ui/launch_config.py tests/ui/test_launch_config.py ui/gradio_app.py
git commit -m "feat: configure Gradio launch from environment"
```

---

### Task 2: Build the non-root application image and health probes

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `deploy/entrypoint.sh`
- Create: `deploy/healthcheck.py`
- Create: `deploy/qdrant.Dockerfile`
- Create: `tests/deploy/__init__.py`
- Create: `tests/deploy/test_image_contract.py`

**Interfaces:**
- `deploy/entrypoint.sh` checks `PDF_ASSISTANT_DATA_DIR` write access and `exec`s `python ui/gradio_app.py`.
- `deploy/healthcheck.py` exits `0` only when `GET http://127.0.0.1:${GRADIO_SERVER_PORT:-7860}/` returns HTTP 200–399.
- `deploy/qdrant.Dockerfile` derives from `qdrant/qdrant:v1.18.2` and adds only the `wget` package required by `/readyz`.

- [ ] **Step 1: Write failing static contract tests**

```python
# tests/deploy/test_image_contract.py
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_dockerfile_uses_pinned_python_and_non_root_user():
    source = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.11-slim-bookworm" in source
    assert "USER app" in source
    assert 'ENTRYPOINT ["/app/deploy/entrypoint.sh"]' in source


def test_dockerignore_excludes_runtime_data_and_secrets():
    source = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    for pattern in (".env", ".env.*", "deploy-data/", "backups/", ".git/"):
        assert pattern in source


def test_qdrant_probe_image_preserves_the_pinned_base():
    source = (ROOT / "deploy" / "qdrant.Dockerfile").read_text(encoding="utf-8")

    assert "FROM qdrant/qdrant:v1.18.2" in source
    assert "wget" in source
    assert "rm -rf /var/lib/apt/lists/*" in source
```

- [ ] **Step 2: Run the contract test to verify it fails**

Run: `python -m pytest tests/deploy/test_image_contract.py -q`

Expected: failures report the missing Docker/deployment files.

- [ ] **Step 3: Add the application Dockerfile**

```dockerfile
# Dockerfile
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /app/requirements.txt

COPY . /app
RUN mkdir -p /app/data \
    && chmod 0755 /app/deploy/entrypoint.sh \
    && chown -R app:app /app

USER app

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD ["python", "/app/deploy/healthcheck.py"]

ENTRYPOINT ["/app/deploy/entrypoint.sh"]
```

- [ ] **Step 4: Add the build context exclusions**

```text
# .dockerignore
.git/
.gitignore
.env
.env.*
!.env.example
deploy/.env
deploy-data/
backups/
data/
.runtime/
.pytest_cache/
__pycache__/
*.py[cod]
*.db
*.pdf
*.docx
tests/
docs/
```

- [ ] **Step 5: Add the entrypoint and application health check**

```sh
#!/bin/sh
# deploy/entrypoint.sh
set -eu

data_dir="${PDF_ASSISTANT_DATA_DIR:-/app/data}"
if [ ! -d "$data_dir" ]; then
    mkdir -p "$data_dir"
fi

probe="$data_dir/.write-probe.$$.tmp"
if ! : > "$probe"; then
    echo "Deployment data directory is not writable: $data_dir" >&2
    exit 1
fi
rm -f "$probe"

echo "Starting Gradio application on ${GRADIO_SERVER_NAME:-127.0.0.1}:${GRADIO_SERVER_PORT:-7860}"
echo "Using data directory: $data_dir"
echo "Using RAG backend: ${RAG_BACKEND:-json}"
exec python ui/gradio_app.py
```

```python
# deploy/healthcheck.py
from __future__ import annotations

import os
import sys
from urllib.error import URLError
from urllib.request import urlopen


def main() -> int:
    port = os.environ.get("GRADIO_SERVER_PORT", "7860")
    url = f"http://127.0.0.1:{port}/"
    try:
        with urlopen(url, timeout=3) as response:
            if 200 <= response.status < 400:
                return 0
            print(f"application returned HTTP {response.status}", file=sys.stderr)
    except (OSError, URLError, ValueError) as exc:
        print(f"application health check failed: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Add the Qdrant readiness derivative**

```dockerfile
# deploy/qdrant.Dockerfile
FROM qdrant/qdrant:v1.18.2

USER root
RUN apt-get update \
    && apt-get install --yes --no-install-recommends wget \
    && rm -rf /var/lib/apt/lists/*
```

This keeps Qdrant’s official binary and storage layout, while making the
Compose `GET /readyz` probe explicit. Qdrant documents `/readyz` as a
readiness endpoint; the official image intentionally does not ship a network
client, so the derivative is limited to this health-check dependency.

- [ ] **Step 7: Run image-contract tests**

Run: `python -m pytest tests/deploy/test_image_contract.py -q`

Expected: PASS.

- [ ] **Step 8: Commit the image layer**

```sh
git add Dockerfile .dockerignore deploy/entrypoint.sh deploy/healthcheck.py \
  deploy/qdrant.Dockerfile tests/deploy/__init__.py tests/deploy/test_image_contract.py
git commit -m "feat: add deployment container images and probes"
```

---

### Task 3: Add Compose services, profiles, environment template, and contract tests

**Files:**
- Create: `compose.yaml`
- Create: `deploy/.env.example`
- Create: `tests/deploy/test_compose_contract.py`
- Modify: `.gitignore`

**Interfaces:**
- Default command: `docker compose --env-file deploy/.env up -d --build`.
- Graph command: `docker compose --env-file deploy/.env --profile graph up -d`.
- `APP_BIND_ADDRESS`, `APP_PORT`, and `DEPLOY_DATA_ROOT` control host binding and volume roots.
- `NEO4J_URI` remains empty by default and is set to `neo4j://neo4j:7687` only when the graph profile is intentionally enabled.

- [ ] **Step 1: Write failing Compose contract tests**

```python
# tests/deploy/test_compose_contract.py
from pathlib import Path


ROOT = Path(__file__).parents[2]
COMPOSE = ROOT / "compose.yaml"
ENV_EXAMPLE = ROOT / "deploy" / ".env.example"


def test_compose_contains_default_app_and_qdrant_and_optional_graph():
    source = COMPOSE.read_text(encoding="utf-8")

    assert "app:" in source
    assert "qdrant:" in source
    assert "neo4j:" in source
    assert "profiles:" in source
    assert "- graph" in source
    assert "qdrant/qdrant:v1.18.2" in source
    assert "neo4j:5.26.28-community" in source


def test_only_app_publishes_a_host_port():
    source = COMPOSE.read_text(encoding="utf-8")

    app_block = source.split("  app:", 1)[1].split("  qdrant:", 1)[0]
    qdrant_block = source.split("  qdrant:", 1)[1].split("  neo4j:", 1)[0]
    neo4j_block = source.split("  neo4j:", 1)[1].split("networks:", 1)[0]

    assert "ports:" in app_block
    assert "ports:" not in qdrant_block
    assert "ports:" not in neo4j_block


def test_environment_template_contains_no_real_secret():
    source = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert "LLM_API_KEY=" in source
    assert "QDRANT_URL=http://qdrant:6333" in source
    assert "NEO4J_PASSWORD=" in source
    assert "replace" in source.lower()
    assert "sk-" not in source
```

- [ ] **Step 2: Run the contract test to verify it fails**

Run: `python -m pytest tests/deploy/test_compose_contract.py -q`

Expected: failures report the missing Compose and environment files.

- [ ] **Step 3: Add the deployment environment template**

```dotenv
# deploy/.env.example
APP_BIND_ADDRESS=0.0.0.0
APP_PORT=7860
DEPLOY_DATA_ROOT=./deploy-data

LLM_API_KEY=replace-with-your-key
LLM_BASE_URL=https://api.example.com/v1
LLM_MODEL_ID=replace-with-your-model
LLM_MAX_RETRIES=2
LLM_RETRY_BACKOFF=0.5
LLM_CONTEXT_WINDOW_TOKENS=8192
LLM_OUTPUT_RESERVED_TOKENS=1024
LLM_CONTEXT_SAFETY_MARGIN_TOKENS=512

NEO4J_URI=
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=replace-before-enabling-graph
NEO4J_DATABASE=neo4j
```

- [ ] **Step 4: Add the Compose file**

```yaml
# compose.yaml
services:
  app:
    build:
      context: .
      dockerfile: Dockerfile
    env_file:
      - ${DEPLOY_ENV_FILE:-deploy/.env}
    environment:
      PDF_ASSISTANT_DATA_DIR: /app/data
      RAG_BACKEND: qdrant
      QDRANT_URL: http://qdrant:6333
      GRADIO_SERVER_NAME: 0.0.0.0
      GRADIO_SERVER_PORT: 7860
    ports:
      - "${APP_BIND_ADDRESS:-0.0.0.0}:${APP_PORT:-7860}:7860"
    volumes:
      - "${DEPLOY_DATA_ROOT:-./deploy-data}/app:/app/data"
    depends_on:
      qdrant:
        condition: service_healthy
    networks:
      - app_net
    restart: unless-stopped

  qdrant:
    build:
      context: .
      dockerfile: deploy/qdrant.Dockerfile
    expose:
      - "6333"
    volumes:
      - "${DEPLOY_DATA_ROOT:-./deploy-data}/qdrant:/qdrant/storage"
    healthcheck:
      test:
        - CMD-SHELL
        - "wget -q -O - http://127.0.0.1:6333/readyz >/dev/null"
      interval: 10s
      timeout: 5s
      start_period: 20s
      retries: 12
    networks:
      - app_net
    restart: unless-stopped

  neo4j:
    profiles:
      - graph
    image: neo4j:5.26.28-community
    env_file:
      - ${DEPLOY_ENV_FILE:-deploy/.env}
    environment:
      NEO4J_AUTH: "neo4j/${NEO4J_PASSWORD}"
    expose:
      - "7474"
      - "7687"
    volumes:
      - "${DEPLOY_DATA_ROOT:-./deploy-data}/neo4j/data:/data"
    healthcheck:
      test:
        - CMD-SHELL
        - "cypher-shell -u neo4j -p \"$${NEO4J_PASSWORD}\" 'RETURN 1' >/dev/null"
      interval: 15s
      timeout: 10s
      start_period: 45s
      retries: 10
    networks:
      - app_net
    restart: unless-stopped

networks:
  app_net:
    driver: bridge
```

The Qdrant and Neo4j services deliberately have no `ports` section. The app
environment file is mounted only as process environment; it is never copied
into the image or data volumes.

- [ ] **Step 5: Add ignored deployment paths**

Append only these lines to `.gitignore`, preserving all existing entries:

```gitignore
deploy/.env
deploy-data/
backups/
```

- [ ] **Step 6: Run static tests and Compose config validation**

Run:

```sh
python -m pytest tests/deploy/test_compose_contract.py -q
cp deploy/.env.example deploy/.env
docker compose --env-file deploy/.env config
docker compose --env-file deploy/.env --profile graph config
rm deploy/.env
```

Expected: contract tests pass; both Compose commands print normalized YAML and
exit `0`. Do not commit the copied `deploy/.env`.

- [ ] **Step 7: Commit the Compose layer**

```sh
git add compose.yaml deploy/.env.example tests/deploy/test_compose_contract.py .gitignore
git commit -m "feat: add single-node Compose deployment"
```

---

### Task 4: Add non-destructive and deep deployment smoke tests

**Files:**
- Create: `deploy/smoke_test.py`
- Create: `tests/deploy/test_smoke_test.py`

**Interfaces:**
- Host command: `python deploy/smoke_test.py --env-file deploy/.env`.
- Host deep command: `python deploy/smoke_test.py --env-file deploy/.env --deep`.
- Internal command used by the host orchestrator:
  `python /app/deploy/smoke_test.py --inside-deep`.
- Default mode checks Compose running/healthy status, app HTTP, Qdrant
  `/readyz` through the app container, writable `/app/data`, and the actual
  `/app/hello_agents` import path without calling an LLM.
- Deep mode uses a temporary database, user directory, document ID and
  Qdrant namespace; it must clean them in `finally`.

- [ ] **Step 1: Write failing unit tests for env parsing and Compose status parsing**

```python
# tests/deploy/test_smoke_test.py
import json
from pathlib import Path

from deploy.smoke_test import parse_env_file, parse_compose_status


def test_parse_env_file_ignores_comments_and_strips_quotes(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_PORT=17860\n# ignored\nLLM_BASE_URL=\"http://llm.local/v1\"\n",
        encoding="utf-8",
    )

    assert parse_env_file(env_file) == {
        "APP_PORT": "17860",
        "LLM_BASE_URL": "http://llm.local/v1",
    }


def test_parse_compose_status_accepts_json_lines():
    raw = "\n".join(
        [
            json.dumps({"Service": "app", "State": "running", "Health": "healthy"}),
            json.dumps({"Service": "qdrant", "State": "running", "Health": "healthy"}),
        ]
    )

    assert parse_compose_status(raw) == {
        "app": ("running", "healthy"),
        "qdrant": ("running", "healthy"),
    }


def test_parse_compose_status_rejects_unhealthy_required_service():
    raw = json.dumps({"Service": "qdrant", "State": "running", "Health": "unhealthy"})

    status = parse_compose_status(raw)
    assert status["qdrant"] == ("running", "unhealthy")
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python -m pytest tests/deploy/test_smoke_test.py -q`

Expected: collection fails because `deploy/smoke_test.py` does not exist.

- [ ] **Step 3: Implement the host orchestration and internal checks**

Implement these exact pure helpers first:

```python
def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def parse_compose_status(raw: str) -> dict[str, tuple[str, str]]:
    status: dict[str, tuple[str, str]] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        service = item.get("Service") or item.get("service")
        state = item.get("State") or item.get("state") or ""
        health = item.get("Health") or item.get("health") or ""
        if service:
            status[str(service)] = (str(state), str(health))
    return status
```

The host `main()` must:

1. Load the env file and derive `APP_PORT` and `APP_BIND_ADDRESS`.
2. Run `docker compose --env-file <file> ps --format json`.
3. Require `app` and `qdrant` to be `running` and `healthy`.
4. GET `http://<bind-address>:<port>/` with `urllib.request`.
5. Run `docker compose --env-file <file> exec -T app python -c ...` to:
   - GET `http://qdrant:6333/readyz`;
   - create and remove a random file under `/app/data`;
   - print `hello_agents.__file__`.
6. With `--deep`, run
   `docker compose --env-file <file> exec -T app python /app/deploy/smoke_test.py --inside-deep`.

The `--inside-deep` branch must use only standard library plus project modules:

```python
def run_deep_inside() -> int:
    import shutil
    import tempfile
    import uuid
    from app.database import initialize_database
    from app.session import SessionRegistry
    from app.storage import UserStorage

    temp_root = Path(tempfile.mkdtemp(prefix="deployment-smoke-"))
    registry = None
    token = None
    assistant = None
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
        document_path = storage.document_path(session.user_id, document_id, ".txt")
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
            raise RuntimeError(loaded)
        search = assistant.search("qdrant persistence", limit=5)
        if "deployment-smoke.txt" not in search:
            raise RuntimeError(f"smoke source missing from search: {search}")
        answer = assistant.ask("What is the deployment smoke marker?")
        if answer.startswith("❌"):
            raise RuntimeError(answer)
        return 0
    finally:
        if assistant is not None:
            try:
                assistant.clear_all_documents()
            except Exception:
                pass
        if registry is not None and token is not None:
            registry.logout(token)
        shutil.rmtree(temp_root, ignore_errors=True)
```

`main()` catches errors, prints only a check name and sanitized exception text,
and returns `1`; it never prints environment values containing `KEY`,
`PASSWORD`, or `TOKEN`.

- [ ] **Step 4: Run unit smoke tests**

Run: `python -m pytest tests/deploy/test_smoke_test.py -q`

Expected: PASS without Docker or an LLM.

- [ ] **Step 5: Run the default deployment smoke test on Linux**

Run:

```sh
python deploy/smoke_test.py --env-file deploy/.env
```

Expected: each check prints `PASS` and the process exits `0`. If Docker is not
available, it must exit `1` with `docker executable not found` rather than
claiming success.

- [ ] **Step 6: Run deep smoke with configured LLM**

Run:

```sh
python deploy/smoke_test.py --env-file deploy/.env --deep
```

Expected: temporary user/database/document/vector namespace is created and
removed; the process exits `0` only when the search source and LLM answer pass.

- [ ] **Step 7: Commit the smoke-test layer**

```sh
git add deploy/smoke_test.py tests/deploy/test_smoke_test.py
git commit -m "test: add deployment smoke checks"
```

---

### Task 5: Add cold backup, checksum validation, restore, and rollback scripts

**Files:**
- Create: `deploy/backup.sh`
- Create: `deploy/restore.sh`
- Create: `tests/deploy/test_backup_restore_contract.py`
- Create: `deploy/README.md`

**Interfaces:**
- `deploy/backup.sh [--env-file path] [--backup-root path]`
  creates `<backup-root>/assistant-<UTC>.tar.gz`,
  `<archive>.sha256`, and `<archive>.meta`.
- `deploy/restore.sh <archive> [--env-file path]`
  validates the checksum and archive members, swaps data into place while
  preserving a rollback directory, and restarts the previously running services.
- `DEPLOY_DATA_ROOT` defaults to `./deploy-data`; backup output defaults to
  `./backups` and must not be inside the data root.

- [ ] **Step 1: Write failing contract tests**

```python
# tests/deploy/test_backup_restore_contract.py
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_backup_script_is_cold_and_excludes_secrets():
    source = (ROOT / "deploy" / "backup.sh").read_text(encoding="utf-8")

    assert "docker compose" in source
    assert "stop" in source
    assert "trap" in source
    assert "sha256sum" in source
    assert "tar -C" in source
    assert "--volumes" not in source


def test_restore_script_validates_and_keeps_a_rollback():
    source = (ROOT / "deploy" / "restore.sh").read_text(encoding="utf-8")

    assert "sha256sum -c" in source
    assert "tar -tzf" in source
    assert "rollback" in source
    assert "rm -rf" not in source
    assert "docker compose" in source


def test_deployment_readme_documents_restart_and_restore_commands():
    source = (ROOT / "deploy" / "README.md").read_text(encoding="utf-8")

    assert "docker compose" in source
    assert "backup.sh" in source
    assert "restore.sh" in source
    assert "单副本" in source
    assert "防火墙" in source
```

- [ ] **Step 2: Run the contract test to verify it fails**

Run: `python -m pytest tests/deploy/test_backup_restore_contract.py -q`

Expected: failures report missing scripts and deployment README.

- [ ] **Step 3: Implement `backup.sh` with a restart trap**

Use POSIX `sh` and quote every path:

```sh
#!/bin/sh
set -eu

env_file="${DEPLOY_ENV_FILE:-deploy/.env}"
backup_root="${DEPLOY_BACKUP_ROOT:-./backups}"
data_root="${DEPLOY_DATA_ROOT:-./deploy-data}"

case "$backup_root" in
    "$data_root"|"$data_root"/*)
        echo "Backup root must be outside DEPLOY_DATA_ROOT" >&2
        exit 1
        ;;
esac

[ -d "$data_root" ] || {
    echo "DEPLOY_DATA_ROOT must exist before backup: $data_root" >&2
    exit 1
}

mkdir -p "$backup_root"
running_services="$(docker compose --env-file "$env_file" ps --services --status running)"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$backup_root/assistant-$timestamp.tar.gz"
metadata="$archive.meta"

restart_services() {
    if [ -n "$running_services" ]; then
        docker compose --env-file "$env_file" start $running_services >/dev/null
    fi
}
trap restart_services EXIT INT TERM

docker compose --env-file "$env_file" stop >/dev/null
tar -C "$data_root" -czf "$archive" .
sha256sum "$archive" > "$archive.sha256"
{
    printf 'created_at=%s\n' "$timestamp"
    printf 'data_root=%s\n' "$data_root"
    printf 'running_services=%s\n' "$running_services"
    printf 'git_revision=%s\n' "$(git rev-parse HEAD 2>/dev/null || printf unknown)"
} > "$metadata"
printf 'Backup created: %s\n' "$archive"
```

The implementation must first verify `data_root` exists and is a directory. It
must not archive `deploy/.env`, and it must not use `docker compose down --volumes`.

- [ ] **Step 4: Implement `restore.sh` with archive validation and rollback**

```sh
#!/bin/sh
set -eu

if [ "$#" -lt 1 ]; then
    echo "Usage: deploy/restore.sh <archive> [--env-file path]" >&2
    exit 2
fi

archive="$1"
shift
env_file="${DEPLOY_ENV_FILE:-deploy/.env}"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --env-file)
            env_file="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 2
            ;;
    esac
done

data_root="${DEPLOY_DATA_ROOT:-./deploy-data}"
checksum="$archive.sha256"
[ -f "$archive" ] || { echo "Archive not found: $archive" >&2; exit 1; }
[ -f "$checksum" ] || { echo "Checksum not found: $checksum" >&2; exit 1; }
case "$data_root" in
    ""|/|.) echo "Refusing to restore into an unsafe data root: $data_root" >&2; exit 1 ;;
esac
sha256sum -c "$checksum"

if tar -tzf "$archive" | awk '
    $0 ~ /^\// || $0 ~ /(^|\/)\.\.(\/|$)/ { bad=1 }
    END { exit bad }
'; then
    :
else
    echo "Archive contains an unsafe path" >&2
    exit 1
fi

parent="$(dirname "$data_root")"
staging="$(mktemp -d "$parent/.assistant-restore.XXXXXX")"
rollback="${data_root}.rollback-$(date -u +%Y%m%dT%H%M%SZ)"
running_services="$(docker compose --env-file "$env_file" ps --services --status running)"

cleanup() {
    if [ -n "${staging:-}" ] && [ -d "$staging" ]; then
        echo "Restore staging retained for inspection: $staging" >&2
    fi
}
rollback_restore() {
    if [ -d "$data_root" ]; then
        mv "$data_root" "${data_root}.failed-$(date -u +%Y%m%dT%H%M%SZ)"
    fi
    if [ -d "$rollback" ]; then
        mv "$rollback" "$data_root"
    fi
    if [ -n "$running_services" ]; then
        docker compose --env-file "$env_file" start $running_services >/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

tar -xzf "$archive" -C "$staging"
docker compose --env-file "$env_file" stop >/dev/null
mv "$data_root" "$rollback"
mv "$staging" "$data_root"
staging=""

if [ -n "$running_services" ]; then
    if ! docker compose --env-file "$env_file" start $running_services >/dev/null; then
        rollback_restore
        exit 1
    fi
fi
printf 'Restore completed. Rollback kept at: %s\n' "$rollback"
```

Before enabling the final script, add a guard that requires the archive
top-level member list to contain only `.`-relative paths and that `data_root`
is not empty or `/`. Keep every old data directory by rename; never use a
recursive delete for rollback.

- [ ] **Step 5: Add deployment operations documentation**

`deploy/README.md` must contain these exact operator flows:

```sh
cp deploy/.env.example deploy/.env
mkdir -p deploy-data/app deploy-data/qdrant
docker compose --env-file deploy/.env up -d --build
python deploy/smoke_test.py --env-file deploy/.env
sh deploy/backup.sh --env-file deploy/.env
sh deploy/restore.sh backups/assistant-<UTC>.tar.gz --env-file deploy/.env
```

Document:

- how to set an internal or external OpenAI-compatible `LLM_BASE_URL`;
- how to restrict `APP_BIND_ADDRESS`/`APP_PORT` with the host firewall;
- that direct HTTP is for an intranet or trusted network only;
- that one app replica/one worker is required;
- how to enable Neo4j only after setting `NEO4J_URI`,
  `NEO4J_USERNAME`, `NEO4J_PASSWORD`, and `NEO4J_DATABASE`;
- that `deploy/.env` is never committed or backed up;
- that backup is a cold backup and briefly stops services;
- that rollback directories must be manually removed only after validation;
- that `docker compose down --volumes` is not a routine command.

- [ ] **Step 6: Run contract tests and shell syntax checks**

Run:

```sh
python -m pytest tests/deploy/test_backup_restore_contract.py -q
sh -n deploy/backup.sh
sh -n deploy/restore.sh
```

Expected: all tests pass and both scripts have valid POSIX shell syntax.

- [ ] **Step 7: Commit operations tooling**

```sh
git add deploy/backup.sh deploy/restore.sh deploy/README.md \
  tests/deploy/test_backup_restore_contract.py
git commit -m "feat: add deployment backup and restore tooling"
```

---

### Task 6: Update user-facing README and perform the full verification pass

**Files:**
- Modify: `README.md` by adding a `Docker 单节点部署` section after the existing
  local run instructions.
- Create: `tests/deploy/test_readme_deployment_section.py`

**Interfaces:**
- The root README links to `deploy/README.md` for operator details.
- The README’s deployment commands use `--env-file deploy/.env`.
- The README states that Docker deployment is single replica, direct HTTP,
  Qdrant by default, and Neo4j optional.

- [ ] **Step 1: Write the README contract test**

```python
# tests/deploy/test_readme_deployment_section.py
from pathlib import Path


def test_root_readme_exposes_the_supported_deployment_path():
    source = (Path(__file__).parents[2] / "README.md").read_text(encoding="utf-8")

    assert "Docker 单节点部署" in source
    assert "docker compose --env-file deploy/.env up -d --build" in source
    assert "deploy/README.md" in source
    assert "Neo4j" in source
    assert "单副本" in source
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/deploy/test_readme_deployment_section.py -q`

Expected: failure because the new deployment section is absent.

- [ ] **Step 3: Add the concise root README section**

Add:

```markdown
## Docker 单节点部署

目标是单台 Linux 云主机或内网服务器上的单副本 Compose 部署。默认启动
Gradio 应用和 Qdrant，Neo4j 通过 `graph` Profile 按需启动；只有应用端口
发布到宿主机，数据保存在 `deploy-data/`。

```sh
cp deploy/.env.example deploy/.env
# 编辑 deploy/.env，设置 LLM_API_KEY、LLM_BASE_URL 和 LLM_MODEL_ID
docker compose --env-file deploy/.env up -d --build
python deploy/smoke_test.py --env-file deploy/.env
```

完整的内网防火墙、Neo4j Profile、冷备份/恢复和故障处理说明见
[`deploy/README.md`](deploy/README.md)。该部署保持单副本、单 worker，直接
HTTP 仅适用于受控内网；公网访问必须由外部 HTTPS 网关保护。
```

- [ ] **Step 4: Run the complete Python test suite**

Run:

```powershell
New-Item -ItemType Directory -Force .runtime\pytest-tmp | Out-Null
$env:TEMP=(Resolve-Path '.runtime\pytest-tmp').Path
$env:TMP=$env:TEMP
python -m pytest -q
```

Expected: all runnable tests pass. If collection stops because the current
interpreter lacks Gradio, record that exact failure and run the non-UI suite
separately; do not claim a full pass.

- [ ] **Step 5: Validate the container build and default Compose stack on Linux**

Run:

```sh
cp deploy/.env.example deploy/.env
docker compose --env-file deploy/.env config
docker compose --env-file deploy/.env build app qdrant
docker compose --env-file deploy/.env up -d
docker compose --env-file deploy/.env ps
python deploy/smoke_test.py --env-file deploy/.env
```

Expected:

- Compose config exits `0`;
- both images build;
- `app` and `qdrant` show `running (healthy)`;
- the smoke test exits `0`;
- `docker compose --env-file deploy/.env exec app id -u` is not `0`;
- `docker compose --env-file deploy/.env exec app python -c \
  "from pathlib import Path; print(Path('/app/data').is_dir())"` prints `True`.

- [ ] **Step 6: Validate the graph profile**

After setting a real `NEO4J_PASSWORD` and
`NEO4J_URI=neo4j://neo4j:7687` in the ignored env file, run:

```sh
docker compose --env-file deploy/.env --profile graph up -d
docker compose --env-file deploy/.env --profile graph ps
```

Expected: `neo4j` is healthy; the default app and Qdrant remain healthy; no
Neo4j host port is published.

- [ ] **Step 7: Perform backup/restore round-trip**

Run in a disposable deployment data root:

```sh
sh deploy/backup.sh --env-file deploy/.env
# Create a marker file under deploy-data/app only for this test.
sh deploy/restore.sh backups/assistant-<UTC>.tar.gz --env-file deploy/.env
python deploy/smoke_test.py --env-file deploy/.env
```

Expected: checksum validation passes, the original marker state is restored,
the app/Qdrant services return healthy, and the rollback directory remains.

- [ ] **Step 8: Review diff and commit the documentation/test closure**

Run:

```sh
git diff --check
git status --short
git diff --stat
```

Confirm every changed line maps to this deployment feature and no `deploy/.env`,
`deploy-data/`, `backups/`, `.pytest-tmp-*`, or generated test data is staged.
Then commit:

```sh
git add README.md tests/deploy/test_readme_deployment_section.py
git commit -m "docs: document Docker deployment operations"
```

---

## Plan Self-Review

### Spec coverage

| Spec requirement | Plan coverage |
|---|---|
| App + Qdrant default Compose | Task 3 |
| Optional Neo4j `graph` Profile | Task 3, Task 6 |
| Environment-driven host/port | Task 1 |
| Non-root app image | Task 2, Task 6 |
| No Qdrant/Neo4j host ports | Task 3 contract test |
| Persistent app/Qdrant/Neo4j data | Task 3, Task 5 |
| Qdrant readiness before app | Task 2 derivative probe, Task 3 `depends_on` |
| Application liveness healthcheck | Task 2 |
| Default and deep smoke checks | Task 4 |
| Cold backup with checksum | Task 5 |
| Restore with rollback and no destructive delete | Task 5 |
| Intranet HTTP/firewall guidance | Task 5, Task 6 |
| Single replica/worker boundary | Global constraints, Task 6 |
| Existing data-isolation contracts unchanged | Global constraints, Task 6 regression |

### Placeholder and consistency checks

- No task contains placeholder markers or an unspecified “appropriate error
  handling” step.
- All later tasks use the exact environment names and file paths introduced by
  earlier tasks.
- The Qdrant readiness command is runnable because Task 2 adds `wget` to the
  pinned official base image before Task 3 references `/readyz`.
- The smoke test’s Docker health inspection runs on the host; only its deep
  project-flow branch runs inside the app container, so no Docker socket is
  mounted into the application.
- Backup scripts use `stop`/`start`, not `down --volumes`, and keep rollback
  directories rather than recursively deleting data.
- The current workstation’s missing Docker CLI is an environment limitation,
  not an implementation shortcut; Linux Docker verification remains a required
  completion gate.

**Plan complete and saved to `docs/superpowers/plans/2026-07-30-single-node-docker-deployment.md`.**
