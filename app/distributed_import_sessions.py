"""Restricted PostgreSQL sessions for isolated API-role import runtimes."""

from __future__ import annotations

import secrets

from psycopg.errors import LockNotAvailable
from psycopg.pq import TransactionStatus

from app.isolated_import_runtime import IsolatedImportRuntime
from app.postgres_auth import PostgresAuthService
from app.postgres_sessions import PostgresSessionRepository, SharedSession
from app.session import InvalidCsrfTokenError, InvalidSessionError


class UntrustedSessionError(PermissionError):
    def __init__(self) -> None:
        super().__init__("untrusted_session")


class SessionLockUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("session_lock_unavailable")


class DistributedImportSessions:
    def __init__(self, runtime: IsolatedImportRuntime):
        if not isinstance(runtime, IsolatedImportRuntime) or runtime.role != "api":
            raise ValueError("isolated_api_runtime_required")
        self.runtime = runtime
        self.repository = PostgresSessionRepository(runtime.database)
        self._auth = PostgresAuthService(runtime.database)

    def _trusted(self, user_id: str) -> None:
        if user_id not in self.runtime.trusted_user_ids:
            raise UntrustedSessionError()

    def login(self, username: str, password: str) -> str:
        user = self._auth.authenticate(username, password)
        self._trusted(user.id)
        return self.repository.create(user.id).token

    def get_session(self, token: str | None) -> SharedSession:
        session = self.repository.get(token)
        self._trusted(session.user_id)
        return session

    def validate_csrf(self, token: str | None,
                      csrf_token: str | None) -> SharedSession:
        session = self.repository.validate_csrf(token, csrf_token)
        self._trusted(session.user_id)
        return session

    def logout(self, token: str | None) -> None:
        self.get_session(token)
        self.repository.delete(token)

    def recheck_csrf_in_transaction(self, cursor, token: str | None,
                                    csrf_token: str | None, *,
                                    expected_user_id: str) -> None:
        if (cursor.connection.info.transaction_status != TransactionStatus.INTRANS
                or cursor.execute("show transaction_isolation").fetchone()[
                    "transaction_isolation"] != "read committed"):
            raise SessionLockUnavailable()
        if not token:
            raise InvalidSessionError("Session expired or logged out")
        try:
            row = cursor.execute("""select user_id,csrf_token,expires_at
                from auth_sessions where token_hash=%s for update nowait""",
                (self.repository._hash(token),)).fetchone()
        except LockNotAvailable:
            raise SessionLockUnavailable() from None
        now = cursor.execute("select clock_timestamp() as now").fetchone()["now"]
        if (row is None or row["user_id"] != expected_user_id
                or row["expires_at"] <= now):
            raise InvalidSessionError("Session expired or logged out")
        self._trusted(row["user_id"])
        if not csrf_token or not secrets.compare_digest(row["csrf_token"], csrf_token):
            raise InvalidCsrfTokenError("Invalid CSRF token")
