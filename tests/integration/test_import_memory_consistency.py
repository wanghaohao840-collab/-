"""Disposable C import consistency checks beyond the terminal fault matrix."""
from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
import threading
import sys
from uuid import UUID, uuid4

import pytest

from app.import_memory_publication import (
    ImportMemoryPublicationError, ImportMemoryPublicationUnknown,
    _RECEIPT_FIELDS, _receipt_projection,
)
from app.import_vector_publication import ImportPairRejected
from app.postgres_import_leases import PostgresImportLeaseRepository
from app.import_document_publication import ImportDocumentPublicationError
from app.history import EMPTY_HISTORY
from app.import_memory_publication import ImportMemoryPublicationService
from app.object_store import ObjectRef
from app.object_store import S3ObjectStore
from app.postgres_coordination import PostgresUserMutationCoordinator
from app.postgres_vector_generations import PostgresVectorGenerationAuthority, VectorScope
from app.published_episode_reads import PublishedEpisodeReadFactory
from app.published_rag_reads import PublishedRAGReadFactory
from app.vector_generation_service import VectorGenerationService
from hello_agents.memory.embedding import SimpleEmbedding
from hello_agents.memory.manager import MemoryManager
from hello_agents.memory.rag.embedding_runtime import build_rag_embedding
from hello_agents.memory.rag.embedding_runtime import RAGEmbeddingRuntime
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.storage.vector_store import QdrantVectorStore, VectorPoint
from hello_agents.memory.storage.generation_vector_store import CandidateGenerationWriter
from tests.integration.test_import_document_publication import (
    _point, _publish_strict_legacy_document, _task, publication,
)
from tests.integration.test_import_memory_publication import memory_publication
from tests.integration.test_import_memory_publication import _mixed_baseline
from tests.integration.test_published_episode_reads import (
    _item as episode_item, _publish as publish_episode_baseline,
)
from tests.integration.test_import_memory_fault_matrix import (
    _assert_committed, _assert_rolled_back, _public_members, _state,
    _deadline, _wait_past,
)
from tests.integration.test_postgres_auth_sessions import shared_database
from tests.integration.test_s3_object_store import store


@pytest.fixture(autouse=True)
def _resource_ledger(request, memory_publication):
    service, db, store, _, _, rag_scope, episode_scope, _, user, other = memory_publication
    yield
    with db.transaction() as cursor:
        schema = cursor.execute('select current_schema() as schema').fetchone()['schema']
        rows = cursor.execute('''select generation_id,tenant_id,task_id,vector_kind,
            state,expected_count,content_digest from vector_generations
            where tenant_id=any(%s) order by tenant_id,vector_kind,generation_id''',
            ([user, other],)).fetchall()
    ledger = {'schema': schema, 'bucket': store.bucket,
              'rag_collection': rag_scope.identity.physical_collection,
              'episode_collection': episode_scope.identity.physical_collection,
              'generations': [dict(row) for row in rows]}
    if hasattr(request.node, '_extra_collections'):
        ledger['additional_collections'] = request.node._extra_collections
    request.node.user_properties.append(('resource_ledger', json.dumps(ledger, default=str)))


@pytest.fixture
def public_pair(memory_publication, request):
    base, db, store, _, snapshots, _, episode_scope, profile, user, other = memory_publication
    runtime = build_rag_embedding({}, backend='qdrant')
    identity = IndexIdentity('qdrant', 'import_public_' + uuid4().hex,
                             runtime.profile)
    raw = QdrantVectorStore(url=os.environ['GENERATION_QDRANT_TEST_URL'],
                            retry_delays=())
    raw.ensure_collection(identity.physical_collection, runtime.profile.dimension)
    rag = VectorGenerationService(PostgresVectorGenerationAuthority(db), raw)
    scopes = {owner: VectorScope(owner, 'rag', f'pdf_{owner}', identity)
              for owner in (user, other)}
    request.node._extra_collections = [identity.physical_collection]
    try:
        for owner in (user, other):
            lease = PostgresUserMutationCoordinator(db).acquire(
                owner, 'public-bootstrap', lease_seconds=120)
            assert lease is not None
            try:
                def domain(cursor, owner=owner):
                    if owner == other:
                        snapshots.compare_and_swap_in_transaction(
                            cursor, owner, 'history', deepcopy(EMPTY_HISTORY),
                            expected_version=0)
                rag.publish_complete(scopes[owner], lease,
                    rag.authority.read_head(scopes[owner]), [],
                    domain_publish=domain, snapshot_version=1)
            finally:
                PostgresUserMutationCoordinator(db).release(lease)
        episode = base.pair.episode
        other_episode = replace(episode_scope, tenant_id=other)
        publish_episode_baseline(db, episode, other_episode,
            [episode_item(other, 'B independent prior episode',
                          logical_id='other-prior-episode')])
        services = {
            user: ImportMemoryPublicationService(db, store, rag, episode,
                                                  trusted_episode_scope=episode_scope),
            other: ImportMemoryPublicationService(db, store, rag, episode,
                                                   trusted_episode_scope=other_episode),
        }
        factory = PublishedRAGReadFactory(rag, runtime, identity)
        yield services, db, store, rag, snapshots, scopes, {
            user: episode_scope, other: other_episode}, profile, factory, (user, other)
    finally:
        raw.client.delete_collection(identity.physical_collection)


