"""Restricted HTTP composition for the isolated API-role import runtime."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from threading import Condition, Event, Thread
import time
from typing import Iterator
from urllib.parse import quote

from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException

from api.config import ApiConfig
from api.schemas.auth import Credentials, SessionResponse
from api.schemas.imports import _task_response, import_batch_response
from app.auth import AuthError
from app.distributed_import_service import DistributedImportError, DistributedImportService
from app.distributed_import_sessions import DistributedImportSessions, UntrustedSessionError
from app.import_uploads import ImportUpload
from app.isolated_import_provider import (
    IsolatedProviderConfigurationError, open_isolated_providers,
)
from app.isolated_import_runtime import (
    IsolatedImportConfigurationError, IsolatedImportSettings, open_isolated_runtime,
)
from app.session import InvalidCsrfTokenError, InvalidSessionError


class _StartupFailure(RuntimeError):
    def __init__(self, code: str = "isolated_import_unavailable") -> None:
        self.code = code
        super().__init__(code)


def _error(status: int, code: str, retryable: bool = False) -> JSONResponse:
    return JSONResponse({"detail": {"code": code, "retryable": retryable}}, status_code=status)


async def _observe(event: Event, deadline: float) -> bool:
    while not event.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return event.is_set()
        await asyncio.sleep(min(0.01, remaining))
    return True


class _Owner:
    """Own one opener, every active synchronous borrower, and one eventual closer."""

    def __init__(self, settings: IsolatedImportSettings) -> None:
        self.settings = settings
        self._gate = Condition()
        self.open_done = Event()
        self.cleanup_done = Event()
        self.open_thread: Thread | None = None
        self.cleanup_thread: Thread | None = None
        self.providers = None
        self.runtime = None
        self.sessions = None
        self.imports = None
        self.accepting = False
        self.closing = False
        self.borrowers = 0
        self.stop_deadline: float | None = None
        self.startup_code: str | None = None
        self.open_error: BaseException | None = None
        self.cleanup_code: str | None = None
        self.cleanup_error: BaseException | None = None
        self.clean = False

    def start(self) -> None:
        with self._gate:
            if self.open_thread is not None:
                raise _StartupFailure()
            self.open_thread = Thread(target=self._open, name="isolated-api-open", daemon=False)
            thread = self.open_thread
        try:
            thread.start()
        except BaseException as error:
            with self._gate:
                self.open_error = error
                self.startup_code = "isolated_import_unavailable"
            self.open_done.set()
            raise _StartupFailure() from None

    def _open(self) -> None:
        try:
            try:
                providers = open_isolated_providers(self.settings)
            except IsolatedProviderConfigurationError:
                providers = None
            if providers is not None:
                with self._gate:
                    self.providers = providers
            runtime = open_isolated_runtime(self.settings,
                provider_probe=providers.probe if providers is not None else None)
            with self._gate:
                self.runtime = runtime
            sessions = DistributedImportSessions(runtime)
            imports = DistributedImportService(runtime, sessions)
            with self._gate:
                if not self.closing:
                    self.sessions = sessions
                    self.imports = imports
                    self.accepting = True
        except IsolatedImportConfigurationError as error:
            with self._gate:
                self.open_error = error
                self.startup_code = error.code
        except BaseException as error:
            with self._gate:
                self.open_error = error
                self.startup_code = "isolated_import_unavailable"
        finally:
            self.open_done.set()

    def begin_close(self) -> float:
        thread = None
        with self._gate:
            if not self.closing:
                self.closing = True
                self.accepting = False
                self.stop_deadline = time.monotonic() + self.settings.stop_wait_seconds
                self.cleanup_thread = Thread(target=self._cleanup,
                    name="isolated-api-close", daemon=False)
                thread = self.cleanup_thread
            assert self.stop_deadline is not None
            deadline = self.stop_deadline
        if thread is not None:
            thread.start()
        return deadline

    def _cleanup(self) -> None:
        provider_ok = True
        runtime_ok = True
        try:
            self.open_done.wait()
            with self._gate:
                while self.borrowers:
                    self._gate.wait()
                providers, runtime = self.providers, self.runtime
            if providers is not None:
                try:
                    while True:
                        active = providers.active_threads
                        if not active:
                            break
                        for probe in active:
                            probe.join(0.05)
                    remaining = max(0.0, (self.stop_deadline or 0) - time.monotonic())
                    provider_ok = providers.close(timeout_seconds=remaining) is True
                except BaseException as error:
                    self.cleanup_error = error
                    provider_ok = False
            if runtime is not None:
                try:
                    runtime.close()
                except BaseException as error:
                    self.cleanup_error = error
                    runtime_ok = False
        except BaseException as error:
            self.cleanup_error = error
            provider_ok = False
            runtime_ok = False
        finally:
            with self._gate:
                self.clean = provider_ok and runtime_ok
                self.cleanup_code = "clean" if self.clean else "cleanup_unclean"
                self.sessions = None
                self.imports = None
            self.cleanup_done.set()

    @contextmanager
    def borrow(self) -> Iterator[tuple[DistributedImportSessions, DistributedImportService]]:
        with self._gate:
            if not self.accepting or self.closing or self.sessions is None or self.imports is None:
                raise DistributedImportError("isolated_import_unavailable", 503, True)
            self.borrowers += 1
            sessions, imports = self.sessions, self.imports
        try:
            yield sessions, imports
        finally:
            with self._gate:
                self.borrowers -= 1
                self._gate.notify_all()

    def status(self) -> dict[str, object]:
        with self._gate:
            providers = self.providers
            state = {"accepting": self.accepting, "closing": self.closing,
                    "borrowers": self.borrowers, "opening": not self.open_done.is_set(),
                    "cleanup_done": self.cleanup_done.is_set(), "clean": self.clean,
                    "code": self.cleanup_code or self.startup_code or
                            ("cleanup_pending" if self.closing else None)}
        state["active_probes"] = len(providers.active_threads) if providers is not None else 0
        return state

    def is_accepting(self) -> bool:
        with self._gate:
            return self.accepting


_MIME = {".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8",
         ".md": "text/markdown; charset=utf-8", ".markdown": "text/markdown; charset=utf-8",
         ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def _download(document) -> Response:
    content = document.content
    if not isinstance(content, bytes) or len(content) != document.size_bytes:
        raise DistributedImportError("dependency_unavailable", 503, True)
    filename = document.filename.replace("\\", "/").rsplit("/", 1)[-1]
    filename = "".join(char for char in filename if ord(char) >= 32 and ord(char) != 127)
    if not filename or filename in {".", ".."}:
        raise DistributedImportError("dependency_unavailable", 503, True)
    fallback = "".join(char if 32 <= ord(char) < 127 and char not in {'"', "\\", ";"}
                       else "_" for char in filename)
    disposition = f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"
    return Response(content, media_type=_MIME.get(document.suffix.lower(),
        "application/octet-stream"), headers={"Cache-Control": "no-store",
        "Content-Disposition": disposition})


def create_isolated_import_app(settings: IsolatedImportSettings | None = None) -> FastAPI:
    """Create a pure API-role app; resources open only during ASGI lifespan."""
    resolved = settings if settings is not None else IsolatedImportSettings.from_env("api")
    if not isinstance(resolved, IsolatedImportSettings) or resolved.role != "api":
        raise IsolatedImportConfigurationError("role")
    config = ApiConfig.from_environment()
    owner = _Owner(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            owner.start()
            opened = await _observe(owner.open_done,
                time.monotonic() + resolved.provider_timeout_seconds)
            if not opened or owner.startup_code is not None or not owner.accepting:
                raise _StartupFailure(owner.startup_code or "isolated_import_unavailable")
            try:
                yield
            finally:
                deadline = owner.begin_close()
                await _observe(owner.cleanup_done, deadline)
        except BaseException:
            deadline = owner.begin_close()
            await _observe(owner.cleanup_done, deadline)
            raise

    app = FastAPI(lifespan=lifespan, openapi_url=None, docs_url=None,
                  redoc_url=None, redirect_slashes=False)
    app.state.lifecycle = owner
    app.state.api_config = config

    @app.exception_handler(DistributedImportError)
    async def import_error(_request: Request, error: DistributedImportError):
        return _error(error.status_code, error.code, error.retryable)

    @app.exception_handler(AuthError)
    async def auth_error(_request: Request, _error_value: AuthError):
        return _error(401, "invalid_credentials")

    @app.exception_handler(InvalidSessionError)
    async def session_error(_request: Request, _error_value: InvalidSessionError):
        return _error(401, "invalid_session")

    @app.exception_handler(InvalidCsrfTokenError)
    @app.exception_handler(UntrustedSessionError)
    async def csrf_error(_request: Request, _error_value: Exception):
        return _error(403, "forbidden")

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _error_value: RequestValidationError):
        return _error(422, "invalid_request")

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, error: HTTPException):
        code = {404: "not_found", 405: "method_not_allowed",
                422: "invalid_request"}.get(error.status_code, "invalid_request")
        return _error(error.status_code, code)

    @app.exception_handler(Exception)
    async def unexpected_error(_request: Request, _error_value: Exception):
        return _error(503, "isolated_import_unavailable", True)

    def token(request: Request) -> str | None:
        return request.cookies.get(config.cookie_name)

    def csrf(request: Request) -> str | None:
        return request.headers.get("X-CSRF-Token")

    @app.get("/healthz")
    def healthz():
        if not owner.is_accepting():
            raise DistributedImportError("isolated_import_unavailable", 503, True)
        return JSONResponse({"status": "ok"})

    @app.post("/api/v1/auth/login")
    def login(credentials: Credentials, request: Request):
        with owner.borrow() as (sessions, _imports):
            issued = sessions.login(credentials.username, credentials.password.get_secret_value())
            session = sessions.get_session(issued)
            body = SessionResponse(username=session.username,
                                   csrf_token=session.csrf_token)
            response = JSONResponse(body.model_dump(mode="json"))
            response.set_cookie(config.cookie_name, issued, httponly=True,
                secure=config.cookie_secure, samesite=config.cookie_samesite, path="/")
            return response

    @app.get("/api/v1/auth/session")
    def session(request: Request):
        with owner.borrow() as (sessions, _imports):
            current = sessions.get_session(token(request))
            body = SessionResponse(username=current.username,
                                   csrf_token=current.csrf_token)
            return JSONResponse(body.model_dump(mode="json"))

    @app.post("/api/v1/auth/logout")
    def logout(request: Request):
        with owner.borrow() as (sessions, _imports):
            original = token(request)
            sessions.validate_csrf(original, csrf(request))
            sessions.logout(original)
            response = Response(status_code=204)
            response.delete_cookie(config.cookie_name, path="/", secure=config.cookie_secure,
                httponly=True, samesite=config.cookie_samesite)
            return response

    @app.post("/api/v1/imports", status_code=202)
    def submit(request: Request, files: list[UploadFile] | None = File(None)):
        with owner.borrow() as (sessions, imports):
            original = token(request)
            original_csrf = csrf(request)
            sessions.validate_csrf(original, original_csrf)
            uploads = [ImportUpload(item.filename or "", item.file) for item in (files or [])]
            body = import_batch_response(imports.submit_uploads(original, original_csrf, uploads))
            return JSONResponse(body.model_dump(mode="json"), status_code=202)

    @app.get("/api/v1/imports")
    def list_batches(request: Request, limit: int = Query(50, ge=1, le=100)):
        with owner.borrow() as (_sessions, imports):
            batches = imports.list_batches(token(request), limit=limit)
            return JSONResponse([import_batch_response(item).model_dump(mode="json")
                                 for item in batches])

    @app.get("/api/v1/imports/tasks/{task_id}")
    def get_task(task_id: str, request: Request):
        with owner.borrow() as (_sessions, imports):
            body = _task_response(imports.get_task(token(request), task_id))
            return JSONResponse(body.model_dump(mode="json"))

    @app.get("/api/v1/imports/{batch_id}")
    def get_batch(batch_id: str, request: Request):
        with owner.borrow() as (_sessions, imports):
            body = import_batch_response(imports.get_batch(token(request), batch_id))
            return JSONResponse(body.model_dump(mode="json"))

    @app.get("/api/v1/documents/{document_id}")
    def read_document(document_id: str, request: Request):
        with owner.borrow() as (_sessions, imports):
            return _download(imports.read_published_document(token(request), document_id))

    return app
