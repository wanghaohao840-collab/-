"""Opt-in isolated runtime contract and disposable service acceptance."""

from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, replace
import json
import os
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.isolated_import_runtime import (
    IsolatedImportConfigurationError, IsolatedImportRuntime, IsolatedImportSettings, ProviderReadiness,
    _capture_authority, open_isolated_runtime,
)
from hello_agents.memory.rag.embedding_profile import EmbeddingProfile
from hello_agents.memory.rag.index_identity import IndexIdentity


def _values() -> dict[str, str]:
    users = (str(uuid4()), str(uuid4()))
    rag = EmbeddingProfile("simple", "SECRET_MARKER", "deterministic", "v1", 4)
    episode = EmbeddingProfile("test-explicit", "SECRET_MARKER", "episode", "v1", 4)
    return {
        "ISOLATED_IMPORT_ENABLED": "1",
        "ISOLATED_IMPORT_DATABASE_URL": "postgresql://user:SECRET_MARKER@localhost:5432/test?options=-csearch_path%3Dp2_test_schema",
        "ISOLATED_IMPORT_SCHEMA": "p2_test_schema",
        "ISOLATED_IMPORT_S3_ENDPOINT_URL": "http://127.0.0.1:9000",
        "ISOLATED_IMPORT_S3_BUCKET": "p2-test-bucket",
        "ISOLATED_IMPORT_QDRANT_URL": "http://127.0.0.1:6333",
        "ISOLATED_IMPORT_RAG_COLLECTION": "p2_rag",
        "ISOLATED_IMPORT_EPISODE_COLLECTION": "p2_episode",
        "ISOLATED_IMPORT_RAG_PROFILE_JSON": json.dumps(asdict(rag)),
        "ISOLATED_IMPORT_EPISODE_PROFILE_JSON": json.dumps(asdict(episode)),
        "ISOLATED_IMPORT_TRUSTED_USER_IDS": ",".join(users),
        "S3_ACCESS_KEY_ID": "SECRET_MARKER",
        "S3_SECRET_ACCESS_KEY": "SECRET_MARKER",
        "QDRANT_API_KEY": "SECRET_MARKER",
    }


def test_settings_are_opt_in_pure_and_secret_safe(monkeypatch):
    values = _values()
    for flag in (None, "", "0", "false", "True"):
        changed = dict(values)
        if flag is None:
            changed.pop("ISOLATED_IMPORT_ENABLED")
        else:
            changed["ISOLATED_IMPORT_ENABLED"] = flag
        with pytest.raises(IsolatedImportConfigurationError):
            IsolatedImportSettings.from_env("api", changed)
    settings = IsolatedImportSettings.from_env("api", values)
    assert settings.trusted_user_ids == frozenset(values["ISOLATED_IMPORT_TRUSTED_USER_IDS"].split(","))
    assert "SECRET_MARKER" not in repr(settings)
    assert "SECRET_MARKER" not in repr(settings.safe_summary())
    assert "SECRET_MARKER" not in repr(ProviderReadiness(True, settings.rag_profile, settings.episode_profile))
    assert replace(settings, role="worker").role == "worker"
    with pytest.raises(IsolatedImportConfigurationError):
        replace(settings, enabled=False)
    with pytest.raises(IsolatedImportConfigurationError):
        replace(settings, trusted_user_ids=frozenset())


@pytest.mark.parametrize("key,value", [
    ("ISOLATED_IMPORT_DATABASE_URL", "postgresql://localhost/test?options=-csearch_path%3Dpublic"),
    ("ISOLATED_IMPORT_DATABASE_URL", "postgresql://localhost/test?options=-csearch_path%3Dp2_test_schema%2Cpublic"),
    ("ISOLATED_IMPORT_DATABASE_URL", "postgresql://localhost/test?options=-csearch_path%3Dp2_test_schema&options=x"),
    ("ISOLATED_IMPORT_SCHEMA", "public"),
    ("ISOLATED_IMPORT_SCHEMA", "cutover_combined_refs_20260930_0f7592dc3978"),
    ("ISOLATED_IMPORT_S3_BUCKET", "cutover-paired-bf20-69fcab0f0cbd4300a0545f2008c15779"),
    ("ISOLATED_IMPORT_S3_ENDPOINT_URL", "http://user:password@localhost:9000"),
    ("ISOLATED_IMPORT_QDRANT_URL", "ftp://localhost:6333"),
    ("ISOLATED_IMPORT_EPISODE_COLLECTION", "p2_rag"),
    ("ISOLATED_IMPORT_TRUSTED_USER_IDS", " "),
    ("ISOLATED_IMPORT_HEARTBEAT_SECONDS", "20"),
    ("ISOLATED_IMPORT_PROVIDER_MAX_RETRIES", "3"),
    ("S3_SECRET_ACCESS_KEY", ""),
])
def test_settings_reject_unsafe_identity_before_io(key, value):
    values = _values()
    values[key] = value
    with pytest.raises(IsolatedImportConfigurationError) as error:
        IsolatedImportSettings.from_env("api", values)
    assert "SECRET_MARKER" not in str(error.value)


