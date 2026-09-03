from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
from typing import Any

from app.database import connect
from app.note_migration import NoteMigrationService
from app.note_models import (
    NewNoteSource,
    Note,
    NoteFilters,
    NoteNotFoundError,
    NotePage,
    NoteSourceDeletingError,
    NoteSourceNotFoundError,
    NoteSourceSelector,
    NoteValidationError,
    validate_note_input,
)
from app.note_projection import NoteProjectionWorker
from app.note_repository import NoteRepository
from app.qa_repository import QaRepository
from app.session import UserSession


class NoteService:
    def __init__(
        self,
        repository: NoteRepository,
        migration: NoteMigrationService,
        qa_repository: QaRepository,
        projection_worker: NoteProjectionWorker,
    ) -> None:
        self.repository = repository
        self.migration = migration
        self.qa_repository = qa_repository
        self.projection_worker = projection_worker

    def list_notes(self, session: UserSession, filters: NoteFilters) -> NotePage:
        self.migration.ensure_user_migrated(session.user_id)
        return self.repository.list_page(
            session.user_id, **filters.as_repository_kwargs()
        )

    def get_note(self, session: UserSession, note_id: str) -> Note:
        self.migration.ensure_user_migrated(session.user_id)
        note = self.repository.get(session.user_id, note_id)
        if note is None:
            raise NoteNotFoundError(note_id)
        return note

    def create(
        self,
        session: UserSession,
        *,
        body_markdown: str,
        concept: str | None,
        tags: tuple[str, ...],
        client_request_id: str,
        source: NoteSourceSelector | None = None,
    ) -> Note:
        self.migration.ensure_user_migrated(session.user_id)
        body, normalized_concept, normalized_tags = validate_note_input(
            body_markdown, concept, tags
        )
        request_digest = _service_request_digest(
            body, normalized_concept, normalized_tags, source
        )
        lock = getattr(getattr(session, "runtime", None), "lock", None)
        with lock if lock is not None else nullcontext():
            note = self.repository.get_by_client_request_id(
                session.user_id, client_request_id, request_digest
            )
            if note is None:
                sources = (
                    (self._resolve_source(session.user_id, source),)
                    if source is not None
                    else ()
                )
                note = self.repository.create(
                    session.user_id,
                    body,
                    normalized_concept,
                    normalized_tags,
                    client_request_id,
                    sources=sources,
                    request_digest=request_digest,
                    guard_sources=True,
                )
        self._notify_best_effort()
        return note

    def update(
        self,
        session: UserSession,
        note_id: str,
        *,
        expected_version: int,
        body_markdown: str,
        concept: str | None,
        tags: tuple[str, ...],
    ) -> Note:
        self.migration.ensure_user_migrated(session.user_id)
        lock = getattr(getattr(session, "runtime", None), "lock", None)
        with lock if lock is not None else nullcontext():
            note = self.repository.update(
                session.user_id,
                note_id,
                expected_version=expected_version,
                body_markdown=body_markdown,
                concept=concept,
                tags=tags,
            )
        self._notify_best_effort()
        return note

    def delete(
        self, session: UserSession, note_id: str, *, expected_version: int
    ) -> Note:
        self.migration.ensure_user_migrated(session.user_id)
        lock = getattr(getattr(session, "runtime", None), "lock", None)
        with lock if lock is not None else nullcontext():
            note = self.repository.soft_delete(
                session.user_id, note_id, expected_version=expected_version
            )
        self._notify_best_effort()
        return note

    def clear_all(self, session: UserSession, *, confirmation: str) -> int:
        self.migration.ensure_user_migrated(session.user_id)
        if confirmation != "清空全部笔记":
            raise NoteValidationError("confirmation is invalid")
        lock = getattr(getattr(session, "runtime", None), "lock", None)
        with lock if lock is not None else nullcontext():
            count = self.repository.clear_all(session.user_id)
        if count:
            self._notify_best_effort()
        return count

    def search(
        self,
        session: UserSession,
        query: str,
        *,
        cursor: str | None = None,
        limit: int = 20,
        tags: tuple[str, ...] = (),
        source_kind: str | None = None,
    ) -> NotePage:
        return self.list_notes(
            session,
            NoteFilters(
                cursor=cursor,
                limit=limit,
                query=query,
                tags=tags,
                source_kind=source_kind,  # type: ignore[arg-type]
            ),
        )

    def count(self, session: UserSession) -> int:
        self.migration.ensure_user_migrated(session.user_id)
        return self.repository.count(session.user_id)

    def recent(self, session: UserSession, *, limit: int = 10) -> tuple[Note, ...]:
        return self.list_notes(session, NoteFilters(limit=limit)).items

    def retry_projection(self, session: UserSession) -> int:
        self.migration.ensure_user_migrated(session.user_id)
        count = self.repository.retry_failed_projections(session.user_id)
        if count:
            self._notify_best_effort()
        return count

    # Trusted-internal entry points keep legacy adapters from fabricating a
    # public session payload while retaining the same user-scoped repository.
    def list_for_user(self, user_id: str, filters: NoteFilters) -> NotePage:
        self.migration.ensure_user_migrated(user_id)
        return self.repository.list_page(user_id, **filters.as_repository_kwargs())

    def count_for_user(self, user_id: str) -> int:
        self.migration.ensure_user_migrated(user_id)
        return self.repository.count(user_id)

    def get_for_user(self, user_id: str, note_id: str) -> Note:
        self.migration.ensure_user_migrated(user_id)
        note = self.repository.get(user_id, note_id)
        if note is None:
            raise NoteNotFoundError(note_id)
        return note

    def create_for_user(
        self,
        user_id: str,
        *,
        body_markdown: str,
        concept: str | None,
        tags: tuple[str, ...],
        client_request_id: str,
    ) -> Note:
        """Trusted legacy/internal creation without accepting a public user ID."""

        self.migration.ensure_user_migrated(user_id)
        runtime = self.migration.runtime_registry.get_or_create(user_id)
        with runtime.lock:
            note = self.repository.create(
                user_id, body_markdown, concept, tags, client_request_id
            )
        self._notify_best_effort()
        return note

    def search_for_user(
        self, user_id: str, query: str, *, limit: int = 20
    ) -> NotePage:
        return self.list_for_user(user_id, NoteFilters(query=query, limit=limit))

    def recent_for_user(self, user_id: str, *, limit: int = 10) -> tuple[Note, ...]:
        return self.list_for_user(user_id, NoteFilters(limit=limit)).items

    def update_for_user(
        self,
        user_id: str,
        note_id: str,
        *,
        expected_version: int,
        body_markdown: str,
        concept: str | None,
        tags: tuple[str, ...],
    ) -> Note:
        self.migration.ensure_user_migrated(user_id)
        runtime = self.migration.runtime_registry.get_or_create(user_id)
        with runtime.lock:
            note = self.repository.update(
                user_id,
                note_id,
                expected_version=expected_version,
                body_markdown=body_markdown,
                concept=concept,
                tags=tags,
            )
        self._notify_best_effort()
        return note

    def delete_for_user(
        self, user_id: str, note_id: str, *, expected_version: int
    ) -> Note:
        self.migration.ensure_user_migrated(user_id)
        runtime = self.migration.runtime_registry.get_or_create(user_id)
        with runtime.lock:
            note = self.repository.soft_delete(
                user_id, note_id, expected_version=expected_version
            )
        self._notify_best_effort()
        return note

    def clear_for_user(self, user_id: str) -> int:
        self.migration.ensure_user_migrated(user_id)
        runtime = self.migration.runtime_registry.get_or_create(user_id)
        with runtime.lock:
            count = self.repository.clear_all(user_id)
        if count:
            self._notify_best_effort()
        return count

    def retry_for_user(self, user_id: str) -> int:
        self.migration.ensure_user_migrated(user_id)
        count = self.repository.retry_failed_projections(user_id)
        if count:
            self._notify_best_effort()
        return count

    def _resolve_source(
        self, user_id: str, selector: NoteSourceSelector
    ) -> NewNoteSource:
        if self._source_fence_active(user_id, selector):
            raise NoteSourceDeletingError(selector.qa_message_id)
        message = self.qa_repository.get_message(user_id, selector.qa_message_id)
        if (
            message is None
            or message.role != "assistant"
            or message.status != "completed"
        ):
            # A fenced QA read is intentionally hidden by QaRepository.  The
            # fence may have committed after the first check and immediately
            # before this read, so distinguish that race from an absent/foreign
            # source before returning the safe not-found error.
            if self._source_fence_active(user_id, selector):
                raise NoteSourceDeletingError(selector.qa_message_id)
            raise NoteSourceNotFoundError(selector.qa_message_id)
        if selector.kind == "qa_answer":
            return NewNoteSource(
                kind="qa_message",
                qa_thread_id=message.conversation_id,
                qa_message_id=message.id,
                citation_id=None,
                document_id=None,
                locator=None,
                title_snapshot=None,
                excerpt_snapshot=message.content,
            )
        citation = next(
            (
                source
                for source in message.sources
                if source.citation_id == selector.citation_id
            ),
            None,
        )
        if citation is None:
            raise NoteSourceNotFoundError(str(selector.citation_id))
        locator = {
            key: value
            for key, value in {
                "page_number": citation.page_number,
                "section": citation.section,
            }.items()
            if value is not None
        }
        return NewNoteSource(
            kind="qa_citation",
            qa_thread_id=message.conversation_id,
            qa_message_id=message.id,
            citation_id=citation.citation_id,
            document_id=citation.document_id,
            locator=locator or None,
            title_snapshot=citation.document_name,
            excerpt_snapshot=citation.excerpt,
        )

    def _source_fence_active(
        self, user_id: str, selector: NoteSourceSelector
    ) -> bool:
        """Distinguish a fenced source from an absent or foreign source."""

        with connect(self.repository.db_path) as conn:
            row = conn.execute(
                """
                select 1 from qa_deletion_fences f
                where f.user_id=?
                  and (f.status in ('queued','running')
                       or (f.status='failed' and f.attempt_count < 3))
                  and (
                    (f.target_type='conversation' and f.target_id=(
                        select conversation_id from qa_messages
                        where id=? and user_id=?
                    ))
                    or (f.target_type='document' and exists (
                        select 1 from qa_message_sources s
                        where s.assistant_message_id=? and s.user_id=?
                          and s.citation_id=? and s.document_id=f.target_id
                    ))
                  )
                limit 1
                """,
                (
                    user_id,
                    selector.qa_message_id,
                    user_id,
                    selector.qa_message_id,
                    user_id,
                    selector.citation_id,
                ),
            ).fetchone()
            return row is not None

    def _notify_best_effort(self) -> None:
        try:
            self.projection_worker.notify()
        except Exception:
            # SQLite + outbox commit is the reliability boundary.  A missed
            # wakeup is recovered by polling/restart and must not fail a save.
            return


def _service_request_digest(
    body: str,
    concept: str | None,
    tags: tuple[str, ...],
    source: NoteSourceSelector | None,
) -> str:
    """Fingerprint the client payload without dereferencing a QA source."""

    payload = {
        "body_markdown": body,
        "concept": concept,
        "tags": list(tags),
        "source": (
            {
                "kind": source.kind,
                "qa_message_id": source.qa_message_id,
                "citation_id": source.citation_id,
            }
            if source is not None
            else None
        ),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
