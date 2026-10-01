"""Opt-in publication of one new imported document; no Worker or runtime wiring.

``document_path`` is an opaque object locator ending in the original suffix.
It is never a local durable path.  The exact S3 version is held exclusively in
``document_objects`` and must be read through that repository.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from uuid import UUID

from app.import_repository import _task_from_row
from app.object_store import ObjectRef, S3ObjectStore, artifact_key
from app.postgres_document_objects import PostgresDocumentObjectRepository
from app.postgres_history_document_witnesses import (
    HistoryDocumentWitnessError, PostgresHistoryDocumentWitnessRepository,
    document_evidence,
)
from app.postgres_import_artifacts import PostgresImportArtifactService
from app.postgres_import_leases import ImportAttempt, PostgresImportLeaseRepository
from app.postgres_snapshots import PostgresSnapshotRepository, SnapshotConflict
from app.postgres_vector_generations import VectorScope
from app.vector_generation_service import VectorGenerationService, VectorPublication
from hello_agents.memory.storage.generation_vector_store import GenerationVectorStore
from hello_agents.memory.storage.vector_store import VectorPoint


class ImportDocumentPublicationError(RuntimeError):
    """An import cannot safely add its document to the shared authorities."""


@dataclass(frozen=True)
class _PreparedTask:
    task_id: str
    batch_id: str
    user_id: str
    document_id: str
    original_name: str
    file_suffix: str
    size_bytes: int
    created_at: str
    source: ObjectRef
    bucket: str


@dataclass(frozen=True)
class _PreparedDocument:
    task: _PreparedTask
    history: object
    old_pairing: object
    old_evidence: object
    fixed_refs: tuple
    verified: object
    issued_ref: tuple
    record: dict
    next_history: dict
    points: tuple[VectorPoint, ...]
    original_ids: frozenset[str]


class ImportDocumentPublicationService:
    """Append only one server-assigned document to an already paired corpus.

    The supplied points are already parsed and embedded by a future Worker.
    This service never parses files, embeds text, or changes runtime selection.
    """

    def __init__(self, database, store: S3ObjectStore,
                 vectors: VectorGenerationService):
        if vectors.authority.database is not database:
            raise ValueError('Vector and document authorities require one database')
        self.database = database
        self.store = store
        self.vectors = vectors
        self.imports = PostgresImportLeaseRepository(database)
        self.sources = PostgresImportArtifactService(database, store)
        self.snapshots = PostgresSnapshotRepository(database)
        self.documents = PostgresDocumentObjectRepository(database, store)
        self.witnesses = PostgresHistoryDocumentWitnessRepository(database, store)

    @staticmethod
    def _canonical_uuid(value: str) -> bool:
        try:
            return isinstance(value, str) and str(UUID(value)) == value
        except (TypeError, ValueError, AttributeError):
            return False

    def _task_and_source(self, cursor, attempt: ImportAttempt) -> _PreparedTask:
        row = self.imports._live(cursor, attempt)
        task = _task_from_row(row)
        fields = ('task_id', 'batch_id', 'user_id', 'document_id',
                  'original_name', 'file_suffix', 'size_bytes', 'created_at')
        if any(getattr(task, field) != getattr(attempt.task, field) for field in fields):
            raise ImportDocumentPublicationError('Attempt task differs from durable task')
        if not self._canonical_uuid(task.document_id):
            raise ImportDocumentPublicationError('Document ID is not server-assigned canonical UUID')
        source = cursor.execute('''select bucket,object_key,version_id,sha256,size_bytes
            from import_objects where task_id=%s and user_id=%s''',
            (task.task_id, task.user_id)).fetchone()
        if source is None:
            raise ImportDocumentPublicationError('Pinned import source is missing')
        ref = ObjectRef(source['object_key'], source['sha256'],
                        source['size_bytes'], source['version_id'])
        if (source['bucket'] != self.store.bucket or source['bucket'] != attempt.bucket
                or ref != attempt.source or ref.size_bytes != task.size_bytes):
            raise ImportDocumentPublicationError('Pinned import source changed')
        if artifact_key(task.user_id, 'imports', task.task_id,
                        task.file_suffix, ref.sha256) != ref.key:
            raise ImportDocumentPublicationError('Import source identity differs from task')
        return _PreparedTask(task.task_id, task.batch_id, task.user_id, task.document_id,
                             task.original_name, task.file_suffix, task.size_bytes,
                             task.created_at, ref, source['bucket'])

    @staticmethod
    def _check_fences(cursor, user_id: str, document_ids: set[str]) -> None:
        if not document_ids:
            return
        fenced = cursor.execute('''select target_id from qa_deletion_fences
            where user_id=%s and target_type='document' and target_id=any(%s)
            and (status in ('queued','running') or
                (status='failed' and attempt_count<3)) limit 1''',
            (user_id, list(document_ids))).fetchone()
        if fenced:
            raise ImportDocumentPublicationError('Document deletion fence is active')

    @staticmethod
    def _point_document(point: VectorPoint) -> str:
        value = point.payload.get('document_id') if isinstance(point.payload, dict) else None
        if not isinstance(value, str):
            raise ImportDocumentPublicationError('Vector point has no document identity')
        return value

    @staticmethod
    def _scope_point(point: VectorPoint, scope: VectorScope, document_id: str) -> bool:
        payload = point.payload
        metadata = payload.get('metadata') if isinstance(payload, dict) else None
        return (isinstance(metadata, dict)
                and ImportDocumentPublicationService._point_document(point) == document_id
                and payload.get('rag_namespace') == scope.namespace
                and metadata.get('embedding_fingerprint') == scope.identity.profile.fingerprint
                and all(payload.get(key, expected) == expected for key, expected in (
                    ('user_id', scope.tenant_id), ('tenant_id', scope.tenant_id),
                    ('namespace', scope.namespace), ('vector_kind', scope.vector_kind),
                    ('embedding_fingerprint', scope.identity.profile.fingerprint)))
                and all(metadata.get(key, expected) == expected for key, expected in (
                    ('document_id', document_id), ('user_id', scope.tenant_id),
                    ('tenant_id', scope.tenant_id), ('namespace', scope.namespace),
                    ('rag_namespace', scope.namespace), ('vector_kind', scope.vector_kind))))

    @staticmethod
    def _new_points(points, scope: VectorScope, document_id: str) -> list[VectorPoint]:
        if not isinstance(points, (list, tuple)) or not points:
            raise ImportDocumentPublicationError('New document requires prepared vector points')
        prepared = []
        ids = set()
        for item in points:
            if not isinstance(item, VectorPoint):
                raise ImportDocumentPublicationError('Prepared vector point is invalid')
            point = VectorPoint(item.id, list(item.vector), deepcopy(item.payload))
            if point.id in ids or not ImportDocumentPublicationService._scope_point(
                    point, scope, document_id):
                raise ImportDocumentPublicationError('New point scope or identity differs')
            ids.add(point.id)
            prepared.append(point)
        return prepared

    def publish(self, scope: VectorScope, attempt: ImportAttempt,
                new_points: list[VectorPoint] | tuple[VectorPoint, ...]) -> VectorPublication:
        # Frozen dataclasses still expose mutable dictionaries and nested lists.
        # Own every caller value before the first potentially blocking I/O.
        scope, attempt, new_points = deepcopy((scope, attempt, new_points))
        if (not isinstance(scope, VectorScope) or scope.vector_kind != 'rag'
                or not isinstance(attempt, ImportAttempt)
                or scope.tenant_id != attempt.task.user_id):
            raise ImportDocumentPublicationError('Import requires a matching RAG tenant')
        prepared = self._prepare_document(scope, attempt, new_points)
        return self.vectors.publish_complete(
            scope, attempt, prepared.old_pairing.head, prepared.points,
            domain_publish=lambda cursor: self._publish_document_domain(
                cursor, prepared, scope, attempt),
            snapshot_version=prepared.history.version + 1)

    def _prepare_document(self, scope, attempt, new_points, *, task=None):
        with self.database.transaction() as cursor:
            cursor.execute('begin')
            prepared_task = self._task_and_source(cursor, attempt)
        if task is not None and prepared_task != task:
            raise ImportDocumentPublicationError('Import task or source changed')
        if scope.namespace != f'pdf_{prepared_task.user_id}':
            raise ImportDocumentPublicationError('RAG namespace differs from user document scope')
        points = self._new_points(new_points, scope, prepared_task.document_id)

        # The repository re-selects the accepted import_objects reference and
        # reads its exact version. The comparison also rejects source mutation.
        content = self.sources.read_source_bytes(prepared_task.user_id,
                                                 prepared_task.task_id)
        import hashlib
        if (len(content) != prepared_task.source.size_bytes
                or hashlib.sha256(content).hexdigest() != prepared_task.source.sha256):
            raise ImportDocumentPublicationError('Accepted source bytes changed')
        object_key = artifact_key(prepared_task.user_id, 'documents',
                                  prepared_task.document_id, prepared_task.file_suffix,
                                  prepared_task.source.sha256)
        object_ref = self.store.put_immutable(prepared_task.user_id, object_key, content).ref
        verified = self.documents.verify_for_publication(prepared_task.user_id,
                                                         prepared_task.document_id, object_ref)

        history = self.snapshots.read(prepared_task.user_id, 'history')
        if history is None:
            raise ImportDocumentPublicationError('History and vector head are not paired')
        try:
            old_pairing = self.witnesses.read_current(scope)
            old_evidence = document_evidence(prepared_task.user_id, history.data)
            self.witnesses.check_document_pairing(old_pairing, old_evidence, history.version)
            fixed_refs = self.witnesses.verify_retained_references(scope, old_evidence)
        except HistoryDocumentWitnessError as exc:
            raise ImportDocumentPublicationError(str(exc)) from exc
        head = old_pairing.head
        original_ids = set(old_evidence.ids)
        if (prepared_task.document_id in original_ids or any(
                item.get('import_task_id') == prepared_task.task_id
                for item in history.data['documents'])):
            raise ImportDocumentPublicationError('Document already exists in History')
        existing: list[VectorPoint] = []
        if head.state == 'published':
            manifest = self.vectors.authority.reconcile(scope, head.generation_id)
            if not manifest or manifest['state'] != 'published' or not manifest['is_head']:
                raise ImportDocumentPublicationError('Published corpus manifest changed')
            view = GenerationVectorStore(self.vectors.raw, scope, head)
            existing = view.scroll(scope.identity.physical_collection, with_vectors=True,
                expected_manifest=(manifest['expected_count'], manifest['content_digest']))
        # This bounded seam refuses zero-chunk historical documents. Without
        # them, a missing whole document could pass a point-count check.
        if {self._point_document(point) for point in existing} != original_ids:
            raise ImportDocumentPublicationError('Prior corpus differs from History')
        if any(not self._scope_point(point, scope, self._point_document(point))
               for point in existing):
            raise ImportDocumentPublicationError('Prior vector scope differs')
        if set(point.id for point in existing).intersection(point.id for point in points):
            raise ImportDocumentPublicationError('New vector logical ID collides with prior corpus')

        record = {
            'user_id': prepared_task.user_id,
            'document_id': prepared_task.document_id,
            'document_name': prepared_task.original_name,
            'file_suffix': prepared_task.file_suffix,
            'document_path': f'object://{self.store.bucket}/{object_ref.key}',
            'loaded_at': prepared_task.created_at,
            'import_task_id': prepared_task.task_id,
        }
        next_history = deepcopy(history.data)
        next_history['documents'].append(deepcopy(record))
        return _PreparedDocument(prepared_task, history, old_pairing, old_evidence,
                                 fixed_refs, verified,
                                 self.documents._issuance_values(verified),
                                 deepcopy(record), next_history,
                                 tuple(deepcopy([*existing, *points])),
                                 frozenset(original_ids))

    def _publish_document_domain(self, cursor, prepared, scope, attempt):
        prepared_task = prepared.task
        history = prepared.history
        old_pairing = prepared.old_pairing
        old_evidence = prepared.old_evidence
        fixed_refs = prepared.fixed_refs
        if self._task_and_source(cursor, attempt) != prepared_task:
            raise ImportDocumentPublicationError('Import task or source changed before commit')
        current = self.snapshots._read(cursor, prepared_task.user_id, 'history')
        if (current is None or current.version != history.version
                or self._canonical_json(current.data) != self._canonical_json(history.data)):
            raise SnapshotConflict('History version changed')
        try:
            current_evidence = document_evidence(prepared_task.user_id, current.data)
            if current_evidence != old_evidence:
                raise HistoryDocumentWitnessError('History documents changed during import')
            # _publish has retired the captured generation in this same
            # transaction. Its immutable receipt remains the old authority.
            self.witnesses.check_old_receipt(cursor, scope, old_pairing)
            self.witnesses.check_retained_references(cursor, scope, fixed_refs)
            if not old_pairing.has_witness:
                self.witnesses.insert(cursor, scope, old_pairing, old_evidence)
        except HistoryDocumentWitnessError as exc:
            raise ImportDocumentPublicationError(str(exc)) from exc
        if any(item.get('import_task_id') == prepared_task.task_id or
               item.get('document_id') == prepared_task.document_id
               for item in current.data['documents']):
            raise ImportDocumentPublicationError('Document or task already exists in History')
        self._check_fences(cursor, prepared_task.user_id,
                           set(prepared.original_ids) | {prepared_task.document_id})
        changed = self.snapshots.compare_and_swap_in_transaction(
            cursor, prepared_task.user_id, 'history', deepcopy(prepared.next_history),
            expected_version=history.version)
        if changed.version != history.version + 1:
            raise SnapshotConflict('History version changed')
        self.documents.publish_in_transaction(cursor, prepared.verified)
        try:
            new_pairing = self.witnesses.read_current(scope, cursor=cursor)
            if new_pairing.has_witness or new_pairing.head.snapshot_version != history.version + 1:
                raise HistoryDocumentWitnessError('New RAG publication receipt differs from History')
            self.witnesses.insert(cursor, scope, new_pairing,
                                  document_evidence(prepared_task.user_id, prepared.next_history))
        except HistoryDocumentWitnessError as exc:
            raise ImportDocumentPublicationError(str(exc)) from exc

    @staticmethod
    def _canonical_json(value):
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, allow_nan=False)
