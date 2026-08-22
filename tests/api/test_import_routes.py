from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.app import create_api_app
from app.database import initialize_database
from app.import_models import ImportBatchSummary, ImportLimits, ImportTaskRecord
from app.import_repository import ImportTaskRepository, InvalidImportTransition
from app.import_service import (
    ImportBatchCommitConfirmationError,
    ImportLimitError,
    ImportStagingCleanupError,
    ImportTaskNotCancellableError,
    ImportTaskService,
)
from app.session import InvalidCsrfTokenError, InvalidSessionError
from app.storage import UserStorage


COOKIE = "zhiyan_session"
BATCH_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER_BATCH_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
TASK_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
OTHER_TASK_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
DOCUMENT_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"


def _task(**overrides):
    values = {
        "task_id": TASK_ID,
        "batch_id": BATCH_ID,
        "user_id": "user-secret-id",
        "document_id": DOCUMENT_ID,
        "original_name": "research.md",
        "file_suffix": ".md",
        "size_bytes": 12,
        "staged_relative_path": "imports/private/staged-secret.md",
        "status": "queued",
        "stage": "staged",
        "progress": 0,
        "total_attempt_count": 0,
        "auto_retry_count": 0,
        "manual_retry_count": 0,
        "max_auto_retries": 2,
        "next_attempt_at": None,
        "error_code": None,
        "error_summary": None,
        "created_at": "2026-08-15T08:30:00Z",
        "started_at": None,
        "finished_at": None,
        "updated_at": "2026-08-15T08:30:00Z",
        "cancel_requested_at": None,
    }
    values.update(overrides)
    return ImportTaskRecord(**values)


def _batch(**overrides):
    task = overrides.pop("task", _task())
    values = {
        "batch_id": BATCH_ID,
        "user_id": "user-secret-id",
        "created_at": "2026-08-15T08:30:00Z",
        "updated_at": "2026-08-15T08:30:01Z",
        "total": 1,
        "queued": 1,
        "running": 0,
        "retry_wait": 0,
        "succeeded": 0,
        "failed": 0,
        "cancelled": 0,
        "tasks": (task,),
    }
    values.update(overrides)
    return ImportBatchSummary(**values)


class FakeRegistry:
    def __init__(self) -> None:
        self.sessions = {
            "owner-token": SimpleNamespace(
                username="owner", csrf_token="owner-csrf", user_id="owner"
            ),
            "other-token": SimpleNamespace(
                username="other", csrf_token="other-csrf", user_id="other"
            ),
        }

    def get_session(self, token):
        if token not in self.sessions:
            raise InvalidSessionError("invalid")
        return self.sessions[token]

    def validate_csrf(self, token, csrf):
        session = self.get_session(token)
        if csrf != session.csrf_token:
            raise InvalidCsrfTokenError("invalid")
        return session


class FakeImportService:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.error: Exception | None = None

    def _raise_or_batch(self):
        if self.error is not None:
            raise self.error
        return _batch()

    def submit_uploads(self, token, uploads):
        captured = [
            (upload.original_name, upload.stream.read()) for upload in uploads
        ]
        self.calls.append(("submit", token, captured))
        if any(name.endswith(".exe") for name, _content in captured):
            raise ValueError("Unsupported document type: .exe")
        return self._raise_or_batch()

    def list_batches(self, token, limit=50):
        self.calls.append(("list", token, limit))
        if token == "other-token":
            return []
        return [self._raise_or_batch()]

    def get_batch(self, token, batch_id):
        self.calls.append(("get", token, batch_id))
        if token == "other-token" or batch_id != BATCH_ID:
            raise KeyError("import batch was not found")
        return self._raise_or_batch()

    def retry_task(self, token, task_id, expected_batch_id=None):
        self.calls.append(("retry", token, task_id, expected_batch_id))
        if (
            token == "other-token"
            or task_id != TASK_ID
            or expected_batch_id != BATCH_ID
        ):
            raise KeyError("import task was not found")
        return self._raise_or_batch()

    def retry_failed_in_batch(self, token, batch_id):
        self.calls.append(("retry-failed", token, batch_id))
        if token == "other-token" or batch_id != BATCH_ID:
            raise KeyError("import batch was not found")
        return self._raise_or_batch()

    def cancel_task(self, token, batch_id, task_id):
        self.calls.append(("cancel", token, batch_id, task_id))
        if token == "other-token" or batch_id != BATCH_ID or task_id != TASK_ID:
            raise KeyError("import task was not found")
        return self._raise_or_batch()


