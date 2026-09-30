"""Opt-in document import publication across disposable PG, S3 and Qdrant."""
from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
import hashlib
import os
from uuid import uuid4

import pytest

from app.history import EMPTY_HISTORY
from app.import_document_publication import (
    ImportDocumentPublicationError, ImportDocumentPublicationService,
)
from app.import_models import ImportTaskCreate
from app.import_repository import PostgresImportTaskRepository
from app.object_store import artifact_key
from app.postgres_coordination import PostgresUserMutationCoordinator
from app.postgres_document_objects import DocumentPublicationError
from app.postgres_import_leases import PostgresImportLeaseRepository
from app.postgres_snapshots import PostgresSnapshotRepository, SnapshotConflict
from app.postgres_vector_generations import PostgresVectorGenerationAuthority, VectorScope
from app.vector_generation_service import VectorGenerationService
from app.vector_generation_service import VectorPublicationUnknown
from hello_agents.memory.rag.embedding_profile import EmbeddingProfile
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.storage.vector_store import QdrantVectorStore, VectorPoint
from hello_agents.memory.storage.generation_vector_store import (
    GenerationVectorStoreError, _physical_id,
)
from tests.integration.test_postgres_auth_sessions import shared_database
from tests.integration.test_s3_object_store import store


@pytest.fixture
def publication(shared_database, store):
    qdrant_url = os.environ.get('GENERATION_QDRANT_TEST_URL')
    if not qdrant_url:
        pytest.skip('GENERATION_QDRANT_TEST_URL is required')
    open_pool, _ = shared_database
    db = open_pool()
    user, other = str(uuid4()), str(uuid4())
    with db.transaction() as cursor:
        for identity in (user, other):
            cursor.execute('insert into users values (%s,%s,%s,%s,%s,%s,%s)',
                           (identity, identity, identity, 'hash', 'active', 'now', 'now'))
    raw = QdrantVectorStore(url=qdrant_url, retry_delays=())
    identity = IndexIdentity('qdrant', 'import_atomic_' + uuid4().hex,
                             EmbeddingProfile('simple', '', 'deterministic', 'v1', 4))
    raw.ensure_collection(identity.physical_collection, 4)
    scope = VectorScope(user, 'rag', f'pdf_{user}', identity)
    vectors = VectorGenerationService(PostgresVectorGenerationAuthority(db), raw)
    snapshots = PostgresSnapshotRepository(db)
    coordinator = PostgresUserMutationCoordinator(db)
    lease = coordinator.acquire(user, 'bootstrap', lease_seconds=120)
    assert lease is not None
    vectors.publish_complete(scope, lease, vectors.authority.read_head(scope), [],
        domain_publish=lambda cursor: snapshots.compare_and_swap_in_transaction(
            cursor, user, 'history', dict(EMPTY_HISTORY), expected_version=0),
        snapshot_version=1)
    coordinator.release(lease)
    service = ImportDocumentPublicationService(db, store, vectors)
    try:
        yield service, db, store, vectors, snapshots, scope, user, other
    finally:
        raw.client.delete_collection(identity.physical_collection)


def _task(db, store, user, content=b'accepted bytes', name='paper.txt'):
    task_id, batch_id, document_id = str(uuid4()), str(uuid4()), str(uuid4())
    digest = hashlib.sha256(content).hexdigest()
    key = artifact_key(user, 'imports', task_id, '.txt', digest)
    ref = store.put_immutable(user, key, content).ref
    with db.transaction() as cursor:
        now = cursor.execute('select clock_timestamp() as now').fetchone()['now'].isoformat()
        PostgresImportTaskRepository(db).create_batch_in_transaction(
            cursor, user, [ImportTaskCreate(task_id, batch_id, user, document_id,
                                           name, '.txt', len(content), '')], now=now)
        cursor.execute('''insert into import_objects
            (task_id,user_id,bucket,object_key,version_id,sha256,size_bytes)
            values (%s,%s,%s,%s,%s,%s,%s)''',
            (task_id, user, store.bucket, ref.key, ref.version_id, ref.sha256,
             ref.size_bytes))
    attempt = PostgresImportLeaseRepository(db).claim_next('worker-' + uuid4().hex)
    assert attempt and attempt.task.task_id == task_id
    return attempt


def _point(scope, document_id, marker, logical_id=None):
    return VectorPoint(logical_id or str(uuid4()), [1., 0., 0., 0.], {
        'document_id': document_id, 'rag_namespace': scope.namespace,
        'content': marker, 'chunk_index': 0,
        'metadata': {'embedding_fingerprint': scope.identity.profile.fingerprint,
                     'file_name': 'paper.txt'},
    })


