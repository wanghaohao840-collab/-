"""Prepare one pinned import's bytes for a later, fenced publication.

This module parses and embeds in memory. It does not read an object store,
claim a task, write vectors, or publish a document.
"""
from __future__ import annotations

from copy import copy, deepcopy
from io import BytesIO
from pathlib import PurePosixPath
from uuid import UUID

from app.import_models import ImportLimits
from app.postgres_vector_generations import VectorScope
from app.storage import SUPPORTED_DOCUMENT_SUFFIXES
from hello_agents.memory.rag.contracts import DocumentSegment
from hello_agents.memory.rag.embedding_client import SiliconFlowEmbedding
from hello_agents.memory.rag.embedding_runtime import (
    CHUNKING_BY_BACKEND, RAGEmbeddingRuntime,
)
from hello_agents.memory.rag.prepare import prepare_document_chunks
from hello_agents.memory.rag.qdrant_pipeline import split_qdrant_text
from hello_agents.memory.storage.vector_store import VectorPoint


class ImportDocumentPreparationError(ValueError):
    """A source cannot yield safe, nonempty points for this import."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Import document preparation failed: {code}")


def _canonical_uuid(value: object) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (TypeError, ValueError, AttributeError):
        return False


def _segments(source_bytes: bytes, document_id: str, file_name: str,
              suffix: str) -> list[DocumentSegment]:
    common = {
        "file_name": file_name,
        "file_suffix": suffix,
        "document_id": document_id,
    }
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(source_bytes))
            return [
                DocumentSegment(text, {
                    **common, "source_type": "pdf", "page_number": index + 1,
                })
                for index, page in enumerate(reader.pages)
                if (text := page.extract_text() or "").strip()
            ]
        if suffix == ".docx":
            from docx import Document

            document = Document(BytesIO(source_bytes))
            paragraphs = [text for paragraph in document.paragraphs
                          if (text := paragraph.text.strip())]
            return [DocumentSegment("\n\n".join(paragraphs),
                                    {**common, "source_type": "document"})]
        try:
            text = source_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = source_bytes.decode("gbk")
        return [DocumentSegment(text, {**common, "source_type": "document"})]
    except Exception:
        # Parser exceptions can include source contents or provider internals.
        raise ImportDocumentPreparationError("parse") from None


def prepare_import_document(
    *, source_bytes: bytes, task_id: str, user_id: str,
    document_id: str, original_name: str, suffix: str,
    scope: VectorScope, embedding_runtime: RAGEmbeddingRuntime,
) -> list[VectorPoint]:
    """Return all points for one new document or raise without returning any.

    The caller owns the fixed source reference, task lease, and publication.
    Logical point IDs are stable for the namespace, document, and chunk index.
    """
    if not all(_canonical_uuid(value) for value in (task_id, user_id, document_id)):
        raise ImportDocumentPreparationError("identity")
    if type(source_bytes) is not bytes or not source_bytes:
        raise ImportDocumentPreparationError("source")
    if len(source_bytes) > ImportLimits().max_file_bytes:
        raise ImportDocumentPreparationError("source_limit")
    if (not isinstance(original_name, str) or not original_name
            or "\x00" in original_name or "/" in original_name
            or "\\" in original_name):
        raise ImportDocumentPreparationError("file_name")
    file_name = PurePosixPath(original_name).name
    if file_name in ("", ".", ".."):
        raise ImportDocumentPreparationError("file_name")
    if (not isinstance(suffix, str) or suffix.lower() not in SUPPORTED_DOCUMENT_SUFFIXES
            or PurePosixPath(file_name).suffix.lower() != suffix.lower()):
        raise ImportDocumentPreparationError("suffix")
    if (not isinstance(scope, VectorScope) or scope.vector_kind != "rag"
            or scope.tenant_id != user_id or scope.namespace != f"pdf_{user_id}"):
        raise ImportDocumentPreparationError("scope")
    if (not isinstance(embedding_runtime, RAGEmbeddingRuntime)
            or embedding_runtime.profile != scope.identity.profile
            or embedding_runtime.profile.chunking != CHUNKING_BY_BACKEND["qdrant"]):
        raise ImportDocumentPreparationError("embedding_identity")

    # Own caller data before the first potentially blocking provider call.
    source = memoryview(source_bytes).tobytes()
    scope = deepcopy(scope)
    file_name = str(file_name)
    suffix = str(suffix.lower())
    source_engine = embedding_runtime._engine
    profile = deepcopy(embedding_runtime.profile)
    if isinstance(source_engine, SiliconFlowEmbedding):
        settings = deepcopy(source_engine.settings)
        if source_engine.profile != profile or settings.profile != profile:
            raise ImportDocumentPreparationError("embedding_identity")
        # Keep the transport handle, but own all request/decode configuration.
        engine = SiliconFlowEmbedding(settings, transport=source_engine._transport)
    else:
        # SimpleEmbedding owns only its dimension; preserve injected test engines.
        engine = copy(source_engine)
    runtime = RAGEmbeddingRuntime(profile, engine, embedding_runtime.batch_size)

    def require_unchanged_embedding():
        changed = (embedding_runtime.profile != runtime.profile
                   or embedding_runtime.batch_size != runtime.batch_size
                   or embedding_runtime._engine is not source_engine)
        if isinstance(source_engine, SiliconFlowEmbedding):
            changed = (changed or source_engine.profile != profile
                       or source_engine.settings != settings
                       or source_engine.settings.api_key != settings.api_key
                       or source_engine._transport is not engine._transport)
        else:
            changed = changed or source_engine.dimension != profile.dimension
        if changed:
            raise ImportDocumentPreparationError("embedding_identity")

    segments = _segments(source, document_id, file_name, suffix)
    if not segments:
        raise ImportDocumentPreparationError("empty_document")
    require_unchanged_embedding()
    try:
        chunks = prepare_document_chunks(
            document_id=document_id, segments=segments,
            rag_namespace=scope.namespace, split_text=split_qdrant_text,
            embed_text=None, embedding_runtime=runtime,
        )
    finally:
        require_unchanged_embedding()
    if not chunks:
        raise ImportDocumentPreparationError("empty_document")

    points: list[VectorPoint] = []
    seen: set[str] = set()
    for chunk in chunks:
        metadata = dict(chunk.metadata)
        if (chunk.id in seen or chunk.document_id != document_id
                or metadata.get("rag_namespace") != scope.namespace
                or metadata.get("embedding_fingerprint") != scope.identity.profile.fingerprint
                or len(chunk.vector) != scope.identity.profile.dimension):
            raise ImportDocumentPreparationError("point_identity")
        seen.add(chunk.id)
        payload = {
            "content": chunk.content,
            "document_id": document_id,
            "rag_namespace": scope.namespace,
            "chunk_index": int(metadata.pop("chunk_index")),
            "created_at": metadata.pop("created_at"),
            "updated_at": metadata.pop("updated_at"),
            "document_version": metadata.pop("document_version"),
            "metadata": metadata,
        }
        for duplicate in ("content", "document_id", "rag_namespace"):
            metadata.pop(duplicate, None)
        points.append(VectorPoint(chunk.id, list(chunk.vector), payload))
    return points
