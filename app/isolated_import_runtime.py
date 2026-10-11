"""Explicit, opt-in identity and read-only readiness for isolated imports."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
import json
import os
import re
from threading import Lock
from typing import Callable, Literal, Mapping
from urllib.parse import parse_qsl, unquote, urlsplit
from uuid import UUID

from psycopg.conninfo import conninfo_to_dict
from psycopg.errors import InvalidSchemaName, UndefinedTable

from app.object_store import S3ObjectStore
from app.postgres import PostgresDatabase
from app.postgres_history_document_witnesses import (
    PostgresHistoryDocumentWitnessRepository, document_evidence,
)
from app.postgres_snapshots import PostgresSnapshotRepository
from app.postgres_vector_generations import PostgresVectorGenerationAuthority, VectorScope
from app.published_episode_reads import (
    PublishedEpisodeReadFactory, _episode, _json, _metadata,
)
from app.vector_generation_service import VectorGenerationService
from hello_agents.memory.rag.embedding_profile import EmbeddingProfile
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.storage.vector_store import QdrantVectorStore


_SCHEMA = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")
_HEAD = "20261007_16"
_RETAINED_SCHEMA = "cutover_combined_refs_20260930_0f7592dc3978"
_RETAINED_BUCKET = "cutover-paired-bf20-69fcab0f0cbd4300a0545f2008c15779"
_RELATIONS = (
    "alembic_version", "users", "auth_sessions", "user_snapshots",
    "memory_documents", "import_batches", "import_tasks", "import_objects",
    "import_task_attempts", "import_user_schedule", "user_mutation_leases",
    "document_objects", "vector_indexes", "vector_heads", "vector_generations",
    "history_document_witnesses", "import_publication_evidence",
    "user_publication_gates", "generation_reservations",
    "import_publication_private_payloads", "import_publication_recovery_queue",
    "import_publication_recovery_leases",
    "import_publication_recovery_token_issuance",
    "import_publication_recovery_schedule",
)
_PROFILE_FIELDS = {item.name for item in fields(EmbeddingProfile)}


class IsolatedImportConfigurationError(ValueError):
    """A code-only error; untrusted setting values are never included."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Isolated import configuration rejected: {code}")


def _reject(code: str) -> None:
    raise IsolatedImportConfigurationError(code)


def _endpoint(value: str, key: str) -> None:
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                 and parsed.username is None and parsed.password is None
                 and not parsed.query and not parsed.fragment and not parsed.path.strip("/"))
        _ = parsed.port
    except Exception:
        valid = False
    if not valid or any(char.isspace() for char in value):
        _reject(key)


def _host(value: str) -> str:
    lowered = value.lower()
    return "loopback" if lowered in {"localhost", "127.0.0.1", "::1"} else lowered


def _connection_identity(value: str, key: str, *, isolated: bool) -> tuple[str, str, str, int, str | None]:
    if not isinstance(value, str):
        _reject(key)
    try:
        normalized = ("postgresql://" + value[len("postgresql+psycopg://"):]
                      if not isolated and value.startswith("postgresql+psycopg://") else value)
        parsed = urlsplit(normalized)
        if (parsed.scheme != "postgresql" or parsed.fragment or not parsed.hostname
                or parsed.path in {"", "/"} or any(char.isspace() for char in value)):
            raise ValueError()
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        identity_keys = {"host", "hostaddr", "port", "dbname", "options", "service", "servicefile"}
        if any(sum(1 for name, _ in pairs if name == key_name) > 1 for key_name in identity_keys):
            raise ValueError()
        if isolated and (len(pairs) != 1 or pairs[0][0] != "options"):
            raise ValueError()
        effective = conninfo_to_dict(normalized)
        if (effective.get("service") or effective.get("hostaddr")
                or any(name in {"service", "hostaddr", "servicefile"} for name, _ in pairs)):
            raise ValueError()
        host = effective.get("host")
        database = effective.get("dbname")
        port_text = effective.get("port") or "5432"
        if (not isinstance(host, str) or not host or "," in host
                or not isinstance(database, str) or not database
                or any(char in database for char in ("=", "\n", "\r"))
                or "://" in database or not str(port_text).isdigit()
                or "," in str(port_text) or not 1 <= int(port_text) <= 65535):
            raise ValueError()
        option = effective.get("options", "")
        if isolated:
            if not option.startswith("-csearch_path="):
                raise ValueError()
            schema = option[len("-csearch_path="):]
            if not _SCHEMA.fullmatch(schema):
                raise ValueError()
        elif option:
            match = re.fullmatch(r"-c\s*search_path=([a-z][a-z0-9_]{0,62})", option)
            if match is None:
                raise ValueError()
            schema = match.group(1)
        else:
            # The effective ordinary path may come from role/database defaults.
            # A same-database target cannot be proven separate without it.
            schema = None
        return _host(host), database, effective.get("user", ""), int(port_text), schema
    except Exception:
        _reject(key)