def _public_publish(service, scope, profile, runtime, attempt, marker):
    base = _point(scope, attempt.task.document_id, marker)
    payload = deepcopy(base.payload)
    payload['metadata']['embedding_fingerprint'] = runtime.profile.fingerprint
    point = VectorPoint(base.id, runtime.embed_query(marker), payload)
    return service.publish(scope, attempt, [point],
        event_vector=[1., 0., 0., 0.], event_profile=profile)


def _public_ids(rag_operation, episode_operation):
    return (set(rag_operation.list_document_ids()),
            {point.id: point.payload for point in
             episode_operation.scroll(min_importance=-1)},
            tuple(sorted(json.dumps(payload, sort_keys=True, ensure_ascii=False)
                         for payload in rag_operation.scroll_payloads())),
            (rag_operation.head, episode_operation.head))


def _expected_rag_payloads(result):
    return tuple(sorted(json.dumps(dict(point.payload) | {'id': point.id}, sort_keys=True,
                                   ensure_ascii=False)
                        for point in result._context.rag.complete_corpus))


def _drain(pool, future, release):
    """Unblock and join a worker before a failing assertion can leave it running."""
    failing = sys.exc_info()[0] is not None
    release.set()
    try:
        if not future.done():
            future.result(timeout=30)
    except Exception:
        if not failing:
            raise
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _assert_public_c_sql(db, rag_scope, episode_scope, attempt, result):
    """Check the accepted pair against a fresh full authority snapshot."""
    state = _state(db, rag_scope, episode_scope, attempt.task.user_id,
                   attempt.task.task_id)
    expected = result._expected
    context = result._context
    assert state['task']['status'] == state['task']['stage'] == 'succeeded'
    assert len(state['audit']) == 1
    assert state['audit'][0]['end_reason'] == 'succeeded'
    assert state['audit'][0]['ended_at'] is not None
    assert [head['last_generation_id'] for head in state['heads']] == list(
        context.generation_ids)
    assert [head['snapshot_version'] for head in state['heads']] == [
        expected.history_version, expected.memory_version]
    assert [(row['kind'], row['version'], row['payload']) for row in
            state['snapshots']] == [
        ('history', expected.history_version, expected.history),
        ('memory', expected.memory_version, expected.memory)]
    assert [_receipt_projection(row) for row in state['old_receipts']] == list(
        expected.new_receipts)
    events = [row for row in state['rows'] if row['document_id'] == expected.event_id]
    assert len(events) == 1
    assert events[0]['content'] == expected.item['content']
    assert json.loads(events[0]['metadata']) == expected.event_metadata
    refs = [row for row in state['refs'] if row['document_id'] == attempt.task.document_id]
    assert len(refs) == 1
    assert (attempt.task.user_id, attempt.task.document_id, refs[0]['bucket'],
            refs[0]['object_key'], refs[0]['version_id'], refs[0]['sha256'],
            refs[0]['size_bytes']) == expected.ref
    witness = [row for row in state['witnesses'] if row['head_revision'] ==
               state['heads'][0]['revision']]
    assert len(witness) == 1
    assert (witness[0]['last_generation_id'], witness[0]['document_count'],
            witness[0]['documents_sha256']) == (
        context.rag.generation_id, expected.new_evidence.count,
        expected.new_evidence.digest)
    return state


