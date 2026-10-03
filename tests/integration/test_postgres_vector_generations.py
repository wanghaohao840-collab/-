"""Opt-in real PostgreSQL authority tests in a fresh disposable schema."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import threading
import time
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from psycopg import errors as pgerrors
from psycopg.types.json import Jsonb

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


def after_statement(monkeypatch, database, fragment, publish):
    """Commit on the other pool after one SELECT executes, before its fetch."""
    original = database.transaction
    fired = False

    class Cursor:
        def __init__(self, raw):
            self.raw = raw

        def execute(self, statement, params=None):
            nonlocal fired
            self.raw.execute(statement, params)
            fragments = fragment if isinstance(fragment, tuple) else (fragment,)
            if not fired and any(part in statement.lower() for part in fragments):
                fired = True
                publish()
            return self

        def __getattr__(self, name):
            return getattr(self.raw, name)

    @contextmanager
    def instrumented():
        with original() as cursor:
            yield Cursor(cursor)

    monkeypatch.setattr(database, 'transaction', instrumented)
    return lambda: fired


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


def _legacy_stage(db, scope, handle):
    """Seed a rev-10 candidate without calling a rev-14 guarded writer."""
    generation = uuid4()
    with db.transaction() as cursor:
        cursor.execute('''insert into vector_indexes
            (tenant_id,vector_kind,namespace,index_key,identity)
            values (%s,%s,%s,%s,%s) on conflict do nothing''',
            (*scope.key, Jsonb(scope.identity.to_dict())))
        head = cursor.execute('''select revision from vector_heads
            where tenant_id=%s and vector_kind=%s and namespace=%s
            and index_key=%s''', scope.key).fetchone()
        cursor.execute('''insert into vector_generations
            (generation_id,tenant_id,vector_kind,namespace,index_key,
             base_revision,index_revision,owner,user_lease_token,user_lease_version,state)
            values (%s,%s,%s,%s,%s,%s,1,%s,%s,%s,'staging')''',
            (generation, *scope.key, None if head is None else head['revision'],
             handle.owner, handle.lease_token, handle.lease_version))
    return generation


def _legacy_seal(db, generation):
    with db.transaction() as cursor:
        cursor.execute('''update vector_generations
            set state='sealed',expected_count=1,content_digest=%s,
                sealed_at=clock_timestamp() where generation_id=%s''',
            (DIGEST, generation))


def import_attempt(db, owner, *, lease_seconds=60):
    task_id, batch_id = str(uuid4()), str(uuid4())
    with db.transaction() as cursor:
        now = cursor.execute('select clock_timestamp() as now').fetchone()['now'].isoformat()
        PostgresImportTaskRepository(db).create_batch_in_transaction(cursor, owner,
            [ImportTaskCreate(task_id, batch_id, owner, str(uuid4()), 'a.txt', '.txt', 1, '')], now=now)
        cursor.execute('''insert into import_objects
            (task_id,user_id,bucket,object_key,version_id,sha256,size_bytes)
            values (%s,%s,'test','object','v1',%s,1)''', (task_id, owner, DIGEST))
    imports = PostgresImportLeaseRepository(db)
    attempt = imports.claim_next('worker', lease_seconds=lease_seconds)
    assert attempt is not None and imports.try_begin_committing(attempt)
    return attempt


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


def test_legacy_publish_without_receipt_rolls_back_head_and_generation(setup):
    first, _, owner, _, scope, _ = setup
    authority = PostgresVectorGenerationAuthority(first)
    handle = lease(first, owner)
    generation = authority.stage(scope, handle, expected_revision=None)
    authority.seal(scope, handle, generation, expected_count=1, content_digest=DIGEST)

    # This is the pre-011 writer's complete first-publication transaction.
    # The deferred head guard is satisfied, so only the receipt constraint
    # can reject the missing revision at the database boundary.
    with pytest.raises(pgerrors.CheckViolation, match='vector_publication_receipt_state'):
        with first.transaction() as cursor:
            cursor.execute('''insert into vector_heads
                (tenant_id,vector_kind,namespace,index_key,revision,generation_id,
                 last_generation_id,index_revision,snapshot_version)
                select tenant_id,vector_kind,namespace,index_key,1,generation_id,
                    generation_id,index_revision,null
                from vector_generations where generation_id=%s''', (generation,))
            cursor.execute('''update vector_generations
                set state='published',published_at=clock_timestamp()
                where generation_id=%s''', (generation,))

    assert authority.read_head(scope).state == 'missing'
    assert authority.reconcile(scope, generation)['state'] == 'sealed'


@pytest.mark.parametrize('shared_database', ['20260929_10'], indirect=True)
def test_receipt_migration_upgrades_unpublished_candidate(shared_database):
    open_pool, _ = shared_database
    first = open_pool()
    owner = str(uuid4())
    with first.transaction() as cursor:
        cursor.execute('insert into users values (%s,%s,%s,%s,%s,%s,%s)',
                       (owner, owner, owner, 'hash', 'active', 'now', 'now'))
    identity = IndexIdentity('qdrant', 'docs',
                             EmbeddingProfile('simple', '', 'SimpleEmbedding', 'v1', 4))
    scope = VectorScope(owner, 'rag', 'documents', identity)
    generation = _legacy_stage(first, scope, lease(first, owner))
    command.upgrade(Config('alembic.ini'), '20260929_11')
    with first.transaction() as cursor:
        row = cursor.execute('''select state,publication_revision,
            publication_snapshot_version from vector_generations
            where generation_id=%s''', (generation,)).fetchone()
        version = cursor.execute('select version_num from alembic_version').fetchone()['version_num']
    assert row == {'state': 'staging', 'publication_revision': None,
                   'publication_snapshot_version': None}
    assert version == '20260929_11'


@pytest.mark.parametrize('historical_state', ['published', 'retired'])
@pytest.mark.parametrize('shared_database', ['20260929_10'], indirect=True)
def test_receipt_migration_rejects_existing_publication(shared_database, historical_state):
    open_pool, _ = shared_database
    first = open_pool()
    owner = str(uuid4())
    with first.transaction() as cursor:
        cursor.execute('insert into users values (%s,%s,%s,%s,%s,%s,%s)',
                       (owner, owner, owner, 'hash', 'active', 'now', 'now'))
    identity = IndexIdentity('qdrant', 'docs',
                             EmbeddingProfile('simple', '', 'SimpleEmbedding', 'v1', 4))
    scope = VectorScope(owner, 'rag', 'documents', identity)
    authority = PostgresVectorGenerationAuthority(first)
    handle = lease(first, owner)
    old = _legacy_stage(first, scope, handle)
    _legacy_seal(first, old)
    with first.transaction() as cursor:
        cursor.execute('''insert into vector_heads
            (tenant_id,vector_kind,namespace,index_key,revision,generation_id,
             last_generation_id,index_revision,snapshot_version)
            select tenant_id,vector_kind,namespace,index_key,1,generation_id,
                generation_id,index_revision,null from vector_generations
            where generation_id=%s''', (old,))
        cursor.execute('''update vector_generations
            set state='published',published_at=clock_timestamp()
            where generation_id=%s''', (old,))
    if historical_state == 'retired':
        new = _legacy_stage(first, scope, handle)
        _legacy_seal(first, new)
        with first.transaction() as cursor:
            cursor.execute('''update vector_heads set revision=2,generation_id=%s,
                last_generation_id=%s,updated_at=clock_timestamp()
                where tenant_id=%s and vector_kind=%s and namespace=%s and index_key=%s''',
                (new, new, *scope.key))
            cursor.execute("update vector_generations set state='retired' where generation_id=%s", (old,))
            cursor.execute('''update vector_generations
                set state='published',published_at=clock_timestamp()
                where generation_id=%s''', (new,))
    with pytest.raises(Exception, match='existing vector publications require an explicit receipt migration'):
        command.upgrade(Config('alembic.ini'), '20260929_11')
    with first.transaction() as cursor:
        version = cursor.execute('select version_num from alembic_version').fetchone()['version_num']
        columns = cursor.execute('''select column_name from information_schema.columns
            where table_schema=current_schema() and table_name='vector_generations'
            and column_name='publication_revision' ''').fetchall()
        state = cursor.execute('select state from vector_generations where generation_id=%s',
                               (old,)).fetchone()['state']
    assert version == '20260929_10'
    assert columns == []
    assert state == historical_state


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
    with pytest.raises(pgerrors.UniqueViolation):  # permanent UUID uniqueness is a DB invariant
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
    with pytest.raises(pgerrors.RaiseException, match='sealed vector manifest is immutable'):
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
            with pytest.raises(MutationLeaseLost):
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
    attempt = import_attempt(first, owner)
    task_id = attempt.task.task_id
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


def test_import_callback_rollback_keeps_task_and_pointer_before_state(setup):
    first, second, owner, _, scope, _ = setup
    attempt = import_attempt(first, owner)
    authority = PostgresVectorGenerationAuthority(first)
    candidate = authority.stage(scope, attempt, expected_revision=None)
    authority.seal(scope, attempt, candidate, expected_count=1, content_digest=DIGEST)
    def fail(cursor):
        cursor.execute("update users set updated_at='uncommitted' where id=%s", (owner,))
        raise RuntimeError('abort after pointer update')
    with pytest.raises(RuntimeError, match='abort after pointer update'):
        authority.complete_import(scope, attempt, candidate, expected_revision=None,
                                  domain_publish=fail)
    assert PostgresVectorGenerationAuthority(second).read_head(scope).state == 'missing'
    assert authority.reconcile(scope, candidate)['state'] == 'sealed'
    with second.transaction() as cursor:
        task = cursor.execute('select status,stage from import_tasks where id=%s',
                              (attempt.task.task_id,)).fetchone()
        audit = cursor.execute('select ended_at from import_task_attempts where task_id=%s',
                               (attempt.task.task_id,)).fetchone()
        user = cursor.execute('select updated_at from users where id=%s', (owner,)).fetchone()
        live = cursor.execute('''select lease_expires_at>clock_timestamp() as live
            from user_mutation_leases where user_id=%s''', (owner,)).fetchone()
    assert task == {'status': 'running', 'stage': 'committing'}
    assert audit['ended_at'] is None and user['updated_at'] != 'uncommitted'
    assert live['live']


def test_import_task_expiry_after_head_lock_wait_rejects_publication(setup):
    first, second, owner, _, scope, url = setup
    authority = PostgresVectorGenerationAuthority(first)
    direct_lease = lease(first, owner, 'initial')
    original = authority.stage(scope, direct_lease, expected_revision=None)
    authority.seal(scope, direct_lease, original, expected_count=1, content_digest=DIGEST)
    authority.publish_user(scope, direct_lease, original, expected_revision=None)
    assert PostgresUserMutationCoordinator(first).release(direct_lease)
    attempt = import_attempt(first, owner, lease_seconds=3)
    replacement = authority.stage(scope, attempt, expected_revision=1)
    authority.seal(scope, attempt, replacement, expected_count=1, content_digest=DIGEST)
    coordinator = PostgresUserMutationCoordinator(first)
    renewed_user = coordinator.heartbeat(attempt.user_lease, lease_seconds=30)
    assert (renewed_user.lease_token, renewed_user.lease_version) == (
        attempt.user_lease.lease_token, attempt.user_lease.lease_version)
    app_name = 'vector_import_wait_' + uuid4().hex
    waiter_db = PostgresDatabase(url.replace('postgresql+psycopg://', 'postgresql://')
                                 + '&application_name=' + app_name, min_size=1, max_size=1)
    waiter_db.open()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with second.transaction() as blocker:
                blocker.execute('''select revision from vector_heads where tenant_id=%s
                    and vector_kind=%s and namespace=%s and index_key=%s for update''', scope.key)
                future = pool.submit(lambda: PostgresVectorGenerationAuthority(waiter_db).complete_import(
                    scope, attempt, replacement, expected_revision=1))
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    blocked = blocker.execute('''select count(*) as n from pg_stat_activity
                        where application_name=%s and wait_event_type='Lock' ''',
                        (app_name,)).fetchone()['n']
                    if blocked:
                        break
                    time.sleep(0.02)
                assert blocked == 1, 'Import publication did not reach head-row lock wait'
                time.sleep(3.1)
            with pytest.raises(ImportLeaseLost):
                future.result(timeout=5)
    finally:
        waiter_db.close()
    assert authority.read_head(scope).generation_id == original
    assert authority.reconcile(scope, replacement)['state'] == 'sealed'
    with first.transaction() as cursor:
        cursor.execute('begin')
        coordinator.require_live_in_transaction(cursor, attempt.user_lease)
        task = cursor.execute('''select status,stage,
            lease_expires_at<=clock_timestamp() as expired
            from import_tasks where id=%s''',
                              (attempt.task.task_id,)).fetchone()
        audit = cursor.execute('select ended_at from import_task_attempts where task_id=%s',
                               (attempt.task.task_id,)).fetchone()
        user_live = cursor.execute('''select lease_expires_at>clock_timestamp() as live
            from user_mutation_leases where user_id=%s''', (owner,)).fetchone()
    assert (task['status'], task['stage']) == ('running', 'committing')
    assert audit['ended_at'] is None
    assert task['expired'] and user_live['live']


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
    with pytest.raises(pgerrors.RaiseException, match='vector head must reference'):
        with first.transaction() as cursor:
            cursor.execute('''insert into vector_heads
                (tenant_id,vector_kind,namespace,index_key,revision,generation_id,
                 last_generation_id,index_revision) values (%s,%s,%s,%s,1,%s,%s,1)''',
                (*scope.key, generation, generation))
    other_scope = replace(scope, tenant_id=other)
    other_lease = lease(first, other, 'other')
    authority.stage(other_scope, other_lease, expected_revision=None)
    with pytest.raises(pgerrors.ForeignKeyViolation):
        with first.transaction() as cursor:
            cursor.execute('''insert into vector_heads
                (tenant_id,vector_kind,namespace,index_key,revision,generation_id,
                 last_generation_id,index_revision) values (%s,%s,%s,%s,1,%s,%s,1)''',
                (*other_scope.key, generation, generation))
    assert authority.read_head(scope).state == 'missing'
    assert authority.read_head(other_scope).state == 'missing'


@pytest.mark.parametrize('old_count', [0, 1])
def test_read_head_is_coherent_across_replacement(setup, monkeypatch, old_count):
    first, second, owner, _, scope, _ = setup
    writer = PostgresVectorGenerationAuthority(first)
    reader = PostgresVectorGenerationAuthority(second)
    handle = lease(first, owner)
    original = writer.stage(scope, handle, expected_revision=None)
    writer.seal(scope, handle, original, expected_count=old_count, content_digest=DIGEST)
    writer.publish_user(scope, handle, original, expected_revision=None)
    replacement = writer.stage(scope, handle, expected_revision=1)
    writer.seal(scope, handle, replacement, expected_count=1, content_digest=DIGEST)
    fired = after_statement(monkeypatch, second, ('from vector_heads', 'join vector_heads'),
        lambda: writer.publish_user(scope, handle, replacement, expected_revision=1))
    observed = reader.read_head(scope)
    assert fired()
    assert (observed.state, observed.revision, observed.generation_id) == (
        'empty' if old_count == 0 else 'published', 1,
        None if old_count == 0 else original)
    assert writer.read_head(scope).generation_id == replacement


@pytest.mark.parametrize('first_count', [None, 0, 1])
def test_reconcile_is_coherent_across_publication(setup, monkeypatch, first_count):
    first, second, owner, _, scope, _ = setup
    writer = PostgresVectorGenerationAuthority(first)
    reader = PostgresVectorGenerationAuthority(second)
    handle = lease(first, owner)
    if first_count is None:
        candidate = writer.stage(scope, handle, expected_revision=None)
        writer.seal(scope, handle, candidate, expected_count=1, content_digest=DIGEST)
        publish = lambda: writer.publish_user(scope, handle, candidate, expected_revision=None)
        expected = ('sealed', False, None)
    else:
        candidate = writer.stage(scope, handle, expected_revision=None)
        writer.seal(scope, handle, candidate, expected_count=first_count, content_digest=DIGEST)
        writer.publish_user(scope, handle, candidate, expected_revision=None)
        replacement = writer.stage(scope, handle, expected_revision=1)
        writer.seal(scope, handle, replacement, expected_count=1, content_digest=DIGEST)
        publish = lambda: writer.publish_user(scope, handle, replacement, expected_revision=1)
        expected = ('published', True, 1)
    fired = after_statement(monkeypatch, second, 'from vector_generations', publish)
    observed = reader.reconcile(scope, candidate)
    assert fired()
    assert (observed['state'], observed['is_head'], observed['head_revision']) == expected


@pytest.mark.parametrize('count', [0, 1])
def test_retiring_current_generation_without_head_advance_fails(setup, count):
    first, _, owner, _, scope, _ = setup
    authority = PostgresVectorGenerationAuthority(first)
    handle = lease(first, owner)
    candidate = authority.stage(scope, handle, expected_revision=None)
    authority.seal(scope, handle, candidate, expected_count=count, content_digest=DIGEST)
    authority.publish_user(scope, handle, candidate, expected_revision=None)
    with pytest.raises(pgerrors.RaiseException, match='vector head must reference'):
        with first.transaction() as cursor:
            cursor.execute("update vector_generations set state='retired' where generation_id=%s", (candidate,))
    assert authority.reconcile(scope, candidate)['is_head']


@pytest.mark.parametrize('count,digest', [(0, None), (1, None), (None, DIGEST)])
@pytest.mark.parametrize('operation', ['update', 'insert'])
def test_database_rejects_incomplete_sealed_manifest(setup, count, digest, operation):
    first, _, owner, _, scope, _ = setup
    authority = PostgresVectorGenerationAuthority(first)
    handle = lease(first, owner)
    candidate = authority.stage(scope, handle, expected_revision=None)
    with pytest.raises(pgerrors.CheckViolation):
        with first.transaction() as cursor:
            if operation == 'update':
                cursor.execute('''update vector_generations set state='sealed',sealed_at=clock_timestamp(),
                    expected_count=%s,content_digest=%s where generation_id=%s''',
                    (count, digest, candidate))
            else:
                cursor.execute('''insert into vector_generations
                    (generation_id,tenant_id,vector_kind,namespace,index_key,base_revision,
                     index_revision,owner,user_lease_token,user_lease_version,state,
                     sealed_at,expected_count,content_digest)
                    select %s,tenant_id,vector_kind,namespace,index_key,base_revision,
                        index_revision,owner,user_lease_token,user_lease_version,'sealed',
                        clock_timestamp(),%s,%s from vector_generations where generation_id=%s''',
                    (uuid4(), count, digest, candidate))
    assert authority.reconcile(scope, candidate)['state'] == 'staging'


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
