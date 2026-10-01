"""Bounded PostgreSQL/Qdrant publication of complete vector corpora.

Callers supply a trusted scope, an already computed *complete* desired corpus,
and DB-only domain publication work. This service does no embedding or copying
from an old generation. A candidate UUID is used once, even after uncertainty.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Callable, Sequence
from uuid import UUID, uuid4

from app.import_repository import _task_from_row
from app.postgres_coordination import MutationLeaseLost, UserMutationLease
from app.postgres_import_leases import ImportAttempt
from app.postgres_vector_generations import (
    PostgresVectorGenerationAuthority, VectorAuthorityError, VectorHead, VectorScope,
)
from hello_agents.memory.storage.generation_vector_store import (
    CandidateGenerationWriter, GenerationVectorStore,
    GenerationVectorStoreError, cleanup_abandoned_generation,
)
from hello_agents.memory.storage.vector_store import QdrantVectorStore, VectorPoint


class VectorPublicationUnknown(RuntimeError):
    """PG outcome or candidate abandonment cannot be confirmed; retain bytes."""

    def __init__(self, generation_id: UUID, phase: str):
        self.generation_id = generation_id
        self.phase = phase
        super().__init__(f"Generation {generation_id} has unknown {phase} outcome; quarantine it")


class VectorCandidateRejected(RuntimeError):
    """Candidate was durably abandoned after an upload or verification failure."""

    def __init__(self, generation_id: UUID):
        self.generation_id = generation_id
        super().__init__(f"Generation {generation_id} was abandoned; use a fresh candidate")


class _StageRejected(RuntimeError):
    """A known authority refusal from stage before its insert can commit."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(str(reason))


@dataclass(frozen=True)
class VectorPublication:
    generation_id: UUID
    head: VectorHead
    import_task: object | None = None


@dataclass(frozen=True)
class _SealedGeneration:
    scope: VectorScope
    generation_id: UUID
    owner_key: tuple
    expected_head: VectorHead
    expected_index_revision: int
    expected_count: int
    content_digest: str
    snapshot_version: int | None


def _owner_key(owner: UserMutationLease | ImportAttempt) -> tuple:
    lease = owner.user_lease if isinstance(owner, ImportAttempt) else owner
    if isinstance(owner, ImportAttempt):
        return ('import', lease.user_id, lease.owner, lease.lease_token,
                lease.lease_version, owner.task.task_id, owner.worker_id,
                owner.lease_token, owner.lease_version)
    return ('user', lease.user_id, lease.owner, lease.lease_token,
            lease.lease_version, None, None, None, None)


def _freeze_request(scope, owner, expected_head, complete_corpus, snapshot_version):
    if not isinstance(scope, VectorScope) or not isinstance(expected_head, VectorHead):
        raise TypeError('Trusted scope and expected PostgreSQL head are required')
    if not isinstance(owner, (UserMutationLease, ImportAttempt)):
        raise TypeError('A live user lease or import attempt is required')
    if expected_head.state not in ('missing', 'empty', 'published'):
        raise ValueError('Invalid expected head')
    if (expected_head.state == 'missing' and
            (expected_head.revision is not None or expected_head.generation_id is not None
             or expected_head.snapshot_version is not None)):
        raise ValueError('Invalid expected head')
    if expected_head.index_revision is not None and (
            type(expected_head.index_revision) is not int or expected_head.index_revision < 1):
        raise ValueError('Invalid expected head')
    if expected_head.snapshot_version is not None and (
            type(expected_head.snapshot_version) is not int or expected_head.snapshot_version < 0):
        raise ValueError('Invalid expected head')
    if expected_head.state != 'missing' and (
            type(expected_head.revision) is not int or expected_head.revision < 1
            or type(expected_head.index_revision) is not int
            or expected_head.index_revision < 1
            or (expected_head.state == 'published' and not isinstance(expected_head.generation_id, UUID))
            or (expected_head.state == 'empty' and expected_head.generation_id is not None)):
        raise ValueError('Invalid expected head')
    if snapshot_version is not None and (type(snapshot_version) is not int or snapshot_version < 0):
        raise ValueError('Invalid snapshot version')
    if not isinstance(complete_corpus, Sequence) or any(
            not isinstance(point, VectorPoint) for point in complete_corpus):
        raise TypeError('A complete sequence of VectorPoint values is required')
    frozen = deepcopy((scope, owner, expected_head, tuple(complete_corpus)))
    if _owner_key(frozen[1])[1] != frozen[0].tenant_id:
        raise ValueError('Wrong tenant')
    return frozen


