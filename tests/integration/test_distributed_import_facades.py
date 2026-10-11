"""Task 03 authenticated facade contract tests."""

from importlib import import_module

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import copy, deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
from types import SimpleNamespace
import threading
import time
from uuid import uuid4

import boto3
import psycopg
from alembic import command
from alembic.config import Config
from botocore.config import Config as BotoConfig
from psycopg import sql
from sqlalchemy.engine import make_url

import pytest

from psycopg.pq import TransactionStatus

from app.distributed_import_sessions import (
    DistributedImportSessions, UntrustedSessionError,
)
from app.distributed_import_service import (
    DistributedImportError, DistributedImportService, PublishedImportDocument,
)
from app.import_publication_evidence import (
    PublicationEvidenceError, UnresolvedPublicationGate,
)
from app.import_uploads import ImportUpload
from app.isolated_import_runtime import (
    IsolatedAdmissionRejected, IsolatedAdmissionStamp,
    IsolatedImportConfigurationError, IsolatedImportRuntime,
)
from app.object_store import ObjectRef, artifact_key
from app.postgres import PostgresDatabase
from app.postgres_auth import PostgresAuthService
from app.postgres_import_artifacts import PostgresImportArtifactService
from app.postgres_coordination import PostgresUserMutationCoordinator
from app.postgres_snapshots import PostgresSnapshotRepository
from app.postgres_vector_generations import (
    PostgresVectorGenerationAuthority, VectorScope,
)
from app.session import InvalidCsrfTokenError, InvalidSessionError
from app.vector_generation_service import VectorGenerationService
from hello_agents.memory.rag.embedding_profile import EmbeddingProfile
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.storage.vector_store import QdrantVectorStore
from tests.integration.test_published_episode_reads import _publish
from tests.test_isolated_import_runtime import _publish_nonempty_history, _synthetic, _values


def test_preimplementation_red_distributed_import_sessions():
    sessions = getattr(import_module("app.distributed_import_sessions"),
                       "DistributedImportSessions")
    for method in ("login", "get_session", "validate_csrf", "logout",
                   "recheck_csrf_in_transaction"):
        assert callable(getattr(sessions, method))


def test_preimplementation_red_distributed_import_service():
    service = getattr(import_module("app.distributed_import_service"),
                      "DistributedImportService")
    for method in ("submit_uploads", "list_batches", "get_batch", "get_task",
                   "read_published_document"):
        assert callable(getattr(service, method))


class _PureDatabase:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _PureCursor:
    connection = SimpleNamespace(info=SimpleNamespace(
        transaction_status=TransactionStatus.INTRANS))

    def execute(self, statement, params=None):
        self.statement = statement
        return self

    def fetchone(self):
        if self.statement == "show transaction_isolation":
            return {"transaction_isolation": "read committed"}
        return None


def _pure_runtime(monkeypatch):
    settings = SimpleNamespace(role="api", trusted_user_ids=frozenset({"user-a", "user-b"}))
    runtime = IsolatedImportRuntime(settings, _PureDatabase(), None)
    monkeypatch.setattr(runtime, "_validated_authority",
                        lambda user_id: {"account": {"status": "active"}, "user": user_id})
    module = import_module("app.isolated_import_runtime")
    monkeypatch.setattr(module, "_capture_authority_cursor",
                        lambda _cursor, user_id: {"account": {"status": "active"},
                                                  "user": user_id})
    return runtime


def test_stamp_original_identity_copy_replace_replay_and_discard(monkeypatch):
    runtime = _pure_runtime(monkeypatch)
    original = runtime.prepare_admission("user-a")
    assert isinstance(original, IsolatedAdmissionStamp)
    assert "user-a" not in repr(original)
    for forged in (IsolatedAdmissionStamp(), copy(original), deepcopy(original),
                   replace(original)):
        with pytest.raises(IsolatedAdmissionRejected):
            runtime.recheck_admission(_PureCursor(), "user-a", forged)
        runtime.discard_admission(forged)
    runtime.recheck_admission(_PureCursor(), "user-a", original)
    with pytest.raises(IsolatedAdmissionRejected):
        runtime.recheck_admission(_PureCursor(), "user-a", original)
    runtime.discard_admission(original)
    runtime.discard_admission(original)
    other = runtime.prepare_admission("user-a")
    with pytest.raises(IsolatedAdmissionRejected):
        runtime.recheck_admission(_PureCursor(), "user-b", other)
    with pytest.raises(IsolatedAdmissionRejected):
        runtime.recheck_admission(_PureCursor(), "user-a", other)
    runtime.close()


def test_stamp_concurrent_consume_and_close_during_prepare(monkeypatch):
    runtime = _pure_runtime(monkeypatch)
    stamp = runtime.prepare_admission("user-a")
    start = threading.Barrier(3)

    def consume():
        start.wait(timeout=3)
        try:
            runtime.recheck_admission(_PureCursor(), "user-a", stamp)
            return True
        except IsolatedAdmissionRejected:
            return False

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(consume) for _ in range(2)]
        start.wait(timeout=3)
        assert sorted(future.result(timeout=3) for future in futures) == [False, True]

    entering = threading.Event()
    release = threading.Event()

    def late_proof(_user_id):
        entering.set()
        assert release.wait(3)
        return {"account": {"status": "active"}}

    monkeypatch.setattr(runtime, "_validated_authority", late_proof)
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(runtime.prepare_admission, "user-a")
        assert entering.wait(3)
        runtime.close()
        release.set()
        assert pending.result(timeout=3) is None


def test_stamp_close_during_cursor_capture_refuses_after_equal_proof(monkeypatch):
    runtime = _pure_runtime(monkeypatch)
    stamp = runtime.prepare_admission("user-a")
    module = import_module("app.isolated_import_runtime")
    captured = []

    def close_during_capture(_cursor, user_id):
        captured.append(user_id)
        runtime.close()
        return {"account": {"status": "active"}, "user": user_id}

    monkeypatch.setattr(module, "_capture_authority_cursor", close_during_capture)
    with pytest.raises(IsolatedAdmissionRejected):
        runtime.recheck_admission(_PureCursor(), "user-a", stamp)
    assert captured == ["user-a"] and not runtime._admissions
    with pytest.raises(IsolatedAdmissionRejected):
        runtime.recheck_admission(_PureCursor(), "user-a", stamp)


