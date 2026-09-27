import json
from uuid import uuid4

import pytest

from app.postgres_memory_documents import PostgresMemoryDocumentStore
from app.postgres_snapshots import PostgresSnapshotRepository
from tests.integration.test_postgres_auth_sessions import shared_database


@pytest.fixture
def documents(shared_database):
    open_pool, _ = shared_database
    first, second = open_pool(), open_pool()
    users = [str(uuid4()), str(uuid4())]
    with first.transaction() as cursor:
        for user in users:
            cursor.execute('insert into users values(%s,%s,%s,%s,%s,%s,%s)',
                           (user, user, user, 'unused', 'active', 'now', 'now'))
    return first, second, users


def test_document_tenant_scope_upsert_and_pool_lifetime(documents):
    first, second, (user, other) = documents
    store = PostgresMemoryDocumentStore(first, user)
    peer = PostgresMemoryDocumentStore(second, user)
    other_store = PostgresMemoryDocumentStore(second, other)
    raw = json.dumps({'user_id': user, 'nested': {'pages': [3, 1]}, 'text': '中文'}, ensure_ascii=False, indent=2)
    store.add_document('same', 'content', raw)
    other_store.add_document('same', 'private', json.dumps({'user_id': other}))
    with first.transaction() as cursor:
        created = cursor.execute('select created_at from memory_documents where user_id=%s', (user,)).fetchone()['created_at']
    store.add_document('same', 'updated', raw)
    assert peer.get_document('same') == {'id': 'same', 'content': 'updated', 'metadata': raw}
    with first.transaction() as cursor:
        assert cursor.execute('select created_at from memory_documents where user_id=%s', (user,)).fetchone()['created_at'] == created
    store.close()
    assert first.ping()
    first.close()
    assert peer.get_document('same')['content'] == 'updated'
    peer.delete_document('same')
    assert peer.get_document('same') is None
    assert other_store.get_document('same')['content'] == 'private'


def test_invalid_metadata_cannot_replace_prior_document(documents):
    first, _second, (user, other) = documents
    store = PostgresMemoryDocumentStore(first, user)
    original = json.dumps({'user_id': user})
    store.add_document('d', 'saved', original)
    for metadata in (json.dumps({'user_id': other}), '{}', '[]',
                     '{"user_id":"' + user + '","value":1e999}',
                     '{"user_id":"' + user + '","value":NaN}'):
        with pytest.raises(ValueError):
            store.add_document('d', 'invalid', metadata)
    assert store.get_document('d') == {'id': 'd', 'content': 'saved', 'metadata': original}


def test_snapshot_and_episode_share_caller_transaction(documents):
    first, second, (user, _other) = documents
    store = PostgresMemoryDocumentStore(first, user)
    snapshots = PostgresSnapshotRepository(first)
    with pytest.raises(RuntimeError, match='abort'):
        with first.transaction() as cursor:
            store.add_document_in_transaction(cursor, 'd', 'content', json.dumps({'user_id': user}))
            snapshots.compare_and_swap_in_transaction(cursor, user, 'memory', {'user_id': user, 'memories': []}, expected_version=0)
            raise RuntimeError('abort')
    assert PostgresMemoryDocumentStore(second, user).get_document('d') is None
    assert PostgresSnapshotRepository(second).read(user, 'memory') is None


def test_caller_delete_rollback_and_autocommit_rejected(documents):
    first, second, (user, _other) = documents
    store = PostgresMemoryDocumentStore(first, user)
    store.add_document('d', 'content', json.dumps({'user_id': user}))
    with pytest.raises(RuntimeError, match='abort'):
        with first.transaction() as cursor:
            store.delete_document_in_transaction(cursor, 'd')
            raise RuntimeError('abort')
    assert PostgresMemoryDocumentStore(second, user).get_document('d')['content'] == 'content'
    with first.connection() as connection:
        connection.autocommit = True
        try:
            with connection.cursor() as cursor:
                with pytest.raises(ValueError, match='transaction'):
                    store.delete_document_in_transaction(cursor, 'd')
        finally:
            connection.autocommit = False


def test_explicit_autocommit_transaction_owns_publication(documents):
    first, second, (user, _other) = documents
    store = PostgresMemoryDocumentStore(first, user)
    metadata = json.dumps({'user_id': user})
    with first.connection() as connection:
        connection.autocommit = True
        try:
            with connection.cursor() as cursor:
                with pytest.raises(ValueError, match='transaction'):
                    store.add_document_in_transaction(cursor, 'd', 'invalid', metadata)
                with pytest.raises(RuntimeError, match='abort'):
                    with connection.transaction():
                        store.add_document_in_transaction(cursor, 'd', 'rolled back', metadata)
                        raise RuntimeError('abort')
                assert PostgresMemoryDocumentStore(second, user).get_document('d') is None
                with connection.transaction():
                    store.add_document_in_transaction(cursor, 'd', 'committed', metadata)
        finally:
            connection.autocommit = False
    assert PostgresMemoryDocumentStore(second, user).get_document('d')['content'] == 'committed'
