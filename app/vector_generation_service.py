"""Bounded PostgreSQL/Qdrant publication of complete vector corpora.

Callers supply a trusted scope, an already computed *complete* desired corpus,
and DB-only domain publication work. This service does no embedding or copying
from an old generation. A candidate UUID is used once, even after uncertainty.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence
from uuid import UUID

from app.import_repository import _task_from_row
from app.postgres_coordination import UserMutationLease
from app.postgres_import_leases import ImportAttempt
from app.postgres_vector_generations import (
    PostgresVectorGenerationAuthority, VectorHead, VectorScope,
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


@dataclass(frozen=True)
class VectorPublication:
    generation_id: UUID
    head: VectorHead
    import_task: object | None = None


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

    def _published(self, scope: VectorScope, generation_id: UUID,
                   expected_revision: int | None, expected_index_revision: int,
                   count: int, digest: str, owner: UserMutationLease | ImportAttempt,
                   *, require_task: bool) -> VectorPublication | None:
        record = self.authority.reconcile(scope, generation_id)
        if not record or record['state'] == 'sealed':
            return None
        if record['state'] != 'published' or not record['is_head']:
            raise VectorPublicationUnknown(generation_id, 'publication')
        revision = 1 if expected_revision is None else expected_revision + 1
        if (record['head_revision'] != revision or record['expected_count'] != count
                or record['content_digest'] != digest):
            raise VectorPublicationUnknown(generation_id, 'publication')
        head = self.authority.read_head(scope)
        # Another subsequent publication may already have retired this candidate.
        # In that case the durable result is historical, never a new permission to replay.
        if head.revision != revision or head.index_revision != expected_index_revision:
            raise VectorPublicationUnknown(generation_id, 'publication')
        if head.state != ('published' if count else 'empty') \
                or head.generation_id != (generation_id if count else None):
            raise VectorPublicationUnknown(generation_id, 'publication')
        task = None
        if require_task:
            attempt = owner
            with self.authority.database.transaction() as cursor:
                row = cursor.execute('''select * from import_tasks
                    where id=%s and user_id=%s''',
                    (attempt.task.task_id, scope.tenant_id)).fetchone()
            if (row is None or row['status'] != 'succeeded'
                    or row['lease_token'] != attempt.lease_token
                    or row['lease_version'] != attempt.lease_version):
                raise VectorPublicationUnknown(generation_id, 'import completion')
            task = _task_from_row(row)
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
        if not isinstance(scope, VectorScope) or not isinstance(expected_head, VectorHead):
            raise TypeError('Trusted scope and expected PostgreSQL head are required')
        if not isinstance(owner, (UserMutationLease, ImportAttempt)):
            raise TypeError('A live user lease or import attempt is required')
        if expected_head.state not in ('missing', 'empty', 'published'):
            raise ValueError('Invalid expected head')
        if not isinstance(complete_corpus, Sequence) or any(
                not isinstance(point, VectorPoint) for point in complete_corpus):
            raise TypeError('A complete sequence of VectorPoint values is required')
        if domain_publish is not None and not callable(domain_publish):
            raise TypeError('domain_publish must be callable')
        if self.authority.read_head(scope) != expected_head:
            raise GenerationVectorStoreError('Expected vector head changed')

        # stage performs the final head CAS and allocates the UUID under PG locks.
        generation_id = self.authority.stage(
            scope, owner, expected_revision=expected_head.revision)
        try:
            writer = CandidateGenerationWriter(self.raw, scope, generation_id, self._state)
            writer.upload(complete_corpus)
            count, digest = writer.verify()
        except Exception as error:
            self._abandon_or_quarantine(scope, owner, generation_id)
            raise VectorCandidateRejected(generation_id) from error

        try:
            self.authority.seal(scope, owner, generation_id,
                                expected_count=count, content_digest=digest)
        except Exception as error:
            try:
                record = self.authority.reconcile(scope, generation_id)
            except Exception:
                raise VectorPublicationUnknown(generation_id, 'seal') from error
            if not record or record['state'] != 'sealed' or record['expected_count'] != count \
                    or record['content_digest'] != digest:
                if record and record['state'] == 'staging':
                    self._abandon_or_quarantine(scope, owner, generation_id)
                raise VectorPublicationUnknown(generation_id, 'seal') from error

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
                task, _ = self.authority.complete_import(
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
                task = None
        except VectorCandidateRejected:
            raise
        except Exception as error:
            try:
                published = self._published(
                    scope, generation_id, expected_head.revision,
                    expected_head.index_revision or 1, count, digest, owner,
                    require_task=is_import)
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
                expected_head.index_revision or 1, count, digest, owner,
                require_task=is_import)
        except Exception as error:
            raise VectorPublicationUnknown(generation_id, 'publication') from error
        if published is None:
            raise VectorPublicationUnknown(generation_id, 'publication')
        return VectorPublication(generation_id, published.head, task)
