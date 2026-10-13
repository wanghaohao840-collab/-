"""Restricted API: controlled lifecycle contracts and disposable real services."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from threading import Event, Thread
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.requests import Request

import api.isolated_import_app as isolated_api
from app.auth import hash_password
from app.distributed_import_service import DistributedImportError, PublishedImportDocument
from app.isolated_import_provider import IsolatedProviderConfigurationError
from app.isolated_import_runtime import IsolatedImportConfigurationError
from app.postgres_vector_generations import VectorScope
from hello_agents.memory.rag.index_identity import IndexIdentity
from tests.integration import test_distributed_import_worker as worker_contract
from tests.integration import test_import_document_publication as publication_fixture
from tests.test_isolated_import_runtime import _publish_nonempty_history


def test_factory_contract_import():
    from api.isolated_import_app import create_isolated_import_app

    assert callable(create_isolated_import_app)


def _settings():
    return replace(worker_contract._settings(), role="api")


def _task():
    return SimpleNamespace(task_id="task-a", document_id="doc-a", original_name="source.txt",
        file_suffix=".txt", size_bytes=3, status="queued", stage="queued", progress=0,
        error_code=None, error_summary=None, cancel_requested_at=None,
        created_at="2026-01-01T00:00:00Z", started_at=None, finished_at=None,
        updated_at="2026-01-01T00:00:00Z")


def _batch():
    return SimpleNamespace(batch_id="batch-a", created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z", total=1, queued=1, running=0,
        retry_wait=0, succeeded=0, failed=0, cancelled=0, tasks=(_task(),))


class _Providers:
    def __init__(self):
        self.close_calls = 0
        self.close_result = True
        self.close_error = None
        self.close_gate = None
        self.probe_threads = ()

    @property
    def active_threads(self):
        return tuple(thread for thread in self.probe_threads if thread.is_alive())

    def probe(self):
        return object()

    def close(self, *, timeout_seconds):
        self.close_calls += 1
        assert timeout_seconds >= 0
        if self.close_gate is not None:
            assert self.close_gate.wait(4)
        if self.close_error is not None:
            raise self.close_error
        return self.close_result


class _Runtime:
    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


class _Sessions:
    def __init__(self, runtime):
        self.runtime = runtime
        self.calls = []
        self.live = set()

    def login(self, username, password):
        self.calls.append(("login", username, password))
        if password != "correct-password":
            from app.auth import AuthError
            raise AuthError("private password value")
        self.live.add("opaque-token")
        return "opaque-token"

    def get_session(self, token):
        self.calls.append(("session", token))
        if token not in self.live:
            from app.session import InvalidSessionError
            raise InvalidSessionError("private session value")
        return SimpleNamespace(username="allowed-user", csrf_token="csrf-secret",
                               user_id="trusted-user")

    def validate_csrf(self, token, csrf):
        self.calls.append(("csrf", token, csrf))
        session = self.get_session(token)
        if csrf != session.csrf_token:
            from app.session import InvalidCsrfTokenError
            raise InvalidCsrfTokenError("private csrf value")
        return session

    def logout(self, token):
        self.calls.append(("logout", token))
        self.get_session(token)
        self.live.remove(token)


class _Imports:
    def __init__(self, runtime, sessions):
        self.runtime = runtime
        self.sessions = sessions
        self.calls = []
        self.failure = None
        self.readback = PublishedImportDocument("doc-a", "报告\";\r\n.txt", ".txt", 3, b"abc")

    def _check(self):
        if self.failure is not None:
            raise self.failure

    def submit_uploads(self, token, csrf, uploads):
        self.calls.append(("submit", token, csrf,
                           tuple((item.original_name, item.stream.read()) for item in uploads)))
        self._check()
        if self.runtime.probe is None:
            raise DistributedImportError("dependency_unavailable", 503, True)
        if not uploads:
            raise DistributedImportError("import_no_files", 400, False)
        return _batch()

    def list_batches(self, token, *, limit=50):
        self.calls.append(("list", token, limit))
        self._check()
        return [_batch()]

    def get_batch(self, token, batch_id):
        self.calls.append(("batch", token, batch_id))
        self._check()
        return _batch()

    def get_task(self, token, task_id):
        self.calls.append(("task", token, task_id))
        self._check()
        return _task()

    def read_published_document(self, token, document_id):
        self.calls.append(("read", token, document_id))
        self._check()
        return self.readback


def _controlled(monkeypatch, *, providers=None, opener=None):
    selected = providers if providers is not None else _Providers()
    runtime = _Runtime()
    runtime.probe = None
    instances = {}

    def open_runtime(settings, *, provider_probe=None):
        runtime.probe = provider_probe
        if opener is not None:
            opener()
        return runtime

    def sessions_factory(current):
        assert current is runtime
        instances["sessions"] = _Sessions(current)
        return instances["sessions"]

    def imports_factory(current, sessions):
        assert current is runtime and sessions is instances["sessions"]
        instances["imports"] = _Imports(current, sessions)
        return instances["imports"]

    monkeypatch.setattr(isolated_api, "open_isolated_providers", lambda _settings: selected)
    monkeypatch.setattr(isolated_api, "open_isolated_runtime", open_runtime)
    monkeypatch.setattr(isolated_api, "DistributedImportSessions", sessions_factory)
    monkeypatch.setattr(isolated_api, "DistributedImportService", imports_factory)
    app = isolated_api.create_isolated_import_app(_settings())
    return app, selected, runtime, instances


def _login(client):
    return client.post("/api/v1/auth/login", json={"username": "allowed-user",
        "password": "correct-password"})


def test_factory_has_no_resource_io_and_refuses_worker(monkeypatch):
    monkeypatch.setattr(isolated_api, "open_isolated_providers", lambda *_:
                        pytest.fail("factory opened provider"))
    monkeypatch.setattr(isolated_api, "open_isolated_runtime", lambda *_a, **_k:
                        pytest.fail("factory opened runtime"))
    app = isolated_api.create_isolated_import_app(_settings())
    assert app.state.lifecycle.open_thread is None
    with pytest.raises(IsolatedImportConfigurationError) as refused:
        isolated_api.create_isolated_import_app(worker_contract._settings())
    assert refused.value.code == "role"


def test_exact_routes_and_excluded_surface(monkeypatch):
    app, _, _, _ = _controlled(monkeypatch)
    actual = {(route.path, tuple(sorted(route.methods))) for route in app.routes
              if isinstance(route, APIRoute)}
    assert actual == {
        ("/healthz", ("GET",)),
        ("/api/v1/auth/login", ("POST",)),
        ("/api/v1/auth/session", ("GET",)),
        ("/api/v1/auth/logout", ("POST",)),
        ("/api/v1/imports", ("POST",)),
        ("/api/v1/imports", ("GET",)),
        ("/api/v1/imports/tasks/{task_id}", ("GET",)),
        ("/api/v1/imports/{batch_id}", ("GET",)),
        ("/api/v1/documents/{document_id}", ("GET",)),
    }
    assert not app.router.redirect_slashes and not app.openapi_url
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        for path in ("/docs", "/redoc", "/openapi.json", "/api/v1/auth/register",
                     "/api/v1/qa", "/api/v1/notes", "/api/v1/learning",
                     "/api/v1/reports", "/api/v1/search", "/legacy/",
                     "/api/v1/imports/", "/api/v1/imports/batch-a/retry-failed"):
            response = client.get(path)
            assert response.status_code == 404 and response.json() == {
                "detail": {"code": "not_found", "retryable": False}}
        for method, path in (("HEAD", "/healthz"), ("OPTIONS", "/api/v1/imports"),
                             ("DELETE", "/api/v1/documents/doc-a"),
                             ("POST", "/api/v1/imports/tasks/task-a")):
            response = client.request(method, path)
            assert response.status_code == 405
            if method == "HEAD":
                assert response.content == b""
            else:
                assert response.json()["detail"]["code"] == "method_not_allowed"


def test_cookie_auth_csrf_upload_status_and_logout(monkeypatch):
    app, provider, runtime, instances = _controlled(monkeypatch)
    with TestClient(app) as client:
        response = _login(client)
        assert response.status_code == 200
        assert response.json() == {"username": "allowed-user", "csrf_token": "csrf-secret"}
        cookie = response.headers["set-cookie"]
        assert "zhiyan_session=opaque-token" in cookie and "httponly" in cookie.lower()
        assert "samesite=lax" in cookie.lower() and "path=/" in cookie.lower()
        assert client.get("/api/v1/auth/session").status_code == 200
        assert client.post("/api/v1/imports", files={"files": ("one.txt", b"abc")}).status_code == 403
        assert not any(call[0] == "submit" for call in instances["imports"].calls)
        csrf = {"X-CSRF-Token": "csrf-secret"}
        missing = client.post("/api/v1/imports", headers=csrf)
        assert missing.status_code == 400
        assert missing.json()["detail"] == {"code": "import_no_files", "retryable": False}
        submitted = client.post("/api/v1/imports", headers=csrf,
                                files={"files": ("one.txt", b"abc")})
        assert submitted.status_code == 202
        assert submitted.json()["tasks"][0]["document_id"] == "doc-a"
        assert instances["imports"].calls[-1] == ("submit", "opaque-token", "csrf-secret",
                                                (("one.txt", b"abc"),))
        assert client.get("/api/v1/imports?limit=100").json()[0]["batch_id"] == "batch-a"
        assert client.get("/api/v1/imports/tasks/task-a").json()["task_id"] == "task-a"
        assert client.get("/api/v1/imports/batch-a").json()["batch_id"] == "batch-a"
        assert client.get("/api/v1/imports?limit=101").json() == {
            "detail": {"code": "invalid_request", "retryable": False}}
        assert client.post("/api/v1/auth/logout").status_code == 403
        assert client.post("/api/v1/auth/logout", headers=csrf).status_code == 204
        assert client.get("/api/v1/auth/session").status_code == 401
    assert provider.close_calls == runtime.close_calls == 1


def test_csrf_blocks_caller_stream_business_and_logout_deletion(monkeypatch):
    from app.session import InvalidCsrfTokenError

    app, _, _, instances = _controlled(monkeypatch)
    with TestClient(app) as client:
        assert _login(client).status_code == 200
        touched = []
        class BombStream:
            def read(self, *_args):
                touched.append("stream")
                raise AssertionError("caller stream read before CSRF")
        def business_bomb(*_args, **_kwargs):
            touched.append("business")
            raise AssertionError("business mutation before CSRF")
        def deletion_bomb(*_args):
            touched.append("deletion")
            raise AssertionError("session deletion before CSRF")
        instances["imports"].submit_uploads = business_bomb
        instances["sessions"].logout = deletion_bomb
        request = Request({"type": "http", "method": "POST", "path": "/api/v1/imports",
            "headers": [(b"cookie", b"zhiyan_session=opaque-token"),
                        (b"x-csrf-token", b"incorrect")], "app": app})
        upload = next(route.endpoint for route in app.routes if isinstance(route, APIRoute)
                      and route.path == "/api/v1/imports" and "POST" in route.methods)
        logout = next(route.endpoint for route in app.routes if isinstance(route, APIRoute)
                      and route.path == "/api/v1/auth/logout")
        with pytest.raises(InvalidCsrfTokenError):
            upload(request, [SimpleNamespace(filename="source.txt", file=BombStream())])
        with pytest.raises(InvalidCsrfTokenError):
            logout(request)
        assert touched == []
        assert any(call[0] == "session" for call in instances["sessions"].calls)
        assert app.state.lifecycle.status()["borrowers"] == 0


def test_secret_free_errors_provider_refusal_and_readback(monkeypatch):
    app, _, _, instances = _controlled(monkeypatch)
    with TestClient(app, raise_server_exceptions=False) as client:
        wrong = client.post("/api/v1/auth/login", json={"username": "allowed-user",
                                                    "password": "wrong-password"})
        assert wrong.status_code == 401 and wrong.json()["detail"]["code"] == "invalid_credentials"
        assert "wrong-password" not in wrong.text and "private password value" not in wrong.text
        malformed = client.post("/api/v1/auth/login", json={"username": "x",
            "password": "private-value"})
        assert malformed.status_code == 422
        assert "private-value" not in malformed.text and "input" not in malformed.text
        multipart = client.post("/api/v1/imports", content=b"broken body",
            headers={"Content-Type": "multipart/form-data; boundary=missing"})
        assert multipart.status_code == 400
        assert multipart.json()["detail"] == {"code": "invalid_request",
                                              "retryable": False}
        assert _login(client).status_code == 200
        service = instances["imports"]
        service.failure = DistributedImportError("needs_reconciliation", 409, False)
        refused = client.post("/api/v1/imports", headers={"X-CSRF-Token": "csrf-secret"},
                              files={"files": ("one.txt", b"abc")})
        assert refused.json() == {"detail": {"code": "needs_reconciliation",
                                             "retryable": False}}
        service.failure = RuntimeError("secret connection string")
        unexpected = client.get("/api/v1/imports")
        assert unexpected.status_code == 503
        assert unexpected.json()["detail"] == {"code": "isolated_import_unavailable",
                                                 "retryable": True}
        assert "secret connection string" not in unexpected.text
        assert app.state.lifecycle.status()["borrowers"] == 0
        service.failure = None
        original_get_task = service.get_task
        service.get_task = lambda *_args: SimpleNamespace(secret="PRIVATE_DTO")
        try:
            malformed_dto = client.get("/api/v1/imports/tasks/task-a")
            assert malformed_dto.status_code == 503
            assert malformed_dto.json()["detail"]["code"] == "isolated_import_unavailable"
            assert "PRIVATE_DTO" not in malformed_dto.text
            assert app.state.lifecycle.status()["borrowers"] == 0
        finally:
            service.get_task = original_get_task
        document = client.get("/api/v1/documents/doc-a")
        assert document.status_code == 200 and document.content == b"abc"
        assert document.headers["cache-control"] == "no-store"
        assert "filename*=UTF-8''" in document.headers["content-disposition"]
        assert "\r" not in document.headers["content-disposition"]
        assert "\n" not in document.headers["content-disposition"]
        assert "object_key" not in document.text and "receipt" not in document.text
        service.readback = replace(service.readback, size_bytes=4)
        assert client.get("/api/v1/documents/doc-a").status_code == 503
        assert app.state.lifecycle.status()["borrowers"] == 0


@pytest.mark.parametrize("suffix,filename,expected_type,expected_fallback", [
    (".pdf", "paper.pdf", "application/pdf", 'filename="paper.pdf"'),
    (".txt", 'folder\\报告";\r\n.txt', "text/plain; charset=utf-8",
     'filename="____.txt"'),
    (".md", "notes.md", "text/markdown; charset=utf-8", 'filename="notes.md"'),
    (".docx", "notes.docx",
     "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
     'filename="notes.docx"'),
    (".other", "file.other", "application/octet-stream", 'filename="file.other"'),
])
def test_readback_mime_disposition_and_private_metadata(monkeypatch, suffix, filename,
                                                         expected_type, expected_fallback):
    app, _, _, instances = _controlled(monkeypatch)
    with TestClient(app) as client:
        assert _login(client).status_code == 200
        instances["imports"].readback = SimpleNamespace(document_id="doc-a",
            filename=filename, suffix=suffix, size_bytes=3, content=b"abc",
            object_key="PRIVATE_OBJECT_KEY", receipt="PRIVATE_RECEIPT")
        response = client.get("/api/v1/documents/doc-a")
        assert response.status_code == 200 and response.content == b"abc"
        assert response.headers["content-type"] == expected_type
        assert response.headers["cache-control"] == "no-store"
        disposition = response.headers["content-disposition"]
        assert expected_fallback in disposition and "filename*=UTF-8''" in disposition
        assert "\r" not in disposition and "\n" not in disposition
        assert "PRIVATE_OBJECT_KEY" not in str(response.headers)
        assert "PRIVATE_RECEIPT" not in str(response.headers)
        assert "PRIVATE_OBJECT_KEY" not in response.text
        assert "PRIVATE_RECEIPT" not in response.text


def test_typed_provider_refusal_keeps_status_and_503_admission(monkeypatch):
    app, _, runtime, instances = _controlled(monkeypatch)
    def refuse(_settings):
        raise IsolatedProviderConfigurationError("provider_configuration")
    monkeypatch.setattr(isolated_api, "open_isolated_providers", refuse)
    with TestClient(app) as client:
        assert _login(client).status_code == 200
        assert client.get("/api/v1/auth/session").status_code == 200
        assert client.get("/api/v1/imports").status_code == 200
        denied = client.post("/api/v1/imports", headers={"X-CSRF-Token": "csrf-secret"})
        assert denied.status_code == 503
        assert denied.json()["detail"]["code"] == "dependency_unavailable"
        assert client.get("/api/v1/documents/doc-a").content == b"abc"
    assert runtime.probe is None and instances["imports"].calls


def test_unexpected_provider_error_fails_startup_and_secure_cookie(monkeypatch):
    app, _, _, _ = _controlled(monkeypatch)
    def fail_provider(_settings):
        raise RuntimeError("private provider endpoint")
    monkeypatch.setattr(isolated_api, "open_isolated_providers", fail_provider)
    with pytest.raises(Exception) as refused:
        with TestClient(app):
            pass
    assert "private provider endpoint" not in str(refused.value)
    app.state.lifecycle.open_thread.join(5)
    app.state.lifecycle.cleanup_thread.join(5)
    monkeypatch.setattr(isolated_api, "open_isolated_providers", lambda _settings: _Providers())
    monkeypatch.setenv("APP_COOKIE_SECURE", "true")
    secure_app = isolated_api.create_isolated_import_app(_settings())
    with TestClient(secure_app, base_url="https://testserver") as client:
        assert "secure" in _login(client).headers["set-cookie"].lower()
        logout = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "csrf-secret"})
        assert "secure" in logout.headers["set-cookie"].lower()
    secure_app.state.lifecycle.open_thread.join(5)
    secure_app.state.lifecycle.cleanup_thread.join(5)


@pytest.mark.parametrize("late_error", [False, True])
def test_startup_timeout_retains_late_opener_and_keeps_async_ticker(monkeypatch, late_error):
    entered, release = Event(), Event()
    def held_open():
        entered.set()
        assert release.wait(4)
        if late_error:
            raise RuntimeError("private late failure")
    app, provider, runtime, _ = _controlled(monkeypatch, opener=held_open)
    owner = app.state.lifecycle
    opened_at_signal = []
    cleaned_at_signal = []
    original_open_set = owner.open_done.set
    original_cleanup_set = owner.cleanup_done.set
    def signal_open():
        opened_at_signal.append((owner.runtime, owner.open_error, owner.accepting))
        original_open_set()
    def signal_cleanup():
        cleaned_at_signal.append((owner.cleanup_code, provider.close_calls,
                                  runtime.close_calls, owner.borrowers))
        original_cleanup_set()
    monkeypatch.setattr(owner.open_done, "set", signal_open)
    monkeypatch.setattr(owner.cleanup_done, "set", signal_cleanup)
    ticks = []
    async def exercise():
        async def ticker():
            while True:
                ticks.append(True)
                await asyncio.sleep(0.01)
        task = asyncio.create_task(ticker())
        try:
            with pytest.raises(Exception) as refused:
                async with app.router.lifespan_context(app):
                    pass
            assert "private" not in str(refused.value)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    try:
        asyncio.run(exercise())
        assert entered.is_set() and len(ticks) > 5
        assert app.state.lifecycle.closing and not app.state.lifecycle.accepting
    finally:
        release.set()
        app.state.lifecycle.open_thread.join(5)
        app.state.lifecycle.cleanup_thread.join(5)
    assert app.state.lifecycle.cleanup_done.is_set()
    assert provider.close_calls == 1
    assert runtime.close_calls == (0 if late_error else 1)
    assert app.state.lifecycle.open_done.is_set()
    assert app.state.lifecycle.runtime is (None if late_error else runtime)
    assert (app.state.lifecycle.open_error is not None) is late_error
    assert len(opened_at_signal) == len(cleaned_at_signal) == 1
    assert opened_at_signal[0][0] is (None if late_error else runtime)
    assert (opened_at_signal[0][1] is not None) is late_error
    assert opened_at_signal[0][2] is False
    assert cleaned_at_signal == [("clean", 1, 0 if late_error else 1, 0)]
    assert not app.state.lifecycle.accepting
    assert app.state.lifecycle.status()["code"] == "clean"


def test_startup_observer_cancellation_retains_opener_and_cleanup(monkeypatch):
    entered, release = Event(), Event()
    def held_open():
        entered.set()
        assert release.wait(4)
    app, provider, runtime, _ = _controlled(monkeypatch, opener=held_open)
    async def exercise():
        async def enter():
            async with app.router.lifespan_context(app):
                pytest.fail("cancelled startup admitted")
        pending = asyncio.create_task(enter())
        assert await asyncio.to_thread(entered.wait, 2)
        pending.cancel()
        with suppress(asyncio.CancelledError):
            await pending
        assert app.state.lifecycle.closing and not app.state.lifecycle.accepting
    try:
        asyncio.run(exercise())
    finally:
        release.set()
        app.state.lifecycle.open_thread.join(5)
        app.state.lifecycle.cleanup_thread.join(5)
    assert provider.close_calls == runtime.close_calls == 1
    assert app.state.lifecycle.open_done.is_set() and app.state.lifecycle.cleanup_done.is_set()


def test_partial_startup_failure_closes_provider_and_code_only(monkeypatch):
    app, provider, runtime, _ = _controlled(monkeypatch)
    def fail_runtime(_settings, *, provider_probe=None):
        assert provider_probe is not None
        raise RuntimeError("secret database endpoint")
    monkeypatch.setattr(isolated_api, "open_isolated_runtime", fail_runtime)
    with pytest.raises(Exception) as refused:
        with TestClient(app):
            pass
    assert "secret database endpoint" not in str(refused.value)
    app.state.lifecycle.open_thread.join(5)
    app.state.lifecycle.cleanup_thread.join(5)
    assert provider.close_calls == 1 and runtime.close_calls == 0
    assert app.state.lifecycle.open_done.is_set() and app.state.lifecycle.open_error is not None
    assert app.state.lifecycle.cleanup_done.is_set() and app.state.lifecycle.cleanup_code == "clean"


def test_probe_and_slow_provider_close_are_retained_and_runtime_close_attempted(monkeypatch):
    app, provider, runtime, _ = _controlled(monkeypatch)
    probe_release, close_release, probe_entered = Event(), Event(), Event()
    provider.close_gate = close_release
    provider.close_error = RuntimeError("private provider close")
    def probe():
        probe_entered.set()
        assert probe_release.wait(5)
    probe_thread = Thread(target=probe, daemon=False)
    ticks = []
    async def exercise():
        async def ticker():
            while True:
                ticks.append(True)
                await asyncio.sleep(0.01)
        async with app.router.lifespan_context(app):
            probe_thread.start()
            provider.probe_threads = (probe_thread,)
            assert probe_entered.wait(2)
            watcher = asyncio.create_task(ticker())
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher
    try:
        asyncio.run(exercise())
        assert len(ticks) > 5 and not app.state.lifecycle.cleanup_done.is_set()
        assert provider.close_calls == runtime.close_calls == 0
    finally:
        probe_release.set()
        probe_thread.join(5)
        close_release.set()
        app.state.lifecycle.open_thread.join(5)
        app.state.lifecycle.cleanup_thread.join(5)
    assert provider.close_calls == runtime.close_calls == 1
    assert app.state.lifecycle.status()["code"] == "cleanup_unclean"
    assert app.state.lifecycle.cleanup_error is provider.close_error


def test_slow_close_observer_yields_loop_then_closes_once(monkeypatch):
    app, provider, runtime, _ = _controlled(monkeypatch)
    release = Event()
    provider.close_gate = release
    ticks = []
    async def exercise():
        async def ticker():
            while True:
                ticks.append(True)
                await asyncio.sleep(0.01)
        watcher = asyncio.create_task(ticker())
        try:
            async with app.router.lifespan_context(app):
                pass
        finally:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
    try:
        asyncio.run(exercise())
        assert len(ticks) > 5 and provider.close_calls == 1
        assert runtime.close_calls == 0 and not app.state.lifecycle.cleanup_done.is_set()
    finally:
        release.set()
        app.state.lifecycle.open_thread.join(5)
        app.state.lifecycle.cleanup_thread.join(5)
    assert runtime.close_calls == 1 and app.state.lifecycle.cleanup_done.is_set()
    assert app.state.lifecycle.cleanup_code == "clean"


def test_later_shutdown_observer_uses_first_absolute_deadline(monkeypatch):
    app, provider, runtime, _ = _controlled(monkeypatch)
    release = Event()
    provider.close_gate = release
    try:
        with TestClient(app):
            owner = app.state.lifecycle
            first_deadline = owner.begin_close()
            time.sleep(0.55)
            started = time.monotonic()
            assert asyncio.run(isolated_api._observe(owner.cleanup_done,
                                                       owner.begin_close())) is False
            elapsed = time.monotonic() - started
            assert owner.stop_deadline == first_deadline
            assert elapsed <= max(0.0, first_deadline - started) + 0.15
            assert elapsed < owner.settings.stop_wait_seconds * 0.75
            assert not owner.cleanup_done.is_set()
    finally:
        release.set()
        app.state.lifecycle.open_thread.join(5)
        app.state.lifecycle.cleanup_thread.join(5)
    assert provider.close_calls == runtime.close_calls == 1
    assert app.state.lifecycle.cleanup_done.is_set()


def test_repeated_close_uses_one_deadline_and_rejects_new_borrowers(monkeypatch):
    app, provider, runtime, _ = _controlled(monkeypatch)
    with TestClient(app) as client:
        first = app.state.lifecycle.begin_close()
        second = app.state.lifecycle.begin_close()
        assert first == second
        refused = client.get("/api/v1/imports")
        assert refused.status_code == 503
        assert refused.json()["detail"]["code"] == "isolated_import_unavailable"
    assert provider.close_calls == runtime.close_calls == 1
    assert app.state.lifecycle.cleanup_thread is not app.state.lifecycle.open_thread
    app.state.lifecycle.open_thread.join(5)
    app.state.lifecycle.cleanup_thread.join(5)


def test_cancelled_asgi_observer_cannot_release_sync_borrower(monkeypatch):
    app, provider, runtime, instances = _controlled(monkeypatch)
    entered, release = Event(), Event()
    async def exercise():
        async with app.router.lifespan_context(app):
            service = instances["imports"]
            original = service.list_batches
            def blocked(token, *, limit=50):
                entered.set()
                assert release.wait(5)
                return original(token, limit=limit)
            service.list_batches = blocked
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://testserver") as client:
                pending = asyncio.create_task(client.get("/api/v1/imports"))
                try:
                    assert await asyncio.to_thread(entered.wait, 2)
                    pending.cancel()
                    await asyncio.sleep(0.02)
                    assert app.state.lifecycle.status()["borrowers"] == 1
                    app.state.lifecycle.begin_close()
                    assert not app.state.lifecycle.cleanup_done.is_set()
                finally:
                    release.set()
                    with suppress(asyncio.CancelledError):
                        await pending
    try:
        asyncio.run(exercise())
    finally:
        release.set()
        app.state.lifecycle.open_thread.join(5)
        if app.state.lifecycle.cleanup_thread is not None:
            app.state.lifecycle.cleanup_thread.join(5)
    assert app.state.lifecycle.cleanup_done.is_set()
    assert provider.close_calls == runtime.close_calls == 1


@pytest.fixture
def real_p5_resources(monkeypatch):
    """Run Task04's disposable fixture with a unique Task05 resource ledger."""
    ledger = (Path(__file__).resolve().parents[2] / ".runtime" /
              "isolated-import-runtime" / f"p5-resources-{uuid4().hex}.jsonl")
    ledger.touch(exist_ok=False)
    def receipt(token, kind, name, event):
        with ledger.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"fixture": token, "kind": kind,
                "name": name, "event": event}, sort_keys=True) + "\n")
    monkeypatch.setattr(worker_contract, "_resource_receipt", receipt)
    base_fixture = worker_contract.real_worker_resources.__wrapped__(monkeypatch)
    worker_settings, db, raw, s3, users, excluded = next(base_fixture)
    apps = []
    try:
        names = tuple("p5" + index + user[:8] for index, user in
                      zip(("a", "b", "x"), (*users, excluded)))
        with db.transaction() as cursor:
            for user, name in zip((*users, excluded), names):
                cursor.execute("update users set username=%s,username_key=%s,password_hash=%s "
                               "where id=%s", (name, name,
                                              hash_password("correct-password"), user))
        monkeypatch.setenv("ISOLATED_IMPORT_RAG_EMBEDDING_PROVIDER", "simple")
        monkeypatch.setenv("ISOLATED_IMPORT_EPISODE_EMBEDDING_PROVIDER", "simple")
        settings = replace(worker_settings, role="api")
        identity = {"schema": settings.schema, "bucket": settings.s3_bucket,
            "collections": [IndexIdentity("qdrant", base, profile).physical_collection
                for base, profile in ((settings.rag_collection, settings.rag_profile),
                                      (settings.episode_collection, settings.episode_profile))]}
        receipt(settings.schema, "api_fixture", settings.schema, "prepared")
        apps = [isolated_api.create_isolated_import_app(settings) for _ in range(2)]
        with TestClient(apps[0]) as first, TestClient(apps[1]) as second:
            yield SimpleNamespace(settings=settings, db=db, raw=raw, s3=s3,
                users=users, names=names, excluded=excluded, apps=apps, first=first,
                second=second,
                identity=identity)
    finally:
        try:
            for app in apps:
                owner = app.state.lifecycle
                if owner.open_thread is not None:
                    owner.open_thread.join(5)
                    assert owner.cleanup_thread is not None
                    owner.cleanup_thread.join(5)
                    assert owner.cleanup_done.is_set() and owner.status()["clean"]
            receipt(worker_settings.schema, "api_clients", worker_settings.schema, "closed")
        finally:
            base_fixture.close()


