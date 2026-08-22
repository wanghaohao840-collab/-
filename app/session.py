from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock

from app.auth import AuthService
from app.runtime import UserRuntime, UserRuntimeRegistry
from app.storage import UserStorage
from assistants.pdf_learning_assistant import PDFLearningAssistant


class InvalidSessionError(ValueError):
    """Raised when a session token is missing, expired, or logged out."""


class InvalidCsrfTokenError(ValueError):
    """Raised when a state-changing request has no valid CSRF token."""


@dataclass
class UserSession:
    token: str
    csrf_token: str
    user_id: str
    username: str
    runtime: UserRuntime
    assistant: PDFLearningAssistant
    last_accessed_at: datetime


class SessionRegistry:
    def __init__(
        self,
        db_path: Path | str,
        storage: UserStorage,
        idle_timeout: timedelta = timedelta(hours=12),
        max_sessions: int = 128,
    ):
        self.auth = AuthService(db_path)
        self.storage = storage
        self.runtime_registry = UserRuntimeRegistry(db_path=db_path, storage=storage)
        self.idle_timeout = idle_timeout
        self.max_sessions = max_sessions
        self._sessions: dict[str, UserSession] = {}
        self._lock = RLock()

    def register(self, username: str, password: str) -> str:
        user = self.auth.register(username, password)
        return self._create_session(user.id, user.username)

    def login(self, username: str, password: str) -> str:
        user = self.auth.authenticate(username, password)
        return self._create_session(user.id, user.username)

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            session = self._sessions.pop(token, None)
        if session is not None:
            session.assistant.close()
            self.runtime_registry.release_session(session.user_id)

    def get_session(self, token: str | None) -> UserSession:
        if not token:
            raise InvalidSessionError("Please log in first")

        with self._lock:
            self._cleanup_expired_locked()
            session = self._sessions.get(token)
            if session is None:
                raise InvalidSessionError("Session expired or logged out")
            session.last_accessed_at = self._now()
            return session

    def get_assistant(self, token: str | None) -> PDFLearningAssistant:
        return self.get_session(token).assistant

    def validate_csrf(self, token: str | None, csrf_token: str | None) -> UserSession:
        session = self.get_session(token)
        if not csrf_token or not secrets.compare_digest(session.csrf_token, csrf_token):
            raise InvalidCsrfTokenError("Invalid CSRF token")
        return session

    def clear_document_selection(self, user_id: str, document_id: str) -> int:
        """Clear one deleted document from this user's active sessions."""

        cleared = 0
        with self._lock:
            for session in self._sessions.values():
                if (
                    session.user_id == user_id
                    and session.assistant.current_document_id == document_id
                ):
                    session.assistant.current_document_id = None
                    session.assistant.current_document = None
                    cleared += 1
        return cleared

    def _create_session(self, user_id: str, username: str) -> str:
        with self._lock:
            self._cleanup_expired_locked()
            if len(self._sessions) >= self.max_sessions:
                raise InvalidSessionError("Too many active sessions")

            token = secrets.token_urlsafe(32)
            csrf_token = secrets.token_urlsafe(32)
            runtime = self.runtime_registry.acquire_session(user_id)
            try:
                assistant = PDFLearningAssistant(
                    user_id=user_id, runtime_dir=runtime.paths.root, runtime=runtime
                )
            except Exception:
                self.runtime_registry.release_session(user_id)
                raise
            self._sessions[token] = UserSession(
                token=token,
                csrf_token=csrf_token,
                user_id=user_id,
                username=username,
                runtime=runtime,
                assistant=assistant,
                last_accessed_at=self._now(),
            )
            return token

    def _cleanup_expired_locked(self) -> None:
        now = self._now()
        expired = [
            token
            for token, session in self._sessions.items()
            if now - session.last_accessed_at >= self.idle_timeout
        ]
        for token in expired:
            session = self._sessions.pop(token)
            session.assistant.close()
            self.runtime_registry.release_session(session.user_id)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
