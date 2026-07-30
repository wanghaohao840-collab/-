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


def test_environment_template_contains_no_real_secret():
    source = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert "LLM_API_KEY=" in source
    assert "QDRANT_URL=http://qdrant:6333" in source
    assert "NEO4J_PASSWORD=" in source
    assert "replace" in source.lower()
    assert "sk-" not in source
