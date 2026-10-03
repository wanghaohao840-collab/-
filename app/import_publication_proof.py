"""Read-only, detached proof of one durable import publication."""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.import_repository import _task_from_row
from app.import_publication_evidence import (
    AttemptKey, PostgresImportPublicationEvidenceRepository,
    PublicationEvidenceError, _canonical, _read_slot, _receipt_from_sql,
)
from app.postgres_document_objects import PostgresDocumentObjectRepository
from app.postgres_history_document_witnesses import (
    HistoryDocumentWitnessError, document_evidence,
)
from app.postgres_snapshots import _snapshot
from app.published_episode_reads import EpisodeReadError
from app.postgres_vector_generations import VectorHead
from app.vector_generation_service import VectorPublication
from app.import_memory_publication import ImportMemoryPublicationError, _all_memory


@dataclass(frozen=True)
class DetachedPairPublication:
    rag: VectorPublication
    episode: VectorPublication
    import_task: object


@dataclass(frozen=True)
class PublicationProofEnvelope:
    attempt_key: AttemptKey
    intent_hash: str
    final_expected_hash: str
    phase: str
    phase_version: int
    rag_receipt: tuple
    episode_receipt: tuple


@dataclass(frozen=True)
class DetachedImportMemoryPublication:
    pair: DetachedPairPublication
    event_id: str
    history_version: int
    memory_version: int
    document_ref: tuple
    witness: tuple
    proof: PublicationProofEnvelope


class PublicationProofUnknown(RuntimeError):
    def __init__(self, attempt_key: AttemptKey, phase: str):
        self.attempt_key = attempt_key
        self.phase = phase
        super().__init__(f'Publication proof read is unknown at {phase}')


class _InvalidProof(ValueError):
    pass


def _require(condition):
    if not condition:
        raise _InvalidProof('Saved publication does not match current durable rows')


def _read_snapshot(cursor, user_id, kind):
    row = cursor.execute(
        'select * from user_snapshots where user_id=%s and kind=%s',
        (user_id, kind),
    ).fetchone()
    try:
        return _snapshot(row)
    except (ValueError, TypeError, KeyError, IndexError) as error:
        raise _InvalidProof('Retained snapshot is invalid') from error


