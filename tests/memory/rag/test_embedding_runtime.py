from dataclasses import replace
import json

import httpx
import pytest

from hello_agents.memory import embedding as legacy
from hello_agents.memory.rag.embedding_profile import EmbeddingFailure
from hello_agents.memory.rag.embedding_runtime import build_rag_embedding


def remote_values(key="fake-embedding-key"):
    return {"RAG_EMBEDDING_PROVIDER": "siliconflow", "RAG_EMBEDDING_API_KEY": key}


def response(count):
    return {"model": "BAAI/bge-m3", "data": [
        {"index": i, "embedding": [1.0] + [0.0] * 1023} for i in range(count)
    ]}


def test_explicit_values_do_not_inherit_llm_or_process_credentials(monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_API_KEY", "process-secret")
    with pytest.raises(EmbeddingFailure):
        build_rag_embedding({"RAG_EMBEDDING_PROVIDER": "siliconflow",
                             "LLM_API_KEY": "llm-secret"}, backend="json")


def test_local_runtime_does_not_change_memory_singleton(monkeypatch):
    singleton = legacy.SimpleEmbedding(17)
    monkeypatch.setattr(legacy, "_embedder", singleton)
    runtime = build_rag_embedding({}, backend="json")
    assert len(runtime.embed_query("public")) == 384
    assert legacy.get_text_embedder() is singleton
    assert len(singleton.encode("public")) == 17


def test_backend_identity_and_key_rotation_are_precise():
    first = build_rag_embedding(remote_values("one"), backend="json")
    rotated = build_rag_embedding(remote_values("two"), backend="json")
    qdrant = build_rag_embedding(remote_values("one"), backend="qdrant")
    assert first.profile.fingerprint == rotated.profile.fingerprint
    assert first.profile.fingerprint != qdrant.profile.fingerprint
    assert first.profile.chunking == "json-paragraph-char800-overlap120-v1"
    assert qdrant.profile.chunking == "qdrant-window-char800-overlap120-v1"
    assert "fake-embedding-key" not in repr(build_rag_embedding(
        remote_values(), backend="json"))


def test_remote_documents_and_query_use_one_client_profile():
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body["input"])
        return httpx.Response(200, json=response(len(body["input"])))
    runtime = build_rag_embedding(remote_values(), backend="qdrant",
                                  transport=httpx.MockTransport(handler))
    assert len(runtime.embed_documents(["one", "two"])) == 2
    assert len(runtime.embed_query("question")) == 1024
    assert calls == [["one", "two"], ["question"]]


@pytest.mark.parametrize("texts", [[""], ["x" * 6001], ["文" * 2001], ["\ud800"], "text"])
def test_validation_precedes_network(texts):
    def forbidden(request):
        pytest.fail("invalid input reached transport")
    runtime = build_rag_embedding(remote_values(), backend="json",
                                  transport=httpx.MockTransport(forbidden))
    with pytest.raises(EmbeddingFailure, match="input"):
        runtime.embed_documents(texts)
    assert runtime.embed_documents([]) == []


def test_mismatched_runtime_engine_is_rejected():
    runtime = build_rag_embedding(remote_values(), backend="json")
    with pytest.raises(EmbeddingFailure, match="configuration"):
        replace(runtime, profile=replace(runtime.profile, revision="another"))
    with pytest.raises(EmbeddingFailure, match="configuration"):
        replace(runtime, batch_size=9)


def test_remote_error_never_falls_back():
    runtime = build_rag_embedding(remote_values(), backend="json",
        transport=httpx.MockTransport(lambda request: httpx.Response(401)))
    with pytest.raises(EmbeddingFailure) as caught:
        runtime.embed_query("public")
    assert caught.value.status_code == 401


def test_unknown_backend_is_rejected():
    with pytest.raises(EmbeddingFailure, match="configuration"):
        build_rag_embedding({}, backend="unrecognized")


@pytest.mark.parametrize("bad", [
    [], [0.0] * 384, [1.0] * 383, [float("nan")] * 384,
    [float("inf")] * 384, [True] * 384,
])
def test_runtime_rejects_invalid_local_vector_without_repair(monkeypatch, bad):
    runtime = build_rag_embedding({}, backend="json")
    monkeypatch.setattr(runtime._engine, "encode",
                        lambda texts: [bad] if isinstance(texts, list) else bad)
    with pytest.raises(EmbeddingFailure, match="response_vector"):
        runtime.embed_query("public")
    with pytest.raises(EmbeddingFailure, match="response_vector"):
        runtime.embed_documents(["public"])


def test_runtime_rejects_wrong_document_vector_count(monkeypatch):
    runtime = build_rag_embedding({}, backend="json")
    monkeypatch.setattr(runtime._engine, "encode", lambda texts: [])
    with pytest.raises(EmbeddingFailure, match="response_schema"):
        runtime.embed_documents(["public"])


@pytest.mark.parametrize("changes", [
    {"model": "another-model"}, {"revision": "another-revision"},
    {"endpoint": "https://example.invalid"}, {"dimension": 384.0},
    {"normalization": "unknown-v2"}, {"document_preprocessing": "unknown-v2"},
    {"query_preprocessing": "unknown-v2"}, {"chunking": "unknown-v2"},
    {"distance": "Dot"},
])
def test_local_runtime_cannot_claim_an_unimplemented_profile(changes):
    runtime = build_rag_embedding({}, backend="json")
    with pytest.raises(EmbeddingFailure, match="configuration"):
        replace(runtime, profile=replace(runtime.profile, **changes))


def test_remote_runtime_rejects_unimplemented_transform_even_when_engine_matches():
    from hello_agents.memory.rag.embedding_client import SiliconFlowEmbedding
    from hello_agents.memory.rag.embedding_runtime import RAGEmbeddingRuntime

    runtime = build_rag_embedding(remote_values(), backend="json")
    profile = replace(runtime.profile, normalization="unimplemented-v2")
    engine = SiliconFlowEmbedding(replace(runtime._engine.settings, profile=profile))
    with pytest.raises(EmbeddingFailure, match="configuration"):
        RAGEmbeddingRuntime(profile, engine)