def _state(db, snapshots, vectors, scope, user, task_id):
    with db.transaction() as cursor:
        task = cursor.execute('select status,stage from import_tasks where id=%s',
                              (task_id,)).fetchone()
        ended = cursor.execute('select ended_at from import_task_attempts where task_id=%s',
                               (task_id,)).fetchone()['ended_at']
        refs = cursor.execute('select count(*) as n from document_objects where user_id=%s',
                              (user,)).fetchone()['n']
        lease = cursor.execute('select lease_expires_at>clock_timestamp() as live '
                               'from user_mutation_leases where user_id=%s',
                               (user,)).fetchone()
    return snapshots.read(user, 'history'), vectors.authority.read_head(scope), task, ended, refs, lease


def test_two_imports_commit_all_authorities_and_preserve_pinned_bytes(publication):
    service, db, store, vectors, snapshots, scope, user, _ = publication
    first = _task(db, store, user)
    # A later write to the same key cannot replace the accepted source version.
    store.client.put_object(Bucket=store.bucket, Key=first.source.key,
                            Body=b'latest is not accepted')
    p1 = _point(scope, first.task.document_id, 'first')
    result1 = service.publish(scope, first, [p1])
    assert result1.import_task.status == 'succeeded'
    assert service.documents.read_document_bytes(user, first.task.document_id) == b'accepted bytes'
    second = _task(db, store, user, b'second bytes')
    p2 = _point(scope, second.task.document_id, 'second')
    result2 = service.publish(scope, second, [p2])
    history, head, task, ended, refs, lease = _state(
        db, snapshots, vectors, scope, user, second.task.task_id)
    assert head == result2.head and head.snapshot_version == history.version == 3
    assert task['status'] == 'succeeded' and ended is not None and refs == 2
    assert lease is not None and not lease['live']
    assert {item['document_id'] for item in history.data['documents']} == {
        first.task.document_id, second.task.document_id}
    assert all(item['document_path'].startswith('object://') and
               item['document_path'].endswith('.txt') for item in history.data['documents'])
    assert {point.id for point in vectors.read_view(scope).scroll(
        scope.identity.physical_collection, with_vectors=True)} == {p1.id, p2.id}
    assert service.documents.read_document_bytes(user, second.task.document_id) == b'second bytes'
    from app.postgres_document_search import _SnapshotDocumentProjection
    assert len(_SnapshotDocumentProjection().project_history_documents(
        user, history.data['documents'])) == 2


@pytest.mark.parametrize('failure', ('history', 'reference', 'fence'))
def test_publication_callback_failures_roll_back_every_authority(publication, monkeypatch, failure):
    service, db, store, vectors, snapshots, scope, user, _ = publication
    attempt = _task(db, store, user)
    before = snapshots.read(user, 'history'), vectors.authority.read_head(scope)
    if failure == 'history':
        def reject(*args, **kwargs):
            raise SnapshotConflict('forced CAS conflict')
        monkeypatch.setattr(service.snapshots, 'compare_and_swap_in_transaction', reject)
    elif failure == 'reference':
        def reject(*args, **kwargs):
            raise DocumentPublicationError('forced reference failure')
        monkeypatch.setattr(service.documents, 'publish_in_transaction', reject)
    else:
        with db.transaction() as cursor:
            cursor.execute('''insert into qa_deletion_fences
                (id,user_id,target_type,target_id,status,stage,attempt_count,created_at,updated_at)
                values (%s,%s,'document',%s,'queued','fenced',0,'now','now')''',
                (str(uuid4()), user, attempt.task.document_id))
    with pytest.raises((SnapshotConflict, DocumentPublicationError,
                        ImportDocumentPublicationError)):
        service.publish(scope, attempt, [_point(scope, attempt.task.document_id, 'new')])
    history, head, task, ended, refs, lease = _state(
        db, snapshots, vectors, scope, user, attempt.task.task_id)
    assert (history, head) == before
    assert task['status'] == 'running' and ended is None and refs == 0
    assert lease['live']


def test_forged_task_scope_and_history_drift_fail_closed(publication):
    service, db, store, vectors, snapshots, scope, user, other = publication
    attempt = _task(db, store, user)
    point = _point(scope, attempt.task.document_id, 'new')
    with pytest.raises(ImportDocumentPublicationError, match='Attempt task'):
        service.publish(scope, replace(attempt, task=replace(
            attempt.task, document_id=str(uuid4()))), [point])
    with pytest.raises(ImportDocumentPublicationError, match='tenant'):
        service.publish(replace(scope, tenant_id=other), attempt, [point])
    with pytest.raises(ImportDocumentPublicationError, match='namespace'):
        service.publish(replace(scope, namespace='documents'), attempt, [point])
    with pytest.raises(ImportDocumentPublicationError, match='New point'):
        service.publish(scope, attempt, [_point(scope, other, 'wrong')])
    wrong_metadata = _point(scope, attempt.task.document_id, 'wrong')
    wrong_metadata.payload['metadata']['document_id'] = other
    with pytest.raises(ImportDocumentPublicationError, match='New point'):
        service.publish(scope, attempt, [wrong_metadata])
    snapshots.compare_and_swap(user, 'history', dict(EMPTY_HISTORY), expected_version=1)
    with pytest.raises(ImportDocumentPublicationError, match='not paired'):
        service.publish(scope, attempt, [point])
    assert vectors.authority.read_head(scope).snapshot_version == 1


