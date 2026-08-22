from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.app import create_api_app
from app.document_library import (
    DocumentDeleteFailedError,
    DocumentImportActiveError,
    DocumentLibraryItem,
    DocumentNotFoundError,
)
from app.session import InvalidCsrfTokenError, InvalidSessionError


COOKIE = "zhiyan_session"
DOCUMENT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


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


class FakeDocumentLibrary:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.delete_error: Exception | None = None

    def list_documents(self, token):
        self.calls.append(("list", token))
        return (
            DocumentLibraryItem(
                document_id=DOCUMENT_ID,
                name="research.md",
                file_suffix=".md",
                size_bytes=17,
                loaded_at="2026-08-15T08:30:00Z",
            ),
            DocumentLibraryItem(
                document_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                name="legacy.txt",
                file_suffix=".txt",
                size_bytes=None,
                loaded_at=None,
            ),
        )

    def delete_document(self, token, document_id):
        self.calls.append(("delete", token, document_id))
        if token == "other-token":
            raise DocumentNotFoundError()
        if self.delete_error is not None:
            raise self.delete_error


@dataclass
class FakeServices:
    session_registry: FakeRegistry
    document_library: FakeDocumentLibrary
    import_service: object = object()
    start_calls: int = 0
    stop_calls: int = 0

    def start(self):
        self.start_calls += 1

    def stop(self):
        self.stop_calls += 1


@pytest.fixture
def services():
    return FakeServices(FakeRegistry(), FakeDocumentLibrary())


@pytest.fixture
def client(services, monkeypatch):
    monkeypatch.delenv("APP_COOKIE_SECURE", raising=False)
    with TestClient(create_api_app(services), raise_server_exceptions=False) as value:
        yield value


def _owner(client):
    client.cookies.set(COOKIE, "owner-token")


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


def test_list_documents_uses_cookie_and_exact_safe_projection(client, services):
    _owner(client)

    response = client.get("/api/v1/documents")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "document_id": DOCUMENT_ID,
                "name": "research.md",
                "file_suffix": ".md",
                "size_bytes": 17,
                "loaded_at": "2026-08-15T08:30:00Z",
                "status": "ready",
            },
            {
                "document_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                "name": "legacy.txt",
                "file_suffix": ".txt",
                "size_bytes": None,
                "loaded_at": None,
                "status": "ready",
            },
        ]
    }
    assert services.document_library.calls == [("list", "owner-token")]
    lowered = response.text.lower()
    for forbidden in ("user_id", "document_path", "namespace", "secret", "csrf"):
        assert forbidden not in lowered


@pytest.mark.parametrize(
    ("method", "url"),
    [("get", "/api/v1/documents"), ("delete", f"/api/v1/documents/{DOCUMENT_ID}")],
)
def test_document_routes_require_authentication(client, method, url):
    response = getattr(client, method)(url)

    _error(response, 401, "invalid_session")


@pytest.mark.parametrize("csrf", [None, "forged"])
def test_delete_requires_valid_csrf(client, csrf):
    _owner(client)
    headers = {"X-CSRF-Token": csrf} if csrf else {}

    response = client.delete(f"/api/v1/documents/{DOCUMENT_ID}", headers=headers)

    _error(response, 403, "invalid_csrf_token")


def test_delete_uses_cookie_token_and_returns_empty_204(client, services):
    _owner(client)

    response = client.delete(
        f"/api/v1/documents/{DOCUMENT_ID}",
        headers={"X-CSRF-Token": "owner-csrf"},
    )

    assert response.status_code == 204
    assert response.content == b""
    assert services.document_library.calls == [
        ("delete", "owner-token", DOCUMENT_ID)
    ]


def test_delete_rejects_non_uuid_before_service_call(client, services):
    _owner(client)

    response = client.delete(
        "/api/v1/documents/not-a-uuid",
        headers={"X-CSRF-Token": "owner-csrf"},
    )

    _error(response, 422, "validation_error")
    assert services.document_library.calls == []


@pytest.mark.parametrize(
    ("error", "status", "code", "retryable"),
    [
        (DocumentNotFoundError(), 404, "document_not_found", False),
        (DocumentImportActiveError(), 409, "document_import_active", False),
        (DocumentDeleteFailedError(), 500, "document_delete_failed", True),
    ],
)
def test_delete_maps_stable_safe_domain_errors(
    client,
    services,
    error,
    status,
    code,
    retryable,
):
    _owner(client)
    services.document_library.delete_error = error

    response = client.delete(
        f"/api/v1/documents/{DOCUMENT_ID}",
        headers={"X-CSRF-Token": "owner-csrf"},
    )

    _error(response, status, code, retryable=retryable)
    assert "C:\\private" not in response.text


def test_cross_user_document_id_is_indistinguishable_from_missing(client):
    client.cookies.set(COOKIE, "other-token")

    response = client.delete(
        f"/api/v1/documents/{DOCUMENT_ID}",
        headers={"X-CSRF-Token": "other-csrf"},
    )

    _error(response, 404, "document_not_found")