@dataclass
class FakeServices:
    session_registry: FakeRegistry
    import_service: FakeImportService
    document_library: object = object()
    start_calls: int = 0
    stop_calls: int = 0

    def start(self):
        self.start_calls += 1

    def stop(self):
        self.stop_calls += 1


@pytest.fixture
def services():
    return FakeServices(FakeRegistry(), FakeImportService())


@pytest.fixture
def client(services, monkeypatch):
    monkeypatch.delenv("APP_COOKIE_SECURE", raising=False)
    with TestClient(create_api_app(services), raise_server_exceptions=False) as value:
        yield value


def _auth(client, *, other=False):
    client.cookies.set(COOKIE, "other-token" if other else "owner-token")
    return {"X-CSRF-Token": "other-csrf" if other else "owner-csrf"}


def _error(response, status, code, *, retryable=False):
    assert response.status_code == status
    error = response.json()["error"]
    assert set(error) == {"code", "message", "retryable", "field_errors"}
    assert error["code"] == code
    assert error["retryable"] is retryable
    if code == "validation_error":
        assert error["field_errors"]
    else:
        assert error["field_errors"] == {}
    assert error["message"]


def _assert_safe_batch(body):
    assert set(body) == {"batch_id", "created_at", "updated_at", "counts", "tasks"}
    assert body["counts"] == {
        "total": 1,
        "queued": 1,
        "running": 0,
        "retry_wait": 0,
        "succeeded": 0,
        "failed": 0,
        "cancelled": 0,
    }
    assert len(body["tasks"]) == 1
    assert set(body["tasks"][0]) == {
        "task_id",
        "document_id",
        "original_name",
        "file_suffix",
        "size_bytes",
        "status",
        "stage",
        "progress",
        "error_code",
        "error_summary",
        "cancel_requested_at",
        "created_at",
        "started_at",
        "finished_at",
        "updated_at",
    }
    serialized = str(body).lower()
    for forbidden in (
        "user_id",
        "staged_relative_path",
        "staged-secret",
        "cookie",
        "csrf",
        "c:\\\\",
        "/private/",
    ):
        assert forbidden not in serialized


def test_submit_actual_repeated_multipart_returns_202_full_safe_batch(
    client,
    services,
):
    headers = _auth(client)

    response = client.post(
        "/api/v1/imports",
        headers=headers,
        files=[
            ("files", ("one.md", b"one", "text/markdown")),
            ("files", ("two.txt", b"two", "text/plain")),
        ],
    )

    assert response.status_code == 202
    _assert_safe_batch(response.json())
    assert services.import_service.calls == [
        ("submit", "owner-token", [("one.md", b"one"), ("two.txt", b"two")])
    ]


def test_list_and_get_imports_use_cookie_and_safe_schema(client, services):
    _auth(client)

    listed = client.get("/api/v1/imports?limit=20")
    fetched = client.get(f"/api/v1/imports/{BATCH_ID}")

    assert listed.status_code == 200
    assert isinstance(listed.json(), list)
    _assert_safe_batch(listed.json()[0])
    assert fetched.status_code == 200
    _assert_safe_batch(fetched.json())
    assert services.import_service.calls == [
        ("list", "owner-token", 20),
        ("get", "owner-token", BATCH_ID),
    ]


@pytest.mark.parametrize(
    ("url", "expected_call"),
    [
        (
            f"/api/v1/imports/{BATCH_ID}/tasks/{TASK_ID}/retry",
            ("retry", "owner-token", TASK_ID, BATCH_ID),
        ),
        (
            f"/api/v1/imports/{BATCH_ID}/retry-failed",
            ("retry-failed", "owner-token", BATCH_ID),
        ),
        (
            f"/api/v1/imports/{BATCH_ID}/tasks/{TASK_ID}/cancel",
            ("cancel", "owner-token", BATCH_ID, TASK_ID),
        ),
    ],
)
def test_import_mutations_return_updated_batch_and_validate_url_membership(
    client,
    services,
    url,
    expected_call,
):
    response = client.post(url, headers=_auth(client))

    assert response.status_code == 200
    _assert_safe_batch(response.json())
    assert services.import_service.calls == [expected_call]


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("post", "/api/v1/imports"),
        ("get", "/api/v1/imports"),
        ("get", f"/api/v1/imports/{BATCH_ID}"),
        ("post", f"/api/v1/imports/{BATCH_ID}/tasks/{TASK_ID}/retry"),
        ("post", f"/api/v1/imports/{BATCH_ID}/retry-failed"),
        ("post", f"/api/v1/imports/{BATCH_ID}/tasks/{TASK_ID}/cancel"),
    ],
)
def test_every_import_route_requires_authentication(client, method, url):
    kwargs = {}
    if url == "/api/v1/imports" and method == "post":
        kwargs["files"] = [("files", ("one.md", b"one", "text/markdown"))]

    response = getattr(client, method)(url, **kwargs)

    _error(response, 401, "invalid_session")