def test_expired_attempt_is_rejected_without_publication(publication):
    service, db, store, vectors, snapshots, scope, user, _ = publication
    attempt = _task(db, store, user)
    with db.transaction() as cursor:
        cursor.execute("update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second' where id=%s",
                       (attempt.task.task_id,))
    with pytest.raises(Exception, match='live'):
        service.publish(scope, attempt, [_point(scope, attempt.task.document_id, 'new')])
    assert snapshots.read(user, 'history').version == 1
    assert vectors.authority.read_head(scope).revision == 1


@pytest.mark.parametrize('damage', ('missing_point', 'same_count_payload', 'missing_digest'))
def test_old_manifest_scan_refuses_incomplete_or_changed_corpus(publication, monkeypatch, damage):
    service, db, store, vectors, snapshots, scope, user, _ = publication
    first = _task(db, store, user)
    point = _point(scope, first.task.document_id, 'old')
    service.publish(scope, first, [point])
    head = vectors.authority.read_head(scope)
    physical = _physical_id(scope, head.generation_id, point.id)
    collection = scope.identity.physical_collection
    if damage == 'missing_point':
        original = vectors.raw.client.scroll
        def truncated(*args, **kwargs):
            batch, offset = original(*args, **kwargs)
            if kwargs.get('with_vectors'):
                return [], None
            return batch, offset
        monkeypatch.setattr(vectors.raw.client, 'scroll', truncated)
    elif damage == 'same_count_payload':
        vectors.raw.client.set_payload(collection_name=collection,
            points=[physical], payload={'content': 'unauthorized edit'})
    else:
        vectors.raw.client.delete_payload(collection_name=collection,
            points=[physical], keys=['_gv_vector_digest'])
    second = _task(db, store, user, b'new')
    with pytest.raises(GenerationVectorStoreError, match='manifest|digest'):
        service.publish(scope, second, [_point(scope, second.task.document_id, 'new')])
    assert vectors.authority.read_head(scope) == head
    assert snapshots.read(user, 'history').version == 2


def test_retained_document_fence_inserted_after_candidate_staging_rolls_back(publication, monkeypatch):
    service, db, store, vectors, snapshots, scope, user, _ = publication
    first = _task(db, store, user)
    service.publish(scope, first, [_point(scope, first.task.document_id, 'old')])
    second = _task(db, store, user, b'new')
    before = snapshots.read(user, 'history'), vectors.authority.read_head(scope)
    original = vectors.authority.complete_import
    def fence_then_complete(*args, **kwargs):
        with db.transaction() as cursor:
            cursor.execute('''insert into qa_deletion_fences
                (id,user_id,target_type,target_id,status,stage,attempt_count,created_at,updated_at)
                values (%s,%s,'document',%s,'queued','fenced',0,'now','now')''',
                (str(uuid4()), user, first.task.document_id))
        return original(*args, **kwargs)
    monkeypatch.setattr(vectors.authority, 'complete_import', fence_then_complete)
    with pytest.raises(ImportDocumentPublicationError, match='fence'):
        service.publish(scope, second, [_point(scope, second.task.document_id, 'new')])
    history, head, task, ended, refs, lease = _state(
        db, snapshots, vectors, scope, user, second.task.task_id)
    assert (history, head) == before
    assert task['status'] == 'running' and ended is None and refs == 1
    assert lease['live']


def test_stale_reclaim_after_candidate_staging_cannot_publish(publication, monkeypatch):
    service, db, store, vectors, snapshots, scope, user, _ = publication
    attempt = _task(db, store, user)
    before = snapshots.read(user, 'history'), vectors.authority.read_head(scope)
    original = vectors.authority.complete_import
    def reclaim_then_complete(*args, **kwargs):
        with db.transaction() as cursor:
            cursor.execute("update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second' where id=%s",
                           (attempt.task.task_id,))
        assert service.imports.recover_expired() == 1
        return original(*args, **kwargs)
    monkeypatch.setattr(vectors.authority, 'complete_import', reclaim_then_complete)
    with pytest.raises(VectorPublicationUnknown, match='publication'):
        service.publish(scope, attempt, [_point(scope, attempt.task.document_id, 'new')])
    history, head, task, ended, refs, _ = _state(
        db, snapshots, vectors, scope, user, attempt.task.task_id)
    assert (history, head) == before
    assert task['status'] == 'queued' and ended is not None and refs == 0


