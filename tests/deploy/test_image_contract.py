import json
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_dockerfile_uses_pinned_python_and_non_root_user():
    source = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM node:22-bookworm-slim AS web-build" in source
    assert "FROM python:3.11-slim-bookworm" in source
    assert "USER app" in source
    assert 'ENTRYPOINT ["/app/deploy/entrypoint.sh"]' in source


def test_dockerfile_builds_and_copies_the_react_distribution():
    source = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    package = json.loads((ROOT / "web" / "package.json").read_text(encoding="utf-8"))

    package_copy = source.index("COPY web/package.json web/package-lock.json ./")
    install = source.index("RUN npm ci")
    source_copy = source.index("COPY web/src ./src")
    assert package_copy < install < source_copy
    assert "RUN npm ci" in source
    assert package["scripts"]["build:app"] == (
        "tsc -b tsconfig.app.json tsconfig.node.json && vite build"
    )
    assert "RUN npm run build:app" in source
    tsconfig_copy = next(
        line for line in source.splitlines() if "web/tsconfig.app.json" in line
    )
    assert "web/tsconfig.json" not in tsconfig_copy
    assert "web/tsconfig.e2e.json" not in source
    assert "COPY --from=web-build /web/dist /app/web/dist" in source
    assert "COPY web/ ./" not in source
    assert "COPY web/e2e" not in source
    assert "COPY web/tests" not in source


def test_final_image_copies_only_runtime_source_and_built_web_assets():
    source = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY . /app" not in source
    for package in ("api", "app", "assistants", "hello_agents", "ui"):
        assert f"COPY {package}/ /app/{package}/" in source
    assert "COPY server.py /app/server.py" in source


def test_entrypoint_runs_one_uvicorn_worker_without_gradio_launch():
    entrypoint = ROOT / "deploy" / "entrypoint.sh"
    raw = entrypoint.read_bytes()
    source = raw.decode("utf-8")

    assert b"\r\n" not in raw
    assert "exec python -m uvicorn server:app" in source
    assert '--host "${APP_HOST:-0.0.0.0}"' in source
    assert '--port "${APP_PORT:-7860}"' in source
    assert "--workers 1" in source
    assert "ui/gradio_app.py" not in source
    assert "demo.launch" not in source


def test_shell_scripts_are_checked_out_with_linux_line_endings():
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")

    assert "deploy/*.sh text eol=lf" in attributes.splitlines()


def test_image_healthcheck_targets_fastapi_health_endpoint():
    source = (ROOT / "deploy" / "healthcheck.py").read_text(encoding="utf-8")

    assert 'os.environ.get("APP_PORT", "7860")' in source
    assert 'f"http://127.0.0.1:{port}/healthz"' in source


def test_dockerignore_excludes_runtime_data_and_secrets():
    source = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    for pattern in (".env", ".env.*", "deploy-data/", "backups/", ".git/"):
        assert pattern in source


def test_dockerignore_excludes_host_builds_and_reviewer_scratch():
    source = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    for pattern in (
        "web/node_modules/",
        "web/dist/",
        ".pytest-tmp*/",
        ".runtime/",
        ".superpowers/",
        "reports/",
        "traces/",
    ):
        assert pattern in source


def test_qdrant_probe_image_preserves_the_pinned_base():
    source = (ROOT / "deploy" / "qdrant.Dockerfile").read_text(encoding="utf-8")

    assert "FROM qdrant/qdrant:v1.18.2" in source
    assert "wget" in source
    assert "rm -rf /var/lib/apt/lists/*" in source
