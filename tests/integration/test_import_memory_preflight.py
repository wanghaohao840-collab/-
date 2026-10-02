"""Disposable, full zero-write oracle for C import preflight refusals."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from uuid import uuid4, uuid5

import pytest

from app.import_memory_publication import ImportMemoryPublicationError, ImportMemoryPublicationService
from app.import_models import ImportTaskCreate
from app.import_repository import PostgresImportTaskRepository
from app.object_store import artifact_key
from app.postgres_coordination import PostgresUserMutationCoordinator
from app.postgres_history_document_witnesses import document_evidence
from app.postgres_import_leases import PostgresImportLeaseRepository
from app.postgres_snapshots import PostgresSnapshotRepository
from app.published_episode_reads import EpisodeReadError, PublishedEpisodeReadFactory
from hello_agents.memory.rag.prepare import PROJECT_POINT_NAMESPACE_UUID
from tests.integration.test_import_document_publication import _point, publication
from tests.integration.test_import_memory_fault_matrix import _public_members, _state
from tests.integration.test_import_memory_publication import memory_publication
from tests.integration.test_published_episode_reads import (
    _item as episode_item, _publish as publish_episode_baseline,
)
from tests.integration.test_postgres_auth_sessions import shared_database
from tests.integration.test_s3_object_store import store


@pytest.fixture(autouse=True)
def _resource_ledger(request, memory_publication):
    service, db, store, _, _, rag_scope, episode_scope, _, user, _ = memory_publication
    yield
    with db.transaction() as cursor:
        schema = cursor.execute('select current_schema() as schema').fetchone()['schema']
        generations = cursor.execute('''select generation_id,task_id,vector_kind,state,
            expected_count,content_digest from vector_generations where tenant_id=%s
            order by vector_kind,generation_id''', (user,)).fetchall()
    request.node.user_properties.append(('resource_ledger', json.dumps({
        'schema': schema, 'bucket': store.bucket,
        'rag_collection': rag_scope.identity.physical_collection,
        'episode_collection': episode_scope.identity.physical_collection,
        'generations': [dict(row) for row in generations],
    }, default=str)))


def _task_with_ids(db, store, user, task_id, document_id):
    """Create and claim a real task with IDs chosen before its old publication."""
    content = b'preflight collision source'
    digest = hashlib.sha256(content).hexdigest()
    key = artifact_key(user, 'imports', task_id, '.txt', digest)
    ref = store.put_immutable(user, key, content).ref
    batch_id = str(uuid4())
    with db.transaction() as cursor:
        now = cursor.execute('select clock_timestamp() as now').fetchone()['now'].isoformat()
        PostgresImportTaskRepository(db).create_batch_in_transaction(
            cursor, user, [ImportTaskCreate(task_id, batch_id, user, document_id,
                                           'new.txt', '.txt', len(content), '')], now=now)
        cursor.execute('''insert into import_objects
            (task_id,user_id,bucket,object_key,version_id,sha256,size_bytes)
            values (%s,%s,%s,%s,%s,%s,%s)''',
            (task_id, user, store.bucket, ref.key, ref.version_id, ref.sha256,
             ref.size_bytes))
    attempt = PostgresImportLeaseRepository(db).claim_next('preflight-' + uuid4().hex,
                                                            lease_seconds=180)
    assert attempt is not None and attempt.task.task_id == task_id
    return attempt


def _publish_old_history(service, db, store, rag_scope, user, document_id,
                         *, prior_task_id=None):
    """Publish a real, pinned History/RAG pair carrying an optional task marker."""
    content = b'old retained document'
    digest = hashlib.sha256(content).hexdigest()
    key = artifact_key(user, 'documents', document_id, '.txt', digest)
    ref = store.put_immutable(user, key, content).ref
    verified = service.documents.documents.verify_for_publication(user, document_id, ref)
    record = {'user_id': user, 'document_id': document_id,
              'document_name': 'old.txt', 'file_suffix': '.txt',
              'document_path': f'object://{store.bucket}/{key}',
              'loaded_at': '2026-10-01T00:00:00+00:00'}
    if prior_task_id is not None:
        record['import_task_id'] = prior_task_id
    snapshots = PostgresSnapshotRepository(db)
    old = snapshots.read(user, 'history')
    changed = deepcopy(old.data)
    changed['documents'].append(record)
    coordinator = PostgresUserMutationCoordinator(db)
    lease = coordinator.acquire(user, 'preflight-old-history', lease_seconds=120)
    assert lease is not None

    def domain(cursor):
        snapshots.compare_and_swap_in_transaction(cursor, user, 'history', changed,
                                                   expected_version=old.version)
        service.documents.documents.publish_in_transaction(cursor, verified)

    try:
        service.pair.rag.publish_complete(rag_scope, lease,
            service.pair.rag.authority.read_head(rag_scope),
            [_point(rag_scope, document_id, 'old')], domain_publish=domain,
            snapshot_version=old.version + 1)
    finally:
        coordinator.release(lease)
    pairing = service.documents.witnesses.read_current(rag_scope)
    with db.transaction() as cursor:
        service.documents.witnesses.insert(cursor, rag_scope, pairing,
                                            document_evidence(user, changed))
    assert service.documents.witnesses.read_current(rag_scope).has_witness
    assert service.documents.documents.read_document_bytes(user, document_id) == content


def _full_state(db, rag_scope, episode_scope, user, task_id):
    state = _state(db, rag_scope, episode_scope, user, task_id)
    with db.transaction() as cursor:
        state['all_receipts'] = [dict(row) for row in cursor.execute(
            '''select * from vector_generations where tenant_id=%s
               order by vector_kind,generation_id''', (user,)).fetchall()]
        state['sources'] = [dict(row) for row in cursor.execute(
            '''select * from import_objects where user_id=%s order by task_id''',
            (user,)).fetchall()]
    return state


def _versions(store):
    return sorted((entry['Key'], entry['VersionId'])
        for page in store.client.get_paginator('list_object_versions').paginate(
            Bucket=store.bucket)
        for entry in page.get('Versions', []) + page.get('DeleteMarkers', []))


def _fence_writes(monkeypatch, service, store):
    calls = []
    def forbidden(name):
        def fail(*args, **kwargs):
            calls.append(name)
            pytest.fail(f'external write after rejected preflight: {name}')
        return fail
    monkeypatch.setattr(store, 'put_immutable', forbidden('s3.put_immutable'))
    monkeypatch.setattr(store.client, 'put_object', forbidden('s3.client.put_object'))
    for name, vector_service in (('rag', service.pair.rag),
                                 ('episode', service.pair.episode)):
        monkeypatch.setattr(vector_service.authority, 'stage', forbidden(name + '.stage'))
        monkeypatch.setattr(vector_service.raw, 'upsert', forbidden(name + '.raw.upsert'))
        monkeypatch.setattr(vector_service.raw.client, 'upsert',
                            forbidden(name + '.client.upsert'))
    return calls


def _assert_refusal(memory_publication, monkeypatch, attempt, *, expected,
                    error=ImportMemoryPublicationError, selected_scope=None,
                    selected_profile=None, vector=None, public=True, service=None):
    base, db, store, _, _, rag_scope, episode_scope, profile, user, _ = memory_publication
    service = service or base
    before = _full_state(db, rag_scope, episode_scope, user, attempt.task.task_id)
    memberships = _public_members(base, rag_scope, episode_scope) if public else None
    versions = _versions(store)
    physical = (base.pair.rag.raw.count(rag_scope.identity.physical_collection),
                base.pair.episode.raw.count(episode_scope.identity.physical_collection))
    calls = _fence_writes(monkeypatch, service, store)
    with pytest.raises(error, match=expected):
        service.publish(selected_scope or rag_scope, attempt,
            [_point(rag_scope, attempt.task.document_id, 'new')],
            event_vector=[1., 0., 0., 0.] if vector is None else vector,
            event_profile=selected_profile or profile)
    assert calls == []
    assert _full_state(db, rag_scope, episode_scope, user, attempt.task.task_id) == before
    assert _versions(store) == versions
    assert (base.pair.rag.raw.count(rag_scope.identity.physical_collection),
            base.pair.episode.raw.count(episode_scope.identity.physical_collection)) == physical
    if public:
        assert _public_members(base, rag_scope, episode_scope) == memberships
    assert not any(row['task_id'] == attempt.task.task_id for row in
                   before['all_receipts'])


@pytest.mark.parametrize('collision', ('history_document', 'history_task',
                                       'episode_event', 'episode_task'))
def test_attested_identity_collisions_are_zero_write(memory_publication,
                                                      monkeypatch, collision):
    service, db, store, _, _, rag_scope, episode_scope, _, user, _ = memory_publication
    task_id = str(uuid4())
    document_id = str(uuid4())
    event_id = 'import-' + str(uuid5(PROJECT_POINT_NAMESPACE_UUID,
                                     f'{user}:{task_id}'))
    if collision.startswith('history'):
        _publish_old_history(service, db, store, rag_scope, user,
            document_id if collision == 'history_document' else str(uuid4()),
            prior_task_id=task_id if collision == 'history_task' else None)
    else:
        old = episode_item(user, 'attested old episode',
            logical_id=event_id if collision == 'episode_event' else 'old-' + uuid4().hex)
        if collision == 'episode_task':
            old['metadata']['import_task_id'] = task_id
        publish_episode_baseline(db, service.pair.episode, episode_scope, [old])
        bundle = PublishedEpisodeReadFactory(service.pair.episode,
                                             episode_scope)._capture_bundle(user)
        assert bundle.receipt['expected_count'] == 1
        assert len(bundle.points) == len(bundle.documents) == len(bundle.snapshot['memories']) == 1
    attempt = _task_with_ids(db, store, user, task_id, document_id)
    predicate = ('Document or task already in History' if collision.startswith('history')
                 else 'Import event or task already exists')
    _assert_refusal(memory_publication, monkeypatch, attempt, expected=predicate)


def test_reader_accepted_semantic_shape_is_rejected_by_all_memory(
        memory_publication, monkeypatch):
    service, db, store, _, _, rag_scope, episode_scope, _, user, _ = memory_publication
    snapshots = PostgresSnapshotRepository(db)
    old = snapshots.read(user, 'memory')
    item = {'content': 'old semantic row without snapshot ID',
            'memory_type': 'semantic', 'metadata': {'user_id': user}}
    lease = PostgresUserMutationCoordinator(db).acquire(
        user, 'preflight-semantic-shape', lease_seconds=120)
    assert lease is not None

    def domain(cursor):
        snapshots.compare_and_swap_in_transaction(cursor, user, 'memory',
            {'user_id': user, 'memories': [item]}, expected_version=old.version)
        cursor.execute('''insert into memory_documents(user_id,document_id,content,metadata)
            values(%s,%s,%s,%s)''',
            (user, 'old-semantic-row', item['content'], json.dumps({
                'user_id': user, 'memory_type': 'semantic', 'content': item['content']})))

    try:
        service.pair.episode.publish_complete(episode_scope, lease,
            service.pair.episode.authority.read_head(episode_scope), [],
            domain_publish=domain, snapshot_version=old.version + 1)
    finally:
        PostgresUserMutationCoordinator(db).release(lease)
    bundle = service.episodes._capture_bundle(user)
    assert bundle.receipt['expected_count'] == 0
    assert len(bundle.snapshot['memories']) == len(bundle.documents) == 1
    attempt = _task_with_ids(db, store, user, str(uuid4()), str(uuid4()))
    _assert_refusal(memory_publication, monkeypatch, attempt,
                    expected='Memory identity is invalid')


@pytest.mark.parametrize('case,expected,error', [
    ('vector_bool', 'Event vector is invalid', ImportMemoryPublicationError),
    ('vector_nan', 'Event vector is invalid', ImportMemoryPublicationError),
    ('vector_dimension', 'Explicit event vector dimension differs', ImportMemoryPublicationError),
    ('profile', 'Episode profile differs', ImportMemoryPublicationError),
    ('scope', 'Import scopes or user differ', ImportMemoryPublicationError),
    ('missing_head', 'Episode index is absent or differs', EpisodeReadError),
    ('memory_version', 'Episode head and snapshot differ', EpisodeReadError),
    ('semantic_raw_task', 'Import event or task already exists', ImportMemoryPublicationError),
])
def test_existing_preflight_refusals_have_full_zero_write_oracle(
        memory_publication, monkeypatch, case, expected, error):
    service, db, store, _, snapshots, rag_scope, episode_scope, profile, user, other = memory_publication
    attempt = _task_with_ids(db, store, user, str(uuid4()), str(uuid4()))
    options = {}
    if case == 'vector_bool':
        options['vector'] = [True, 0, 0, 0]
    elif case == 'vector_nan':
        options['vector'] = [float('nan'), 0, 0, 0]
    elif case == 'vector_dimension':
        options['vector'] = [1, 0]
    elif case == 'profile':
        options['selected_profile'] = replace(profile, dimension=8)
    elif case == 'scope':
        options['selected_scope'] = replace(rag_scope, tenant_id=other)
    elif case == 'missing_head':
        options['service'] = ImportMemoryPublicationService(db, store,
            service.pair.rag, service.pair.episode,
            trusted_episode_scope=replace(episode_scope, namespace='unpaired'))
        options['public'] = False
    elif case == 'memory_version':
        current = snapshots.read(user, 'memory')
        snapshots.compare_and_swap(user, 'memory', deepcopy(current.data),
                                   expected_version=current.version)
        options['public'] = False
    elif case == 'semantic_raw_task':
        with db.transaction() as cursor:
            cursor.execute('''insert into memory_documents(user_id,document_id,content,metadata)
                values(%s,%s,%s,%s)''',
                (user, 'orphan-' + uuid4().hex, 'orphan', json.dumps({
                    'user_id': user, 'memory_type': 'semantic',
                    'import_task_id': attempt.task.task_id})))
        options['public'] = False
    _assert_refusal(memory_publication, monkeypatch, attempt,
                    expected=expected, error=error, **options)