def test_lost_pg_commit_response_uses_receipt_without_replay(publication, monkeypatch):
    service, db, store, vectors, snapshots, scope, user, _ = publication
    attempt = _task(db, store, user)
    original = vectors.authority.imports.complete
    calls = []
    def response_lost(*args, **kwargs):
        calls.append(original(*args, **kwargs))
        raise TimeoutError('commit response lost')
    monkeypatch.setattr(vectors.authority.imports, 'complete', response_lost)
    result = service.publish(scope, attempt, [_point(scope, attempt.task.document_id, 'new')])
    assert len(calls) == 1 and result.import_task.status == 'succeeded'
    history, head, task, ended, refs, lease = _state(
        db, snapshots, vectors, scope, user, attempt.task.task_id)
    assert head == result.head and head.snapshot_version == history.version == 2
    assert task['status'] == 'succeeded' and ended is not None and refs == 1
    assert not lease['live']
    assert service.documents.read_document_bytes(user, attempt.task.document_id) == b'accepted bytes'


def test_old_document_without_points_and_logical_id_reuse_fail_closed(publication):
    service, db, store, vectors, snapshots, scope, user, _ = publication
    first = _task(db, store, user)
    old = _point(scope, first.task.document_id, 'old')
    service.publish(scope, first, [old])
    second = _task(db, store, user, b'next')
    with pytest.raises(ImportDocumentPublicationError, match='collides'):
        service.publish(scope, second, [_point(scope, second.task.document_id,
                                             'new', logical_id=old.id)])
    service.imports.release_unstarted(second)
    history = snapshots.read(user, 'history')
    changed = dict(history.data)
    changed['documents'] = [*changed['documents'],
        {'user_id': user, 'document_id': str(uuid4()), 'document_name': 'empty.txt'}]
    coordinator = PostgresUserMutationCoordinator(db)
    lease = coordinator.acquire(user, 'zero-chunk-setup', lease_seconds=120)
    assert lease is not None
    old_points = vectors.read_view(scope).scroll(scope.identity.physical_collection,
                                                 with_vectors=True)
    vectors.publish_complete(scope, lease, vectors.authority.read_head(scope), old_points,
        domain_publish=lambda cursor: snapshots.compare_and_swap_in_transaction(
            cursor, user, 'history', changed, expected_version=history.version),
        snapshot_version=history.version + 1)
    coordinator.release(lease)
    second = service.imports.claim_next('retry-' + uuid4().hex)
    assert second is not None and second.task.document_id != first.task.document_id
    with pytest.raises(ImportDocumentPublicationError, match='Prior corpus'):
        service.publish(scope, second, [_point(scope, second.task.document_id, 'new')])


def test_task_expires_in_publication_callback_rolls_back_all_four_authorities(publication, monkeypatch):
    service, db, store, vectors, snapshots, scope, user, _ = publication
    attempt = _task(db, store, user)
    before = snapshots.read(user, 'history'), vectors.authority.read_head(scope)
    original = service.documents.publish_in_transaction
    def expire_after_reference(cursor, verified):
        original(cursor, verified)
        cursor.execute("update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second' where id=%s",
                       (attempt.task.task_id,))
    monkeypatch.setattr(service.documents, 'publish_in_transaction', expire_after_reference)
    with pytest.raises(VectorPublicationUnknown, match='publication'):
        service.publish(scope, attempt, [_point(scope, attempt.task.document_id, 'new')])
    history, head, task, ended, refs, lease = _state(
        db, snapshots, vectors, scope, user, attempt.task.task_id)
    assert (history, head) == before
    assert task['status'] == 'running' and ended is None and refs == 0
    assert lease['live']


def test_caller_mutation_during_source_io_cannot_change_publication(publication, monkeypatch):
    service, db, store, vectors, snapshots, scope, user, _ = publication
    attempt = _task(db, store, user)
    trusted_scope = deepcopy(scope)
    document_id = attempt.task.document_id
    point = _point(scope, document_id, 'original text')
    original = service.sources.read_source_bytes
    def mutate_caller_then_read(*args, **kwargs):
        scope.__dict__['namespace'] = 'wrong_namespace'
        attempt.task.__dict__['document_id'] = str(uuid4())
        point.payload['content'] = 'tampered text'
        point.payload['metadata']['document_id'] = str(uuid4())
        return original(*args, **kwargs)
    monkeypatch.setattr(service.sources, 'read_source_bytes', mutate_caller_then_read)
    result = service.publish(scope, attempt, [point])
    assert result.import_task.status == 'succeeded'
    assert snapshots.read(user, 'history').data['documents'][0]['document_id'] == document_id
    published = vectors.read_view(trusted_scope).scroll(
        trusted_scope.identity.physical_collection)
    assert published[0].payload['content'] == 'original text'