def test_settings_reject_ordinary_reuse_and_bad_profile():
    values = _values()
    values["DATABASE_URL"] = values["ISOLATED_IMPORT_DATABASE_URL"]
    with pytest.raises(IsolatedImportConfigurationError):
        IsolatedImportSettings.from_env("api", values)
    values.pop("DATABASE_URL")
    values["S3_BUCKET"] = values["ISOLATED_IMPORT_S3_BUCKET"]
    with pytest.raises(IsolatedImportConfigurationError):
        IsolatedImportSettings.from_env("api", values)
    values.pop("S3_BUCKET")
    values["QDRANT_COLLECTION"] = values["ISOLATED_IMPORT_RAG_COLLECTION"]
    with pytest.raises(IsolatedImportConfigurationError):
        IsolatedImportSettings.from_env("api", values)
    values.pop("QDRANT_COLLECTION")
    values["ISOLATED_IMPORT_RAG_PROFILE_JSON"] = '{"provider":"simple"}'
    with pytest.raises(IsolatedImportConfigurationError):
        IsolatedImportSettings.from_env("api", values)


@pytest.mark.parametrize("ordinary", [
    "postgresql://other:secret@localhost/test?options=-csearch_path%3Dp2_test_schema",
    "postgresql://other:secret@localhost:5432/test?options=-csearch_path%3Dp2_test_schema",
    "postgresql://other:secret@127.0.0.1:5432/%74est?options=-csearch_path%3Dp2_test_schema",
    "postgresql://other:secret@localhost/test?options=-c%20search_path%3Dp2_test_schema",
    "postgresql://other:secret@localhost:5433/other?port=5432&dbname=test&options=-csearch_path%3Dp2_test_schema",
    "postgresql://other:secret@local%68ost:5432/test?options=-csearch_path%3Dp2_test_schema",
    "postgresql://other:secret@localhost/test",
])
@pytest.mark.parametrize("construction", ["env", "direct"])
def test_equivalent_ordinary_database_identity_rejected(ordinary, construction):
    values = _values()
    values["DATABASE_URL"] = ordinary
    baseline = IsolatedImportSettings.from_env("api", _values())
    with pytest.raises(IsolatedImportConfigurationError) as error:
        if construction == "env":
            IsolatedImportSettings.from_env("api", values)
        else:
            replace(baseline, ordinary_database_url=ordinary)
    assert error.value.code == "ISOLATED_IMPORT_DATABASE_IDENTITY"
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("ordinary", [
    "postgresql://other:secret@different:5432/other?host=localhost&dbname=test&options=-csearch_path%3Dp2_test_schema",
    "postgresql://other:secret@localhost/test?hostaddr=127.0.0.1&options=-csearch_path%3Dp2_test_schema",
    "postgresql://other:secret@localhost/test?service=local&options=-csearch_path%3Dp2_test_schema",
    "postgresql://other:secret@localhost/test?servicefile=local&options=-csearch_path%3Dp2_test_schema",
    "postgresql://other:secret@localhost/test?port=5432&port=5433&options=-csearch_path%3Dp2_test_schema",
    "postgresql://other:secret@localhost,elsewhere/test?options=-csearch_path%3Dp2_test_schema",
    "postgresql://other:secret@localhost/test?options=-c%20search_path%3Dp2_test_schema%2Cpublic",
    "postgresql://other:secret@localhost/test?options=-c%20statement_timeout%3D5000",
    "postgresql://other:secret@localhost/test?dbname=postgresql%3A%2F%2Felsewhere%2Fother",
])
@pytest.mark.parametrize("construction", ["env", "direct"])
def test_ordinary_database_indirect_or_ambiguous_identity_rejected(ordinary, construction):
    values = _values()
    baseline = IsolatedImportSettings.from_env("api", values)
    values["DATABASE_URL"] = ordinary
    with pytest.raises(IsolatedImportConfigurationError) as error:
        if construction == "env":
            IsolatedImportSettings.from_env("api", values)
        else:
            replace(baseline, ordinary_database_url=ordinary)
    assert error.value.code in {"DATABASE_URL", "ISOLATED_IMPORT_DATABASE_IDENTITY"}
    assert "secret" not in str(error.value)


def test_direct_construction_rejects_wrong_types_with_sanitized_error():
    settings = IsolatedImportSettings.from_env("api", _values())
    for changes in ({"rag_profile": None}, {"qdrant_url": 123},
                    {"database_url": 123}, {"ordinary_database_url": 123},
                    {"ordinary_database_url": 0}, {"ordinary_database_url": False},
                    {"trusted_user_ids": frozenset({1, str(uuid4())})}):
        with pytest.raises(IsolatedImportConfigurationError) as error:
            replace(settings, **changes)
        assert "SECRET_MARKER" not in str(error.value)