def _dsn(value: str, key: str) -> tuple[str, str, str, int, str]:
    identity = _connection_identity(value, key, isolated=True)
    return identity  # type: ignore[return-value]


def _ordinary_dsn(value: str) -> tuple[str, str, str, int, str | None]:
    return _connection_identity(value, "DATABASE_URL", isolated=False)


def _profile(value: str, key: str) -> EmbeddingProfile:
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, dict) or set(parsed) != _PROFILE_FIELDS:
            raise ValueError()
        profile = EmbeddingProfile(**parsed)
        IndexIdentity("qdrant", "validation", profile)
        return profile
    except Exception:
        _reject(key)


def _integer(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name, str(default))
    if not isinstance(raw, str) or not re.fullmatch(r"[0-9]+", raw):
        _reject(name)
    return int(raw)


@dataclass(frozen=True)
class IsolatedImportSettings:
    role: Literal["api", "worker"]
    enabled: bool
    database_url: str = field(repr=False)
    schema: str
    s3_endpoint_url: str = field(repr=False)
    s3_bucket: str
    qdrant_url: str = field(repr=False)
    rag_collection: str
    episode_collection: str
    rag_profile: EmbeddingProfile = field(repr=False)
    episode_profile: EmbeddingProfile = field(repr=False)
    trusted_user_ids: frozenset[str]
    s3_region: str = "us-east-1"
    s3_access_key_id: str | None = field(default=None, repr=False)
    s3_secret_access_key: str | None = field(default=None, repr=False)
    qdrant_api_key: str | None = field(default=None, repr=False)
    ordinary_database_url: str | None = field(default=None, repr=False)
    ordinary_s3_bucket: str | None = field(default=None, repr=False)
    ordinary_rag_collection: str | None = field(default=None, repr=False)
    ordinary_episode_collection: str | None = field(default=None, repr=False)
    pg_pool_min: int = 1
    pg_pool_max: int = 10
    lease_seconds: int = 60
    heartbeat_seconds: int = 10
    attempt_seconds: int = 300
    provider_timeout_seconds: int = 30
    stop_wait_seconds: int = 10
    provider_max_retries: int = 2

    def __post_init__(self) -> None:
        if self.role not in ("api", "worker"):
            _reject("role")
        if self.enabled is not True:
            _reject("ISOLATED_IMPORT_ENABLED")
        if not isinstance(self.schema, str) or not _SCHEMA.fullmatch(self.schema) or self.schema in {"public", _RETAINED_SCHEMA}:
            _reject("ISOLATED_IMPORT_SCHEMA")
        identity = _dsn(self.database_url, "ISOLATED_IMPORT_DATABASE_URL")
        if identity[-1] != self.schema:
            _reject("ISOLATED_IMPORT_SCHEMA")
        for value, key in ((self.s3_endpoint_url, "ISOLATED_IMPORT_S3_ENDPOINT_URL"),
                           (self.qdrant_url, "ISOLATED_IMPORT_QDRANT_URL")):
            _endpoint(value, key)
        if (not isinstance(self.s3_bucket, str) or not _BUCKET.fullmatch(self.s3_bucket)
                or self.s3_bucket in {_RETAINED_BUCKET, self.ordinary_s3_bucket}):
            _reject("ISOLATED_IMPORT_S3_BUCKET")
        if bool(self.s3_access_key_id) != bool(self.s3_secret_access_key):
            _reject("S3_ACCESS_KEY_ID")
        if not isinstance(self.s3_region, str) or not self.s3_region.strip():
            _reject("S3_REGION")
        try:
            rag = IndexIdentity("qdrant", self.rag_collection, self.rag_profile)
            episode = IndexIdentity("qdrant", self.episode_collection, self.episode_profile)
        except Exception:
            _reject("ISOLATED_IMPORT_PROFILE_OR_COLLECTION")
        if (rag.base_collection == episode.base_collection
                or rag.physical_collection == episode.physical_collection
                or self.rag_collection in {self.ordinary_rag_collection, self.ordinary_episode_collection}
                or self.episode_collection in {self.ordinary_rag_collection, self.ordinary_episode_collection}):
            _reject("ISOLATED_IMPORT_COLLECTION_IDENTITY")
        if self.ordinary_database_url is not None:
            ordinary = _ordinary_dsn(self.ordinary_database_url)
            if ((ordinary[0], ordinary[1], ordinary[3]) ==
                    (identity[0], identity[1], identity[3])
                    and ordinary[4] in {None, identity[4]}):
                _reject("ISOLATED_IMPORT_DATABASE_IDENTITY")
        if not isinstance(self.trusted_user_ids, frozenset) or len(self.trusted_user_ids) != 2:
            _reject("ISOLATED_IMPORT_TRUSTED_USER_IDS")
        for user_id in self.trusted_user_ids:
            try:
                if not isinstance(user_id, str) or str(UUID(user_id)) != user_id:
                    raise ValueError()
            except Exception:
                _reject("ISOLATED_IMPORT_TRUSTED_USER_IDS")
        numbers = (self.pg_pool_min, self.pg_pool_max, self.lease_seconds,
                   self.heartbeat_seconds, self.attempt_seconds,
                   self.provider_timeout_seconds, self.stop_wait_seconds)
        if (any(type(number) is not int or number < 1 for number in numbers)
                or self.pg_pool_min > self.pg_pool_max
                or self.heartbeat_seconds * 3 >= self.lease_seconds
                or self.provider_timeout_seconds > self.attempt_seconds
                or type(self.provider_max_retries) is not int
                or not 0 <= self.provider_max_retries <= 2):
            _reject("ISOLATED_IMPORT_TIMING")

    @classmethod
    def from_env(cls, role: Literal["api", "worker"], env: Mapping[str, str] | None = None) -> IsolatedImportSettings:
        values = os.environ if env is None else env
        def required(name: str) -> str:
            value = values.get(name)
            if not isinstance(value, str) or not value:
                _reject(name)
            return value
        enabled = values.get("ISOLATED_IMPORT_ENABLED") == "1"
        if not enabled:
            _reject("ISOLATED_IMPORT_ENABLED")
        raw_users = required("ISOLATED_IMPORT_TRUSTED_USER_IDS").split(",")
        if len(raw_users) != 2 or any(not part or part.strip() != part for part in raw_users) or len(set(raw_users)) != 2:
            _reject("ISOLATED_IMPORT_TRUSTED_USER_IDS")
        return cls(
            role=role, enabled=True,
            database_url=required("ISOLATED_IMPORT_DATABASE_URL"),
            schema=required("ISOLATED_IMPORT_SCHEMA"),
            s3_endpoint_url=required("ISOLATED_IMPORT_S3_ENDPOINT_URL"),
            s3_bucket=required("ISOLATED_IMPORT_S3_BUCKET"),
            qdrant_url=required("ISOLATED_IMPORT_QDRANT_URL"),
            rag_collection=required("ISOLATED_IMPORT_RAG_COLLECTION"),
            episode_collection=required("ISOLATED_IMPORT_EPISODE_COLLECTION"),
            rag_profile=_profile(required("ISOLATED_IMPORT_RAG_PROFILE_JSON"), "ISOLATED_IMPORT_RAG_PROFILE_JSON"),
            episode_profile=_profile(required("ISOLATED_IMPORT_EPISODE_PROFILE_JSON"), "ISOLATED_IMPORT_EPISODE_PROFILE_JSON"),
            trusted_user_ids=frozenset(raw_users),
            s3_region=values.get("S3_REGION", "us-east-1"),
            s3_access_key_id=values.get("S3_ACCESS_KEY_ID") or None,
            s3_secret_access_key=values.get("S3_SECRET_ACCESS_KEY") or None,
            qdrant_api_key=values.get("QDRANT_API_KEY") or None,
            ordinary_database_url=values.get("DATABASE_URL"),
            ordinary_s3_bucket=values.get("S3_BUCKET") or None,
            ordinary_rag_collection=values.get("QDRANT_COLLECTION") or None,
            ordinary_episode_collection=values.get("EPISODE_QDRANT_COLLECTION") or None,
            pg_pool_min=_integer(values, "ISOLATED_IMPORT_PG_POOL_MIN", 1),
            pg_pool_max=_integer(values, "ISOLATED_IMPORT_PG_POOL_MAX", 10),
            lease_seconds=_integer(values, "ISOLATED_IMPORT_LEASE_SECONDS", 60),
            heartbeat_seconds=_integer(values, "ISOLATED_IMPORT_HEARTBEAT_SECONDS", 10),
            attempt_seconds=_integer(values, "ISOLATED_IMPORT_ATTEMPT_SECONDS", 300),
            provider_timeout_seconds=_integer(values, "ISOLATED_IMPORT_PROVIDER_TIMEOUT_SECONDS", 30),
            stop_wait_seconds=_integer(values, "ISOLATED_IMPORT_STOP_WAIT_SECONDS", 10),
            provider_max_retries=_integer(values, "ISOLATED_IMPORT_PROVIDER_MAX_RETRIES", 2),
        )

    def safe_summary(self) -> dict[str, object]:
        return {"role": self.role, "enabled": True, "schema": self.schema,
                "s3_bucket": self.s3_bucket, "trusted_user_count": len(self.trusted_user_ids)}