def test_artifact_callback_after_lock_before_batch_and_default_unchanged(monkeypatch):
    events = []

    class Database:
        def transaction(self):
            class Transaction:
                def __enter__(self):
                    return Cursor()

                def __exit__(self, *_):
                    return False
            return Transaction()

    class Cursor:
        def execute(self, statement, params=None):
            events.append("sql")
            return self

        def fetchone(self):
            return {"now": datetime.now(timezone.utc)}

    service = PostgresImportArtifactService(Database(), SimpleNamespace(bucket="only-test"))
    monkeypatch.setattr(service, "_require_active", lambda _user: events.append("active"))
    monkeypatch.setattr(service, "_require_active_locked",
                        lambda _cursor, _user: events.append("locked"))
    monkeypatch.setattr(service, "_upload_one",
                        lambda *_: (3, ObjectRef("only-test", "0" * 64, 3, "v")))
    service.repository = SimpleNamespace(create_batch_in_transaction=
        lambda *_args, **_kwargs: events.append("batch") or "summary")
    upload = [ImportUpload("one.txt", BytesIO(b"abc"))]
    assert service.submit_uploads("user-a", upload, admission_recheck=
        lambda _cursor, _user: events.append("callback")) == "summary"
    assert events.index("locked") < events.index("callback") < events.index("batch")
    events.clear()
    assert service.submit_uploads("user-a", upload) == "summary"
    assert "callback" not in events


def test_artifact_callback_failure_rolls_back_before_any_batch(monkeypatch):
    state = {"committed": [], "pending": []}

    class Database:
        def transaction(self):
            class Transaction:
                def __enter__(self):
                    state["pending"] = []
                    return Cursor()

                def __exit__(self, error_type, *_):
                    if error_type is None:
                        state["committed"].extend(state["pending"])
                    state["pending"] = []
                    return False
            return Transaction()

    class Cursor:
        def execute(self, *_):
            return self

        def fetchone(self):
            return {"now": datetime.now(timezone.utc)}

    service = PostgresImportArtifactService(Database(), SimpleNamespace(bucket="only-test"))
    monkeypatch.setattr(service, "_require_active", lambda *_: None)
    monkeypatch.setattr(service, "_require_active_locked", lambda *_: None)
    monkeypatch.setattr(service, "_upload_one", lambda *_: (3, ObjectRef("key", "0" * 64, 3, "v")))
    service.repository = SimpleNamespace(create_batch_in_transaction=lambda *_:
        state["pending"].append("batch"))
    with pytest.raises(IsolatedAdmissionRejected):
        service.submit_uploads("user-a", [ImportUpload("one.txt", BytesIO(b"abc"))],
            admission_recheck=lambda *_: (_ for _ in ()).throw(IsolatedAdmissionRejected()))
    assert state == {"committed": [], "pending": []}


def test_facade_discards_stamp_on_all_pre_callback_failures(monkeypatch):
    runtime = _pure_runtime(monkeypatch)
    sessions = DistributedImportSessions(runtime)
    service = DistributedImportService(runtime, sessions)
    monkeypatch.setattr(sessions, "validate_csrf", lambda *_:
                        SimpleNamespace(user_id="user-a"))
    monkeypatch.setattr(runtime, "storage_clients", lambda: (object(), object()))
    issued = []
    original_prepare = runtime.prepare_admission

    def prepare(user_id):
        stamp = original_prepare(user_id)
        issued.append(stamp)
        return stamp

    monkeypatch.setattr(runtime, "prepare_admission", prepare)
    for failure in (ValueError("invalid stream"), OSError("S3 unavailable"),
                    FileNotFoundError("disabled"),
                    UnresolvedPublicationGate("gate"), InvalidSessionError("logout")):
        monkeypatch.setattr(PostgresImportArtifactService, "submit_uploads",
                            lambda *_args, failure=failure, **_kwargs:
                            (_ for _ in ()).throw(failure))
        with pytest.raises(DistributedImportError):
            service.submit_uploads("token", "csrf", [])
        stamp = issued[-1]
        with pytest.raises(IsolatedAdmissionRejected):
            runtime.recheck_admission(_PureCursor(), "user-a", stamp)
    assert not runtime._admissions
    runtime.close()


def test_facade_callback_refusal_discards_unconsumed_stamp(monkeypatch):
    runtime = _pure_runtime(monkeypatch)
    sessions = DistributedImportSessions(runtime)
    service = DistributedImportService(runtime, sessions)
    monkeypatch.setattr(sessions, "validate_csrf", lambda *_:
                        SimpleNamespace(user_id="user-a"))
    monkeypatch.setattr(runtime, "storage_clients", lambda: (object(), object()))
    issued = []
    original_prepare = runtime.prepare_admission

    def prepare(user_id):
        stamp = original_prepare(user_id)
        issued.append(stamp)
        return stamp

    def invoke_callback(_self, user_id, _uploads, *, admission_recheck):
        admission_recheck(_PureCursor(), user_id)

    monkeypatch.setattr(runtime, "prepare_admission", prepare)
    monkeypatch.setattr(PostgresImportArtifactService, "submit_uploads", invoke_callback)
    monkeypatch.setattr(sessions, "recheck_csrf_in_transaction", lambda *_args,
        **_kwargs: (_ for _ in ()).throw(InvalidSessionError("revoked")))
    with pytest.raises(DistributedImportError) as refused:
        service.submit_uploads("token", "csrf", [])
    assert refused.value.status_code == 401
    with pytest.raises(IsolatedAdmissionRejected):
        runtime.recheck_admission(_PureCursor(), "user-a", issued[0])
    assert not runtime._admissions
    runtime.close()


def test_session_and_csrf_refusal_precede_upload_stream_or_storage(monkeypatch):
    runtime = _pure_runtime(monkeypatch)
    sessions = DistributedImportSessions(runtime)
    service = DistributedImportService(runtime, sessions)

    class Bomb:
        def read(self, *_):
            raise AssertionError("stream was touched")

    upload = [ImportUpload("one.txt", Bomb())]
    for error, status in ((InvalidSessionError("invalid"), 401),
                          (InvalidCsrfTokenError("invalid"), 403),
                          (UntrustedSessionError(), 403)):
        monkeypatch.setattr(sessions, "validate_csrf",
                            lambda *_args, error=error: (_ for _ in ()).throw(error))
        with pytest.raises(DistributedImportError) as caught:
            service.submit_uploads("token", "csrf", upload)
        assert caught.value.status_code == status
        assert set(vars(caught.value)) == {"code", "status_code", "retryable"}
    runtime.close()


