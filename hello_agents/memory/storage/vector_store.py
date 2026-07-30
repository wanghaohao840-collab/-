from __future__ import annotations

import math
import re
import time
import uuid
from dataclasses import dataclass
from threading import RLock
from typing import Any, Iterable, Mapping, Optional, Protocol, runtime_checkable

from hello_agents.memory.rag.errors import (
    RAGAuthenticationError,
    RAGBackendError,
    RAGCollectionError,
    RAGConnectionError,
    RAGOperationError,
    sanitize_error_message,
)


VectorFilter = Mapping[str, Any]
_SUPPORTED_PAYLOAD_INDEX_SCHEMAS = {"keyword", "integer"}


@dataclass(frozen=True)
class VectorPoint:
    id: str
    vector: list[float]
    payload: dict[str, Any]


@dataclass(frozen=True)
class VectorHit:
    id: str
    score: float
    payload: dict[str, Any]


@runtime_checkable
class VectorStore(Protocol):
    def ensure_collection(
        self,
        collection_name: str,
        dimension: int,
        distance: str = "Cosine",
    ) -> None: ...

    def ensure_payload_indexes(
        self,
        collection_name: str,
        indexes: Mapping[str, str],
    ) -> None: ...

    def upsert(self, collection_name: str, points: Iterable[VectorPoint]) -> None: ...

    def search(
        self,
        collection_name: str,
        query_vector: list[float],
        filters: Optional[VectorFilter] = None,
        limit: int = 5,
        score_threshold: Optional[float] = None,
    ) -> list[VectorHit]: ...

    def count(
        self,
        collection_name: str,
        filters: Optional[VectorFilter] = None,
    ) -> int: ...

    def delete_by_filter(
        self,
        collection_name: str,
        filters: Optional[VectorFilter] = None,
    ) -> int: ...

    def scroll(
        self,
        collection_name: str,
        filters: Optional[VectorFilter] = None,
        with_vectors: bool = False,
        payload_fields: Optional[list[str]] = None,
    ) -> list[VectorPoint]: ...