class ImportPublicationProofService:
    def __init__(self, database):
        self.database = database
        self.evidence = PostgresImportPublicationEvidenceRepository(database)

    @staticmethod
    def _pair(cursor, evidence, terminal):
        intent = evidence.intent
        attempt = intent['attempt']
        task_row = cursor.execute('''select * from import_tasks where id=%s and user_id=%s''',
            (attempt['task_id'], attempt['user_id'])).fetchone()
        _require(task_row is not None and task_row['status'] == 'succeeded')
        task = _task_from_row(task_row)
        _require(all(getattr(task, name) == value for name, value in intent['task'].items()))
        _require((task_row['claimed_by'], str(task_row['lease_token']),
                  task_row['lease_version'], str(task_row['user_lease_token']),
                  task_row['user_lease_version']) ==
                 (attempt['worker_id'], attempt['task_lease_token'],
                  attempt['task_lease_version'], attempt['user_lease_token'],
                  attempt['user_lease_version']))
        audit = cursor.execute('''select * from import_task_attempts where task_id=%s
            and user_id=%s and lease_version=%s''',
            (attempt['task_id'], attempt['user_id'], attempt['task_lease_version'])).fetchone()
        _require(audit is not None and audit['ended_at'] is not None and
                 audit['end_reason'] == 'succeeded' and
                 (audit['worker_id'], str(audit['lease_token']),
                  str(audit['user_lease_token']), audit['user_lease_version']) ==
                 (attempt['worker_id'], attempt['task_lease_token'],
                  attempt['user_lease_token'], attempt['user_lease_version']))
        source = cursor.execute('''select bucket,object_key,version_id,sha256,size_bytes
            from import_objects where task_id=%s and user_id=%s''',
            (task.task_id, task.user_id)).fetchone()
        _require(source is not None and
                 {name: source[column] for name, column in (
                     ('bucket', 'bucket'), ('key', 'object_key'),
                     ('version_id', 'version_id'), ('sha256', 'sha256'),
                     ('size_bytes', 'size_bytes'))} == intent['source'])
        publications = []
        for kind in ('rag', 'episode'):
            scope = intent['scopes'][kind]
            saved = tuple(terminal[kind + '_receipt'])
            _require(len(saved) == 20)
            row = cursor.execute('''select g.state,
                to_jsonb(g) as receipt, i.identity as index_identity
                from vector_generations g join vector_indexes i
                  on i.tenant_id=g.tenant_id and i.vector_kind=g.vector_kind
                  and i.namespace=g.namespace and i.index_key=g.index_key
                where g.generation_id=%s and g.tenant_id=%s and g.vector_kind=%s
                  and g.namespace=%s and g.index_key=%s''',
                (UUID(scope['candidate_id']), attempt['user_id'], kind,
                 scope['namespace'], scope['index_key'])).fetchone()
            _require(row is not None and row['state'] in ('published', 'retired') and
                     row['index_identity'] == scope['identity'] and
                     tuple(_receipt_from_sql(row['receipt'])) == saved)
            sealed = _read_slot(getattr(evidence, kind + '_sealed_slot'))
            _require(saved[0] == scope['candidate_id'] and
                     saved[5] == scope['head']['revision'] and
                     saved[6] == scope['index_revision'] and
                     saved[7] == scope['head']['revision'] + 1 and
                     saved[8] == intent['snapshots']['next_' +
                         ('history' if kind == 'rag' else 'memory')]['version'] and
                     saved[9] == sealed['expected_count'] and
                     saved[10] == sealed['content_digest'])
            old = cursor.execute('''select state,to_jsonb(g) as receipt
                from vector_generations g where generation_id=%s and tenant_id=%s
                  and vector_kind=%s and namespace=%s and index_key=%s''',
                (UUID(scope['head']['last_generation_id']), attempt['user_id'],
                 kind, scope['namespace'], scope['index_key'])).fetchone()
            _require(old is not None and old['state'] == 'retired' and
                     _receipt_from_sql(old['receipt']) == scope['old_receipt'])
            head = VectorHead('published' if saved[9] else 'empty', saved[7],
                              UUID(saved[0]) if saved[9] else None,
                              saved[6], saved[8])
            publications.append(VectorPublication(UUID(saved[0]), head, task))
        return DetachedPairPublication(*publications, task)

    @staticmethod
    def _domain(cursor, evidence, terminal, pair):
        intent = evidence.intent
        attempt = intent['attempt']
        document = _read_slot(evidence.document_slot)
        user = attempt['user_id']
        history = _read_snapshot(cursor, user, 'history')
        memory = _read_snapshot(cursor, user, 'memory')
        for current, kind in ((history, 'history'), (memory, 'memory')):
            saved = intent['snapshots']['next_' + kind]
            _require(current is not None and current.version >= saved['version'])
            if current.version == saved['version']:
                _require(_canonical(current.data) == _canonical(saved['data']))
        document_evidence(user, history.data)
        records = [record for record in history.data['documents']
            if record.get('document_id') == attempt['document_id'] or
               record.get('import_task_id') == attempt['task_id']]
        _require(len(records) == 1 and
                 _canonical(records[0]) == _canonical(intent['document']['record']))
        rows = cursor.execute('''select user_id,document_id,content,metadata,created_at
            from memory_documents where user_id=%s order by document_id''',
            (user,)).fetchall()
        try:
            _all_memory(memory.data, rows, user, attempt['task_id'],
                        intent['event']['event_id'],
                        expected_item=intent['event']['item'],
                        expected_metadata=intent['event']['metadata'])
        except EpisodeReadError as error:
            raise _InvalidProof('Retained Memory row is invalid') from error
        target_rows = [row for row in rows
                       if row['document_id'] == intent['event']['event_id']]
        _require(len(target_rows) == 1 and
                 target_rows[0]['metadata'] ==
                     _canonical(intent['event']['metadata']).decode('utf-8'))
        pinned = cursor.execute('''select bucket,object_key,version_id,sha256,
            size_bytes,history_record_sha256 from document_objects
            where user_id=%s and document_id=%s''',
            (user, attempt['document_id'])).fetchone()
        ref = (user, attempt['document_id'], document['bucket'], document['key'],
               document['version_id'], document['sha256'], document['size_bytes'])
        _require(pinned is not None and
                 (user, attempt['document_id'], pinned['bucket'], pinned['object_key'],
                  pinned['version_id'], pinned['sha256'], pinned['size_bytes']) == ref and
                 pinned['history_record_sha256'] == document['record_hash'] ==
                 PostgresDocumentObjectRepository._record_hash(intent['document']['record']))
        rag = intent['scopes']['rag']
        witness = cursor.execute('''select last_generation_id,index_revision,
            publication_snapshot_version,document_count,documents_sha256
            from history_document_witnesses where tenant_id=%s and vector_kind='rag'
            and namespace=%s and index_key=%s and head_revision=%s''',
            (user, rag['namespace'], rag['index_key'],
             terminal['rag_receipt'][7])).fetchone()
        _require(witness is not None and
                 str(witness['last_generation_id']) == terminal['rag_receipt'][0] and
                 witness['index_revision'] == terminal['rag_receipt'][6] and
                 witness['publication_snapshot_version'] ==
                     intent['snapshots']['next_history']['version'] and
                 witness['document_count'] == intent['document']['count'] and
                 witness['documents_sha256'] == intent['document']['digest'])
        fence = cursor.execute('''select 1 from qa_deletion_fences where user_id=%s
            and target_type='document' and target_id=%s and
            (status in ('queued','running') or
             (status='failed' and attempt_count<3)) limit 1''',
            (user, attempt['document_id'])).fetchone()
        _require(fence is None)
        return ref, (pair.rag.head.revision, pair.rag.generation_id,
                     intent['document']['count'], intent['document']['digest'])

    def prove_exact(self, user_id: str, task_id: str,
                    task_lease_version: int) -> DetachedImportMemoryPublication | None:
        key = AttemptKey(user_id, task_id, task_lease_version)
        phase = 'evidence'
        try:
            with self.database.transaction() as cursor:
                cursor.execute('set transaction isolation level repeatable read read only')
                evidence = self.evidence._read_exact_in_cursor(cursor, key)
                if evidence is None or evidence.phase not in (
                        'terminal_committed', 'proved_succeeded'):
                    return None
                phase = evidence.phase
                terminal = _read_slot(evidence.terminal_slot)
                pair = self._pair(cursor, evidence, terminal)
                ref, witness = self._domain(cursor, evidence, terminal, pair)
                document = _read_slot(evidence.document_slot)
                envelope = PublicationProofEnvelope(key, evidence.intent_hash,
                    document['final_expected_hash'], evidence.phase,
                    evidence.phase_version, tuple(terminal['rag_receipt']),
                    tuple(terminal['episode_receipt']))
                return DetachedImportMemoryPublication(pair,
                    evidence.intent['event']['event_id'],
                    evidence.intent['snapshots']['next_history']['version'],
                    evidence.intent['snapshots']['next_memory']['version'],
                    ref, witness, envelope)
        except (PublicationEvidenceError, ImportMemoryPublicationError,
                HistoryDocumentWitnessError, _InvalidProof):
            return None
        except Exception as error:
            raise PublicationProofUnknown(key, phase) from error
