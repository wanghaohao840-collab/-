from dataclasses import replace

import pytest

from hello_agents.memory.rag.embedding_runtime import build_rag_embedding
from hello_agents.memory.rag.index_cutover import (
    CutoverFailure,
    apply_cutover,
    begin_cutover,
    complete_cutover,
    require_no_incomplete_cutover,
    rollback_cutover,
)
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.rag.index_registry import (
    IndexRecord,
    IndexRegistry,
    IndexValidation,
)
from hello_agents.memory.rag.pipeline import resolve_rag_embedding_dependencies


def record(revision: str, migration_id: str) -> IndexRecord:
    runtime = build_rag_embedding(
        {
            "RAG_EMBEDDING_PROVIDER": "siliconflow",
            "RAG_EMBEDDING_API_KEY": "fake-key",
            "RAG_EMBEDDING_REVISION": revision,
        },
        backend="qdrant",
    )
    return IndexRecord(
        identity=IndexIdentity("qdrant", "docs", runtime.profile),
        source_index="docs",
        migration_id=migration_id,
        validation=IndexValidation(
            True,
            "2026-09-03T12:00:00Z",
            2,
            "a" * 64,
            0,
            1.0,
            1.0,
        ),
        state="active",
    )


def test_cutover_blocks_startup_until_complete(tmp_path) -> None:
    registry = IndexRegistry(tmp_path.resolve())
    old = record("old-v1", "old")
    candidate = record("new-v2", "new")
    registry.save([old])

    assert begin_cutover(registry, candidate).state == "prepared"
    with pytest.raises(CutoverFailure, match="incomplete"):
        require_no_incomplete_cutover(registry)
    with pytest.raises(CutoverFailure, match="incomplete"):
        resolve_rag_embedding_dependencies(
            backend="qdrant",
            data_root=tmp_path.resolve(),
            values={"RAG_EMBEDDING_PROVIDER": "simple"},
        )

    assert apply_cutover(registry, "new").state == "registry_written"
    assert registry.require_active(candidate.identity) == candidate
    assert apply_cutover(registry, "new").state == "registry_written"
    assert complete_cutover(registry, "new").state == "complete"
    require_no_incomplete_cutover(registry)


def test_precomplete_rollback_restores_previous_record(tmp_path) -> None:
    registry = IndexRegistry(tmp_path.resolve())
    old = record("old-v1", "old")
    candidate = record("new-v2", "new")
    registry.save([old])
    begin_cutover(registry, candidate)
    apply_cutover(registry, "new")

    assert rollback_cutover(registry, "new").state == "rolled_back"
    assert registry.require_active(old.identity) == old
    require_no_incomplete_cutover(registry)


def test_completed_cutover_cannot_fast_rollback_without_data_check(tmp_path) -> None:
    registry = IndexRegistry(tmp_path.resolve())
    candidate = record("new-v2", "new")
    begin_cutover(registry, candidate)
    apply_cutover(registry, "new")
    complete_cutover(registry, "new")

    with pytest.raises(CutoverFailure, match="rollback_requires_data_check"):
        rollback_cutover(registry, "new")
