"""Disposable opt-in RAG plus episode import publication acceptance."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import os
from uuid import uuid4

import pytest

from app.import_memory_publication import (
    ImportMemoryPublicationError, ImportMemoryPublicationService,
    ImportMemoryPublicationUnknown,
)
from app.postgres_vector_generations import PostgresVectorGenerationAuthority, VectorScope
from app.postgres_coordination import PostgresUserMutationCoordinator
from app.postgres_memory_documents import PostgresMemoryDocumentStore
from app.postgres_snapshots import PostgresSnapshotRepository
from app.published_episode_reads import EpisodeReadError, PublishedEpisodeReadFactory
from app.vector_generation_service import VectorGenerationService
from hello_agents.memory.rag.embedding_profile import EmbeddingProfile
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.storage.vector_store import QdrantVectorStore
from hello_agents.memory.storage.vector_store import VectorPoint
from hello_agents.memory.storage.generation_vector_store import GenerationVectorStore
from tests.integration.test_import_document_publication import publication, _point, _task
from tests.integration.test_published_episode_reads import (
    _publish as publish_episode_baseline, _item as episode_item,
    _payload as episode_payload,
)
from tests.integration.test_postgres_auth_sessions import shared_database
from tests.integration.test_s3_object_store import store


@pytest.fixture
def memory_publication(publication):
    document_service, db, store, rag, snapshots, rag_scope, user, other = publication
    raw = QdrantVectorStore(url=os.environ['GENERATION_QDRANT_TEST_URL'], retry_delays=())
    profile = EmbeddingProfile('test-explicit', '', 'episode-test', 'v1', 4)
    identity = IndexIdentity('qdrant', 'import_memory_' + uuid4().hex, profile)
    raw.ensure_collection(identity.physical_collection, 4)
    episode_scope = VectorScope(user, 'episode', 'episodes', identity)
    episode = VectorGenerationService(PostgresVectorGenerationAuthority(db), raw)
    publish_episode_baseline(db, episode, episode_scope, [])
    service = ImportMemoryPublicationService(db, store, rag, episode,
                                              trusted_episode_scope=episode_scope)
    try:
        yield service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, other
    finally:
        raw.client.delete_collection(identity.physical_collection)


def test_empty_episode_baseline_publishes_one_event_and_both_heads(memory_publication):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    attempt = _task(db, store, user, name='材料.txt')
    result = service.publish(rag_scope, attempt,
        [_point(rag_scope, attempt.task.document_id, '新文档')],
        event_vector=[0.5, 0.25, 0.125, 0.0], event_profile=profile)
    assert result.pair.import_task.status == 'succeeded'
    assert result.pair.rag.head.snapshot_version == result.history_version == 2
    assert result.pair.episode.head.snapshot_version == result.memory_version == 2
    history = snapshots.read(user, 'history').data
    memory = snapshots.read(user, 'memory').data
    assert len(history['documents']) == len(memory['memories']) == 1
    item = memory['memories'][0]
    assert item['id'] == result.event_id
    assert item['content'] == '用户导入了文档：材料.txt'
    assert item['metadata']['document_path'] == history['documents'][0]['document_path']
    assert 'event_type' not in item['metadata']
    with db.transaction() as cursor:
        row = cursor.execute('select content,metadata from memory_documents '
                             'where user_id=%s and document_id=%s',
                             (user, result.event_id)).fetchone()
    assert row['content'] == item['content']
    assert result.event_id == PublishedEpisodeReadFactory(
        service.pair.episode, episode_scope).open_operation(user).get_item(result.event_id).id
    assert service._reconcile(result._context, result._expected) is not None


@pytest.mark.parametrize('durable', [False, True])
@pytest.mark.parametrize('episode_dimension', [3, 4])
def test_rag_candidate_validation_keeps_distinct_episode_vector(
        publication, durable, episode_dimension):
    document_service, db, store, rag, snapshots, rag_scope, user, _ = publication
    raw = QdrantVectorStore(url=os.environ['GENERATION_QDRANT_TEST_URL'], retry_delays=())
    profile = EmbeddingProfile('test-explicit', '', 'episode-test', 'v1',
                               episode_dimension)
    identity = IndexIdentity('qdrant', 'import_memory_' + uuid4().hex, profile)
    raw.ensure_collection(identity.physical_collection, episode_dimension)
    episode_scope = VectorScope(user, 'episode', 'episodes', identity)
    episode = VectorGenerationService(PostgresVectorGenerationAuthority(db), raw)
    try:
        publish_episode_baseline(db, episode, episode_scope, [])
        service = ImportMemoryPublicationService(db, store, rag, episode,
                                                  trusted_episode_scope=episode_scope)
        attempt = _task(db, store, user)
        rag_point = _point(rag_scope, attempt.task.document_id, 'new')
        event_vector = [0., 1., *([0.] * (episode_dimension - 2))]
        plan = service._plan_intent(rag_scope, attempt, [rag_point],
                                    event_vector=event_vector, event_profile=profile)
        assert plan.vector == tuple(event_vector)
        if durable:
            live = service._issue_live_publication(rag_scope, attempt, [rag_point],
                event_vector=event_vector, event_profile=profile)
            result = service._execute_live_publication(live)
        else:
            result = service.publish(rag_scope, attempt, [rag_point],
                event_vector=event_vector, event_profile=profile)
        episode_points = PublishedEpisodeReadFactory(episode,
            episode_scope)._capture_bundle(user).points
        assert next(point.vector for point in episode_points
                    if point.id == result.event_id) == event_vector
    finally:
        raw.client.delete_collection(identity.physical_collection)


def _mixed_baseline(service, db, episode_scope, user):
    negative = episode_item(user, '旧负值', logical_id='old-negative')
    negative['importance'] = -0.5
    positive = episode_item(user, '旧正值', logical_id='old-positive')
    semantic = {'id': 'old-semantic', 'content': '旧语义', 'memory_type': 'semantic',
                'importance': 0.4, 'timestamp': '2026-10-01T00:00:00+00:00',
                'metadata': {'user_id': user, 'session_id': 'old'}}
    items = [negative, semantic, positive]
    old = PostgresSnapshotRepository(db).read(user, 'memory')
    lease = PostgresUserMutationCoordinator(db).acquire(user, 'mixed-baseline', lease_seconds=120)
    assert lease
    rows = [(item['id'], item['content'],
             json.dumps(episode_payload(item) if item['memory_type'] == 'episodic'
                        else {**item['metadata'], 'memory_type': 'semantic',
                              'content': item['content']},
                        ensure_ascii=False, indent=1)) for item in items]
    def domain(cursor):
        PostgresSnapshotRepository(db).compare_and_swap_in_transaction(
            cursor, user, 'memory', {'user_id': user, 'memories': items},
            expected_version=old.version)
        for doc_id, content, metadata in rows:
            PostgresMemoryDocumentStore(db, user).add_document_in_transaction(
                cursor, doc_id, content, metadata)
    try:
        service.pair.episode.publish_complete(episode_scope, lease,
            service.pair.episode.authority.read_head(episode_scope),
            [VectorPoint(item['id'], [1., 0., 0., 0.], episode_payload(item))
             for item in (negative, positive)],
            domain_publish=domain, snapshot_version=old.version + 1)
    finally:
        PostgresUserMutationCoordinator(db).release(lease)
    return deepcopy(items), rows


def test_mixed_prior_memory_retains_negative_episode_semantic_and_raw_row(memory_publication):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    old_items, old_rows = _mixed_baseline(service, db, episode_scope, user)
    attempt = _task(db, store, user)
    result = service.publish(rag_scope, attempt,
        [_point(rag_scope, attempt.task.document_id, 'new')],
        event_vector=[1., 0., 0., 0.], event_profile=profile)
    memory = snapshots.read(user, 'memory')
    assert memory.version == 3
    assert memory.data['memories'][:3] == old_items
    with db.transaction() as cursor:
        rows = cursor.execute('select document_id,metadata from memory_documents '
                              'where user_id=%s order by document_id', (user,)).fetchall()
    by_id = {row['document_id']: row['metadata'] for row in rows}
    assert all(by_id[doc_id] == metadata for doc_id, _, metadata in old_rows)
    operation = PublishedEpisodeReadFactory(service.pair.episode,
                                            episode_scope).open_operation(user)
    assert {point.id for point in operation.scroll(min_importance=-1)} == {
        'old-negative', 'old-positive', result.event_id}


def test_duplicate_task_in_semantic_row_rejected_before_document_put(memory_publication,
                                                                     monkeypatch):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    with db.transaction() as cursor:
        cursor.execute('''insert into memory_documents(user_id,document_id,content,metadata)
            values(%s,%s,%s,%s)''',
            (user, 'orphan-' + uuid4().hex, 'orphan', json.dumps({
                'user_id': user, 'memory_type': 'semantic',
                'import_task_id': attempt.task.task_id})))
    monkeypatch.setattr(store, 'put_immutable', lambda *args, **kwargs:
                        pytest.fail('document put after failed preflight'))
    with pytest.raises(ImportMemoryPublicationError):
        service.publish(rag_scope, attempt,
            [_point(rag_scope, attempt.task.document_id, 'new')],
            event_vector=[1., 0., 0., 0.], event_profile=profile)


def test_memory_semantic_drift_in_terminal_rolls_back_both_heads(memory_publication,
                                                                monkeypatch):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    old_rag = rag.authority.read_head(rag_scope)
    old_episode = service.pair.episode.authority.read_head(episode_scope)
    original = service.pair.publish_prepared_pair
    def drift(*args, **kwargs):
        current = snapshots.read(user, 'memory')
        changed = deepcopy(current.data)
        changed['note'] = True
        snapshots.compare_and_swap(user, 'memory', changed,
                                   expected_version=current.version)
        return original(*args, **kwargs)
    monkeypatch.setattr(service.pair, 'publish_prepared_pair', drift)
    with pytest.raises(ImportMemoryPublicationError):
        service.publish(rag_scope, attempt,
            [_point(rag_scope, attempt.task.document_id, 'new')],
            event_vector=[1., 0., 0., 0.], event_profile=profile)
    assert rag.authority.read_head(rag_scope) == old_rag
    assert service.pair.episode.authority.read_head(episode_scope) == old_episode
    assert snapshots.read(user, 'history').data['documents'] == []


def test_later_orphan_task_row_removes_strong_reconciliation_proof(memory_publication):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    result = service.publish(rag_scope, attempt,
        [_point(rag_scope, attempt.task.document_id, 'new')],
        event_vector=[1., 0., 0., 0.], event_profile=profile)
    with db.transaction() as cursor:
        cursor.execute('''insert into memory_documents(user_id,document_id,content,metadata)
            values(%s,%s,%s,%s)''', (user, 'orphan-' + uuid4().hex, 'orphan',
            json.dumps({'user_id': user, 'memory_type': 'semantic',
                        'import_task_id': attempt.task.task_id})))
    assert service._reconcile(result._context, result._expected) is None


@pytest.mark.parametrize('event_vector', [None, [True, 0, 0, 0],
    [float('nan'), 0, 0, 0], [1e308, 1e308, 1e308, 1e308], [1, 2]])
def test_invalid_explicit_vector_rejected_before_document_put(memory_publication,
                                                              monkeypatch, event_vector):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    puts = []
    original = store.put_immutable
    monkeypatch.setattr(store, 'put_immutable', lambda *args, **kwargs: (
        puts.append(args), original(*args, **kwargs))[1])
    with pytest.raises(ImportMemoryPublicationError):
        service.publish(rag_scope, attempt,
            [_point(rag_scope, attempt.task.document_id, 'new')],
            event_vector=event_vector, event_profile=profile)
    assert puts == []
    assert snapshots.read(user, 'memory').version == 1
    assert snapshots.read(user, 'history').version == 1


def test_changed_task_original_name_removes_strong_proof(memory_publication):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    result = service.publish(rag_scope, attempt,
        [_point(rag_scope, attempt.task.document_id, 'new')],
        event_vector=[1., 0., 0., 0.], event_profile=profile)
    with db.transaction() as cursor:
        cursor.execute('update import_tasks set original_name=%s where id=%s',
                       ('changed.txt', attempt.task.task_id))
    assert service._reconcile(result._context, result._expected) is None


def test_new_receipt_immutable_time_drift_removes_strong_proof(memory_publication,
                                                              monkeypatch):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    result = service.publish(rag_scope, attempt,
        [_point(rag_scope, attempt.task.document_id, 'new')],
        event_vector=[1., 0., 0., 0.], event_profile=profile)
    original = service._receipt
    def altered(cursor, scope, generation_id):
        receipt = original(cursor, scope, generation_id)
        if generation_id == result.pair.episode.generation_id:
            fields = list(receipt[1])
            fields[-1] = '2000-01-01T00:00:00.000000+00:00'
            return receipt[0], tuple(fields)
        return receipt
    monkeypatch.setattr(service, '_receipt', altered)
    assert service._reconcile(result._context, result._expected) is None


def test_two_imports_retire_both_first_receipts_but_keep_first_proof(memory_publication):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    first = _task(db, store, user, name='first.txt')
    old = service.publish(rag_scope, first,
        [_point(rag_scope, first.task.document_id, 'first')],
        event_vector=[1., 0., 0., 0.], event_profile=profile)
    second = _task(db, store, user, name='second.txt')
    latest = service.publish(rag_scope, second,
        [_point(rag_scope, second.task.document_id, 'second')],
        event_vector=[0., 1., 0., 0.], event_profile=profile)
    assert latest.history_version == latest.memory_version == 3
    assert len(snapshots.read(user, 'history').data['documents']) == 2
    assert len(snapshots.read(user, 'memory').data['memories']) == 2
    assert service._reconcile(old._context, old._expected) is not None
    assert service._reconcile(latest._context, latest._expected) is not None
    with db.transaction() as cursor:
        old_states = cursor.execute('''select vector_kind,state from vector_generations
            where generation_id=any(%s) order by vector_kind''',
            ([old.pair.rag.generation_id, old.pair.episode.generation_id],)).fetchall()
    assert [row['state'] for row in old_states] == ['retired', 'retired']


def test_lost_completion_response_after_actual_commit_reconciles(memory_publication,
                                                                 monkeypatch):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    original = service.pair.imports.complete
    calls = []
    def response_lost(*args, **kwargs):
        calls.append(original(*args, **kwargs))
        raise TimeoutError('response lost after commit')
    monkeypatch.setattr(service.pair.imports, 'complete', response_lost)
    # B's independent pair-only read is undecided; C must prove pair and domain
    # together using its own one-snapshot reader and frozen callback receipts.
    monkeypatch.setattr(service.pair, 'reconcile_pair', lambda context: None)
    result = service.publish(rag_scope, attempt,
        [_point(rag_scope, attempt.task.document_id, 'new')],
        event_vector=[1., 0., 0., 0.], event_profile=profile)
    assert len(calls) == 1
    assert result.pair.import_task.status == 'succeeded'
    assert service._reconcile(result._context, result._expected) is not None


def test_unknown_diagnostic_mutation_cannot_change_frozen_reconciliation(memory_publication):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    result = service.publish(rag_scope, attempt,
        [_point(rag_scope, attempt.task.document_id, 'new')],
        event_vector=[1., 0., 0., 0.], event_profile=profile)
    unknown = ImportMemoryPublicationUnknown(result._context, result._expected, 'diagnostic')
    unknown.expected.item['content'] = 'forged'
    assert service.reconcile(unknown) is not None


def test_later_deletion_of_event_proof_returns_unknown(memory_publication):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    result = service.publish(rag_scope, attempt,
        [_point(rag_scope, attempt.task.document_id, 'new')],
        event_vector=[1., 0., 0., 0.], event_profile=profile)
    with db.transaction() as cursor:
        cursor.execute('delete from memory_documents where user_id=%s and document_id=%s',
                       (user, result.event_id))
    assert service._reconcile(result._context, result._expected) is None


def test_episode_insert_failure_rolls_back_pair_and_document(memory_publication,
                                                             monkeypatch):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    before_rag = rag.authority.read_head(rag_scope)
    before_episode = service.pair.episode.authority.read_head(episode_scope)
    monkeypatch.setattr(PostgresMemoryDocumentStore, 'add_document_in_transaction',
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            RuntimeError('injected episode insert failure')))
    with pytest.raises(RuntimeError, match='injected episode insert failure'):
        service.publish(rag_scope, attempt,
            [_point(rag_scope, attempt.task.document_id, 'new')],
            event_vector=[1., 0., 0., 0.], event_profile=profile)
    assert rag.authority.read_head(rag_scope) == before_rag
    assert service.pair.episode.authority.read_head(episode_scope) == before_episode
    assert snapshots.read(user, 'history').version == 1
    assert snapshots.read(user, 'memory').version == 1
    with db.transaction() as cursor:
        assert cursor.execute('select count(*) as n from document_objects where user_id=%s',
                              (user,)).fetchone()['n'] == 0


@pytest.mark.parametrize('damage', ['profile', 'bound', 'missing_head',
                                    'memory_version', 'foreign_user'])
def test_episode_preflight_refusal_has_no_document_put(memory_publication,
                                                       monkeypatch, damage):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, other = memory_publication
    attempt = _task(db, store, user)
    selected_profile = profile
    selected_rag_scope = rag_scope
    if damage == 'profile':
        selected_profile = replace(profile, dimension=8)
    elif damage == 'bound':
        monkeypatch.setattr('app.import_memory_publication._MAX_POINTS', 0)
    elif damage == 'missing_head':
        service = ImportMemoryPublicationService(db, store, rag, service.pair.episode,
            trusted_episode_scope=replace(episode_scope, namespace='unpaired'))
    elif damage == 'memory_version':
        current = snapshots.read(user, 'memory')
        snapshots.compare_and_swap(user, 'memory', deepcopy(current.data),
                                   expected_version=current.version)
    elif damage == 'foreign_user':
        selected_rag_scope = replace(rag_scope, tenant_id=other)
    monkeypatch.setattr(store, 'put_immutable', lambda *args, **kwargs:
                        pytest.fail('document put after failed episode preflight'))
    with pytest.raises(EpisodeReadError if damage in ('missing_head', 'memory_version')
                       else ImportMemoryPublicationError):
        service.publish(selected_rag_scope, attempt,
            [_point(rag_scope, attempt.task.document_id, 'new')],
            event_vector=[1., 0., 0., 0.], event_profile=selected_profile)


def test_raw_memory_document_metadata_drift_in_callback_rolls_back(memory_publication,
                                                                   monkeypatch):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    _mixed_baseline(service, db, episode_scope, user)
    attempt = _task(db, store, user)
    old_rag = rag.authority.read_head(rag_scope)
    old_episode = service.pair.episode.authority.read_head(episode_scope)
    original = service.pair.publish_prepared_pair
    def drift(*args, **kwargs):
        with db.transaction() as cursor:
            row = cursor.execute('select metadata from memory_documents where user_id=%s '
                                 'and document_id=%s', (user, 'old-semantic')).fetchone()
            cursor.execute('update memory_documents set metadata=%s where user_id=%s '
                           'and document_id=%s',
                           (json.dumps(json.loads(row['metadata']), separators=(',', ':')),
                            user, 'old-semantic'))
        return original(*args, **kwargs)
    monkeypatch.setattr(service.pair, 'publish_prepared_pair', drift)
    with pytest.raises(ImportMemoryPublicationError):
        service.publish(rag_scope, attempt,
            [_point(rag_scope, attempt.task.document_id, 'new')],
            event_vector=[1., 0., 0., 0.], event_profile=profile)
    assert rag.authority.read_head(rag_scope) == old_rag
    assert service.pair.episode.authority.read_head(episode_scope) == old_episode
    assert snapshots.read(user, 'history').version == 1


def test_caller_mutation_during_source_read_cannot_change_event_or_rag(memory_publication,
                                                                       monkeypatch):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    point = _point(rag_scope, attempt.task.document_id, 'original')
    original_id = point.id
    vector = [1., 0., 0., 0.]
    original = service.documents.sources.read_source_bytes
    def mutate(*args, **kwargs):
        point.vector[0] = 0.0
        point.vector[1] = 1.0
        point.payload['content'] = 'changed'
        vector[0] = 0.0
        vector[1] = 1.0
        return original(*args, **kwargs)
    monkeypatch.setattr(service.documents.sources, 'read_source_bytes', mutate)
    result = service.publish(rag_scope, attempt, [point], event_vector=vector,
                             event_profile=profile)
    rag_points = GenerationVectorStore(rag.raw, rag_scope, result.pair.rag.head).scroll(
        rag_scope.identity.physical_collection, with_vectors=True)
    assert len(rag_points) == 1
    assert rag_points[0].id == original_id
    assert rag_points[0].payload['content'] == 'original'
    episode_points = PublishedEpisodeReadFactory(service.pair.episode,
                                                  episode_scope)._capture_bundle(user).points
    assert next(point.vector for point in episode_points
                if point.id == result.event_id) == [1., 0., 0., 0.]