def test_two_user_pinned_old_new_and_later_import(public_pair, monkeypatch):
    services, db, store, rag, snapshots, scopes, episode_scopes, profile, factory, users = public_pair
    user, other = users
    runtime = factory._runtime
    first_a = _task(db, store, user, content=b'A first accepted', name='A-first.txt')
    first_b = _task(db, store, other, content=b'B accepted', name='B-first.txt')
    first_result_a = _public_publish(services[user], scopes[user], profile,
                                     runtime, first_a, 'A-first')
    first_result_b = _public_publish(services[other], scopes[other], profile,
                                     runtime, first_b, 'B-first')
    a_rag_old = factory.open_operation(user, scopes[user].namespace)
    a_episode_old = PublishedEpisodeReadFactory(services[user].pair.episode,
                                                 episode_scopes[user]).open_operation(user)
    b_rag = factory.open_operation(other, scopes[other].namespace)
    b_episode = PublishedEpisodeReadFactory(services[other].pair.episode,
                                            episode_scopes[other]).open_operation(other)
    old_a = _public_ids(a_rag_old, a_episode_old)
    old_b = _public_ids(b_rag, b_episode)
    assert old_a[0] == {first_a.task.document_id}
    assert old_a[2] == _expected_rag_payloads(first_result_a)
    assert set(old_a[1]) == {first_result_a.event_id}
    assert old_b[0] == {first_b.task.document_id}
    assert old_b[2] == _expected_rag_payloads(first_result_b)
    assert set(old_b[1]) == {'other-prior-episode', first_result_b.event_id}
    b_state = _state(db, scopes[other], episode_scopes[other], other,
                     first_b.task.task_id)
    b_pinned = _pinned(db, other, first_b.task.document_id)
    b_bytes = store.read_verified(other, b_pinned[1])
    a_old_ref = _pinned(db, user, first_a.task.document_id)
    a_old_bytes = store.read_verified(user, a_old_ref[1])

    second_a = _task(db, store, user, content=b'A second accepted', name='A-second.txt')
    ready, release = threading.Event(), threading.Event()
    original_complete = services[user].pair.imports.complete

    def wait_after_seal(owner, callback):
        ready.set()
        assert release.wait(20), 'Sealed C pair was not released'
        return original_complete(owner, callback)

    monkeypatch.setattr(services[user].pair.imports, 'complete', wait_after_seal)
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(_public_publish, services[user], scopes[user], profile,
                         runtime, second_a, 'A-second')
    try:
        assert ready.wait(20), 'C did not reach completion after both seals'
        with db.transaction() as cursor:
            candidates = cursor.execute('''select vector_kind,state from
                vector_generations where task_id=%s order by vector_kind''',
                (second_a.task.task_id,)).fetchall()
            stage = cursor.execute('select stage from import_tasks where id=%s',
                (second_a.task.task_id,)).fetchone()['stage']
        assert [(row['vector_kind'], row['state']) for row in candidates] == [
            ('episode', 'sealed'), ('rag', 'sealed')]
        assert stage == 'committing'
        assert _public_ids(a_rag_old, a_episode_old) == old_a
        assert _public_ids(b_rag, b_episode) == old_b
        release.set()
        second_result = future.result(timeout=30)
    finally:
        _drain(pool, future, release)
    monkeypatch.setattr(services[user].pair.imports, 'complete', original_complete)
    a_rag_new = factory.open_operation(user, scopes[user].namespace)
    a_episode_new = PublishedEpisodeReadFactory(services[user].pair.episode,
                                                 episode_scopes[user]).open_operation(user)
    second_members = _public_ids(a_rag_new, a_episode_new)
    assert second_members[0] == {first_a.task.document_id,
                                 second_a.task.document_id}
    assert set(second_members[1]) == {first_result_a.event_id,
                                      second_result.event_id}
    assert second_members[2] == _expected_rag_payloads(second_result)
    assert second_members[1][second_result.event_id] == \
        second_result._expected.event_metadata
    assert second_members[3][0].generation_id == second_result.pair.rag.generation_id
    assert second_members[3][1].generation_id == second_result.pair.episode.generation_id
    _assert_public_c_sql(db, scopes[user], episode_scopes[user], second_a,
                         second_result)
    assert _public_ids(a_rag_old, a_episode_old) == old_a
    assert snapshots.read(user, 'history').version == second_result.history_version
    assert snapshots.read(user, 'memory').version == second_result.memory_version
    assert _pinned(db, user, first_a.task.document_id) == a_old_ref
    assert store.read_verified(user, a_old_ref[1]) == a_old_bytes
    a_second_ref = _pinned(db, user, second_a.task.document_id)
    assert store.read_verified(user, a_second_ref[1]) == b'A second accepted'

    third_a = _task(db, store, user, content=b'A third accepted', name='A-third.txt')
    third_result = _public_publish(services[user], scopes[user], profile,
                                   runtime, third_a, 'A-third')
    a_rag_latest = factory.open_operation(user, scopes[user].namespace)
    a_episode_latest = PublishedEpisodeReadFactory(services[user].pair.episode,
                                                    episode_scopes[user]).open_operation(user)
    assert _public_ids(a_rag_old, a_episode_old) == old_a
    assert _public_ids(a_rag_new, a_episode_new) == second_members
    latest_members = _public_ids(a_rag_latest, a_episode_latest)
    assert latest_members[0] == {first_a.task.document_id,
                                 second_a.task.document_id,
                                 third_a.task.document_id}
    assert set(latest_members[1]) == {first_result_a.event_id,
                                      second_result.event_id,
                                      third_result.event_id}
    assert latest_members[2] == _expected_rag_payloads(third_result)
    assert latest_members[1][third_result.event_id] == \
        third_result._expected.event_metadata
    assert latest_members[3][0].generation_id == third_result.pair.rag.generation_id
    assert latest_members[3][1].generation_id == third_result.pair.episode.generation_id
    _assert_public_c_sql(db, scopes[user], episode_scopes[user], third_a,
                         third_result)
    assert snapshots.read(user, 'history').version == third_result.history_version
    assert snapshots.read(user, 'memory').version == third_result.memory_version
    assert _pinned(db, user, first_a.task.document_id) == a_old_ref
    assert _pinned(db, user, second_a.task.document_id) == a_second_ref
    assert store.read_verified(user, a_old_ref[1]) == a_old_bytes
    assert store.read_verified(user, a_second_ref[1]) == b'A second accepted'
    assert store.read_verified(user, _pinned(db, user,
        third_a.task.document_id)[1]) == b'A third accepted'
    for result in (first_result_a, second_result, third_result):
        assert services[user]._reconcile(result._context, result._expected) is not None
    assert _state(db, scopes[other], episode_scopes[other], other,
                  first_b.task.task_id) == b_state
    assert _pinned(db, other, first_b.task.document_id) == b_pinned
    assert store.read_verified(other, b_pinned[1]) == b_bytes
    assert _public_ids(b_rag, b_episode) == old_b
    assert _public_ids(factory.open_operation(other, scopes[other].namespace),
        PublishedEpisodeReadFactory(services[other].pair.episode,
                                    episode_scopes[other]).open_operation(other)) == old_b


