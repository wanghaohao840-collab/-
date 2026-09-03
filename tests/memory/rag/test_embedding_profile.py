# tests/memory/rag/test_embedding_profile.py
from dataclasses import replace
import math
import pytest

from hello_agents.memory.rag.embedding_profile import (
    EmbeddingFailure, EmbeddingSettings, normalize_vector,
)


def test_default_is_legacy_and_candidate_is_independent():
    values = {"LLM_API_KEY": "not-an-embedding-key"}
    assert EmbeddingSettings.from_env(values).profile.dimension == 384
    with pytest.raises(EmbeddingFailure, match="configuration"):
        EmbeddingSettings.from_env(values, candidate=True)
    values["RAG_EMBEDDING_API_KEY"] = "private-test-token"
    active = EmbeddingSettings.from_env(values)
    candidate = EmbeddingSettings.from_env(values, candidate=True)
    assert active.profile.provider == "simple"
    assert candidate.profile.model == "BAAI/bge-m3"
    assert candidate.profile.dimension == 1024
    assert "private-test-token" not in repr(candidate)
    assert "not-an-embedding-key" not in repr(candidate)
    assert "RAG_EMBEDDING_PROVIDER" not in values


def test_profile_changes_only_for_vector_space_changes():
    first = EmbeddingSettings.from_env(
        {"RAG_EMBEDDING_API_KEY": "one"}, candidate=True
    )
    second = EmbeddingSettings.from_env(
        {"RAG_EMBEDDING_API_KEY": "two"}, candidate=True
    )
    assert first.profile.fingerprint == second.profile.fingerprint
    assert first.profile.fingerprint != replace(
        first.profile, revision="next"
    ).fingerprint
    assert first.profile.fingerprint != replace(
        first.profile, query_preprocessing="query-v2"
    ).fingerprint
    assert len(first.profile.fingerprint) == 64


@pytest.mark.parametrize("key,value", [
    ("PROVIDER", "unknown"), ("MODEL", "Pro/BAAI/bge-m3"),
    ("DIMENSION", "384"), ("DIMENSION", "bad"),
    ("BASE_URL", "http://api.siliconflow.cn/v1"),
    ("BASE_URL", "https://secret@api.siliconflow.cn/v1"),
    ("BASE_URL", "https://api.siliconflow.cn/v1?key=secret"),
    ("BASE_URL", "https://api.siliconflow.cn/v1#secret"),
    ("BASE_URL", "https://other.invalid/v1"),
    ("TIMEOUT_SECONDS", "nan"), ("TIMEOUT_SECONDS", "0"),
    ("TIMEOUT_SECONDS", "21"), ("MAX_RETRIES", "3"),
    ("MAX_RETRIES", "-1"), ("BATCH_SIZE", "0"),
    ("BATCH_SIZE", "9"), ("REVISION", ""),
    ("API_KEY", ""), ("API_KEY", "bad\nheader"),
])
def test_invalid_remote_configuration_is_safe(key, value):
    values = {"RAG_EMBEDDING_PROVIDER": "siliconflow",
              "RAG_EMBEDDING_API_KEY": "private-test-token"}
    values["RAG_EMBEDDING_" + key] = value
    with pytest.raises(EmbeddingFailure) as caught:
        EmbeddingSettings.from_env(values)
    assert "private-test-token" not in str(caught.value)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("value", [
    [], [1], [0, 0], [float("nan"), 1], [float("inf"), 1],
    [True, 1], ["1", 2], [[1], [2]], None,
])
def test_bad_vectors_are_rejected(value):
    with pytest.raises(EmbeddingFailure):
        normalize_vector(value, 2)


def test_normalize_without_padding_or_overflow():
    assert normalize_vector([3, 4], 2) == [0.6, 0.8]
    result = normalize_vector([1e308, 1e308], 2)
    assert math.isclose(math.hypot(*result), 1.0)


@pytest.mark.parametrize("changes", [
    {"api_key": "${SECRET}"}, {"api_key": "bad\nheader"},
    {"timeout_seconds": 21}, {"timeout_seconds": float("nan")},
    {"max_retries": 3}, {"max_retries": True},
    {"batch_size": 0}, {"batch_size": 9},
])
def test_direct_settings_cannot_bypass_safety_limits(changes):
    original = EmbeddingSettings.from_env(
        {"RAG_EMBEDDING_API_KEY": "one"}, candidate=True
    )
    with pytest.raises(EmbeddingFailure, match="configuration"):
        replace(original, **changes)


@pytest.mark.parametrize("changes", [
    {"endpoint": "http://api.siliconflow.cn/v1"},
    {"endpoint": "https://other.invalid/v1"},
    {"model": "Pro/BAAI/bge-m3"}, {"dimension": 384},
])
def test_direct_profile_cannot_redirect_credentials(changes):
    original = EmbeddingSettings.from_env(
        {"RAG_EMBEDDING_API_KEY": "one"}, candidate=True
    )
    with pytest.raises(EmbeddingFailure, match="configuration"):
        replace(original, profile=replace(original.profile, **changes))
