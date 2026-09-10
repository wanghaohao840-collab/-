from copy import deepcopy
from dataclasses import replace
import json

import pytest

from hello_agents.memory.rag.embedding_runtime import build_rag_embedding
from hello_agents.memory.rag.index_identity import (
    IndexIdentity, IndexIdentityError, physical_collection_name, require_point_identity,
)


def identity():
    runtime = build_rag_embedding(
        {"RAG_EMBEDDING_PROVIDER": "siliconflow", "RAG_EMBEDDING_API_KEY": "fake-secret"},
        backend="qdrant",
    )
    return IndexIdentity("qdrant", "doc_learning_vectors", runtime.profile)


def test_round_trip_contains_no_key_or_runtime_configuration():
    expected = identity()
    data = expected.to_dict()
    assert IndexIdentity.from_dict(json.loads(json.dumps(data))) == expected
    assert "fake-secret" not in json.dumps(data)
    assert data["physical_collection"].endswith(expected.profile.fingerprint[:16])


@pytest.mark.parametrize("kind", ["schema", "extra_key", "profile_key", "fingerprint",
                                  "name", "dimension", "backend"])
def test_corrupt_identity_is_rejected_safely(kind):
    data = deepcopy(identity().to_dict())
    if kind == "schema": data["schema_version"] = True
    elif kind == "extra_key": data["api_key"] = "secret-body"
    elif kind == "profile_key": data["profile"]["api_key"] = "secret-body"
    elif kind == "fingerprint": data["fingerprint"] = "0" * 64
    elif kind == "name": data["physical_collection"] = "other"
    elif kind == "dimension": data["profile"]["dimension"] = True
    elif kind == "backend": data["backend"] = []
    with pytest.raises(IndexIdentityError) as caught:
        IndexIdentity.from_dict(data)
    assert "secret-body" not in str(caught.value)


def test_same_dimension_does_not_mean_same_model_identity():
    original = identity()
    changed = replace(original, profile=replace(original.profile, revision="v2"))
    assert original.profile.dimension == changed.profile.dimension
    with pytest.raises(IndexIdentityError, match="mismatch"):
        original.require_match(changed)


@pytest.mark.parametrize(("name", "value"), [
    ("provider", "another-provider"),
    ("endpoint", "https://example.invalid/v1"),
    ("model", "another-model"),
    ("dimension", 1023),
    ("normalization", "another-normalization"),
    ("document_preprocessing", "another-document-transform"),
    ("query_preprocessing", "another-query-transform"),
    ("chunking", "another-chunking"),
])
def test_each_algorithm_identity_change_requires_migration(name, value):
    original = identity()
    changed = replace(original, profile=replace(original.profile, **{name: value}))
    assert changed.profile.fingerprint != original.profile.fingerprint
    with pytest.raises(IndexIdentityError, match="mismatch"):
        original.require_match(changed)


def test_shortened_name_is_not_the_identity(monkeypatch):
    original = identity()
    changed = replace(original, profile=replace(original.profile, revision="v2"))
    monkeypatch.setattr(IndexIdentity, "physical_collection", property(lambda self: "collision"))
    assert original.physical_collection == changed.physical_collection
    with pytest.raises(IndexIdentityError, match="mismatch"):
        original.require_match(changed)


@pytest.mark.parametrize("base", ["", "../escape", "bad.name", "x" * 238])
def test_collection_name_rejects_unsafe_or_oversized_base(base):
    with pytest.raises(IndexIdentityError):
        physical_collection_name(base, "a" * 64)
    assert len(physical_collection_name("x" * 237, "a" * 64)) == 255


@pytest.mark.parametrize("changes", [
    {"embedding_fingerprint": "wrong"}, {"rag_namespace": "another-user"},
    {"document_id": "another-document"}, {"document_id": None},
])
def test_point_requires_fingerprint_and_scope(changes):
    expected = identity()
    metadata = {"embedding_fingerprint": expected.profile.fingerprint,
                "rag_namespace": "user-a", "document_id": "doc-a"}
    require_point_identity(metadata, identity=expected,
                           rag_namespace="user-a", document_id="doc-a")
    metadata.update(changes)
    with pytest.raises(IndexIdentityError, match="point"):
        require_point_identity(metadata, identity=expected,
                               rag_namespace="user-a", document_id="doc-a")