@pytest.mark.parametrize('delayed_kind', ('rag', 'episode'))
def test_reclaimed_c_pair_second_late_upsert(public_pair, monkeypatch, delayed_kind):
    services, db, store, rag, snapshots, scopes, episode_scopes, profile, factory, users = public_pair
    user, other = users
    runtime = factory._runtime
    b_attempt = _task(db, store, other, content=b'B unaffected', name='B.txt')
    _public_publish(services[other], scopes[other], profile, runtime,
                    b_attempt, 'B-only')
    b_rag = factory.open_operation(other, scopes[other].namespace)
    b_episode = PublishedEpisodeReadFactory(services[other].pair.episode,
                                            episode_scopes[other]).open_operation(other)
    b_public = _public_ids(b_rag, b_episode)
    b_state = _state(db, scopes[other], episode_scopes[other], other,
                     b_attempt.task.task_id)
    old_attempt = _task(db, store, user, content=b'A accepted', name='A.txt')
    selected_scope = scopes[user] if delayed_kind == 'rag' else episode_scopes[user]
    selected_service = rag if delayed_kind == 'rag' else services[user].pair.episode
    client = selected_service.raw.client
    original_upsert = client.upsert
    entered, release = threading.Event(), threading.Event()
    captured = []

    def gated_upsert(*args, **kwargs):
        if (threading.current_thread().name.startswith('old-c-upload')
                and kwargs.get('collection_name') ==
                selected_scope.identity.physical_collection):
            captured.append((deepcopy(args), deepcopy(kwargs)))
            entered.set()
            assert release.wait(20), 'Old Qdrant upload was not released'
        return original_upsert(*args, **kwargs)

    monkeypatch.setattr(client, 'upsert', gated_upsert)
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='old-c-upload')
    old = pool.submit(_public_publish, services[user], scopes[user], profile,
                      runtime, old_attempt, 'A-old-attempt')
    try:
        assert entered.wait(20), 'Old C candidate did not reach real Qdrant upsert'
        with db.transaction() as cursor:
            rows = cursor.execute('''select generation_id,vector_kind,state from
                vector_generations where task_id=%s order by vector_kind''',
                (old_attempt.task.task_id,)).fetchall()
        old_ids = {row['vector_kind']: row['generation_id'] for row in rows}
        assert set(old_ids) == ({'rag'} if delayed_kind == 'rag'
                                else {'rag', 'episode'})
        assert next(row['state'] for row in rows if
                    row['vector_kind'] == delayed_kind) == 'staging'
        for kind, generation_id in old_ids.items():
            scope = scopes[user] if kind == 'rag' else episode_scopes[user]
            authority = (rag if kind == 'rag' else
                         services[user].pair.episode).authority
            authority.abandon(scope, old_attempt, generation_id)
            assert authority.reconcile(scope, generation_id)['state'] == 'abandoned'
        deadline = _deadline(db, user, old_attempt.task.task_id, seconds=2)
        _wait_past(db, deadline)
        imports = PostgresImportLeaseRepository(db)
        assert imports.recover_expired() == 1
        new_attempt = imports.claim_next('new-c-worker')
        assert new_attempt is not None
        assert new_attempt.task.task_id == old_attempt.task.task_id
        assert new_attempt.lease_version == old_attempt.lease_version + 1
        current = _public_publish(services[user], scopes[user], profile,
                                  runtime, new_attempt, 'A-current')
        assert current.pair.import_task.status == 'succeeded'
        release.set()
        with pytest.raises((ImportPairRejected, ImportMemoryPublicationUnknown)):
            old.result(timeout=30)
    finally:
        _drain(pool, old, release)
    assert len(captured) == 1
    old_id = old_ids[delayed_kind]
    points = captured[0][1]['points']
    assert points and all(point.payload['_gv_generation'] == str(old_id)
                          for point in points)
    collection = selected_scope.identity.physical_collection
    assert selected_service.raw.count(collection,
        {'_gv_generation': str(old_id)}) == len(points)
    expected_a = _public_ids(
        factory.open_operation(user, scopes[user].namespace),
        PublishedEpisodeReadFactory(services[user].pair.episode,
            episode_scopes[user]).open_operation(user))
    assert expected_a[0] == {new_attempt.task.document_id}
    assert expected_a[2] == _expected_rag_payloads(current)
    assert set(expected_a[1]) == {current.event_id}
    assert expected_a[1][current.event_id] == current._expected.event_metadata
    assert expected_a[3][0].generation_id == current.pair.rag.generation_id
    assert expected_a[3][1].generation_id == current.pair.episode.generation_id
    assert 'A-current' in expected_a[2][0]
    assert 'A-old-attempt' not in expected_a[2][0]
    assert _public_ids(factory.open_operation(other, scopes[other].namespace),
        PublishedEpisodeReadFactory(services[other].pair.episode,
            episode_scopes[other]).open_operation(other)) == b_public
    cleaned = selected_service.cleanup_abandoned(selected_scope, old_id)
    assert cleaned == len(points)
    assert selected_service.raw.count(collection,
        {'_gv_generation': str(old_id)}) == 0
    assert _public_ids(factory.open_operation(user, scopes[user].namespace),
        PublishedEpisodeReadFactory(services[user].pair.episode,
            episode_scopes[user]).open_operation(user)) == expected_a
    assert _public_ids(factory.open_operation(other, scopes[other].namespace),
        PublishedEpisodeReadFactory(services[other].pair.episode,
            episode_scopes[other]).open_operation(other)) == b_public
    # Test-injected delayed transport replay of the captured, identical request.
    # The one-shot production writer does not send this after abandonment.
    original_upsert(*deepcopy(captured[0][0]), **deepcopy(captured[0][1]))
    assert selected_service.raw.count(collection,
        {'_gv_generation': str(old_id)}) == len(points)
    a_rag = factory.open_operation(user, scopes[user].namespace)
    a_episode = PublishedEpisodeReadFactory(services[user].pair.episode,
                                            episode_scopes[user]).open_operation(user)
    assert _public_ids(a_rag, a_episode) == expected_a
    service_public = services[user]._reconcile(current._context,
                                               current._expected)
    assert service_public is not None
    assert service_public.pair.rag.generation_id != old_ids['rag']
    assert service_public.pair.episode.generation_id != old_ids.get('episode')
    assert _state(db, scopes[other], episode_scopes[other], other,
                  b_attempt.task.task_id) == b_state
    assert _public_ids(b_rag, b_episode) == b_public
    assert _public_ids(factory.open_operation(other, scopes[other].namespace),
        PublishedEpisodeReadFactory(services[other].pair.episode,
            episode_scopes[other]).open_operation(other)) == b_public


