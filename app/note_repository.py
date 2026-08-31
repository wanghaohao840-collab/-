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

from app.database import connect
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
        conn = connect(self.db_path)
        try:
            conn.execute("begin immediate")
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
            conn.commit()
            return note
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

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
        existing = conn.execute(
            "select id, request_digest from notes where user_id=? and client_request_id=?",
            (user_id, request_id),
        ).fetchone()
        if existing is not None:
            if existing["request_digest"] != digest:
                raise NoteIdempotencyConflict(request_id)
            return self._get(conn, user_id, existing["id"], include_deleted=True)

        identifier = note_id or str(uuid4())
        timestamp = now or utc_now()
        try:
            conn.execute(
                """
                insert into notes (
                    id,user_id,body_markdown,concept,version,projection_state,
                    client_request_id,request_digest,created_at,updated_at
                ) values (?,?,?,?,1,'pending',?,?,?,?)
                """,
                (
                    identifier,
                    user_id,
                    body,
                    normalized_concept,
                    request_id,
                    digest,
                    timestamp,
                    timestamp,
                ),
            )
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
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                select id, request_digest from notes
                where user_id=? and client_request_id=?
                """,
                (user_id, request_id),
            ).fetchone()
            if row is None:
                return None
            if row["request_digest"] != request_digest:
                raise NoteIdempotencyConflict(request_id)
            return self._get(conn, user_id, row["id"], include_deleted=True)

    def get(self, user_id: str, note_id: str) -> Note | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "select 1 from notes where id=? and user_id=? and deleted_at is null",
                (note_id, user_id),
            ).fetchone()
            return self._get(conn, user_id, note_id) if row is not None else None

    def get_including_deleted(self, user_id: str, note_id: str) -> Note | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "select 1 from notes where id=? and user_id=?", (note_id, user_id)
            ).fetchone()
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
        if source_kind is not None and source_kind not in {"qa_message", "qa_citation"}:
            raise NoteValidationError("source_kind is invalid")
        normalized_tags = tuple(normalize_tag(tag)[0] for tag in tags if normalize_tag(tag)[0])
        params: list[object] = [user_id]
        clauses = ["n.user_id=?", "n.deleted_at is null"]
        joins = ""
        normalized_query = unicodedata.normalize("NFKC", str(query or "")).strip()
        if normalized_query:
            match_query = _fts_query(normalized_query)
            if not match_query:
                return NotePage((), None)
            joins += " join notes_fts f on f.note_id=n.id and f.user_id=n.user_id"
            clauses.append("notes_fts match ?")
            params.append(match_query)
        for normalized_tag in dict.fromkeys(normalized_tags):
            clauses.append(
                "exists (select 1 from note_tags t where t.user_id=n.user_id and t.note_id=n.id and t.normalized_tag=?)"
            )
            params.append(normalized_tag)
        if source_kind is not None:
            clauses.append(
                "exists (select 1 from note_sources s where s.user_id=n.user_id and s.note_id=n.id and s.source_kind=?)"
            )
            params.append(source_kind)
        if cursor:
            timestamp, identifier = decode_note_cursor(cursor)
            clauses.append("(n.updated_at < ? or (n.updated_at=? and n.id < ?))")
            params.extend((timestamp, timestamp, identifier))
        params.append(limit + 1)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                select n.id from notes n {joins}
                where {' and '.join(clauses)}
                order by n.updated_at desc, n.id desc limit ?
                """,
                params,
            ).fetchall()
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
        conn = connect(self.db_path)
        try:
            conn.execute("begin immediate")
            row = conn.execute(
                "select version, deleted_at from notes where id=? and user_id=?",
                (note_id, user_id),
            ).fetchone()
            if row is None or row["deleted_at"] is not None:
                raise NoteNotFoundError(note_id)
            if row["version"] != expected_version:
                raise NoteVersionConflict(note_id, row["version"])
            version = expected_version + 1
            updated = conn.execute(
                """
                update notes set body_markdown=?, concept=?, version=?,
                    projection_state='pending', updated_at=?
                where id=? and user_id=? and deleted_at is null and version=?
                """,
                (body, normalized_concept, version, timestamp, note_id, user_id, expected_version),
            )
            if not updated.rowcount:
                raise NoteVersionConflict(note_id)
            self._replace_tags(conn, user_id, note_id, normalized_tags)
            self._replace_fts(conn, user_id, note_id, body, normalized_concept, normalized_tags)
            self._enqueue(conn, user_id, note_id, version, "upsert", timestamp)
            note = self._get(conn, user_id, note_id)
            conn.commit()
            return note
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def soft_delete(
        self,
        user_id: str,
        note_id: str,
        *,
        expected_version: int,
        now: str | None = None,
    ) -> Note:
        timestamp = now or utc_now()
        conn = connect(self.db_path)
        try:
            conn.execute("begin immediate")
            row = conn.execute(
                "select version, deleted_at from notes where id=? and user_id=?",
                (note_id, user_id),
            ).fetchone()
            if row is None or row["deleted_at"] is not None:
                raise NoteNotFoundError(note_id)
            if row["version"] != expected_version:
                raise NoteVersionConflict(note_id, row["version"])
            version = expected_version + 1
            result = conn.execute(
                """
                update notes set deleted_at=?, updated_at=?, version=?, projection_state='pending'
                where id=? and user_id=? and deleted_at is null and version=?
                """,
                (timestamp, timestamp, version, note_id, user_id, expected_version),
            )
            if not result.rowcount:
                raise NoteVersionConflict(note_id)
            conn.execute("delete from notes_fts where note_id=? and user_id=?", (note_id, user_id))
            self._enqueue(conn, user_id, note_id, version, "delete", timestamp)
            note = self._get(conn, user_id, note_id, include_deleted=True)
            conn.commit()
            return note
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def clear_all(self, user_id: str, *, now: str | None = None) -> int:
        timestamp = now or utc_now()
        conn = connect(self.db_path)
        try:
            conn.execute("begin immediate")
            rows = conn.execute(
                "select id, version from notes where user_id=? and deleted_at is null",
                (user_id,),
            ).fetchall()
            for row in rows:
                version = row["version"] + 1
                conn.execute(
                    "update notes set deleted_at=?,updated_at=?,version=?,projection_state='pending' where id=? and user_id=?",
                    (timestamp, timestamp, version, row["id"], user_id),
                )
                conn.execute(
                    "delete from notes_fts where note_id=? and user_id=?", (row["id"], user_id)
                )
                self._enqueue(conn, user_id, row["id"], version, "delete", timestamp)
            conn.commit()
            return len(rows)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def retry_failed_projections(self, user_id: str, *, now: str | None = None) -> int:
        timestamp = now or utc_now()
        conn = connect(self.db_path)
        try:
            conn.execute("begin immediate")
            rows = conn.execute(
                """
                select t.id, t.note_id from note_projection_tasks t
                join notes n on n.id=t.note_id and n.user_id=t.user_id
                where t.user_id=? and t.status='failed' and t.note_version=n.version
                """,
                (user_id,),
            ).fetchall()
            if rows:
                conn.executemany(
                    "update note_projection_tasks set status='queued', attempt_count=0, available_at=?, lease_owner=null, lease_expires_at=null, last_error_code=null, finished_at=null where id=?",
                    [(timestamp, row["id"]) for row in rows],
                )
                conn.executemany(
                    "update notes set projection_state='pending' where id=? and user_id=?",
                    [(row["note_id"], user_id) for row in rows],
                )
            conn.commit()
            return len(rows)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def count(self, user_id: str) -> int:
        with connect(self.db_path) as conn:
            return int(
                conn.execute(
                    "select count(*) from notes where user_id=? and deleted_at is null",
                    (user_id,),
                ).fetchone()[0]
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
        return scrub_sources_in_transaction(
            conn,
            user_id=user_id,
            document_id=document_id,
            thread_id=thread_id,
            deleted_at=deleted_at,
        )

    def _get(
        self, conn: sqlite3.Connection, user_id: str, note_id: str, *, include_deleted: bool = False
    ) -> Note:
        condition = "" if include_deleted else "and deleted_at is null"
        row = conn.execute(
            f"select * from notes where id=? and user_id=? {condition}", (note_id, user_id)
        ).fetchone()
        if row is None:
            raise NoteNotFoundError(note_id)
        tag_rows = conn.execute(
            "select display_tag from note_tags where user_id=? and note_id=? order by rowid",
            (user_id, note_id),
        ).fetchall()
        source_rows = conn.execute(
            "select * from note_sources where user_id=? and note_id=? order by created_at,id",
            (user_id, note_id),
        ).fetchall()
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
        conn.execute("delete from note_tags where user_id=? and note_id=?", (user_id, note_id))
        conn.executemany(
            "insert into note_tags (user_id,note_id,normalized_tag,display_tag) values (?,?,?,?)",
            [(user_id, note_id, *normalize_tag(tag)) for tag in tags],
        )

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
        conn.execute(
            """
            insert into note_sources (
                id,user_id,note_id,source_kind,qa_thread_id,qa_message_id,
                citation_id,document_id,locator_json,title_snapshot,
                excerpt_snapshot,created_at
            ) values (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(uuid4()), user_id, note_id, source.kind, source.qa_thread_id,
                source.qa_message_id, source.citation_id, source.document_id,
                locator_json, source.title_snapshot, source.excerpt_snapshot, timestamp,
            ),
        )

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

        message = conn.execute(
            """
            select id, conversation_id from qa_messages
            where id=? and conversation_id=? and user_id=?
              and role='assistant' and status='completed'
            """,
            (source.qa_message_id, source.qa_thread_id, user_id),
        ).fetchone()
        if message is None:
            raise NoteSourceNotFoundError(source.qa_message_id or "")

        document_id: str | None = None
        if source.kind == "qa_citation":
            citation = conn.execute(
                """
                select citation_id, document_id from qa_message_sources
                where assistant_message_id=? and conversation_id=? and user_id=?
                  and citation_id=?
                """,
                (
                    source.qa_message_id,
                    source.qa_thread_id,
                    user_id,
                    source.citation_id,
                ),
            ).fetchone()
            if citation is None:
                raise NoteSourceNotFoundError(source.citation_id or "")
            document_id = citation["document_id"]

        active = conn.execute(
            """
            select 1 from qa_deletion_fences
            where user_id=?
              and (
                status in ('queued','running')
                or (status='failed' and attempt_count < 3)
              )
              and (
                (target_type='conversation' and target_id=?)
                or (target_type='document' and target_id=? and exists (
                    select 1 from qa_conversation_documents
                    where user_id=? and conversation_id=? and document_id=?
                ))
              )
            limit 1
            """,
            (
                user_id,
                source.qa_thread_id,
                document_id,
                user_id,
                source.qa_thread_id,
                document_id,
            ),
        ).fetchone()
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
        conn.execute("delete from notes_fts where note_id=? and user_id=?", (note_id, user_id))
        conn.execute(
            "insert into notes_fts (note_id,user_id,body_markdown,concept,tags_text) values (?,?,?,?,?)",
            (
                note_id,
                user_id,
                body,
                concept or "",
                " ".join(normalize_tag(tag)[0] for tag in tags),
            ),
        )

    @staticmethod
    def _enqueue(
        conn: sqlite3.Connection,
        user_id: str,
        note_id: str,
        version: int,
        operation: str,
        timestamp: str,
    ) -> None:
        conn.execute(
            """
            insert or ignore into note_projection_tasks (
                id,user_id,note_id,note_version,operation,status,attempt_count,
                available_at,created_at
            ) values (?,?,?,?,?,'queued',0,?,?)
            """,
            (str(uuid4()), user_id, note_id, version, operation, timestamp, timestamp),
        )


def scrub_sources_in_transaction(
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
    rows = conn.execute(
        f"""
        select distinct note_id from note_sources
        where user_id=? and {field}=? and source_deleted_at is null
        """,
        (user_id, value),
    ).fetchall()
    for row in rows:
        note_id = row["note_id"]
        conn.execute(
            f"""
            update note_sources set qa_thread_id=null,qa_message_id=null,citation_id=null,
                document_id=null,locator_json=null,title_snapshot=null,excerpt_snapshot=null,
                source_deleted_at=?
            where user_id=? and note_id=? and {field}=? and source_deleted_at is null
            """,
            (deleted_at, user_id, note_id, value),
        )
        note = conn.execute(
            "select version from notes where id=? and user_id=? and deleted_at is null",
            (note_id, user_id),
        ).fetchone()
        if note is None:
            continue
        version = note["version"] + 1
        conn.execute(
            "update notes set version=?,updated_at=?,projection_state='pending' where id=? and user_id=?",
            (version, deleted_at, note_id, user_id),
        )
        NoteRepository._enqueue(conn, user_id, note_id, version, "upsert", deleted_at)
    return len(rows)


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
