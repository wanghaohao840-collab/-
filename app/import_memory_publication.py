"""Opt-in atomic RAG, episode, History and Memory import publication."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import json
import math
from uuid import UUID, uuid5

from app.import_document_publication import (
    ImportDocumentPublicationError, ImportDocumentPublicationService,
)
from app.import_vector_publication import (
    ImportPairUnknown, ImportVectorPairPublicationService, PairScopePlan,
)
from app.postgres_document_objects import PostgresDocumentObjectRepository
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

    def publish(self, rag_scope, attempt, new_rag_points, *, event_vector,
                event_profile) -> ImportMemoryPublication:
        (rag_scope, attempt, new_rag_points, task, bundle, event_id, vector,
         old_rows) = self._preflight(rag_scope, attempt, new_rag_points,
                                     event_vector, event_profile)
        old_rag_pairing = self.documents.witnesses.read_current(rag_scope)
        with self.database.transaction() as cursor:
            old_rag = self._receipt(cursor, rag_scope, old_rag_pairing.last_generation_id)
        if old_rag is None or old_rag[0] != 'published':
            raise ImportMemoryPublicationError('Prior RAG receipt changed')
        prepared = self.documents._prepare_document(rag_scope, attempt,
                                                    new_rag_points, task=task)
        if prepared.old_pairing != old_rag_pairing:
            raise ImportMemoryPublicationError('Prior RAG pairing changed')
        user = task.user_id
        timestamp = datetime.now(timezone.utc).isoformat()
        item = {'id': event_id, 'content': f'用户导入了文档：{task.original_name}',
                'memory_type': 'episodic', 'importance': 0.8,
                'timestamp': timestamp,
                'metadata': {'user_id': user, 'import_task_id': task.task_id,
                             'document_id': task.document_id,
                             'document_name': task.original_name,
                             'document_path': prepared.record['document_path'],
                             'file_suffix': task.file_suffix, 'session_id': 'import'}}
        payload = dict(item['metadata']) | {
            'memory_id': event_id, 'episode_id': event_id, 'timestamp': timestamp,
            'memory_type': 'episodic', 'importance': 0.8,
            'content': item['content'], 'session_id': 'import'}
        _canonical(payload)
        next_memory = deepcopy(bundle.snapshot)
        next_memory['memories'].append(deepcopy(item))
        next_history = deepcopy(prepared.next_history)
        old_episode = _receipt_projection(bundle.publication_receipt)
        new_evidence = document_evidence(user, next_history)
        expected = _Expected(deepcopy(task), deepcopy(next_history), prepared.history.version + 1,
                             deepcopy(next_memory), bundle.head.snapshot_version + 1,
                             deepcopy(prepared.record), deepcopy(item), deepcopy(payload),
                             event_id, tuple(prepared.issued_ref), new_evidence,
                             old_rag[1], old_episode, prepared.old_pairing.last_generation_id,
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
                                     [*bundle.points, VectorPoint(event_id, vector, payload)],
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
                  expected, new_receipts):
        user = expected.task.user_id
        if self.documents._task_and_source(cursor, attempt) != expected.task:
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
        self.documents._publish_document_domain(cursor, prepared, rag_scope, attempt)
        updated = self.documents.snapshots.compare_and_swap_in_transaction(
            cursor, user, 'memory', deepcopy(expected.memory),
            expected_version=expected.memory_version - 1)
        if updated.version != expected.memory_version:
            raise ImportMemoryPublicationError('Memory CAS version differs')
        PostgresMemoryDocumentStore(self.database, user).add_document_in_transaction(
            cursor, expected.event_id, expected.item['content'],
            json.dumps(expected.event_metadata, ensure_ascii=False,
                       sort_keys=True, separators=(',', ':'), allow_nan=False))
        if self.documents._task_and_source(cursor, attempt) != expected.task:
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
