"""Pinned document bytes under caller-owned PostgreSQL publication transactions.

The caller must publish History and this reference in one transaction, and
validate its Worker attempt before commit. This module does not enable runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from weakref import WeakKeyDictionary
from psycopg.pq import TransactionStatus

from app.object_store import ObjectRef, S3ObjectStore, artifact_key
from app.postgres import PostgresDatabase


class DocumentPublicationError(RuntimeError):
    """A visible document has no trustworthy object reference."""


@dataclass(frozen=True, eq=False)
class VerifiedDocumentRef:
    user_id: str
    document_id: str
    bucket: str
    ref: ObjectRef


def _identity(user_id: str, document_id: str, ref: ObjectRef) -> None:
    # artifact_key checks canonical IDs, supported suffix and the digest.
    suffix = ref.key.rsplit('/', 1)[-1][64:]
    if artifact_key(user_id, 'documents', document_id, suffix, ref.sha256) != ref.key:
        raise DocumentPublicationError('Document object key differs from identity')
    if not ref.version_id or ref.version_id == 'null' or ref.size_bytes < 0:
        raise DocumentPublicationError('Document object version or size is invalid')


class PostgresDocumentObjectRepository:
    def __init__(self, database: PostgresDatabase, store: S3ObjectStore):
        self.database = database
        self.store = store
        # Values are copied scalars, independent of the caller's mutable
        # dataclass dictionaries. Weak keys do not retain abandoned tokens.
        self._verified: WeakKeyDictionary[VerifiedDocumentRef, tuple] = WeakKeyDictionary()

    def verify_for_publication(self, user_id: str, document_id: str,
                               ref: ObjectRef) -> VerifiedDocumentRef:
        """Verify exact retained bytes before entering the short PG transaction."""
        retained = ObjectRef(ref.key, ref.sha256, ref.size_bytes, ref.version_id)
        _identity(user_id, document_id, retained)
        self.store.read_verified(user_id, retained)
        verified = VerifiedDocumentRef(user_id, document_id, self.store.bucket, retained)
        self._verified[verified] = self._issuance_values(verified)
        return verified

    @staticmethod
    def _issuance_values(token: VerifiedDocumentRef) -> tuple:
        ref = token.ref
        return (token.user_id, token.document_id, token.bucket,
                ref.key, ref.version_id, ref.sha256, ref.size_bytes)

    def publish_in_transaction(self, cursor, verified: VerifiedDocumentRef,
                               *, admission=None) -> None:
        """Insert once in the same transaction that makes History visible.

        Caller locks the user first and checks its Worker lease/fence. Replays
        with the exact same immutable reference are idempotent.
        """
        if (cursor.connection.autocommit
                and cursor.connection.info.transaction_status != TransactionStatus.INTRANS):
            raise ValueError('Caller-owned transaction required')
        if type(verified) is not VerifiedDocumentRef:
            raise DocumentPublicationError('Original verified document token required')
        issued = self._verified.get(verified)
        if issued is None or issued != self._issuance_values(verified):
            raise DocumentPublicationError('Document reference differs from verification')
        # Use the repository's independent copy for every subsequent check
        # and SQL argument, even if a caller mutates the token concurrently.
        original_verified = verified
        user_id, document_id, bucket, key, version_id, sha256, size_bytes = issued
        verified = VerifiedDocumentRef(
            user_id, document_id, bucket, ObjectRef(key, sha256, size_bytes, version_id))
        if verified.bucket != self.store.bucket:
            raise DocumentPublicationError('Document object bucket differs from configured storage')
        _identity(verified.user_id, verified.document_id, verified.ref)
        row = cursor.execute("select id from users where id=%s and status='active' for update",
                             (verified.user_id,)).fetchone()
        if row is None:
            raise FileNotFoundError(verified.user_id)
        if admission is None:
            from app.import_publication_evidence import require_no_gate_in_transaction
            require_no_gate_in_transaction(cursor, issued[0])
        else:
            from app.import_memory_publication import _require_terminal_admission
            _require_terminal_admission(admission, cursor, 'document',
                                        self, original_verified, verified)
        record = self._visible_record(cursor, verified.user_id, verified.document_id)
        if record is None:
            raise DocumentPublicationError('Document is absent from History')
        record_hash = self._record_hash(record)
        ref = verified.ref
        cursor.execute('''insert into document_objects
            (user_id,document_id,bucket,object_key,version_id,sha256,size_bytes,history_record_sha256)
            values(%s,%s,%s,%s,%s,%s,%s,%s) on conflict(user_id,document_id) do nothing''',
            (verified.user_id, verified.document_id, verified.bucket, ref.key,
             ref.version_id, ref.sha256, ref.size_bytes, record_hash))
        stored = cursor.execute('''select bucket,object_key,version_id,sha256,size_bytes,history_record_sha256
            from document_objects where user_id=%s and document_id=%s''',
            (verified.user_id, verified.document_id)).fetchone()
        if tuple(stored.values()) != (verified.bucket, ref.key, ref.version_id,
                                       ref.sha256, ref.size_bytes, record_hash):
            raise DocumentPublicationError('Document already has another pinned object')

    @staticmethod
    def _record_hash(record: dict) -> str:
        return hashlib.sha256(json.dumps(record, sort_keys=True, separators=(',', ':'),
                                         ensure_ascii=False).encode('utf-8')).hexdigest()

    @staticmethod
    def _visible_record(cursor, user_id: str, document_id: str) -> dict | None:
        row = cursor.execute('''select payload from user_snapshots
            where user_id=%s and kind='history' ''', (user_id,)).fetchone()
        if row is None:
            return None
        documents = row['payload'].get('documents')
        if not isinstance(documents, list):
            raise DocumentPublicationError('History documents are invalid')
        latest = None
        for item in documents:
            if isinstance(item, dict) and item.get('document_id') == document_id:
                latest = item
        if latest is None or latest.get('user_id', user_id) != user_id:
            return None
        fenced = cursor.execute('''select 1 from qa_deletion_fences where user_id=%s
            and target_type='document' and target_id=%s
            and (status in ('queued','running') or
                (status='failed' and attempt_count<3)) limit 1''',
            (user_id, document_id)).fetchone()
        return None if fenced else latest

    def read_document_bytes(self, user_id: str, document_id: str) -> bytes:
        # One database snapshot binds History visibility to the reference.
        with self.database.transaction() as cursor:
            cursor.execute('set transaction isolation level repeatable read read only')
            record = self._visible_record(cursor, user_id, document_id)
            if record is None:
                raise FileNotFoundError(document_id)
            row = cursor.execute('''select bucket,object_key,version_id,sha256,size_bytes,history_record_sha256
                from document_objects where user_id=%s and document_id=%s''',
                (user_id, document_id)).fetchone()
        if row is None:
            raise DocumentPublicationError('Document has no published object reference')
        if row['bucket'] != self.store.bucket:
            raise DocumentPublicationError('Document object bucket differs from configured storage')
        if row['history_record_sha256'] != self._record_hash(record):
            raise DocumentPublicationError('Document History changed after object publication')
        ref = ObjectRef(row['object_key'], row['sha256'], row['size_bytes'], row['version_id'])
        try:
            _identity(user_id, document_id, ref)
        except ValueError as exc:
            raise DocumentPublicationError('Document object identity is invalid') from exc
        return self.store.read_verified(user_id, ref)