def _publish(service, scope, profile, attempt):
    return service.publish(scope, attempt,
        [_point(scope, attempt.task.document_id, 'C-consistency')],
        event_vector=[1., 0., 0., 0.], event_profile=profile)


def _pinned(db, user, document_id):
    with db.transaction() as cursor:
        row = cursor.execute('''select bucket,object_key,version_id,sha256,
            size_bytes,history_record_sha256 from document_objects
            where user_id=%s and document_id=%s''', (user, document_id)).fetchone()
    assert row is not None
    return dict(row), ObjectRef(row['object_key'], row['sha256'],
                                row['size_bytes'], row['version_id'])


@pytest.mark.parametrize('document_kind', ('new', 'retained'))
def test_exact_c_pinned_versions_survive_head_changes(
        memory_publication, publication, document_kind):
    service, db, store, rag, snapshots, rag_scope, _, profile, user, _ = memory_publication
    retained = (_publish_strict_legacy_document(publication)
                if document_kind == 'retained' else None)
    attempt = _task(db, store, user, content=b'C pinned source bytes')
    accepted_source = store.read_verified(user, attempt.source)
    result = _publish(service, rag_scope, profile, attempt)
    document_id = retained or attempt.task.document_id
    row, ref = _pinned(db, user, document_id)
    pinned = store.read_verified(user, ref)
    assert row['bucket'] == store.bucket
    assert len(pinned) == ref.size_bytes
    assert hashlib.sha256(pinned).hexdigest() == ref.sha256
    assert result.pair.import_task.status == 'succeeded'
    assert result.witness[2] == len(snapshots.read(user, 'history').data['documents'])
    assert any(record['document_id'] == document_id and
               record['document_path'] == f'object://{store.bucket}/{ref.key}'
               for record in snapshots.read(user, 'history').data['documents'])
    newer = store.client.put_object(Bucket=store.bucket, Key=ref.key,
                                    Body=b'unpublished newer bytes')
    assert newer['VersionId'] != ref.version_id
    marker = store.client.delete_object(Bucket=store.bucket, Key=ref.key)
    assert marker['DeleteMarker'] and marker['VersionId'] != ref.version_id
    assert store.read_verified(user, ref) == pinned
    assert store.read_verified(user, attempt.source) == accepted_source
    assert _pinned(db, user, document_id)[0] == row
    assert service._reconcile(result._context, result._expected) is not None


def test_missing_retained_exact_version_refuses_before_candidate_stage(
        memory_publication, publication, monkeypatch):
    service, db, store, rag, snapshots, rag_scope, _, profile, user, _ = memory_publication
    retained = _publish_strict_legacy_document(publication)
    _, ref = _pinned(db, user, retained)
    attempt = _task(db, store, user)
    before = (rag.authority.read_head(rag_scope),
              service.pair.episode.authority.read_head(service.episode_scope),
              snapshots.read(user, 'history'), snapshots.read(user, 'memory'))
    staged = []
    original = rag.authority.stage

    def track_stage(*args, **kwargs):
        staged.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(rag.authority, 'stage', track_stage)
    store.client.delete_object(Bucket=store.bucket, Key=ref.key,
                               VersionId=ref.version_id)
    with pytest.raises(ImportDocumentPublicationError):
        _publish(service, rag_scope, profile, attempt)
    assert staged == []
    assert (rag.authority.read_head(rag_scope),
            service.pair.episode.authority.read_head(service.episode_scope),
            snapshots.read(user, 'history'), snapshots.read(user, 'memory')) == before
    with db.transaction() as cursor:
        assert cursor.execute('select count(*) as n from document_objects '
                              'where user_id=%s', (user,)).fetchone()['n'] == 1
        assert cursor.execute('select status from import_tasks where id=%s',
                              (attempt.task.task_id,)).fetchone()['status'] != 'succeeded'


