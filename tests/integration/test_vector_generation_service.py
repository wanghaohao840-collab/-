"""Opt-in orchestration tests using disposable PostgreSQL and Qdrant state."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import os
import threading
from uuid import uuid4

import pytest

from app.postgres_coordination import MutationLeaseLost, PostgresUserMutationCoordinator
from app.postgres_import_leases import PostgresImportLeaseRepository
from app.postgres_vector_generations import PostgresVectorGenerationAuthority, VectorScope
from app.import_models import ImportTaskCreate
from app.import_repository import PostgresImportTaskRepository
from app.vector_generation_service import (
    VectorCandidateRejected, VectorGenerationService, VectorPublicationUnknown,
)
from hello_agents.memory.rag.embedding_profile import EmbeddingProfile
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.storage.vector_store import QdrantVectorStore, VectorPoint
from tests.integration.test_postgres_auth_sessions import shared_database


@pytest.fixture
def setup(shared_database):
    qdrant_url = os.environ.get('GENERATION_QDRANT_TEST_URL')
    if not qdrant_url:
        pytest.skip('GENERATION_QDRANT_TEST_URL is required')
    open_pool, _ = shared_database
    first, second = open_pool(), open_pool()
    users = (str(uuid4()), str(uuid4()))
    with first.transaction() as cursor:
        for user in users:
            cursor.execute('insert into users values (%s,%s,%s,%s,%s,%s,%s)',
                           (user, user, user, 'hash', 'active', 'now', 'now'))
    raw = QdrantVectorStore(url=qdrant_url, retry_delays=())
    identity = IndexIdentity('qdrant', 'orchestrator_' + uuid4().hex,
                             EmbeddingProfile('simple', '', 'deterministic', 'v1', 4))
    raw.ensure_collection(identity.physical_collection, 4)
    scope = VectorScope(users[0], 'rag', 'documents', identity)
    try:
        yield (VectorGenerationService(PostgresVectorGenerationAuthority(first), raw),
               VectorGenerationService(PostgresVectorGenerationAuthority(second), raw),
               first, second, raw, scope, users)
    finally:
        raw.client.delete_collection(identity.physical_collection)


def point(marker):
    return VectorPoint('same-logical-id', [1.0, 0.0, 0.0, 0.0], {'marker': marker})


def lease(db, user, owner='worker'):
    result = PostgresUserMutationCoordinator(db).acquire(user, owner, lease_seconds=60)
    assert result is not None
    return result


def import_attempt(db, user, worker='import-worker'):
    task_id, batch_id = str(uuid4()), str(uuid4())
    digest = hashlib.sha256(b'source').hexdigest()
    with db.transaction() as cursor:
        now = cursor.execute('select clock_timestamp() as now').fetchone()['now'].isoformat()
        PostgresImportTaskRepository(db).create_batch_in_transaction(
            cursor, user,
            [ImportTaskCreate(task_id, batch_id, user, str(uuid4()),
                              'a.txt', '.txt', 1, '')], now=now)
        cursor.execute('''insert into import_objects
            (task_id,user_id,bucket,object_key,version_id,sha256,size_bytes)
            values (%s,%s,'test','object','v1',%s,1)''', (task_id, user, digest))
    attempt = PostgresImportLeaseRepository(db).claim_next(worker)
    assert attempt is not None and attempt.task.task_id == task_id
    return attempt


def test_publication_pinned_read_empty_and_tenant_isolation(setup):
    writer, reader, first, second, _, scope, users = setup
    handle = lease(first, users[0])
    initial = writer.authority.read_head(scope)
    with pytest.raises(Exception, match='Missing or invalid published vector head'):
        reader.read_view(scope)
    old = writer.publish_complete(scope, handle, initial, [point('old')])
    pinned = reader.read_view(scope)
    assert (old.head.revision, pinned.count(scope.identity.physical_collection)) == (1, 1)
    assert pinned.scroll(scope.identity.physical_collection)[0].payload['marker'] == 'old'

    second_user = replace(scope, tenant_id=users[1])
    other = writer.publish_complete(second_user, lease(first, users[1], 'other'),
                                    writer.authority.read_head(second_user), [point('other')])
    assert other.head.revision == 1
    empty = writer.publish_complete(scope, handle, old.head, [])
    assert empty.head.state == 'empty' and empty.head.revision == 2
    assert pinned.count(scope.identity.physical_collection) == 1
    assert pinned.scroll(scope.identity.physical_collection)[0].payload['marker'] == 'old'
    fresh = reader.read_view(scope)
    assert fresh.count(scope.identity.physical_collection) == 0
    assert fresh.scroll(scope.identity.physical_collection) == []
    assert reader.read_view(second_user).scroll(scope.identity.physical_collection)[0].payload['marker'] == 'other'
    assert PostgresVectorGenerationAuthority(second).read_head(scope) == empty.head


def test_unknown_qdrant_write_is_abandoned_and_exact_cleanup_is_safe(setup, monkeypatch):
    writer, reader, first, _, raw, scope, users = setup
    handle = lease(first, users[0])
    original = raw.client.upsert
    calls = []

    def lost_response(*args, **kwargs):
        calls.append(kwargs['points'][0].id)
        original(*args, **kwargs)  # server applied it, response was lost
        raise TimeoutError('response lost')

    monkeypatch.setattr(raw.client, 'upsert', lost_response)
    with pytest.raises(VectorCandidateRejected) as captured:
        writer.publish_complete(scope, handle, writer.authority.read_head(scope), [point('unknown')])
    abandoned = captured.value.generation_id
    assert len(calls) == 1
    assert writer.authority.reconcile(scope, abandoned)['state'] == 'abandoned'
    assert reader.authority.read_head(scope).state == 'missing'
    monkeypatch.setattr(raw.client, 'upsert', original)

    entered, release = threading.Event(), threading.Event()
    original_delete = raw.delete_by_filter

    def delayed_delete(*args, **kwargs):
        entered.set()
        assert release.wait(15)
        return original_delete(*args, **kwargs)

    monkeypatch.setattr(raw, 'delete_by_filter', delayed_delete)
    with ThreadPoolExecutor(max_workers=1) as pool:
        cleanup = pool.submit(writer.cleanup_abandoned, scope, abandoned)
        assert entered.wait(15), 'Exact cleanup did not pause before Qdrant delete'
        visible = writer.publish_complete(scope, handle, writer.authority.read_head(scope),
                                          [point('visible')])
        other_scope = replace(scope, tenant_id=users[1])
        writer.publish_complete(other_scope, lease(first, users[1], 'other'),
                                writer.authority.read_head(other_scope), [point('other')])
        release.set()
        assert cleanup.result(timeout=15) == 1
    assert reader.read_view(scope).scroll(scope.identity.physical_collection)[0].payload['marker'] == 'visible'
    assert reader.read_view(other_scope).scroll(scope.identity.physical_collection)[0].payload['marker'] == 'other'
    assert writer.authority.reconcile(scope, visible.generation_id)['is_head']


def test_unknown_write_with_lost_lease_is_quarantined(setup, monkeypatch):
    writer, _, first, _, raw, scope, users = setup
    handle = lease(first, users[0])
    original = raw.client.upsert
    submitted = []

    def late_unknown(*args, **kwargs):
        submitted.append(kwargs['points'][0].id)
        original(*args, **kwargs)
        with first.transaction() as cursor:
            cursor.execute("""update user_mutation_leases set
                lease_expires_at=clock_timestamp()-interval '1 second' where user_id=%s""",
                           (users[0],))
        raise TimeoutError('server applied bytes but ownership expired')

    monkeypatch.setattr(raw.client, 'upsert', late_unknown)
    with pytest.raises(VectorPublicationUnknown) as captured:
        writer.publish_complete(scope, handle, writer.authority.read_head(scope), [point('late')])
    assert len(submitted) == 1
    assert captured.value.phase == 'abandonment'
    assert writer.authority.reconcile(scope, captured.value.generation_id)['state'] == 'staging'
    assert writer.authority.read_head(scope).state == 'missing'


def test_pre_server_delayed_upsert_after_lease_recovery_cannot_publish(setup, monkeypatch):
    writer, reader, first, _, raw, scope, users = setup
    old = lease(first, users[0], 'old')
    entered, release = threading.Event(), threading.Event()
    original = raw.client.upsert

    def delayed(*args, **kwargs):
        if threading.current_thread().name.startswith('stale-upload'):
            entered.set()
            assert release.wait(15)
        return original(*args, **kwargs)

    monkeypatch.setattr(raw.client, 'upsert', delayed)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='stale-upload') as pool:
        future = pool.submit(writer.publish_complete, scope, old,
                             writer.authority.read_head(scope), [point('stale')])
        assert entered.wait(15), 'A did not pause before raw Qdrant upsert'
        with first.transaction() as cursor:
            cursor.execute("""update user_mutation_leases set
                lease_expires_at=clock_timestamp()-interval '1 second' where user_id=%s""",
                           (users[0],))
        current = lease(first, users[0], 'new')
        published = writer.publish_complete(scope, current,
                                             writer.authority.read_head(scope), [point('current')])
        release.set()
        with pytest.raises(VectorPublicationUnknown) as captured:
            future.result(timeout=15)
    stale = captured.value.generation_id
    assert writer.authority.reconcile(scope, stale)['state'] == 'staging'
    assert writer.authority.reconcile(scope, published.generation_id)['is_head']
    view = reader.read_view(scope)
    collection = scope.identity.physical_collection
    assert view.count(collection) == 1
    assert view.scroll(collection)[0].payload['marker'] == 'current'
    assert view.search(collection, [1.0, 0.0, 0.0, 0.0])[0].payload['marker'] == 'current'


def test_pg_commit_response_loss_reconciles_and_callback_rollback(setup, monkeypatch):
    writer, reader, first, _, _, scope, users = setup
    handle = lease(first, users[0])
    original = writer.authority.publish_user

    def commit_then_lose(*args, **kwargs):
        original(*args, **kwargs)
        raise ConnectionError('commit response lost')

    monkeypatch.setattr(writer.authority, 'publish_user', commit_then_lose)
    initial = writer.authority.read_head(scope)
    committed = writer.publish_complete(scope, handle, initial, [point('committed')])
    assert committed.head.revision == 1
    assert reader.read_view(scope).scroll(scope.identity.physical_collection)[0].payload['marker'] == 'committed'
    monkeypatch.setattr(writer.authority, 'publish_user', original)

    def rollback(cursor):
        cursor.execute("update users set updated_at='rolled-back' where id=%s", (users[0],))
        raise RuntimeError('rollback domain publication')

    with pytest.raises(RuntimeError, match='rollback domain publication'):
        writer.publish_complete(scope, handle, committed.head, [point('new')],
                                domain_publish=rollback)
    assert reader.authority.read_head(scope) == committed.head
    assert reader.read_view(scope).scroll(scope.identity.physical_collection)[0].payload['marker'] == 'committed'
    with first.transaction() as cursor:
        row = cursor.execute('select updated_at from users where id=%s', (users[0],)).fetchone()
    assert row['updated_at'] != 'rolled-back'


def test_unconfirmed_pg_publication_is_explicitly_quarantined(setup, monkeypatch):
    writer, reader, first, _, _, scope, users = setup
    handle = lease(first, users[0])

    def response_lost(*args, **kwargs):
        raise ConnectionError('publisher outcome unknown')

    monkeypatch.setattr(writer.authority, 'publish_user', response_lost)
    with pytest.raises(VectorPublicationUnknown) as captured:
        writer.publish_complete(scope, handle, writer.authority.read_head(scope),
                                [point('sealed-only')])
    assert captured.value.phase == 'publication'
    assert writer.authority.reconcile(scope, captured.value.generation_id)['state'] == 'sealed'
    assert reader.authority.read_head(scope).state == 'missing'


def test_import_completion_and_callback_rollback_share_pg_transaction(setup):
    writer, reader, first, _, _, scope, users = setup
    attempt = import_attempt(first, users[0])
    task_id = attempt.task.task_id

    def rollback(cursor):
        cursor.execute("update users set updated_at='rolled-back' where id=%s", (users[0],))
        raise RuntimeError('domain rollback')

    initial = writer.authority.read_head(scope)
    with pytest.raises(RuntimeError, match='domain rollback'):
        writer.publish_complete(scope, attempt, initial, [point('attempt')],
                                domain_publish=rollback)
    assert reader.authority.read_head(scope).state == 'missing'
    with first.transaction() as cursor:
        row = cursor.execute('select status,stage from import_tasks where id=%s',
                             (task_id,)).fetchone()
        audit = cursor.execute('select ended_at from import_task_attempts where task_id=%s',
                               (task_id,)).fetchone()
        user = cursor.execute('select updated_at from users where id=%s', (users[0],)).fetchone()
    assert row == {'status': 'running', 'stage': 'committing'}
    assert audit['ended_at'] is None and user['updated_at'] != 'rolled-back'

    # A new candidate cannot use an already-entered committing stage.
    with pytest.raises(VectorCandidateRejected):
        writer.publish_complete(scope, attempt, writer.authority.read_head(scope),
                                [point('retry')])


def test_import_commit_response_loss_reconciles_task_and_head(setup, monkeypatch):
    writer, reader, first, _, _, scope, users = setup
    attempt = import_attempt(first, users[0])
    original = writer.authority.complete_import

    def commit_then_lose(*args, **kwargs):
        original(*args, **kwargs)
        raise ConnectionError('commit response lost')

    monkeypatch.setattr(writer.authority, 'complete_import', commit_then_lose)
    result = writer.publish_complete(scope, attempt, writer.authority.read_head(scope),
                                     [point('imported')])
    assert result.import_task.task_id == attempt.task.task_id
    assert result.import_task.status == 'succeeded'
    assert reader.authority.read_head(scope) == result.head
    assert reader.read_view(scope).scroll(scope.identity.physical_collection)[0].payload['marker'] == 'imported'


@pytest.mark.parametrize('lost_response', [False, True])
@pytest.mark.parametrize('interleave', ['before_receipt', 'after_receipt_query'])
def test_historical_import_receipt_survives_later_head(
        setup, monkeypatch, lost_response, interleave):
    writer, reader, first, second, raw, scope, users = setup
    a = import_attempt(first, users[0], 'worker-a')
    original_complete = writer.authority.complete_import
    original_receipt = writer.authority.publication_receipt
    events = []
    b_result = []

    def publish_b():
        assert not b_result
        current = reader.authority.read_head(scope)
        assert current.revision == 1
        b_lease = lease(second, users[0], 'worker-b')
        b_result.append(reader.publish_complete(scope, b_lease, current, [point('B')]))

    def complete_a(*args, **kwargs):
        result = original_complete(*args, **kwargs)
        if interleave == 'before_receipt':
            publish_b()
        if lost_response:
            raise ConnectionError('A commit response lost')
        return result

    def receipt_a(*args, **kwargs):
        row = original_receipt(*args, **kwargs)
        if interleave == 'after_receipt_query' and not b_result:
            publish_b()  # B commits after A's coherent SQL read, before A returns.
        return row

    monkeypatch.setattr(writer.authority, 'complete_import', complete_a)
    monkeypatch.setattr(writer.authority, 'publication_receipt', receipt_a)

    def domain(cursor):
        events.append('A')
        cursor.execute("update users set updated_at='A-committed' where id=%s", (users[0],))

    a_result = writer.publish_complete(scope, a, writer.authority.read_head(scope),
                                       [point('A')], domain_publish=domain,
                                       snapshot_version=7)
    assert events == ['A'] and len(b_result) == 1
    assert (a_result.head.revision, a_result.head.snapshot_version,
            a_result.head.generation_id) == (1, 7, a_result.generation_id)
    assert a_result.import_task.task_id == a.task.task_id
    assert a_result.import_task.status == 'succeeded'
    assert writer.authority.reconcile(scope, a_result.generation_id)['state'] == 'retired'
    assert reader.authority.read_head(scope) == b_result[0].head
    collection = scope.identity.physical_collection
    assert raw.count(collection, {'_gv_generation': str(a_result.generation_id)}) == 1
    assert raw.count(collection, {'_gv_generation': str(b_result[0].generation_id)}) == 1
    assert reader.read_view(scope).scroll(collection)[0].payload['marker'] == 'B'
    with first.transaction() as cursor:
        user = cursor.execute('select updated_at from users where id=%s', (users[0],)).fetchone()
    assert user['updated_at'] == 'A-committed'


def test_database_receipt_cannot_be_inserted_retired_or_edited(setup):
    writer, _, first, _, _, scope, users = setup
    handle = lease(first, users[0])
    result = writer.publish_complete(scope, handle, writer.authority.read_head(scope),
                                     [point('committed')], snapshot_version=3)
    with pytest.raises(Exception, match='vector publication receipt is immutable'):
        with first.transaction() as cursor:
            cursor.execute('''update vector_generations
                set publication_snapshot_version=4 where generation_id=%s''',
                (result.generation_id,))
    with pytest.raises(Exception, match='vector publication requires a sealed transition'):
        with first.transaction() as cursor:
            cursor.execute('''insert into vector_generations
                (generation_id,tenant_id,vector_kind,namespace,index_key,base_revision,
                 index_revision,owner,user_lease_token,user_lease_version,state,
                 sealed_at,expected_count,content_digest,published_at,
                 publication_revision,publication_snapshot_version)
                select %s,tenant_id,vector_kind,namespace,index_key,base_revision,
                    index_revision,owner,user_lease_token,user_lease_version,'retired',
                    sealed_at,expected_count,content_digest,published_at,
                    publication_revision,publication_snapshot_version
                from vector_generations where generation_id=%s''',
                (uuid4(), result.generation_id))


def test_late_import_upsert_after_exact_cleanup_and_task_reclaim_is_invisible(setup, monkeypatch):
    writer, reader, first, _, raw, scope, users = setup
    a = import_attempt(first, users[0], 'old-worker')
    entered, release = threading.Event(), threading.Event()
    original = raw.client.upsert

    def delayed(*args, **kwargs):
        if threading.current_thread().name.startswith('old-worker-upload'):
            entered.set()
            assert release.wait(15)
        return original(*args, **kwargs)

    monkeypatch.setattr(raw.client, 'upsert', delayed)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='old-worker-upload') as pool:
        future = pool.submit(writer.publish_complete, scope, a,
                             writer.authority.read_head(scope), [point('stale')])
        assert entered.wait(15), 'A did not pause before server application'
        with first.transaction() as cursor:
            row = cursor.execute('''select generation_id from vector_generations
                where tenant_id=%s and state='staging' ''', (users[0],)).fetchone()
        a_generation = row['generation_id']
        writer.authority.abandon(scope, a, a_generation)
        assert writer.cleanup_abandoned(scope, a_generation) == 0
        with first.transaction() as cursor:
            cursor.execute("""update import_tasks set
                lease_expires_at=clock_timestamp()-interval '1 second' where id=%s""",
                           (a.task.task_id,))
        imports = PostgresImportLeaseRepository(first)
        assert imports.recover_expired() == 1
        b = imports.claim_next('new-worker')
        assert b is not None and b.task.task_id == a.task.task_id
        assert b.lease_version == a.lease_version + 1
        published = writer.publish_complete(scope, b, writer.authority.read_head(scope),
                                             [point('current')])
        assert published.import_task.status == 'succeeded'
        release.set()
        with pytest.raises(VectorCandidateRejected) as captured:
            future.result(timeout=15)
    assert captured.value.generation_id == a_generation
    assert writer.authority.reconcile(scope, a_generation)['state'] == 'abandoned'
    assert writer.authority.reconcile(scope, published.generation_id)['is_head']
    collection = scope.identity.physical_collection
    assert raw.count(collection, {'_gv_generation': str(a_generation)}) == 1
    view = reader.read_view(scope)
    assert view.count(collection) == 1
    assert view.scroll(collection)[0].payload['marker'] == 'current'
    assert view.search(collection, [1.0, 0.0, 0.0, 0.0])[0].payload['marker'] == 'current'


def test_ambiguous_seal_requires_complete_owner_and_scope_receipt(setup, monkeypatch):
    writer, _, first, _, _, scope, users = setup
    handle = lease(first, users[0])
    original_seal = writer.authority.seal
    original_receipt = writer.authority.publication_receipt

    def sealed_then_lost(*args, **kwargs):
        original_seal(*args, **kwargs)
        raise ConnectionError('seal response lost')

    def wrong_owner(*args, **kwargs):
        return original_receipt(*args, **kwargs) | {'owner': 'wrong-owner'}

    monkeypatch.setattr(writer.authority, 'seal', sealed_then_lost)
    monkeypatch.setattr(writer.authority, 'publication_receipt', wrong_owner)
    with pytest.raises(VectorPublicationUnknown) as captured:
        writer.publish_complete(scope, handle, writer.authority.read_head(scope),
                                [point('candidate')])
    assert captured.value.phase == 'seal'
    assert writer.authority.reconcile(scope, captured.value.generation_id)['state'] == 'sealed'
    assert writer.authority.read_head(scope).state == 'missing'


def test_stale_expected_head_is_rejected_before_stage_without_abandonment(setup, monkeypatch):
    writer, _, first, _, _, scope, users = setup
    handle = lease(first, users[0])
    stale = writer.authority.read_head(scope)
    writer.publish_complete(scope, handle, stale, [point('current')])
    calls = []

    def forbidden(*args, **kwargs):
        calls.append('called')
        raise AssertionError('No candidate may be staged or abandoned')

    monkeypatch.setattr(writer.authority, 'stage', forbidden)
    monkeypatch.setattr(writer.authority, 'abandon', forbidden)
    with pytest.raises(Exception, match='Expected vector head changed'):
        writer.publish_complete(scope, handle, stale, [point('stale')])
    assert calls == []


def test_expired_direct_user_lease_stage_keeps_original_exception_and_no_candidate(setup):
    writer, _, first, _, _, scope, users = setup
    handle = lease(first, users[0])
    with first.transaction() as cursor:
        cursor.execute("""update user_mutation_leases set
            lease_expires_at=clock_timestamp()-interval '1 second' where user_id=%s""",
                       (users[0],))
    with pytest.raises(MutationLeaseLost):
        writer.publish_complete(scope, handle, writer.authority.read_head(scope),
                                [point('never-staged')])
    assert writer.authority.read_head(scope).state == 'missing'
    with first.transaction() as cursor:
        count = cursor.execute('''select count(*) as n from vector_generations
            where tenant_id=%s and vector_kind=%s and namespace=%s and index_key=%s''',
                               scope.key).fetchone()['n']
    assert count == 0
