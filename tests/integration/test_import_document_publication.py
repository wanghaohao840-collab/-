"""Opt-in document import publication across disposable PG, S3 and Qdrant."""
from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
import hashlib
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from psycopg.types.json import Jsonb

from app.history import EMPTY_HISTORY
from app.import_document_publication import (
    ImportDocumentPublicationError, ImportDocumentPublicationService,
)
from app.import_models import ImportTaskCreate
from app.import_repository import PostgresImportTaskRepository
from app.object_store import artifact_key
from app.postgres_coordination import PostgresUserMutationCoordinator
from app.postgres_document_objects import DocumentPublicationError
from app.postgres_history_document_witnesses import document_evidence
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
    with db.transaction() as cursor:
        legacy_revision = cursor.execute(
            'select version_num from alembic_version').fetchone()['version_num']
    if legacy_revision == '20260929_12':
        # Build the rev-12 paired empty head using that revision's real SQL.
        # The current writer requires the rev-14 gate table.
        generation = uuid4()
        empty_digest = hashlib.sha256(b'[]').hexdigest()
        with db.transaction() as cursor:
            cursor.execute('''insert into vector_indexes
                (tenant_id,vector_kind,namespace,index_key,identity)
                values (%s,%s,%s,%s,%s)''',
                (*scope.key, Jsonb(scope.identity.to_dict())))
            cursor.execute('''insert into vector_generations
                (generation_id,tenant_id,vector_kind,namespace,index_key,
                 base_revision,index_revision,owner,user_lease_token,
                 user_lease_version,state)
                values (%s,%s,%s,%s,%s,null,1,%s,%s,%s,'staging')''',
                (generation, *scope.key, lease.owner, lease.lease_token,
                 lease.lease_version))
            cursor.execute('''update vector_generations
                set state='sealed',expected_count=0,content_digest=%s,
                    sealed_at=clock_timestamp() where generation_id=%s''',
                (empty_digest, generation))
            cursor.execute('''insert into user_snapshots
                (user_id,kind,version,payload,updated_at)
                values(%s,'history',1,%s,clock_timestamp())''',
                (user, Jsonb(dict(EMPTY_HISTORY))))
            cursor.execute('''update vector_generations
                set state='published',publication_revision=1,
                    publication_snapshot_version=1,published_at=clock_timestamp()
                where generation_id=%s''', (generation,))
            cursor.execute('''insert into vector_heads
                (tenant_id,vector_kind,namespace,index_key,revision,generation_id,
                 last_generation_id,index_revision,snapshot_version)
                values (%s,%s,%s,%s,1,null,%s,1,1)''',
                (*scope.key, generation))
    else:
        def bootstrap_history(cursor):
            snapshots.compare_and_swap_in_transaction(
                cursor, user, 'history', dict(EMPTY_HISTORY), expected_version=0)
        vectors.publish_complete(scope, lease, vectors.authority.read_head(scope), [],
            domain_publish=bootstrap_history,
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


def _witnesses(db, scope):
    with db.transaction() as cursor:
        return cursor.execute('''select head_revision,last_generation_id,index_revision,
            publication_snapshot_version,document_count,documents_sha256
            from history_document_witnesses where tenant_id=%s and vector_kind=%s
            and namespace=%s and index_key=%s order by head_revision''', scope.key).fetchall()


def _publish_strict_legacy_document(publication, *, with_ref=True, omit_user_id=False):
    service, db, store, vectors, snapshots, scope, user, _ = publication
    document_id = str(uuid4())
    content = b'legacy retained bytes'
    digest = hashlib.sha256(content).hexdigest()
    ref = store.put_immutable(user, artifact_key(user, 'documents', document_id,
                                                '.txt', digest), content).ref
    verified = service.documents.verify_for_publication(user, document_id, ref)
    record = {'user_id': user, 'document_id': document_id, 'document_name': 'legacy.txt',
              'file_suffix': '.txt', 'document_path': f'object://{store.bucket}/{ref.key}',
              'loaded_at': '2026-09-30T00:00:00+00:00'}
    if omit_user_id:
        del record['user_id']
    history = snapshots.read(user, 'history')
    changed = deepcopy(history.data)
    changed['documents'].append(record)
    lease = PostgresUserMutationCoordinator(db).acquire(user, 'legacy-publisher', lease_seconds=120)
    assert lease is not None

    def domain(cursor):
        snapshots.compare_and_swap_in_transaction(cursor, user, 'history', changed,
                                                  expected_version=history.version)
        if with_ref:
            service.documents.publish_in_transaction(cursor, verified)

    try:
        vectors.publish_complete(scope, lease, vectors.authority.read_head(scope),
                                 [_point(scope, document_id, 'legacy')],
                                 domain_publish=domain, snapshot_version=history.version + 1)
    finally:
        PostgresUserMutationCoordinator(db).release(lease)
    assert _witnesses(db, scope) == []
    return document_id


def test_strict_paired_nonempty_head_bootstraps_both_witnesses(publication):
    service, db, store, vectors, snapshots, scope, user, _ = publication
    old_id = _publish_strict_legacy_document(publication)
    old_head = vectors.authority.read_head(scope)
    second = _task(db, store, user, b'next')
    service.publish(scope, second, [_point(scope, second.task.document_id, 'new')])
    rows = _witnesses(db, scope)
    assert len(rows) == 2
    assert rows[0]['head_revision'] == old_head.revision
    assert rows[0]['last_generation_id'] == old_head.generation_id
    assert rows[0]['document_count'] == 1
    assert rows[1]['document_count'] == 2
    assert rows[0]['documents_sha256'] == document_evidence(
        user, {'documents': [snapshots.read(user, 'history').data['documents'][0]],
               'questions': [], 'notes': [], 'sessions': []}).digest
    assert service.documents.read_document_bytes(user, old_id) == b'legacy retained bytes'


def test_legacy_document_without_owner_bootstraps_and_survives_later_notes(publication):
    service, db, store, vectors, snapshots, scope, user, _ = publication
    old_id = _publish_strict_legacy_document(publication, omit_user_id=True)
    legacy = snapshots.read(user, 'history').data['documents'][0]
    assert 'user_id' not in legacy
    with db.transaction() as cursor:
        pinned_hash = cursor.execute('''select history_record_sha256 from document_objects
            where user_id=%s and document_id=%s''', (user, old_id)).fetchone()['history_record_sha256']
    assert pinned_hash == service.documents._record_hash(legacy)

    second = _task(db, store, user, b'second bytes')
    service.publish(scope, second, [_point(scope, second.task.document_id, 'second')])
    rows = _witnesses(db, scope)
    assert len(rows) == 2
    history = snapshots.read(user, 'history')
    assert history.data['documents'][0] == legacy
    before_notes_digest = document_evidence(user, history.data).digest

    changed = deepcopy(history.data)
    changed['notes'].append({'content': 'independent entry'})
    snapshots.compare_and_swap(user, 'history', changed, expected_version=history.version)
    assert document_evidence(user, changed).digest == before_notes_digest
    third = _task(db, store, user, b'third bytes')
    result = service.publish(scope, third, [_point(scope, third.task.document_id, 'third')])

    current = snapshots.read(user, 'history')
    assert result.import_task.status == 'succeeded'
    assert current.data['documents'][0] == legacy
    assert current.data['notes'] == [{'content': 'independent entry'}]
    assert len(_witnesses(db, scope)) == 3
    assert service.documents.read_document_bytes(user, old_id) == b'legacy retained bytes'


def test_strict_paired_legacy_head_with_missing_ref_rejects_bootstrap(publication):
    service, db, store, vectors, snapshots, scope, user, _ = publication
    _publish_strict_legacy_document(publication, with_ref=False)
    second = _task(db, store, user, b'next')
    before = snapshots.read(user, 'history'), vectors.authority.read_head(scope)
    with pytest.raises(ImportDocumentPublicationError, match='retained document reference'):
        service.publish(scope, second, [_point(scope, second.task.document_id, 'new')])
    assert (snapshots.read(user, 'history'), vectors.authority.read_head(scope)) == before
    assert _witnesses(db, scope) == []


def test_callback_failure_after_inline_old_witness_rolls_everything_back(publication, monkeypatch):
    service, db, store, vectors, snapshots, scope, user, _ = publication
    old_id = _publish_strict_legacy_document(publication)
    second = _task(db, store, user, b'next')
    before = snapshots.read(user, 'history'), vectors.authority.read_head(scope)

    def reject(*args, **kwargs):
        raise DocumentPublicationError('forced new reference failure')

    monkeypatch.setattr(service.documents, 'publish_in_transaction', reject)
    with pytest.raises(DocumentPublicationError, match='forced'):
        service.publish(scope, second, [_point(scope, second.task.document_id, 'new')])
    assert (snapshots.read(user, 'history'), vectors.authority.read_head(scope)) == before
    assert _witnesses(db, scope) == []
    with db.transaction() as cursor:
        assert cursor.execute('select count(*) as n from document_objects where user_id=%s',
                              (user,)).fetchone()['n'] == 1
    assert service.documents.read_document_bytes(user, old_id) == b'legacy retained bytes'


@pytest.mark.parametrize('mutation', (
    'metadata', 'remove', 'add', 'duplicate', 'cross_tenant', 'null_user', 'missing_user'))
def test_complete_history_document_record_or_set_change_rejects(publication, mutation):
    service, db, store, vectors, snapshots, scope, user, other = publication
    first = _task(db, store, user)
    service.publish(scope, first, [_point(scope, first.task.document_id, 'old')])
    previous = snapshots.read(user, 'history')
    changed = deepcopy(previous.data)
    if mutation == 'metadata':
        changed['documents'][0]['document_name'] = 'changed.txt'
    elif mutation == 'remove':
        changed['documents'].clear()
    elif mutation == 'add':
        changed['documents'].append({'user_id': user, 'document_id': str(uuid4())})
    elif mutation == 'duplicate':
        changed['documents'].append(deepcopy(changed['documents'][0]))
    elif mutation == 'cross_tenant':
        changed['documents'][0]['user_id'] = other
    elif mutation == 'null_user':
        changed['documents'][0]['user_id'] = None
    else:
        changed['documents'][0].pop('user_id')
    snapshots.compare_and_swap(user, 'history', changed, expected_version=previous.version)
    second = _task(db, store, user, b'next')
    with pytest.raises(ImportDocumentPublicationError, match='Prior corpus|identity'):
        service.publish(scope, second, [_point(scope, second.task.document_id, 'new')])
    assert vectors.authority.read_head(scope).revision == 2
    assert len(_witnesses(db, scope)) == 2


def test_reordered_document_records_reject_even_with_identical_id_set(publication):
    service, db, store, vectors, snapshots, scope, user, _ = publication
    for marker in ('first', 'second'):
        attempt = _task(db, store, user, marker.encode())
        service.publish(scope, attempt, [_point(scope, attempt.task.document_id, marker)])
    previous = snapshots.read(user, 'history')
    changed = deepcopy(previous.data)
    changed['documents'].reverse()
    snapshots.compare_and_swap(user, 'history', changed, expected_version=previous.version)
    third = _task(db, store, user, b'third')
    with pytest.raises(ImportDocumentPublicationError, match='Prior corpus'):
        service.publish(scope, third, [_point(scope, third.task.document_id, 'third')])


def test_staging_non_document_write_rolls_back_head_history_refs_witness(publication, monkeypatch):
    service, db, store, vectors, snapshots, scope, user, _ = publication
    first = _task(db, store, user)
    service.publish(scope, first, [_point(scope, first.task.document_id, 'old')])
    second = _task(db, store, user, b'next')
    old_head = vectors.authority.read_head(scope)
    old_witnesses = _witnesses(db, scope)
    original = vectors.authority.complete_import

    def notes_then_complete(*args, **kwargs):
        previous = snapshots.read(user, 'history')
        changed = deepcopy(previous.data)
        changed['notes'].append({'content': 'arrived during staging'})
        snapshots.compare_and_swap(user, 'history', changed, expected_version=previous.version)
        return original(*args, **kwargs)

    monkeypatch.setattr(vectors.authority, 'complete_import', notes_then_complete)
    with pytest.raises(SnapshotConflict, match='History version changed'):
        service.publish(scope, second, [_point(scope, second.task.document_id, 'new')])
    history, head, task, ended, refs, lease = _state(
        db, snapshots, vectors, scope, user, second.task.task_id)
    assert head == old_head and history.version == 3
    assert history.data['notes'] == [{'content': 'arrived during staging'}]
    assert len(history.data['documents']) == 1
    assert task['status'] == 'running' and ended is None and refs == 1 and lease['live']
    assert _witnesses(db, scope) == old_witnesses


def test_history_version_behind_witness_receipt_is_rejected(publication):
    service, db, store, vectors, snapshots, scope, user, _ = publication
    first = _task(db, store, user)
    service.publish(scope, first, [_point(scope, first.task.document_id, 'old')])
    pairing = service.witnesses.read_current(scope)
    evidence = document_evidence(user, snapshots.read(user, 'history').data)
    with pytest.raises(Exception, match='precedes'):
        service.witnesses.check_document_pairing(pairing, evidence,
                                                 pairing.receipt_snapshot_version - 1)


def test_retained_pinned_document_version_missing_rejects(publication):
    service, db, store, vectors, snapshots, scope, user, _ = publication
    first = _task(db, store, user)
    service.publish(scope, first, [_point(scope, first.task.document_id, 'old')])
    with db.transaction() as cursor:
        ref = cursor.execute('''select object_key,version_id from document_objects
            where user_id=%s and document_id=%s''',
            (user, first.task.document_id)).fetchone()
    store.client.delete_object(Bucket=store.bucket, Key=ref['object_key'],
                               VersionId=ref['version_id'])
    second = _task(db, store, user, b'next')
    with pytest.raises(Exception, match='Retained document pinned bytes'):
        service.publish(scope, second, [_point(scope, second.task.document_id, 'new')])
    assert vectors.authority.read_head(scope).revision == 2


@pytest.mark.parametrize('shared_database', ['20260929_12'], indirect=True)
def test_013_upgrade_leaves_existing_strict_paired_head_without_witness(publication):
    service, db, store, vectors, snapshots, scope, user, _ = publication
    old = vectors.authority.read_head(scope)
    assert old.state == 'empty' and old.snapshot_version == 1
    with db.transaction() as cursor:
        old_generation_id = cursor.execute('''select last_generation_id from vector_heads
            where tenant_id=%s and vector_kind=%s and namespace=%s and index_key=%s''',
            scope.key).fetchone()['last_generation_id']
    command.upgrade(Config('alembic.ini'), 'head')
    assert _witnesses(db, scope) == []
    assert vectors.authority.read_head(scope) == old
    attempt = _task(db, store, user)
    service.publish(scope, attempt, [_point(scope, attempt.task.document_id, 'new')])
    rows = _witnesses(db, scope)
    assert len(rows) == 2 and rows[0]['document_count'] == 0
    assert rows[0]['last_generation_id'] == old_generation_id
    with db.transaction() as cursor:
        empty = cursor.execute('''select generation_id,last_generation_id
            from vector_heads where tenant_id=%s and vector_kind=%s
            and namespace=%s and index_key=%s''', scope.key).fetchone()
    assert empty['last_generation_id'] == rows[1]['last_generation_id']


def test_witness_sql_rejects_mismatched_receipts_and_mutation(publication):
    service, db, store, vectors, snapshots, scope, user, other = publication
    attempt = _task(db, store, user)
    service.publish(scope, attempt, [_point(scope, attempt.task.document_id, 'new')])
    row = _witnesses(db, scope)[-1]
    columns = '''(tenant_id,vector_kind,namespace,index_key,head_revision,
        last_generation_id,index_revision,publication_snapshot_version,
        document_count,documents_sha256)'''
    insert = 'insert into history_document_witnesses ' + columns + ' values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'
    values = [*scope.key, row['head_revision'], row['last_generation_id'],
              row['index_revision'], row['publication_snapshot_version'],
              row['document_count'], row['documents_sha256']]
    for index, bad in ((4, row['head_revision'] + 20),
                       (6, row['index_revision'] + 1),
                       (7, row['publication_snapshot_version'] + 1)):
        candidate = values.copy()
        candidate[index] = bad
        with pytest.raises(Exception, match='matching publication receipt'):
            with db.transaction() as cursor:
                cursor.execute(insert, candidate)
    candidate = values.copy()
    candidate[0] = other
    with pytest.raises(Exception, match='matching publication receipt'):
        with db.transaction() as cursor:
            cursor.execute(insert, candidate)
    with pytest.raises(Exception, match='immutable'):
        with db.transaction() as cursor:
            cursor.execute('''update history_document_witnesses set document_count=9
                where tenant_id=%s and vector_kind=%s and namespace=%s
                and index_key=%s and head_revision=%s''', (*scope.key, row['head_revision']))
    with pytest.raises(Exception, match='immutable'):
        with db.transaction() as cursor:
            cursor.execute('''delete from history_document_witnesses where tenant_id=%s
                and vector_kind=%s and namespace=%s and index_key=%s
                and head_revision=%s''', (*scope.key, row['head_revision']))
    with pytest.raises(Exception, match='immutable'):
        with db.transaction() as cursor:
            cursor.execute(insert + ''' on conflict(tenant_id,vector_kind,namespace,index_key,
                head_revision) do update set document_count=excluded.document_count''',
                [*scope.key, row['head_revision'], row['last_generation_id'],
                 row['index_revision'], row['publication_snapshot_version'],
                 row['document_count'], row['documents_sha256']])


def test_witness_sql_rejects_null_receipt_and_unpublished_generation(publication):
    service, db, store, vectors, snapshots, scope, user, _ = publication
    null_scope = replace(scope, namespace='null_receipt_' + uuid4().hex)
    lease = PostgresUserMutationCoordinator(db).acquire(user, 'null-receipt', lease_seconds=120)
    assert lease is not None
    try:
        vectors.publish_complete(null_scope, lease, vectors.authority.read_head(null_scope),
                                 [], snapshot_version=None)
    finally:
        PostgresUserMutationCoordinator(db).release(lease)
    with db.transaction() as cursor:
        null_head = cursor.execute('''select revision,last_generation_id,index_revision
            from vector_heads where tenant_id=%s and vector_kind=%s
            and namespace=%s and index_key=%s''', null_scope.key).fetchone()
    insert = '''insert into history_document_witnesses
        (tenant_id,vector_kind,namespace,index_key,head_revision,last_generation_id,
         index_revision,publication_snapshot_version,document_count,documents_sha256)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'''
    empty_digest = document_evidence(user, EMPTY_HISTORY).digest
    with pytest.raises(Exception, match='matching publication receipt'):
        with db.transaction() as cursor:
            cursor.execute(insert, (*null_scope.key, null_head['revision'],
                null_head['last_generation_id'], null_head['index_revision'],
                1, 0, empty_digest))
    attempt = _task(db, store, user)
    candidate_id = vectors.authority.stage(scope, attempt,
                                            expected_revision=vectors.authority.read_head(scope).revision)
    with pytest.raises(Exception, match='matching publication receipt'):
        with db.transaction() as cursor:
            cursor.execute(insert, (*scope.key, 2, candidate_id, 1, 2, 0, empty_digest))


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


@pytest.mark.parametrize('field', ('questions', 'notes', 'sessions'))
def test_non_document_history_change_between_imports_preserves_pairing(publication, field):
    service, db, store, vectors, snapshots, scope, user, _ = publication
    first = _task(db, store, user)
    service.publish(scope, first, [_point(scope, first.task.document_id, 'first')])
    history = snapshots.read(user, 'history')
    changed = deepcopy(history.data)
    changed[field].append({'content': 'independent entry'})
    snapshots.compare_and_swap(user, 'history', changed, expected_version=history.version)

    second = _task(db, store, user, b'second bytes')
    result = service.publish(scope, second, [_point(scope, second.task.document_id, 'second')])
    assert result.import_task.status == 'succeeded'
    current = snapshots.read(user, 'history')
    assert current.version == history.version + 2
    assert current.data[field] == [{'content': 'independent entry'}]
    assert len(current.data['documents']) == 2
    witness_rows = _witnesses(db, scope)
    assert len(witness_rows) == 3
    assert witness_rows[-1]['publication_snapshot_version'] == current.version
    assert witness_rows[-1]['documents_sha256'] == document_evidence(user, current.data).digest


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
    assert len(_witnesses(db, scope)) == 2
    next_attempt = _task(db, store, user, b'after lost response')
    service.publish(scope, next_attempt,
                    [_point(scope, next_attempt.task.document_id, 'after')])
    receipt = vectors.authority.publication_receipt(scope, result.generation_id)
    assert receipt['state'] == 'retired'
    assert vectors._published(scope, result.generation_id, 1, 1,
                              receipt['expected_count'], receipt['content_digest'],
                              attempt, 2).generation_id == result.generation_id
    assert len(_witnesses(db, scope)) == 3


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