def test_half_open_pool_is_closed_without_provider_construction(monkeypatch):
    import app.isolated_import_runtime as module
    closed = []

    class Pool:
        def close(self):
            closed.append("pool")

    class HalfOpen:
        def __init__(self, *_args, **_kwargs):
            self._opened = False
            self._pool = Pool()

        def open(self):
            raise RuntimeError("SECRET_MARKER")

        def close(self):
            pass

    monkeypatch.setattr(module, "PostgresDatabase", HalfOpen)
    with pytest.raises(IsolatedImportConfigurationError) as error:
        open_isolated_runtime(IsolatedImportSettings.from_env("api", _values()))
    assert closed == ["pool"]
    assert "SECRET_MARKER" not in str(error.value)


def test_close_attempts_all_owned_resources_after_one_failure():
    closed = []
    def close(name, fail=False):
        closed.append(name)
        if fail:
            raise RuntimeError("SECRET_MARKER")
    database = SimpleNamespace(close=lambda: close("database"))
    runtime = IsolatedImportRuntime(IsolatedImportSettings.from_env("api", _values()), database, None)
    runtime._raw = SimpleNamespace(client=SimpleNamespace(close=lambda: close("qdrant", True)))
    runtime._store = SimpleNamespace(client=SimpleNamespace(close=lambda: close("s3")))
    runtime.close()
    runtime.close()
    assert closed == ["qdrant", "s3", "database"]


def test_concurrent_lazy_clients_have_one_owner_and_close_fences_creation(monkeypatch):
    import boto3
    import app.isolated_import_runtime as module
    started = Event()
    release = Event()
    created = []
    closed = []

    class Client:
        def __init__(self, label):
            self.label = label
            created.append(label)

        def close(self):
            closed.append(self.label)

    def s3_client(*_args, **_kwargs):
        started.set()
        assert release.wait(5)
        return Client("s3")

    def qdrant_store(**_kwargs):
        return SimpleNamespace(client=Client("qdrant"))

    monkeypatch.setattr(boto3, "client", s3_client)
    monkeypatch.setattr(module, "QdrantVectorStore", qdrant_store)
    database = SimpleNamespace(close=lambda: closed.append("database"))
    runtime = IsolatedImportRuntime(IsolatedImportSettings.from_env("api", _values()), database, None)
    with ThreadPoolExecutor(max_workers=3) as workers:
        first = workers.submit(runtime._providers)
        assert started.wait(5)
        second = workers.submit(runtime._providers)
        release.set()
        assert first.result(timeout=5) == second.result(timeout=5)
        workers.submit(runtime.close).result(timeout=5)
    assert created == ["s3", "qdrant"]
    assert closed == ["qdrant", "s3", "database"]
    with pytest.raises(RuntimeError, match="isolated_runtime_closed"):
        runtime._providers()


def test_close_waits_for_inflight_lazy_creation_without_leaking(monkeypatch):
    import boto3
    import app.isolated_import_runtime as module
    entered = Event()
    release = Event()
    close_started = Event()
    closed = []

    class Client:
        def __init__(self, name):
            self.name = name

        def close(self):
            closed.append(self.name)

    def s3_client(*_args, **_kwargs):
        entered.set()
        assert release.wait(5)
        return Client("s3")

    monkeypatch.setattr(boto3, "client", s3_client)
    monkeypatch.setattr(module, "QdrantVectorStore",
                        lambda **_kwargs: SimpleNamespace(client=Client("qdrant")))
    runtime = IsolatedImportRuntime(IsolatedImportSettings.from_env("api", _values()),
                                    SimpleNamespace(close=lambda: closed.append("database")), None)
    with ThreadPoolExecutor(max_workers=2) as workers:
        opening = workers.submit(runtime._providers)
        assert entered.wait(5)
        def closing():
            close_started.set()
            runtime.close()
        closing_future = workers.submit(closing)
        assert close_started.wait(5)
        release.set()
        opening.result(timeout=5)
        closing_future.result(timeout=5)
    assert closed == ["qdrant", "s3", "database"]
    with pytest.raises(RuntimeError, match="isolated_runtime_closed"):
        runtime._providers()