@dataclass(frozen=True)
class ProviderReadiness:
    available: bool
    rag_profile: EmbeddingProfile = field(repr=False)
    episode_profile: EmbeddingProfile = field(repr=False)


def _schema_ready(database: PostgresDatabase, settings: IsolatedImportSettings) -> bool:
    # The relation names are fixed source literals. to_regclass uses the fully
    # qualified schema, so a same-named public table cannot satisfy this gate.
    with database.transaction() as cursor:
        names = [f"{settings.schema}.{name}" for name in _RELATIONS]
        found = cursor.execute("select to_regclass(relation.name) is not null as present from unnest(%s::text[]) as relation(name)", (names,)).fetchall()
        if len(found) != len(_RELATIONS) or not all(item["present"] for item in found):
            return False
        row = cursor.execute("""select current_database() as database_name,
            current_schema() as schema_name,current_setting('search_path') as search_path,
            (select count(*) from alembic_version) as migration_count,
            (select min(version_num) from alembic_version) as migration_head""").fetchone()
        if (row is None or row["database_name"] != unquote(urlsplit(settings.database_url).path[1:])
                or row["schema_name"] != settings.schema
                or row["search_path"] != settings.schema
                or row["migration_count"] != 1
                or row["migration_head"] != _HEAD):
            return False
    return True