def test_preparation_identity_drift_is_sanitized_dependency_503(monkeypatch):
    runtime = _pure_runtime(monkeypatch)
    sessions = DistributedImportSessions(runtime)
    service = DistributedImportService(runtime, sessions)
    monkeypatch.setattr(sessions, "validate_csrf", lambda *_:
                        SimpleNamespace(user_id="user-a"))
    monkeypatch.setattr(runtime, "prepare_admission", lambda *_:
        (_ for _ in ()).throw(IsolatedImportConfigurationError("private identity")))
    with pytest.raises(DistributedImportError) as refused:
        service.submit_uploads("token", "csrf", [ImportUpload("one.txt", BytesIO(b"x"))])
    assert (refused.value.status_code, refused.value.code) == \
        (503, "dependency_unavailable")
    assert "private identity" not in repr(refused.value)
    runtime.close()


def test_nonvalidation_storage_value_error_is_dependency_503(monkeypatch):
    runtime = _pure_runtime(monkeypatch)
    sessions = DistributedImportSessions(runtime)
    service = DistributedImportService(runtime, sessions)
    monkeypatch.setattr(sessions, "validate_csrf", lambda *_:
                        SimpleNamespace(user_id="user-a"))
    monkeypatch.setattr(runtime, "storage_clients", lambda: (_ for _ in ()).throw(
        ValueError("private store configuration")))
    with pytest.raises(DistributedImportError) as refused:
        service.submit_uploads("token", "csrf", [ImportUpload("one.txt", BytesIO(b"x"))])
    assert (refused.value.status_code, refused.value.code) == \
        (503, "dependency_unavailable")
    assert "private store configuration" not in repr(refused.value)
    assert not runtime._admissions
    runtime.close()


@pytest.mark.parametrize("upload", [
    ImportUpload("bad.exe", BytesIO(b"x")),
    ImportUpload("", BytesIO(b"x")),
    ImportUpload("good.txt", SimpleNamespace(read=lambda *_: "not binary")),
])
def test_known_malformed_upload_validation_is_400(monkeypatch, upload):
    runtime = _pure_runtime(monkeypatch)
    sessions = DistributedImportSessions(runtime)
    service = DistributedImportService(runtime, sessions)
    monkeypatch.setattr(sessions, "validate_csrf", lambda *_:
                        SimpleNamespace(user_id="user-a"))
    monkeypatch.setattr(runtime, "storage_clients", lambda: (object(), object()))
    monkeypatch.setattr(PostgresImportArtifactService, "_require_active", lambda *_: None)
    with pytest.raises(DistributedImportError) as refused:
        service.submit_uploads("token", "csrf", [upload])
    assert (refused.value.status_code, refused.value.code) == (400, "invalid_upload")
    assert not runtime._admissions
    runtime.close()


def test_typed_gate_only_maps_matching_fresh_evidence_to_409(monkeypatch):
    runtime = _pure_runtime(monkeypatch)
    sessions = DistributedImportSessions(runtime)
    service = DistributedImportService(runtime, sessions)
    monkeypatch.setattr(sessions, "validate_csrf", lambda *_: SimpleNamespace(user_id="user-a"))
    monkeypatch.setattr(runtime, "prepare_admission", lambda *_: IsolatedAdmissionStamp())
    monkeypatch.setattr(runtime, "storage_clients", lambda: (object(), object()))
    monkeypatch.setattr(runtime, "discard_admission", lambda *_: None)

    def fail(_self, *_args, **_kwargs):
        raise UnresolvedPublicationGate("secret raw detail")

    monkeypatch.setattr(PostgresImportArtifactService, "submit_uploads", fail)
    for current, expected in ((True, 409), (False, 503)):
        monkeypatch.setattr(service, "_gate_current", lambda _user, value=current: value)
        with pytest.raises(DistributedImportError) as caught:
            service.submit_uploads("token", "csrf", [])
        assert caught.value.status_code == expected
        assert "secret" not in repr(caught.value)

    def non_gate(_self, *_args, **_kwargs):
        raise PublicationEvidenceError("secret raw detail")

    monkeypatch.setattr(PostgresImportArtifactService, "submit_uploads", non_gate)
    with pytest.raises(DistributedImportError) as caught:
        service.submit_uploads("token", "csrf", [])
    assert caught.value.status_code == 503
    runtime.close()


def test_published_dto_hides_content_and_constructor_rejects_foreign_runtime(monkeypatch):
    result = PublishedImportDocument("doc", "safe.txt", ".txt", 6, b"secret")
    assert "secret" not in repr(result)
    runtime = _pure_runtime(monkeypatch)
    sessions = DistributedImportSessions(runtime)
    another = _pure_runtime(monkeypatch)
    with pytest.raises(ValueError, match="isolated_api_composition_required"):
        DistributedImportService(another, sessions)
    runtime.close()
    another.close()