@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/imports",
        f"/api/v1/imports/{BATCH_ID}/tasks/{TASK_ID}/retry",
        f"/api/v1/imports/{BATCH_ID}/retry-failed",
        f"/api/v1/imports/{BATCH_ID}/tasks/{TASK_ID}/cancel",
    ],
)
@pytest.mark.parametrize("csrf", [None, "forged"])
def test_every_import_mutation_requires_valid_csrf(client, url, csrf):
    client.cookies.set(COOKIE, "owner-token")
    headers = {"X-CSRF-Token": csrf} if csrf else {}
    kwargs = {"headers": headers}
    if url == "/api/v1/imports":
        kwargs["files"] = [("files", ("one.md", b"one", "text/markdown"))]

    response = client.post(url, **kwargs)

    _error(response, 403, "invalid_csrf_token")


@pytest.mark.parametrize("limit", [0, 51, "invalid"])
def test_import_list_limit_is_constrained(client, services, limit):
    _auth(client)

    response = client.get(f"/api/v1/imports?limit={limit}")

    _error(response, 422, "validation_error")
    assert services.import_service.calls == []


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("get", "/api/v1/imports/not-a-uuid"),
        ("post", f"/api/v1/imports/{BATCH_ID}/tasks/not-a-uuid/retry"),
        ("post", f"/api/v1/imports/not-a-uuid/tasks/{TASK_ID}/cancel"),
    ],
)
def test_import_path_ids_must_be_uuids(client, services, method, url):
    headers = _auth(client)
    response = getattr(client, method)(url, headers=headers)

    _error(response, 422, "validation_error")
    assert services.import_service.calls == []


def test_empty_batch_and_unsupported_suffix_have_stable_422_codes(client):
    headers = _auth(client)

    empty = client.post("/api/v1/imports", headers=headers)
    unsupported = client.post(
        "/api/v1/imports",
        headers=headers,
        files=[("files", ("payload.exe", b"unsafe", "application/octet-stream"))],
    )

    _error(empty, 422, "import_batch_empty")
    _error(unsupported, 422, "unsupported_document_type")


@pytest.mark.parametrize(
    ("limits", "files", "expected_code"),
    [
        (
            ImportLimits(max_files=20, max_file_bytes=4, max_batch_bytes=100),
            [("files", ("large.md", b"12345", "text/markdown"))],
            "import_file_too_large",
        ),
        (
            ImportLimits(max_files=20, max_file_bytes=100, max_batch_bytes=5),
            [
                ("files", ("one.md", b"123", "text/markdown")),
                ("files", ("two.md", b"456", "text/markdown")),
            ],
            "import_batch_too_large",
        ),
    ],
)
def test_multipart_enforces_actual_streamed_byte_limits(
    tmp_path,
    monkeypatch,
    limits,
    files,
    expected_code,
):
    monkeypatch.delenv("APP_COOKIE_SECURE", raising=False)
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    storage = UserStorage(tmp_path / "data")
    registry = FakeRegistry()
    registry.sessions["owner-token"].user_id = "11111111-1111-4111-8111-111111111111"
    registry.sessions["owner-token"].runtime = SimpleNamespace(
        lock=__import__("threading").RLock()
    )
    worker = SimpleNamespace(notify=lambda: None)
    service = ImportTaskService(
        registry,
        ImportTaskRepository(db_path),
        storage,
        worker,
        limits=limits,
    )
    services = FakeServices(registry, service)

    with TestClient(create_api_app(services), raise_server_exceptions=False) as api_client:
        response = api_client.post(
            "/api/v1/imports",
            headers=_auth(api_client),
            files=files,
        )

    _error(response, 413, expected_code)


