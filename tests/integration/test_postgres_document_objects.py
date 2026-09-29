"""Disposable PostgreSQL and versioned S3 document source contract."""
import hashlib
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError

from app.object_store import ObjectRef, ObjectIntegrityError, S3ObjectStore, artifact_key
from app.postgres_document_objects import (DocumentPublicationError,
    PostgresDocumentObjectRepository, VerifiedDocumentRef)
from app.postgres_snapshots import PostgresSnapshotRepository
from tests.integration.test_postgres_auth_sessions import shared_database
from tests.integration.test_s3_object_store import store


@pytest.fixture
def documents(shared_database, store):
    open_pool, _ = shared_database
    db = open_pool()
    users = [str(uuid4()), str(uuid4())]
    with db.transaction() as cursor:
        for user in users:
            cursor.execute('insert into users(id,username,username_key,password_hash,created_at,updated_at) '
                           'values(%s,%s,%s,%s,%s,%s)',
                           (user, user, user, 'unused', '2026-09-29', '2026-09-29'))
    yield PostgresDocumentObjectRepository(db, store), PostgresSnapshotRepository(db), users, db, store


def _history(user, doc):
    return {'documents': [{'user_id': user, 'document_id': doc}],
            'questions': [], 'notes': [], 'sessions': []}


def _stage(repo, store, user, doc, data=b'original bytes'):
    digest = hashlib.sha256(data).hexdigest()
    key = artifact_key(user, 'documents', doc, '.pdf', digest)
    return repo.verify_for_publication(user, doc, store.put_immutable(user, key, data).ref)


def test_history_and_reference_commit_or_roll_back_together(documents):
    repo, snapshots, (user, _), db, store = documents
    doc = str(uuid4())
    verified = _stage(repo, store, user, doc)
    with pytest.raises(RuntimeError, match='abort'):
        with db.transaction() as cursor:
            snapshots.compare_and_swap_in_transaction(cursor, user, 'history', _history(user, doc), expected_version=0)
            repo.publish_in_transaction(cursor, verified)
            raise RuntimeError('abort')
    assert snapshots.read(user, 'history') is None
    with db.transaction() as cursor:
        assert cursor.execute('select count(*) as n from document_objects').fetchone()['n'] == 0
    with db.transaction() as cursor:
        snapshots.compare_and_swap_in_transaction(cursor, user, 'history', _history(user, doc), expected_version=0)
        repo.publish_in_transaction(cursor, verified)
    assert repo.read_document_bytes(user, doc) == b'original bytes'
    with db.transaction() as cursor:
        repo.publish_in_transaction(cursor, verified)
    forged = VerifiedDocumentRef(user, doc, store.bucket, verified.ref)
    with pytest.raises(DocumentPublicationError, match='not verified'):
        with db.transaction() as cursor:
            repo.publish_in_transaction(cursor, forged)
    changed = _stage(repo, store, user, doc, b'replacement bytes')
    with pytest.raises(DocumentPublicationError, match='another pinned'):
        with db.transaction() as cursor:
            repo.publish_in_transaction(cursor, changed)
    assert repo.read_document_bytes(user, doc) == b'original bytes'


def test_missing_reference_and_history_fail_closed(documents):
    repo, snapshots, (user, _), db, store = documents
    doc = str(uuid4())
    verified = _stage(repo, store, user, doc)
    with pytest.raises(DocumentPublicationError, match='History'):
        with db.transaction() as cursor:
            repo.publish_in_transaction(cursor, verified)
    snapshots.compare_and_swap(user, 'history', _history(user, doc), expected_version=0)
    with pytest.raises(DocumentPublicationError, match='reference'):
        repo.read_document_bytes(user, doc)


def test_changed_latest_history_record_blocks_stale_reference(documents):
    repo, snapshots, (user, _), db, store = documents
    doc = str(uuid4())
    verified = _stage(repo, store, user, doc)
    with db.transaction() as cursor:
        snapshots.compare_and_swap_in_transaction(cursor, user, 'history', _history(user, doc), expected_version=0)
        repo.publish_in_transaction(cursor, verified)
    replacement = _history(user, doc)
    replacement['documents'].append({'user_id': user, 'document_id': doc, 'name': 'replacement'})
    snapshots.compare_and_swap(user, 'history', replacement, expected_version=1)
    with pytest.raises(DocumentPublicationError, match='History changed'):
        repo.read_document_bytes(user, doc)


def test_pinned_version_cross_tenant_and_integrity(documents):
    repo, snapshots, (user, other), db, store = documents
    doc = str(uuid4())
    verified = _stage(repo, store, user, doc)
    with db.transaction() as cursor:
        snapshots.compare_and_swap_in_transaction(cursor, user, 'history', _history(user, doc), expected_version=0)
        snapshots.compare_and_swap_in_transaction(cursor, other, 'history', _history(other, doc), expected_version=0)
        repo.publish_in_transaction(cursor, verified)
    with pytest.raises(DocumentPublicationError, match='reference'):
        repo.read_document_bytes(other, doc)
    store.client.put_object(Bucket=store.bucket, Key=verified.ref.key, Body=b'overwritten')
    store.client.delete_object(Bucket=store.bucket, Key=verified.ref.key)
    assert repo.read_document_bytes(user, doc) == b'original bytes'
    wrong = S3ObjectStore(store.client, 'other-bucket')
    with pytest.raises(DocumentPublicationError, match='bucket'):
        PostgresDocumentObjectRepository(db, wrong).read_document_bytes(user, doc)
    with pytest.raises(ClientError):
        repo.verify_for_publication(user, doc, ObjectRef(
            verified.ref.key, verified.ref.sha256, verified.ref.size_bytes,
            'wrong-version'))
    with pytest.raises(ObjectIntegrityError):
        repo.verify_for_publication(user, doc, ObjectRef(
            verified.ref.key, verified.ref.sha256, verified.ref.size_bytes + 1,
            verified.ref.version_id))
    with pytest.raises(DocumentPublicationError, match='identity'):
        repo.verify_for_publication(user, doc, ObjectRef(
            verified.ref.key, '0' * 64, verified.ref.size_bytes,
            verified.ref.version_id))
    with pytest.raises(DocumentPublicationError, match='identity'):
        repo.verify_for_publication(other, doc, verified.ref)