@pytest.fixture
def real_p3_resources(monkeypatch):
    keys = ("POSTGRES_TEST_URL", "S3_TEST_ENDPOINT", "S3_TEST_ACCESS_KEY",
            "S3_TEST_SECRET_KEY", "GENERATION_QDRANT_TEST_URL")
    values = tuple(os.environ.get(key) for key in keys)
    if not all(values):
        pytest.skip("Dedicated PostgreSQL, S3 and target Qdrant test endpoints are required")
    base, endpoint, access, secret, qdrant_url = values
    token = uuid4().hex
    schema = "p3_facade_" + token
    bucket = "p3-facade-" + token
    rag_base = "p3_rag_" + token
    episode_base = "p3_episode_" + token
    assert schema != "cutover_combined_refs_20260930_0f7592dc3978"
    assert bucket != "cutover-paired-bf20-69fcab0f0cbd4300a0545f2008c15779"
    assert qdrant_url != os.environ.get("QDRANT_SOURCE_TEST_URL", "nonexistent-protected-source")
    rag_profile = EmbeddingProfile("simple", "", "deterministic", "v1", 4)
    episode_profile = EmbeddingProfile("test-explicit", "", "episode-test", "v1", 4)
    rag_identity = IndexIdentity("qdrant", rag_base, rag_profile)
    episode_identity = IndexIdentity("qdrant", episode_base, episode_profile)
    receipt_path = (Path(__file__).resolve().parents[2] / ".runtime" /
                    "isolated-import-runtime" / "p3-generated-resources-n1.jsonl")
    with psycopg.connect(base, autocommit=True) as admin:
        assert admin.execute("select to_regnamespace(%s)", (schema,)).fetchone()[0] is None
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
    migrated_url = make_url(base).set(drivername="postgresql+psycopg",
        query={"options": f"-csearch_path={schema}"}).render_as_string(hide_password=False)
    db = None
    s3 = None
    raw = None
    bucket_created = False
    collections = []
    runtimes = []
    try:
        with monkeypatch.context() as migration_env:
            migration_env.setenv("DATABASE_URL", migrated_url)
            command.upgrade(Config("alembic.ini"), "head")
        dsn = migrated_url.replace("postgresql+psycopg://", "postgresql://")
        s3 = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1",
            aws_access_key_id=access, aws_secret_access_key=secret,
            config=BotoConfig(s3={"addressing_style": "path"}))
        s3.create_bucket(Bucket=bucket)
        bucket_created = True
        s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
        raw = QdrantVectorStore(url=qdrant_url, retry_delays=())
        for identity in (rag_identity, episode_identity):
            raw.ensure_collection(identity.physical_collection, 4)
            collections.append(identity.physical_collection)
        with receipt_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"schema": schema, "bucket": bucket,
                "collections": collections, "phase": "created"}) + "\n")
        db = PostgresDatabase(dsn, min_size=1, max_size=4)
        db.open()
        auth = PostgresAuthService(db)
        names = ("p3a" + token[:8], "p3b" + token[:8], "p3x" + token[:8])
        users = tuple(auth.register(name, "correct-password").id for name in names)
        vectors = VectorGenerationService(PostgresVectorGenerationAuthority(db), raw)
        snapshots = PostgresSnapshotRepository(db)
        rag_scopes = []
        episode_scopes = []
        for user_id in users[:2]:
            rag_scope = VectorScope(user_id, "rag", f"pdf_{user_id}", rag_identity)
            episode_scope = VectorScope(user_id, "episode", "episodes", episode_identity)
            rag_scopes.append(rag_scope)
            episode_scopes.append(episode_scope)
            coordinator = PostgresUserMutationCoordinator(db)
            lease = coordinator.acquire(user_id, "p3-baseline", lease_seconds=120)
            assert lease is not None
            try:
                def publish_history(cursor):
                    from app.history import EMPTY_HISTORY
                    snapshots.compare_and_swap_in_transaction(cursor, user_id,
                        "history", dict(EMPTY_HISTORY), expected_version=0)
                vectors.publish_complete(rag_scope, lease,
                    vectors.authority.read_head(rag_scope), [],
                    domain_publish=publish_history, snapshot_version=1)
            finally:
                coordinator.release(lease)
            _publish(db, vectors, episode_scope, [])
        env_values = _values()
        env_values.update({
            "ISOLATED_IMPORT_DATABASE_URL": dsn,
            "ISOLATED_IMPORT_SCHEMA": schema,
            "ISOLATED_IMPORT_S3_ENDPOINT_URL": endpoint,
            "ISOLATED_IMPORT_S3_BUCKET": bucket,
            "ISOLATED_IMPORT_QDRANT_URL": qdrant_url,
            "ISOLATED_IMPORT_RAG_COLLECTION": rag_base,
            "ISOLATED_IMPORT_EPISODE_COLLECTION": episode_base,
            "ISOLATED_IMPORT_RAG_PROFILE_JSON": json.dumps(asdict(rag_profile)),
            "ISOLATED_IMPORT_EPISODE_PROFILE_JSON": json.dumps(asdict(episode_profile)),
            "ISOLATED_IMPORT_TRUSTED_USER_IDS": ",".join(users[:2]),
            "S3_ACCESS_KEY_ID": access,
            "S3_SECRET_ACCESS_KEY": secret,
        })
        env_values.pop("QDRANT_API_KEY", None)
        ordinary_url = os.environ.get("DATABASE_URL")
        if ordinary_url:
            env_values["DATABASE_URL"] = ordinary_url
        else:
            env_values.pop("DATABASE_URL", None)
        from app.isolated_import_runtime import IsolatedImportSettings, open_isolated_runtime
        settings = IsolatedImportSettings.from_env("api", env_values)
        api1 = open_isolated_runtime(settings, provider_probe=lambda: _synthetic(settings))
        runtimes.append(api1)
        api2 = open_isolated_runtime(settings, provider_probe=lambda: _synthetic(settings))
        runtimes.append(api2)
        sessions1 = DistributedImportSessions(api1)
        sessions2 = DistributedImportSessions(api2)
        yield SimpleNamespace(settings=settings, db=db, s3=s3, raw=raw,
            users=users, names=names, rag_scopes=rag_scopes,
            episode_scopes=episode_scopes, api1=api1, api2=api2,
            sessions1=sessions1, sessions2=sessions2,
            service1=DistributedImportService(api1, sessions1),
            service2=DistributedImportService(api2, sessions2))
    finally:
        for runtime in runtimes:
            runtime.close()
        if db is not None:
            db.close()
        if raw is not None:
            for collection in collections:
                raw.client.delete_collection(collection)
            closer = getattr(raw.client, "close", None)
            if callable(closer):
                closer()
        if s3 is not None and bucket_created:
            pages = s3.get_paginator("list_object_versions").paginate(Bucket=bucket)
            for page in pages:
                versions = page.get("Versions", []) + page.get("DeleteMarkers", [])
                if versions:
                    s3.delete_objects(Bucket=bucket, Delete={"Objects": [
                        {"Key": item["Key"], "VersionId": item["VersionId"]}
                        for item in versions]})
            s3.delete_bucket(Bucket=bucket)
        if s3 is not None:
            s3.close()
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))
        with receipt_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"schema": schema, "bucket": bucket,
                "collections": collections, "phase": "removed"}) + "\n")


