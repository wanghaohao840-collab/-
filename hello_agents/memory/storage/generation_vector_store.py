"""Pinned Qdrant reads and isolated staging for immutable vector generations.

The caller obtains heads and candidate state from PostgreSQL. This module never
publishes a head or treats an absent head as an empty corpus.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import hashlib
import json
import math
import uuid
from typing import Any, Callable, Iterable, Iterator, Mapping

from app.postgres_vector_generations import VectorHead, VectorScope
from hello_agents.memory.storage.vector_scan import (
    NativePointId, VectorScanPage, VectorScanRecord, cursor_key,
    iter_qdrant_pages, native_point_id,
)
from hello_agents.memory.storage.vector_store import (
    QdrantVectorStore, VectorFilter, VectorHit, VectorPoint,
)


class GenerationVectorStoreError(RuntimeError):
    """The requested vector operation cannot be proven safe for this generation."""


class CandidateAbortRequired(GenerationVectorStoreError):
    """Unknown write outcome: permanently abandon this PG candidate UUID."""


_POINT_NAMESPACE = uuid.UUID("314ebff1-4f8b-42c6-a03a-04f94935d181")
_FIELDS = {
    "_gv_tenant": "tenant_id", "_gv_kind": "vector_kind",
    "_gv_namespace": "namespace", "_gv_index": "index_key",
}
_GENERATION = "_gv_generation"
_LOGICAL_ID = "_gv_logical_id"
_VECTOR_DIGEST = "_gv_vector_digest"
_RESERVED = {*_FIELDS, _GENERATION, _LOGICAL_ID, _VECTOR_DIGEST,
             QdrantVectorStore.LOGICAL_ID_PAYLOAD_KEY}
_MAX_SCAN_PAGES = 10_000
_PAGE_SIZE = 128


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _scope_fields(scope: VectorScope, generation_id: uuid.UUID) -> dict[str, str]:
    return {key: str(getattr(scope, attribute)) for key, attribute in _FIELDS.items()} | {
        _GENERATION: str(generation_id),
    }


def _physical_id(scope: VectorScope, generation_id: uuid.UUID, logical_id: str) -> str:
    # JSON array encoding is unambiguous even when IDs contain separators.
    identity = [scope.tenant_id, scope.vector_kind, scope.namespace,
                scope.identity.to_dict(), str(generation_id), logical_id]
    return str(uuid.uuid5(_POINT_NAMESPACE, _canonical(identity)))


def _validate_logical_id(value: object) -> str:
    if (not isinstance(value, str) or not value or len(value) > 512
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise GenerationVectorStoreError("Invalid logical vector ID")
    return value


def _check_caller_fields(scope: VectorScope, values: Mapping[str, Any]) -> None:
    if any(key in _RESERVED or key.startswith("_gv_") for key in values):
        raise GenerationVectorStoreError("Reserved vector field in caller data")
    aliases = {
        "tenant_id": scope.tenant_id, "user_id": scope.tenant_id,
        "vector_kind": scope.vector_kind, "namespace": scope.namespace,
        "rag_namespace": scope.namespace,
        "embedding_fingerprint": scope.identity.profile.fingerprint,
    }
    if any(key in values and values[key] != expected for key, expected in aliases.items()):
        raise GenerationVectorStoreError("Caller scope conflicts with pinned vector scope")


@dataclass(frozen=True, slots=True)
class GenerationVectorStore:
    """Read-only view of one PostgreSQL head, pinned for a public operation."""

    raw: QdrantVectorStore
    scope: VectorScope
    head: VectorHead
    collection_name: str = field(init=False)

    def __post_init__(self):
        if not isinstance(self.raw, QdrantVectorStore) or not isinstance(self.scope, VectorScope):
            raise TypeError("Qdrant store and trusted vector scope are required")
        if not isinstance(self.head, VectorHead) or self.head.state not in {"empty", "published"}:
            raise GenerationVectorStoreError("Missing or invalid published vector head")
        if (type(self.head.revision) is not int or self.head.revision < 1
                or type(self.head.index_revision) is not int or self.head.index_revision < 1
                or (self.head.state == "published") != isinstance(self.head.generation_id, uuid.UUID)):
            raise GenerationVectorStoreError("Invalid published vector head")
        self.raw.require_collection(self.scope.identity.physical_collection,
                                    self.scope.identity.profile.dimension,
                                    self.scope.identity.profile.distance)
        object.__setattr__(self, "collection_name", self.scope.identity.physical_collection)

    def _collection(self, collection_name: str) -> None:
        if collection_name != self.collection_name:
            raise GenerationVectorStoreError("Collection differs from pinned index identity")

    def _filters(self, filters: VectorFilter | None) -> dict[str, Any]:
        requested = dict(filters or {})
        _check_caller_fields(self.scope, requested)
        if "_id" in requested:
            ids = requested.pop("_id")
            if isinstance(ids, (list, tuple, set, frozenset)):
                requested[_LOGICAL_ID] = [_validate_logical_id(item) for item in ids]
            else:
                requested[_LOGICAL_ID] = _validate_logical_id(ids)
        return requested | _scope_fields(self.scope, self.head.generation_id)

    def _record(self, physical_id: str, payload: Mapping[str, Any],
                storage_id: NativePointId | None = None) -> tuple[str, dict[str, Any]]:
        if not isinstance(payload, Mapping):
            raise GenerationVectorStoreError("Malformed vector payload")
        logical_id = _validate_logical_id(payload.get(_LOGICAL_ID))
        expected_id = _physical_id(self.scope, self.head.generation_id, logical_id)
        if (physical_id != expected_id
                or payload.get(QdrantVectorStore.LOGICAL_ID_PAYLOAD_KEY) != expected_id
                or any(payload.get(key) != value for key, value in
                       _scope_fields(self.scope, self.head.generation_id).items())
                or any(key.startswith("_gv_") and key not in _RESERVED
                       for key in payload)
                or (storage_id is not None and str(storage_id) != expected_id)):
            raise GenerationVectorStoreError("Vector point identity or scope is corrupt")
        public = {key: value for key, value in payload.items()
                  if key not in _RESERVED and not key.startswith("_gv_")}
        _check_caller_fields(self.scope, public)
        return logical_id, public

    def require_collection(self, collection_name: str, dimension: int,
                           distance: str = "Cosine") -> None:
        self._collection(collection_name)
        if (dimension != self.scope.identity.profile.dimension
                or distance.lower() != self.scope.identity.profile.distance.lower()):
            raise GenerationVectorStoreError("Index identity differs from pinned scope")
        self.raw.require_collection(collection_name, dimension, distance)

    def ensure_collection(self, collection_name: str, dimension: int,
                          distance: str = "Cosine") -> None:
        raise GenerationVectorStoreError("Published vector view is read only")

    def ensure_payload_indexes(self, collection_name: str, indexes: Mapping[str, str]) -> None:
        raise GenerationVectorStoreError("Published vector view is read only")

    def upsert(self, collection_name: str, points: Iterable[VectorPoint]) -> None:
        raise GenerationVectorStoreError("Published vector view is read only")

    def delete_by_filter(self, collection_name: str, filters: VectorFilter | None = None) -> int:
        raise GenerationVectorStoreError("Published vector view is read only")

    def clear(self) -> None:
        raise GenerationVectorStoreError("Published vector view is read only")

    def search(self, collection_name: str, query_vector: list[float],
               filters: VectorFilter | None = None, limit: int = 5,
               score_threshold: float | None = None) -> list[VectorHit]:
        self._collection(collection_name)
        if self.head.state == "empty":
            self._filters(filters)  # reject conflicting caller scope even for emptiness
            return []
        response = self.raw._call(
            "search", self.raw.client.query_points,
            collection_name=collection_name, query=query_vector,
            query_filter=self.raw._filter(self._filters(filters)),
            limit=limit, with_payload=True,
        )
        result = []
        for point in getattr(response, "points", response):
            physical = str(native_point_id(getattr(point, "id", None)))
            logical_id, payload = self._record(physical, getattr(point, "payload", None),
                                               physical)
            score = float(getattr(point, "score", 0.0))
            if score_threshold is None or score >= score_threshold:
                result.append(VectorHit(logical_id, score, payload))
        return result

    def count(self, collection_name: str, filters: VectorFilter | None = None) -> int:
        self._collection(collection_name)
        effective = self._filters(filters)
        return 0 if self.head.state == "empty" else self.raw.count(collection_name, effective)

    def scroll_page(self, collection_name: str, filters: VectorFilter | None = None,
                    *, offset: NativePointId | None = None,
                    page_size: int = 128) -> VectorScanPage:
        self._collection(collection_name)
        effective = self._filters(filters)
        if self.head.state == "empty":
            return VectorScanPage((), None)
        page = self.raw.scroll_page(collection_name, effective,
                                    offset=offset, page_size=page_size)
        records = []
        for record in page.records:
            logical_id, payload = self._record(record.logical_id, record.payload,
                                               record.storage_id)
            records.append(VectorScanRecord(record.storage_id, logical_id, payload))
        return VectorScanPage(tuple(records), page.next_offset)

    def iter_scroll_pages(self, collection_name: str,
                          filters: VectorFilter | None = None, *,
                          offset: NativePointId | None = None,
                          page_size: int = 128,
                          max_pages: int = _MAX_SCAN_PAGES) -> Iterator[VectorScanPage]:
        self._collection(collection_name)
        effective = copy.deepcopy(filters) if filters is not None else None
        yield from iter_qdrant_pages(
            lambda current: self.scroll_page(collection_name, effective,
                                             offset=current, page_size=page_size),
            offset=offset, max_pages=max_pages,
        )

    def scroll(self, collection_name: str, filters: VectorFilter | None = None,
               with_vectors: bool = False,
               payload_fields: list[str] | None = None, *,
               expected_manifest: tuple[int, str] | None = None) -> list[VectorPoint]:
        """Optionally verify the full pinned manifest in this same bounded scan.

        The digest authenticates point membership, public payload and each
        declared vector digest. It cannot detect out-of-band vector writes that
        leave the stored digest untouched.
        """
        self._collection(collection_name)
        if expected_manifest is not None and filters is not None:
            raise GenerationVectorStoreError("Manifest requires a full corpus scan")
        effective = self._filters(filters)
        if self.head.state == "empty":
            if expected_manifest is not None and expected_manifest != (
                    0, hashlib.sha256(_canonical([]).encode()).hexdigest()):
                raise GenerationVectorStoreError("Published vector manifest differs")
            return []
        fields = True if expected_manifest is not None or payload_fields is None else list(dict.fromkeys(
            [*payload_fields, *_RESERVED]))
        result = []
        manifest_rows = []
        offset = None
        seen_offsets = set()
        seen_points = set()
        for _ in range(_MAX_SCAN_PAGES):
            batch, next_offset = self.raw._call(
                "scroll", self.raw.client.scroll,
                collection_name=collection_name,
                scroll_filter=self.raw._filter(effective), limit=_PAGE_SIZE,
                offset=offset, with_payload=fields, with_vectors=with_vectors,
            )
            if not isinstance(batch, (list, tuple)) or len(batch) > _PAGE_SIZE:
                raise GenerationVectorStoreError("Invalid vector scan page")
            for point in batch:
                physical = str(native_point_id(getattr(point, "id", None)))
                if physical in seen_points:
                    raise GenerationVectorStoreError("Duplicate vector point in scan")
                seen_points.add(physical)
                logical_id, payload = self._record(
                    physical, getattr(point, "payload", None), physical)
                if expected_manifest is not None:
                    declared = point.payload.get(_VECTOR_DIGEST)
                    if (not isinstance(declared, str) or len(declared) != 64
                            or any(char not in '0123456789abcdef' for char in declared)):
                        raise GenerationVectorStoreError("Published vector digest is invalid")
                    manifest_rows.append([logical_id, payload.copy(), declared])
                if payload_fields is not None:
                    payload = {field: payload[field] for field in payload_fields if field in payload}
                vector = getattr(point, "vector", None) if with_vectors else []
                result.append(VectorPoint(logical_id, list(vector or []), payload))
            if next_offset is None:
                if expected_manifest is not None:
                    expected_count, expected_digest = expected_manifest
                    digest = hashlib.sha256(_canonical(sorted(manifest_rows,
                        key=lambda item: item[0])).encode()).hexdigest()
                    if len(result) != expected_count or digest != expected_digest:
                        raise GenerationVectorStoreError("Published vector manifest differs")
                return result
            native = native_point_id(next_offset)
            key = cursor_key(native)
            if not batch or key in seen_offsets or (offset is not None and key == cursor_key(offset)):
                raise GenerationVectorStoreError("Invalid vector scan cursor")
            seen_offsets.add(key)
            offset = native
        raise GenerationVectorStoreError("Vector scan exceeds page bound")


class CandidateGenerationWriter:
    """One-shot upload into a fresh staging generation; no automatic write retry."""

    def __init__(self, raw: QdrantVectorStore, scope: VectorScope,
                 generation_id: uuid.UUID,
                 state_check: Callable[[VectorScope, uuid.UUID], str],
                 *, max_points: int = 100_000):
        if not isinstance(generation_id, uuid.UUID) or generation_id.int == 0:
            raise ValueError("Fresh generation UUID is required")
        if type(max_points) is not int or not 1 <= max_points <= 1_000_000:
            raise ValueError("Invalid verification point bound")
        self.raw = raw
        self.scope = scope
        self.generation_id = generation_id
        self.state_check = state_check
        self.max_points = max_points
        self.collection_name = scope.identity.physical_collection
        self._uploaded = False
        self._poisoned = False
        self._expected: dict[str, VectorPoint] = {}
        raw.require_collection(self.collection_name, scope.identity.profile.dimension,
                               scope.identity.profile.distance)

    def _staging(self) -> None:
        if self._poisoned or self.state_check(self.scope, self.generation_id) != "staging":
            raise GenerationVectorStoreError("Candidate is not safe to write or verify")

    def upload(self, points: Iterable[VectorPoint]) -> None:
        self._staging()
        if self._uploaded:
            raise GenerationVectorStoreError("Candidate upload is one shot")
        if self.raw.count(self.collection_name,
                          _scope_fields(self.scope, self.generation_id)) != 0:
            raise GenerationVectorStoreError("Staging generation is not physically fresh")
        prepared = []
        expected = {}
        for point in points:
            logical_id = _validate_logical_id(point.id)
            if logical_id in expected:
                raise GenerationVectorStoreError("Duplicate logical vector ID")
            if not isinstance(point.payload, dict):
                raise GenerationVectorStoreError("Invalid vector payload")
            _check_caller_fields(self.scope, point.payload)
            vector = point.vector
            if (not isinstance(vector, list)
                    or len(vector) != self.scope.identity.profile.dimension
                    or any(type(item) not in (float, int) or not math.isfinite(item)
                           for item in vector)):
                raise GenerationVectorStoreError("Invalid vector dimension or component")
            if not math.isfinite(math.hypot(*vector)):
                raise GenerationVectorStoreError("Invalid vector magnitude")
            if len(expected) >= self.max_points:
                raise GenerationVectorStoreError("Candidate exceeds verification bound")
            public = VectorPoint(logical_id, list(vector), dict(point.payload))
            try:
                _canonical(public.payload)  # preflight before any write
            except (TypeError, ValueError, OverflowError) as error:
                raise GenerationVectorStoreError("Invalid vector payload") from error
            expected[logical_id] = public
            physical = _physical_id(self.scope, self.generation_id, logical_id)
            vector_digest = hashlib.sha256(_canonical(vector).encode()).hexdigest()
            payload = dict(point.payload) | _scope_fields(self.scope, self.generation_id) | {
                _LOGICAL_ID: logical_id, _VECTOR_DIGEST: vector_digest,
            }
            prepared.append(self.raw._point_struct(physical, list(vector), payload))
        # Preflight the complete input before submitting any batch.
        self._uploaded = True
        self._expected = expected
        for start in range(0, len(prepared), self.raw.UPSERT_BATCH_SIZE):
            self._staging()
            try:
                result = self.raw.client.upsert(
                    collection_name=self.collection_name,
                    points=prepared[start:start + self.raw.UPSERT_BATCH_SIZE], wait=True,
                )
                status = getattr(result, "status", None)
                if str(getattr(status, "value", status)).lower() != "completed":
                    raise GenerationVectorStoreError("Qdrant write outcome is unknown")
            except Exception as error:
                self._poisoned = True
                raise CandidateAbortRequired(
                    "Qdrant write outcome is unknown; abandon PostgreSQL candidate and never reuse its UUID"
                ) from error

    def verify(self) -> tuple[int, str]:
        self._staging()
        if not self._uploaded:
            raise GenerationVectorStoreError("Candidate has not been uploaded")
        view = GenerationVectorStore(
            self.raw, self.scope,
            VectorHead("published", 1, self.generation_id, 1, None),
        )
        actual = {}
        offset = None
        seen_offsets = set()
        for _ in range(_MAX_SCAN_PAGES):
            batch, next_offset = self.raw._call(
                "scroll", self.raw.client.scroll,
                collection_name=self.collection_name,
                scroll_filter=self.raw._filter(view._filters(None)),
                offset=offset, limit=_PAGE_SIZE,
                with_payload=True, with_vectors=True,
            )
            if not isinstance(batch, (list, tuple)) or len(batch) > _PAGE_SIZE:
                raise GenerationVectorStoreError("Invalid verification scan page")
            for item in batch:
                payload = getattr(item, "payload", None)
                physical = str(native_point_id(getattr(item, "id", None)))
                logical_id, public_payload = view._record(physical, payload, physical)
                if logical_id in actual or logical_id not in self._expected:
                    raise GenerationVectorStoreError("Candidate contains unexpected or duplicate point")
                expected = self._expected[logical_id]
                vector = getattr(item, "vector", None)
                if public_payload != expected.payload or not _same_cosine_vector(vector, expected.vector):
                    raise GenerationVectorStoreError("Candidate payload or vector differs")
                if payload.get(_VECTOR_DIGEST) != hashlib.sha256(
                        _canonical(expected.vector).encode()).hexdigest():
                    raise GenerationVectorStoreError("Candidate vector digest differs")
                actual[logical_id] = [logical_id, public_payload, payload[_VECTOR_DIGEST]]
                if len(actual) > self.max_points:
                    raise GenerationVectorStoreError("Verification exceeds point bound")
            if next_offset is None:
                break
            native = native_point_id(next_offset)
            key = cursor_key(native)
            if not batch or key in seen_offsets or (offset is not None and key == cursor_key(offset)):
                raise GenerationVectorStoreError("Invalid verification scan cursor")
            seen_offsets.add(key)
            offset = native
        else:
            raise GenerationVectorStoreError("Verification exceeds page bound")
        if set(actual) != set(self._expected):
            raise GenerationVectorStoreError("Candidate scan is incomplete")
        self._staging()
        digest = hashlib.sha256(_canonical([actual[key] for key in sorted(actual)]).encode()).hexdigest()
        return len(actual), digest


def _same_cosine_vector(actual: object, expected: list[float]) -> bool:
    if (not isinstance(actual, list) or len(actual) != len(expected)
            or any(type(value) not in (int, float) or not math.isfinite(value)
                   for value in actual)):
        return False
    expected_norm = math.sqrt(sum(value * value for value in expected))
    if expected_norm == 0:
        return all(abs(value) < 1e-6 for value in actual)
    return all(abs(value - original / expected_norm) < 2e-5
               for value, original in zip(actual, expected))


def cleanup_abandoned_generation(
    raw: QdrantVectorStore, scope: VectorScope, generation_id: uuid.UUID,
    state_check: Callable[[VectorScope, uuid.UUID], str],
) -> int:
    """Delete only a PostgreSQL-confirmed abandoned generation's exact scope."""
    if not isinstance(generation_id, uuid.UUID) or state_check(scope, generation_id) != "abandoned":
        raise GenerationVectorStoreError("Only an abandoned candidate can be cleaned")
    collection = scope.identity.physical_collection
    raw.require_collection(collection, scope.identity.profile.dimension,
                           scope.identity.profile.distance)
    filters = _scope_fields(scope, generation_id)
    # The state is irrevocable; an old HTTP request may still recreate invisible orphans.
    return raw.delete_by_filter(collection, filters)
