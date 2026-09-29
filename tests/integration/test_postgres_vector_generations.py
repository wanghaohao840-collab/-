"""Opt-in real PostgreSQL authority tests in a fresh disposable schema."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import threading
import time
from uuid import uuid4

import pytest

from app.import_models import ImportTaskCreate
from app.import_repository import PostgresImportTaskRepository
from app.postgres_coordination import PostgresUserMutationCoordinator
from app.postgres_coordination import MutationLeaseLost
from app.postgres import PostgresDatabase
from app.postgres_import_leases import ImportLeaseLost, PostgresImportLeaseRepository
from app.postgres_vector_generations import (
    PostgresVectorGenerationAuthority, VectorAuthorityError, VectorScope,
)
from hello_agents.memory.rag.embedding_profile import EmbeddingProfile
from hello_agents.memory.rag.index_identity import IndexIdentity
from tests.integration.test_postgres_auth_sessions import shared_database

DIGEST = hashlib.sha256(b'verified-complete-corpus').hexdigest()


@pytest.fixture
def setup(shared_database):
    open_pool, url = shared_database
    first, second = open_pool(), open_pool()
    owner, other = str(uuid4()), str(uuid4())
    with first.transaction() as cursor:
        for user in (owner, other):
            cursor.execute('insert into users values (%s,%s,%s,%s,%s,%s,%s)',
                           (user, user, user, 'hash', 'active', 'now', 'now'))
    identity = IndexIdentity('qdrant', 'docs',
                             EmbeddingProfile('simple', '', 'SimpleEmbedding', 'v1', 4))
    scope = VectorScope(owner, 'rag', 'documents', identity)
    return first, second, owner, other, scope, url


def lease(db, user, owner='worker'):
    result = PostgresUserMutationCoordinator(db).acquire(user, owner)
    assert result is not None
    return result


def test_missing_staged_published_empty_and_reconciliation(setup):
    first, second, owner, _, scope, _ = setup
    authority = PostgresVectorGenerationAuthority(first)
    observer = PostgresVectorGenerationAuthority(second)
    assert observer.read_head(scope).state == 'missing'
    handle = lease(first, owner)
    generation = authority.stage(scope, handle, expected_revision=None)
    assert observer.read_head(scope).state == 'missing'
    assert authority.reconcile(scope, generation)['state'] == 'staging'
    authority.seal(scope, handle, generation, expected_count=2, content_digest=DIGEST)
    assert observer.read_head(scope).state == 'missing'
    assert authority.publish_user(scope, handle, generation, expected_revision=None) == 1
    head = observer.read_head(scope)
    assert (head.state, head.revision, head.generation_id) == ('published', 1, generation)
    assert observer.reconcile(scope, generation)['is_head']
    with pytest.raises(VectorAuthorityError):
        authority.seal(scope, handle, generation, expected_count=2, content_digest=DIGEST)
    empty = authority.stage(scope, handle, expected_revision=1)
    authority.seal(scope, handle, empty, expected_count=0, content_digest=DIGEST)
    assert authority.publish_user(scope, handle, empty, expected_revision=1) == 2
    head = observer.read_head(scope)
    assert (head.state, head.revision, head.generation_id) == ('empty', 2, None)
    assert observer.reconcile(scope, generation)['state'] == 'retired'
    assert observer.reconcile(scope, empty)['state'] == 'published'
    assert observer.reconcile(scope, empty)['is_head']
    after_empty = authority.stage(scope, handle, expected_revision=2)
    authority.seal(scope, handle, after_empty, expected_count=1, content_digest=DIGEST)
    authority.publish_user(scope, handle, after_empty, expected_revision=2)
    assert observer.reconcile(scope, empty)['state'] == 'retired'


def test_scope_fingerprint_cas_abandon_and_no_reuse(setup):
    first, second, owner, other, scope, _ = setup
    authority = PostgresVectorGenerationAuthority(first)
    handle = lease(first, owner)
    candidate = authority.stage(scope, handle, expected_revision=None)
    wrong_tenant = replace(scope, tenant_id=other)
    wrong_kind = replace(scope, vector_kind='episode')
    wrong_identity = replace(scope, identity=IndexIdentity('qdrant', 'docs',
        EmbeddingProfile('simple', '', 'DifferentModel', 'v1', 4)))
    for wrong in (wrong_tenant, wrong_kind, wrong_identity):
        with pytest.raises(VectorAuthorityError):
            authority.seal(wrong, handle, candidate, expected_count=1, content_digest=DIGEST)
        assert authority.reconcile(wrong, candidate) is None
    authority.abandon(scope, handle, candidate)
    assert authority.reconcile(scope, candidate)['state'] == 'abandoned'
    with pytest.raises(VectorAuthorityError):
        authority.seal(scope, handle, candidate, expected_count=1, content_digest=DIGEST)
    with pytest.raises(Exception):  # permanent UUID uniqueness is a DB invariant
        authority.stage(scope, handle, expected_revision=None, generation_id=candidate)
    current = authority.stage(scope, handle, expected_revision=None)
    authority.seal(scope, handle, current, expected_count=1, content_digest=DIGEST)
    with pytest.raises(MutationLeaseLost):
        authority.publish_user(scope, replace(handle, lease_token=uuid4()), current,
                               expected_revision=None)
    with pytest.raises(VectorAuthorityError):
        authority.publish_user(scope, handle, current, expected_revision=1)
    assert PostgresVectorGenerationAuthority(second).read_head(scope).state == 'missing'
    competing = authority.stage(scope, handle, expected_revision=None)
    authority.seal(scope, handle, competing, expected_count=1, content_digest=DIGEST)
    authority.publish_user(scope, handle, current, expected_revision=None)
    with pytest.raises(VectorAuthorityError):
        PostgresVectorGenerationAuthority(second).publish_user(
            scope, handle, competing, expected_revision=None)
    assert authority.reconcile(scope, competing)['state'] == 'sealed'


def test_rollback_and_two_pool_wait_then_expired_lease(setup):
    first, second, owner, _, scope, url = setup
    authority = PostgresVectorGenerationAuthority(first)
    handle = lease(first, owner)
    generation = authority.stage(scope, handle, expected_revision=None)
    authority.seal(scope, handle, generation, expected_count=1, content_digest=DIGEST)
    def fail(cursor):
        cursor.execute("update users set updated_at='uncommitted' where id=%s", (owner,))
        raise RuntimeError('rollback after pointer change')
    with pytest.raises(RuntimeError):
        authority.publish_user(scope, handle, generation, expected_revision=None,
                               domain_publish=fail)
    assert PostgresVectorGenerationAuthority(second).read_head(scope).state == 'missing'
    assert authority.reconcile(scope, generation)['state'] == 'sealed'
    with pytest.raises(Exception):
        with first.transaction() as cursor:
            cursor.execute("update vector_generations set content_digest=%s where generation_id=%s",
                           (hashlib.sha256(b'changed').hexdigest(), generation))
    waiting = threading.Event()
    app_name = 'vector_wait_' + uuid4().hex
    waiter_db = PostgresDatabase(url.replace('postgresql+psycopg://', 'postgresql://')
                                 + '&application_name=' + app_name, min_size=1, max_size=1)
    waiter_db.open()
    def delayed():
        waiting.set()
        return PostgresVectorGenerationAuthority(waiter_db).publish_user(
            scope, handle, generation, expected_revision=None)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with first.transaction() as blocker:
                blocker.execute('select id from users where id=%s for update', (owner,))
                future = pool.submit(delayed)
                assert waiting.wait(5)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    blocked = blocker.execute('''select count(*) as n from pg_stat_activity
                        where application_name=%s and wait_event_type='Lock' ''',
                        (app_name,)).fetchone()['n']
                    if blocked:
                        break
                    time.sleep(0.02)
                assert blocked == 1, 'Publication did not reach the user-row lock wait'
                blocker.execute("update user_mutation_leases set lease_expires_at=clock_timestamp()-interval '1 second' where user_id=%s", (owner,))
            with pytest.raises(Exception):
                future.result(timeout=5)
    finally:
        waiter_db.close()
    assert authority.read_head(scope).state == 'missing'


def test_head_lock_wait_rechecks_expiry_after_acquisition(setup):
    first, second, owner, _, scope, url = setup
    authority = PostgresVectorGenerationAuthority(first)
    coordinator = PostgresUserMutationCoordinator(first)
    original_lease = lease(first, owner)
    original = authority.stage(scope, original_lease, expected_revision=None)
    authority.seal(scope, original_lease, original, expected_count=1, content_digest=DIGEST)
    authority.publish_user(scope, original_lease, original, expected_revision=None)
    assert coordinator.release(original_lease)
    short_lease = coordinator.acquire(owner, 'replacement', lease_seconds=3)
    assert short_lease is not None
    replacement = authority.stage(scope, short_lease, expected_revision=1)
    authority.seal(scope, short_lease, replacement, expected_count=1, content_digest=DIGEST)
    app_name = 'vector_head_wait_' + uuid4().hex
    waiter_db = PostgresDatabase(url.replace('postgresql+psycopg://', 'postgresql://')
                                 + '&application_name=' + app_name, min_size=1, max_size=1)
    waiter_db.open()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with second.transaction() as blocker:
                blocker.execute('''select revision from vector_heads where tenant_id=%s
                    and vector_kind=%s and namespace=%s and index_key=%s for update''', scope.key)
                future = pool.submit(lambda: PostgresVectorGenerationAuthority(waiter_db).publish_user(
                    scope, short_lease, replacement, expected_revision=1))
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    blocked = blocker.execute('''select count(*) as n from pg_stat_activity
                        where application_name=%s and wait_event_type='Lock' ''',
                        (app_name,)).fetchone()['n']
                    if blocked:
                        break
                    time.sleep(0.02)
                assert blocked == 1, 'Publication did not reach the head-row lock wait'
                time.sleep(3.1)
            with pytest.raises(MutationLeaseLost):
                future.result(timeout=5)
    finally:
        waiter_db.close()
    head = authority.read_head(scope)
    assert (head.revision, head.generation_id) == (1, original)
    assert authority.reconcile(scope, replacement)['state'] == 'sealed'


def test_import_complete_is_atomic_and_stale_task_rejected(setup):
    first, second, owner, _, scope, _ = setup
    task_id, batch_id = str(uuid4()), str(uuid4())
    with first.transaction() as cursor:
        now = cursor.execute('select clock_timestamp() as now').fetchone()['now'].isoformat()
        PostgresImportTaskRepository(first).create_batch_in_transaction(cursor, owner,
            [ImportTaskCreate(task_id, batch_id, owner, str(uuid4()), 'a.txt', '.txt', 1, '')], now=now)
        cursor.execute('''insert into import_objects
            (task_id,user_id,bucket,object_key,version_id,sha256,size_bytes)
            values (%s,%s,'test','object','v1',%s,1)''', (task_id, owner, DIGEST))
    imports = PostgresImportLeaseRepository(first)
    attempt = imports.claim_next('worker')
    assert attempt is not None and imports.try_begin_committing(attempt)
    authority = PostgresVectorGenerationAuthority(first)
    generation = authority.stage(scope, attempt, expected_revision=None)
    authority.seal(scope, attempt, generation, expected_count=1, content_digest=DIGEST)
    with pytest.raises(ImportLeaseLost):
        authority.complete_import(scope, replace(attempt, lease_token=uuid4()),
                                  generation, expected_revision=None)
    with first.transaction() as cursor:
        cursor.execute("update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second' where id=%s", (task_id,))
    with pytest.raises(ImportLeaseLost):
        authority.complete_import(scope, attempt, generation, expected_revision=None)
    assert PostgresVectorGenerationAuthority(second).read_head(scope).state == 'missing'
    with first.transaction() as cursor:
        cursor.execute("update import_tasks set lease_expires_at=clock_timestamp()+interval '30 seconds' where id=%s", (task_id,))
    task, revision = authority.complete_import(scope, attempt, generation, expected_revision=None)
    assert task.status == 'succeeded' and revision == 1
    assert PostgresVectorGenerationAuthority(second).read_head(scope).generation_id == generation


def test_expired_user_lease_rejects_seal_and_disabled_user_rejects_stage(setup):
    first, _, owner, _, scope, _ = setup
    authority = PostgresVectorGenerationAuthority(first)
    handle = lease(first, owner)
    candidate = authority.stage(scope, handle, expected_revision=None)
    with first.transaction() as cursor:
        cursor.execute("update user_mutation_leases set lease_expires_at=clock_timestamp()-interval '1 second' where user_id=%s", (owner,))
    with pytest.raises(MutationLeaseLost):
        authority.seal(scope, handle, candidate, expected_count=1, content_digest=DIGEST)
    assert authority.reconcile(scope, candidate)['state'] == 'staging'
    with first.transaction() as cursor:
        cursor.execute("update users set status='disabled' where id=%s", (owner,))
    with pytest.raises(MutationLeaseLost):
        authority.stage(scope, handle, expected_revision=None)


def test_database_rejects_cross_scope_and_unpublished_head(setup):
    first, _, owner, other, scope, _ = setup
    authority = PostgresVectorGenerationAuthority(first)
    owner_lease = lease(first, owner, 'owner')
    generation = authority.stage(scope, owner_lease, expected_revision=None)
    authority.seal(scope, owner_lease, generation, expected_count=1, content_digest=DIGEST)
    with pytest.raises(Exception):
        with first.transaction() as cursor:
            cursor.execute('''insert into vector_heads
                (tenant_id,vector_kind,namespace,index_key,revision,generation_id,
                 last_generation_id,index_revision) values (%s,%s,%s,%s,1,%s,%s,1)''',
                (*scope.key, generation, generation))
    other_scope = replace(scope, tenant_id=other)
    other_lease = lease(first, other, 'other')
    authority.stage(other_scope, other_lease, expected_revision=None)
    with pytest.raises(Exception):
        with first.transaction() as cursor:
            cursor.execute('''insert into vector_heads
                (tenant_id,vector_kind,namespace,index_key,revision,generation_id,
                 last_generation_id,index_revision) values (%s,%s,%s,%s,1,%s,%s,1)''',
                (*other_scope.key, generation, generation))
    assert authority.read_head(scope).state == 'missing'
    assert authority.read_head(other_scope).state == 'missing'


def test_autocommit_and_isolation_rejected(setup):
    first, _, owner, _, scope, _ = setup
    authority = PostgresVectorGenerationAuthority(first)
    handle = lease(first, owner)
    with first.connection() as connection:
        connection.autocommit = True
        with connection.cursor() as cursor:
            with pytest.raises(ValueError):
                authority._owner(cursor, handle, scope)
        connection.autocommit = False
    with first.transaction() as cursor:
        cursor.execute('set transaction isolation level repeatable read')
        with pytest.raises(ValueError):
            authority._owner(cursor, handle, scope)
