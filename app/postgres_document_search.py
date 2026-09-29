"""Opt-in authenticated document search over shared PostgreSQL and published RAG.

This bridge is deliberately separate from distributed bootstrap.  It does not
construct a local user runtime or use historical source paths as read authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath
from types import SimpleNamespace
from uuid import UUID

from app.document_library import DocumentLibraryItem, DocumentLibraryService
from app.document_search import DocumentSearchService, DocumentSearchUnavailableError
from app.postgres_sessions import PostgresSessionRepository
from app.postgres_snapshots import PostgresSnapshotRepository
from hello_agents.tools.builtin.rag_tool import RAGTool


class _SnapshotDocumentProjection(DocumentLibraryService):
    """Reuse latest-record projection without consulting an API host's files."""

    def __init__(self) -> None:
        super().__init__(None, None, None)

    def _project_record(self, user_id: str, record: dict) -> DocumentLibraryItem | None:
        document_id = record.get("document_id")
        name = record.get("document_name")
        suffix = record.get("file_suffix")
        path = record.get("document_path")
        if (record.get("user_id", user_id) != user_id
                or not all(isinstance(value, str) and value.strip()
                           for value in (document_id, name, suffix, path))):
            return None
        try:
            if str(UUID(document_id)) != document_id.lower():
                return None
        except (TypeError, ValueError, AttributeError):
            return None
        if (PurePath(name.replace("\\", "/")).name != name
                or PurePath(path.replace("\\", "/")).suffix.lower() != suffix.lower()):
            return None
        loaded_at = record.get("loaded_at")
        try:
            if loaded_at is not None:
                self._parse_loaded_at(loaded_at)
        except (TypeError, ValueError, AttributeError):
            loaded_at = None
        return DocumentLibraryItem(document_id, name, suffix, None, loaded_at)


@dataclass(frozen=True)
class _SearchSession:
    user_id: str
    runtime: object


class _SearchSessions:
    def __init__(self, sessions: PostgresSessionRepository, published_reads,
                 namespace: str) -> None:
        self._sessions = sessions
        self._published_reads = published_reads
        self._namespace = namespace

    def get_session(self, token: str) -> _SearchSession:
        shared = self._sessions.get(token)
        # A new tenant-bound tool for each operation avoids process-local
        # tenant authority and leaves the published head pinned only per action.
        tool = RAGTool(rag_namespace=self._namespace,
                       published_read_factory=self._published_reads,
                       authenticated_tenant_id=shared.user_id)
        return _SearchSession(shared.user_id, SimpleNamespace(rag_tool=tool))


class _SearchDocuments:
    def __init__(self, sessions: PostgresSessionRepository,
                 snapshots: PostgresSnapshotRepository) -> None:
        self._sessions = sessions
        self._snapshots = snapshots
        self._projection = _SnapshotDocumentProjection()

    def list_documents(self, token: str) -> tuple[DocumentLibraryItem, ...]:
        user_id = self._sessions.get(token).user_id
        try:
            with self._snapshots.database.transaction() as cursor:
                # History removal precedes fence completion. Both reads must
                # observe one commit boundary, including during a final scope
                # recheck that races the deleting worker.
                cursor.execute("set transaction isolation level repeatable read read only")
                snapshot = self._snapshots._read(cursor, user_id, "history")
                records = snapshot.data.get("documents", []) if snapshot else []
                projected = self._projection.project_history_documents(user_id, records)
                fenced = {row["target_id"] for row in cursor.execute(
                    "select target_id from qa_deletion_fences "
                    "where user_id=%s and target_type='document' and "
                    "(status in ('queued','running') or "
                    "(status='failed' and attempt_count < 3))", (user_id,)
                ).fetchall()}
        except Exception as exc:
            raise DocumentSearchUnavailableError("document authority unavailable") from exc
        return tuple(item for item in projected if item.document_id not in fenced)


def create_postgres_document_search(
    sessions: PostgresSessionRepository,
    snapshots: PostgresSnapshotRepository,
    published_reads,
    *,
    namespace: str = "documents",
    max_concurrent: int = 4,
) -> DocumentSearchService:
    """Build an isolated read-only service; callers supply trusted dependencies."""
    if sessions.database is not snapshots.database:
        raise ValueError("Session and snapshot repositories must share database authority")
    if not callable(getattr(published_reads, "open_operation", None)):
        raise TypeError("Published RAG read factory is required")
    if not isinstance(namespace, str) or not namespace.strip():
        raise ValueError("RAG namespace is required")
    return DocumentSearchService(
        _SearchSessions(sessions, published_reads, namespace),
        _SearchDocuments(sessions, snapshots), max_concurrent=max_concurrent,
    )