@pytest.mark.parametrize('boundary', (
    'source_read', 'copy_put', 'copy_verify', 'retained_verify',
    'rag_upload', 'rag_verify', 'rag_seal', 'episode_verify',
))
def test_c_prepublication_faults_leave_old_pair(memory_publication, publication,
                                                monkeypatch, boundary):
    service, db, store, rag, _, rag_scope, episode_scope, profile, user, _ = memory_publication
    retained = (_publish_strict_legacy_document(publication)
                if boundary == 'retained_verify' else None)
    retained_ref = _pinned(db, user, retained) if retained else None
    retained_bytes = store.read_verified(user, retained_ref[1]) if retained_ref else None
    attempt = _task(db, store, user)
    before = _state(db, rag_scope, episode_scope, user, attempt.task.task_id)
    if retained:
        assert before['refs']
    before_public = _public_members(service, rag_scope, episode_scope)
    reached = []
    selected = {
        'source_read': (service.documents.sources, 'read_source_bytes'),
        'copy_put': (store, 'put_immutable'),
        'copy_verify': (service.documents.documents, 'verify_for_publication'),
        'retained_verify': (service.documents.witnesses, 'verify_retained_references'),
        'rag_upload': (rag.raw.client, 'upsert'),
        'rag_verify': (CandidateGenerationWriter, 'verify'),
        'rag_seal': (rag.authority, 'seal'),
        'episode_verify': (CandidateGenerationWriter, 'verify'),
    }
    owner, method = selected[boundary]
    original = getattr(owner, method)

    def injected(*args, **kwargs):
        if boundary == 'rag_upload' and kwargs.get('collection_name') != \
                rag_scope.identity.physical_collection:
            return original(*args, **kwargs)
        if boundary in ('rag_verify', 'episode_verify'):
            writer = args[0]
            intended = rag_scope if boundary == 'rag_verify' else episode_scope
            if writer.scope != intended:
                return original(*args, **kwargs)
        if boundary == 'rag_seal' and args[0] != rag_scope:
            return original(*args, **kwargs)
        reached.append(boundary)
        raise RuntimeError('injected before external boundary: ' + boundary)

    monkeypatch.setattr(owner, method, injected)
    try:
        with pytest.raises((RuntimeError, ImportPairRejected,
                            ImportMemoryPublicationUnknown)) as outcome:
            _publish(service, rag_scope, profile, attempt)
    finally:
        monkeypatch.setattr(owner, method, original)
    assert reached == [boundary]
    after = _state(db, rag_scope, episode_scope, user, attempt.task.task_id)
    assert after == before
    if retained:
        assert _pinned(db, user, retained) == retained_ref
        assert store.read_verified(user, retained_ref[1]) == retained_bytes
    assert _public_members(service, rag_scope, episode_scope) == before_public
    with db.transaction() as cursor:
        rows = cursor.execute('''select generation_id,vector_kind,state from
            vector_generations where task_id=%s order by vector_kind''',
            (attempt.task.task_id,)).fetchall()
    if boundary in ('source_read', 'copy_put', 'copy_verify', 'retained_verify'):
        assert rows == []
        assert isinstance(outcome.value, RuntimeError)
    else:
        error = outcome.value
        assert isinstance(error, (ImportPairRejected, ImportMemoryPublicationUnknown))
        context = error.context
        assert context.generation_ids[0] != context.generation_ids[1]
        assert {row['generation_id'] for row in rows}.issubset(
            set(context.generation_ids))
        assert all(row['generation_id'] == context.generation_ids[
            0 if row['vector_kind'] == 'rag' else 1] for row in rows)
        assert all(row['state'] == 'abandoned' for row in rows)
        if boundary == 'episode_verify':
            assert {row['vector_kind'] for row in rows} == {'rag', 'episode'}
        else:
            assert {row['vector_kind'] for row in rows} == {'rag'}
        if boundary == 'rag_seal':
            assert isinstance(error, ImportMemoryPublicationUnknown)
        else:
            assert isinstance(error, ImportPairRejected)


