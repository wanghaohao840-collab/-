"""Internal, bounded publication of one RAG and one episode generation.

This layer proves only the immutable vector pair and exact import task. Domain
History/Memory evidence belongs to the later trusted import composition.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
import re
from typing import Callable, Sequence
from uuid import UUID, uuid4

from app.import_repository import _task_from_row
from app.postgres_import_leases import ImportAttempt
from app.postgres_vector_generations import VectorHead, VectorScope
from app.vector_generation_service import (
    VectorGenerationService, VectorPublication, VectorPublicationUnknown,
    _SealedGeneration, _StageRejected, _freeze_request, _owner_key,
)
from hello_agents.memory.storage.vector_store import VectorPoint


@dataclass(frozen=True)
class PairScopePlan:
    service: VectorGenerationService
    scope: VectorScope
    expected_head: VectorHead
    complete_corpus: Sequence[VectorPoint]
    snapshot_version: int | None = None


@dataclass(frozen=True)
class _FrozenScopePlan:
    service: VectorGenerationService
    scope: VectorScope
    expected_head: VectorHead
    complete_corpus: tuple[VectorPoint, ...]
    snapshot_version: int | None
    generation_id: UUID
    sealed: _SealedGeneration | None = None


@dataclass(frozen=True)
class _PairContext:
    attempt: ImportAttempt
    rag: _FrozenScopePlan
    episode: _FrozenScopePlan
    domain_context: object | None = None

    @property
    def generation_ids(self):
        return (self.rag.generation_id, self.episode.generation_id)

    @property
    def scopes(self):
        return (self.rag.scope, self.episode.scope)

    @property
    def attempt_key(self):
        return _owner_key(self.attempt)


@dataclass(frozen=True)
class PairPublication:
    rag: VectorPublication
    episode: VectorPublication
    import_task: object
    _context: _PairContext = field(repr=False, compare=False)


class ImportPairUnknown(RuntimeError):
    """Unknown publication/cleanup; both IDs and frozen context are retained."""

    def __init__(self, context: _PairContext, phase: str, candidate_states=None):
        self.context = context
        self.generation_ids = context.generation_ids
        self.scopes = context.scopes
        self.attempt_key = context.attempt_key
        self.phase = phase
        self.candidate_states = candidate_states or {}
        super().__init__(f'Import pair {self.generation_ids} has unknown {phase} outcome')


class ImportPairRejected(RuntimeError):
    """Preparation failed and every invoked candidate was durably abandoned."""

    def __init__(self, context: _PairContext):
        self.context = context
        self.generation_ids = context.generation_ids
        super().__init__(f'Import pair {self.generation_ids} was rejected')


class ImportVectorPairPublicationService:
    def __init__(self, rag_service: VectorGenerationService,
                 episode_service: VectorGenerationService):
        if not isinstance(rag_service, VectorGenerationService) or not isinstance(
                episode_service, VectorGenerationService):
            raise TypeError('Two vector generation services are required')
        if rag_service.authority.database is not episode_service.authority.database:
            raise ValueError('Both vector authorities must use the same database object')
        self.rag = rag_service
        self.episode = episode_service
        self.database = rag_service.authority.database
        self.imports = rag_service.authority.imports

    @staticmethod
    def _freeze_plan(plan, service, attempt):
        if not isinstance(plan, PairScopePlan) or plan.service is not service:
            raise TypeError('A plan for the matching service is required')
        scope, owner, head, corpus = _freeze_request(
            plan.scope, attempt, plan.expected_head, plan.complete_corpus,
            plan.snapshot_version)
        if owner != attempt:
            raise ValueError('Import attempt changed while freezing')
        return _FrozenScopePlan(service, scope, head, corpus,
                                plan.snapshot_version, uuid4())

    def _plan(self, rag_plan, episode_plan, attempt, domain_context):
        if not isinstance(attempt, ImportAttempt):
            raise TypeError('Exact import attempt is required')
        frozen_attempt = deepcopy(attempt)
        if (frozen_attempt.user_lease.user_id != frozen_attempt.task.user_id
                or frozen_attempt.user_lease.owner != frozen_attempt.worker_id):
            raise ValueError('Malformed import attempt')
        rag = self._freeze_plan(rag_plan, self.rag, frozen_attempt)
        episode = self._freeze_plan(episode_plan, self.episode, frozen_attempt)
        if (rag.scope.vector_kind != 'rag' or episode.scope.vector_kind != 'episode'
                or rag.scope.tenant_id != frozen_attempt.task.user_id
                or episode.scope.tenant_id != frozen_attempt.task.user_id
                or rag.scope.key == episode.scope.key
                or rag.generation_id == episode.generation_id):
            raise ValueError('Expected distinct same-user RAG and episode scopes')
        return _PairContext(frozen_attempt, rag, episode, deepcopy(domain_context))

    def _states(self, context):
        states = {}
        for plan in (context.rag, context.episode):
            try:
                states[plan.generation_id] = plan.service._state(plan.scope, plan.generation_id)
            except Exception:
                states[plan.generation_id] = 'unreadable'
        return states

    @staticmethod
    def _valid_descriptor(context, plan):
        sealed = plan.sealed
        def same_typed(left, right):
            return type(left) is type(right) and left == right

        return (isinstance(sealed, _SealedGeneration)
                and sealed.scope == plan.scope
                and isinstance(sealed.scope, VectorScope)
                and sealed.scope.key == plan.scope.key
                and sealed.scope.identity.to_dict() == plan.scope.identity.to_dict()
                and sealed.generation_id == plan.generation_id
                and isinstance(sealed.generation_id, UUID)
                and isinstance(sealed.owner_key, tuple)
                and len(sealed.owner_key) == len(context.attempt_key)
                and all(same_typed(value, expected) for value, expected in
                        zip(sealed.owner_key, context.attempt_key))
                and sealed.expected_head == plan.expected_head
                and isinstance(sealed.expected_head, VectorHead)
                and all(same_typed(getattr(sealed.expected_head, field),
                                   getattr(plan.expected_head, field)) for field in
                        ('state', 'revision', 'generation_id', 'index_revision',
                         'snapshot_version'))
                and sealed.expected_index_revision == (plan.expected_head.index_revision or 1)
                and type(sealed.expected_index_revision) is int
                and sealed.snapshot_version == plan.snapshot_version
                and (sealed.snapshot_version is None or type(sealed.snapshot_version) is int)
                and type(sealed.expected_count) is int and sealed.expected_count >= 0
                and sealed.expected_count == len(plan.complete_corpus)
                and isinstance(sealed.content_digest, str)
                and re.fullmatch(r'[a-f0-9]{64}', sealed.content_digest) is not None)

    def _abandon_invoked(self, context, stage_invoked, definitively_rejected=()):
        states = {}
        all_abandoned = True
        for plan, invoked in zip((context.rag, context.episode), stage_invoked):
            if not invoked:
                states[plan.generation_id] = 'not_invoked'
                continue
            if plan.generation_id in definitively_rejected:
                states[plan.generation_id] = 'rejected_before_insert'
                continue
            try:
                plan.service._abandon_or_quarantine(
                    plan.scope, context.attempt, plan.generation_id)
                state = plan.service._state(plan.scope, plan.generation_id)
            except Exception:
                state = 'unconfirmed'
            states[plan.generation_id] = state
            all_abandoned &= state == 'abandoned'
        return all_abandoned, states

    def _read_pair_evidence_in_cursor(self, cursor, context):
        """Pure cursor read for C's future shared read-only snapshot."""
        return self.rag.authority._receipts_in_cursor(cursor, (
            (context.rag.scope, context.rag.generation_id),
            (context.episode.scope, context.episode.generation_id)))

    @staticmethod
    def _validate_pair_evidence(context, rows):
        """Validate immutable receipt/task/audit fields without live lease."""
        if len(rows) != 2 or rows[0] is None or rows[1] is None:
            return None
        owner_key = context.attempt_key
        _, user_id, owner, user_token, user_version, task_id, worker_id, task_token, task_version = owner_key
        publications = []
        tasks = []
        for plan, row in zip((context.rag, context.episode), rows):
            if not ImportVectorPairPublicationService._valid_descriptor(context, plan):
                return None
            sealed = plan.sealed
            next_revision = 1 if sealed.expected_head.revision is None else sealed.expected_head.revision + 1
            if (row['generation_id'] != sealed.generation_id
                    or (row['tenant_id'], row['vector_kind'], row['namespace'],
                        row['index_key']) != sealed.scope.key
                    or row['identity'] != sealed.scope.identity.to_dict()
                    or row['state'] not in ('published', 'retired')
                    or row['sealed_at'] is None or row['published_at'] is None
                    or row['base_revision'] != sealed.expected_head.revision
                    or row['index_revision'] != sealed.expected_index_revision
                    or row['publication_revision'] != next_revision
                    or row['publication_snapshot_version'] != sealed.snapshot_version
                    or row['expected_count'] != sealed.expected_count
                    or row['content_digest'] != sealed.content_digest
                    or row['tenant_id'] != user_id or row['owner'] != owner
                    or row['user_lease_token'] != user_token
                    or row['user_lease_version'] != user_version
                    or row['task_id'] != task_id
                    or row['task_lease_token'] != task_token
                    or row['task_lease_version'] != task_version
                    or row['task_user_id'] != user_id
                    or row['task_status'] != 'succeeded'
                    or row['task_owner'] != worker_id
                    or row['current_task_token'] != task_token
                    or row['current_task_version'] != task_version
                    or row['task_user_token'] != user_token
                    or row['task_user_version'] != user_version
                    or row['audit_task_id'] != task_id
                    or row['audit_user_id'] != user_id
                    or row['audit_task_version'] != task_version
                    or row['attempt_ended_at'] is None
                    or row['attempt_end_reason'] != 'succeeded'
                    or row['attempt_owner'] != worker_id
                    or row['audit_task_token'] != task_token
                    or row['audit_user_token'] != user_token
                    or row['audit_user_version'] != user_version
                    or row['task_record'] is None):
                return None
            task = _task_from_row(row['task_record'])
            if task.task_id != task_id or task.user_id != user_id or task.status != 'succeeded':
                return None
            tasks.append(task)
            head = VectorHead('published' if sealed.expected_count else 'empty',
                              next_revision,
                              sealed.generation_id if sealed.expected_count else None,
                              sealed.expected_index_revision, sealed.snapshot_version)
            publications.append(VectorPublication(sealed.generation_id, head, task))
        if tasks[0] != tasks[1]:
            return None
        return PairPublication(publications[0], publications[1], tasks[0], context)

    def reconcile_pair(self, frozen_context: _PairContext):
        """One read-only MVCC snapshot; no live lease and no replay."""
        if not isinstance(frozen_context, _PairContext):
            raise TypeError('A private frozen pair context is required')
        with self.database.transaction() as cursor:
            cursor.execute('set transaction isolation level repeatable read read only')
            rows = self._read_pair_evidence_in_cursor(cursor, frozen_context)
            return self._validate_pair_evidence(frozen_context, rows)

    def publish_prepared_pair(self, rag_plan, episode_plan, attempt, *,
                              _domain_work: Callable | None = None,
                              _domain_context=None):
        """Publish one fixed pair/task; trusted domain work is DB-only."""
        if _domain_work is not None and not callable(_domain_work):
            raise TypeError('Trusted domain work must be callable')
        context = self._plan(rag_plan, episode_plan, attempt, _domain_context)
        invoked = [False, False]
        preparing = None
        try:
            for index, plan in enumerate((context.rag, context.episode)):
                preparing = index
                def mark_stage(index=index):
                    invoked[index] = True
                sealed = plan.service._prepare_sealed(
                    plan.scope, context.attempt, plan.expected_head,
                    plan.complete_corpus, snapshot_version=plan.snapshot_version,
                    generation_id=plan.generation_id, before_stage=mark_stage)
                context = replace(context, **{
                    'rag' if index == 0 else 'episode': replace(plan, sealed=sealed)})
                if not self._valid_descriptor(context, context.rag if index == 0 else context.episode):
                    raise ValueError('Prepared candidate descriptor differs from frozen pair plan')
        except Exception as error:
            # A known lease/CAS refusal from stage is definitive: it has not
            # inserted this candidate. Transport errors and a lost stage
            # response are never interpreted from a missing row.
            definitive = ()
            if (preparing is not None and invoked[preparing]
                    and (context.rag, context.episode)[preparing].sealed is None
                    and isinstance(error, _StageRejected)):
                definitive = (context.generation_ids[preparing],)
            all_abandoned, states = self._abandon_invoked(context, invoked, definitive)
            if isinstance(error, VectorPublicationUnknown) or not all_abandoned:
                raise ImportPairUnknown(context, 'preparation', states) from error
            raise ImportPairRejected(context) from error

        try:
            begun = self.imports.try_begin_committing(context.attempt)
        except Exception as error:
            raise ImportPairUnknown(context, 'begin committing', self._states(context)) from error
        if not begun:
            all_abandoned, states = self._abandon_invoked(context, (True, True))
            if all_abandoned:
                raise ImportPairRejected(context)
            raise ImportPairUnknown(context, 'begin committing', states)

        callback_errors = []
        def publish(cursor):
            try:
                for plan in (context.rag, context.episode):
                    if not self._valid_descriptor(context, plan):
                        raise ValueError('Prepared candidate descriptor differs from frozen pair plan')
                    sealed = plan.sealed
                    plan.service.authority._publish(
                        cursor, plan.scope, context.attempt, sealed.generation_id,
                        expected_revision=sealed.expected_head.revision,
                        expected_index_revision=sealed.expected_index_revision,
                        snapshot_version=sealed.snapshot_version,
                        expected_count=sealed.expected_count,
                        content_digest=sealed.content_digest)
                if _domain_work is not None:
                    _domain_work(cursor)
            except Exception as error:
                callback_errors.append(error)
                raise

        try:
            self.imports.complete(context.attempt, publish)
        except Exception as error:
            if callback_errors and callback_errors[0] is error:
                raise
            try:
                result = self.reconcile_pair(context)
            except Exception:
                result = None
            if result is not None:
                return result
            raise ImportPairUnknown(context, 'completion', self._states(context)) from error
        try:
            result = self.reconcile_pair(context)
        except Exception as error:
            raise ImportPairUnknown(context, 'reconciliation', self._states(context)) from error
        if result is None:
            raise ImportPairUnknown(context, 'reconciliation', self._states(context))
        return result
