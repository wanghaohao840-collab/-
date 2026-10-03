"""Private RAG History-document pairing and immutable retained-object evidence."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from uuid import UUID

from app.object_store import ObjectRef
from app.postgres_document_objects import PostgresDocumentObjectRepository, _identity
from app.postgres_snapshots import _validate
from app.postgres_vector_generations import VectorHead, VectorScope


class HistoryDocumentWitnessError(RuntimeError):
    """A RAG publication cannot be paired with the complete History documents."""


@dataclass(frozen=True)
class DocumentEvidence:
    count: int
    digest: str
    records: tuple[tuple[str, str], ...]  # document ID and complete record hash

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(document_id for document_id, _ in self.records)


@dataclass(frozen=True)
class Pairing:
    head: VectorHead
    last_generation_id: UUID
    generation_state: str
    expected_count: int
    content_digest: str
    receipt_revision: int
    receipt_index_revision: int
    receipt_snapshot_version: int
    witness_count: int | None
    witness_digest: str | None

    @property
    def has_witness(self) -> bool:
        return self.witness_count is not None


def document_evidence(user_id: str, history: dict) -> DocumentEvidence:
    data = _validate(user_id, 'history', history)
    documents = data['documents']
    records = []
    ids = set()
    for item in documents:
        if (not isinstance(item, dict) or not isinstance(item.get('document_id'), str)
                or not item['document_id']
                or ('user_id' in item and item['user_id'] != user_id)
                or item['document_id'] in ids):
            raise HistoryDocumentWitnessError('History document identity is invalid')
        ids.add(item['document_id'])
        records.append((item['document_id'], PostgresDocumentObjectRepository._record_hash(item)))
    serialized = json.dumps(documents, sort_keys=True, separators=(',', ':'),
                            ensure_ascii=False, allow_nan=False).encode('utf-8')
    return DocumentEvidence(len(documents), hashlib.sha256(serialized).hexdigest(), tuple(records))


class PostgresHistoryDocumentWitnessRepository:
    def __init__(self, database, store):
        self.database = database
        self.store = store

    @staticmethod
    def _pairing(row, scope: VectorScope) -> Pairing:
        if row is None or row['head_revision'] is None:
            raise HistoryDocumentWitnessError('RAG vector head is missing')
        if row['identity'] != scope.identity.to_dict():
            raise HistoryDocumentWitnessError('RAG index identity changed')
        generation_id = row['generation_id']
        count = row['expected_count']
        if (row['head_index_revision'] != row['registered_index_revision']
                or row['generation_state'] != 'published'
                or row['receipt_revision'] != row['head_revision']
                or row['receipt_index_revision'] != row['head_index_revision']
                or row['receipt_snapshot_version'] is None
                or row['receipt_snapshot_version'] < 1
                or row['receipt_snapshot_version'] != row['head_snapshot_version']
                or count is None or count < 0 or not row['content_digest']
                or (generation_id is None) != (count == 0)
                or (generation_id is not None and generation_id != row['last_generation_id'])):
            raise HistoryDocumentWitnessError('RAG head and publication receipt are not paired')
        if row['witness_count'] is not None and (
                row['witness_generation_id'] != row['last_generation_id']
                or row['witness_index_revision'] != row['head_index_revision']
                or row['witness_snapshot_version'] != row['head_snapshot_version']
                or row['witness_count'] < 0 or not row['witness_digest']):
            raise HistoryDocumentWitnessError('RAG History witness differs from head')
        return Pairing(
            VectorHead('empty' if generation_id is None else 'published',
                       row['head_revision'], generation_id,
                       row['head_index_revision'], row['head_snapshot_version']),
            row['last_generation_id'], row['generation_state'], count,
            row['content_digest'], row['receipt_revision'],
            row['receipt_index_revision'], row['receipt_snapshot_version'],
            row['witness_count'], row['witness_digest'])

    def read_current(self, scope: VectorScope, *, cursor=None) -> Pairing:
        if cursor is None:
            with self.database.transaction() as own:
                return self.read_current(scope, cursor=own)
        # One scope-filtered statement binds the registered identity, head,
        # concrete last generation/receipt, and optional witness.
        row = cursor.execute('''select i.identity,i.index_revision as registered_index_revision,
            h.revision as head_revision,h.generation_id,h.last_generation_id,
            h.index_revision as head_index_revision,h.snapshot_version as head_snapshot_version,
            g.state as generation_state,g.expected_count,g.content_digest,
            g.publication_revision as receipt_revision,
            g.index_revision as receipt_index_revision,
            g.publication_snapshot_version as receipt_snapshot_version,
            w.last_generation_id as witness_generation_id,
            w.index_revision as witness_index_revision,
            w.publication_snapshot_version as witness_snapshot_version,
            w.document_count as witness_count,w.documents_sha256 as witness_digest
            from vector_indexes i
            left join vector_heads h on h.tenant_id=i.tenant_id
              and h.vector_kind=i.vector_kind and h.namespace=i.namespace and h.index_key=i.index_key
            left join vector_generations g on g.generation_id=h.last_generation_id
              and g.tenant_id=i.tenant_id and g.vector_kind=i.vector_kind
              and g.namespace=i.namespace and g.index_key=i.index_key
            left join history_document_witnesses w on w.tenant_id=i.tenant_id
              and w.vector_kind=i.vector_kind and w.namespace=i.namespace
              and w.index_key=i.index_key and w.head_revision=h.revision
            where i.tenant_id=%s and i.vector_kind=%s and i.namespace=%s and i.index_key=%s''',
            scope.key).fetchone()
        return self._pairing(row, scope)

    @staticmethod
    def check_document_pairing(pairing: Pairing, evidence: DocumentEvidence,
                               history_version: int) -> None:
        if type(history_version) is not int or history_version < pairing.receipt_snapshot_version:
            raise HistoryDocumentWitnessError('History version precedes RAG publication receipt')
        if pairing.has_witness:
            if pairing.witness_count != evidence.count or pairing.witness_digest != evidence.digest:
                raise HistoryDocumentWitnessError('Prior corpus History documents differ from RAG witness')
        elif pairing.head.snapshot_version != history_version:
            raise HistoryDocumentWitnessError('History and vector head are not paired; witness is missing')

    def verify_retained_references(self, scope: VectorScope,
                                   evidence: DocumentEvidence) -> tuple[tuple, ...]:
        if not evidence.records:
            return ()
        ids = [document_id for document_id, _ in evidence.records]
        with self.database.transaction() as cursor:
            rows = cursor.execute('''select document_id,bucket,object_key,version_id,sha256,
                size_bytes,history_record_sha256 from document_objects
                where user_id=%s and document_id=any(%s)''',
                (scope.tenant_id, ids)).fetchall()
        by_id = {row['document_id']: row for row in rows}
        fixed = []
        for document_id, record_hash in evidence.records:
            row = by_id.get(document_id)
            if row is None or row['history_record_sha256'] != record_hash:
                raise HistoryDocumentWitnessError('Prior corpus retained document reference differs from History')
            ref = ObjectRef(row['object_key'], row['sha256'], row['size_bytes'], row['version_id'])
            if row['bucket'] != self.store.bucket:
                raise HistoryDocumentWitnessError('Retained document object bucket changed')
            try:
                _identity(scope.tenant_id, document_id, ref)
                self.store.read_verified(scope.tenant_id, ref)
            except Exception as exc:
                raise HistoryDocumentWitnessError('Retained document pinned bytes differ') from exc
            fixed.append((document_id, row['bucket'], ref.key, ref.version_id,
                          ref.sha256, ref.size_bytes, record_hash))
        return tuple(fixed)

    @staticmethod
    def check_retained_references(cursor, scope: VectorScope,
                                  fixed: tuple[tuple, ...]) -> None:
        if not fixed:
            return
        rows = cursor.execute('''select document_id,bucket,object_key,version_id,sha256,
            size_bytes,history_record_sha256 from document_objects
            where user_id=%s and document_id=any(%s)''',
            (scope.tenant_id, [item[0] for item in fixed])).fetchall()
        found = {row['document_id']: (row['document_id'], row['bucket'],
                 row['object_key'], row['version_id'], row['sha256'],
                 row['size_bytes'], row['history_record_sha256']) for row in rows}
        if len(found) != len(fixed) or any(found.get(item[0]) != item for item in fixed):
            raise HistoryDocumentWitnessError('Retained document references changed')

    @staticmethod
    def check_old_receipt(cursor, scope: VectorScope, old: Pairing) -> None:
        row = cursor.execute('''select state,index_revision,publication_revision,
            publication_snapshot_version,expected_count,content_digest
            from vector_generations where generation_id=%s and tenant_id=%s
            and vector_kind=%s and namespace=%s and index_key=%s''',
            (old.last_generation_id, *scope.key)).fetchone()
        if row is None or (row['state'], row['index_revision'], row['publication_revision'],
                row['publication_snapshot_version'], row['expected_count'],
                row['content_digest']) != ('retired', old.receipt_index_revision,
                old.receipt_revision, old.receipt_snapshot_version,
                old.expected_count, old.content_digest):
            raise HistoryDocumentWitnessError('Prior RAG publication receipt changed')
        witness = cursor.execute('''select last_generation_id,index_revision,
            publication_snapshot_version,document_count,documents_sha256
            from history_document_witnesses where tenant_id=%s and vector_kind=%s
            and namespace=%s and index_key=%s and head_revision=%s''',
            (*scope.key, old.head.revision)).fetchone()
        if old.has_witness:
            if witness is None or (witness['last_generation_id'], witness['index_revision'],
                    witness['publication_snapshot_version'], witness['document_count'],
                    witness['documents_sha256']) != (old.last_generation_id,
                    old.receipt_index_revision, old.receipt_snapshot_version,
                    old.witness_count, old.witness_digest):
                raise HistoryDocumentWitnessError('Prior RAG History witness changed')
        elif witness is not None:
            raise HistoryDocumentWitnessError('Prior RAG History witness appeared')

    @staticmethod
    def insert(cursor, scope: VectorScope, pairing: Pairing,
               evidence: DocumentEvidence, *, admission=None) -> None:
        if admission is None:
            from app.import_publication_evidence import require_no_gate_in_transaction
            from app.postgres_coordination import PostgresUserMutationCoordinator
            PostgresUserMutationCoordinator._lock_user(cursor, scope.tenant_id)
            require_no_gate_in_transaction(cursor, scope.tenant_id)
        else:
            from app.import_memory_publication import _require_terminal_admission
            _require_terminal_admission(admission, cursor, 'witness',
                scope, pairing, evidence)
        cursor.execute('''insert into history_document_witnesses
            (tenant_id,vector_kind,namespace,index_key,head_revision,last_generation_id,
             index_revision,publication_snapshot_version,document_count,documents_sha256)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (*scope.key, pairing.head.revision, pairing.last_generation_id,
             pairing.receipt_index_revision, pairing.receipt_snapshot_version,
             evidence.count, evidence.digest))