def _login(resources, user_index=0):
    token = resources.sessions1.login(resources.names[user_index], "correct-password")
    csrf = resources.sessions2.get_session(token).csrf_token
    return token, csrf


def _upload(resources, token, csrf, content=b"fresh source"):
    return resources.service2.submit_uploads(token, csrf,
        [ImportUpload("fresh.txt", BytesIO(content))])


def _counts(resources, user_id):
    with resources.db.transaction() as cursor:
        batch = cursor.execute("select count(*) as n from import_batches where user_id=%s",
                               (user_id,)).fetchone()["n"]
        task = cursor.execute("select count(*) as n from import_tasks where user_id=%s",
                              (user_id,)).fetchone()["n"]
        ref = cursor.execute("select count(*) as n from import_objects where user_id=%s",
                             (user_id,)).fetchone()["n"]
    return batch, task, ref


def test_real_cross_pool_session_allowlist_upload_status_and_logout(real_p3_resources):
    r = real_p3_resources
    assert not hasattr(r.sessions1, "register")
    with pytest.raises(UntrustedSessionError):
        r.sessions1.login(r.names[2], "correct-password")
    token, csrf = _login(r)
    assert r.sessions2.validate_csrf(token, csrf).user_id == r.users[0]
    with pytest.raises(DistributedImportError) as invalid:
        r.service2.submit_uploads(token, "wrong-csrf",
            [ImportUpload("fresh.txt", BytesIO(b"source"))])
    assert invalid.value.status_code == 403 and _counts(r, r.users[0]) == (0, 0, 0)
    batch = _upload(r, token, csrf)
    assert batch.user_id == r.users[0] and batch.total == 1
    assert _counts(r, r.users[0]) == (1, 1, 1)
    assert r.service1.get_batch(token, batch.batch_id) == batch
    assert r.service1.get_task(token, batch.tasks[0].task_id) == batch.tasks[0]
    token_b, _ = _login(r, 1)
    assert r.service1.list_batches(token_b) == []
    for operation, item_id in ((r.service1.get_batch, batch.batch_id),
                               (r.service1.get_task, batch.tasks[0].task_id)):
        with pytest.raises(DistributedImportError) as missing:
            operation(token_b, item_id)
        assert missing.value.status_code == 404
    r.sessions2.logout(token)
    with pytest.raises(DistributedImportError) as expired:
        r.service1.get_batch(token, batch.batch_id)
    assert expired.value.status_code == 401


def test_real_status_reads_survive_provider_unavailability(real_p3_resources, monkeypatch):
    r = real_p3_resources
    token, csrf = _login(r)
    batch = _upload(r, token, csrf)
    monkeypatch.setattr(r.api1, "storage_clients", lambda:
        (_ for _ in ()).throw(OSError("provider down")))
    assert r.service1.list_batches(token)[0].batch_id == batch.batch_id
    assert r.service1.get_batch(token, batch.batch_id) == batch
    assert r.service1.get_task(token, batch.tasks[0].task_id) == batch.tasks[0]


def test_real_runtime_schema_identity_drift_refuses_before_stream_or_storage(
        real_p3_resources, monkeypatch):
    r = real_p3_resources
    token, csrf = _login(r)
    with r.db.transaction() as cursor:
        head = cursor.execute("select version_num from alembic_version").fetchone()["version_num"]
        cursor.execute("update alembic_version set version_num='p3-drift-test'")

    io_calls = {"stream": 0, "storage": 0}

    class Bomb:
        def read(self, *_):
            io_calls["stream"] += 1
            raise AssertionError("upload stream was touched")

    def storage_bomb():
        io_calls["storage"] += 1
        raise AssertionError("storage client was touched")

    monkeypatch.setattr(r.api2, "storage_clients", storage_bomb)
    try:
        with pytest.raises(DistributedImportError) as refused:
            r.service2.submit_uploads(token, csrf, [ImportUpload("fresh.txt", Bomb())])
        assert (refused.value.status_code, refused.value.code) == \
            (503, "dependency_unavailable")
        assert io_calls == {"stream": 0, "storage": 0}
        assert _counts(r, r.users[0]) == (0, 0, 0)
    finally:
        with r.db.transaction() as cursor:
            cursor.execute("update alembic_version set version_num=%s", (head,))


def test_real_logout_before_recheck_refuses_and_leaves_no_refs(real_p3_resources,
                                                                monkeypatch):
    r = real_p3_resources
    token, csrf = _login(r)
    original = PostgresImportArtifactService._upload_one

    def logout_after_stage(service, *args):
        result = original(service, *args)
        r.sessions1.logout(token)
        return result

    monkeypatch.setattr(PostgresImportArtifactService, "_upload_one", logout_after_stage)
    with pytest.raises(DistributedImportError) as refused:
        _upload(r, token, csrf)
    assert refused.value.status_code == 401
    assert _counts(r, r.users[0]) == (0, 0, 0)


def test_real_close_during_cursor_authority_rolls_back_and_replay_refuses(
        real_p3_resources, monkeypatch):
    r = real_p3_resources
    token, csrf = _login(r)
    module = import_module("app.isolated_import_runtime")
    original_capture = module._capture_authority_cursor
    original_prepare = r.api2.prepare_admission
    original_session_recheck = r.sessions2.recheck_csrf_in_transaction
    issued = []
    closed = []
    callback_entries = []
    armed = False

    def retain_stamp(user_id):
        nonlocal armed
        stamp = original_prepare(user_id)
        assert stamp is not None
        issued.append(stamp)
        armed = True
        return stamp

    def record_callback(cursor, original_token, original_csrf, *, expected_user_id):
        callback_entries.append(expected_user_id)
        return original_session_recheck(cursor, original_token, original_csrf,
                                        expected_user_id=expected_user_id)

    def close_after_equal_capture(cursor, user_id):
        if not armed:
            return original_capture(cursor, user_id)
        assert armed and callback_entries == [user_id]
        assert issued[0] not in r.api2._admissions
        evidence = original_capture(cursor, user_id)
        with ThreadPoolExecutor(max_workers=1) as closer:
            closer.submit(r.api2.close).result(timeout=8)
        closed.append(True)
        return evidence

    monkeypatch.setattr(r.api2, "prepare_admission", retain_stamp)
    monkeypatch.setattr(r.sessions2, "recheck_csrf_in_transaction", record_callback)
    monkeypatch.setattr(module, "_capture_authority_cursor", close_after_equal_capture)
    with pytest.raises(DistributedImportError) as refused:
        _upload(r, token, csrf)
    assert refused.value.status_code == 503 and closed == [True]
    assert callback_entries == [r.users[0]] and len(issued) == 1
    assert _counts(r, r.users[0]) == (0, 0, 0)
    with pytest.raises(IsolatedAdmissionRejected):
        r.api2.recheck_admission(_PureCursor(), r.users[0], issued[0])