@pytest.mark.parametrize('drift', ('snapshot_bool_to_int', 'raw_nested_bool_to_int'))
def test_same_version_typed_memory_drift_rolls_back_pair(memory_publication,
                                                         monkeypatch, drift):
    service, db, store, _, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    _mixed_baseline(service, db, episode_scope, user)
    with db.transaction() as cursor:
        if drift == 'snapshot_bool_to_int':
            cursor.execute('''update user_snapshots set payload=jsonb_set(payload,
                '{memories,1,metadata,probe}', '{"flag":true}'::jsonb)
                where user_id=%s and kind='memory' ''', (user,))
        else:
            row = cursor.execute('''select metadata from memory_documents
                where user_id=%s and document_id='old-semantic' ''', (user,)).fetchone()
            metadata = json.loads(row['metadata'])
            metadata['probe'] = {'flag': True}
            cursor.execute('''update memory_documents set metadata=%s
                where user_id=%s and document_id='old-semantic' ''',
                (json.dumps(metadata, ensure_ascii=False), user))
    baseline_version = snapshots.read(user, 'memory').version
    attempt = _task(db, store, user)
    before_public = _public_members(service, rag_scope, episode_scope)
    original_pair = service.pair.publish_prepared_pair
    original_complete = service.pair.imports.complete
    observed = []
    before_terminal = []

    def change_after_capture(*args, **kwargs):
        with db.transaction() as cursor:
            if drift == 'snapshot_bool_to_int':
                old = cursor.execute('''select version,payload from user_snapshots
                    where user_id=%s and kind='memory' ''', (user,)).fetchone()
                assert old['version'] == baseline_version
                assert old['payload']['memories'][1]['metadata']['probe']['flag'] is True
                cursor.execute('''update user_snapshots set payload=jsonb_set(payload,
                    '{memories,1,metadata,probe,flag}', '1'::jsonb)
                    where user_id=%s and kind='memory' ''', (user,))
                new = cursor.execute('''select version,payload from user_snapshots
                    where user_id=%s and kind='memory' ''', (user,)).fetchone()
                assert new['version'] == baseline_version
                assert type(new['payload']['memories'][1]['metadata']['probe']['flag']) is int
            else:
                old = cursor.execute('''select metadata from memory_documents
                    where user_id=%s and document_id='old-semantic' ''', (user,)).fetchone()
                metadata = json.loads(old['metadata'])
                assert metadata['probe']['flag'] is True
                metadata['probe']['flag'] = 1
                cursor.execute('''update memory_documents set metadata=%s
                    where user_id=%s and document_id='old-semantic' ''',
                    (json.dumps(metadata, ensure_ascii=False), user))
            observed.append(drift)
        return original_pair(*args, **kwargs)

    def capture_complete(*args, **kwargs):
        before_terminal.append(_state(db, rag_scope, episode_scope, user,
                                      attempt.task.task_id))
        return original_complete(*args, **kwargs)

    monkeypatch.setattr(service.pair, 'publish_prepared_pair', change_after_capture)
    monkeypatch.setattr(service.pair.imports, 'complete', capture_complete)
    with pytest.raises(ImportMemoryPublicationError):
        _publish(service, rag_scope, profile, attempt)
    assert observed == [drift]
    assert len(before_terminal) == 1
    _assert_rolled_back(before_terminal[0], _state(db, rag_scope, episode_scope,
        user, attempt.task.task_id), service, rag_scope, episode_scope)
    assert _public_members(service, rag_scope, episode_scope) == before_public


def test_reversed_memory_document_capture_order_preserves_publication(
        memory_publication, monkeypatch):
    service, db, store, _, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    _mixed_baseline(service, db, episode_scope, user)
    attempt = _task(db, store, user)
    original = service.episodes._capture_bundle
    observed = []

    def reverse_capture(owner):
        bundle = original(owner)
        rows = bundle.documents
        assert len(rows) >= 3
        bundle._documents = list(reversed(rows))
        observed.append(([row['document_id'] for row in rows],
                         [row['document_id'] for row in bundle.documents]))
        return bundle

    monkeypatch.setattr(service.episodes, '_capture_bundle', reverse_capture)
    result = _publish(service, rag_scope, profile, attempt)
    assert len(observed) == 1 and observed[0][0] == list(reversed(observed[0][1]))
    assert observed[0][0] != observed[0][1]
    _assert_committed(service, db, rag_scope, episode_scope, attempt, result)
    assert service._reconcile(result._context, result._expected) is not None
    assert snapshots.read(user, 'memory').version == result.memory_version


@pytest.mark.parametrize('field', ('owner', 'base_revision',
    'user_lease_token', 'task_lease_version', 'created_at'))
def test_frozen_old_receipt_field_mismatch_is_unknown(memory_publication, field):
    service, db, store, _, _, rag_scope, _, profile, user, _ = memory_publication
    first = _task(db, store, user, name='first.txt')
    _publish(service, rag_scope, profile, first)
    attempt = _task(db, store, user, name='second.txt')
    result = _publish(service, rag_scope, profile, attempt)
    assert service._reconcile(result._context, result._expected) is not None
    values = list(result._expected.old_rag_receipt)
    index = _RECEIPT_FIELDS.index(field)
    value = values[index]
    values[index] = (str(uuid4()) if field == 'user_lease_token' else
                     '2000-01-01T00:00:00.000000+00:00' if field == 'created_at' else
                     value + 1 if field in ('base_revision', 'task_lease_version') else
                     value + '-forged')
    damaged = replace(result._expected, old_rag_receipt=tuple(values))
    assert service._reconcile(result._context, damaged) is None
    assert service._reconcile(result._context, result._expected) is not None


def test_receipt_projection_typed_sql_and_json_are_equal(memory_publication):
    service, db, store, _, _, rag_scope, _, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    result = _publish(service, rag_scope, profile, attempt)
    with db.transaction() as cursor:
        row = cursor.execute('''select g.*,to_jsonb(g) as receipt
            from vector_generations g where generation_id=%s''',
            (result.pair.rag.generation_id,)).fetchone()
    typed = dict(row)
    assert isinstance(typed['generation_id'], UUID)
    assert isinstance(typed['created_at'], datetime)
    assert isinstance(typed['published_at'], datetime)
    assert _receipt_projection(typed) == _receipt_projection(row['receipt'])
    assert _receipt_projection(row['receipt']) == result._expected.new_receipts[0]


@pytest.mark.parametrize('damage', ('missing_second_receipt', 'wrong_second_receipt',
                                    'ended_audit', 'extra_raw_memory_row'))
