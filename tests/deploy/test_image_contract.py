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


def test_qdrant_probe_image_preserves_the_pinned_base():
    source = (ROOT / "deploy" / "qdrant.Dockerfile").read_text(encoding="utf-8")

    assert "FROM qdrant/qdrant:v1.18.2" in source
    assert "wget" in source
    assert "rm -rf /var/lib/apt/lists/*" in source