@pytest.mark.parametrize("change", [
    {"migration_count": 0}, {"migration_head": "other-head"},
    {"migration_count": 2}, {"schema_name": "public"},
    {"search_path": "p2_test_schema,public"}, {"missing_relation": True},
])
def test_schema_identity_fails_hard_while_transient_ping_fails_closed(monkeypatch, change):
    import app.isolated_import_runtime as module
    settings = IsolatedImportSettings.from_env("api", _values())

    class Cursor:
        def __init__(self, database):
            self.database = database
            self.relations = False

        def execute(self, query, _params=None):
            self.relations = "to_regclass" in query
            return self

        def fetchall(self):
            return [{"present": not (self.database.change.get("missing_relation") and index == 0)}
                    for index in range(24)]

        def fetchone(self):
            return {"database_name": "test", "schema_name": "p2_test_schema",
                    "search_path": "p2_test_schema", "migration_count": 1,
                    "migration_head": "20261007_16"} | {
                        key: value for key, value in self.database.change.items()
                        if key != "missing_relation"}

    class Database:
        def __init__(self):
            self.change = {}
            self._opened = False
            self._pool = SimpleNamespace(close=lambda: None)
            self.closed = 0
            self.ping_ok = True

        def open(self):
            self._opened = True

        def close(self):
            self.closed += 1
            self._opened = False

        def ping(self):
            if not self.ping_ok:
                raise OSError("SECRET_MARKER")
            return True

        @contextmanager
        def transaction(self):
            yield Cursor(self)

    database = Database()
    monkeypatch.setattr(module, "PostgresDatabase", lambda *_args, **_kwargs: database)
    runtime = open_isolated_runtime(settings)
    assert runtime.recovery_ready() is True
    database.change = change
    with pytest.raises(IsolatedImportConfigurationError) as error:
        runtime.recovery_ready()
    assert error.value.code == "ISOLATED_IMPORT_DATABASE_IDENTITY"
    assert "SECRET_MARKER" not in str(error.value)
    database.ping_ok = False
    assert runtime.recovery_ready() is False
    runtime.close()
    assert database.closed == 1
    database.ping_ok = True
    with pytest.raises(IsolatedImportConfigurationError):
        open_isolated_runtime(settings)
    assert database.closed == 2


