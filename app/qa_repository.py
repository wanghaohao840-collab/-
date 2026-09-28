from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence
from uuid import uuid4

from app.qa_persistence import (
    QaStore, SQLiteQaPersistence, PostgresQaPersistence, _not_fenced_clause,
)
from app.database import connect
from app.qa_models import (
    PendingTurn,
    QaConflictError,
    QaConversation,
    QaConversationAggregate,
    QaConversationDocument,
    QaConversationPage,
    QaDocumentCandidate,
    QaMessage,
    QaMessagePage,
    QaReportTurn,
    QaSource,
    QaSourceDraft,
    QaValidationError,
    conversation_title,
    decode_cursor,
    encode_cursor,
    normalize_question,
    validate_document_candidates,
    validate_mode,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class QaRepository:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self._persistence = SQLiteQaPersistence(self.db_path)

    def create_conversation(
        self,
        user_id: str,
        documents: Sequence[QaDocumentCandidate],
        *,
        origin: str = "product",
        now: str | None = None,
    ) -> QaConversationAggregate:
        if origin not in {"product", "legacy_json", "legacy_gradio"}:
            raise QaValidationError("QA_ORIGIN_INVALID", "unsupported QA origin")
        scope = validate_document_candidates(user_id, documents)
        timestamp = now or _utc_now()
        conversation_id = str(uuid4())
        with self._persistence.write(user_id) as conn:
            fenced_document = conn.selected_documents_fenced(
                (user_id, *(item.document_id for item in scope)), len(scope)
            ).fetchone()
            if fenced_document is not None:
                raise QaValidationError(
                    "QA_DOCUMENT_DELETING", "a selected document is being deleted"
                )
            conn.insert_conversation(
                (
                    conversation_id,
                    user_id,
                    origin,
                    timestamp,
                    timestamp,
                    timestamp,
                )
            )
            conn.insert_documents(
                [
                    (
                        conversation_id,
                        user_id,
                        item.document_id,
                        item.document_name,
                        item.position,
                    )
                    for item in scope
                ]
            )
            created = self._get_conversation(conn, user_id, conversation_id)
            return created

    def list_conversations(
        self,
        user_id: str,
        *,
        cursor: str | None = None,
        limit: int = 20,
    ) -> QaConversationPage:
        page_size = _validated_limit(limit, maximum=100)
        params: list[object] = [user_id]
        if cursor:
            timestamp, conversation_id = decode_cursor(cursor)
            params.extend((timestamp, timestamp, conversation_id))
        params.append(page_size + 1)
        with self._persistence.read() as conn:
            rows = conn.conversation_page(params, bool(cursor)).fetchall()
            has_more = len(rows) > page_size
            rows = rows[:page_size]
            items = tuple(
                self._aggregate_from_row(conn, row)
                for row in rows
            )
        next_cursor = None
        if has_more and items:
            last = items[-1].conversation
            next_cursor = encode_cursor(last.last_message_at, last.id)
        return QaConversationPage(items, next_cursor)

    def get_conversation(
        self, user_id: str, conversation_id: str
    ) -> QaConversationAggregate | None:
        with self._persistence.read() as conn:
            return self._get_conversation(conn, user_id, conversation_id)

    def list_messages(
        self,
        user_id: str,
        conversation_id: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> QaMessagePage:
        page_size = _validated_limit(limit, maximum=200)
        params: list[object] = [user_id, conversation_id]
        if cursor:
            timestamp, message_id = decode_cursor(cursor)
            params.extend((timestamp, timestamp, message_id))
        params.append(page_size + 1)
        with self._persistence.read() as conn:
            rows = conn.message_page(params, bool(cursor)).fetchall()
            has_more = len(rows) > page_size
            rows = rows[:page_size]
            items = tuple(self._message_from_row(conn, row) for row in rows)
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = encode_cursor(last.created_at, last.id)
        return QaMessagePage(items, next_cursor)

    def list_recent_messages(
        self,
        user_id: str,
        conversation_id: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> QaMessagePage:
        page_size = _validated_limit(limit, maximum=200)
        params: list[object] = [user_id, conversation_id]
        if cursor:
            timestamp, message_id = decode_cursor(cursor)
            params.extend((timestamp, timestamp, message_id))
        params.append(page_size + 1)
        with self._persistence.read() as conn:
            rows = conn.recent_message_page(params, bool(cursor)).fetchall()
            has_more = len(rows) > page_size
            page_rows = rows[:page_size]
            items = tuple(
                self._message_from_row(conn, row)
                for row in reversed(page_rows)
            )
        next_cursor = None
        if has_more and page_rows:
            oldest = page_rows[-1]
            next_cursor = encode_cursor(oldest["created_at"], oldest["id"])
        return QaMessagePage(items, next_cursor)

    def get_message(self, user_id: str, message_id: str) -> QaMessage | None:
        with self._persistence.read() as conn:
            row = conn.visible_message((message_id, user_id)).fetchone()
            return self._message_from_row(conn, row) if row is not None else None

    def create_pending_turn(
        self,
        user_id: str,
        conversation_id: str,
        question: str,
        mode: str,
        client_request_id: str,
        *,
        now: str | None = None,
    ) -> PendingTurn:
        timestamp = now or _utc_now()
        with self._persistence.write(user_id) as conn:
            result = self._create_pending_turn_in_connection(
                conn,
                user_id,
                conversation_id,
                question,
                mode,
                client_request_id,
                retry_of_message_id=None,
                timestamp=timestamp,
            )
            return result

    def create_pending_retry(
        self,
        user_id: str,
        conversation_id: str,
        failed_assistant_message_id: str,
        client_request_id: str,
        *,
        now: str | None = None,
    ) -> PendingTurn:
        request_id = str(client_request_id or "").strip()
        if not request_id:
            raise QaValidationError(
                "QA_CLIENT_REQUEST_REQUIRED", "client request id is required"
            )
        timestamp = now or _utc_now()
        with self._persistence.write(user_id) as conn:
            result = self._create_pending_retry_in_connection(
                conn,
                user_id,
                conversation_id,
                failed_assistant_message_id,
                request_id,
                timestamp,
            )
            return result

    def complete_turn(
        self,
        user_id: str,
        assistant_message_id: str,
        expected_version: int,
        answer: str,
        sources: Sequence[QaSourceDraft],
        source_state: str,
        memory_id: str | None,
        *,
        now: str | None = None,
    ) -> bool:
        if source_state not in {"available", "none", "legacy_unavailable"}:
            raise QaValidationError("QA_SOURCE_STATE_INVALID", "invalid source state")
        source_list = tuple(sources)
        timestamp = now or _utc_now()
        with self._persistence.write(user_id) as conn:
            row = conn.pending_for_completion((assistant_message_id, user_id, expected_version)).fetchone()
            if row is None:
                return False
            conversation_id = row["conversation_id"]
            self._validate_source_scope(
                conn, user_id, conversation_id, source_list
            )
            memory_status = "completed" if memory_id else "pending"
            updated = conn.complete_message(
                (
                    str(answer),
                    source_state,
                    memory_id,
                    memory_status,
                    timestamp,
                    timestamp,
                    assistant_message_id,
                    user_id,
                    expected_version,
                )
            )
            if not updated.rowcount:
                return False
            conn.insert_sources(
                [
                    (
                        str(uuid4()),
                        assistant_message_id,
                        conversation_id,
                        user_id,
                        position,
                        source.citation_id,
                        source.document_id,
                        source.document_name,
                        source.page_number,
                        source.section,
                        source.excerpt,
                        source.reference,
                        int(source.truncated),
                        source.source_type,
                    )
                    for position, source in enumerate(source_list)
                ]
            )
            self._touch_conversation(conn, user_id, conversation_id, timestamp)
            return True

    def fail_turn(
        self,
        user_id: str,
        assistant_message_id: str,
        expected_version: int,
        error_code: str,
        trace_id: str | None,
        *,
        now: str | None = None,
    ) -> bool:
        return self._finish_pending(
            user_id,
            assistant_message_id,
            expected_version,
            "failed",
            error_code,
            trace_id,
            now=now,
        )

    def cancel_turn(
        self,
        user_id: str,
        assistant_message_id: str,
        expected_version: int,
        *,
        now: str | None = None,
    ) -> bool:
        return self._finish_pending(
            user_id,
            assistant_message_id,
            expected_version,
            "cancelled",
            None,
            None,
            now=now,
        )

    def recover_interrupted_questions(
        self,
        error_code: str = "QA_REQUEST_INTERRUPTED",
        *,
        now: str | None = None,
    ) -> int:
        timestamp = now or _utc_now()
        with connect(self.db_path) as conn:
            updated = conn.execute(
                """
                update qa_messages
                set status = 'failed', safe_error_code = ?, trace_id = null,
                    memory_sync_status = 'not_required', version = version + 1,
                    updated_at = ?, completed_at = ?
                where role = 'assistant' and status = 'pending'
                  and not exists (
                      select 1 from qa_jobs
                      where qa_jobs.assistant_message_id = qa_messages.id
                        and qa_jobs.user_id = qa_messages.user_id
                        and qa_jobs.status in ('queued','running')
                  )
                """,
                (error_code, timestamp, timestamp),
            )
            return updated.rowcount

    def hard_delete_conversation(
        self, user_id: str, conversation_id: str
    ) -> bool:
        """Physical deletion seam; the deletion workflow owns authorization and fences."""
        with self._persistence.write(user_id) as conn:
            deleted = conn.delete_conversation((conversation_id, user_id))
            return bool(deleted.rowcount)

    def list_completed_turns_for_report(
        self, user_id: str
    ) -> tuple[QaReportTurn, ...]:
        with self._persistence.read() as conn:
            rows = conn.report_turns((user_id,)).fetchall()
            turns: list[QaReportTurn] = []
            for row in rows:
                documents = conn.report_documents((user_id, row["conversation_id"])).fetchall()
                turns.append(
                    QaReportTurn(
                        question=row["question"],
                        answer=row["answer"],
                        document_ids=tuple(item["document_id"] for item in documents),
                        document_names=tuple(item["document_name"] for item in documents),
                        mode=row["mode"] or "auto",
                        asked_at=row["asked_at"],
                    )
                )
            return tuple(turns)

    def list_recent_completed_turns(
        self, user_id: str, *, limit: int = 5
    ) -> tuple[QaReportTurn, ...]:
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        with self._persistence.read() as conn:
            rows = conn.recent_report_turns((user_id, limit)).fetchall()
            turns = []
            for row in rows:
                documents = conn.report_documents((user_id, row["conversation_id"])).fetchall()
                turns.append(QaReportTurn(
                    question=row["question"], answer=row["answer"],
                    document_ids=tuple(item["document_id"] for item in documents),
                    document_names=tuple(item["document_name"] for item in documents),
                    mode=row["mode"] or "auto", asked_at=row["asked_at"],
                ))
        return tuple(turns)

    def count_completed_turns(self, user_id: str) -> int:
        with self._persistence.read() as conn:
            row = conn.completed_count((user_id,)).fetchone()
        return int(row["count"])

    def list_completed_activity_dates(
        self, user_id: str, *, since: str
    ) -> tuple[str, ...]:
        with self._persistence.read() as conn:
            rows = conn.activity_dates((user_id, since)).fetchall()
        return tuple(row["occurred_at"] for row in rows if row["occurred_at"])

    def update_rolling_summary(
        self,
        user_id: str,
        conversation_id: str,
        expected_version: int,
        expected_summary_version: int,
        summary: str,
        through_message_id: str,
        *,
        now: str | None = None,
    ) -> bool:
        timestamp = now or _utc_now()
        with self._persistence.write(user_id) as conn:
            updated = conn.update_summary(
                (
                    summary,
                    through_message_id,
                    timestamp,
                    conversation_id,
                    user_id,
                    expected_version,
                    expected_summary_version,
                    through_message_id,
                )
            )
            return bool(updated.rowcount)

    def claim_next_memory_sync(
        self,
        worker_id: str,
        *,
        lease_seconds: int,
        now: str | None = None,
    ) -> QaMessage | None:
        if lease_seconds < 1:
            raise QaValidationError(
                "QA_MEMORY_LEASE_INVALID", "lease seconds must be positive"
            )
        timestamp = now or _utc_now()
        expires_at = _add_seconds(timestamp, lease_seconds)
        conn = connect(self.db_path)
        try:
            conn.execute("begin immediate")
            candidates = conn.execute(
                f"""
                select * from qa_messages
                where role = 'assistant' and status = 'completed'
                  and exists (
                      select 1 from qa_conversations
                      where qa_conversations.id = qa_messages.conversation_id
                        and qa_conversations.user_id = qa_messages.user_id
                        and {_not_fenced_clause('qa_conversations')}
                  )
                  and (
                      memory_sync_status in ('pending','failed')
                      or (
                          memory_sync_status = 'running'
                          and memory_sync_lease_expires_at <= ?
                      )
                  )
                order by completed_at, id
                """,
                (timestamp,),
            ).fetchall()
            for candidate in candidates:
                updated = conn.execute(
                    f"""
                    update qa_messages
                    set memory_sync_status = 'running',
                        memory_sync_lease_owner = ?,
                        memory_sync_lease_expires_at = ?,
                        memory_sync_attempt_count = memory_sync_attempt_count + 1,
                        version = version + 1, updated_at = ?
                    where id = ? and user_id = ? and version = ?
                      and role = 'assistant' and status = 'completed'
                      and exists (
                          select 1 from qa_conversations
                          where qa_conversations.id = qa_messages.conversation_id
                            and qa_conversations.user_id = qa_messages.user_id
                            and {_not_fenced_clause('qa_conversations')}
                      )
                      and (
                          memory_sync_status in ('pending','failed')
                          or (
                              memory_sync_status = 'running'
                              and memory_sync_lease_expires_at <= ?
                          )
                      )
                    """,
                    (
                        worker_id,
                        expires_at,
                        timestamp,
                        candidate["id"],
                        candidate["user_id"],
                        candidate["version"],
                        timestamp,
                    ),
                )
                if updated.rowcount:
                    row = conn.execute(
                        "select * from qa_messages where id = ?",
                        (candidate["id"],),
                    ).fetchone()
                    result = self._message_from_row(conn, row)
                    conn.commit()
                    return result
            conn.commit()
            return None
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def heartbeat_memory_sync(
        self,
        message_id: str,
        worker_id: str,
        *,
        lease_seconds: int,
        now: str | None = None,
    ) -> bool:
        if lease_seconds < 1:
            return False
        timestamp = now or _utc_now()
        expires_at = _add_seconds(timestamp, lease_seconds)
        with connect(self.db_path) as conn:
            updated = conn.execute(
                f"""
                update qa_messages
                set memory_sync_lease_expires_at = ?, updated_at = ?
                where id = ? and memory_sync_status = 'running'
                  and memory_sync_lease_owner = ?
                  and memory_sync_lease_expires_at > ?
                  and exists (
                      select 1 from qa_conversations
                      where qa_conversations.id = qa_messages.conversation_id
                        and qa_conversations.user_id = qa_messages.user_id
                        and {_not_fenced_clause('qa_conversations')}
                  )
                """,
                (expires_at, timestamp, message_id, worker_id, timestamp),
            )
            return bool(updated.rowcount)

    def complete_memory_sync(
        self,
        user_id: str,
        message_id: str,
        worker_id: str,
        memory_id: str,
        expected_version: int,
        *,
        now: str | None = None,
    ) -> bool:
        return self._finish_memory_sync(
            user_id,
            message_id,
            worker_id,
            expected_version,
            "completed",
            memory_id,
            now=now,
        )

    def fail_memory_sync(
        self,
        user_id: str,
        message_id: str,
        worker_id: str,
        expected_version: int,
        safe_error_code: str,
        *,
        now: str | None = None,
    ) -> bool:
        return self._finish_memory_sync(
            user_id,
            message_id,
            worker_id,
            expected_version,
            "failed",
            None,
            now=now,
        )

    def _finish_memory_sync(
        self,
        user_id: str,
        message_id: str,
        worker_id: str,
        expected_version: int,
        status: str,
        memory_id: str | None,
        *,
        now: str | None,
    ) -> bool:
        timestamp = now or _utc_now()
        with connect(self.db_path) as conn:
            updated = conn.execute(
                f"""
                update qa_messages
                set memory_sync_status = ?, memory_id = ?,
                    memory_sync_lease_owner = null,
                    memory_sync_lease_expires_at = null,
                    version = version + 1, updated_at = ?
                where id = ? and user_id = ? and role = 'assistant'
                  and status = 'completed' and memory_sync_status = 'running'
                  and memory_sync_lease_owner = ?
                  and memory_sync_lease_expires_at > ? and version = ?
                  and exists (
                      select 1 from qa_conversations
                      where qa_conversations.id = qa_messages.conversation_id
                        and qa_conversations.user_id = qa_messages.user_id
                        and {_not_fenced_clause('qa_conversations')}
                  )
                """,
                (
                    status,
                    memory_id,
                    timestamp,
                    message_id,
                    user_id,
                    worker_id,
                    timestamp,
                    expected_version,
                ),
            )
            return bool(updated.rowcount)

    def _create_pending_turn_in_connection(
        self,
        conn: sqlite3.Connection,
        user_id: str,
        conversation_id: str,
        question: str,
        mode: str,
        client_request_id: str,
        *,
        retry_of_message_id: str | None,
        timestamp: str,
    ) -> PendingTurn:
        conn = self._persistence.caller_owned(conn, user_id)
        normalized_question = normalize_question(question)
        request_id = str(client_request_id or "").strip()
        if not request_id:
            raise QaValidationError(
                "QA_CLIENT_REQUEST_REQUIRED", "client request id is required"
            )
        conversation = conn.visible_conversation((conversation_id, user_id)).fetchone()
        if conversation is None:
            raise QaValidationError("QA_CONVERSATION_NOT_FOUND", "conversation not found")
        document_rows = conn.ordered_document_ids((conversation_id, user_id)).fetchall()
        normalized_mode = validate_mode(mode, document_rows)

        existing_user = conn.request_message((user_id, conversation_id, request_id)).fetchone()
        if existing_user is not None:
            assistant = conn.turn_assistant((user_id, conversation_id, existing_user["turn_id"])).fetchone()
            if assistant is None:
                raise QaConflictError("QA_TURN_INCOMPLETE")
            return PendingTurn(
                self._message_from_row(conn, existing_user),
                self._message_from_row(conn, assistant),
                True,
            )

        if retry_of_message_id is not None:
            retried = conn.failed_assistant_exists((retry_of_message_id, user_id, conversation_id)).fetchone()
            if retried is None:
                raise QaValidationError(
                    "QA_RETRY_NOT_ALLOWED", "retry target is not a failed answer"
                )

        busy = conn.pending_exists((user_id, conversation_id)).fetchone()
        if busy is not None:
            raise QaConflictError("QA_CONVERSATION_BUSY")

        turn_id = str(uuid4())
        # The page cursor deliberately uses (created_at, id). Both rows share a
        # transaction timestamp, so assigning the lower ID to the user keeps
        # the pair in conversational order without another ordering column.
        user_message_id, assistant_message_id = sorted(
            (str(uuid4()), str(uuid4()))
        )
        conn.insert_user_message(
            (
                user_message_id,
                conversation_id,
                user_id,
                turn_id,
                normalized_mode,
                normalized_question,
                request_id,
                timestamp,
                timestamp,
                timestamp,
            )
        )
        conn.insert_assistant_message(
            (
                assistant_message_id,
                conversation_id,
                user_id,
                turn_id,
                normalized_mode,
                retry_of_message_id,
                timestamp,
                timestamp,
            )
        )
        prior_count = conn.prior_message_count((conversation_id, user_id, user_message_id, assistant_message_id)).fetchone()["count"]
        title = conversation_title(normalized_question) if prior_count == 0 else None
        conn.update_conversation_title((title, timestamp, timestamp, conversation_id, user_id))
        user_row = conn.message((user_message_id, user_id)).fetchone()
        assistant_row = conn.message((assistant_message_id, user_id)).fetchone()
        return PendingTurn(
            self._message_from_row(conn, user_row),
            self._message_from_row(conn, assistant_row),
            False,
        )

    def _create_pending_retry_in_connection(
        self,
        conn: sqlite3.Connection,
        user_id: str,
        conversation_id: str,
        failed_assistant_message_id: str,
        client_request_id: str,
        timestamp: str,
    ) -> PendingTurn:
        conn = self._persistence.caller_owned(conn, user_id)
        conversation = conn.visible_conversation((conversation_id, user_id)).fetchone()
        if conversation is None:
            raise QaValidationError("QA_CONVERSATION_NOT_FOUND", "conversation not found")

        request = conn.retry_request((user_id, conversation_id, client_request_id)).fetchone()
        if request is not None:
            if request["failed_assistant_message_id"] != failed_assistant_message_id:
                raise QaValidationError(
                    "QA_CLIENT_REQUEST_REUSED", "client request id is already in use"
                )
            return self._pending_retry_from_ids(
                conn,
                user_id,
                conversation_id,
                request["failed_assistant_message_id"],
                request["assistant_message_id"],
            )

        request = conn.retry_for_target((user_id, conversation_id, failed_assistant_message_id)).fetchone()
        if request is not None:
            return self._pending_retry_from_ids(
                conn,
                user_id,
                conversation_id,
                request["failed_assistant_message_id"],
                request["assistant_message_id"],
            )

        failed = conn.failed_assistant((failed_assistant_message_id, user_id, conversation_id)).fetchone()
        if failed is None:
            raise QaValidationError(
                "QA_RETRY_NOT_ALLOWED", "retry target is not a failed answer"
            )
        paired_user = conn.paired_user((user_id, conversation_id, failed["turn_id"])).fetchone()
        if paired_user is None:
            raise QaValidationError(
                "QA_RETRY_NOT_ALLOWED", "retry target has no user message"
            )

        # Databases created before the retry ledger may already contain a
        # linked assistant. Adopt one canonical child without rewriting history.
        legacy_retry = conn.legacy_retry((user_id, conversation_id, failed_assistant_message_id)).fetchone()
        if legacy_retry is not None:
            conn.insert_retry_request(
                (
                    user_id,
                    conversation_id,
                    failed_assistant_message_id,
                    legacy_retry["id"],
                    client_request_id,
                    timestamp,
                )
            )
            return PendingTurn(
                self._message_from_row(conn, paired_user),
                self._message_from_row(conn, legacy_retry),
                True,
            )

        busy = conn.pending_exists((user_id, conversation_id)).fetchone()
        if busy is not None:
            raise QaConflictError("QA_CONVERSATION_BUSY")

        assistant_message_id = str(uuid4())
        conn.insert_assistant_message(
            (
                assistant_message_id,
                conversation_id,
                user_id,
                failed["turn_id"],
                failed["mode"],
                failed_assistant_message_id,
                timestamp,
                timestamp,
            )
        )
        conn.insert_retry_request(
            (
                user_id,
                conversation_id,
                failed_assistant_message_id,
                assistant_message_id,
                client_request_id,
                timestamp,
            )
        )
        conn.touch_conversation((timestamp, timestamp, conversation_id, user_id))
        assistant = conn.message((assistant_message_id, user_id)).fetchone()
        return PendingTurn(
            self._message_from_row(conn, paired_user),
            self._message_from_row(conn, assistant),
            False,
        )

    def _pending_retry_from_ids(
        self,
        conn: sqlite3.Connection,
        user_id: str,
        conversation_id: str,
        failed_assistant_message_id: str,
        assistant_message_id: str,
    ) -> PendingTurn:
        conn = _domain_store(conn)
        failed = conn.retry_target_turn((failed_assistant_message_id, user_id, conversation_id)).fetchone()
        assistant = conn.conversation_message((assistant_message_id, user_id, conversation_id)).fetchone()
        if failed is None or assistant is None:
            raise QaConflictError("QA_RETRY_INCOMPLETE")
        paired_user = conn.paired_user((user_id, conversation_id, failed["turn_id"])).fetchone()
        if paired_user is None:
            raise QaConflictError("QA_RETRY_INCOMPLETE")
        return PendingTurn(
            self._message_from_row(conn, paired_user),
            self._message_from_row(conn, assistant),
            True,
        )

    def _finish_pending(
        self,
        user_id: str,
        assistant_message_id: str,
        expected_version: int,
        status: str,
        error_code: str | None,
        trace_id: str | None,
        *,
        now: str | None,
    ) -> bool:
        timestamp = now or _utc_now()
        with self._persistence.write(user_id) as conn:
            row = conn.pending_for_completion((assistant_message_id, user_id, expected_version)).fetchone()
            if row is None:
                return False
            updated = conn.finish_message(
                (
                    status,
                    error_code,
                    trace_id,
                    timestamp,
                    timestamp,
                    assistant_message_id,
                    user_id,
                    expected_version,
                )
            )
            changed = bool(updated.rowcount)
            if changed:
                self._touch_conversation(
                    conn, user_id, row["conversation_id"], timestamp
                )
            return changed

    def _validate_source_scope(
        self,
        conn: sqlite3.Connection,
        user_id: str,
        conversation_id: str,
        sources: Sequence[QaSourceDraft],
    ) -> None:
        conn = _domain_store(conn)
        scope = {
            row["document_id"]
            for row in conn.document_ids((user_id, conversation_id))
        }
        if any(source.document_id not in scope for source in sources):
            raise QaValidationError(
                "QA_SOURCE_OUT_OF_SCOPE", "source document is outside the conversation"
            )

    def _touch_conversation(
        self,
        conn: sqlite3.Connection,
        user_id: str,
        conversation_id: str,
        timestamp: str,
    ) -> None:
        conn = _domain_store(conn)
        conn.touch_conversation((timestamp, timestamp, conversation_id, user_id))

    def _get_conversation(
        self, conn: sqlite3.Connection, user_id: str, conversation_id: str
    ) -> QaConversationAggregate | None:
        conn = _domain_store(conn)
        row = conn.visible_conversation((conversation_id, user_id)).fetchone()
        return self._aggregate_from_row(conn, row) if row is not None else None

    def _aggregate_from_row(
        self, conn: sqlite3.Connection, row: sqlite3.Row
    ) -> QaConversationAggregate:
        conn = _domain_store(conn)
        documents = conn.documents((row["id"], row["user_id"])).fetchall()
        return QaConversationAggregate(
            _conversation_from_row(row),
            tuple(_document_from_row(document) for document in documents),
        )

    def _message_from_row(
        self, conn: sqlite3.Connection, row: sqlite3.Row
    ) -> QaMessage:
        conn = _domain_store(conn)
        source_rows = conn.sources((row["id"], row["user_id"])).fetchall()
        return _message_from_row(row, tuple(_source_from_row(item) for item in source_rows))


class PostgresQaRepository(QaRepository):
    """Shared conversation state. Worker lifecycle requires the durable lease adapter."""

    def __init__(self, database):
        self._persistence = PostgresQaPersistence(database)

    def create_pending_turn_in_transaction(self, cursor, user_id, conversation_id,
                                           question, mode, client_request_id, *, now=None):
        """Use an active caller transaction; lock user first and leave commit to caller."""
        return self._create_pending_turn_in_connection(
            cursor, user_id, conversation_id, question, mode, client_request_id,
            retry_of_message_id=None, timestamp=now or _utc_now())

    def create_pending_retry_in_transaction(self, cursor, user_id, conversation_id,
                                            failed_assistant_message_id, client_request_id, *, now=None):
        """Use an active caller transaction; never commit or roll it back here."""
        request_id = str(client_request_id or "").strip()
        if not request_id:
            raise QaValidationError("QA_CLIENT_REQUEST_REQUIRED", "client request id is required")
        return self._create_pending_retry_in_connection(
            cursor, user_id, conversation_id, failed_assistant_message_id,
            request_id, now or _utc_now())

    def _worker_lifecycle_unavailable(self, *args, **kwargs):
        raise NotImplementedError("PostgreSQL QA worker lifecycle requires durable lease integration")

    claim_next_memory_sync = _worker_lifecycle_unavailable
    heartbeat_memory_sync = _worker_lifecycle_unavailable
    complete_memory_sync = _worker_lifecycle_unavailable
    fail_memory_sync = _worker_lifecycle_unavailable
    recover_interrupted_questions = _worker_lifecycle_unavailable
    _finish_memory_sync = _worker_lifecycle_unavailable


def _domain_store(conn):
    # Local job/deletion/Worker callers historically pass a SQLite transaction.
    if isinstance(conn, QaStore):
        return conn
    if isinstance(conn, sqlite3.Connection):
        return QaStore(conn)
    raise TypeError("QA domain store or SQLite connection required")


def _validated_limit(limit: int, *, maximum: int) -> int:
    if not 1 <= limit <= maximum:
        raise QaValidationError(
            "QA_PAGE_LIMIT_INVALID", f"limit must be between 1 and {maximum}"
        )
    return limit


def _add_seconds(timestamp: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed + timedelta(seconds=seconds)).astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _conversation_from_row(row: sqlite3.Row) -> QaConversation:
    return QaConversation(
        id=row["id"],
        user_id=row["user_id"],
        title=row["title"],
        origin=row["origin"],
        rolling_summary=row["rolling_summary"],
        summary_through_message_id=row["summary_through_message_id"],
        summary_version=row["summary_version"],
        version=row["version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_message_at=row["last_message_at"],
    )


def _document_from_row(row: sqlite3.Row) -> QaConversationDocument:
    return QaConversationDocument(
        conversation_id=row["conversation_id"],
        user_id=row["user_id"],
        document_id=row["document_id"],
        document_name=row["document_name"],
        position=row["position"],
    )


def _message_from_row(row: sqlite3.Row, sources: tuple[QaSource, ...]) -> QaMessage:
    return QaMessage(
        id=row["id"],
        conversation_id=row["conversation_id"],
        user_id=row["user_id"],
        turn_id=row["turn_id"],
        role=row["role"],
        status=row["status"],
        mode=row["mode"],
        content=row["content"],
        source_state=row["source_state"],
        client_request_id=row["client_request_id"],
        retry_of_message_id=row["retry_of_message_id"],
        memory_id=row["memory_id"],
        memory_sync_status=row["memory_sync_status"],
        memory_sync_attempt_count=row["memory_sync_attempt_count"],
        memory_sync_lease_owner=row["memory_sync_lease_owner"],
        memory_sync_lease_expires_at=row["memory_sync_lease_expires_at"],
        safe_error_code=row["safe_error_code"],
        trace_id=row["trace_id"],
        version=row["version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
        sources=sources,
    )


def _source_from_row(row: sqlite3.Row) -> QaSource:
    return QaSource(
        id=row["id"],
        assistant_message_id=row["assistant_message_id"],
        conversation_id=row["conversation_id"],
        user_id=row["user_id"],
        position=row["position"],
        citation_id=row["citation_id"],
        document_id=row["document_id"],
        document_name=row["document_name"],
        page_number=row["page_number"],
        section=row["section"],
        excerpt=row["excerpt"],
        reference=row["reference"],
        truncated=bool(row["truncated"]),
        source_type=row["source_type"],
    )
