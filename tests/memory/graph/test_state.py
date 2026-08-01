from __future__ import annotations

import json

import pytest

from hello_agents.memory.graph.state import (
    GraphStateCorruptionError,
    GraphStateRepository,
    sanitize_error,
)


def test_state_repository_persists_atomically_without_chunk_content(tmp_path):
    path = tmp_path / "graph-status.json"
    repository = GraphStateRepository(path)

    saved = repository.upsert(
        "doc-1",
        status="building",
        build_id="build-1",
        attempt_count=1,
        llm_attempt_count=2,
    )

    assert repository.get("doc-1") == saved
    assert json.loads(path.read_text(encoding="utf-8"))["documents"]["doc-1"]["status"] == "building"
    assert "chunk" not in path.read_text(encoding="utf-8").lower()
    assert list(tmp_path.glob("*.tmp")) == []


def test_update_preserves_counts_and_lists_by_status(tmp_path):
    repository = GraphStateRepository(tmp_path / "state.json")
    repository.upsert(
        "a", status="building", build_id="1", attempt_count=2, llm_attempt_count=3
    )
    repository.upsert("a", status="ready", build_id="1")
    repository.upsert("b", status="failed", build_id="2")

    assert repository.get("a")["attempt_count"] == 2
    assert repository.get("a")["llm_attempt_count"] == 3
    assert [row["document_id"] for row in repository.list_by_status("failed")] == ["b"]


def test_error_sanitization_removes_secrets_and_limits_to_500_characters():
    message = (
        "neo4j://user:password@secret-host "
        "token=top-secret "
        + "x" * 700
    )

    sanitized = sanitize_error(
        message,
        secrets=["top-secret", "password", "secret-host"],
    )

    assert len(sanitized) == 500
    assert sanitized.endswith("…")
    assert "top-secret" not in sanitized
    assert "password" not in sanitized
    assert "secret-host" not in sanitized


def test_corrupt_state_manifest_fails_closed(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(GraphStateCorruptionError):
        GraphStateRepository(path).get("doc-1")