@pytest.mark.parametrize("mutation", ["disable", "logout", "rag_head",
                                      "episode_head", "history", "memory"])
def test_real_staging_time_authority_change_has_no_sql_reference(
        real_p3_resources, monkeypatch, mutation):
    r = real_p3_resources
    token, csrf = _login(r)
    actual_upload = PostgresImportArtifactService._upload_one

    def stage_then_mutate(service, *args):
        staged = actual_upload(service, *args)
        if mutation == "logout":
            r.sessions2.logout(token)
        else:
            with r.db.transaction() as cursor:
                if mutation == "disable":
                    cursor.execute("update users set status='disabled' where id=%s",
                                   (r.users[0],))
                elif mutation in ("rag_head", "episode_head"):
                    cursor.execute("""update vector_heads set revision=revision+1
                        where tenant_id=%s and vector_kind=%s""",
                        (r.users[0], mutation.removesuffix("_head")))
                else:
                    cursor.execute("""update user_snapshots set version=version+1
                        where user_id=%s and kind=%s""", (r.users[0], mutation))
        return staged

    monkeypatch.setattr(PostgresImportArtifactService, "_upload_one", stage_then_mutate)
    with pytest.raises(DistributedImportError) as refused:
        _upload(r, token, csrf)
    assert refused.value.status_code in (401, 503)
    assert _counts(r, r.users[0]) == (0, 0, 0)


def _insert_real_gate(r, task):
    payload = b"x"
    with r.db.transaction() as cursor:
        cursor.execute("""insert into import_publication_evidence
            (user_id,task_id,task_lease_version,document_id,worker_id,
             task_lease_token,user_lease_token,user_lease_version,schema_version,
             intent_format,intent_hash)
            values (%s,%s,1,%s,'p3-gate-test',%s,%s,1,1,
                    'canonical-json-zlib-1',%s)""",
            (r.users[0], task.task_id, task.document_id, str(uuid4()),
             str(uuid4()), sha256(payload).hexdigest()))
        cursor.execute("""insert into import_publication_private_payloads
            (user_id,task_id,task_lease_version,intent_payload,
             canonical_bytes,payload_phase_version)
            values (%s,%s,1,%s,1,1)""", (r.users[0], task.task_id, payload))
        cursor.execute("""insert into user_publication_gates
            (user_id,task_id,task_lease_version) values (%s,%s,1)""",
            (r.users[0], task.task_id))
        cursor.execute("""insert into import_publication_recovery_schedule
            (user_id,last_claimed_at) values (%s,'epoch'::timestamptz)
            on conflict (user_id) do nothing""", (r.users[0],))
        cursor.execute("""insert into import_publication_recovery_queue
            (user_id,task_id,task_lease_version,due_at,state,queue_version,reason_code)
            values (%s,%s,1,clock_timestamp(),'pending',1,'seed')""",
            (r.users[0], task.task_id))


def test_real_staging_gate_maps_409_and_blocks_sql_reference(real_p3_resources, monkeypatch):
    r = real_p3_resources
    token, csrf = _login(r)
    first = _upload(r, token, csrf)
    actual_upload = PostgresImportArtifactService._upload_one

    def stage_then_gate(service, *args):
        staged = actual_upload(service, *args)
        _insert_real_gate(r, first.tasks[0])
        return staged

    monkeypatch.setattr(PostgresImportArtifactService, "_upload_one", stage_then_gate)
    with pytest.raises(DistributedImportError) as refused:
        _upload(r, token, csrf)
    assert (refused.value.status_code, refused.value.code,
            refused.value.retryable) == (409, "needs_reconciliation", False)
    assert _counts(r, r.users[0]) == (1, 1, 1)


def test_real_readback_proves_pinned_bytes_manifest_and_tenant(real_p3_resources):
    r = real_p3_resources
    resources = (r.settings, r.db, r.raw, r.s3, r.users,
                 r.rag_scopes, r.episode_scopes)
    document_id = _publish_nonempty_history(resources)
    token_a, _ = _login(r)
    token_b, _ = _login(r, 1)
    result = r.service1.read_published_document(token_a, document_id)
    assert result.document_id == document_id
    assert result.filename == "legacy.txt" and result.suffix == ".txt"
    assert result.content == b"legacy retained bytes"
    assert "legacy retained bytes" not in repr(result)
    for selected in (token_b,):
        with pytest.raises(DistributedImportError) as missing:
            r.service1.read_published_document(selected, document_id)
        assert missing.value.status_code == 404
    with pytest.raises(DistributedImportError) as missing:
        r.service1.read_published_document(token_a, str(uuid4()))
    assert missing.value.status_code == 404


def test_real_deletion_fence_arriving_during_read_rejects(real_p3_resources, monkeypatch):
    r = real_p3_resources
    document_id = _publish_nonempty_history((r.settings, r.db, r.raw, r.s3,
        r.users, r.rag_scopes, r.episode_scopes))
    token, _ = _login(r)
    store, _ = r.api1.storage_clients()
    actual_read = store.read_verified
    injected = False

    def read_then_fence(*args):
        nonlocal injected
        content = actual_read(*args)
        if not injected:
            injected = True
            with r.db.transaction() as cursor:
                cursor.execute("""insert into qa_deletion_fences
                    (id,user_id,target_type,target_id,status,stage,created_at,updated_at)
                    values (%s,%s,'document',%s,'queued','fenced',%s,%s)""",
                    (str(uuid4()), r.users[0], document_id,
                     datetime.now(timezone.utc).isoformat(),
                     datetime.now(timezone.utc).isoformat()))
        return content

    monkeypatch.setattr(store, "read_verified", read_then_fence)
    with pytest.raises(DistributedImportError) as refused:
        r.service1.read_published_document(token, document_id)
    assert refused.value.status_code == 503 and injected