class InMemoryVectorStore:
    """Deterministic VectorStore for development and tests."""

    def __init__(
        self,
        collection_name: str = "default",
        dimension: Optional[int] = None,
        **kwargs,
    ) -> None:
        self.collection_name = collection_name
        self._collections: dict[str, dict[str, VectorPoint]] = {}
        self._dimensions: dict[str, int] = {}
        self._lock = RLock()
        if dimension is not None:
            self.ensure_collection(collection_name, dimension)

    def ensure_collection(
        self,
        collection_name: str,
        dimension: int,
        distance: str = "Cosine",
    ) -> None:
        with self._lock:
            existing = self._dimensions.get(collection_name)
            if existing is not None and existing != dimension:
                raise RAGCollectionError(
                    f"Collection {collection_name} has vector size {existing}, expected {dimension}"
                )
            self._dimensions[collection_name] = dimension
            self._collections.setdefault(collection_name, {})

    def ensure_payload_indexes(
        self,
        collection_name: str,
        indexes: Mapping[str, str],
    ) -> None:
        self._validate_payload_indexes(indexes)

    def upsert(self, collection_name: str, points: Iterable[VectorPoint]) -> None:
        with self._lock:
            collection = self._collections.setdefault(collection_name, {})
            dimension = self._dimensions.get(collection_name)
            for point in points:
                if dimension is not None and len(point.vector) != dimension:
                    raise RAGCollectionError(
                        f"Point {point.id} has vector size {len(point.vector)}, expected {dimension}"
                    )
                collection[str(point.id)] = point

    def search(
        self,
        collection_name: str,
        query_vector: list[float],
        filters: Optional[VectorFilter] = None,
        limit: int = 5,
        score_threshold: Optional[float] = None,
    ) -> list[VectorHit]:
        hits: list[VectorHit] = []
        for point in self._collections.get(collection_name, {}).values():
            if not self._matches(point, filters):
                continue
            score = self._cosine_similarity(query_vector, point.vector)
            if score_threshold is not None and score < score_threshold:
                continue
            hits.append(VectorHit(point.id, score, dict(point.payload)))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:limit]

    def count(
        self,
        collection_name: str,
        filters: Optional[VectorFilter] = None,
    ) -> int:
        return sum(
            1
            for point in self._collections.get(collection_name, {}).values()
            if self._matches(point, filters)
        )

    def delete_by_filter(
        self,
        collection_name: str,
        filters: Optional[VectorFilter] = None,
    ) -> int:
        with self._lock:
            collection = self._collections.get(collection_name, {})
            ids = [
                point_id
                for point_id, point in collection.items()
                if self._matches(point, filters)
            ]
            for point_id in ids:
                collection.pop(point_id, None)
            return len(ids)

    def scroll(
        self,
        collection_name: str,
        filters: Optional[VectorFilter] = None,
        with_vectors: bool = False,
        payload_fields: Optional[list[str]] = None,
    ) -> list[VectorPoint]:
        points = []
        for point in self._collections.get(collection_name, {}).values():
            if not self._matches(point, filters):
                continue
            payload = self._select_payload(point.payload, payload_fields)
            points.append(
                VectorPoint(
                    point.id,
                    list(point.vector) if with_vectors else [],
                    payload,
                )
            )
        return points

    @property
    def vectors(self) -> dict[str, dict[str, Any]]:
        return {
            point_id: {
                "id": point.id,
                "vector": point.vector,
                "metadata": point.payload,
            }
            for point_id, point in self._collections.get(self.collection_name, {}).items()
        }

    def add_vectors(
        self,
        vectors: list[list[float]],
        metadata: list[dict[str, Any]],
        ids: list[str],
    ) -> None:
        self.upsert(
            self.collection_name,
            [
                VectorPoint(str(point_id), list(vector), dict(payload))
                for vector, payload, point_id in zip(vectors, metadata, ids)
            ],
        )

    def search_similar(
        self,
        query_vector: list[float],
        limit: int = 5,
        score_threshold: Optional[float] = None,
        where: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": hit.id,
                "score": hit.score,
                "metadata": hit.payload,
            }
            for hit in self.search(
                self.collection_name,
                query_vector,
                filters=where,
                limit=limit,
                score_threshold=score_threshold,
            )
        ]

    def delete_vectors(self, ids: list[str]) -> None:
        self.delete_by_filter(self.collection_name, {"_id": ids})

    def clear(self) -> None:
        self.delete_by_filter(self.collection_name)

    def _matches(self, point: VectorPoint, filters: Optional[VectorFilter]) -> bool:
        if not filters:
            return True
        for key, expected in filters.items():
            actual = point.id if key == "_id" else point.payload.get(key)
            if isinstance(expected, (list, tuple, set, frozenset)):
                if actual not in expected:
                    return False
            elif actual != expected:
                return False
        return True

    @staticmethod
    def _select_payload(
        payload: dict[str, Any],
        payload_fields: Optional[list[str]],
    ) -> dict[str, Any]:
        if payload_fields is None:
            return dict(payload)
        return {key: payload[key] for key in payload_fields if key in payload}

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        dot = sum(x * y for x, y in zip(left, right))
        norm_left = math.sqrt(sum(x * x for x in left))
        norm_right = math.sqrt(sum(y * y for y in right))
        if norm_left == 0 or norm_right == 0:
            return 0.0
        return dot / (norm_left * norm_right)

    @staticmethod
    def _validate_payload_indexes(
        indexes: Mapping[str, str],
    ) -> dict[str, str]:
        normalized = {}
        for field_name, schema_name in indexes.items():
            schema = str(schema_name).strip().lower()
            if schema not in _SUPPORTED_PAYLOAD_INDEX_SCHEMAS:
                raise ValueError(
                    f"Unsupported payload index schema for {field_name}: {schema_name}"
                )
            normalized[str(field_name)] = schema
        return normalized


