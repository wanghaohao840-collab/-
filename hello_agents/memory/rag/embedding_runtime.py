from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from hello_agents.memory.embedding import SimpleEmbedding
from hello_agents.memory.rag.embedding_client import SiliconFlowEmbedding
from hello_agents.memory.rag.embedding_profile import (
    EmbeddingFailure, EmbeddingProfile, EmbeddingSettings, normalize_vector,
)

CHUNKING_BY_BACKEND = {
    "json": "json-paragraph-char800-overlap120-v1",
    "qdrant": "qdrant-window-char800-overlap120-v1",
}


def profile_for_backend(profile: EmbeddingProfile, backend: str) -> EmbeddingProfile:
    if not isinstance(backend, str) or backend not in CHUNKING_BY_BACKEND:
        raise EmbeddingFailure("configuration")
    return replace(
        profile, chunking=CHUNKING_BY_BACKEND[backend],
        document_preprocessing="identity-v1", query_preprocessing="identity-v1",
        normalization="l2-v1", distance="Cosine",
    )


def validate_texts(texts: object) -> None:
    if not isinstance(texts, list):
        raise EmbeddingFailure("input")
    for text in texts:
        try:
            valid = (isinstance(text, str) and bool(text.strip())
                     and len(text.encode("utf-8")) <= 6000)
        except UnicodeEncodeError:
            valid = False
        if not valid:
            raise EmbeddingFailure("input")


def validate_matrix(value: object, count: int, dimension: int) -> list[list[float]]:
    if not isinstance(value, list) or len(value) != count:
        raise EmbeddingFailure("response_schema")
    return [normalize_vector(vector, dimension) for vector in value]


@dataclass(frozen=True)
class RAGEmbeddingRuntime:
    profile: EmbeddingProfile
    _engine: Any = field(repr=False, compare=False)
    batch_size: int = 8

    def __post_init__(self) -> None:
        # An identity may describe a future algorithm, but this runtime must
        # never claim to execute a transform that it does not implement.
        if (not isinstance(self.profile, EmbeddingProfile)
                or type(self.profile.dimension) is not int
                or self.profile.chunking not in CHUNKING_BY_BACKEND.values()
                or self.profile.document_preprocessing != "identity-v1"
                or self.profile.query_preprocessing != "identity-v1"
                or self.profile.normalization != "l2-v1"
                or self.profile.distance != "Cosine"):
            raise EmbeddingFailure("configuration")
        if type(self.batch_size) is not int or not 1 <= self.batch_size <= 8:
            raise EmbeddingFailure("configuration")
        if self.profile.provider not in {"simple", "siliconflow"}:
            raise EmbeddingFailure("configuration")
        if self.profile.provider == "simple":
            expected = replace(EmbeddingSettings.from_env({}).profile,
                               chunking=self.profile.chunking)
            if (self.profile != expected
                    or not isinstance(self._engine, SimpleEmbedding)
                    or self._engine.dimension != self.profile.dimension):
                raise EmbeddingFailure("configuration")
        elif (not isinstance(self._engine, SiliconFlowEmbedding)
              or self._engine.profile != self.profile
              or self._engine.settings.batch_size != self.batch_size):
            raise EmbeddingFailure("configuration")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        validate_texts(texts)
        if not texts:
            return []
        if self.profile.provider == "simple":
            raw = self._engine.encode(texts)
        else:
            raw = self._engine.embed_documents(texts)
        return validate_matrix(raw, len(texts), self.profile.dimension)

    def embed_query(self, text: str) -> list[float]:
        validate_texts([text])
        if self.profile.provider == "simple":
            raw = self._engine.encode(text)
        else:
            raw = self._engine.embed_query(text)
        return normalize_vector(raw, self.profile.dimension)


def build_rag_embedding(
    values: Mapping[str, str | None], *, backend: str, transport=None,
) -> RAGEmbeddingRuntime:
    settings = EmbeddingSettings.from_env(values)
    profile = profile_for_backend(settings.profile, backend)
    settings = replace(settings, profile=profile)
    if profile.provider == "simple":
        engine = SimpleEmbedding(dimension=profile.dimension)
    else:
        engine = SiliconFlowEmbedding(settings, transport=transport)
    return RAGEmbeddingRuntime(profile, engine, settings.batch_size)