@pytest.mark.parametrize(
    ("error", "status", "code", "retryable"),
    [
        (
            ImportLimitError("import_file_too_large", "raw secret", status_code=413),
            413,
            "import_file_too_large",
            False,
        ),
        (
            ImportLimitError("import_batch_too_large", "raw secret", status_code=413),
            413,
            "import_batch_too_large",
            False,
        ),
        (
            ImportLimitError("import_too_many_files", "raw secret", status_code=413),
            422,
            "import_too_many_files",
            False,
        ),
        (ImportStagingCleanupError(), 500, "import_stage_failed", True),
        (
            ImportBatchCommitConfirmationError(),
            500,
            "import_stage_failed",
            True,
        ),
        (ValueError("could not stage uploaded files C:\\private\\secret"), 500, "import_stage_failed", True),
    ],
)
def test_submit_maps_limit_and_staging_failures_without_raw_details(
    client,
    services,
    error,
    status,
    code,
    retryable,
):
    services.import_service.error = error

    response = client.post(
        "/api/v1/imports",
        headers=_auth(client),
        files=[("files", ("one.md", b"one", "text/markdown"))],
    )

    _error(response, status, code, retryable=retryable)
    assert "raw secret" not in response.text
    assert "private" not in response.text.lower()


def test_submit_propagates_session_expiry_to_existing_401_handler(
    client,
    services,
):
    services.import_service.error = InvalidSessionError("expired between checks")

    response = client.post(
        "/api/v1/imports",
        headers=_auth(client),
        files=[("files", ("one.md", b"one", "text/markdown"))],
    )

    _error(response, 401, "invalid_session")


@pytest.mark.parametrize(
    ("url", "error", "status", "code"),
    [
        (f"/api/v1/imports/{BATCH_ID}", KeyError("missing"), 404, "import_batch_not_found"),
        (
            f"/api/v1/imports/{BATCH_ID}/tasks/{TASK_ID}/retry",
            KeyError("missing"),
            404,
            "import_task_not_found",
        ),
        (
            f"/api/v1/imports/{BATCH_ID}/tasks/{TASK_ID}/retry",
            InvalidImportTransition("raw state"),
            409,
            "import_not_retryable",
        ),
        (
            f"/api/v1/imports/{BATCH_ID}/tasks/{TASK_ID}/cancel",
            ImportTaskNotCancellableError("raw state"),
            409,
            "import_not_cancellable",
        ),
    ],
)
def test_import_resource_errors_use_stable_codes(
    client,
    services,
    url,
    error,
    status,
    code,
):
    services.import_service.error = error
    headers = _auth(client)

    response = client.get(url) if "/tasks/" not in url else client.post(url, headers=headers)

    _error(response, status, code)
    assert "raw state" not in response.text


@pytest.mark.parametrize(
    ("method", "url", "code"),
    [
        ("get", f"/api/v1/imports/{BATCH_ID}", "import_batch_not_found"),
        (
            "post",
            f"/api/v1/imports/{BATCH_ID}/tasks/{TASK_ID}/retry",
            "import_task_not_found",
        ),
        (
            "post",
            f"/api/v1/imports/{BATCH_ID}/retry-failed",
            "import_batch_not_found",
        ),
        (
            "post",
            f"/api/v1/imports/{BATCH_ID}/tasks/{TASK_ID}/cancel",
            "import_task_not_found",
        ),
    ],
)
def test_cross_user_import_ids_are_indistinguishable_from_missing(
    client,
    method,
    url,
    code,
):
    headers = _auth(client, other=True)

    response = getattr(client, method)(url, headers=headers)

    _error(response, 404, code)


@pytest.mark.parametrize(
    ("url", "expected_code"),
    [
        (
            f"/api/v1/imports/{OTHER_BATCH_ID}/tasks/{TASK_ID}/retry",
            "import_task_not_found",
        ),
        (
            f"/api/v1/imports/{OTHER_BATCH_ID}/tasks/{TASK_ID}/cancel",
            "import_task_not_found",
        ),
        (
            f"/api/v1/imports/{BATCH_ID}/tasks/{OTHER_TASK_ID}/retry",
            "import_task_not_found",
        ),
    ],
)
def test_nested_task_routes_enforce_batch_membership(client, url, expected_code):
    response = client.post(url, headers=_auth(client))

    _error(response, 404, expected_code)
