from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence
from uuid import uuid4

from app.note_persistence import NoteStore, SQLiteNotePersistence, PostgresNotePersistence
from app.note_models import (
    NewNoteSource,
    Note,
    NoteIdempotencyConflict,
    NoteNotFoundError,
    NotePage,
    NoteSource,
    NoteSourceDeletingError,
    NoteSourceNotFoundError,
    NoteValidationError,
    NoteVersionConflict,
    decode_note_cursor,
    encode_note_cursor,
    freeze_locator,
    normalize_tag,
    validate_note_input,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class NoteRepository:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.persistence = SQLiteNotePersistence(self.db_path)

    def create(
        self,
        user_id: str,
        body_markdown: str,
        concept: str | None,
        tags: tuple[str, ...],
        client_request_id: str,
        *,
        sources: tuple[NewNoteSource, ...] = (),
        note_id: str | None = None,
        now: str | None = None,
        request_digest: str | None = None,
        guard_sources: bool = False,
    ) -> Note:
        with self.persistence.write(user_id) as conn:
            note = self.create_in_transaction(
                conn,
                user_id,
                body_markdown,
                concept,
                tags,
                client_request_id,
                sources=sources,
                note_id=note_id,
                now=now,
                request_digest=request_digest,
                guard_sources=guard_sources,
            )
            return note

    def list_activity_dates(self, user_id: str, *, since: str) -> tuple[str, ...]:
        with self.persistence.read() as conn:
            rows = conn.activity_dates((user_id, since)).fetchall()
        return tuple(row["created_at"] for row in rows)

    def create_in_transaction(
        self,
        conn: sqlite3.Connection,
        user_id: str,
        body_markdown: str,
        concept: str | None,
        tags: tuple[str, ...],
        client_request_id: str,
        *,
        sources: tuple[NewNoteSource, ...] = (),
        note_id: str | None = None,
        now: str | None = None,
        request_digest: str | None = None,
        guard_sources: bool = False,
    ) -> Note:
        conn = self.persistence.caller_owned(conn, user_id)
        body, normalized_concept, normalized_tags = validate_note_input(
            body_markdown, concept, tags
        )
        request_id = str(client_request_id or "").strip()
        if not request_id:
            raise NoteValidationError("client_request_id is required")
        if len(sources) > 10:
            raise NoteValidationError("sources must contain at most 10 values")
        digest = request_digest or _request_digest(
            body, normalized_concept, normalized_tags, sources
        )
        existing = conn.request((user_id, request_id)).fetchone()
        if existing is not None:
            if existing["request_digest"] != digest:
                raise NoteIdempotencyConflict(request_id)
            return self._get(conn, user_id, existing["id"], include_deleted=True)

        identifier = note_id or str(uuid4())
        timestamp = now or utc_now()
        try:
            conn.insert_note((
                    identifier,
                    user_id,
                    body,
                    normalized_concept,
                    request_id,
                    digest,
                    timestamp,
                    timestamp,
                ))
        except sqlite3.IntegrityError as exc:
            if "client_request_id" in str(exc):
                raise NoteIdempotencyConflict(request_id) from exc
            raise
        self._replace_tags(conn, user_id, identifier, normalized_tags)
        for source in sources:
            if guard_sources:
                self._guard_source_in_transaction(conn, user_id, source)
            self._insert_source(conn, user_id, identifier, source, timestamp)
        self._replace_fts(conn, user_id, identifier, body, normalized_concept, normalized_tags)
        self._enqueue(conn, user_id, identifier, 1, "upsert", timestamp)
        return self._get(conn, user_id, identifier, include_deleted=True)

    def get_by_client_request_id(
        self, user_id: str, client_request_id: str, request_digest: str
    ) -> Note | None:
        """Return an idempotent replay before resolving external sources.

        The digest is supplied by the caller because a source selector is part
        of the create payload.  Keeping this lookup user-scoped lets callers
        safely replay a request even after the referenced QA row is deleted.
        """

        request_id = str(client_request_id or "").strip()
        with self.persistence.read() as conn:
            row = conn.request((user_id, request_id)).fetchone()
            if row is None:
                return None
            if row["request_digest"] != request_digest:
                raise NoteIdempotencyConflict(request_id)
            return self._get(conn, user_id, row["id"], include_deleted=True)

    def get(self, user_id: str, note_id: str) -> Note | None:
        with self.persistence.read() as conn:
            row = conn.live_exists((note_id, user_id)).fetchone()
            return self._get(conn, user_id, note_id) if row is not None else None

    def get_including_deleted(self, user_id: str, note_id: str) -> Note | None:
        with self.persistence.read() as conn:
            row = conn.exists((note_id, user_id)).fetchone()
            return (
                self._get(conn, user_id, note_id, include_deleted=True)
                if row is not None
                else None
            )

    def list_page(
        self,
        user_id: str,
        *,
        cursor: str | None = None,
        limit: int = 20,
        query: str | None = None,
        tags: tuple[str, ...] = (),
        source_kind: str | None = None,
        sort: str = "updated_desc",
    ) -> NotePage:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise NoteValidationError("limit must be between 1 and 50")
        if sort != "updated_desc":
            raise NoteValidationError("sort is invalid")
        if source_kind is not None and source_kind not in {"qa_message", "qa_citation", "document_chunk"}:
            raise NoteValidationError("source_kind is invalid")
        normalized_tags = tuple(normalize_tag(tag)[0] for tag in tags if normalize_tag(tag)[0])
        normalized_query = unicodedata.normalize("NFKC", str(query or "")).strip()
        match_query = _fts_query(normalized_query) if normalized_query else None
        if normalized_query and not match_query:
            return NotePage((), None)
        last = decode_note_cursor(cursor) if cursor else None
        with self.persistence.read() as conn:
            rows = conn.page(user_id, limit=limit, match_query=match_query,
                             tags=normalized_tags, source_kind=source_kind, last=last)
            has_more = len(rows) > limit
            rows = rows[:limit]
            items = tuple(self._get(conn, user_id, row["id"]) for row in rows)
        next_cursor = None
        if has_more and items:
            next_cursor = encode_note_cursor(items[-1].updated_at, items[-1].id)
        return NotePage(items, next_cursor)

    def update(
        self,
        user_id: str,
        note_id: str,
        *,
        expected_version: int,
        body_markdown: str,
        concept: str | None,
        tags: tuple[str, ...],
        now: str | None = None,
    ) -> Note:
        body, normalized_concept, normalized_tags = validate_note_input(
            body_markdown, concept, tags
        )
        timestamp = now or utc_now()
        with self.persistence.write(user_id) as conn:
            row = conn.version((note_id, user_id)).fetchone()
            if row is None or row["deleted_at"] is not None:
                raise NoteNotFoundError(note_id)
            if row["version"] != expected_version:
                raise NoteVersionConflict(note_id, row["version"])
            version = expected_version + 1
            updated = conn.update_note((body, normalized_concept, version, timestamp, note_id, user_id, expected_version))
            if not updated.rowcount:
                raise NoteVersionConflict(note_id)
            self._replace_tags(conn, user_id, note_id, normalized_tags)
            self._replace_fts(conn, user_id, note_id, body, normalized_concept, normalized_tags)
            self._enqueue(conn, user_id, note_id, version, "upsert", timestamp)
            note = self._get(conn, user_id, note_id)
            return note

    def soft_delete(
        self,
        user_id: str,
        note_id: str,
        *,
        expected_version: int,
        now: str | None = None,
    ) -> Note:
        timestamp = now or utc_now()
        with self.persistence.write(user_id) as conn:
            row = conn.version((note_id, user_id)).fetchone()
            if row is None or row["deleted_at"] is not None:
                raise NoteNotFoundError(note_id)
            if row["version"] != expected_version:
                raise NoteVersionConflict(note_id, row["version"])
            version = expected_version + 1
            result = conn.soft_delete((timestamp, timestamp, version, note_id, user_id, expected_version))
            if not result.rowcount:
                raise NoteVersionConflict(note_id)
            conn.delete_search((note_id, user_id))
            self._enqueue(conn, user_id, note_id, version, "delete", timestamp)
            note = self._get(conn, user_id, note_id, include_deleted=True)
            return note

    def clear_all(self, user_id: str, *, now: str | None = None) -> int:
        timestamp = now or utc_now()
        with self.persistence.write(user_id) as conn:
            rows = conn.live_notes((user_id,)).fetchall()
            for row in rows:
                version = row["version"] + 1
                conn.mark_deleted((timestamp, timestamp, version, row["id"], user_id))
                conn.delete_search((row["id"], user_id))
                self._enqueue(conn, user_id, row["id"], version, "delete", timestamp)
            return len(rows)

    def retry_failed_projections(self, user_id: str, *, now: str | None = None) -> int:
        timestamp = now or utc_now()
        with self.persistence.write(user_id) as conn:
            rows = conn.failed_projections((user_id,)).fetchall()
            if rows:
                conn.retry_projections([(timestamp, row["id"]) for row in rows])
                conn.mark_pending([(row["note_id"], user_id) for row in rows])
            return len(rows)

    def count(self, user_id: str) -> int:
        with self.persistence.read() as conn:
            return int(
                conn.count((user_id,)).fetchone()["n"]
            )

    def scrub_sources_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str,
        document_id: str | None = None,
        thread_id: str | None = None,
        deleted_at: str,
    ) -> int:
        return _scrub_sources(
            self.persistence.caller_owned(conn, user_id),
            user_id=user_id,
            document_id=document_id,
            thread_id=thread_id,
            deleted_at=deleted_at,
        )

    def _get(
        self, conn: sqlite3.Connection, user_id: str, note_id: str, *, include_deleted: bool = False
    ) -> Note:
        condition = "" if include_deleted else "and deleted_at is null"
        row = conn.note((note_id, user_id), condition).fetchone()
        if row is None:
            raise NoteNotFoundError(note_id)
        tag_rows = conn.tags((user_id, note_id)).fetchall()
        source_rows = conn.sources((user_id, note_id)).fetchall()
        source_rows.extend(conn.document_sources((user_id, note_id)).fetchall())
        source_rows.sort(key=lambda item: (item["created_at"], item["id"]))
        return Note(
            id=row["id"],
            user_id=row["user_id"],
            body_markdown=row["body_markdown"],
            concept=row["concept"],
            tags=tuple(item["display_tag"] for item in tag_rows),
            sources=tuple(_source_from_row(item) for item in source_rows),
            version=row["version"],
            projection_state=row["projection_state"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            deleted_at=row["deleted_at"],
        )

    @staticmethod
    def _replace_tags(
        conn: sqlite3.Connection, user_id: str, note_id: str, tags: Sequence[str]
    ) -> None:
        conn.delete_tags((user_id, note_id))
        conn.insert_tags([(user_id, note_id, *normalize_tag(tag)) for tag in tags])

    @staticmethod
    def _insert_source(
        conn: sqlite3.Connection,
        user_id: str,
        note_id: str,
        source: NewNoteSource,
        timestamp: str,
    ) -> None:
        locator_json = (
            json.dumps(dict(source.locator), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if source.locator is not None
            else None
        )
        if source.kind == "document_chunk":
            locator = source.locator
            conn.insert_document_source((str(uuid4()), user_id, note_id, source.document_id, locator["chunk_id"],
                 locator["chunk_index"], locator["content_sha256"], locator_json,
                 source.title_snapshot, source.excerpt_snapshot, timestamp))
            return
        conn.insert_source((
                str(uuid4()), user_id, note_id, source.kind, source.qa_thread_id,
                source.qa_message_id, source.citation_id, source.document_id,
                locator_json, source.title_snapshot, source.excerpt_snapshot, timestamp,
            ))

    @staticmethod
    def _guard_source_in_transaction(
        conn: sqlite3.Connection, user_id: str, source: NewNoteSource
    ) -> None:
        """Validate a resolved source while holding the Note write transaction.

        Source lookup happens through ``QaRepository`` before this method, but
        the deletion fence can commit in between that read and Note insertion.
        Rechecking here makes the race deterministic: either this transaction
        commits and the deletion transaction scrubs it, or the fence is observed
        and the association is rejected.
        """

        if source.kind == "document_chunk":
            # Completed fences also close the gap between resolution and insertion.
            # Document UUIDs are not reused for a new import.
            fence = conn.document_fence((user_id, source.document_id)).fetchone()
            if fence is not None:
                if fence["status"] == "completed":
                    raise NoteSourceNotFoundError(source.document_id)
                raise NoteSourceDeletingError(source.document_id)
            return

        message = conn.completed_message((source.qa_message_id, source.qa_thread_id, user_id)).fetchone()
        if message is None:
            raise NoteSourceNotFoundError(source.qa_message_id or "")

        document_id: str | None = None
        if source.kind == "qa_citation":
            citation = conn.citation((
                    source.qa_message_id,
                    source.qa_thread_id,
                    user_id,
                    source.citation_id,
                )).fetchone()
            if citation is None:
                raise NoteSourceNotFoundError(source.citation_id or "")
            document_id = citation["document_id"]

        active = conn.active_fence((
                user_id,
                source.qa_thread_id,
                document_id,
                user_id,
                source.qa_thread_id,
                document_id,
            )).fetchone()
        if active is not None:
            raise NoteSourceDeletingError(source.qa_message_id or "")

    @staticmethod
    def _replace_fts(
        conn: sqlite3.Connection,
        user_id: str,
        note_id: str,
        body: str,
        concept: str | None,
        tags: Sequence[str],
    ) -> None:
        conn.delete_search((note_id, user_id))
        conn.insert_search((
                note_id,
                user_id,
                body,
                concept or "",
                " ".join(normalize_tag(tag)[0] for tag in tags),
            ))

    @staticmethod
    def _enqueue(
        conn: sqlite3.Connection,
        user_id: str,
        note_id: str,
        version: int,
        operation: str,
        timestamp: str,
    ) -> None:
        conn.enqueue((str(uuid4()), user_id, note_id, version, operation, timestamp, timestamp))


class PostgresNoteRepository(NoteRepository):
    """Shared Notes rules backed by an opened PostgreSQL pool.

    Caller-owned create/scrub require an active transaction. The surrounding
    writer MUST lock users(id) before any domain/fence reads or writes; the
    caller-owned boundary also acquires that lock before Notes operations.
    These methods never commit or roll back the caller's transaction.
    """
    def __init__(self, database):
        self.persistence = PostgresNotePersistence(database)


def scrub_sources_in_transaction(conn: sqlite3.Connection, **kwargs) -> int:
    """SQLite deletion seam; transaction ownership stays with the caller."""
    return _scrub_sources(NoteStore(conn), **kwargs)


def _scrub_sources(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    document_id: str | None = None,
    thread_id: str | None = None,
    deleted_at: str,
) -> int:
    if (document_id is None) == (thread_id is None):
        raise NoteValidationError("exactly one source deletion scope is required")
    field = "document_id" if document_id is not None else "qa_thread_id"
    value = document_id if document_id is not None else thread_id
    rows = conn.scrub_candidates((user_id, value), field).fetchall()
    note_ids = {row["note_id"] for row in rows}
    if document_id is not None:
        note_ids.update(row["note_id"] for row in conn.document_scrub_candidates((user_id, document_id)))
        conn.scrub_document_sources((deleted_at, user_id, document_id))
    for note_id in sorted(note_ids):
        conn.scrub_qa_sources((deleted_at, user_id, note_id, value), field)
        note = conn.live_version((note_id, user_id)).fetchone()
        if note is None:
            continue
        version = note["version"] + 1
        conn.bump_version((version, deleted_at, note_id, user_id))
        NoteRepository._enqueue(conn, user_id, note_id, version, "upsert", deleted_at)
    return len(note_ids)


def _source_from_row(row: sqlite3.Row) -> NoteSource:
    locator = json.loads(row["locator_json"]) if row["locator_json"] else None
    return NoteSource(
        id=row["id"],
        kind=row["source_kind"],
        deleted=row["source_deleted_at"] is not None,
        qa_thread_id=row["qa_thread_id"],
        qa_message_id=row["qa_message_id"],
        citation_id=row["citation_id"],
        document_id=row["document_id"],
        locator=freeze_locator(locator),
        title_snapshot=row["title_snapshot"],
        excerpt_snapshot=row["excerpt_snapshot"],
        created_at=row["created_at"],
        source_deleted_at=row["source_deleted_at"],
    )


def _request_digest(
    body: str,
    concept: str | None,
    tags: Sequence[str],
    sources: Sequence[NewNoteSource],
) -> str:
    payload = {
        "body_markdown": body,
        "concept": concept,
        "tags": list(tags),
        "sources": [
            {
                "kind": source.kind,
                "qa_thread_id": source.qa_thread_id,
                "qa_message_id": source.qa_message_id,
                "citation_id": source.citation_id,
                "document_id": source.document_id,
                "locator": dict(source.locator) if source.locator is not None else None,
                "title_snapshot": source.title_snapshot,
                "excerpt_snapshot": source.excerpt_snapshot,
            }
            for source in sources
        ],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _fts_query(query: str) -> str:
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", query, flags=re.UNICODE)
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in tokens)
