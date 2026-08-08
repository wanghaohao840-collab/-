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
    qdrant_image = (
        ROOT / "deploy" / "qdrant.Dockerfile"
    ).read_text(encoding="utf-8")
    assert "qdrant/qdrant:v1.18.2" in qdrant_image
    assert "neo4j:5.26.28-community" in source


def test_only_app_publishes_a_host_port():
    source = COMPOSE.read_text(encoding="utf-8")

    app_block = source.split("  app:", 1)[1].split("  qdrant:", 1)[0]
    qdrant_block = source.split("  qdrant:", 1)[1].split("  neo4j:", 1)[0]
    neo4j_block = source.split("  neo4j:", 1)[1].split("networks:", 1)[0]

    assert "ports:" in app_block
    assert "ports:" not in qdrant_block
    assert "ports:" not in neo4j_block


def test_app_runs_as_the_deployment_account():
    source = COMPOSE.read_text(encoding="utf-8")
    env_source = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert 'user: "${APP_UID:-1000}:${APP_GID:-1000}"' in source
    assert "APP_UID=1000" in env_source
    assert "APP_GID=1000" in env_source


def test_app_uses_unified_runtime_port_and_persistent_data_directory():
    source = COMPOSE.read_text(encoding="utf-8")
    env_source = ENV_EXAMPLE.read_text(encoding="utf-8")
    app_block = source.split("  app:", 1)[1].split("  qdrant:", 1)[0]

    assert "PDF_ASSISTANT_DATA_DIR: /app/data" in app_block
    assert 'APP_HOST: "0.0.0.0"' in app_block
    assert 'APP_PORT: "${APP_PORT:-7860}"' in app_block
    assert (
        '"${APP_BIND_ADDRESS:-0.0.0.0}:${APP_PORT:-7860}:'
        '${APP_PORT:-7860}"'
    ) in app_block
    assert '"${DEPLOY_DATA_ROOT:-./deploy-data}/app:/app/data"' in app_block
    assert "GRADIO_SERVER_NAME" not in app_block
    assert "GRADIO_SERVER_PORT" not in app_block
    assert "APP_HOST=0.0.0.0" in env_source


def test_environment_template_contains_no_real_secret():
    source = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert "LLM_API_KEY=" in source
    assert "QDRANT_URL=http://qdrant:6333" in source
    assert "NEO4J_PASSWORD=" in source
    assert "replace" in source.lower()
    assert "sk-" not in source
