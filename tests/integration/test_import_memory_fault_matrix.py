"""Packet D: real-service terminal failure and race acceptance for import Memory."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from copy import deepcopy
import json
import threading
import time
from uuid import uuid4

import pytest

from app.import_memory_publication import (
    ImportMemoryPublicationUnknown, _receipt_projection,
)
from app.postgres_import_leases import ImportLeaseLost, PostgresImportLeaseRepository
from app.postgres_memory_documents import PostgresMemoryDocumentStore
from app.object_store import ObjectRef
from tests.integration.test_import_document_publication import (
    _point, _publish_strict_legacy_document, _task,
)
from tests.integration.test_import_memory_publication import _mixed_baseline, memory_publication
from tests.integration.test_postgres_auth_sessions import shared_database
from tests.integration.test_import_document_publication import publication
from tests.integration.test_s3_object_store import store


@pytest.fixture(autouse=True)
def _resource_ledger(request, memory_publication):
    service, db, store, _, _, rag_scope, episode_scope, _, user, _ = memory_publication
    yield
    with db.transaction() as cursor:
        schema = cursor.execute('select current_schema() as schema').fetchone()['schema']
        rows = cursor.execute('''select generation_id,task_id,vector_kind,state,
            expected_count,content_digest from vector_generations where tenant_id=%s
            order by vector_kind,generation_id''', (user,)).fetchall()
    ledger = {'schema': schema, 'bucket': store.bucket,
              'rag_collection': rag_scope.identity.physical_collection,
              'episode_collection': episode_scope.identity.physical_collection,
              'generations': [dict(row) for row in rows]}
    request.node.user_properties.append(('resource_ledger', json.dumps(ledger, default=str)))


def _publish(service, rag_scope, profile, attempt):
    return service.publish(
        rag_scope, attempt, [_point(rag_scope, attempt.task.document_id, 'D-payload')],
        event_vector=[1., 0., 0., 0.], event_profile=profile)


def _state(db, rag_scope, episode_scope, user, task_id):
    """Read all PostgreSQL authorities that a failed completion could expose."""
    scopes = (rag_scope, episode_scope)
    with db.transaction() as cursor:
        heads = []
        old_ids = []
        for scope in scopes:
            head = cursor.execute('''select * from vector_heads where tenant_id=%s
                and vector_kind=%s and namespace=%s and index_key=%s''', scope.key).fetchone()
            assert head is not None
            heads.append(dict(head))
            old_ids.append(head['last_generation_id'])
        old_receipts = [dict(cursor.execute('''select * from vector_generations
            where generation_id=%s''', (generation_id,)).fetchone())
            for generation_id in old_ids]
        snapshots = [dict(row) for row in cursor.execute('''select *
            from user_snapshots where user_id=%s and kind in ('history','memory')
            order by kind''', (user,)).fetchall()]
        rows = [dict(row) for row in cursor.execute('''select * from memory_documents
            where user_id=%s order by document_id''', (user,)).fetchall()]
        refs = [dict(row) for row in cursor.execute('''select * from document_objects
            where user_id=%s order by document_id''', (user,)).fetchall()]
        witnesses = [dict(row) for row in cursor.execute('''select * from
            history_document_witnesses where tenant_id=%s and vector_kind=%s
            and namespace=%s and index_key=%s order by head_revision''',
            rag_scope.key).fetchall()]
        task = dict(cursor.execute('''select * from import_tasks where id=%s''',
                                   (task_id,)).fetchone())
        batch = dict(cursor.execute('''select * from import_batches where id=%s''',
                                    (task['batch_id'],)).fetchone())
        audit = [dict(row) for row in cursor.execute('''select * from import_task_attempts
            where task_id=%s order by lease_version''', (task_id,)).fetchall()]
        lease = dict(cursor.execute('''select * from user_mutation_leases
            where user_id=%s''', (user,)).fetchone())
    return {
        'heads': heads, 'old_receipts': old_receipts, 'snapshots': snapshots,
        'rows': rows, 'refs': refs, 'witnesses': witnesses,
        'task': task, 'batch': batch, 'audit': audit, 'lease': lease,
    }


def _assert_rolled_back(before, after, service, rag_scope, episode_scope):
    for key in ('heads', 'old_receipts', 'snapshots', 'rows', 'refs',
                'witnesses', 'lease', 'task', 'batch', 'audit'):
        assert after[key] == before[key], key
    assert after['task']['stage'] == 'committing'
    assert after['task']['status'] != 'succeeded'
    assert len(after['audit']) == len(before['audit']) == 1
    assert after['audit'][0]['ended_at'] is None
    assert service.pair.rag.read_view(rag_scope).count(
        rag_scope.identity.physical_collection) == before['old_receipts'][0]['expected_count']
    assert service.pair.episode.read_view(episode_scope).count(
        episode_scope.identity.physical_collection) == before['old_receipts'][1]['expected_count']


def _public_members(service, rag_scope, episode_scope):
    result = []
    for vector_service, scope in ((service.pair.rag, rag_scope),
                                  (service.pair.episode, episode_scope)):
        points = vector_service.read_view(scope).scroll(
            scope.identity.physical_collection, with_vectors=True)
        result.append(sorted((p.id, p.vector, p.payload) for p in points))
    return result


def _assert_committed(service, db, rag_scope, episode_scope, attempt, result):
    expected = result._expected
    context = result._context
    state = _state(db, rag_scope, episode_scope, attempt.task.user_id,
                   attempt.task.task_id)
    assert state['task']['status'] == state['task']['stage'] == 'succeeded'
    assert len(state['audit']) == 1
    task, audit = state['task'], state['audit'][0]
    assert (task['id'], task['user_id'], task['claimed_by'], task['lease_token'],
            task['lease_version'], task['user_lease_token'], task['user_lease_version']) == (
        attempt.task.task_id, attempt.task.user_id, attempt.worker_id,
        attempt.lease_token, attempt.lease_version,
        attempt.user_lease.lease_token, attempt.user_lease.lease_version)
    assert (audit['task_id'], audit['user_id'], audit['worker_id'],
            audit['lease_token'], audit['lease_version'], audit['user_lease_token'],
            audit['user_lease_version'], audit['end_reason'], audit['last_stage']) == (
        attempt.task.task_id, attempt.task.user_id, attempt.worker_id,
        attempt.lease_token, attempt.lease_version,
        attempt.user_lease.lease_token, attempt.user_lease.lease_version,
        'succeeded', 'succeeded')
    assert audit['ended_at'] is not None
    assert [head['last_generation_id'] for head in state['heads']] == list(context.generation_ids)
    assert [head['generation_id'] for head in state['heads']] == list(context.generation_ids)
    assert [head['snapshot_version'] for head in state['heads']] == [
        expected.history_version, expected.memory_version]
    assert [(row['kind'], row['version'], row['payload']) for row in state['snapshots']] == [
        ('history', expected.history_version, expected.history),
        ('memory', expected.memory_version, expected.memory)]
    with db.transaction() as cursor:
        candidates = [dict(cursor.execute('select * from vector_generations where generation_id=%s',
                                  (generation_id,)).fetchone()) for generation_id in context.generation_ids]
    assert expected.new_receipts is not None and len(expected.new_receipts) == 2
    for row, plan, head, receipt in zip(candidates, (context.rag, context.episode),
                                        state['heads'], expected.new_receipts):
        assert row['state'] == 'published'
        assert row['task_id'] == attempt.task.task_id
        assert (row['owner'], row['user_lease_token'], row['user_lease_version'],
                row['task_lease_token'], row['task_lease_version']) == (
            attempt.worker_id, attempt.user_lease.lease_token,
            attempt.user_lease.lease_version, attempt.lease_token,
            attempt.lease_version)
        assert _receipt_projection(row) == receipt
        assert (row['base_revision'], row['index_revision']) == (
            plan.sealed.expected_head.revision, plan.sealed.expected_index_revision)
        assert row['publication_revision'] == head['revision']
        assert row['publication_snapshot_version'] == head['snapshot_version']
        assert head['index_revision'] == plan.sealed.expected_index_revision
        assert head['revision'] == plan.sealed.expected_head.revision + 1
        assert row['expected_count'] == plan.sealed.expected_count
        assert row['content_digest'] == plan.sealed.content_digest
    assert len([row for row in state['rows'] if row['document_id'] == expected.event_id]) == 1
    event = next(row for row in state['rows'] if row['document_id'] == expected.event_id)
    assert event['content'] == expected.item['content']
    assert json.loads(event['metadata']) == expected.event_metadata
    ref = next(row for row in state['refs'] if row['document_id'] == attempt.task.document_id)
    assert (attempt.task.user_id, attempt.task.document_id, ref['bucket'],
            ref['object_key'], ref['version_id'], ref['sha256'], ref['size_bytes']) == expected.ref
    assert service.documents.store.read_verified(attempt.task.user_id,
        ObjectRef(ref['object_key'], ref['sha256'], ref['size_bytes'], ref['version_id'])) == (
            service.documents.store.read_verified(attempt.task.user_id, attempt.source))
    witness = next(row for row in state['witnesses'] if row['head_revision'] ==
                   state['heads'][0]['revision'])
    assert witness['last_generation_id'] == context.rag.generation_id
    assert witness['index_revision'] == state['heads'][0]['index_revision']
    assert witness['publication_snapshot_version'] == expected.history_version
    assert witness['document_count'] == expected.new_evidence.count
    assert witness['documents_sha256'] == expected.new_evidence.digest
    public = _public_members(service, rag_scope, episode_scope)
    assert public == [sorted((point.id, point.vector, point.payload)
                             for point in plan.complete_corpus)
                      for plan in (context.rag, context.episode)]


def _after_one(monkeypatch, owner, method, seen, *, kind=None):
    original = getattr(owner, method)

    def injected(*args, **kwargs):
        result = original(*args, **kwargs)
        if kind is None or kind in args:
            seen.append(method)
            raise RuntimeError('injected after SQL write: ' + method)
        return result

    monkeypatch.setattr(owner, method, injected)


def _deadline(db, user, task_id, seconds=3):
    with db.transaction() as cursor:
        deadline = cursor.execute('''select clock_timestamp() + %s * interval '1 second'
            as deadline''', (seconds,)).fetchone()['deadline']
        cursor.execute('update user_mutation_leases set lease_expires_at=%s where user_id=%s',
                       (deadline, user))
        cursor.execute('update import_tasks set lease_expires_at=%s where id=%s',
                       (deadline, task_id))
    return deadline


def _wait_past(db, deadline, *, blocker_pid=None, timeout=12):
    end = time.monotonic() + timeout
    observed_block = False
    while time.monotonic() < end:
        with db.transaction() as cursor:
            row = cursor.execute('select clock_timestamp() as now').fetchone()
            if blocker_pid is not None:
                blocked = cursor.execute('''select pid from pg_stat_activity
                    where datname=current_database() and wait_event_type='Lock'
                    and %s=any(pg_blocking_pids(pid))''', (blocker_pid,)).fetchall()
                if blocked and not observed_block:
                    assert row['now'] < deadline, 'worker first blocked after lease expiry'
                    observed_block = True
        if row['now'] > deadline and (blocker_pid is None or observed_block):
            return observed_block
        time.sleep(0.05)
    raise AssertionError('Actual PostgreSQL clock or intended lock wait was not observed')


def _lock_row(cursor, site, user, task_id, rag_scope, episode_scope):
    if site == 'user':
        query, key = 'select id from users where id=%s for update', (user,)
    elif site == 'lease':
        query, key = 'select user_id from user_mutation_leases where user_id=%s for update', (user,)
    elif site == 'task':
        query, key = 'select id from import_tasks where id=%s for update', (task_id,)
    elif site == 'audit':
        query, key = ('select task_id from import_task_attempts where task_id=%s '
                      'and lease_version=1 for update'), (task_id,)
    else:
        scope = rag_scope if site.startswith('rag') else episode_scope
        if site.endswith('head'):
            query, key = ('''select tenant_id from vector_heads where tenant_id=%s
                and vector_kind=%s and namespace=%s and index_key=%s for update'''), scope.key
        else:
            query, key = ('''select generation_id from vector_generations where task_id=%s
                and tenant_id=%s and vector_kind=%s and namespace=%s and index_key=%s
                and state='sealed' for update'''), (task_id, *scope.key)
    assert cursor.execute(query, key).fetchone() is not None, site


@pytest.mark.parametrize('site', ('user', 'lease', 'task', 'audit',
    'rag_head', 'rag_candidate', 'episode_head', 'episode_candidate'))
def test_expiry_after_real_completion_lock_wait(memory_publication, shared_database,
                                                monkeypatch, site):
    service, db, store, _, _, rag_scope, episode_scope, profile, user, _ = memory_publication
    holder = shared_database[0]()
    observer = shared_database[0]()
    attempt = _task(db, store, user)
    entry, release = threading.Event(), threading.Event()
    before = []
    original = service.pair.imports.complete
    def gated(*args, **kwargs):
        entry.set()
        assert release.wait(15), 'completion entry barrier timed out'
        return original(*args, **kwargs)
    monkeypatch.setattr(service.pair.imports, 'complete', gated)
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_publish, service, rag_scope, profile, attempt)
    worker_error = None
    try:
        assert entry.wait(30), 'both candidate seals never reached complete'
        with db.transaction() as cursor:
            states = cursor.execute('''select vector_kind,state from vector_generations
                where task_id=%s order by vector_kind''', (attempt.task.task_id,)).fetchall()
            assert [(r['vector_kind'], r['state']) for r in states] == [
                ('episode', 'sealed'), ('rag', 'sealed')]
            assert cursor.execute('select stage from import_tasks where id=%s',
                (attempt.task.task_id,)).fetchone()['stage'] == 'committing'
        before_public = _public_members(service, rag_scope, episode_scope)
        deadline = _deadline(db, user, attempt.task.task_id)
        before.append(_state(db, rag_scope, episode_scope, user, attempt.task.task_id))
        with holder.transaction() as cursor:
            blocker_pid = cursor.execute('select pg_backend_pid() as pid').fetchone()['pid']
            _lock_row(cursor, site, user, attempt.task.task_id, rag_scope, episode_scope)
            release.set()
            assert _wait_past(observer, deadline, blocker_pid=blocker_pid)
    finally:
        release.set()
        try:
            future.result(timeout=20)
        except BaseException as error:
            worker_error = error
        executor.shutdown(wait=False, cancel_futures=True)
    assert isinstance(worker_error, (ImportLeaseLost, ImportMemoryPublicationUnknown)), worker_error
    if isinstance(worker_error, ImportMemoryPublicationUnknown):
        assert isinstance(worker_error.__cause__.__cause__, ImportLeaseLost)
    assert len(before) == 1
    _assert_rolled_back(before[0], _state(db, rag_scope, episode_scope,
                        user, attempt.task.task_id), service, rag_scope, episode_scope)
    assert _public_members(service, rag_scope, episode_scope) == before_public


@pytest.mark.parametrize('site', (
    'rag_head', 'episode_head', 'history_cas', 'memory_cas',
    'episode_row', 'document_ref', 'new_witness', 'old_witness', 'finish',
))
def test_every_terminal_write_fault_rolls_back_all_authorities(
        memory_publication, monkeypatch, site):
    service, db, _, _, _, rag_scope, episode_scope, profile, user, _ = memory_publication
    _mixed_baseline(service, db, episode_scope, user)
    # The strict old RAG publication has a pinned object and no witness;
    # every case must preserve both it and the nonempty Memory corpus.
    old_id = _publish_strict_legacy_document((service.documents, db,
        service.documents.store, service.pair.rag, service.documents.snapshots,
        rag_scope, user, str(uuid4())))
    old_bytes = service.documents.documents.read_document_bytes(user, old_id)
    attempt = _task(db, service.documents.store, user)
    before = []
    before_public = _public_members(service, rag_scope, episode_scope)
    seen = []
    if site == 'rag_head':
        _after_one(monkeypatch, service.pair.rag.authority, '_publish', seen)
    elif site == 'episode_head':
        _after_one(monkeypatch, service.pair.episode.authority, '_publish', seen)
    elif site in ('history_cas', 'memory_cas'):
        _after_one(monkeypatch, service.documents.snapshots,
                   'compare_and_swap_in_transaction', seen,
                   kind='history' if site == 'history_cas' else 'memory')
    elif site == 'episode_row':
        _after_one(monkeypatch, PostgresMemoryDocumentStore,
                   'add_document_in_transaction', seen)
    elif site == 'document_ref':
        _after_one(monkeypatch, service.documents.documents,
                   'publish_in_transaction', seen)
    elif site in ('new_witness', 'old_witness'):
        original = service.documents.witnesses.insert
        def inserted(*args, **kwargs):
            result = original(*args, **kwargs)
            revision = args[2].head.revision
            selected = (before[0]['heads'][0]['revision'] if site == 'old_witness'
                        else before[0]['heads'][0]['revision'] + 1)
            if revision == selected:
                seen.append(site)
                raise RuntimeError('injected after SQL write: ' + site)
            return result

        monkeypatch.setattr(service.documents.witnesses, 'insert', inserted)
    else:
        _after_one(monkeypatch, service.pair.imports, '_finish', seen)
    complete = service.pair.imports.complete
    def capture_complete(*args, **kwargs):
        before.append(_state(db, rag_scope, episode_scope, user, attempt.task.task_id))
        return complete(*args, **kwargs)
    monkeypatch.setattr(service.pair.imports, 'complete', capture_complete)
    if site == 'finish':
        with pytest.raises(ImportMemoryPublicationUnknown) as outcome:
            _publish(service, rag_scope, profile, attempt)
        assert 'injected after SQL write: _finish' in repr(outcome.value.__cause__.__cause__)
    else:
        with pytest.raises(RuntimeError, match='injected after SQL write'):
            _publish(service, rag_scope, profile, attempt)
    assert seen == [site if site in ('new_witness', 'old_witness') else {
        'rag_head': '_publish', 'episode_head': '_publish',
        'history_cas': 'compare_and_swap_in_transaction',
        'memory_cas': 'compare_and_swap_in_transaction',
        'episode_row': 'add_document_in_transaction',
        'document_ref': 'publish_in_transaction', 'finish': '_finish',
    }[site]]
    assert len(before) == 1
    after = _state(db, rag_scope, episode_scope, user, attempt.task.task_id)
    _assert_rolled_back(before[0], after, service, rag_scope, episode_scope)
    assert _public_members(service, rag_scope, episode_scope) == before_public
    assert service.documents.documents.read_document_bytes(user, old_id) == old_bytes


def test_real_clock_expiry_after_all_domain_writes(memory_publication, shared_database,
                                                    monkeypatch):
    service, db, store, _, _, rag_scope, episode_scope, profile, user, _ = memory_publication
    observer = shared_database[0]()
    attempt = _task(db, store, user)
    domain_done, release = threading.Event(), threading.Event()
    before, deadline = [], []
    before_public = _public_members(service, rag_scope, episode_scope)
    complete = service.pair.imports.complete
    terminal = service._terminal
    def capture_complete(*args, **kwargs):
        deadline.append(_deadline(db, user, attempt.task.task_id, seconds=5))
        before.append(_state(db, rag_scope, episode_scope, user, attempt.task.task_id))
        return complete(*args, **kwargs)
    def pause_after_domain(cursor, scope, owner, prepared, old_rows, expected, receipts):
        result = terminal(cursor, scope, owner, prepared, old_rows, expected, receipts)
        assert set(receipts) == {'rag', 'episode'}
        domain_done.set()
        assert release.wait(15), 'domain barrier timed out'
        return result
    monkeypatch.setattr(service.pair.imports, 'complete', capture_complete)
    monkeypatch.setattr(service, '_terminal', pause_after_domain)
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_publish, service, rag_scope, profile, attempt)
    worker_error = None
    try:
        assert domain_done.wait(30), 'terminal domain writes never completed'
        with observer.transaction() as cursor:
            assert cursor.execute('select clock_timestamp() as now').fetchone()['now'] < deadline[0]
        _wait_past(observer, deadline[0])
    finally:
        release.set()
        try:
            future.result(timeout=20)
        except BaseException as error:
            worker_error = error
        executor.shutdown(wait=False, cancel_futures=True)
    assert isinstance(worker_error, ImportMemoryPublicationUnknown), worker_error
    assert isinstance(worker_error.__cause__.__cause__, ImportLeaseLost)
    assert len(before) == len(deadline) == 1
    _assert_rolled_back(before[0], _state(db, rag_scope, episode_scope,
                        user, attempt.task.task_id), service, rag_scope, episode_scope)
    assert _public_members(service, rag_scope, episode_scope) == before_public


@pytest.mark.parametrize('decision', ('commit', 'rollback'))
def test_first_reconciliation_of_both_sealed_during_real_transaction(
        memory_publication, monkeypatch, decision):
    service, db, store, _, _, rag_scope, episode_scope, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    domain_done, release = threading.Event(), threading.Event()
    contexts, expecteds, before = [], [], []
    valid = service.pair._valid_descriptor
    terminal = service._terminal
    complete = service.pair.imports.complete
    def capture_descriptor(context, plan):
        result = valid(context, plan)
        if result and context.rag.sealed is not None and context.episode.sealed is not None:
            if not contexts:
                contexts.append(context)
        return result
    def capture_complete(*args, **kwargs):
        before.append(_state(db, rag_scope, episode_scope, user, attempt.task.task_id))
        return complete(*args, **kwargs)
    def pause_after_domain(cursor, scope, owner, prepared, old_rows, expected, receipts):
        terminal(cursor, scope, owner, prepared, old_rows, expected, receipts)
        assert set(receipts) == {'rag', 'episode'}
        expecteds.append(replace(expected, new_receipts=(receipts['rag'], receipts['episode'])))
        domain_done.set()
        assert release.wait(15), 'domain barrier timed out'
        if decision == 'rollback':
            raise RuntimeError('controlled rollback after both callback receipts')
    monkeypatch.setattr(service.pair, '_valid_descriptor', capture_descriptor)
    monkeypatch.setattr(service.pair.imports, 'complete', capture_complete)
    monkeypatch.setattr(service, '_terminal', pause_after_domain)
    before_public = _public_members(service, rag_scope, episode_scope)
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_publish, service, rag_scope, profile, attempt)
    outcome = worker_error = None
    try:
        assert domain_done.wait(30), 'both receipt barrier never reached'
        assert len(contexts) == len(expecteds) == 1
        context, expected = contexts[0], expecteds[0]
        assert context.rag.sealed is not None and context.episode.sealed is not None
        assert expected.new_receipts is not None and len(expected.new_receipts) == 2
        with db.transaction() as cursor:
            states = cursor.execute('''select generation_id,state from vector_generations
                where generation_id=any(%s)''', (list(context.generation_ids),)).fetchall()
            assert {row['generation_id']: row['state'] for row in states} == {
                generation_id: 'sealed' for generation_id in context.generation_ids}
            task = cursor.execute('select status,stage from import_tasks where id=%s',
                                  (attempt.task.task_id,)).fetchone()
            assert (task['status'], task['stage']) == ('running', 'committing')
        assert service._reconcile(context, expected) is None
    finally:
        release.set()
        try:
            outcome = future.result(timeout=20)
        except BaseException as error:
            worker_error = error
        executor.shutdown(wait=False, cancel_futures=True)
    if decision == 'commit':
        assert worker_error is None
        assert outcome.pair.import_task.status == 'succeeded'
        _assert_committed(service, db, rag_scope, episode_scope, attempt, outcome)
        assert service._reconcile(context, expected) is not None
        assert (outcome.pair.rag.generation_id, outcome.pair.episode.generation_id) == context.generation_ids
    else:
        assert isinstance(worker_error, RuntimeError)
        assert str(worker_error) == 'controlled rollback after both callback receipts'
        assert service._reconcile(context, expected) is None
        assert len(before) == 1
        _assert_rolled_back(before[0], _state(db, rag_scope, episode_scope,
                            user, attempt.task.task_id), service, rag_scope, episode_scope)
        assert _public_members(service, rag_scope, episode_scope) == before_public


@pytest.mark.parametrize('decision', ('commit', 'rollback'))
def test_lost_response_after_actual_transaction_exit(memory_publication,
                                                      monkeypatch, decision):
    service, db, store, _, _, rag_scope, episode_scope, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    contexts, expecteds, before, exits = [], [], [], []
    before_public = _public_members(service, rag_scope, episode_scope)
    valid = service.pair._valid_descriptor
    terminal = service._terminal
    complete = service.pair.imports.complete
    original_transaction = db.transaction
    def capture_descriptor(context, plan):
        result = valid(context, plan)
        if result and context.rag.sealed is not None and context.episode.sealed is not None:
            if not contexts:
                contexts.append(context)
        return result
    def after_domain(cursor, scope, owner, prepared, old_rows, expected, receipts):
        terminal(cursor, scope, owner, prepared, old_rows, expected, receipts)
        assert set(receipts) == {'rag', 'episode'}
        expecteds.append(replace(expected, new_receipts=(receipts['rag'], receipts['episode'])))
        if decision == 'rollback':
            raise RuntimeError('controlled rollback before transaction exit')
    @contextmanager
    def lost_transaction_response():
        try:
            with original_transaction() as cursor:
                yield cursor
        except RuntimeError as error:
            if (decision != 'rollback' or
                    str(error) != 'controlled rollback before transaction exit'):
                raise
            exits.append('rolled_back')
            raise TimeoutError('lost rollback response') from error
        exits.append('committed')
        raise TimeoutError('lost commit response')
    def replace_completion_transaction(*args, **kwargs):
        before.append(_state(db, rag_scope, episode_scope, user, attempt.task.task_id))
        monkeypatch.setattr(db, 'transaction', lost_transaction_response)
        try:
            return complete(*args, **kwargs)
        finally:
            monkeypatch.setattr(db, 'transaction', original_transaction)
    monkeypatch.setattr(service.pair, '_valid_descriptor', capture_descriptor)
    monkeypatch.setattr(service, '_terminal', after_domain)
    monkeypatch.setattr(service.pair.imports, 'complete', replace_completion_transaction)
    assert not contexts and not expecteds and not exits
    if decision == 'commit':
        outcome = _publish(service, rag_scope, profile, attempt)
        assert outcome.pair.import_task.status == 'succeeded'
        _assert_committed(service, db, rag_scope, episode_scope, attempt, outcome)
        assert service._reconcile(contexts[0], expecteds[0]) is not None
        assert (outcome.pair.rag.generation_id, outcome.pair.episode.generation_id) == contexts[0].generation_ids
        assert exits == ['committed']
    else:
        with pytest.raises(ImportMemoryPublicationUnknown) as unknown:
            _publish(service, rag_scope, profile, attempt)
        assert exits == ['rolled_back']
        assert unknown.value.generation_ids == contexts[0].generation_ids
        assert unknown.value._frozen_expected.new_receipts == expecteds[0].new_receipts
        assert service.reconcile(unknown.value) is None
        assert len(before) == 1
        _assert_rolled_back(before[0], _state(db, rag_scope, episode_scope,
                            user, attempt.task.task_id), service, rag_scope, episode_scope)
        assert _public_members(service, rag_scope, episode_scope) == before_public
    assert len(contexts) == len(expecteds) == 1
    assert contexts[0].rag.sealed is not None and contexts[0].episode.sealed is not None
    assert expecteds[0].new_receipts is not None and len(expecteds[0].new_receipts) == 2