@pytest.fixture
def real_isolated_resources(monkeypatch):
    """Create only generated names; restore genuine ordinary DATABASE_URL after migration."""
    base = os.environ.get("POSTGRES_TEST_URL")
    endpoint = os.environ.get("S3_TEST_ENDPOINT")
    qdrant_url = os.environ.get("GENERATION_QDRANT_TEST_URL")
    access = os.environ.get("S3_TEST_ACCESS_KEY")
    secret = os.environ.get("S3_TEST_SECRET_KEY")
    if not all((base, endpoint, qdrant_url, access, secret)):
        pytest.skip("Dedicated PostgreSQL, S3 and target Qdrant test endpoints are required")

    import boto3
    import psycopg
    from alembic import command
    from alembic.config import Config
    from botocore.config import Config as BotoConfig
    from psycopg import sql
    from sqlalchemy.engine import make_url

    from app.history import EMPTY_HISTORY
    from app.postgres import PostgresDatabase
    from app.postgres_coordination import PostgresUserMutationCoordinator
    from app.postgres_snapshots import PostgresSnapshotRepository
    from app.postgres_vector_generations import PostgresVectorGenerationAuthority, VectorScope
    from app.vector_generation_service import VectorGenerationService
    from hello_agents.memory.storage.vector_store import QdrantVectorStore
    from tests.integration.test_published_episode_reads import _publish

    genuine_ordinary_url = os.environ.get("DATABASE_URL")
    token = uuid4().hex
    schema = "p2_runtime_" + token
    bucket = "p2-runtime-" + token
    rag_base = "p2_rag_" + token
    episode_base = "p2_episode_" + token
    assert len({schema, bucket, rag_base, episode_base}) == 4
    assert schema != "cutover_combined_refs_20260930_0f7592dc3978"
    assert bucket != "cutover-paired-bf20-69fcab0f0cbd4300a0545f2008c15779"
    assert qdrant_url != os.environ.get("QDRANT_SOURCE_TEST_URL", "nonexistent-protected-source")
    rag_profile = EmbeddingProfile("simple", "", "deterministic", "v1", 4)
    episode_profile = EmbeddingProfile("test-explicit", "", "episode-test", "v1", 4)
    rag_identity = IndexIdentity("qdrant", rag_base, rag_profile)
    episode_identity = IndexIdentity("qdrant", episode_base, episode_profile)
    with psycopg.connect(base, autocommit=True) as admin:
        assert admin.execute("select to_regnamespace(%s)", (schema,)).fetchone()[0] is None
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
    migrated_url = make_url(base).set(drivername="postgresql+psycopg",
                                      query={"options": f"-csearch_path={schema}"}).render_as_string(hide_password=False)
    client = None
    raw = None
    db = None
    bucket_created = False
    collections = []
    try:
        with monkeypatch.context() as migration_env:
            migration_env.setenv("DATABASE_URL", migrated_url)
            command.upgrade(Config("alembic.ini"), "head")
        dsn = migrated_url.replace("postgresql+psycopg://", "postgresql://")
        assert os.environ.get("DATABASE_URL") == genuine_ordinary_url
        client = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1",
                              aws_access_key_id=access, aws_secret_access_key=secret,
                              config=BotoConfig(s3={"addressing_style": "path"}))
        client.create_bucket(Bucket=bucket)
        bucket_created = True
        client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
        raw = QdrantVectorStore(url=qdrant_url, retry_delays=())
        raw.ensure_collection(rag_identity.physical_collection, 4)
        collections.append(rag_identity.physical_collection)
        raw.ensure_collection(episode_identity.physical_collection, 4)
        collections.append(episode_identity.physical_collection)
        db = PostgresDatabase(dsn, min_size=1, max_size=3)
        db.open()
        users = (str(uuid4()), str(uuid4()))
        with db.transaction() as cursor:
            for user in users:
                cursor.execute("insert into users values (%s,%s,%s,%s,%s,%s,%s)",
                               (user, user, user, "hash", "active", "now", "now"))
        authority = PostgresVectorGenerationAuthority(db)
        vectors = VectorGenerationService(authority, raw)
        snapshots = PostgresSnapshotRepository(db)
        rag_scopes = []
        episode_scopes = []
        for user in users:
            rag_scope = VectorScope(user, "rag", f"pdf_{user}", rag_identity)
            episode_scope = VectorScope(user, "episode", "episodes", episode_identity)
            rag_scopes.append(rag_scope)
            episode_scopes.append(episode_scope)
            coordinator = PostgresUserMutationCoordinator(db)
            lease = coordinator.acquire(user, "p2-baseline", lease_seconds=120)
            assert lease is not None
            try:
                def publish_history(cursor):
                    snapshots.compare_and_swap_in_transaction(
                        cursor, user, "history", dict(EMPTY_HISTORY), expected_version=0)
                vectors.publish_complete(rag_scope, lease, authority.read_head(rag_scope), [],
                                         domain_publish=publish_history, snapshot_version=1)
            finally:
                coordinator.release(lease)
            _publish(db, vectors, episode_scope, [])
        values = _values()
        values.update({
            "ISOLATED_IMPORT_DATABASE_URL": dsn,
            "ISOLATED_IMPORT_SCHEMA": schema,
            "ISOLATED_IMPORT_S3_ENDPOINT_URL": endpoint,
            "ISOLATED_IMPORT_S3_BUCKET": bucket,
            "ISOLATED_IMPORT_QDRANT_URL": qdrant_url,
            "ISOLATED_IMPORT_RAG_COLLECTION": rag_base,
            "ISOLATED_IMPORT_EPISODE_COLLECTION": episode_base,
            "ISOLATED_IMPORT_RAG_PROFILE_JSON": json.dumps(asdict(rag_profile)),
            "ISOLATED_IMPORT_EPISODE_PROFILE_JSON": json.dumps(asdict(episode_profile)),
            "ISOLATED_IMPORT_TRUSTED_USER_IDS": ",".join(users),
            "S3_ACCESS_KEY_ID": access,
            "S3_SECRET_ACCESS_KEY": secret,
        })
        values.pop("QDRANT_API_KEY", None)
        if genuine_ordinary_url:
            values["DATABASE_URL"] = genuine_ordinary_url
        else:
            values.pop("DATABASE_URL", None)
        assert values.get("DATABASE_URL") != dsn
        settings = IsolatedImportSettings.from_env("api", values)
        yield settings, db, raw, client, users, rag_scopes, episode_scopes
    finally:
        try:
            if db is not None:
                db.close()
            if raw is not None:
                for collection in collections:
                    raw.client.delete_collection(collection)
                closer = getattr(raw.client, "close", None)
                if callable(closer):
                    closer()
            if client is not None and bucket_created:
                pages = client.get_paginator("list_object_versions").paginate(Bucket=bucket)
                for page in pages:
                    versions = page.get("Versions", []) + page.get("DeleteMarkers", [])
                    if versions:
                        client.delete_objects(Bucket=bucket, Delete={"Objects": [
                            {"Key": item["Key"], "VersionId": item["VersionId"]} for item in versions]})
                client.delete_bucket(Bucket=bucket)
            if client is not None:
                client.close()
        finally:
            with psycopg.connect(base, autocommit=True) as admin:
                admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))


def _synthetic(settings):
    # Contract-only synthetic report; it does not attest a real embedding provider.
    return ProviderReadiness(True, settings.rag_profile, settings.episode_profile)


def _publish_nonempty_history(resources):
    from app.import_document_publication import ImportDocumentPublicationService
    from app.object_store import S3ObjectStore
    from app.postgres_history_document_witnesses import (
        PostgresHistoryDocumentWitnessRepository, document_evidence,
    )
    from app.postgres_snapshots import PostgresSnapshotRepository
    from app.postgres_vector_generations import PostgresVectorGenerationAuthority
    from app.vector_generation_service import VectorGenerationService
    from tests.integration.test_import_document_publication import _publish_strict_legacy_document

    settings, db, raw, client, users, rag_scopes, _ = resources
    store = S3ObjectStore(client, settings.s3_bucket)
    vectors = VectorGenerationService(PostgresVectorGenerationAuthority(db), raw)
    snapshots = PostgresSnapshotRepository(db)
    service = ImportDocumentPublicationService(db, store, vectors)
    fixture_tuple = (service, db, store, vectors, snapshots, rag_scopes[0], users[0], users[1])
    document_id = _publish_strict_legacy_document(fixture_tuple)
    witness = PostgresHistoryDocumentWitnessRepository(db, store)
    pairing = witness.read_current(rag_scopes[0])
    history = snapshots.read(users[0], "history")
    evidence = document_evidence(users[0], history.data)
    with db.transaction() as cursor:
        cursor.execute("select 1")
        witness.insert(cursor, rag_scopes[0], pairing, evidence)
    assert witness.read_current(rag_scopes[0]).has_witness
    return document_id


