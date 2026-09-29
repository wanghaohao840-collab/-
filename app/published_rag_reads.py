"""Request-scoped, read-only RAG operations over a PostgreSQL vector head.

The caller supplies the authenticated tenant. Opening an operation resolves one
head; every vector and lexical subread uses the resulting immutable Qdrant view.
This module does not wire distributed application bootstrap or document writes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.postgres_vector_generations import VectorHead, VectorScope
from app.vector_generation_service import VectorGenerationService
from hello_agents.memory.rag.embedding_runtime import RAGEmbeddingRuntime
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.rag.index_registry import IndexRegistryError
from hello_agents.memory.rag.qdrant_pipeline import RAGPipeline
from hello_agents.memory.storage.generation_vector_store import GenerationVectorStore


@dataclass(frozen=True, slots=True)
class _PinnedIndexAuthority:
    """A published PG head, never a local registry file, grants read access."""

    identity: IndexIdentity
    head: VectorHead

    def require_active(self, expected: IndexIdentity) -> VectorHead:
        if expected != self.identity or self.head.state not in ("published", "empty"):
            raise IndexRegistryError("identity")
        return self.head

    def mark_failed_if_active(self, expected: IndexIdentity) -> bool:
        raise IndexRegistryError("read_only")


@dataclass(frozen=True, slots=True)
class PublishedRAGReadOperation:
    """One public operation; its pipeline and vector head are never shared."""

    head: VectorHead
    view: GenerationVectorStore
    _pipeline: RAGPipeline = field(repr=False)

    def search(self, query: str, *, limit: int = 5, min_score: float = 0.0,
               document_id: str | None = None,
               document_ids: list[str] | None = None, **kwargs: Any) -> list[dict]:
        return self._pipeline.search(query, limit=limit, min_score=min_score,
                                     document_id=document_id,
                                     document_ids=document_ids, **kwargs)

    def stats(self) -> dict:
        return self._pipeline.stats()

    def get_document_summary_context(self, document_id: str,
                                     limit: int = 12) -> list[dict]:
        return self._pipeline.get_document_summary_context(document_id, limit)

    def get_document_chunk(self, document_id: str, chunk_id: str,
                           chunk_index: int) -> dict | None:
        return self._pipeline.get_document_chunk(document_id, chunk_id, chunk_index)

    def get_document_chunks(self, document_id: str) -> list[dict]:
        return self._pipeline.get_document_chunks(document_id)

    def list_document_ids(self) -> list[str]:
        return self._pipeline.list_document_ids()

    def count(self, document_id: str | None = None) -> int:
        return self._pipeline._count(document_id)

    def scroll_payloads(self, document_id: str | None = None) -> list[dict]:
        return self._pipeline._scroll_payloads(document_id)

    def max_chunk_index(self, document_id: str) -> int:
        return self._pipeline._max_chunk_index(document_id)


class PublishedRAGReadFactory:
    """Build a fresh pinned reader for a trusted tenant and RAG namespace."""

    def __init__(self, service: VectorGenerationService,
                 embedding_runtime: RAGEmbeddingRuntime,
                 index_identity: IndexIdentity):
        if not isinstance(service, VectorGenerationService):
            raise TypeError("Vector generation service is required")
        if (not isinstance(embedding_runtime, RAGEmbeddingRuntime)
                or not isinstance(index_identity, IndexIdentity)
                or index_identity.backend != "qdrant"
                or embedding_runtime.profile != index_identity.profile):
            raise ValueError("Trusted Qdrant runtime and full index identity must match")
        self._service = service
        self._runtime = embedding_runtime
        self._identity = index_identity

    def open_operation(self, tenant_id: str,
                       rag_namespace: str) -> PublishedRAGReadOperation:
        scope = VectorScope(tenant_id, "rag", rag_namespace, self._identity)
        view = self._service.read_view(scope)  # one PostgreSQL head resolution
        registry = _PinnedIndexAuthority(self._identity, view.head)
        pipeline = RAGPipeline(
            collection_name=self._identity.base_collection,
            rag_namespace=rag_namespace,
            vector_store=view,
            embedding_runtime=self._runtime,
            index_registry=registry,
        )
        return PublishedRAGReadOperation(view.head, view, pipeline)