def test_real_missing_pinned_s3_version_rejects_readback(real_p3_resources):
    r = real_p3_resources
    document_id = _publish_nonempty_history((r.settings, r.db, r.raw, r.s3,
        r.users, r.rag_scopes, r.episode_scopes))
    token, _ = _login(r)
    with r.db.transaction() as cursor:
        ref = cursor.execute("""select object_key,version_id from document_objects
            where user_id=%s and document_id=%s""",
            (r.users[0], document_id)).fetchone()
    r.s3.delete_object(Bucket=r.settings.s3_bucket, Key=ref["object_key"],
                       VersionId=ref["version_id"])
    with pytest.raises(DistributedImportError) as refused:
        r.service1.read_published_document(token, document_id)
    assert refused.value.status_code == 503


def test_real_readback_rejects_changed_head(real_p3_resources, monkeypatch):
    r = real_p3_resources
    document_id = _publish_nonempty_history((r.settings, r.db, r.raw, r.s3,
        r.users, r.rag_scopes, r.episode_scopes))
    token, _ = _login(r)
    store, _ = r.api1.storage_clients()
    read = store.read_verified
    changed = False

    def change_head(*args):
        nonlocal changed
        content = read(*args)
        if not changed:
            changed = True
            with r.db.transaction() as cursor:
                cursor.execute("""update vector_heads set revision=revision+1
                    where tenant_id=%s and vector_kind='rag'""", (r.users[0],))
        return content

    monkeypatch.setattr(store, "read_verified", change_head)
    with pytest.raises(DistributedImportError) as refused:
        r.service1.read_published_document(token, document_id)
    assert refused.value.status_code == 503 and changed


def test_real_equal_invalid_fence_rejects_intermediate_public_visibility(
        real_p3_resources, monkeypatch):
    r = real_p3_resources
    document_id = _publish_nonempty_history((r.settings, r.db, r.raw, r.s3,
        r.users, r.rag_scopes, r.episode_scopes))
    token, _ = _login(r)
    with r.db.transaction() as cursor:
        cursor.execute("""insert into qa_deletion_fences
            (id,user_id,target_type,target_id,status,stage,created_at,updated_at)
            values (%s,%s,'document',%s,'queued','fenced',%s,%s)""",
            (str(uuid4()), r.users[0], document_id,
             datetime.now(timezone.utc).isoformat(),
             datetime.now(timezone.utc).isoformat()))
    facade_module = import_module("app.distributed_import_service")
    original_record = facade_module._record
    observed = []
    original_capture = r.service1._capture_readback

    def capture(scope, selected):
        row = original_capture(scope, selected)
        observed.append(row)
        return row

    def briefly_visible_record(row, user_id, selected):
        if len(observed) == 1:
            # Simulate a public helper briefly reporting visible content while
            # the equal before/after authority captures both retain the fence.
            return original_record({**row, "fences": []}, user_id, selected)
        return original_record(row, user_id, selected)

    monkeypatch.setattr(r.service1, "_capture_readback", capture)
    monkeypatch.setattr(facade_module, "_record", briefly_visible_record)
    with pytest.raises(DistributedImportError) as fenced:
        r.service1.read_published_document(token, document_id)
    assert fenced.value.status_code == 503
    assert len(observed) == 2 and observed[0] == observed[1]
    assert observed[0]["fences"][0]["status"] == "queued"


def test_real_readback_rejects_actual_qdrant_manifest_mismatch(
        real_p3_resources, monkeypatch):
    r = real_p3_resources
    document_id = _publish_nonempty_history((r.settings, r.db, r.raw, r.s3,
        r.users, r.rag_scopes, r.episode_scopes))
    token, _ = _login(r)
    collection = r.rag_scopes[0].identity.physical_collection
    points, offset = r.raw.client.scroll(collection_name=collection, limit=100,
                                         with_payload=True)
    assert offset is None and len(points) == 1
    r.raw.client.set_payload(collection_name=collection,
        payload={"p3_manifest_tamper": "different"}, points=[points[0].id], wait=True)
    with pytest.raises(DistributedImportError) as mismatch:
        r.service1.read_published_document(token, document_id)
    assert mismatch.value.status_code == 503


def test_real_readback_rejects_alternate_pinned_source_bytes(
        real_p3_resources, monkeypatch):
    r = real_p3_resources
    document_id = _publish_nonempty_history((r.settings, r.db, r.raw, r.s3,
        r.users, r.rag_scopes, r.episode_scopes))
    token, _ = _login(r)
    store, _ = r.api1.storage_clients()
    original_read = store.read_verified
    calls = 0
    with r.db.transaction() as cursor:
        key = cursor.execute("""select object_key from document_objects
            where user_id=%s and document_id=%s""",
            (r.users[0], document_id)).fetchone()["object_key"]
    alternate = b"x" * len(b"legacy retained bytes")
    alternate_version = r.s3.put_object(Bucket=r.settings.s3_bucket,
        Key=key, Body=alternate)["VersionId"]

    def different_intermediate_bytes(*args):
        nonlocal calls
        calls += 1
        result = original_read(*args)
        if calls == 2:
            # This is the requested-document read, after full retained-ref proof.
            body = r.s3.get_object(Bucket=r.settings.s3_bucket, Key=key,
                                   VersionId=alternate_version)["Body"]
            return body.read()
        return result

    monkeypatch.setattr(store, "read_verified", different_intermediate_bytes)
    with pytest.raises(DistributedImportError) as changed:
        r.service1.read_published_document(token, document_id)
    assert changed.value.status_code == 503 and calls == 2


def test_real_readback_rejects_reference_aba_to_real_alternate_object(
        real_p3_resources, monkeypatch):
    r = real_p3_resources
    document_id = _publish_nonempty_history((r.settings, r.db, r.raw, r.s3,
        r.users, r.rag_scopes, r.episode_scopes))
    token, _ = _login(r)
    store, _ = r.api1.storage_clients()
    alternate = b"alternate retained bytes"
    digest = sha256(alternate).hexdigest()
    alternate_ref = store.put_immutable(r.users[0], artifact_key(r.users[0],
        "documents", document_id, ".txt", digest), alternate).ref
    facade_module = import_module("app.distributed_import_service")
    original_refs = facade_module._references
    calls = 0

    def abased_reference(*args):
        nonlocal calls
        calls += 1
        proven = original_refs(*args)
        return alternate_ref if calls == 1 else proven

    monkeypatch.setattr(facade_module, "_references", abased_reference)
    with pytest.raises(DistributedImportError) as refused:
        r.service1.read_published_document(token, document_id)
    assert refused.value.status_code == 503 and calls == 2