_AUTHORITY_SQL = """select
    (select to_jsonb(u) from users u where u.id=%s) as account,
    (select coalesce(jsonb_agg(to_jsonb(i) order by i.vector_kind,i.namespace,i.index_key),'[]'::jsonb)
       from vector_indexes i where i.tenant_id=%s) as indexes,
    (select coalesce(jsonb_agg(to_jsonb(h) order by h.vector_kind,h.namespace,h.index_key),'[]'::jsonb)
       from vector_heads h where h.tenant_id=%s) as heads,
    (select coalesce(jsonb_agg(to_jsonb(g) order by g.vector_kind,g.namespace,g.index_key,g.generation_id),'[]'::jsonb)
       from vector_generations g where g.tenant_id=%s and g.generation_id in
       (select last_generation_id from vector_heads where tenant_id=%s)) as receipts,
    (select coalesce(jsonb_agg(to_jsonb(w) order by w.namespace,w.index_key,w.head_revision),'[]'::jsonb)
       from history_document_witnesses w where w.tenant_id=%s) as witnesses,
    (select coalesce(jsonb_agg(to_jsonb(s) order by s.kind),'[]'::jsonb)
       from user_snapshots s where s.user_id=%s) as snapshots,
    (select coalesce(jsonb_agg(to_jsonb(d) order by d.document_id),'[]'::jsonb)
       from memory_documents d where d.user_id=%s) as memory_documents,
    (select coalesce(jsonb_agg(to_jsonb(o) order by o.document_id),'[]'::jsonb)
       from document_objects o where o.user_id=%s) as document_objects"""


