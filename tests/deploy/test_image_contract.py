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

    assert "RUN npm ci" in source
    assert "RUN npm run build" in source
    assert "COPY --from=web-build /web/dist /app/web/dist" in source


def test_entrypoint_runs_one_uvicorn_worker_without_gradio_launch():
    source = (ROOT / "deploy" / "entrypoint.sh").read_text(encoding="utf-8")

    assert "exec python -m uvicorn server:app" in source
    assert '--host "${APP_HOST:-0.0.0.0}"' in source
    assert '--port "${APP_PORT:-7860}"' in source
    assert "--workers 1" in source
    assert "ui/gradio_app.py" not in source
    assert "demo.launch" not in source


def test_image_healthcheck_targets_fastapi_health_endpoint():
    source = (ROOT / "deploy" / "healthcheck.py").read_text(encoding="utf-8")

    assert 'os.environ.get("APP_PORT", "7860")' in source
    assert 'f"http://127.0.0.1:{port}/healthz"' in source


def test_dockerignore_excludes_runtime_data_and_secrets():
    source = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    for pattern in (".env", ".env.*", "deploy-data/", "backups/", ".git/"):
        assert pattern in source


def test_qdrant_probe_image_preserves_the_pinned_base():
    source = (ROOT / "deploy" / "qdrant.Dockerfile").read_text(encoding="utf-8")

    assert "FROM qdrant/qdrant:v1.18.2" in source
    assert "wget" in source
    assert "rm -rf /var/lib/apt/lists/*" in source