def _resource_inventory(settings, db, raw, client):
    with db.transaction() as cursor:
        tables = tuple(row["tablename"] for row in cursor.execute(
            "select tablename from pg_tables where schemaname=%s order by tablename",
            (settings.schema,)).fetchall())
    collections = tuple(sorted(item.name for item in raw.client.get_collections().collections))
    versions = []
    pages = client.get_paginator("list_object_versions").paginate(Bucket=settings.s3_bucket)
    for page in pages:
        versions.extend((item["Key"], item["VersionId"])
                        for item in page.get("Versions", []) + page.get("DeleteMarkers", []))
    return tables, collections, tuple(sorted(versions))


def test_real_nonempty_history_witness_reads_pinned_object_without_writes(real_isolated_resources):
    settings, db, raw, client, users, _, _ = real_isolated_resources
    document_id = _publish_nonempty_history(real_isolated_resources)
    assert document_id
    before = _resource_inventory(settings, db, raw, client)
    with open_isolated_runtime(settings, provider_probe=lambda: _synthetic(settings)) as runtime:
        assert runtime.recovery_ready() is True
        assert runtime.ordinary_ready(users[0]) is True
        assert runtime.ordinary_ready(users[1]) is True
    after = _resource_inventory(settings, db, raw, client)
    assert before == after


def test_real_missing_pinned_version_and_final_reference_or_receipt_mismatch(
        real_isolated_resources, monkeypatch):
    import app.isolated_import_runtime as module
    settings, db, _, client, users, _, _ = real_isolated_resources
    document_id = _publish_nonempty_history(real_isolated_resources)
    with open_isolated_runtime(settings, provider_probe=lambda: _synthetic(settings)) as runtime:
        assert runtime.ordinary_ready(users[0]) is True
        actual_capture = module._capture_authority
        original = actual_capture(runtime.database, users[0])
        for kind in ("document_objects", "witnesses", "receipts"):
            invalid = deepcopy(original)
            if kind == "document_objects":
                invalid[kind] = []
            elif kind == "witnesses":
                invalid[kind][0]["documents_sha256"] = "0" * 64
            else:
                rag_receipt = next(row for row in invalid[kind] if row["vector_kind"] == "rag")
                rag_receipt["content_digest"] = "0" * 64
            monkeypatch.setattr(module, "_capture_authority",
                                lambda *_args, evidence=invalid: deepcopy(evidence))
            # Public History/S3 and episode proofs see the valid live baseline;
            # equal invalid authority captures must still be rejected.
            assert runtime.ordinary_ready(users[0]) is False
        monkeypatch.setattr(module, "_capture_authority", actual_capture)
        with db.transaction() as cursor:
            ref = cursor.execute("select object_key,version_id from document_objects "
                                 "where user_id=%s and document_id=%s",
                                 (users[0], document_id)).fetchone()
        client.delete_object(Bucket=settings.s3_bucket, Key=ref["object_key"],
                             VersionId=ref["version_id"])
        assert runtime.recovery_ready() is True
        assert runtime.ordinary_ready(users[0]) is False


def test_real_two_user_baselines_and_separate_role_pools(real_isolated_resources):
    settings, db, raw, client, users, rag_scopes, episode_scopes = real_isolated_resources
    with open_isolated_runtime(settings) as no_provider:
        assert no_provider.recovery_ready() is True
        assert all(no_provider.ordinary_ready(user) is False for user in users)
    api = open_isolated_runtime(settings, provider_probe=lambda: _synthetic(settings))
    second = open_isolated_runtime(settings, provider_probe=lambda: _synthetic(settings))
    worker = open_isolated_runtime(replace(settings, role="worker"),
                                   provider_probe=lambda: _synthetic(settings))
    try:
        assert len({id(api.database._pool), id(second.database._pool), id(worker.database._pool)}) == 3
        assert all(api.ordinary_ready(user) is True for user in users)
        assert api.ordinary_ready(str(uuid4())) is False
        assert second.recovery_ready() is True and worker.recovery_ready() is True
        with db.transaction() as cursor:
            cursor.execute("update users set status='disabled' where id=%s", (users[0],))
        assert api.ordinary_ready(users[0]) is False
        assert api.ordinary_ready(users[1]) is True
    finally:
        api.close()
        api.close()
        second.close()
        worker.close()


