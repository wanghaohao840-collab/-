"""Opt-in atomic RAG, episode, History and Memory import publication."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from threading import Lock
from uuid import UUID, uuid4, uuid5
from weakref import WeakKeyDictionary

from app.import_document_publication import (
    ImportDocumentPublicationError, ImportDocumentPublicationService,
)
from app.import_publication_evidence import (
    AttemptKey, PostgresImportPublicationEvidenceRepository, encode_intent,
    require_no_gate_in_transaction,
)
from app.import_vector_publication import (
    ImportPairUnknown, ImportVectorPairPublicationService, PairScopePlan,
)
from app.postgres_document_objects import PostgresDocumentObjectRepository, VerifiedDocumentRef
from app.postgres_history_document_witnesses import document_evidence
from app.postgres_memory_documents import PostgresMemoryDocumentStore
from app.postgres_vector_generations import VectorScope
from app.published_episode_reads import (
    PublishedEpisodeReadFactory, _MAX_POINTS, _metadata, _json,
)
from hello_agents.memory.rag.embedding_profile import EmbeddingProfile
from hello_agents.memory.rag.index_identity import IndexIdentityError, validate_profile
from hello_agents.memory.rag.prepare import PROJECT_POINT_NAMESPACE_UUID
from hello_agents.memory.storage.vector_store import VectorPoint
from hello_agents.memory.storage.generation_vector_store import (
    GenerationVectorStoreError, _validate_candidate_points,
)


class ImportMemoryPublicationError(ImportDocumentPublicationError):
    """The complete import domain cannot be safely published."""


@dataclass(frozen=True)
class ImportMemoryPublication:
    pair: object
    event_id: str
    history_version: int
    memory_version: int
    document_ref: tuple
    witness: tuple
    _context: object = field(repr=False, compare=False)
    _expected: object = field(repr=False, compare=False)


class ImportMemoryPublicationUnknown(RuntimeError):
    def __init__(self, context, expected, phase, cause=None):
        self.context = context
        self.expected = deepcopy(expected)
        self._frozen_expected = deepcopy(expected)
        self.generation_ids = context.generation_ids
        self.scopes = context.scopes
        self.attempt_key = context.attempt_key
        self.phase = phase
        super().__init__(f'Import Memory {self.generation_ids} has unknown {phase} outcome')
        if cause is not None:
            self.__cause__ = cause


class _LivePublication:
    """Opaque, process-local handle; issuance is kept by its C service."""

    __slots__ = ('__weakref__',)


_LIVE_ISSUERS = WeakKeyDictionary()


def _require_live_issuer(live):
    if type(live) is not _LivePublication:
        raise ImportMemoryPublicationError('Original issued live handle required')
    service = _LIVE_ISSUERS.get(live)
    if service is None:
        raise ImportMemoryPublicationError('Live publication issuer is missing')
    return service, service._require_live_issue(live)


@dataclass(frozen=True)
class _LiveIssue:
    plan: _FrozenMemoryPlan
    original_attempt: object
    key: AttemptKey
    intent_hash: str
    document_service: object
    document_repository: object
    pair: object
    imports: object
    coordinator: object
    rag_service: object
    episode_service: object
    rag_authority: object
    episode_authority: object
    database: object
    rag_raw: object
    episode_raw: object


class DurablePublicationUnknown(RuntimeError):
    def __init__(self, attempt_key: AttemptKey, phase: str):
        self.attempt_key = attempt_key
        self.phase = phase
        super().__init__(f'Durable publication outcome is unknown at {phase}')


class _FixedTerminalWork:
    __slots__ = ('__weakref__',)

    def run(self, cursor, admission):
        service, issue = _require_terminal_work(self)
        return service._run_fixed_terminal(cursor, admission, issue)


@dataclass
class _FixedTerminalIssue:
    live: _LivePublication
    prepared: object
    rag_sealed: object
    episode_sealed: object
    expected: object
    callback: object
    consumed: bool = False


_TERMINAL_ISSUERS = WeakKeyDictionary()


def _require_terminal_work(work):
    if type(work) is not _FixedTerminalWork:
        raise ImportMemoryPublicationError('Issued fixed terminal work required')
    service = _TERMINAL_ISSUERS.get(work)
    issue = None if service is None else service._terminal_issues.get(work)
    if (issue is None or issue.callback.__self__ is not work
            or issue.callback.__func__ is not _FixedTerminalWork.run
            or service._terminal_work_for_live.get(issue.live) is not work):
        raise ImportMemoryPublicationError('Fixed terminal callback issuer differs')
    service._require_live_issue(issue.live)
    return service, issue


class _TerminalAdmission:
    __slots__ = ('__weakref__',)


@dataclass
class _AdmissionIssue:
    service: object
    terminal_work: _FixedTerminalWork
    original_issue: _LiveIssue
    coordinator: object
    cursor: object
    evidence: object
    transaction_id: int
    active: bool = True


_ADMISSIONS = WeakKeyDictionary()


def _create_terminal_admission(work, cursor, evidence):
    service, terminal = _require_terminal_work(work)
    original_issue = service._require_live_issue(terminal.live)
    with service._live_execution_lock:
        if terminal.consumed:
            raise ImportMemoryPublicationError('Fixed terminal work was already attempted')
        terminal.consumed = True
    admission = _TerminalAdmission()
    transaction_id = cursor.execute('select txid_current() as id').fetchone()['id']
    _ADMISSIONS[admission] = _AdmissionIssue(service, work, original_issue,
        original_issue.coordinator, cursor, evidence, transaction_id)
    return admission


def _close_terminal_admission(admission):
    issued = _ADMISSIONS.get(admission)
    if issued is not None:
        issued.active = False


def _require_terminal_admission(admission, cursor, operation, *values):
    from psycopg.pq import TransactionStatus
    issued = _ADMISSIONS.get(admission) if type(admission) is _TerminalAdmission else None
    if (issued is None or not issued.active or issued.cursor is not cursor
            or cursor.connection.info.transaction_status != TransactionStatus.INTRANS):
        raise ImportMemoryPublicationError('Active transaction-bound terminal admission required')
    if cursor.execute('select txid_current() as id').fetchone()['id'] != issued.transaction_id:
        raise ImportMemoryPublicationError('Terminal admission transaction changed')
    service, terminal = _require_terminal_work(issued.terminal_work)
    evidence = issued.evidence
    intent = evidence.intent
    user = intent['attempt']['user_id']
    if (service is not issued.service
            or issued.original_issue is not service._require_live_issue(terminal.live)
            or issued.coordinator is not issued.original_issue.coordinator
            or evidence.phase != 'pair_sealed'
            or evidence.terminal_slot is not None):
        raise ImportMemoryPublicationError('Terminal admission evidence differs')
    if operation == 'terminal_entry':
        pass
    elif operation == 'task_source':
        (attempt,) = values
        issue = service._require_live_issue(terminal.live)
        if attempt is not issue.plan.attempt or attempt != issue.original_attempt:
            raise ImportMemoryPublicationError('Admitted attempt differs')
    elif operation == 'snapshot':
        subject, kind, data, old_version = values
        expected = intent['snapshots'].get('next_' + kind)
        prior = intent['snapshots'].get('old_' + kind)
        if (subject != user or kind not in ('history', 'memory')
                or expected is None or prior is None
                or old_version != prior['version']
                or _canonical(data) != _canonical(expected['data'])):
            raise ImportMemoryPublicationError('Admitted snapshot differs')
    elif operation == 'memory_document':
        subject, event_id, content, metadata = values
        if (subject != user or event_id != intent['event']['event_id']
                or content != intent['event']['item']['content']
                or metadata != _canonical(intent['event']['metadata'])):
            raise ImportMemoryPublicationError('Admitted Memory row differs')
    elif operation == 'document':
        repository, token, copied = values
        if repository is not service.documents.documents:
            raise ImportMemoryPublicationError('Admitted document issuer differs')
        issued_ref = service._require_document_continuation(terminal.live, token)
        document = json.loads(evidence.document_slot)
        if (repository._issuance_values(copied) != issued_ref
                or issued_ref != (document['user_id'], document['document_id'],
                           document['bucket'], document['key'],
                           document['version_id'], document['sha256'],
                           document['size_bytes'])
                or document['record_hash'] !=
                    PostgresDocumentObjectRepository._record_hash(
                        intent['document']['record'])):
            raise ImportMemoryPublicationError('Admitted document reference differs')
    elif operation == 'witness':
        scope, pairing, document_evidence_value = values
        rag = intent['scopes']['rag']
        next_history = intent['snapshots']['next_history']
        if (scope != service._require_live_issue(terminal.live).plan.rag_scope
                or scope.key != (user, 'rag', rag['namespace'], rag['index_key'])):
            raise ImportMemoryPublicationError('Admitted witness scope differs')
        actual = (pairing.head.revision, str(pairing.last_generation_id),
                  pairing.receipt_index_revision, pairing.receipt_snapshot_version,
                  document_evidence_value.count, document_evidence_value.digest)
        old = terminal.prepared.old_pairing
        expected_old = (old.head.revision, str(old.last_generation_id),
                        old.receipt_index_revision, old.receipt_snapshot_version,
                        terminal.prepared.old_evidence.count,
                        terminal.prepared.old_evidence.digest)
        expected_new = (rag['head']['revision'] + 1, rag['candidate_id'],
                        rag['index_revision'], next_history['version'],
                        intent['document']['count'], intent['document']['digest'])
        if actual not in (expected_old, expected_new):
            raise ImportMemoryPublicationError('Admitted witness differs')
    elif operation == 'vector_publish':
        (repository, scope, authority, generation_id, expected_revision,
         expected_index_revision, snapshot_version, expected_count,
         content_digest) = values
        issue = service._require_live_issue(terminal.live)
        kind = scope.vector_kind
        if kind not in ('rag', 'episode'):
            raise ImportMemoryPublicationError('Vector publication kind differs')
        vector = issue.rag_service if kind == 'rag' else issue.episode_service
        sealed = terminal.rag_sealed if kind == 'rag' else terminal.episode_sealed
        planned_scope = issue.plan.rag_scope if kind == 'rag' else service.episode_scope
        frozen = intent['scopes'][kind]
        saved = json.loads(getattr(evidence, kind + '_sealed_slot'))
        if (repository is not vector.authority or scope is not planned_scope
                or authority is not issue.original_attempt
                or generation_id != sealed.generation_id
                or str(generation_id) != frozen['candidate_id']
                or expected_revision != sealed.expected_head.revision
                or expected_revision != frozen['head']['revision']
                or expected_index_revision != sealed.expected_index_revision
                or expected_index_revision != frozen['index_revision']
                or snapshot_version != sealed.snapshot_version
                or expected_count is None or expected_count != sealed.expected_count
                or content_digest is None or content_digest != sealed.content_digest
                or saved['expected_count'] != expected_count
                or saved['content_digest'] != content_digest
                or saved['snapshot_version'] != snapshot_version):
            raise ImportMemoryPublicationError('Admitted vector publication differs')
        from app.import_publication_evidence import PostgresImportPublicationEvidenceRepository
        repo = PostgresImportPublicationEvidenceRepository(service.database)
        current = repo._read_exact_in_cursor(cursor, issue.key)
        if current != evidence:
            raise ImportMemoryPublicationError('Admitted vector evidence changed')
        gate = cursor.execute('''select status from user_publication_gates
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            (issue.key.user_id,issue.key.task_id,issue.key.task_lease_version)).fetchone()
        if gate is None or gate['status'] != 'unresolved':
            raise ImportMemoryPublicationError('Admitted vector gate changed')
        for candidate_kind in ('rag','episode'):
            candidate = intent['scopes'][candidate_kind]
            reservation = cursor.execute('''select state,owner,user_lease_token,
                user_lease_version,task_id,task_lease_version,vector_kind,
                namespace,index_key,base_revision from generation_reservations
                where generation_id=%s''', (candidate['candidate_id'],)).fetchone()
            if (reservation is None or reservation['state'] != 'reserved'
                    or reservation['owner'] != issue.original_attempt.worker_id
                    or str(reservation['user_lease_token']) != intent['attempt']['user_lease_token']
                    or reservation['user_lease_version'] != intent['attempt']['user_lease_version']
                    or reservation['task_id'] != issue.key.task_id
                    or reservation['task_lease_version'] != issue.key.task_lease_version
                    or reservation['vector_kind'] != candidate_kind
                    or reservation['namespace'] != candidate['namespace']
                    or reservation['index_key'] != candidate['index_key']
                    or reservation['base_revision'] != candidate['head']['revision']):
                raise ImportMemoryPublicationError('Admitted vector reservation changed')
    else:
        raise ImportMemoryPublicationError('Operation is outside terminal admission')


class _TerminalReleaseBinding:
    __slots__ = ('__weakref__',)


@dataclass
class _ReleaseIssue:
    service: object
    work: _FixedTerminalWork
    admission: _TerminalAdmission
    admission_issue: _AdmissionIssue
    original_issue: _LiveIssue
    coordinator: object
    cursor: object
    transaction_id: int
    attempt: object
    evidence: object
    terminal_sha256: str
    receipts: tuple
    finish_done: bool = False
    release_done: bool = False
    active: bool = True


_RELEASE_BINDINGS = WeakKeyDictionary()


def _terminal_receipts(cursor, evidence):
    from app.import_publication_evidence import _receipt_from_sql
    terminal = json.loads(evidence.terminal_slot)
    result = []
    for kind in ('rag', 'episode'):
        scope = evidence.intent['scopes'][kind]
        row = cursor.execute('''select * from vector_generations
            where generation_id=%s and tenant_id=%s and vector_kind=%s
            and namespace=%s and index_key=%s''',
            (scope['candidate_id'],evidence.key.user_id,kind,
             scope['namespace'],scope['index_key'])).fetchone()
        if row is None:
            raise ImportMemoryPublicationError('Terminal vector receipt is missing')
        projected = _receipt_from_sql(row)
        if projected != terminal[kind + '_receipt']:
            raise ImportMemoryPublicationError('Terminal vector receipt changed')
        result.append(tuple(projected))
    return tuple(result)


def _issue_terminal_release_binding(admission, cursor, terminal_evidence):
    _require_terminal_admission(admission, cursor, 'terminal_entry')
    issued = _ADMISSIONS[admission]
    service, terminal = _require_terminal_work(issued.terminal_work)
    issue = service._require_live_issue(terminal.live)
    if (service is not issued.service or service.database is not issue.database
            or issued.cursor is not cursor or terminal_evidence.phase != 'terminal_committed'
            or terminal_evidence.phase_version != issued.evidence.phase_version + 1
            or terminal_evidence.key != issue.key):
        raise ImportMemoryPublicationError('Terminal release issuer differs')
    repository = PostgresImportPublicationEvidenceRepository(service.database)
    current = repository._read_exact_in_cursor(cursor, issue.key)
    if current != terminal_evidence or current.terminal_slot is None:
        raise ImportMemoryPublicationError('Terminal release evidence differs')
    receipts = _terminal_receipts(cursor, current)
    binding = _TerminalReleaseBinding()
    _RELEASE_BINDINGS[binding] = _ReleaseIssue(service,issued.terminal_work,
        admission, issued, issued.original_issue, issued.coordinator,
        cursor,issued.transaction_id,issue.original_attempt,current,
        sha256(current.terminal_slot).hexdigest(),receipts)
    return binding


def _require_terminal_release_binding(binding, cursor, attempt, *, operation):
    from psycopg.pq import TransactionStatus
    issued = _RELEASE_BINDINGS.get(binding) if type(binding) is _TerminalReleaseBinding else None
    if (issued is None or not issued.active or issued.cursor is not cursor
            or issued.attempt is not attempt or operation not in ('finish','release')
            or cursor.connection.info.transaction_status != TransactionStatus.INTRANS
            or cursor.execute('select txid_current() as id').fetchone()['id'] != issued.transaction_id):
        raise ImportMemoryPublicationError('Terminal release binding is invalid')
    service, terminal = _require_terminal_work(issued.work)
    admission_issue = _ADMISSIONS.get(issued.admission)
    issue = service._require_live_issue(terminal.live)
    if (service is not issued.service or admission_issue is not issued.admission_issue
            or admission_issue.service is not service
            or admission_issue.terminal_work is not issued.work
            or admission_issue.original_issue is not issued.original_issue
            or admission_issue.coordinator is not issued.coordinator
            or issued.original_issue is not issue
            or issued.coordinator is not issue.coordinator
            or issued.coordinator is not issue.imports.coordinator
            or issue.original_attempt is not attempt
            or service.database is not issue.database
            or service.pair.imports is not issue.imports
            or service.pair.rag.authority is not issue.rag_authority
            or service.pair.episode.authority is not issue.episode_authority
            or terminal.callback.__self__ is not issued.work
            or terminal.callback.__func__ is not _FixedTerminalWork.run):
        raise ImportMemoryPublicationError('Terminal release provenance changed')
    if operation == 'release' and (not issued.finish_done or issued.release_done
                                   or admission_issue.active):
        raise ImportMemoryPublicationError('Terminal release sequence differs')
    if operation == 'finish' and (issued.finish_done or admission_issue.active):
        raise ImportMemoryPublicationError('Terminal finish sequence differs')
    repository = PostgresImportPublicationEvidenceRepository(service.database)
    current = repository._read_exact_in_cursor(cursor, issue.key)
    if (current != issued.evidence or current.terminal_slot is None
            or sha256(current.terminal_slot).hexdigest() != issued.terminal_sha256
            or _terminal_receipts(cursor, current) != issued.receipts):
        raise ImportMemoryPublicationError('Terminal release proof changed')
    gate = cursor.execute('''select status from user_publication_gates
        where user_id=%s and task_id=%s and task_lease_version=%s''',
        (issue.key.user_id,issue.key.task_id,issue.key.task_lease_version)).fetchone()
    task = cursor.execute('''select * from import_tasks where id=%s and user_id=%s''',
        (issue.key.task_id,issue.key.user_id)).fetchone()
    audit = cursor.execute('''select * from import_task_attempts
        where task_id=%s and lease_version=%s''',
        (issue.key.task_id,issue.key.task_lease_version)).fetchone()
    lease = cursor.execute('''select * from user_mutation_leases
        where user_id=%s''', (issue.key.user_id,)).fetchone()
    if (gate is None or gate['status'] != 'unresolved' or task is None
            or audit is None or lease is None
            or task['claimed_by'] != attempt.worker_id
            or task['lease_token'] != attempt.lease_token
            or task['lease_version'] != attempt.lease_version
            or task['user_lease_token'] != attempt.user_lease.lease_token
            or task['user_lease_version'] != attempt.user_lease.lease_version
            or audit['worker_id'] != attempt.worker_id
            or audit['lease_token'] != attempt.lease_token
            or audit['user_lease_token'] != attempt.user_lease.lease_token
            or audit['user_lease_version'] != attempt.user_lease.lease_version
            or lease['owner'] != attempt.worker_id
            or lease['lease_token'] != attempt.user_lease.lease_token
            or lease['lease_version'] != attempt.user_lease.lease_version):
        raise ImportMemoryPublicationError('Terminal release owner changed')
    now = cursor.execute('select clock_timestamp() as now').fetchone()['now']
    if lease['lease_expires_at'] <= now or task['lease_expires_at'] <= now:
        raise ImportMemoryPublicationError('Terminal release owner expired')
    if operation == 'finish':
        if task['status'] != 'running' or task['stage'] != 'committing' or audit['ended_at'] is not None:
            raise ImportMemoryPublicationError('Terminal finish task changed')
        issued.finish_done = True
    else:
        if (task['status'] != 'succeeded' or task['stage'] != 'succeeded'
                or task['progress'] != 100 or audit['end_reason'] != 'succeeded'
                or audit['ended_at'] is None):
            raise ImportMemoryPublicationError('Terminal release task changed')
        issued.release_done = True


def _close_terminal_release_binding(binding):
    issued = _RELEASE_BINDINGS.get(binding)
    if issued is not None:
        issued.active = False


@dataclass(frozen=True)
class _Expected:
    task: object
    history: dict
    history_version: int
    memory: dict
    memory_version: int
    record: dict
    item: dict
    event_metadata: dict
    event_id: str
    ref: tuple
    new_evidence: object
    old_rag_receipt: tuple
    old_episode_receipt: tuple
    old_rag_id: UUID
    old_episode_id: UUID
    new_receipts: tuple | None = None


@dataclass(frozen=True)
class _FrozenMemoryPlan:
    rag_scope: VectorScope
    attempt: object
    task: object
    bundle: object = field(repr=False)
    event_id: str
    vector: tuple = field(repr=False)
    old_rows: tuple = field(repr=False)
    document: object = field(repr=False)
    old_rag_pairing: object = field(repr=False)
    old_rag_receipt: tuple = field(repr=False)
    old_episode_receipt: tuple = field(repr=False)
    item: dict = field(repr=False)
    event_metadata: dict = field(repr=False)
    next_memory: dict = field(repr=False)
    new_evidence: object = field(repr=False)
    candidate_ids: tuple[UUID, UUID]
    intent: dict = field(repr=False)


_RECEIPT_FIELDS = (
    'generation_id', 'tenant_id', 'vector_kind', 'namespace', 'index_key',
    'base_revision', 'index_revision', 'publication_revision',
    'publication_snapshot_version', 'expected_count', 'content_digest',
    'owner', 'user_lease_token', 'user_lease_version', 'task_id',
    'task_lease_token', 'task_lease_version', 'created_at', 'sealed_at',
    'published_at',
)
_UUID_FIELDS = {'generation_id', 'user_lease_token', 'task_lease_token'}
_TIME_FIELDS = {'created_at', 'sealed_at', 'published_at'}
_INT_FIELDS = {'base_revision', 'index_revision', 'publication_revision',
               'publication_snapshot_version', 'expected_count',
               'user_lease_version', 'task_lease_version'}


def _canonical(value):
    return _json(value)


def _receipt_projection(row):
    if row is None:
        raise ImportMemoryPublicationError('Publication receipt is missing')
    values = []
    for key in _RECEIPT_FIELDS:
        if key not in row:
            raise ImportMemoryPublicationError('Publication receipt is incomplete')
        value = row[key]
        if key in _UUID_FIELDS and value is not None:
            try:
                parsed = UUID(str(value))
            except (ValueError, TypeError, AttributeError) as error:
                raise ImportMemoryPublicationError('Publication UUID is invalid') from error
            if str(parsed) != str(value):
                raise ImportMemoryPublicationError('Publication UUID is noncanonical')
            value = str(parsed)
        elif key in _TIME_FIELDS and value is not None:
            try:
                parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
                    value.replace('Z', '+00:00'))
                if parsed.utcoffset() is None:
                    raise ValueError('Naive timestamp')
                value = parsed.astimezone(timezone.utc).isoformat(timespec='microseconds')
            except (ValueError, TypeError, AttributeError) as error:
                raise ImportMemoryPublicationError('Publication time is invalid') from error
        elif key in _INT_FIELDS and value is not None and type(value) is not int:
            raise ImportMemoryPublicationError('Publication integer is invalid')
        elif key not in _UUID_FIELDS | _TIME_FIELDS | _INT_FIELDS:
            if value is not None and not isinstance(value, str):
                raise ImportMemoryPublicationError('Publication text is invalid')
        values.append(value)
    if values[0] is None or any(values[_RECEIPT_FIELDS.index(k)] is None
                                for k in ('created_at', 'sealed_at', 'published_at')):
        raise ImportMemoryPublicationError('Publication receipt is incomplete')
    return tuple(values)


def _all_memory(snapshot, documents, user, task_id, event_id, *, expected_item=None,
                expected_metadata=None):
    if not isinstance(snapshot, dict) or snapshot.get('user_id') != user or not isinstance(
            snapshot.get('memories'), list):
        raise ImportMemoryPublicationError('Complete Memory snapshot is invalid')
    seen_items = set()
    matches = []
    for item in snapshot['memories']:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id']:
            raise ImportMemoryPublicationError('Memory identity is invalid')
        metadata = item.get('metadata')
        if not isinstance(metadata, dict) or metadata.get('user_id') != user:
            raise ImportMemoryPublicationError('Memory owner differs')
        _canonical(item)
        if item['id'] in seen_items:
            raise ImportMemoryPublicationError('Duplicate Memory identity')
        seen_items.add(item['id'])
        if item['id'] == event_id or metadata.get('import_task_id') == task_id:
            matches.append(item)
    seen_rows = set()
    row_matches = []
    normalized = []
    for row in documents:
        if row.get('user_id') != user or not isinstance(row.get('document_id'), str):
            raise ImportMemoryPublicationError('Memory document owner or identity differs')
        doc_id = row['document_id']
        if not doc_id or doc_id in seen_rows:
            raise ImportMemoryPublicationError('Duplicate Memory document identity')
        seen_rows.add(doc_id)
        metadata = _metadata(row.get('metadata'))
        if metadata.get('user_id') != user:
            raise ImportMemoryPublicationError('Memory document owner differs')
        if doc_id == event_id or metadata.get('import_task_id') == task_id:
            row_matches.append((row, metadata))
        normalized.append((doc_id, row.get('content'), row.get('metadata'),
                           row.get('created_at')))
    if expected_item is None:
        if matches or row_matches or event_id in seen_items or event_id in seen_rows:
            raise ImportMemoryPublicationError('Import event or task already exists')
    else:
        if (len(matches) != 1 or _canonical(matches[0]) != _canonical(expected_item)
                or len(row_matches) != 1 or row_matches[0][0]['document_id'] != event_id
                or row_matches[0][0]['content'] != expected_item['content']
                or _canonical(row_matches[0][1]) != _canonical(expected_metadata)):
            raise ImportMemoryPublicationError('Import event proof differs')
    return tuple(sorted(normalized, key=lambda value: value[0]))


class ImportMemoryPublicationService:
    def __init__(self, database, store, rag_vectors, episode_vectors, *,
                 trusted_episode_scope: VectorScope):
        if (rag_vectors.authority.database is not database
                or episode_vectors.authority.database is not database):
            raise ValueError('All authorities require one database object')
        self.documents = ImportDocumentPublicationService(database, store, rag_vectors)
        self.pair = ImportVectorPairPublicationService(rag_vectors, episode_vectors)
        self.episodes = PublishedEpisodeReadFactory(episode_vectors, trusted_episode_scope)
        self.episode_scope = deepcopy(trusted_episode_scope)
        self.database = database
        self._live_issues = WeakKeyDictionary()
        self._document_continuations = WeakKeyDictionary()
        self._sealed_continuations = WeakKeyDictionary()
        self._terminal_issues = WeakKeyDictionary()
        self._terminal_work_for_live = WeakKeyDictionary()
        self._live_executions = WeakKeyDictionary()
        self._live_execution_lock = Lock()

    def _issue_live_publication(self, rag_scope, attempt, new_rag_points, *,
                                event_vector, event_profile) -> _LivePublication:
        plan = self._plan_intent(rag_scope, attempt, new_rag_points,
            event_vector=event_vector, event_profile=event_profile)
        encoded = encode_intent(plan.intent)
        key = PostgresImportPublicationEvidenceRepository(self.database).reserve_intent(
            plan.attempt, plan.intent)
        live = _LivePublication()
        self._live_issues[live] = _LiveIssue(plan, attempt, key, encoded.digest,
            self.documents, self.documents.documents, self.pair,
            self.pair.imports, self.pair.imports.coordinator,
            self.pair.rag, self.pair.episode,
            self.pair.rag.authority, self.pair.episode.authority,
            self.database, self.pair.rag.raw, self.pair.episode.raw)
        _LIVE_ISSUERS[live] = self
        return live

    def _require_live_issue(self, live: _LivePublication) -> _LiveIssue:
        if type(live) is not _LivePublication:
            raise ImportMemoryPublicationError('Original issued live handle required')
        issue = self._live_issues.get(live)
        if (issue is None or _LIVE_ISSUERS.get(live) is not self
                or issue.document_service is not self.documents
                or issue.document_repository is not self.documents.documents
                or issue.pair is not self.pair or issue.imports is not self.pair.imports
                or issue.coordinator is not issue.imports.coordinator
                or issue.coordinator.database is not self.database
                or issue.rag_service is not self.pair.rag
                or issue.episode_service is not self.pair.episode
                or issue.rag_authority is not self.pair.rag.authority
                or issue.episode_authority is not self.pair.episode.authority
                or issue.database is not self.database
                or issue.document_service.database is not self.database
                or issue.document_repository.database is not self.database
                or issue.imports.database is not self.database
                or issue.rag_authority.database is not self.database
                or issue.episode_authority.database is not self.database
                or issue.rag_raw is not self.pair.rag.raw
                or issue.episode_raw is not self.pair.episode.raw
                or issue.plan.attempt != issue.original_attempt
                or encode_intent(issue.plan.intent).digest != issue.intent_hash):
            raise ImportMemoryPublicationError('Live publication issuance differs')
        return issue

    def _bind_verified_document(self, live: _LivePublication, verified) -> None:
        issue = self._require_live_issue(live)
        if type(verified) is not VerifiedDocumentRef:
            raise ImportMemoryPublicationError('Original verified document token required')
        if live in self._document_continuations:
            raise ImportMemoryPublicationError('Document continuation is write-once')
        repository = issue.document_repository
        issued = repository._verified.get(verified)
        if (issued is None or issued != repository._issuance_values(verified)
                or issued != (issue.key.user_id, issue.plan.task.document_id,
                    self.documents.store.bucket, issue.plan.document.object_key,
                    verified.ref.version_id, issue.plan.task.source.sha256,
                    issue.plan.task.source.size_bytes)):
            raise ImportMemoryPublicationError('Verified document issuer or value differs')
        self._document_continuations[live] = (verified, tuple(issued))

    def _require_document_continuation(self, live, verified):
        self._require_live_issue(live)
        continuation = self._document_continuations.get(live)
        if (type(verified) is not VerifiedDocumentRef
                or continuation is None or continuation[0] is not verified
                or self.documents.documents._verified.get(verified) != continuation[1]
                or self.documents.documents._issuance_values(verified) != continuation[1]):
            raise ImportMemoryPublicationError('Original verified document continuation required')
        return continuation[1]

    def _issue_fixed_terminal(self, live, prepared, rag_sealed, episode_sealed,
                               expected):
        issue = self._require_live_issue(live)
        if (prepared.verified is not self._document_continuations[live][0]
                or prepared.task != issue.plan.task
                or rag_sealed.generation_id != issue.plan.candidate_ids[0]
                or episode_sealed.generation_id != issue.plan.candidate_ids[1]):
            raise ImportMemoryPublicationError('Terminal work differs from issuance')
        issue.rag_service._require_issued_seal(rag_sealed)
        issue.episode_service._require_issued_seal(episode_sealed)
        with self._live_execution_lock:
            if live in self._terminal_work_for_live:
                raise ImportMemoryPublicationError('Fixed terminal work was already issued')
            work = _FixedTerminalWork()
            self._terminal_issues[work] = _FixedTerminalIssue(live, prepared,
                rag_sealed, episode_sealed, deepcopy(expected), work.run)
            self._terminal_work_for_live[live] = work
            _TERMINAL_ISSUERS[work] = self
        return work

    def _bind_sealed(self, live, kind, sealed):
        issue = self._require_live_issue(live)
        service = issue.rag_service if kind == 'rag' else issue.episode_service
        service._require_issued_seal(sealed)
        current = self._sealed_continuations.setdefault(live, {})
        if kind in current:
            raise ImportMemoryPublicationError('Sealed continuation is write-once')
        current[kind] = sealed

    def _require_pair_sealed(self, live, evidence):
        issue = self._require_live_issue(live)
        selected = self._sealed_continuations.get(live)
        if (selected is None or set(selected) != {'rag', 'episode'}
                or evidence.phase != 'pair_sealed'
                or evidence.rag_sealed_slot is None
                or evidence.episode_sealed_slot is None):
            raise ImportMemoryPublicationError('Both original seals are required')
        for kind, service in (('rag', issue.rag_service),
                              ('episode', issue.episode_service)):
            sealed = selected[kind]
            service._require_issued_seal(sealed)
            saved = json.loads(getattr(evidence, kind + '_sealed_slot'))
            if (saved['generation_id'] != str(sealed.generation_id)
                    or saved['expected_count'] != sealed.expected_count
                    or saved['content_digest'] != sealed.content_digest
                    or saved['snapshot_version'] != sealed.snapshot_version):
                raise ImportMemoryPublicationError('Saved seal differs from issuance')
        return selected

    def _run_fixed_terminal(self, cursor, admission, terminal):
        issued = _ADMISSIONS.get(admission)
        if (issued is None or not issued.active or issued.cursor is not cursor
                or issued.service is not self or issued.evidence.phase != 'pair_sealed'
                or _require_terminal_work(issued.terminal_work)[1] is not terminal):
            raise ImportMemoryPublicationError('Fixed terminal admission differs')
        _require_terminal_admission(admission, cursor, 'terminal_entry')
        live = terminal.live
        issue = self._require_live_issue(live)
        saved = self._require_pair_sealed(live, issued.evidence)
        if (saved['rag'] is not terminal.rag_sealed
                or saved['episode'] is not terminal.episode_sealed):
            raise ImportMemoryPublicationError('Terminal seals differ')
        for kind, sealed, scope, service in (
                ('rag', terminal.rag_sealed, issue.plan.rag_scope, issue.rag_service),
                ('episode', terminal.episode_sealed, self.episode_scope,
                 issue.episode_service)):
            service.authority._publish(cursor, scope, issue.original_attempt,
                sealed.generation_id,
                expected_revision=sealed.expected_head.revision,
                expected_index_revision=sealed.expected_index_revision,
                snapshot_version=sealed.snapshot_version,
                expected_count=sealed.expected_count,
                content_digest=sealed.content_digest, admission=admission)
        receipts = {}
        self._terminal(cursor, issue.plan.rag_scope, issue.plan.attempt,
            terminal.prepared, issue.plan.old_rows, terminal.expected,
            receipts, admission=admission)
        for kind, sealed in (('rag', terminal.rag_sealed),
                             ('episode', terminal.episode_sealed)):
            if UUID(receipts[kind][0]) != sealed.generation_id:
                raise ImportMemoryPublicationError('Terminal receipt identity differs')
        repository = PostgresImportPublicationEvidenceRepository(self.database)
        terminal_evidence = repository.append_terminal_in_transaction(cursor, live,
            receipts['rag'], receipts['episode'],
            expected_phase='pair_sealed',
            expected_version=issued.evidence.phase_version)
        return _issue_terminal_release_binding(admission, cursor, terminal_evidence)

    def _execute_live_publication(self, live: _LivePublication):
        """One private forward-only attempt; any ambiguous external result stops."""
        issue = self._require_live_issue(live)
        with self._live_execution_lock:
            if self._live_executions.get(live):
                raise ImportMemoryPublicationError('Live publication was already attempted')
            self._live_executions[live] = True
        plan = issue.plan
        repository = PostgresImportPublicationEvidenceRepository(self.database)
        frozen = repository.read_exact(issue.key.user_id, issue.key.task_id,
                                        issue.key.task_lease_version)
        if (frozen is None or frozen.phase != 'intent'
                or frozen.intent_hash != issue.intent_hash):
            raise ImportMemoryPublicationError('Original committed reservation required')
        try:
            prepared = self.documents._write_planned_document(
                plan.document, plan.attempt, live=live)
        except Exception as error:
            from app.postgres_import_leases import ImportLeaseLost
            if isinstance(error, ImportLeaseLost):
                raise
            raise DurablePublicationUnknown(issue.key, 'document_put') from error
        self._bind_verified_document(live, prepared.verified)
        try:
            with self.database.transaction() as cursor:
                cursor.execute('begin')
                frozen = repository._read_exact_in_cursor(cursor, issue.key)
                if frozen is None or frozen.phase != 'intent':
                    raise ImportMemoryPublicationError('Document evidence phase differs')
                frozen = repository.append_document_in_transaction(cursor, live,
                    prepared.verified, expected_phase='intent',
                    expected_version=frozen.phase_version)
        except Exception as error:
            raise DurablePublicationUnknown(issue.key, 'document_evidence') from error

        expected = _Expected(deepcopy(plan.task), deepcopy(plan.document.next_history),
            prepared.history.version + 1, deepcopy(plan.next_memory),
            plan.bundle.head.snapshot_version + 1, deepcopy(prepared.record),
            deepcopy(plan.item), deepcopy(plan.event_metadata), plan.event_id,
            tuple(prepared.issued_ref), plan.new_evidence, plan.old_rag_receipt,
            plan.old_episode_receipt, prepared.old_pairing.last_generation_id,
            plan.bundle.receipt['generation_id'])
        requests = (
            ('rag', issue.rag_service, plan.rag_scope,
             prepared.old_pairing.head, prepared.points,
             expected.history_version, plan.candidate_ids[0]),
            ('episode', issue.episode_service, self.episode_scope,
             plan.bundle.head,
             [*plan.bundle.points, VectorPoint(plan.event_id, list(plan.vector),
                                               deepcopy(plan.event_metadata))],
             expected.memory_version, plan.candidate_ids[1]),
        )
        seals = {}
        for kind, vector_service, scope, head, points, version, candidate in requests:
            try:
                sealed = vector_service._prepare_sealed(scope, plan.attempt,
                    head, points, snapshot_version=version,
                    generation_id=candidate, live=live)
                with self.database.transaction() as cursor:
                    cursor.execute('begin')
                    current = repository._read_exact_in_cursor(cursor, issue.key)
                    if current is None or current.phase != frozen.phase:
                        raise ImportMemoryPublicationError('Sealed evidence phase changed')
                    frozen = repository.append_sealed_in_transaction(cursor,
                        live, kind, sealed, expected_phase=current.phase,
                        expected_version=current.phase_version)
                seals[kind] = sealed
            except Exception as error:
                from app.postgres_import_leases import ImportLeaseLost
                from app.postgres_coordination import MutationLeaseLost
                from app.vector_generation_service import _StageRejected
                if isinstance(error, _StageRejected):
                    raise error.reason from error
                if isinstance(error, (ImportLeaseLost, MutationLeaseLost)):
                    raise
                raise DurablePublicationUnknown(issue.key,
                    kind + '_candidate_or_seal') from error

        work = self._issue_fixed_terminal(live, prepared,
            seals['rag'], seals['episode'], expected)
        try:
            begun = issue.imports.try_begin_committing(issue.original_attempt,
                                                       live=live)
            if not begun:
                raise ImportMemoryPublicationError('Committing stage refused')
            issue.imports.complete(issue.original_attempt, terminal_work=work)
        except Exception as error:
            from app.import_publication_proof import ImportPublicationProofService
            try:
                proved = ImportPublicationProofService(self.database).prove_exact(
                    issue.key.user_id, issue.key.task_id,
                    issue.key.task_lease_version)
            except Exception:
                proved = None
            if proved is not None:
                return proved
            from app.postgres_import_leases import ImportLeaseLost
            if isinstance(error, ImportLeaseLost):
                raise
            raise DurablePublicationUnknown(issue.key, 'terminal_commit') from error
        from app.import_publication_proof import ImportPublicationProofService
        try:
            proved = ImportPublicationProofService(self.database).prove_exact(
                issue.key.user_id, issue.key.task_id,
                issue.key.task_lease_version)
        except Exception as error:
            raise DurablePublicationUnknown(issue.key, 'terminal_proof') from error
        if proved is None:
            raise DurablePublicationUnknown(issue.key, 'terminal_proof')
        return proved

    @staticmethod
    def _rows(cursor, user):
        return cursor.execute('''select user_id,document_id,content,metadata,created_at
            from memory_documents where user_id=%s order by document_id''', (user,)).fetchall()

    @staticmethod
    def _receipt(cursor, scope, generation_id):
        row = cursor.execute('''select state,to_jsonb(g) as receipt from vector_generations g
            where generation_id=%s and tenant_id=%s and vector_kind=%s
            and namespace=%s and index_key=%s''',
            (generation_id, *scope.key)).fetchone()
        return None if row is None else (row['state'], _receipt_projection(row['receipt']))

    def _preflight(self, rag_scope, attempt, new_rag_points, event_vector, event_profile):
        rag_scope, attempt, new_rag_points = deepcopy((rag_scope, attempt, new_rag_points))
        if (not isinstance(rag_scope, VectorScope) or rag_scope.vector_kind != 'rag'
                or rag_scope.tenant_id != attempt.task.user_id
                or self.episode_scope.tenant_id != attempt.task.user_id):
            raise ImportMemoryPublicationError('Import scopes or user differ')
        if not isinstance(event_profile, EmbeddingProfile):
            raise ImportMemoryPublicationError('Explicit episode profile required')
        try:
            validate_profile(event_profile)
        except IndexIdentityError as error:
            raise ImportMemoryPublicationError('Episode profile is invalid') from error
        if _canonical(asdict(event_profile)) != _canonical(asdict(
                self.episode_scope.identity.profile)):
            raise ImportMemoryPublicationError('Episode profile differs')
        if not isinstance(event_vector, (list, tuple)) or len(event_vector) != event_profile.dimension:
            raise ImportMemoryPublicationError('Explicit event vector dimension differs')
        vector = list(event_vector)
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in vector):
            raise ImportMemoryPublicationError('Event vector is invalid')
        if not math.isfinite(math.hypot(*vector)):
            raise ImportMemoryPublicationError('Event vector magnitude is invalid')
        with self.database.transaction() as cursor:
            cursor.execute('begin')
            task = self.documents._task_and_source(cursor, attempt)
        if rag_scope.namespace != f'pdf_{task.user_id}':
            raise ImportMemoryPublicationError('RAG namespace differs')
        event_id = 'import-' + str(uuid5(PROJECT_POINT_NAMESPACE_UUID,
                                         f'{task.user_id}:{task.task_id}'))
        history = self.documents.snapshots.read(task.user_id, 'history')
        if history is None:
            raise ImportMemoryPublicationError('History baseline is missing')
        document_evidence(task.user_id, history.data)
        if any(record.get('document_id') == task.document_id or
               record.get('import_task_id') == task.task_id
               for record in history.data['documents']):
            raise ImportMemoryPublicationError('Document or task already in History')
        bundle = self.episodes._capture_bundle(task.user_id)
        if bundle.scope != self.episode_scope or bundle.head.snapshot_version != bundle.receipt['snapshot_version']:
            raise ImportMemoryPublicationError('Episode baseline differs')
        rows = bundle.documents
        _all_memory(bundle.snapshot, rows, task.user_id, task.task_id, event_id)
        if len(bundle.points) + 1 > _MAX_POINTS:
            raise ImportMemoryPublicationError('Episode corpus exceeds bound')
        if any(point.id == event_id or point.payload.get('import_task_id') == task.task_id
               for point in bundle.points):
            raise ImportMemoryPublicationError('Prior episode point collides')
        if any(point.id == event_id for point in bundle.points):
            raise ImportMemoryPublicationError('Event ID collides')
        return rag_scope, attempt, new_rag_points, task, bundle, event_id, vector, tuple(
            sorted((row['document_id'], row['content'], row['metadata'], row['created_at'])
                   for row in rows))

    @staticmethod
    def _scope_intent(scope, head, last_id, receipt, candidate):
        return {'tenant_id': scope.tenant_id, 'vector_kind': scope.vector_kind,
                'namespace': scope.namespace, 'index_key': scope.index_key,
                'identity': deepcopy(scope.identity.to_dict()),
                'index_revision': head.index_revision,
                'head': {'state': head.state, 'revision': head.revision,
                         'generation_id': str(head.generation_id) if head.generation_id else None,
                         'last_generation_id': str(last_id),
                         'index_revision': head.index_revision,
                         'snapshot_version': head.snapshot_version},
                'old_receipt': list(receipt), 'candidate_id': str(candidate)}

    def _plan_intent(self, rag_scope, attempt, new_rag_points, *, event_vector,
                     event_profile) -> _FrozenMemoryPlan:
        (rag_scope, attempt, new_rag_points, task, bundle, event_id, vector,
         old_rows) = self._preflight(rag_scope, attempt, new_rag_points,
                                     event_vector, event_profile)
        old_rag_pairing = self.documents.witnesses.read_current(rag_scope)
        with self.database.transaction() as cursor:
            old_rag = self._receipt(cursor, rag_scope, old_rag_pairing.last_generation_id)
        if old_rag is None or old_rag[0] != 'published':
            raise ImportMemoryPublicationError('Prior RAG receipt changed')
        planned = self.documents._plan_document(rag_scope, attempt,
                                                new_rag_points, task=task)
        if planned.old_pairing != old_rag_pairing:
            raise ImportMemoryPublicationError('Prior RAG pairing changed')
        if len(planned.points) > _MAX_POINTS:
            raise ImportMemoryPublicationError('RAG corpus exceeds candidate bound')
        try:
            _validate_candidate_points(rag_scope, planned.points, _MAX_POINTS)
        except GenerationVectorStoreError as error:
            raise ImportMemoryPublicationError('RAG candidate is invalid') from error
        user = task.user_id
        timestamp = datetime.now(timezone.utc).isoformat()
        item = {'id': event_id, 'content': f'用户导入了文档：{task.original_name}',
                'memory_type': 'episodic', 'importance': 0.8,
                'timestamp': timestamp,
                'metadata': {'user_id': user, 'import_task_id': task.task_id,
                             'document_id': task.document_id,
                             'document_name': task.original_name,
                             'document_path': planned.record['document_path'],
                             'file_suffix': task.file_suffix, 'session_id': 'import'}}
        payload = dict(item['metadata']) | {
            'memory_id': event_id, 'episode_id': event_id, 'timestamp': timestamp,
            'memory_type': 'episodic', 'importance': 0.8,
            'content': item['content'], 'session_id': 'import'}
        _canonical(payload)
        next_memory = deepcopy(bundle.snapshot)
        next_memory['memories'].append(deepcopy(item))
        next_history = deepcopy(planned.next_history)
        old_episode = _receipt_projection(bundle.publication_receipt)
        new_evidence = document_evidence(user, next_history)
        candidates = (uuid4(), uuid4())
        if candidates[0] == candidates[1]:
            raise ImportMemoryPublicationError('Candidate identities collided')
        raw_rows = [list(row) for row in old_rows]
        intent = {
            'schema_version': 1,
            'attempt': {'user_id': user, 'task_id': task.task_id,
                        'task_lease_version': attempt.lease_version,
                        'worker_id': attempt.worker_id,
                        'task_lease_token': str(attempt.lease_token),
                        'user_lease_token': str(attempt.user_lease.lease_token),
                        'user_lease_version': attempt.user_lease.lease_version,
                        'document_id': task.document_id},
            'task': {key: getattr(task, key) for key in (
                'task_id', 'batch_id', 'user_id', 'document_id',
                'original_name', 'file_suffix', 'size_bytes', 'created_at')},
            'source': {'bucket': task.bucket, 'key': task.source.key,
                       'version_id': task.source.version_id,
                       'sha256': task.source.sha256,
                       'size_bytes': task.source.size_bytes},
            'scopes': {
                'rag': self._scope_intent(rag_scope, planned.old_pairing.head,
                    planned.old_pairing.last_generation_id, old_rag[1], candidates[0]),
                'episode': self._scope_intent(self.episode_scope, bundle.head,
                    bundle.receipt['generation_id'], old_episode, candidates[1])},
            'snapshots': {
                'old_history': {'version': planned.history.version,
                                'data': deepcopy(planned.history.data)},
                'next_history': {'version': planned.history.version + 1,
                                 'data': deepcopy(next_history)},
                'old_memory': {'version': bundle.head.snapshot_version,
                               'data': deepcopy(bundle.snapshot)},
                'next_memory': {'version': bundle.head.snapshot_version + 1,
                                'data': deepcopy(next_memory)}},
            'memory_rows': raw_rows,
            'event': {'event_id': event_id, 'timestamp': timestamp,
                      'item': deepcopy(item), 'metadata': deepcopy(payload)},
            'document': {'key': planned.object_key, 'record': deepcopy(planned.record),
                         'count': new_evidence.count, 'digest': new_evidence.digest,
                         'baseline_row_digest': sha256(_canonical(raw_rows).encode('utf-8')).hexdigest(),
                         'fixed_refs': [list(ref) for ref in planned.fixed_refs],
                         'old_witness': ({'head_revision': old_rag_pairing.head.revision,
                             'last_generation_id': str(old_rag_pairing.last_generation_id),
                             'index_revision': old_rag_pairing.receipt_index_revision,
                             'publication_snapshot_version': old_rag_pairing.receipt_snapshot_version,
                             'document_count': old_rag_pairing.witness_count,
                             'documents_sha256': old_rag_pairing.witness_digest}
                             if old_rag_pairing.has_witness else None)},
        }
        encode_intent(intent)  # Refuse complete oversized intents before any publication write.
        return _FrozenMemoryPlan(rag_scope, attempt, task, bundle, event_id,
            tuple(vector), old_rows, planned, old_rag_pairing, old_rag[1],
            old_episode, deepcopy(item), deepcopy(payload), next_memory,
            new_evidence, candidates, deepcopy(intent))

    def publish(self, rag_scope, attempt, new_rag_points, *, event_vector,
                event_profile) -> ImportMemoryPublication:
        with self.database.transaction() as cursor:
            require_no_gate_in_transaction(cursor, attempt.task.user_id)
        plan = self._plan_intent(rag_scope, attempt, new_rag_points,
                                 event_vector=event_vector, event_profile=event_profile)
        rag_scope, attempt, task, bundle = (plan.rag_scope, plan.attempt,
                                            plan.task, plan.bundle)
        event_id, vector, old_rows = plan.event_id, plan.vector, plan.old_rows
        prepared = self.documents._write_planned_document(plan.document, attempt)
        if prepared.old_pairing != plan.old_rag_pairing:
            raise ImportMemoryPublicationError('Prior RAG pairing changed')
        expected = _Expected(deepcopy(task), deepcopy(plan.document.next_history), prepared.history.version + 1,
                             deepcopy(plan.next_memory), bundle.head.snapshot_version + 1,
                             deepcopy(prepared.record), deepcopy(plan.item), deepcopy(plan.event_metadata),
                             event_id, tuple(prepared.issued_ref), plan.new_evidence,
                             plan.old_rag_receipt, plan.old_episode_receipt,
                             prepared.old_pairing.last_generation_id,
                             bundle.receipt['generation_id'])
        # The callback owns independent copies and the original issued capability.
        callback_expected = deepcopy(expected)
        callback_prepared = prepared
        new_receipts = {}
        def domain_work(cursor):
            self._terminal(cursor, rag_scope, attempt, callback_prepared,
                           old_rows, callback_expected, new_receipts)
        rag_plan = PairScopePlan(self.pair.rag, rag_scope, prepared.old_pairing.head,
                                 prepared.points, expected.history_version)
        episode_plan = PairScopePlan(self.pair.episode, self.episode_scope, bundle.head,
                                     [*bundle.points, VectorPoint(event_id, list(vector),
                                                                   plan.event_metadata)],
                                     expected.memory_version)
        try:
            pair = self.pair.publish_prepared_pair(rag_plan, episode_plan, attempt,
                _domain_work=domain_work,
                _domain_context={'task_id': task.task_id, 'event_id': event_id,
                                 'history_version': expected.history_version,
                                 'memory_version': expected.memory_version,
                                 'ref': expected.ref})
        except ImportPairUnknown as error:
            frozen = replace(expected, new_receipts=tuple(new_receipts[k]
                for k in ('rag', 'episode')) if len(new_receipts) == 2 else None)
            found = self._reconcile(error.context, frozen)
            if found is not None:
                return found
            raise ImportMemoryPublicationUnknown(error.context, frozen,
                                                  error.phase, error) from error
        frozen = replace(expected, new_receipts=tuple(new_receipts[k]
            for k in ('rag', 'episode')) if len(new_receipts) == 2 else None)
        found = self._reconcile(pair._context, frozen)
        if found is None:
            raise ImportMemoryPublicationUnknown(pair._context, frozen, 'domain reconciliation')
        return found

    def _terminal(self, cursor, rag_scope, attempt, prepared, old_rows,
                  expected, new_receipts, *, admission=None):
        admission_args = {'admission': admission} if admission is not None else {}
        user = expected.task.user_id
        if self.documents._task_and_source(cursor, attempt, admission=admission) != expected.task:
            raise ImportMemoryPublicationError('Import task or source changed')
        old_history = self.documents.snapshots._read(cursor, user, 'history')
        old_memory = self.documents.snapshots._read(cursor, user, 'memory')
        if (old_history is None or old_history.version + 1 != expected.history_version
                or _canonical(old_history.data) != _canonical(prepared.history.data)
                or old_memory is None or old_memory.version + 1 != expected.memory_version
                or _canonical(old_memory.data) != _canonical({
                    **expected.memory, 'memories': expected.memory['memories'][:-1]})):
            raise ImportMemoryPublicationError('History or Memory changed')
        current_rows = self._rows(cursor, user)
        current_raw = _all_memory(old_memory.data, current_rows, user,
                                  expected.task.task_id, expected.event_id)
        if current_raw != old_rows:
            raise ImportMemoryPublicationError('Memory document rows changed')
        for scope, generation_id, projection in (
                (rag_scope, expected.old_rag_id, expected.old_rag_receipt),
                (self.episode_scope, expected.old_episode_id, expected.old_episode_receipt)):
            receipt = self._receipt(cursor, scope, generation_id)
            if receipt != ('retired', projection):
                raise ImportMemoryPublicationError('Prior publication receipt changed')
        for scope, version in ((rag_scope, expected.history_version),
                               (self.episode_scope, expected.memory_version)):
            head = cursor.execute('''select revision,last_generation_id,index_revision,
                snapshot_version from vector_heads where tenant_id=%s and vector_kind=%s
                and namespace=%s and index_key=%s''', scope.key).fetchone()
            if head is None or head['snapshot_version'] != version or head['last_generation_id'] is None:
                raise ImportMemoryPublicationError('New publication head differs')
            receipt = self._receipt(cursor, scope, head['last_generation_id'])
            if (receipt is None or receipt[0] != 'published'
                    or receipt[1][_RECEIPT_FIELDS.index('publication_revision')] != head['revision']
                    or receipt[1][_RECEIPT_FIELDS.index('index_revision')] != head['index_revision']):
                raise ImportMemoryPublicationError('New publication receipt differs')
        self.documents._publish_document_domain(cursor, prepared, rag_scope,
                                                attempt, **admission_args)
        updated = self.documents.snapshots.compare_and_swap_in_transaction(
            cursor, user, 'memory', deepcopy(expected.memory),
            expected_version=expected.memory_version - 1, **admission_args)
        if updated.version != expected.memory_version:
            raise ImportMemoryPublicationError('Memory CAS version differs')
        PostgresMemoryDocumentStore(self.database, user).add_document_in_transaction(
            cursor, expected.event_id, expected.item['content'],
            json.dumps(expected.event_metadata, ensure_ascii=False,
                       sort_keys=True, separators=(',', ':'), allow_nan=False),
            **admission_args)
        if self.documents._task_and_source(cursor, attempt, admission=admission) != expected.task:
            raise ImportMemoryPublicationError('Import task or source changed')
        if (_canonical(self.documents.snapshots._read(cursor, user, 'history').data)
                != _canonical(expected.history)
                or _canonical(self.documents.snapshots._read(cursor, user, 'memory').data)
                != _canonical(expected.memory)):
            raise ImportMemoryPublicationError('Published snapshots differ')
        _all_memory(expected.memory, self._rows(cursor, user), user,
                    expected.task.task_id, expected.event_id,
                    expected_item=expected.item,
                    expected_metadata=expected.event_metadata)
        ref = cursor.execute('''select bucket,object_key,version_id,sha256,size_bytes,
            history_record_sha256 from document_objects where user_id=%s and document_id=%s''',
            (user, expected.task.document_id)).fetchone()
        if (ref is None or (user, expected.task.document_id, ref['bucket'],
                ref['object_key'], ref['version_id'], ref['sha256'],
                ref['size_bytes']) != expected.ref or ref['history_record_sha256'] !=
                PostgresDocumentObjectRepository._record_hash(expected.record)):
            raise ImportMemoryPublicationError('New pinned document differs')
        witness = cursor.execute('''select last_generation_id,index_revision,
            publication_snapshot_version,document_count,documents_sha256
            from history_document_witnesses where tenant_id=%s and vector_kind=%s
            and namespace=%s and index_key=%s and head_revision=%s''',
            (*rag_scope.key, prepared.old_pairing.head.revision + 1)).fetchone()
        if (witness is None or witness['publication_snapshot_version'] != expected.history_version
                or witness['document_count'] != expected.new_evidence.count
                or witness['documents_sha256'] != expected.new_evidence.digest):
            raise ImportMemoryPublicationError('New History witness differs')
        self.documents._check_fences(cursor, user,
            set(prepared.original_ids) | {expected.task.document_id})
        for key, scope in (('rag', rag_scope), ('episode', self.episode_scope)):
            head = cursor.execute('''select last_generation_id from vector_heads
                where tenant_id=%s and vector_kind=%s and namespace=%s and index_key=%s''',
                scope.key).fetchone()
            receipt = self._receipt(cursor, scope, head['last_generation_id']) if head else None
            if receipt is None or receipt[0] != 'published':
                raise ImportMemoryPublicationError('New receipt differs')
            new_receipts[key] = receipt[1]

    def _reconcile(self, context, expected):
        try:
            with self.database.transaction() as cursor:
                cursor.execute('set transaction isolation level repeatable read read only')
                rows = self.pair._read_pair_evidence_in_cursor(cursor, context)
                pair = self.pair._validate_pair_evidence(context, rows)
                if pair is None:
                    return None
                if expected.new_receipts is None:
                    return None
                fields = ('task_id', 'batch_id', 'user_id', 'document_id',
                          'original_name', 'file_suffix', 'size_bytes', 'created_at')
                if any(getattr(pair.import_task, key) != getattr(expected.task, key)
                       for key in fields):
                    return None
                for scope, publication, frozen in zip(
                        (context.rag.scope, context.episode.scope),
                        (pair.rag, pair.episode), expected.new_receipts):
                    current = self._receipt(cursor, scope, publication.generation_id)
                    if current is None or current[0] not in ('published', 'retired') or current[1] != frozen:
                        return None
                user = expected.task.user_id
                history = self.documents.snapshots._read(cursor, user, 'history')
                memory = self.documents.snapshots._read(cursor, user, 'memory')
                if (history is None or history.version < expected.history_version
                        or memory is None or memory.version < expected.memory_version):
                    return None
                document_evidence(user, history.data)
                history_matches = [record for record in history.data['documents']
                    if record.get('document_id') == expected.task.document_id
                    or record.get('import_task_id') == expected.task.task_id]
                if len(history_matches) != 1 or _canonical(history_matches[0]) != _canonical(expected.record):
                    return None
                _all_memory(memory.data, self._rows(cursor, user), user,
                    expected.task.task_id, expected.event_id,
                    expected_item=expected.item,
                    expected_metadata=expected.event_metadata)
                pinned = cursor.execute('''select bucket,object_key,version_id,sha256,
                    size_bytes,history_record_sha256 from document_objects
                    where user_id=%s and document_id=%s''',
                    (user, expected.task.document_id)).fetchone()
                ref = expected.ref
                if (pinned is None or (user, expected.task.document_id,
                        pinned['bucket'], pinned['object_key'], pinned['version_id'],
                        pinned['sha256'], pinned['size_bytes']) != ref
                        or pinned['history_record_sha256'] !=
                        PostgresDocumentObjectRepository._record_hash(expected.record)):
                    return None
                witness = cursor.execute('''select last_generation_id,index_revision,
                    publication_snapshot_version,document_count,documents_sha256
                    from history_document_witnesses where tenant_id=%s and vector_kind=%s
                    and namespace=%s and index_key=%s and head_revision=%s''',
                    (*context.rag.scope.key, pair.rag.head.revision)).fetchone()
                if (witness is None or witness['last_generation_id'] != pair.rag.generation_id
                        or witness['index_revision'] != pair.rag.head.index_revision
                        or witness['publication_snapshot_version'] != expected.history_version
                        or witness['document_count'] != expected.new_evidence.count
                        or witness['documents_sha256'] != expected.new_evidence.digest):
                    return None
                for scope, generation_id, projection in (
                        (context.rag.scope, expected.old_rag_id, expected.old_rag_receipt),
                        (context.episode.scope, expected.old_episode_id,
                         expected.old_episode_receipt)):
                    old = self._receipt(cursor, scope, generation_id)
                    if old != ('retired', projection):
                        return None
                for selected, publication in zip(rows, (pair.rag, pair.episode)):
                    if (selected['generation_id'] != publication.generation_id
                            or selected['state'] not in ('published', 'retired')):
                        return None
                if (self.documents._check_fences(cursor, user,
                        {expected.task.document_id}) is not None):
                    return None
                return ImportMemoryPublication(pair, expected.event_id,
                    expected.history_version, expected.memory_version, ref,
                    (pair.rag.head.revision, pair.rag.generation_id,
                     expected.new_evidence.count, expected.new_evidence.digest),
                    context, deepcopy(expected))
        except Exception:
            return None

    def reconcile(self, unknown: ImportMemoryPublicationUnknown):
        if not isinstance(unknown, ImportMemoryPublicationUnknown):
            raise TypeError('C unknown result required')
        return self._reconcile(unknown.context, unknown._frozen_expected)