class VectorGenerationService:
    def __init__(self, authority: PostgresVectorGenerationAuthority,
                 raw: QdrantVectorStore):
        self.authority = authority
        self.raw = raw

    def read_view(self, scope: VectorScope) -> GenerationVectorStore:
        """Resolve one coherent PG head for all subreads of a public operation."""
        return GenerationVectorStore(self.raw, scope, self.authority.read_head(scope))

    def _state(self, scope: VectorScope, generation_id: UUID) -> str:
        result = self.authority.reconcile(scope, generation_id)
        return result['state'] if result is not None else 'missing'

    def _abandon_or_quarantine(self, scope: VectorScope,
                               owner: UserMutationLease | ImportAttempt,
                               generation_id: UUID) -> None:
        try:
            self.authority.abandon(scope, owner, generation_id)
        except Exception as error:
            try:
                state = self._state(scope, generation_id)
            except Exception:
                raise VectorPublicationUnknown(generation_id, 'abandonment') from error
            if state != 'abandoned':
                raise VectorPublicationUnknown(generation_id, 'abandonment') from error

    def cleanup_abandoned(self, scope: VectorScope, generation_id: UUID) -> int:
        """Optional exact-scope cleanup, only after durable abandonment."""
        return cleanup_abandoned_generation(self.raw, scope, generation_id, self._state)

    @staticmethod
    def _matches_sealed(record, scope, generation_id, owner_key, head,
                        index_revision, count, digest):
        if not record:
            return False
        kind, user_id, lease_owner, user_token, user_version, task_id, worker_id, task_token, task_version = owner_key
        return (record['generation_id'] == generation_id
                and (record['tenant_id'], record['vector_kind'], record['namespace'],
                     record['index_key']) == scope.key
                and record['identity'] == scope.identity.to_dict()
                and record['state'] == 'sealed' and record['sealed_at'] is not None
                and record['owner'] == lease_owner
                and record['user_lease_token'] == user_token
                and record['user_lease_version'] == user_version
                and record['tenant_id'] == user_id
                and record['task_id'] == task_id
                and record['task_lease_token'] == task_token
                and record['task_lease_version'] == task_version
                and (kind != 'import' or worker_id == lease_owner)
                and record['base_revision'] == head.revision
                and record['index_revision'] == index_revision
                and record['expected_count'] == count
                and record['content_digest'] == digest
                and record['published_at'] is None
                and record['publication_revision'] is None
                and record['publication_snapshot_version'] is None)

    def _prepare_sealed(self, scope, owner, expected_head, complete_corpus,
                        *, snapshot_version, generation_id, before_stage=None):
        """Prepare one internally issued candidate; caller owns uncertain-ID policy."""
        if self.authority.read_head(scope) != expected_head:
            raise GenerationVectorStoreError('Expected vector head changed')
        if before_stage is not None:
            before_stage()
        try:
            self.authority.stage(scope, owner, expected_revision=expected_head.revision,
                                 generation_id=generation_id)
        except (MutationLeaseLost, VectorAuthorityError) as error:
            raise _StageRejected(error) from error
        writer = CandidateGenerationWriter(self.raw, scope, generation_id, self._state)
        writer.upload(complete_corpus)
        count, digest = writer.verify()
        owner_key = _owner_key(owner)
        index_revision = expected_head.index_revision or 1
        try:
            self.authority.seal(scope, owner, generation_id,
                                expected_count=count, content_digest=digest)
        except Exception as error:
            try:
                record = self.authority.publication_receipt(scope, generation_id)
            except Exception:
                raise VectorPublicationUnknown(generation_id, 'seal') from error
            if not self._matches_sealed(record, scope, generation_id, owner_key,
                                        expected_head, index_revision, count, digest):
                raise VectorPublicationUnknown(generation_id, 'seal') from error
        return _SealedGeneration(scope, generation_id, owner_key, expected_head,
                                 index_revision, count, digest, snapshot_version)

    def _published(self, scope: VectorScope, generation_id: UUID,
                   expected_revision: int | None, expected_index_revision: int,
                   count: int, digest: str, owner: UserMutationLease | ImportAttempt,
                   snapshot_version: int | None) -> VectorPublication | None:
        record = self.authority.publication_receipt(scope, generation_id)
        if not record or record['state'] == 'sealed':
            return None
        if record['state'] not in ('published', 'retired'):
            raise VectorPublicationUnknown(generation_id, 'publication')
        revision = 1 if expected_revision is None else expected_revision + 1
        if (record['base_revision'] != expected_revision
                or record['publication_revision'] != revision
                or record['index_revision'] != expected_index_revision
                or record['publication_snapshot_version'] != snapshot_version
                or record['expected_count'] != count
                or record['content_digest'] != digest
                or record['published_at'] is None):
            raise VectorPublicationUnknown(generation_id, 'publication')
        lease = owner.user_lease if isinstance(owner, ImportAttempt) else owner
        if (record['owner'] != lease.owner
                or record['user_lease_token'] != lease.lease_token
                or record['user_lease_version'] != lease.lease_version):
            raise VectorPublicationUnknown(generation_id, 'publication')
        task = None
        if isinstance(owner, ImportAttempt):
            if (record['task_id'] != owner.task.task_id
                    or record['task_lease_token'] != owner.lease_token
                    or record['task_lease_version'] != owner.lease_version
                    or record['task_status'] != 'succeeded'
                    or record['task_owner'] != owner.worker_id
                    or record['current_task_token'] != owner.lease_token
                    or record['current_task_version'] != owner.lease_version
                    or record['task_user_token'] != lease.lease_token
                    or record['task_user_version'] != lease.lease_version
                    or record['attempt_ended_at'] is None
                    or record['attempt_end_reason'] != 'succeeded'
                    or record['attempt_owner'] != owner.worker_id
                    or record['audit_task_token'] != owner.lease_token
                    or record['audit_user_token'] != lease.lease_token
                    or record['audit_user_version'] != lease.lease_version
                    or record['task_record'] is None):
                raise VectorPublicationUnknown(generation_id, 'import completion')
            task = _task_from_row(record['task_record'])
        elif any(record[key] is not None for key in
                 ('task_id', 'task_lease_token', 'task_lease_version')):
            raise VectorPublicationUnknown(generation_id, 'publication')
        head = VectorHead('published' if count else 'empty', revision,
                          generation_id if count else None,
                          expected_index_revision, snapshot_version)
        return VectorPublication(generation_id, head, task)

    def publish_complete(
        self, scope: VectorScope, owner: UserMutationLease | ImportAttempt,
        expected_head: VectorHead, complete_corpus: Sequence[VectorPoint],
        *, domain_publish: Callable | None = None,
        snapshot_version: int | None = None,
    ) -> VectorPublication:
        """Stage, verify, seal and publish one full replacement generation.

        The caller is responsible for constructing the entire desired corpus;
        passing only changed points would intentionally replace the head with
        only those points. Domain callbacks receive the publication transaction's
        cursor and may perform short PostgreSQL work only.
        """
        scope, owner, expected_head, complete_corpus = _freeze_request(
            scope, owner, expected_head, complete_corpus, snapshot_version)
        if domain_publish is not None and not callable(domain_publish):
            raise TypeError('domain_publish must be callable')
        generation_id = uuid4()
        stage_invoked = []
        try:
            sealed = self._prepare_sealed(
                scope, owner, expected_head, complete_corpus,
                snapshot_version=snapshot_version, generation_id=generation_id,
                before_stage=lambda: stage_invoked.append(True))
        except Exception as error:
            if not stage_invoked:
                raise
            if isinstance(error, VectorPublicationUnknown):
                raise
            # These authority checks fail before the stage insert commits.
            # Keep the original type and never infer a staged row from a
            # failed local/CAS/lease validation.
            if isinstance(error, _StageRejected):
                raise error.reason from error
            try:
                self._abandon_or_quarantine(scope, owner, generation_id)
            except VectorPublicationUnknown:
                raise
            raise VectorCandidateRejected(generation_id) from error
        count, digest = sealed.expected_count, sealed.content_digest

        is_import = isinstance(owner, ImportAttempt)
        domain_error: list[Exception] = []

        def publish_domain(cursor):
            if domain_publish is not None:
                try:
                    domain_publish(cursor)
                except Exception as error:
                    domain_error.append(error)
                    raise

        try:
            if is_import:
                if not self.authority.imports.try_begin_committing(owner):
                    self._abandon_or_quarantine(scope, owner, generation_id)
                    raise VectorCandidateRejected(generation_id)
                self.authority.complete_import(
                    scope, owner, generation_id,
                    expected_revision=expected_head.revision,
                    expected_index_revision=expected_head.index_revision or 1,
                    snapshot_version=snapshot_version, domain_publish=publish_domain)
            else:
                self.authority.publish_user(
                    scope, owner, generation_id,
                    expected_revision=expected_head.revision,
                    expected_index_revision=expected_head.index_revision or 1,
                    snapshot_version=snapshot_version, domain_publish=publish_domain)
        except VectorCandidateRejected:
            raise
        except Exception as error:
            try:
                published = self._published(
                    scope, generation_id, expected_head.revision,
                    sealed.expected_index_revision, count, digest, owner,
                    snapshot_version)
            except VectorPublicationUnknown:
                raise
            except Exception:
                raise VectorPublicationUnknown(generation_id, 'publication') from error
            if published is not None:
                return published
            # A domain callback exception is a known transaction rollback. A
            # transport/commit exception may have been observed before PG made
            # its final decision, even if this first read still sees "sealed".
            if domain_error and domain_error[0] is error:
                raise
            raise VectorPublicationUnknown(generation_id, 'publication') from error
        try:
            published = self._published(
                scope, generation_id, expected_head.revision,
                sealed.expected_index_revision, count, digest, owner,
                snapshot_version)
        except Exception as error:
            raise VectorPublicationUnknown(generation_id, 'publication') from error
        if published is None:
            raise VectorPublicationUnknown(generation_id, 'publication')
        return published