def test_real_readback_holds_no_user_lock_during_object_io(real_p3_resources,
                                                            monkeypatch):
    r = real_p3_resources
    document_id = _publish_nonempty_history((r.settings, r.db, r.raw, r.s3,
        r.users, r.rag_scopes, r.episode_scopes))
    token, _ = _login(r)
    store, _ = r.api1.storage_clients()
    original = store.read_verified
    checked = False

    def assert_unlocked(*args):
        nonlocal checked
        with r.db.transaction() as cursor:
            assert cursor.execute("""select id from users
                where id=%s for update nowait""", (r.users[0],)).fetchone()
        checked = True
        return original(*args)

    monkeypatch.setattr(store, "read_verified", assert_unlocked)
    assert r.service1.read_published_document(token, document_id).content == \
        b"legacy retained bytes"
    assert checked


def test_real_logout_waits_behind_locked_valid_admission(real_p3_resources, monkeypatch):
    r = real_p3_resources
    token, csrf = _login(r)
    locked = threading.Event()
    release = threading.Event()
    original = r.api2.recheck_admission

    def hold_after_token_lock(cursor, user_id, stamp):
        # The session callback already acquired FOR UPDATE NOWAIT on this cursor.
        locked.set()
        assert release.wait(8)
        return original(cursor, user_id, stamp)

    monkeypatch.setattr(r.api2, "recheck_admission", hold_after_token_lock)
    with ThreadPoolExecutor(max_workers=2) as workers:
        admitted = workers.submit(_upload, r, token, csrf)
        assert locked.wait(8)
        logout = workers.submit(r.sessions1.logout, token)
        time.sleep(0.15)
        assert not logout.done()
        release.set()
        batch = admitted.result(timeout=12)
        logout.result(timeout=12)
    assert batch.total == 1 and _counts(r, r.users[0]) == (1, 1, 1)
    with pytest.raises(InvalidSessionError):
        r.sessions2.get_session(token)


def test_real_expiry_during_user_lock_wait_uses_post_lock_clock(
        real_p3_resources, monkeypatch):
    r = real_p3_resources
    token, csrf = _login(r)
    staged = threading.Event()
    original = PostgresImportArtifactService._upload_one

    def signal_after_stage(service, *args):
        result = original(service, *args)
        with r.db.transaction() as cursor:
            cursor.execute("""update auth_sessions
                set expires_at=clock_timestamp()+interval '0.5 seconds'
                where token_hash=%s""", (r.sessions1.repository._hash(token),))
        staged.set()
        return result

    monkeypatch.setattr(PostgresImportArtifactService, "_upload_one", signal_after_stage)
    with ThreadPoolExecutor(max_workers=1) as workers:
        with r.db.transaction() as holder:
            holder.execute("select id from users where id=%s for update", (r.users[0],))
            admission = workers.submit(_upload, r, token, csrf)
            assert staged.wait(8)
            time.sleep(0.65)
        with pytest.raises(DistributedImportError) as refused:
            admission.result(timeout=12)
    assert refused.value.status_code == 401 and _counts(r, r.users[0]) == (0, 0, 0)


def test_real_expired_cleanup_fk_wait_nowait_refuses_without_deadlock(
        real_p3_resources, monkeypatch):
    r = real_p3_resources
    token, csrf = _login(r)
    user_locked = threading.Event()
    release_callback = threading.Event()
    original_stage = PostgresImportArtifactService._upload_one
    original_lock = PostgresImportArtifactService._require_active_locked
    original_login_transaction = r.api1.database.transaction
    admission_pid = []

    @contextmanager
    def bounded_login_transaction():
        with original_login_transaction() as cursor:
            cursor.execute("set local statement_timeout='12s'")
            yield cursor

    def expire_after_stage(service, *args):
        staged = original_stage(service, *args)
        with r.db.transaction() as cursor:
            cursor.execute("""update auth_sessions
                set expires_at=clock_timestamp()-interval '1 second'
                where token_hash=%s""", (r.sessions1.repository._hash(token),))
        return staged

    def hold_user_then_callback(cursor, user_id):
        original_lock(cursor, user_id)
        cursor.execute("set local statement_timeout='12s'")
        admission_pid.append(cursor.execute("select pg_backend_pid() as pid").fetchone()["pid"])
        user_locked.set()
        assert release_callback.wait(10)

    monkeypatch.setattr(PostgresImportArtifactService, "_upload_one", expire_after_stage)
    monkeypatch.setattr(PostgresImportArtifactService, "_require_active_locked",
                        staticmethod(hold_user_then_callback))
    monkeypatch.setattr(r.api1.database, "transaction", bounded_login_transaction)
    with ThreadPoolExecutor(max_workers=2) as workers:
        admission = workers.submit(_upload, r, token, csrf)
        assert user_locked.wait(8)
        login = workers.submit(r.sessions1.login, r.names[0], "correct-password")
        try:
            deadline = time.monotonic() + 8
            blocked_insert = False
            while time.monotonic() < deadline:
                with r.db.transaction() as cursor:
                    rows = cursor.execute("""select pid,wait_event_type,
                        pg_blocking_pids(pid) as blockers from pg_stat_activity
                        where lower(query) like '%%insert into auth_sessions%%'
                        and state='active'""").fetchall()
                blocked_insert = any(row["wait_event_type"] == "Lock" and
                    admission_pid[0] in row["blockers"] for row in rows)
                if blocked_insert:
                    break
                time.sleep(0.05)
            assert blocked_insert and not login.done()
        finally:
            release_callback.set()
        with pytest.raises(DistributedImportError) as refused:
            admission.result(timeout=12)
        new_token = login.result(timeout=12)
    assert refused.value.status_code == 503
    assert _counts(r, r.users[0]) == (0, 0, 0)
    assert r.sessions2.get_session(new_token).user_id == r.users[0]
