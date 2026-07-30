from pathlib import Path


def test_root_readme_exposes_the_supported_deployment_path():
    source = (
        Path(__file__).parents[2] / "README.md"
    ).read_text(encoding="utf-8")

    assert "Docker 单节点部署" in source
    assert "docker compose --env-file deploy/.env up -d --build" in source
    assert "deploy/README.md" in source
    assert "Neo4j" in source
    assert "单副本" in source
