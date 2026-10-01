"""Opt-in, attested episode reads pinned to one PostgreSQL publication."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import re
from uuid import UUID

from app.postgres_vector_generations import VectorHead, VectorScope
from app.vector_generation_service import VectorGenerationService
from hello_agents.memory.rag.embedding_profile import EmbeddingProfile
from hello_agents.memory.rag.index_identity import (
    IndexIdentity, IndexIdentityError, validate_profile,
)
from hello_agents.memory.storage.generation_vector_store import GenerationVectorStore
from hello_agents.memory.storage.vector_store import VectorHit, VectorPoint, VectorRange


class EpisodeReadError(ValueError):
    """An episode read cannot prove its authority or returned material."""


_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_OFFSET = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-](?:0\d|1\d|2[0-3]):[0-5]\d)\Z")
_KINDS = {"episodic", "semantic", "working", "perceptual"}
_MAX_POINTS = 100_000
_MAX_LIMIT = 1_000


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as error:
        raise EpisodeReadError("Non-finite or non-JSON episode data") from error


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EpisodeReadError("Duplicate document metadata key")
        result[key] = value
    return result


def _metadata(raw):
    if not isinstance(raw, str):
        raise EpisodeReadError("Document metadata must be JSON text")
    try:
        value = json.loads(raw, object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(
                               EpisodeReadError("Non-finite document metadata")))
    except (ValueError, TypeError) as error:
        raise EpisodeReadError("Invalid document metadata") from error
    if not isinstance(value, dict):
        raise EpisodeReadError("Document metadata must be an object")
    _json(value)  # catches exponent overflow nested at any depth
    return value


def _time(value):
    if not isinstance(value, str) or not _OFFSET.fullmatch(value):
        raise EpisodeReadError("Timezone-aware episode timestamp required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EpisodeReadError("Invalid episode timestamp") from error
    if parsed.utcoffset() is None:
        raise EpisodeReadError("Timezone-aware episode timestamp required")
    return parsed.astimezone(timezone.utc)


def _positive(value, name):
    if type(value) is not int or value < 1:
        raise EpisodeReadError(f"Invalid {name}")


def _finite(value, name):
    if type(value) not in (int, float):
        raise EpisodeReadError(f"Invalid {name}")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise EpisodeReadError(f"Invalid {name}")


def _episode(item, user):
    if not isinstance(item, dict) or item.get("memory_type") not in _KINDS:
        raise EpisodeReadError("Unknown snapshot memory kind")
    metadata = item.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("user_id") != user:
        raise EpisodeReadError("Snapshot item owner differs")
    _json(item)
    if item["memory_type"] != "episodic":
        return None
    logical_id = item.get("id")
    if not isinstance(logical_id, str) or not logical_id:
        raise EpisodeReadError("Invalid episode ID")
    if not isinstance(item.get("content"), str):
        raise EpisodeReadError("Invalid episode content")
    session = metadata.get("session_id")
    if not isinstance(session, str) or not session:
        raise EpisodeReadError("Explicit session ID required")
    _finite(item.get("importance"), "importance")
    _time(item.get("timestamp"))
    derived = {"memory_id": logical_id, "episode_id": logical_id,
               "session_id": session, "timestamp": item["timestamp"],
               "memory_type": "episodic", "importance": item["importance"],
               "content": item["content"]}
    for key, value in derived.items():
        if key in metadata and _json(metadata[key]) != _json(value):
            raise EpisodeReadError("Episode metadata conflicts with snapshot")
    return dict(metadata) | derived


class PublishedEpisodeReadOperation:
    """A validated, immutable generation with narrow scoped read methods."""

    __slots__ = ("head", "_receipt", "_view", "_payloads", "_times", "_profile")

    def __init__(self, head, receipt, view, payloads, profile):
        self.head = head
        self._receipt = deepcopy(receipt)
        self._view = view
        self._payloads = deepcopy(payloads)
        self._times = {key: _time(payload["timestamp"]) for key, payload in payloads.items()}
        self._profile = profile

    @property
    def receipt(self):
        return deepcopy(self._receipt)

    def _criteria(self, *, session_id=None, min_importance=0, start_time=None,
                  end_time=None, limit=None):
        if session_id is not None and (not isinstance(session_id, str) or not session_id):
            raise EpisodeReadError("Invalid session ID")
        _finite(min_importance, "minimum importance")
        if limit is not None and (type(limit) is not int or not 1 <= limit <= _MAX_LIMIT):
            raise EpisodeReadError("Invalid read limit")
        start = _time(start_time) if start_time is not None else None
        end = _time(end_time) if end_time is not None else None
        if start and end and start > end:
            raise EpisodeReadError("Episode time range is reversed")
        filters = {"memory_type": "episodic", "user_id": self._view.scope.tenant_id,
                   "importance": VectorRange(gte=min_importance)}
        if session_id is not None:
            filters["session_id"] = session_id
        if start or end:
            filters["timestamp"] = VectorRange(gte=start, lte=end)
        selected = {key for key, payload in self._payloads.items()
                    if (session_id is None or payload["session_id"] == session_id)
                    and payload["importance"] >= min_importance
                    and (start is None or self._times[key] >= start)
                    and (end is None or self._times[key] <= end)}
        return filters, selected

    def _check(self, item, selected):
        if item.id not in selected or item.id not in self._payloads:
            raise EpisodeReadError("Unexpected episode point")
        if _json(item.payload) != _json(self._payloads[item.id]):
            raise EpisodeReadError("Episode point payload differs")
        return deepcopy(item)

    def scroll(self, *, session_id=None, min_importance=0, start_time=None, end_time=None):
        filters, selected = self._criteria(session_id=session_id,
                                           min_importance=min_importance,
                                           start_time=start_time, end_time=end_time)
        points = self._view.scroll(self._view.collection_name, filters=filters)
        seen = set()
        output = []
        for point in points:
            if point.id in seen:
                raise EpisodeReadError("Duplicate episode point")
            seen.add(point.id)
            output.append(self._check(point, selected))
        if seen != selected:
            raise EpisodeReadError("Selected episode membership differs")
        return output

    def count(self, *, session_id=None, min_importance=0, start_time=None, end_time=None):
        return len(self.scroll(session_id=session_id, min_importance=min_importance,
                               start_time=start_time, end_time=end_time))

    def get_item(self, episode_id):
        if not isinstance(episode_id, str) or not episode_id:
            raise EpisodeReadError("Invalid episode ID")
        payload = self._payloads.get(episode_id)
        minimum = min(0, payload["importance"]) if payload is not None else 0
        matches = [point for point in self.scroll(min_importance=minimum)
                   if point.id == episode_id]
        return matches[0] if matches else None

    def search_vector(self, query_vector, *, query_profile: EmbeddingProfile,
                      session_id=None, min_importance=0, start_time=None,
                      end_time=None, limit=5):
        if not isinstance(query_profile, EmbeddingProfile):
            raise EpisodeReadError("Episode query profile differs")
        try:
            validate_profile(query_profile)
        except IndexIdentityError as error:
            raise EpisodeReadError("Malformed episode query profile") from error
        if _json(asdict(query_profile)) != _json(asdict(self._profile)):
            raise EpisodeReadError("Episode query profile differs")
        if type(limit) is not int or not 1 <= limit <= _MAX_LIMIT:
            raise EpisodeReadError("Invalid read limit")
        if not isinstance(query_vector, (list, tuple)):
            raise EpisodeReadError("Invalid query vector")
        vector = list(query_vector)
        if len(vector) != self._profile.dimension:
            raise EpisodeReadError("Query dimension differs")
        for component in vector:
            _finite(component, "query component")
        if not math.isfinite(math.hypot(*vector)):
            raise EpisodeReadError("Invalid query magnitude")
        filters, selected = self._criteria(session_id=session_id,
                                           min_importance=min_importance,
                                           start_time=start_time, end_time=end_time,
                                           limit=limit)
        hits = self._view.search(self._view.collection_name, vector, filters=filters,
                                 limit=limit)
        seen = set()
        output = []
        for hit in hits:
            if hit.id in seen:
                raise EpisodeReadError("Duplicate episode hit")
            seen.add(hit.id)
            _finite(hit.score, "search score")
            output.append(self._check(hit, selected))
        return output


class PublishedEpisodeReadFactory:
    """Trust boundary: caller supplies a separately attested episode scope."""

    def __init__(self, service: VectorGenerationService, trusted_scope: VectorScope):
        if not isinstance(service, VectorGenerationService):
            raise TypeError("Vector generation service required")
        if not isinstance(trusted_scope, VectorScope) or trusted_scope.vector_kind != "episode":
            raise EpisodeReadError("Trusted episode scope required")
        identity = IndexIdentity.from_dict(trusted_scope.identity.to_dict())
        self._scope = VectorScope(trusted_scope.tenant_id, "episode",
                                  trusted_scope.namespace, identity)
        self._service = service

    def open_operation(self, authenticated_user_id: str):
        scope = self._scope
        if authenticated_user_id != scope.tenant_id:
            raise EpisodeReadError("Authenticated user differs from trusted scope")
        with self._service.authority.database.transaction() as cursor:
            row = cursor.execute("""select i.identity,i.index_revision,
                h.revision,h.generation_id,h.last_generation_id,
                h.index_revision as head_index_revision,h.snapshot_version,
                g.tenant_id as receipt_tenant,g.vector_kind as receipt_kind,
                g.namespace as receipt_namespace,g.index_key as receipt_index_key,
                g.state as receipt_state,g.base_revision,g.index_revision as receipt_index_revision,
                g.publication_revision,g.publication_snapshot_version,g.expected_count,
                g.content_digest,g.published_at,
                s.user_id as snapshot_user,s.version as snapshot_version_actual,s.payload as snapshot_payload,
                (select coalesce(jsonb_agg(to_jsonb(d)), '[]'::jsonb)
                   from memory_documents d where d.user_id=i.tenant_id) as documents
                from vector_indexes i
                left join vector_heads h on h.tenant_id=i.tenant_id
                    and h.vector_kind=i.vector_kind and h.namespace=i.namespace
                    and h.index_key=i.index_key
                left join vector_generations g on g.generation_id=h.last_generation_id
                    and g.tenant_id=i.tenant_id and g.vector_kind=i.vector_kind
                    and g.namespace=i.namespace and g.index_key=i.index_key
                left join user_snapshots s on s.user_id=i.tenant_id and s.kind='memory'
                where i.tenant_id=%s and i.vector_kind=%s and i.namespace=%s
                    and i.index_key=%s""", scope.key).fetchone()
        if row is None:
            raise EpisodeReadError("Episode index is absent or differs")
        try:
            registered = IndexIdentity.from_dict(row["identity"])
        except IndexIdentityError as error:
            raise EpisodeReadError("Episode index is malformed") from error
        if (_json(row["identity"]) != _json(scope.identity.to_dict())
                or registered != scope.identity):
            raise EpisodeReadError("Episode index is absent or differs")
        revision = row["revision"]
        index_revision = row["index_revision"]
        version = row["snapshot_version_actual"]
        _positive(revision, "head revision")
        _positive(index_revision, "index revision")
        _positive(version, "snapshot version")
        if (row["head_index_revision"] != index_revision
                or row["snapshot_version"] != version
                or row["snapshot_user"] != scope.tenant_id
                or row["last_generation_id"] is None):
            raise EpisodeReadError("Episode head and snapshot differ")
        receipt_id = row["last_generation_id"]
        count = row["expected_count"]
        digest = row["content_digest"]
        if (not isinstance(receipt_id, UUID)
                or row["receipt_tenant"] != scope.tenant_id
                or row["receipt_kind"] != "episode"
                or row["receipt_namespace"] != scope.namespace
                or row["receipt_index_key"] != scope.index_key
                or row["receipt_state"] != "published"
                or row["receipt_index_revision"] != index_revision
                or row["publication_revision"] != revision
                or row["publication_snapshot_version"] != version
                or row["base_revision"] != (None if revision == 1 else revision - 1)
                or type(count) is not int or not 0 <= count <= _MAX_POINTS
                or not isinstance(digest, str) or not _DIGEST.fullmatch(digest)
                or row["published_at"] is None
                or (row["generation_id"] is None) != (count == 0)
                or (count and row["generation_id"] != receipt_id)):
            raise EpisodeReadError("Episode publication receipt differs")
        snapshot = row["snapshot_payload"]
        if (not isinstance(snapshot, dict) or snapshot.get("user_id") != scope.tenant_id
                or not isinstance(snapshot.get("memories"), list)):
            raise EpisodeReadError("Memory snapshot differs")
        payloads = {}
        for item in snapshot["memories"]:
            projected = _episode(item, scope.tenant_id)
            if projected is not None:
                logical_id = item["id"]
                if logical_id in payloads:
                    raise EpisodeReadError("Duplicate snapshot episode ID")
                payloads[logical_id] = projected
        rows = {}
        for document in row["documents"]:
            if document.get("user_id") != scope.tenant_id:
                raise EpisodeReadError("Document row owner differs")
            metadata = _metadata(document.get("metadata"))
            if metadata.get("user_id") != scope.tenant_id or metadata.get("memory_type") not in _KINDS:
                raise EpisodeReadError("Unknown or cross-user document row")
            if metadata["memory_type"] != "episodic":
                continue
            logical_id = document.get("document_id")
            if (not isinstance(logical_id, str) or not logical_id
                    or logical_id in rows or not isinstance(document.get("content"), str)
                    or metadata.get("content") != document["content"]):
                raise EpisodeReadError("Malformed or duplicate episode row")
            rows[logical_id] = metadata
        if set(payloads) != set(rows) or len(payloads) != count:
            raise EpisodeReadError("Snapshot and document episode IDs differ")
        for key, payload in payloads.items():
            if _json(payload) != _json(rows[key]):
                raise EpisodeReadError("Episode document metadata differs")
        head = VectorHead("empty" if count == 0 else "published", revision,
                          None if count == 0 else receipt_id, index_revision, version)
        view = GenerationVectorStore(self._service.raw, scope, head)
        points = view.scroll(view.collection_name, with_vectors=True,
                             expected_manifest=(count, digest))
        seen = set()
        for point in points:
            if point.id in seen or point.id not in payloads:
                raise EpisodeReadError("Unexpected or duplicate manifest episode")
            seen.add(point.id)
            if _json(point.payload) != _json(payloads[point.id]):
                raise EpisodeReadError("Manifest episode payload differs")
        if seen != set(payloads):
            raise EpisodeReadError("Manifest episode membership differs")
        receipt = {"generation_id": receipt_id, "revision": revision,
                   "snapshot_version": version, "expected_count": count,
                   "content_digest": digest, "published_at": row["published_at"]}
        return PublishedEpisodeReadOperation(head, receipt, view, payloads,
                                             scope.identity.profile)