def _capture_authority(database: PostgresDatabase, user_id: str) -> dict:
    with database.transaction() as cursor:
        return cursor.execute(_AUTHORITY_SQL, (user_id,) * 9).fetchone()


def _one(rows: list[dict], **selector) -> dict:
    selected = [row for row in rows if all(row.get(key) == value for key, value in selector.items())]
    if len(selected) != 1:
        raise ValueError("authority_row")
    return selected[0]


def _full_episode_points(authority: dict, user_id: str, operation):
    memory = _one(authority["snapshots"], kind="memory")["payload"]
    if memory.get("user_id") != user_id or not isinstance(memory.get("memories"), list):
        raise ValueError("memory_snapshot")
    payloads = [_episode(item, user_id) for item in memory["memories"]]
    minimum = min((0, *(item["importance"] for item in payloads if item is not None)))
    return operation.scroll(min_importance=minimum)


def _final_matches(final: dict, user_id: str, rag_scope: VectorScope,
                   episode_scope: VectorScope, history, pairing, evidence,
                   retained, operation, points) -> bool:
    try:
        if final["account"]["status"] != "active":
            return False
        snapshots = final["snapshots"]
        history_row = _one(snapshots, kind="history")
        memory_row = _one(snapshots, kind="memory")
        if (history_row["version"] != history.version
                or history_row["payload"] != history.data):
            return False
        final_evidence = document_evidence(user_id, history_row["payload"])
        if final_evidence != evidence:
            return False
        PostgresHistoryDocumentWitnessRepository.check_document_pairing(
            pairing, final_evidence, history_row["version"])
        rag_index = _one(final["indexes"], tenant_id=user_id, vector_kind="rag",
                         namespace=rag_scope.namespace, index_key=rag_scope.index_key)
        episode_index = _one(final["indexes"], tenant_id=user_id, vector_kind="episode",
                             namespace=episode_scope.namespace, index_key=episode_scope.index_key)
        for row, scope in ((rag_index, rag_scope), (episode_index, episode_scope)):
            if row["identity"] != scope.identity.to_dict():
                return False
        rag_head = _one(final["heads"], tenant_id=user_id, vector_kind="rag",
                        namespace=rag_scope.namespace, index_key=rag_scope.index_key)
        episode_head = _one(final["heads"], tenant_id=user_id, vector_kind="episode",
                            namespace=episode_scope.namespace, index_key=episode_scope.index_key)
        for row, head in ((rag_head, pairing.head), (episode_head, operation.head)):
            if (row["revision"], row["generation_id"], row["index_revision"], row["snapshot_version"]) != (
                    head.revision, str(head.generation_id) if head.generation_id else None,
                    head.index_revision, head.snapshot_version):
                return False
        if (rag_head["last_generation_id"] != str(pairing.last_generation_id)
                or rag_head["index_revision"] != rag_index["index_revision"]):
            return False
        rag_receipt = _one(final["receipts"], generation_id=str(pairing.last_generation_id))
        if (rag_receipt["tenant_id"] != user_id or rag_receipt["vector_kind"] != "rag"
                or rag_receipt["namespace"] != rag_scope.namespace
                or rag_receipt["index_key"] != rag_scope.index_key
                or rag_receipt["state"] != "published" or rag_receipt["expected_count"] != pairing.expected_count
                or rag_receipt["content_digest"] != pairing.content_digest
                or rag_receipt["publication_revision"] != pairing.receipt_revision
                or rag_receipt["index_revision"] != pairing.receipt_index_revision
                or rag_receipt["publication_snapshot_version"] != pairing.receipt_snapshot_version
                or rag_receipt["published_at"] is None):
            return False
        witness = [row for row in final["witnesses"] if row["head_revision"] == pairing.head.revision
                   and row["namespace"] == rag_scope.namespace and row["index_key"] == rag_scope.index_key]
        if pairing.has_witness:
            if (len(witness) != 1 or witness[0]["document_count"] != pairing.witness_count
                    or witness[0]["documents_sha256"] != pairing.witness_digest
                    or witness[0]["last_generation_id"] != str(pairing.last_generation_id)
                    or witness[0]["index_revision"] != pairing.receipt_index_revision
                    or witness[0]["publication_snapshot_version"] != pairing.receipt_snapshot_version):
                return False
        elif witness:
            return False
        fixed = tuple((row["document_id"], row["bucket"], row["object_key"], row["version_id"],
                       row["sha256"], row["size_bytes"], row["history_record_sha256"])
                      for row in final["document_objects"])
        if len(fixed) != final_evidence.count or set(fixed) != set(retained):
            return False
        episode_receipt = _one(final["receipts"], generation_id=episode_head["last_generation_id"])
        public_receipt = operation.receipt
        if (episode_receipt["tenant_id"] != user_id or episode_receipt["vector_kind"] != "episode"
                or episode_receipt["namespace"] != episode_scope.namespace
                or episode_receipt["index_key"] != episode_scope.index_key
                or episode_receipt["published_at"] is None
                or episode_receipt["state"] != "published" or
                (episode_receipt["generation_id"], episode_receipt["publication_revision"],
                 episode_receipt["publication_snapshot_version"], episode_receipt["expected_count"],
                 episode_receipt["content_digest"]) !=
                (str(public_receipt["generation_id"]), public_receipt["revision"],
                 public_receipt["snapshot_version"], public_receipt["expected_count"],
                 public_receipt["content_digest"]) or
                episode_receipt["index_revision"] != episode_index["index_revision"] or
                episode_head["snapshot_version"] != memory_row["version"] or
                episode_head["revision"] != episode_receipt["publication_revision"] or
                episode_head["index_revision"] != episode_receipt["index_revision"] or
                (episode_head["generation_id"] is None) != (episode_receipt["expected_count"] == 0) or
                (episode_receipt["expected_count"] > 0 and
                 episode_head["generation_id"] != episode_receipt["generation_id"])):
            return False
        memory = memory_row["payload"]
        if memory.get("user_id") != user_id or not isinstance(memory.get("memories"), list):
            return False
        payloads = {}
        for item in memory["memories"]:
            payload = _episode(item, user_id)
            if payload is not None:
                if item["id"] in payloads:
                    return False
                payloads[item["id"]] = payload
        documents = {}
        for row in final["memory_documents"]:
            if row.get("user_id") != user_id:
                return False
            metadata = _metadata(row.get("metadata"))
            if metadata.get("user_id") != user_id or metadata.get("memory_type") not in {
                    "episodic", "semantic", "working", "perceptual"}:
                return False
            if metadata["memory_type"] == "episodic":
                key = row.get("document_id")
                if key in documents or metadata.get("content") != row.get("content"):
                    return False
                documents[key] = metadata
        if (set(payloads) != set(documents) or len(payloads) != episode_receipt["expected_count"]
                or any(_json(payload) != _json(documents[key]) for key, payload in payloads.items())):
            return False
        if (len(points) != len(payloads) or len({point.id for point in points}) != len(points)
                or {point.id for point in points} != set(payloads)
                or any(_json(point.payload) != _json(payloads[point.id]) for point in points)):
            return False
        return True
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


