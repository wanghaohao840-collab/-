import json
from dataclasses import replace
from pathlib import Path

import pytest

import hello_agents.memory.rag.index_registry as registry_module
from hello_agents.memory.rag.embedding_runtime import build_rag_embedding
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.rag.index_registry import (
    IndexRecord, IndexRegistry, IndexRegistryError, IndexValidation,
)


def identity(*, revision="siliconflow-bge-m3-v1"):
    runtime = build_rag_embedding(
        {
            "RAG_EMBEDDING_PROVIDER": "siliconflow",
            "RAG_EMBEDDING_API_KEY": "fake-private-key",
            "RAG_EMBEDDING_REVISION": revision,
        },
        backend="qdrant",
    )
    return IndexIdentity("qdrant", "doc_learning_vectors", runtime.profile)


def validation(*, passed=True, scope_leaks=0):
    return IndexValidation(
        passed=passed,
        checked_at="2026-09-03T12:00:00Z",
        chunk_count=3,
        content_digest="a" * 64,
        scope_leaks=scope_leaks,
        recall_at_5=0.95,
        mrr_at_5=0.80,
    )


_DEFAULT_VALIDATION = object()


def record(*, expected=None, state="active", checked=_DEFAULT_VALIDATION):
    return IndexRecord(
        identity=expected or identity(),
        source_index="doc_learning_vectors",
        migration_id="migration-20260903",
        validation=validation() if checked is _DEFAULT_VALIDATION else checked,
        state=state,
    )


def test_registry_uses_explicit_app_data_root_and_round_trips_without_key(tmp_path):
    registry = IndexRegistry(tmp_path)
    expected = record()

    registry.save([expected])

    assert registry.path == (
        tmp_path / "vector_indexes" / "rag" / "registry.json"
    ).resolve()
    assert registry.require_active(expected.identity) == expected
    raw = registry.path.read_text(encoding="utf-8")
    assert "fake-private-key" not in raw
    assert json.loads(raw)["schema_version"] == 1


def test_registry_does_not_follow_working_directory(tmp_path, monkeypatch):
    registry = IndexRegistry(tmp_path / "data")
    expected = record()
    registry.save([expected])
    another = tmp_path / "another"
    another.mkdir()
    monkeypatch.chdir(another)

    assert registry.require_active(expected.identity) == expected
    assert not (another / "vector_indexes").exists()


@pytest.mark.parametrize("payload", [
    "{broken",
    json.dumps({"schema_version": 1, "entries": [], "api_key": "private-body"}),
    json.dumps({"schema_version": True, "entries": {}}),
])
def test_missing_or_corrupt_registry_fails_closed_without_echoing_content(
    tmp_path, payload
):
    registry = IndexRegistry(tmp_path)
    registry.registry_root.mkdir(parents=True)
    registry.path.write_text(payload, encoding="utf-8")

    with pytest.raises(IndexRegistryError) as caught:
        registry.load()
    assert "private-body" not in str(caught.value)


def test_missing_active_registry_and_same_dimension_different_model_fail(tmp_path):
    registry = IndexRegistry(tmp_path)
    with pytest.raises(IndexRegistryError, match="missing"):
        registry.require_active(identity())

    expected = record()
    registry.save([expected])
    changed = replace(
        expected.identity,
        profile=replace(expected.identity.profile, revision="different-v2"),
    )
    with pytest.raises(IndexRegistryError):
        registry.require_active(changed)


@pytest.mark.parametrize(("state", "checked"), [
    ("active", None),
    ("validated", IndexValidation(
        passed=False,
        checked_at="2026-09-03T12:00:00Z",
        chunk_count=3,
        content_digest="a" * 64,
        scope_leaks=0,
    )),
    ("active", IndexValidation(
        passed=True,
        checked_at="2026-09-03T12:00:00Z",
        chunk_count=3,
        content_digest="a" * 64,
        scope_leaks=1,
    )),
])
def test_validated_or_active_records_require_passing_leak_free_validation(
    state, checked
):
    with pytest.raises(IndexRegistryError, match="unvalidated"):
        record(state=state, checked=checked)


def test_failed_atomic_replace_preserves_previous_registry(tmp_path, monkeypatch):
    registry = IndexRegistry(tmp_path)
    original = record()
    registry.save([original])
    before = registry.path.read_bytes()

    def fail_replace(source, destination):
        raise OSError("forced failure")

    monkeypatch.setattr(registry_module.os, "replace", fail_replace)
    with pytest.raises(IndexRegistryError, match="write"):
        registry.put(record(expected=identity(revision="v2"), state="planned",
                           checked=None))

    assert registry.path.read_bytes() == before
    assert list(registry.registry_root.glob("*.tmp")) == []


def test_relative_data_root_and_unknown_fields_are_rejected(tmp_path):
    with pytest.raises(IndexRegistryError, match="data_root"):
        IndexRegistry(Path("relative-data"))

    registry = IndexRegistry(tmp_path)
    raw = record().to_dict()
    raw["api_key"] = "private-body"
    registry.registry_root.mkdir(parents=True)
    registry.path.write_text(json.dumps({
        "schema_version": 1,
        "entries": {"qdrant:doc_learning_vectors": raw},
    }), encoding="utf-8")
    with pytest.raises(IndexRegistryError) as caught:
        registry.load()
    assert "private-body" not in str(caught.value)


@pytest.mark.parametrize("checked_at", [
    "", "not-a-time", "2026-09-03T12:00:00", "2026-09-03T12:00:00+08:00",
])
def test_validation_requires_an_auditable_utc_timestamp(checked_at):
    with pytest.raises(IndexRegistryError, match="validation"):
        replace(validation(), checked_at=checked_at)


@pytest.mark.parametrize(("recall", "mrr"), [
    (None, 0.80), (0.95, None), (0.89, 0.80), (0.95, 0.74),
])
def test_nonempty_remote_index_cannot_be_active_below_quality_gate(recall, mrr):
    checked = replace(validation(), recall_at_5=recall, mrr_at_5=mrr)
    with pytest.raises(IndexRegistryError, match="quality"):
        record(state="active", checked=checked)


def test_empty_remote_index_can_be_explicitly_active_without_retrieval_scores():
    checked = replace(
        validation(), chunk_count=0, recall_at_5=None, mrr_at_5=None
    )
    assert record(state="active", checked=checked).state == "active"


def test_conditional_failure_preserves_other_entries_and_is_idempotent(tmp_path):
    registry = IndexRegistry(tmp_path)
    current = record()
    other = record(expected=replace(current.identity, base_collection="other_docs"))
    registry.save([current, other])

    assert registry.mark_failed_if_active(current.identity) is True
    assert registry.require_active(other.identity) == other
    before = registry.path.read_bytes()
    assert registry.mark_failed_if_active(current.identity) is False
    assert registry.path.read_bytes() == before


def test_conditional_failure_does_not_overwrite_same_identity_staging(tmp_path):
    registry = IndexRegistry(tmp_path)
    expected = identity()
    registry.save([record(expected=expected, state="building", checked=None)])
    before = registry.path.read_bytes()

    assert registry.mark_failed_if_active(expected) is False
    assert registry.path.read_bytes() == before