class QdrantVectorStore:
    """Remote Qdrant implementation. Qdrant-specific models stay behind this boundary."""

    DEFAULT_RETRY_DELAYS = (0.5, 1.0, 2.0)
    UPSERT_BATCH_SIZE = 100
    LOGICAL_ID_PAYLOAD_KEY = "_vector_store_id"
    ID_NAMESPACE = uuid.UUID("70dc1fe0-daf6-45f2-8868-f78cce4f32d6")

    def __init__(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        client: Any = None,
        retry_delays: Optional[tuple[float, ...]] = None,
    ) -> None:
        self.url = url
        self.api_key = api_key
        self.retry_delays = (
            self.DEFAULT_RETRY_DELAYS if retry_delays is None else tuple(retry_delays)
        )
        self.models = self._load_models()
        self.client = client or self._create_client()

    def ensure_collection(
        self,
        collection_name: str,
        dimension: int,
        distance: str = "Cosine",
    ) -> None:
        exists = self._call("collection_exists", self.client.collection_exists, collection_name)
        if exists:
            info = self._call("get_collection", self.client.get_collection, collection_name)
            self._validate_collection(collection_name, info, dimension, distance)
            return

        try:
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=self._vector_params(dimension, distance),
            )
        except RAGBackendError:
            raise
        except Exception as error:
            status = self._status_code(error)
            mapped_error = self._map_error("create_collection", error, status)
            if not self._should_retry(error, status):
                raise mapped_error from None
            try:
                info = self._call(
                    "get_collection",
                    self.client.get_collection,
                    collection_name,
                )
            except RAGBackendError:
                raise mapped_error from None
            self._validate_collection(collection_name, info, dimension, distance)

    def ensure_payload_indexes(
        self,
        collection_name: str,
        indexes: Mapping[str, str],
    ) -> None:
        normalized = InMemoryVectorStore._validate_payload_indexes(indexes)
        for field_name, schema_name in normalized.items():
            field_schema = (
                getattr(self.models.PayloadSchemaType, schema_name.upper())
                if self.models
                else schema_name
            )
            self._call(
                "create_payload_index",
                self.client.create_payload_index,
                collection_name=collection_name,
                field_name=field_name,
                field_schema=field_schema,
                wait=True,
            )

    def _validate_collection(
        self,
        collection_name: str,
        info: Any,
        dimension: int,
        distance: str,
    ) -> None:
        config = self._extract_vector_config(info)
        size = getattr(config, "size", None)
        actual_distance = str(getattr(getattr(config, "distance", None), "value", getattr(config, "distance", "")))
        if int(size or 0) != int(dimension) or actual_distance.lower() != distance.lower():
            raise RAGCollectionError(
                f"Qdrant collection {collection_name} is incompatible: "
                f"vector size {size}, distance {actual_distance}; "
                f"expected {dimension}/{distance}"
            )

    def upsert(self, collection_name: str, points: Iterable[VectorPoint]) -> None:
        qdrant_points = [
            self._point_struct(point.id, point.vector, point.payload)
            for point in points
        ]
        for start in range(0, len(qdrant_points), self.UPSERT_BATCH_SIZE):
            self._call(
                "upsert",
                self.client.upsert,
                collection_name=collection_name,
                points=qdrant_points[start:start + self.UPSERT_BATCH_SIZE],
                wait=True,
            )

    def search(
        self,
        collection_name: str,
        query_vector: list[float],
        filters: Optional[VectorFilter] = None,
        limit: int = 5,
        score_threshold: Optional[float] = None,
    ) -> list[VectorHit]:
        response = self._call(
            "search",
            self.client.query_points,
            collection_name=collection_name,
            query=query_vector,
            query_filter=self._filter(filters),
            limit=limit,
            with_payload=True,
        )
        points = getattr(response, "points", response)
        return [
            VectorHit(
                str(
                    (getattr(point, "payload", {}) or {}).get(
                        self.LOGICAL_ID_PAYLOAD_KEY,
                        getattr(point, "id", ""),
                    )
                ),
                float(getattr(point, "score", 0.0)),
                dict(getattr(point, "payload", {}) or {}),
            )
            for point in points
            if score_threshold is None
            or float(getattr(point, "score", 0.0)) >= score_threshold
        ]

    def count(
        self,
        collection_name: str,
        filters: Optional[VectorFilter] = None,
    ) -> int:
        result = self._call(
            "count",
            self.client.count,
            collection_name=collection_name,
            count_filter=self._filter(filters),
            exact=True,
        )
        return int(getattr(result, "count", result))

    def delete_by_filter(
        self,
        collection_name: str,
        filters: Optional[VectorFilter] = None,
    ) -> int:
        removed = self.count(collection_name, filters)
        if filters and "_id" in filters:
            point_ids = list(filters["_id"])
            removed = len(point_ids)
            selector = self._point_ids_selector(point_ids)
        else:
            selector = self._filter_selector(self._filter(filters))
        self._call(
            "delete",
            self.client.delete,
            collection_name=collection_name,
            points_selector=selector,
            wait=True,
        )
        return removed

    def scroll(
        self,
        collection_name: str,
        filters: Optional[VectorFilter] = None,
        with_vectors: bool = False,
        payload_fields: Optional[list[str]] = None,
    ) -> list[VectorPoint]:
        points: list[VectorPoint] = []
        offset = None
        while True:
            batch, offset = self._call(
                "scroll",
                self.client.scroll,
                collection_name=collection_name,
                scroll_filter=self._filter(filters),
                limit=256,
                offset=offset,
                with_payload=payload_fields if payload_fields is not None else True,
                with_vectors=with_vectors,
            )
            for point in batch:
                vector = getattr(point, "vector", []) if with_vectors else []
                points.append(
                    VectorPoint(
                        str(
                            (getattr(point, "payload", {}) or {}).get(
                                self.LOGICAL_ID_PAYLOAD_KEY,
                                getattr(point, "id", ""),
                            )
                        ),
                        list(vector or []),
                        dict(getattr(point, "payload", {}) or {}),
                    )
                )
            if offset is None:
                return points

    def add_vectors(
        self,
        vectors: list[list[float]],
        metadata: list[dict[str, Any]],
        ids: list[str],
    ) -> None:
        self.upsert(
            self.collection_name,
            [
                VectorPoint(str(point_id), list(vector), dict(payload))
                for vector, payload, point_id in zip(vectors, metadata, ids)
            ],
        )

    def search_similar(
        self,
        query_vector: list[float],
        limit: int = 5,
        score_threshold: Optional[float] = None,
        where: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        return [
            {"id": hit.id, "score": hit.score, "metadata": hit.payload}
            for hit in self.search(
                self.collection_name,
                query_vector,
                filters=where,
                limit=limit,
                score_threshold=score_threshold,
            )
        ]

    def delete_vectors(self, ids: list[str]) -> None:
        self.delete_by_filter(self.collection_name, {"_id": ids})

    def clear(self) -> None:
        self.delete_by_filter(self.collection_name)

    def _filter(self, filters: Optional[VectorFilter]):
        if not filters:
            return None
        conditions = []
        for key, value in filters.items():
            if key == "_id":
                continue
            if isinstance(value, (list, tuple, set, frozenset)):
                match = self.models.MatchAny(any=list(value)) if self.models else _Object(any=list(value))
            else:
                match = self.models.MatchValue(value=value) if self.models else _Object(value=value)
            condition = (
                self.models.FieldCondition(key=key, match=match)
                if self.models
                else _Object(key=key, match=match)
            )
            conditions.append(condition)
        if self.models:
            return self.models.Filter(must=conditions)
        return _Object(must=conditions, should=[], must_not=[])

    def _call(self, operation: str, function, *args, **kwargs):
        for attempt in range(1 + len(self.retry_delays)):
            try:
                return function(*args, **kwargs)
            except RAGBackendError:
                raise
            except Exception as error:
                status = self._status_code(error)
                if self._should_retry(error, status) and attempt < len(self.retry_delays):
                    time.sleep(self.retry_delays[attempt])
                    continue
                raise self._map_error(operation, error, status) from None

    def _should_retry(self, error: Exception, status: Optional[int]) -> bool:
        if status is not None:
            return status >= 500
        current: Optional[BaseException] = error
        while current is not None:
            if isinstance(current, (TimeoutError, ConnectionError)):
                return True
            if type(current).__name__ in {
                "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout"
            }:
                return True
            current = current.__cause__ or current.__context__
        return False

    @staticmethod
    def _status_code(error: Exception) -> Optional[int]:
        for attr in ("status_code", "status", "code"):
            value = getattr(error, attr, None)
            if isinstance(value, int):
                return value
        response = getattr(error, "response", None)
        value = getattr(response, "status_code", None)
        if isinstance(value, int):
            return value
        match = re.search(r"\b([45]\d{2})\b", str(error))
        return int(match.group(1)) if match else None

    def _map_error(
        self,
        operation: str,
        error: Exception,
        status: Optional[int],
    ) -> RAGBackendError:
        message = sanitize_error_message(error, (self.api_key or "",))
        if status in {401, 403}:
            return RAGAuthenticationError(
                f"Qdrant authentication failed during {operation}: {message}"
            )
        if status == 404 and operation in {"get_collection", "collection_exists"}:
            return RAGCollectionError(f"Qdrant collection was not found: {message}")
        if status is None and self._should_retry(error, status):
            return RAGConnectionError(
                f"Qdrant connection failed during {operation}: {message}"
            )
        return RAGOperationError(
            f"Qdrant operation {operation} failed: {message}",
            operation=operation,
        )

    def _create_client(self):
        try:
            from qdrant_client import QdrantClient

            return QdrantClient(url=self.url, api_key=self.api_key)
        except Exception as error:
            message = sanitize_error_message(error, (self.api_key or "",))
            raise RAGConnectionError(f"Failed to create Qdrant client: {message}") from None

    @staticmethod
    def _load_models():
        try:
            from qdrant_client import models

            return models
        except Exception:
            return None

    def _vector_params(self, dimension: int, distance: str):
        if self.models:
            return self.models.VectorParams(
                size=dimension,
                distance=getattr(self.models.Distance, distance.upper()),
            )
        return _Object(size=dimension, distance=distance)

    def _point_struct(self, point_id: str, vector: list[float], payload: dict[str, Any]):
        qdrant_id = self._qdrant_id(point_id)
        stored_payload = dict(payload)
        stored_payload[self.LOGICAL_ID_PAYLOAD_KEY] = str(point_id)
        if self.models:
            return self.models.PointStruct(
                id=qdrant_id,
                vector=vector,
                payload=stored_payload,
            )
        return _Object(id=qdrant_id, vector=vector, payload=stored_payload)

    def _filter_selector(self, filter_value):
        if self.models:
            return self.models.FilterSelector(filter=filter_value)
        return _Object(filter=filter_value)

    def _point_ids_selector(self, point_ids: list[str]):
        qdrant_ids = [self._qdrant_id(point_id) for point_id in point_ids]
        if self.models:
            return self.models.PointIdsList(points=qdrant_ids)
        return _Object(points=qdrant_ids)

    @classmethod
    def _qdrant_id(cls, point_id: str) -> Any:
        text = str(point_id)
        if text.isdigit():
            value = int(text)
            if value >= 0:
                return value
        try:
            return str(uuid.UUID(text))
        except ValueError:
            return str(uuid.uuid5(cls.ID_NAMESPACE, text))

    @staticmethod
    def _extract_vector_config(collection_info: Any) -> Any:
        params = getattr(getattr(collection_info, "config", None), "params", None)
        vectors = getattr(params, "vectors", None)
        if isinstance(vectors, dict):
            if "" in vectors:
                return vectors[""]
            if len(vectors) == 1:
                return next(iter(vectors.values()))
        return vectors


class _Object:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