def test_real_provider_and_storage_fail_closed(real_isolated_resources, monkeypatch):
    settings, _, _, _, users, _, _ = real_isolated_resources
    for provider in (None, lambda: None,
                     lambda: ProviderReadiness(False, settings.rag_profile, settings.episode_profile),
                     lambda: ProviderReadiness(True, settings.episode_profile, settings.rag_profile),
                     lambda: (_ for _ in ()).throw(RuntimeError("SECRET_MARKER"))):
        with open_isolated_runtime(settings, provider_probe=provider) as runtime:
            assert runtime.recovery_ready() is True
            assert runtime.ordinary_ready(users[0]) is False
    with open_isolated_runtime(settings, provider_probe=lambda: _synthetic(settings)) as runtime:
        assert runtime.ordinary_ready(users[0]) is True
        store, raw = runtime._providers()
        monkeypatch.setattr(store, "check_ready", lambda: (_ for _ in ()).throw(OSError("down")))
        assert runtime.recovery_ready() is True and runtime.ordinary_ready(users[0]) is False
        monkeypatch.undo()
        monkeypatch.setattr(raw, "require_collection", lambda *_args: (_ for _ in ()).throw(OSError("down")))
        assert runtime.recovery_ready() is True and runtime.ordinary_ready(users[0]) is False
        monkeypatch.undo()
        monkeypatch.setattr(runtime.database, "ping", lambda: (_ for _ in ()).throw(OSError("down")))
        assert runtime.recovery_ready() is False and runtime.ordinary_ready(users[0]) is False


def test_real_missing_baseline_and_wrong_profile_fail_closed(real_isolated_resources):
    settings, db, _, _, users, _, _ = real_isolated_resources
    altered = replace(settings, rag_profile=EmbeddingProfile("simple", "", "different", "v1", 4))
    with open_isolated_runtime(altered, provider_probe=lambda: _synthetic(altered)) as runtime:
        assert runtime.recovery_ready() is True
        assert runtime.ordinary_ready(users[0]) is False
    with db.transaction() as cursor:
        cursor.execute("delete from user_snapshots where user_id=%s and kind='history'", (users[0],))
        cursor.execute("delete from user_snapshots where user_id=%s and kind='memory'", (users[1],))
    with open_isolated_runtime(settings, provider_probe=lambda: _synthetic(settings)) as runtime:
        assert runtime.recovery_ready() is True
        assert all(runtime.ordinary_ready(user) is False for user in users)


@pytest.mark.parametrize("fault", ["extra_head", "missing_relation"])
def test_real_durable_schema_drift_is_sanitized_hard_error(real_isolated_resources, fault):
    settings, db, _, _, users, _, _ = real_isolated_resources
    with open_isolated_runtime(settings) as runtime:
        assert runtime.recovery_ready() is True
        with db.transaction() as cursor:
            if fault == "extra_head":
                cursor.execute("insert into alembic_version(version_num) values('other_head')")
            else:
                cursor.execute("drop table import_publication_recovery_schedule cascade")
        with pytest.raises(IsolatedImportConfigurationError) as error:
            runtime.recovery_ready()
        assert error.value.code == "ISOLATED_IMPORT_DATABASE_IDENTITY"
        assert settings.database_url not in str(error.value)
        with pytest.raises(IsolatedImportConfigurationError):
            runtime.ordinary_ready(users[0])
    with pytest.raises(IsolatedImportConfigurationError):
        open_isolated_runtime(settings)


def test_real_final_authority_rejects_aba_and_unchanged_version_row_loss(real_isolated_resources, monkeypatch):
    import app.isolated_import_runtime as module
    from app.published_episode_reads import PublishedEpisodeReadOperation
    from tests.integration.test_published_episode_reads import _item, _publish
    settings, db, raw, _, users, _, episode_scopes = real_isolated_resources
    item = _item(users[0], "negative", logical_id="negative-episode")
    item["importance"] = -0.5
    service = __import__("app.vector_generation_service", fromlist=["VectorGenerationService"]).VectorGenerationService(
        __import__("app.postgres_vector_generations", fromlist=["PostgresVectorGenerationAuthority"]).PostgresVectorGenerationAuthority(db), raw)
    _publish(db, service, episode_scopes[0], [item])
    with open_isolated_runtime(settings, provider_probe=lambda: _synthetic(settings)) as runtime:
        assert runtime.ordinary_ready(users[0]) is True
        original_capture = module._capture_authority
        invalid = deepcopy(original_capture(runtime.database, users[0]))
        invalid["memory_documents"] = []
        calls = []
        def aba_capture(database, user_id):
            calls.append(user_id)
            return deepcopy(invalid)
        monkeypatch.setattr(module, "_capture_authority", aba_capture)
        assert runtime.ordinary_ready(users[0]) is False
        assert len(calls) >= 2
        monkeypatch.setattr(module, "_capture_authority", original_capture)
        original_scroll = PublishedEpisodeReadOperation.scroll
        deleted = []
        def delete_during_scroll(operation, **kwargs):
            result = original_scroll(operation, **kwargs)
            if not deleted:
                with db.transaction() as cursor:
                    cursor.execute("delete from memory_documents where user_id=%s and document_id=%s",
                                   (users[0], item["id"]))
                deleted.append(True)
            return result
        monkeypatch.setattr(PublishedEpisodeReadOperation, "scroll", delete_during_scroll)
        assert runtime.ordinary_ready(users[0]) is False
        assert deleted == [True]
        with db.transaction() as cursor:
            assert cursor.execute("select count(*) as n from memory_documents where user_id=%s", (users[0],)).fetchone()["n"] == 0