class IsolatedImportRuntime:
    def __init__(self, settings: IsolatedImportSettings, database: PostgresDatabase,
                 provider_probe: Callable[[], ProviderReadiness] | None):
        self.settings = settings
        self.database = database
        self.role = settings.role
        self.trusted_user_ids = settings.trusted_user_ids
        self._provider_probe = provider_probe
        self._store: S3ObjectStore | None = None
        self._raw: QdrantVectorStore | None = None
        self._closed = False
        self._lifecycle_lock = Lock()

    def __enter__(self) -> IsolatedImportRuntime:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            for owned in (self._raw.client if self._raw is not None else None,
                          self._store.client if self._store is not None else None,
                          self.database):
                if owned is None:
                    continue
                closer = getattr(owned, "close", None)
                if callable(closer):
                    try:
                        closer()
                    except Exception:
                        # All owned resources still receive their close attempt.
                        pass

    def recovery_ready(self) -> bool:
        if self._closed:
            return False
        try:
            if not self.database.ping():
                return False
        except Exception:
            return False
        try:
            if not _schema_ready(self.database, self.settings):
                _reject("ISOLATED_IMPORT_DATABASE_IDENTITY")
            return True
        except IsolatedImportConfigurationError:
            raise
        except (UndefinedTable, InvalidSchemaName):
            _reject("ISOLATED_IMPORT_DATABASE_IDENTITY")
        except Exception:
            return False

    def _providers(self) -> tuple[S3ObjectStore, QdrantVectorStore]:
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("isolated_runtime_closed")
            if self._store is None:
                import boto3
                from botocore.config import Config
                client = boto3.client("s3", endpoint_url=self.settings.s3_endpoint_url,
                                      region_name=self.settings.s3_region,
                                      aws_access_key_id=self.settings.s3_access_key_id,
                                      aws_secret_access_key=self.settings.s3_secret_access_key,
                                      config=Config(s3={"addressing_style": "path"}))
                self._store = S3ObjectStore(client, self.settings.s3_bucket)
            if self._raw is None:
                self._raw = QdrantVectorStore(url=self.settings.qdrant_url,
                                              api_key=self.settings.qdrant_api_key,
                                              retry_delays=())
            return self._store, self._raw

    def ordinary_ready(self, user_id: str) -> bool:
        if not isinstance(user_id, str) or user_id not in self.trusted_user_ids or not self.recovery_ready():
            return False
        for _ in range(2):
            try:
                before = _capture_authority(self.database, user_id)
                if before["account"] is None or before["account"]["status"] != "active":
                    return False
                if self._provider_probe is None:
                    return False
                report = self._provider_probe()
                if (type(report) is not ProviderReadiness or report.available is not True
                        or report.rag_profile != self.settings.rag_profile
                        or report.episode_profile != self.settings.episode_profile):
                    return False
                store, raw = self._providers()
                store.check_ready()
                rag_identity = IndexIdentity("qdrant", self.settings.rag_collection, self.settings.rag_profile)
                episode_identity = IndexIdentity("qdrant", self.settings.episode_collection, self.settings.episode_profile)
                for identity in (rag_identity, episode_identity):
                    raw.require_collection(identity.physical_collection,
                                           identity.profile.dimension, identity.profile.distance)
                rag_scope = VectorScope(user_id, "rag", f"pdf_{user_id}", rag_identity)
                episode_scope = VectorScope(user_id, "episode", "episodes", episode_identity)
                history = PostgresSnapshotRepository(self.database).read(user_id, "history")
                if history is None:
                    return False
                witness = PostgresHistoryDocumentWitnessRepository(self.database, store)
                pairing = witness.read_current(rag_scope)
                evidence = document_evidence(user_id, history.data)
                witness.check_document_pairing(pairing, evidence, history.version)
                retained = witness.verify_retained_references(rag_scope, evidence)
                service = VectorGenerationService(PostgresVectorGenerationAuthority(self.database), raw)
                operation = PublishedEpisodeReadFactory(service, episode_scope).open_operation(user_id)
                candidate = _capture_authority(self.database, user_id)
                points = _full_episode_points(candidate, user_id, operation)
                after = _capture_authority(self.database, user_id)
                if before == after and _final_matches(after, user_id, rag_scope, episode_scope,
                                                       history, pairing, evidence, retained, operation, points):
                    return True
            except Exception:
                continue
        return False


def open_isolated_runtime(settings: IsolatedImportSettings, *,
                          provider_probe: Callable[[], ProviderReadiness] | None = None) -> IsolatedImportRuntime:
    if not isinstance(settings, IsolatedImportSettings):
        _reject("settings")
    database = PostgresDatabase(settings.database_url, min_size=settings.pg_pool_min,
                                max_size=settings.pg_pool_max)
    try:
        database.open()
        if not _schema_ready(database, settings):
            _reject("ISOLATED_IMPORT_DATABASE_IDENTITY")
        return IsolatedImportRuntime(settings, database, provider_probe)
    except Exception:
        was_open = database._opened
        try:
            database.close()
        except Exception:
            pass
        finally:
            # PostgresDatabase.close() intentionally skips a pool whose open()
            # failed before it set _opened. This runtime still owns that pool.
            if not was_open:
                try:
                    database._pool.close()
                except Exception:
                    pass
        _reject("ISOLATED_IMPORT_DATABASE_IDENTITY")
