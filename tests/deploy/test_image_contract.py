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


def test_dockerignore_excludes_acl_prone_test_and_runtime_roots():
    patterns = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())

    assert {
        ".runtime/",
        ".pytest_cache/",
        ".pytest-tmp-*/",
        ".pytest-*/",
        ".operations-test/",
        ".operations-test-*/",
    } <= patterns


def test_dockerignore_excludes_local_environment_and_generated_roots():
    patterns = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())

    assert {
        "venv/",
        ".venv/",
        ".superpowers/",
        ".worktrees/",
        ".idea/",
        "deploy-state/",
        "memory_data/",
        "**/memory_data/",
        "**/rag_cache/",
        "ui/knowledge_base/uploads/",
        "ui/reports/",
        "knowledge_base/.graph/",
    } <= patterns


def test_qdrant_probe_image_uses_pinned_base_and_removes_complete_web_ui():
    source = (ROOT / "deploy" / "qdrant.Dockerfile").read_text(encoding="utf-8")

    expected_base = (
        "FROM qdrant/qdrant:v1.18.3@sha256:"
        "0bd98fa7977f1e75694779359ca4e212822e5a71334e28421182f72f209d5286"
    )
    expected_inventory = "/qdrant/static/qdrant-web-ui.spdx.json"

    assert expected_base in source
    assert f"test -f {expected_inventory}" in source
    assert "rm -rf /qdrant/static" in source
    assert "rm -f /qdrant/static/qdrant-web-ui.spdx.json" not in source
    assert "install -d -o 0 -g 0 -m 0755 /qdrant/static" in source
    assert source.index(f"test -f {expected_inventory}") < source.index(
        "rm -rf /qdrant/static"
    )
    assert "wget" in source
    assert "rm -rf /var/lib/apt/lists/*" in source