def test_real_two_apps_persisted_cookie_tenants_status_and_logout(real_p5_resources,
                                                                 monkeypatch):
    import app.distributed_import_service as service_module
    from app.import_models import ImportLimits

    r = real_p5_resources
    token_user = r.names[0]
    login = r.first.post("/api/v1/auth/login", json={"username": token_user,
        "password": "correct-password"})
    assert login.status_code == 200
    csrf = login.json()["csrf_token"]
    cookie = login.cookies.get("zhiyan_session")
    assert cookie
    other = r.second.get("/api/v1/auth/session", cookies={"zhiyan_session": cookie})
    assert other.status_code == 200 and other.json()["csrf_token"] == csrf
    with monkeypatch.context() as guard:
        hit = []
        def bomb(*_args, **_kwargs):
            hit.append(True)
            raise AssertionError("import storage, business write or logout deletion crossed CSRF")
        guard.setattr(r.apps[1].state.lifecycle.runtime, "storage_clients", bomb)
        guard.setattr(service_module.PostgresImportArtifactService, "submit_uploads", bomb)
        guard.setattr(r.apps[1].state.lifecycle.sessions.repository, "delete", bomb)
        assert r.second.post("/api/v1/imports", cookies={"zhiyan_session": cookie},
            files={"files": ("fresh.txt", b"source")}).status_code == 403
        assert r.second.post("/api/v1/auth/logout", cookies={"zhiyan_session": cookie},
            headers={"X-CSRF-Token": "incorrect"}).status_code == 403
        assert hit == []
    with r.db.transaction() as cursor:
        before = cursor.execute("select count(*) as n from import_batches where user_id=%s",
                                (r.users[0],)).fetchone()["n"]
    assert before == 0
    no_files = r.second.post("/api/v1/imports", cookies={"zhiyan_session": cookie},
                             headers={"X-CSRF-Token": csrf})
    assert no_files.status_code == 400
    assert no_files.json()["detail"]["code"] == "import_no_files"
    unsupported = r.second.post("/api/v1/imports", cookies={"zhiyan_session": cookie},
        headers={"X-CSRF-Token": csrf}, files={"files": ("bad.exe", b"abc")})
    assert unsupported.status_code == 400
    assert unsupported.json()["detail"]["code"] == "invalid_upload"
    original_artifacts = service_module.PostgresImportArtifactService
    with monkeypatch.context() as guard:
        guard.setattr(service_module, "PostgresImportArtifactService",
            lambda db, store: original_artifacts(db, store,
                limits=ImportLimits(max_file_bytes=2)))
        oversized = r.second.post("/api/v1/imports", cookies={"zhiyan_session": cookie},
            headers={"X-CSRF-Token": csrf}, files={"files": ("large.txt", b"abc")})
        assert oversized.status_code == 413
        assert oversized.json()["detail"]["code"] == "import_file_too_large"
    upload = r.second.post("/api/v1/imports", cookies={"zhiyan_session": cookie},
        headers={"X-CSRF-Token": csrf},
        files={"files": ("fresh.txt", b"source")},
        params={"user_id": r.users[1]})
    assert upload.status_code == 202, upload.text
    batch = upload.json()
    assert batch["counts"]["queued"] == 1
    task = batch["tasks"][0]
    with r.db.transaction() as cursor:
        persisted = cursor.execute("select user_id,status,stage,document_id from import_tasks "
            "where id=%s", (task["task_id"],)).fetchone()
        source = cursor.execute("select user_id,bucket,object_key,version_id,sha256,size_bytes "
            "from import_objects where task_id=%s", (task["task_id"],)).fetchone()
    assert persisted == {"user_id": r.users[0], "status": "queued", "stage": "queued",
                         "document_id": task["document_id"]}
    assert source["user_id"] == r.users[0] and source["bucket"] == r.settings.s3_bucket
    assert source["size_bytes"] == len(b"source") and source["version_id"]
    immutable = r.s3.get_object(Bucket=source["bucket"], Key=source["object_key"],
                                VersionId=source["version_id"])["Body"].read()
    assert immutable == b"source"
    assert sha256(immutable).hexdigest() == source["sha256"]
    pdf = r.second.post("/api/v1/imports", cookies={"zhiyan_session": cookie},
        headers={"X-CSRF-Token": csrf},
        files={"files": ("paper.pdf", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF")})
    assert pdf.status_code == 202 and pdf.json()["tasks"][0]["file_suffix"] == ".pdf"
    assert r.first.get("/api/v1/imports/tasks/" + task["task_id"],
        cookies={"zhiyan_session": cookie}).json()["document_id"] == task["document_id"]
    assert r.first.get("/api/v1/imports/" + batch["batch_id"],
        cookies={"zhiyan_session": cookie}).status_code == 200
    other_login = r.first.post("/api/v1/auth/login", json={"username": r.names[1],
        "password": "correct-password"})
    other_cookie = other_login.cookies.get("zhiyan_session")
    assert r.second.get("/api/v1/imports", cookies={"zhiyan_session": other_cookie}).json() == []
    assert r.second.get("/api/v1/imports/tasks/" + task["task_id"],
        cookies={"zhiyan_session": other_cookie}).status_code == 404
    assert r.second.get("/api/v1/imports/" + batch["batch_id"],
        cookies={"zhiyan_session": other_cookie}).status_code == 404
    assert r.first.post("/api/v1/auth/login", json={"username": r.names[2],
        "password": "correct-password"}).status_code == 403
    assert r.second.post("/api/v1/auth/logout", cookies={"zhiyan_session": cookie},
        headers={"X-CSRF-Token": csrf}).status_code == 204
    assert r.first.get("/api/v1/auth/session", cookies={"zhiyan_session": cookie}).status_code == 401


def _publish_compatible_history(r, monkeypatch):
    provider = r.apps[0].state.lifecycle.providers.rag_runtime
    assert provider.profile == r.settings.rag_profile
    assert provider.profile.dimension == 384
    original_point = publication_fixture._point

    def compatible_point(scope, document_id, marker, logical_id=None):
        point = original_point(scope, document_id, marker, logical_id)
        vector = provider.embed_query(marker)
        assert len(vector) == scope.identity.profile.dimension == 384
        compatible = replace(point, vector=vector)
        assert compatible.id == point.id and compatible.payload == point.payload
        return compatible

    monkeypatch.setattr(publication_fixture, "_point", compatible_point)
    rag_identity = IndexIdentity("qdrant", r.settings.rag_collection, r.settings.rag_profile)
    episode_identity = IndexIdentity("qdrant", r.settings.episode_collection,
                                     r.settings.episode_profile)
    scopes = ([VectorScope(user, "rag", f"pdf_{user}", rag_identity) for user in r.users],
              [VectorScope(user, "episode", "episodes", episode_identity) for user in r.users])
    document_id = _publish_nonempty_history((r.settings, r.db, r.raw, r.s3,
                                              r.users, *scopes))
    return document_id, scopes[0][0]


def test_real_readback_proof_bytes_and_tenant(real_p5_resources, monkeypatch):
    r = real_p5_resources
    document_id, _ = _publish_compatible_history(r, monkeypatch)
    first = r.first.post("/api/v1/auth/login", json={"username": r.names[0],
        "password": "correct-password"})
    second = r.second.post("/api/v1/auth/login", json={"username": r.names[1],
        "password": "correct-password"})
    one = r.second.get("/api/v1/documents/" + document_id,
        cookies={"zhiyan_session": first.cookies.get("zhiyan_session")})
    assert one.status_code == 200 and one.content == b"legacy retained bytes"
    assert one.headers["cache-control"] == "no-store"
    assert r.first.get("/api/v1/documents/" + str(uuid4()),
        cookies={"zhiyan_session": first.cookies.get("zhiyan_session")}).status_code == 404
    assert r.first.get("/api/v1/documents/" + document_id,
        cookies={"zhiyan_session": second.cookies.get("zhiyan_session")}).status_code == 404
    runtime = r.apps[1].state.lifecycle.runtime
    actual_probe = runtime._provider_probe
    runtime._provider_probe = None
    try:
        independent = r.second.get("/api/v1/documents/" + document_id,
            cookies={"zhiyan_session": first.cookies.get("zhiyan_session")})
        assert independent.status_code == 200 and independent.content == one.content
    finally:
        runtime._provider_probe = actual_probe
    healthy = r.second.get("/api/v1/documents/" + document_id,
        cookies={"zhiyan_session": first.cookies.get("zhiyan_session")})
    assert healthy.status_code == 200 and healthy.content == one.content
    with r.db.transaction() as cursor:
        ref = cursor.execute("select object_key,version_id from document_objects "
            "where user_id=%s and document_id=%s", (r.users[0], document_id)).fetchone()
    assert ref is not None
    r.s3.delete_object(Bucket=r.settings.s3_bucket, Key=ref["object_key"],
                       VersionId=ref["version_id"])
    failed = r.second.get("/api/v1/documents/" + document_id,
        cookies={"zhiyan_session": first.cookies.get("zhiyan_session")})
    assert failed.status_code == 503
    assert failed.json()["detail"]["code"] == "dependency_unavailable"
    with r.db.transaction() as cursor:
        cursor.execute("""insert into qa_deletion_fences
            (id,user_id,target_type,target_id,status,stage,attempt_count,created_at,updated_at)
            values (%s,%s,'document',%s,'queued','fenced',0,'now','now')""",
            (str(uuid4()), r.users[0], document_id))
    deleted = r.second.get("/api/v1/documents/" + document_id,
        cookies={"zhiyan_session": first.cookies.get("zhiyan_session")})
    assert deleted.status_code == 404
    assert deleted.json()["detail"]["code"] == "not_found"


def test_real_readback_rejects_changed_head_proof(real_p5_resources, monkeypatch):
    r = real_p5_resources
    document_id, rag_scope = _publish_compatible_history(r, monkeypatch)
    login = r.first.post("/api/v1/auth/login", json={"username": r.names[0],
        "password": "correct-password"})
    cookie = {"zhiyan_session": login.cookies.get("zhiyan_session")}
    healthy = r.second.get("/api/v1/documents/" + document_id, cookies=cookie)
    assert healthy.status_code == 200 and healthy.content == b"legacy retained bytes"
    with r.db.transaction() as cursor:
        advanced = cursor.execute("update vector_heads set revision=revision+1 "
            "where tenant_id=%s and vector_kind=%s and namespace=%s and index_key=%s "
            "returning revision", rag_scope.key).fetchall()
        assert len(advanced) == 1
    changed = r.second.get("/api/v1/documents/" + document_id, cookies=cookie)
    assert changed.status_code == 503
    assert changed.json()["detail"]["code"] == "dependency_unavailable"


def test_real_provider_unavailable_status_and_gate_409(real_p5_resources):
    from tests.integration.test_distributed_import_facades import _insert_real_gate

    r = real_p5_resources
    login = r.first.post("/api/v1/auth/login", json={"username": r.names[0],
        "password": "correct-password"})
    cookie = {"zhiyan_session": login.cookies.get("zhiyan_session")}
    csrf = {"X-CSRF-Token": login.json()["csrf_token"]}
    with r.db.transaction() as cursor:
        original_head = cursor.execute("select version_num from alembic_version").fetchone()[
            "version_num"]
        cursor.execute("update alembic_version set version_num='p5-baseline-drift'")
    try:
        baseline = r.second.post("/api/v1/imports", cookies=cookie, headers=csrf,
                                 files={"files": ("one.txt", b"abc")})
        assert baseline.status_code == 503
        assert baseline.json()["detail"]["code"] == "dependency_unavailable"
    finally:
        with r.db.transaction() as cursor:
            cursor.execute("update alembic_version set version_num=%s", (original_head,))
    runtime = r.apps[1].state.lifecycle.runtime
    actual_probe = runtime._provider_probe
    runtime._provider_probe = None
    try:
        assert r.second.get("/api/v1/imports", cookies=cookie).status_code == 200
        unavailable = r.second.post("/api/v1/imports", cookies=cookie,
                                    headers=csrf, files={"files": ("one.txt", b"abc")})
        assert unavailable.status_code == 503
        assert unavailable.json()["detail"]["code"] == "dependency_unavailable"
    finally:
        runtime._provider_probe = actual_probe
    first = r.second.post("/api/v1/imports", cookies=cookie, headers=csrf,
                          files={"files": ("one.txt", b"abc")})
    assert first.status_code == 202
    task_id = first.json()["tasks"][0]["task_id"]
    task = r.apps[0].state.lifecycle.imports.get_task(cookie["zhiyan_session"], task_id)
    _insert_real_gate(SimpleNamespace(db=r.db, users=r.users), task)
    denied = r.second.post("/api/v1/imports", cookies=cookie, headers=csrf,
                           files={"files": ("two.txt", b"def")})
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "needs_reconciliation"
