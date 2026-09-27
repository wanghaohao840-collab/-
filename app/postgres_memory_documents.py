"""Per-user episodic documents; metadata JSON text is preserved byte-for-byte.

The pool belongs to the application, not this adapter. Caller transactions use
user-first lock order and must validate any Worker lease before publication.
This adapter alone does not fence stale vector writes or resident managers.
"""
import json

from psycopg.pq import TransactionStatus

from app.postgres import PostgresDatabase


def _identifier(value):
    if not isinstance(value, str) or not value:
        raise ValueError('Document ID must be a nonempty string')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate metadata key')
        result[key] = value
    return result


class PostgresMemoryDocumentStore:
    def __init__(self, database: PostgresDatabase, user_id: str):
        _identifier(user_id)
        self.database = database
        self.user_id = user_id
        self._closed = False

    def _open(self):
        if self._closed:
            raise RuntimeError('Document store is closed')

    def _lock_user(self, cursor):
        self._open()
        if (cursor.connection.autocommit
                and cursor.connection.info.transaction_status != TransactionStatus.INTRANS):
            raise ValueError('Caller-owned transaction required')
        if cursor.execute('select id from users where id=%s for update', (self.user_id,)).fetchone() is None:
            raise ValueError('Document owner does not exist')

    def _metadata(self, raw):
        if not isinstance(raw, str):
            raise ValueError('Metadata must be JSON text')
        value = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(value, dict) or value.get('user_id') != self.user_id:
            raise ValueError('Document metadata owner mismatch')
        # Reject both named NaN/Infinity and exponent overflow at any depth.
        json.dumps(value, allow_nan=False)

    def add_document(self, doc_id: str, content: str, metadata: str = '{}') -> None:
        with self.database.transaction() as cursor:
            self.add_document_in_transaction(cursor, doc_id, content, metadata)

    def add_document_in_transaction(self, cursor, doc_id: str, content: str, metadata: str) -> None:
        _identifier(doc_id)
        if not isinstance(content, str):
            raise ValueError('Document content must be text')
        self._metadata(metadata)
        self._lock_user(cursor)
        cursor.execute('''insert into memory_documents(user_id,document_id,content,metadata,created_at)
            values(%s,%s,%s,%s,to_char(clock_timestamp() at time zone 'UTC','YYYY-MM-DD HH24:MI:SS.US'))
            on conflict(user_id,document_id) do update set content=excluded.content,metadata=excluded.metadata''',
            (self.user_id, doc_id, content, metadata))

    def get_document(self, doc_id: str) -> dict | None:
        self._open()
        _identifier(doc_id)
        with self.database.transaction() as cursor:
            row = cursor.execute('select document_id as id,content,metadata from memory_documents where user_id=%s and document_id=%s',
                                 (self.user_id, doc_id)).fetchone()
        if row is not None:
            self._metadata(row['metadata'])
        return row

    def delete_document(self, doc_id: str) -> None:
        with self.database.transaction() as cursor:
            self.delete_document_in_transaction(cursor, doc_id)

    def delete_document_in_transaction(self, cursor, doc_id: str) -> None:
        _identifier(doc_id)
        self._lock_user(cursor)
        cursor.execute('delete from memory_documents where user_id=%s and document_id=%s', (self.user_id, doc_id))

    def close(self) -> None:
        self._closed = True
