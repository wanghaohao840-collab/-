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
    assert (
        "qdrant/qdrant:v1.18.3@sha256:"
        "0bd98fa7977f1e75694779359ca4e212822e5a71334e28421182f72f209d5286"
        in qdrant_image
    )
    assert (
        "neo4j:5.26.28-community@sha256:"
        "ff32db30b2baff97971e441b46bfd9c832c1b62c970398ef579244c06b21d357"
        in source
    )


def test_only_app_publishes_a_host_port():
    source = COMPOSE.read_text(encoding="utf-8")

    app_block = source.split("  app:", 1)[1].split("  qdrant:", 1)[0]
    qdrant_block = source.split("  qdrant:", 1)[1].split("  neo4j:", 1)[0]
    neo4j_block = source.split("  neo4j:", 1)[1].split("networks:", 1)[0]

    assert "ports:" in app_block
    assert "ports:" not in qdrant_block
    assert "ports:" not in neo4j_block
    assert 'expose:\n      - "6333"' in qdrant_block
    assert "http://127.0.0.1:6333/readyz" in qdrant_block


def test_services_use_stable_local_images_and_bounded_logs():
    source = COMPOSE.read_text(encoding="utf-8")

    assert "image: python_self_agent-app:local" in source
    assert "image: python_self_agent-qdrant:local" in source
    assert source.count("driver: local") == 3
    assert source.count('max-size: "10m"') == 3
    assert source.count('max-file: "5"') == 3


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


def test_qdrant_uses_stable_posix_named_volume():
    source = COMPOSE.read_text(encoding="utf-8")
    env_source = ENV_EXAMPLE.read_text(encoding="utf-8")
    qdrant_block = source.split("  qdrant:", 1)[1].split("  neo4j:", 1)[0]

    assert "type: volume" in qdrant_block
    assert "source: qdrant_data" in qdrant_block
    assert "target: /qdrant/storage" in qdrant_block
    assert '${DEPLOY_DATA_ROOT:-./deploy-data}/qdrant' not in qdrant_block
    assert "name: ${QDRANT_VOLUME_NAME:-zhiyan_qdrant_data}" in source
    assert "QDRANT_VOLUME_NAME=zhiyan_qdrant_data" in env_source


def test_environment_template_contains_operations_roots_and_cooldown():
    source = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert "DEPLOY_STATE_ROOT=./deploy-state" in source
    assert "DEPLOY_BACKUP_ROOT=D:/python_self_agent_backups" in source
    assert "OPERATIONS_NOTIFY_COOLDOWN_MINUTES=30" in source
