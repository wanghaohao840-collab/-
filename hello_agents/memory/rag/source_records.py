"""Read-only migration records; callers supply authoritative scope separately."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from hello_agents.memory.rag.errors import RAGConfigError
from hello_agents.memory.rag.index_identity import IndexIdentity, require_point_identity
from hello_agents.memory.rag.prepare import contains_secret_metadata
from hello_agents.memory.storage.vector_scan import VectorScanRecord


class SourceInventoryError(RAGConfigError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"RAG source inventory rejected: {code}")


def require_name(value: object) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > 512
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise SourceInventoryError("name")
    return value


def canonical(value: object) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        # Reject lone surrogates and excessively large records before persistence.
        if len(encoded.encode("utf-8")) > 2 * 1024 * 1024:
            raise SourceInventoryError("record_size")
        return encoded
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise SourceInventoryError("record") from None


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SourceChunk:
    storage_id: int | str
    chunk_id: str
    namespace: str
    document_id: str
    content: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "storage_id": self.storage_id, "chunk_id": self.chunk_id,
            "namespace": self.namespace, "document_id": self.document_id,
            "content": self.content, "metadata": self.metadata,
        }


def validate_chunk(chunk: SourceChunk, identity: IndexIdentity | None = None) -> SourceChunk:
    if not isinstance(chunk, SourceChunk):
        raise SourceInventoryError("record")
    require_name(chunk.chunk_id)
    require_name(chunk.namespace)
    require_name(chunk.document_id)
    if type(chunk.storage_id) is int:
        if not 0 <= chunk.storage_id < 2**64:
            raise SourceInventoryError("storage_id")
    else:
        require_name(chunk.storage_id)
    if not isinstance(chunk.content, str) or not chunk.content.strip():
        raise SourceInventoryError("content")
    metadata = chunk.metadata
    if not isinstance(metadata, dict):
        raise SourceInventoryError("metadata")
    try:
        if contains_secret_metadata(metadata):
            raise SourceInventoryError("secret_metadata")
    except RecursionError:
        raise SourceInventoryError("metadata") from None
    for key, expected in (
        ("memory_id", chunk.chunk_id), ("document_id", chunk.document_id),
        ("rag_namespace", chunk.namespace), ("content", chunk.content),
    ):
        if metadata.get(key) != expected:
            raise SourceInventoryError("metadata_conflict")
    if (type(metadata.get("chunk_index")) is not int or not 0 <= metadata["chunk_index"] < 2**63
            or type(metadata.get("document_version")) is not int
            or not 1 <= metadata["document_version"] < 2**63
            or "_vector_store_id" in metadata):
        raise SourceInventoryError("metadata")
    if identity is not None:
        try:
            require_point_identity(metadata, identity=identity,
                                   rag_namespace=chunk.namespace, document_id=chunk.document_id)
        except RAGConfigError:
            raise SourceInventoryError("identity") from None
    elif "embedding_fingerprint" in metadata:
        # An unidentified source cannot silently claim a managed model.
        raise SourceInventoryError("identity")
    canonical(chunk.to_dict())
    return chunk


def json_chunk(raw: object, namespace: str, identity: IndexIdentity | None) -> SourceChunk:
    if not isinstance(raw, dict) or set(raw) != {"id", "document_id", "content", "vector", "metadata"}:
        raise SourceInventoryError("chunk_schema")
    if not isinstance(raw["vector"], list):
        raise SourceInventoryError("vector")
    return validate_chunk(SourceChunk(raw["id"], raw["id"], namespace,
                                      raw["document_id"], raw["content"], raw["metadata"]), identity)


def qdrant_chunk(record: VectorScanRecord, identity: IndexIdentity | None) -> SourceChunk:
    payload = record.payload
    if not isinstance(payload.get("metadata"), dict):
        raise SourceInventoryError("metadata")
    metadata = dict(payload["metadata"])
    # Preserve every source field, but never let flattening hide a conflict.
    for key, value in payload.items():
        if key in {"metadata", "_vector_store_id"}:
            continue
        if key in metadata and metadata[key] != value:
            raise SourceInventoryError("metadata_conflict")
        metadata[key] = value
    return validate_chunk(SourceChunk(
        record.storage_id, record.logical_id, payload.get("rag_namespace"),
        payload.get("document_id"), payload.get("content"), metadata,
    ), identity)
