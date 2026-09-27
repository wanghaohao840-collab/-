"""Database authoritative sessions shared by independent API processes."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.postgres import PostgresDatabase
from app.session import InvalidCsrfTokenError, InvalidSessionError


@dataclass(frozen=True)
class SharedSession:
    token: str = field(repr=False)
    csrf_token: str = field(repr=False)
    user_id: str
    username: str
    last_accessed_at: datetime


class PostgresSessionRepository:
    _CAPACITY_LOCK = 0x4155544853455353

    def __init__(self, database: PostgresDatabase,
                 idle_timeout: timedelta = timedelta(hours=12), max_sessions: int = 128):
        if idle_timeout <= timedelta(0) or max_sessions <= 0:
            raise ValueError("Session timeout and capacity must be positive")
        self.database = database
        self.idle_timeout = idle_timeout
        self.max_sessions = max_sessions

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create(self, user_id: str) -> SharedSession:
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        with self.database.transaction() as cursor:
            cursor.execute("select pg_advisory_xact_lock(%s)", (self._CAPACITY_LOCK,))
            cursor.execute("delete from auth_sessions where expires_at <= clock_timestamp()")
            user = cursor.execute(
                "select username from users where id=%s and status='active'", (user_id,)
            ).fetchone()
            if user is None:
                raise InvalidSessionError("Session expired or logged out")
            count = cursor.execute("select count(*) as count from auth_sessions").fetchone()["count"]
            if count >= self.max_sessions:
                raise InvalidSessionError("Too many active sessions")
            row = cursor.execute("select clock_timestamp() as now").fetchone()
            now = row["now"]
            cursor.execute(
                "insert into auth_sessions "
                "(token_hash,user_id,csrf_token,created_at,last_accessed_at,expires_at) "
                "values (%s,%s,%s,%s,%s,%s)",
                (self._hash(token), user_id, csrf_token, now, now, now + self.idle_timeout),
            )
        return SharedSession(token, csrf_token, user_id, user["username"], now)

    def get(self, token: str | None) -> SharedSession:
        if not token:
            raise InvalidSessionError("Please log in first")
        with self.database.transaction() as cursor:
            row = cursor.execute(
                "select user_id,csrf_token,expires_at from auth_sessions "
                "where token_hash=%s for update", (self._hash(token),)
            ).fetchone()
            # Read the clock only after acquiring the row lock. A waiter must not
            # refresh a token that expired while another transaction held it.
            now = cursor.execute("select clock_timestamp() as now").fetchone()["now"]
            if row is None or row["expires_at"] <= now:
                raise InvalidSessionError("Session expired or logged out")
            user = cursor.execute(
                "select username from users where id=%s and status='active'", (row["user_id"],)
            ).fetchone()
            if user is None:
                raise InvalidSessionError("Session expired or logged out")
            cursor.execute(
                "update auth_sessions set last_accessed_at=%s,expires_at=%s where token_hash=%s",
                (now, now + self.idle_timeout, self._hash(token)),
            )
        return SharedSession(token, row["csrf_token"], row["user_id"], user["username"], now)

    def validate_csrf(self, token: str | None, csrf_token: str | None) -> SharedSession:
        session = self.get(token)
        if not csrf_token or not secrets.compare_digest(session.csrf_token, csrf_token):
            raise InvalidCsrfTokenError("Invalid CSRF token")
        return session

    def delete(self, token: str | None) -> None:
        if not token:
            return
        with self.database.transaction() as cursor:
            cursor.execute("delete from auth_sessions where token_hash=%s", (self._hash(token),))