@pytest.mark.parametrize("kind", ["rag", "episode"])
@pytest.mark.parametrize("changes,expected", [(1, True), (2, False)])
def test_real_head_change_causes_one_complete_bounded_recheck(
        real_isolated_resources, monkeypatch, kind, changes, expected):
    import app.isolated_import_runtime as module
    from app.postgres_coordination import PostgresUserMutationCoordinator
    from app.postgres_snapshots import PostgresSnapshotRepository
    from app.postgres_vector_generations import PostgresVectorGenerationAuthority
    from app.vector_generation_service import VectorGenerationService
    from tests.integration.test_published_episode_reads import _publish

    settings, db, raw, _, users, rag_scopes, episode_scopes = real_isolated_resources
    vectors = VectorGenerationService(PostgresVectorGenerationAuthority(db), raw)
    snapshots = PostgresSnapshotRepository(db)
    user = users[0]
    provider_calls = []
    original = module._full_episode_points
    changed = []

    def change_after_public_read(authority, user_id, operation):
        points = original(authority, user_id, operation)
        if len(changed) < changes:
            if kind == "episode":
                _publish(db, vectors, episode_scopes[0], [])
            else:
                lease = PostgresUserMutationCoordinator(db).acquire(
                    user, "p2-rag-race", lease_seconds=120)
                assert lease is not None
                history = snapshots.read(user, "history")
                def domain(cursor):
                    snapshots.compare_and_swap_in_transaction(
                        cursor, user, "history", history.data,
                        expected_version=history.version)
                try:
                    vectors.publish_complete(rag_scopes[0], lease,
                        vectors.authority.read_head(rag_scopes[0]), [],
                        domain_publish=domain, snapshot_version=history.version + 1)
                finally:
                    PostgresUserMutationCoordinator(db).release(lease)
            changed.append(True)
        return points

    monkeypatch.setattr(module, "_full_episode_points", change_after_public_read)
    def provider():
        provider_calls.append(True)
        return _synthetic(settings)
    with open_isolated_runtime(settings, provider_probe=provider) as runtime:
        assert runtime.ordinary_ready(user) is expected
        assert changed == [True] * changes
        assert len(provider_calls) == 2


def test_real_sql_invalid_valid_invalid_memory_document_aba(real_isolated_resources, monkeypatch):
    import app.isolated_import_runtime as module
    from app.postgres_vector_generations import PostgresVectorGenerationAuthority
    from app.vector_generation_service import VectorGenerationService
    from tests.integration.test_published_episode_reads import _item, _publish

    settings, db, raw, _, users, _, episode_scopes = real_isolated_resources
    user = users[0]
    item = _item(user, "ABA payload", logical_id="aba-episode")
    item["importance"] = -0.4
    _publish(db, VectorGenerationService(PostgresVectorGenerationAuthority(db), raw),
             episode_scopes[0], [item])
    with db.transaction() as cursor:
        row = cursor.execute("select content,metadata from memory_documents "
                             "where user_id=%s and document_id=%s",
                             (user, item["id"])).fetchone()
        cursor.execute("delete from memory_documents where user_id=%s and document_id=%s",
                       (user, item["id"]))
    original_capture = module._capture_authority
    captures = []
    def capture_with_sql_aba(database, user_id):
        if user_id != user:
            return original_capture(database, user_id)
        if len(captures) == 2:
            with db.transaction() as cursor:
                cursor.execute("delete from memory_documents where user_id=%s and document_id=%s",
                               (user, item["id"]))
        result = original_capture(database, user_id)
        captures.append(deepcopy(result))
        if len(captures) == 1:
            with db.transaction() as cursor:
                cursor.execute("insert into memory_documents(user_id,document_id,content,metadata) "
                               "values(%s,%s,%s,%s)",
                               (user, item["id"], row["content"], row["metadata"]))
        return result

    monkeypatch.setattr(module, "_capture_authority", capture_with_sql_aba)
    with open_isolated_runtime(settings, provider_probe=lambda: _synthetic(settings)) as runtime:
        assert runtime.ordinary_ready(user) is False
    assert len(captures) >= 3
    assert captures[0] == captures[2]
    assert captures[1]["memory_documents"] != captures[0]["memory_documents"]
    with db.transaction() as cursor:
        assert cursor.execute("select count(*) as n from memory_documents where user_id=%s", (user,)).fetchone()["n"] == 0