def test_reconcile_missing_evidence_is_unknown(memory_publication, monkeypatch, damage):
    service, db, store, _, _, rag_scope, _, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    result = _publish(service, rag_scope, profile, attempt)
    assert service._reconcile(result._context, result._expected) is not None
    if damage in ('missing_second_receipt', 'wrong_second_receipt'):
        original = service._receipt
        calls = []

        def projected(cursor, scope, generation_id):
            receipt = original(cursor, scope, generation_id)
            if generation_id == result.pair.episode.generation_id:
                calls.append(generation_id)
                if damage == 'missing_second_receipt':
                    return None
                fields = list(receipt[1])
                fields[_RECEIPT_FIELDS.index('content_digest')] = '0' * 64
                return receipt[0], tuple(fields)
            return receipt

        monkeypatch.setattr(service, '_receipt', projected)
        assert service._reconcile(result._context, result._expected) is None
        assert calls == [result.pair.episode.generation_id]
        monkeypatch.setattr(service, '_receipt', original)
        assert service._reconcile(result._context, result._expected) is not None
    elif damage == 'ended_audit':
        original = service.pair._read_pair_evidence_in_cursor
        calls = []

        def projected(cursor, context):
            first, second = original(cursor, context)
            calls.append(context.generation_ids)
            return (first, dict(second) | {'attempt_ended_at': None})

        monkeypatch.setattr(service.pair, '_read_pair_evidence_in_cursor', projected)
        assert service._reconcile(result._context, result._expected) is None
        assert calls == [result._context.generation_ids]
        monkeypatch.setattr(service.pair, '_read_pair_evidence_in_cursor', original)
        assert service._reconcile(result._context, result._expected) is not None
    else:
        with db.transaction() as cursor:
            cursor.execute('''insert into memory_documents(user_id,document_id,
                content,metadata) values(%s,%s,%s,%s)''',
                (user, 'extra-' + uuid4().hex, 'extra', json.dumps({
                    'user_id': user, 'memory_type': 'semantic',
                    'import_task_id': attempt.task.task_id})))
        assert service._reconcile(result._context, result._expected) is None


@pytest.mark.parametrize('fault', ('success', 'db_callback_error'))
def test_terminal_has_no_external_io(memory_publication, monkeypatch, fault):
    service, db, store, rag, _, rag_scope, episode_scope, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    before_public = _public_members(service, rag_scope, episode_scope)
    active = [False]
    guarded_entries = []

    def guard(owner, name):
        original = getattr(owner, name)

        def checked(*args, **kwargs):
            if active[0]:
                guarded_entries.append((type(owner).__name__, name))
                pytest.fail(f'external I/O in terminal callback: {name}')
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, name, checked)

    for name in ('get_object', 'put_object', 'head_object', 'delete_object'):
        guard(store.client, name)
    for name in ('read_verified', 'put_immutable'):
        guard(store, name)
    for client in (rag.raw.client, service.pair.episode.raw.client):
        for name in ('upsert', 'scroll', 'delete', 'query_points',
                     'count', 'retrieve', 'search'):
            if hasattr(client, name):
                guard(client, name)
    for owner, name in ((SimpleEmbedding, 'encode'),
                        (RAGEmbeddingRuntime, 'embed_query'),
                        (RAGEmbeddingRuntime, 'embed_documents'),
                        (MemoryManager, 'add_memory'),
                        (MemoryManager, 'forget_memories'),
                        (MemoryManager, 'remove_memory'),
                        (MemoryManager, 'consolidate_memories'),
                        (MemoryManager, 'clear_all')):
        guard(owner, name)

    original_complete = service.pair.imports.complete
    original_terminal = service._terminal
    callback_entries = []
    before = []

    def terminal_then_fault(*args, **kwargs):
        original_terminal(*args, **kwargs)
        raise RuntimeError('injected DB-only callback error')

    if fault == 'db_callback_error':
        monkeypatch.setattr(service, '_terminal', terminal_then_fault)

    def guarded_complete(owner, callback):
        before.append(_state(db, rag_scope, episode_scope, user,
                             attempt.task.task_id))
        def guarded_callback(cursor):
            pid = cursor.execute('select pg_backend_pid() as pid').fetchone()['pid']
            locks = cursor.execute('''select count(*) as n from pg_locks
                where pid=%s and granted and locktype='relation'
                and mode in ('RowShareLock','RowExclusiveLock','ShareRowExclusiveLock')''',
                (pid,)).fetchone()['n']
            assert locks > 0
            callback_entries.append((pid, locks))
            active[0] = True
            try:
                callback(cursor)
            finally:
                active[0] = False
        return original_complete(owner, guarded_callback)

    monkeypatch.setattr(service.pair.imports, 'complete', guarded_complete)
    if fault == 'db_callback_error':
        with pytest.raises(RuntimeError, match='DB-only callback error'):
            _publish(service, rag_scope, profile, attempt)
        after = _state(db, rag_scope, episode_scope, user, attempt.task.task_id)
        assert len(before) == 1
        _assert_rolled_back(before[0], after, service, rag_scope, episode_scope)
        assert _public_members(service, rag_scope, episode_scope) == before_public
    else:
        result = _publish(service, rag_scope, profile, attempt)
        assert len(before) == 1
        _assert_committed(service, db, rag_scope, episode_scope, attempt, result)
    assert len(callback_entries) == 1
    assert guarded_entries == []
