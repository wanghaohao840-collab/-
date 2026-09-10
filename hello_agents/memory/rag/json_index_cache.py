from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from threading import RLock
import uuid

from hello_agents.memory.rag.embedding_profile import normalize_vector
from hello_agents.memory.rag.embedding_runtime import validate_texts
from hello_agents.memory.rag.errors import RAGConfigError, RAGEmbeddingError
from hello_agents.memory.rag.index_identity import (
    IndexIdentity, IndexIdentityError, require_point_identity,
)
from hello_agents.memory.rag.prepare import contains_secret_metadata
from hello_agents.memory.storage.vector_store import VectorPoint

_DOCUMENT_FIELDS = {
    "schema_version", "identity", "rag_namespace",
    "updated_at", "chunk_count", "chunks",
}
_CHUNK_FIELDS = {"id", "document_id", "content", "vector", "metadata"}
_LOCK = RLock()


class JsonIndexCacheError(RAGConfigError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"RAG JSON index cache rejected: {code}")


def _safe_text(value: object, *, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= maximum
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _require_timestamp(value: object) -> str:
    try:
        result = str(value)
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
        if (
            not isinstance(value, str)
            or not result.endswith("Z")
            or parsed.tzinfo is None
            or parsed.utcoffset() != timezone.utc.utcoffset(parsed)
        ):
            raise ValueError()
        return result
    except (TypeError, ValueError, OverflowError):
        raise JsonIndexCacheError("schema") from None


def versioned_cache_path(
    legacy_path: Path | str,
    identity: IndexIdentity,
    rag_namespace: str,
) -> Path:
    path = Path(legacy_path)
    if (
        not path.is_absolute()
        or not isinstance(identity, IndexIdentity)
        or identity.backend != "json"
    ):
        raise JsonIndexCacheError("path")
    if not _safe_text(rag_namespace, maximum=512):
        raise JsonIndexCacheError("namespace")
    path = path.resolve()
    namespace_hash = hashlib.sha256(rag_namespace.encode("utf-8")).hexdigest()[:12]
    suffix = path.suffix or ".json"
    stem = path.stem if path.suffix else path.name
    name = (
        f"{stem}__{namespace_hash}__"
        f"{identity.profile.fingerprint[:16]}{suffix}"
    )
    return path.with_name(name)


class JsonIndexCache:
    def __init__(
        self,
        legacy_path: Path | str,
        identity: IndexIdentity,
        rag_namespace: str,
    ):
        if identity.backend != "json":
            raise JsonIndexCacheError("backend")
        self.identity = identity
        self.rag_namespace = rag_namespace
        self.legacy_path = Path(legacy_path).resolve()
        self.path = versioned_cache_path(
            legacy_path, identity, rag_namespace
        )

    def load(self) -> list[VectorPoint]:
        with _LOCK:
            try:
                document = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                raise JsonIndexCacheError("missing") from None
            except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
                raise JsonIndexCacheError("unreadable") from None
            return self._decode(document)

    def write(self, points: list[VectorPoint]) -> None:
        with _LOCK:
            if self.path.exists():
                # Refuse to overwrite corruption or a full-fingerprint collision.
                self.load()
            chunks = self._encode_points(points)
            document = {
                "schema_version": 2,
                "identity": self.identity.to_dict(),
                "rag_namespace": self.rag_namespace,
                "updated_at": (
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                ),
                "chunk_count": len(chunks),
                "chunks": chunks,
            }
            try:
                encoded = json.dumps(
                    document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            except (TypeError, ValueError, OverflowError, RecursionError):
                raise JsonIndexCacheError("point") from None
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.parent / (
                f".{self.path.name}.{uuid.uuid4().hex}.tmp"
            )
            try:
                with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            except (OSError, UnicodeError):
                raise JsonIndexCacheError("write") from None
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _decode(self, document: object) -> list[VectorPoint]:
        if (
            not isinstance(document, dict)
            or set(document) != _DOCUMENT_FIELDS
            or type(document.get("schema_version")) is not int
            or document["schema_version"] != 2
            or not isinstance(document.get("chunks"), list)
            or type(document.get("chunk_count")) is not int
            or document["chunk_count"] != len(document["chunks"])
            or document.get("rag_namespace") != self.rag_namespace
        ):
            raise JsonIndexCacheError("schema")
        _require_timestamp(document.get("updated_at"))
        try:
            stored_identity = IndexIdentity.from_dict(document["identity"])
            stored_identity.require_match(self.identity)
        except (IndexIdentityError, KeyError):
            raise JsonIndexCacheError("identity") from None
        points = []
        seen_ids: set[str] = set()
        seen_indexes: set[tuple[str, int]] = set()
        for chunk in document["chunks"]:
            point = self._decode_chunk(chunk)
            chunk_index = point.payload["chunk_index"]
            key = (point.payload["document_id"], chunk_index)
            if point.id in seen_ids or key in seen_indexes:
                raise JsonIndexCacheError("duplicate")
            seen_ids.add(point.id)
            seen_indexes.add(key)
            points.append(point)
        return points

    def _decode_chunk(self, chunk: object) -> VectorPoint:
        if not isinstance(chunk, dict) or set(chunk) != _CHUNK_FIELDS:
            raise JsonIndexCacheError("point")
        point_id = chunk.get("id")
        document_id = chunk.get("document_id")
        content = chunk.get("content")
        metadata = chunk.get("metadata")
        if (
            not _safe_text(point_id, maximum=512)
            or not _safe_text(document_id, maximum=512)
            or not isinstance(content, str)
            or not content.strip()
            or not isinstance(metadata, dict)
            or metadata.get("memory_id") != point_id
            or metadata.get("content") != content
            or "_vector_store_id" in metadata
            or contains_secret_metadata(metadata)
            or type(metadata.get("chunk_index")) is not int
            or metadata["chunk_index"] < 0
            or type(metadata.get("document_version")) is not int
            or metadata["document_version"] < 1
        ):
            raise JsonIndexCacheError("point")
        try:
            validate_texts([content])
            require_point_identity(
                metadata,
                identity=self.identity,
                rag_namespace=self.rag_namespace,
                document_id=document_id,
            )
            vector = normalize_vector(
                chunk.get("vector"), self.identity.profile.dimension
            )
        except (IndexIdentityError, RAGConfigError, RAGEmbeddingError):
            raise JsonIndexCacheError("point") from None
        return VectorPoint(point_id, vector, dict(metadata))

    def _encode_points(self, points: object) -> list[dict[str, object]]:
        if not isinstance(points, list):
            raise JsonIndexCacheError("point")
        result = []
        seen_ids: set[str] = set()
        seen_indexes: set[tuple[str, int]] = set()
        for point in points:
            if not isinstance(point, VectorPoint):
                raise JsonIndexCacheError("point")
            decoded = self._decode_chunk({
                "id": point.id,
                "document_id": point.payload.get("document_id"),
                "content": point.payload.get("content"),
                "vector": point.vector,
                "metadata": point.payload,
            })
            key = (
                decoded.payload["document_id"],
                decoded.payload["chunk_index"],
            )
            if decoded.id in seen_ids or key in seen_indexes:
                raise JsonIndexCacheError("duplicate")
            seen_ids.add(decoded.id)
            seen_indexes.add(key)
            result.append({
                "id": decoded.id,
                "document_id": decoded.payload["document_id"],
                "content": decoded.payload["content"],
                "vector": decoded.vector,
                "metadata": decoded.payload,
            })
        return result
