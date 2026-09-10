from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.app import create_api_app
from app.bootstrap import ApplicationServices
from app.document_search import DocumentSearchBusyError, DocumentSearchUnavailableError, DocumentSearchScopeChangedError


@pytest.fixture
def host(tmp_path):
    services = ApplicationServices.create(tmp_path / "data")
    with TestClient(create_api_app(services), raise_server_exceptions=False) as client:
        yield services, client


def authenticate(client):
    result = client.post("/api/v1/auth/register", json={"username": "alice", "password": "correct horse battery"})
    assert result.status_code == 200
    return {"X-CSRF-Token": result.json()["csrf_token"]}


def test_search_auth_csrf_validation_and_empty_success(host, monkeypatch):
    services, client = host
    doc = str(uuid4())
    body = {"query": "source", "document_ids": [doc]}
    unauthorized = client.post("/api/v1/search", json=body)
    assert unauthorized.status_code == 401
    assert unauthorized.headers["cache-control"] == "no-store"
    headers = authenticate(client)
    no_csrf = client.post("/api/v1/search", json=body)
    assert no_csrf.status_code == 403
    assert no_csrf.headers["cache-control"] == "no-store"
    for invalid in ({"query": " "}, {"document_ids": []}, {"document_ids": [doc, doc]}, {"limit": 3}, {"namespace": "bob"}):
        response = client.post("/api/v1/search", headers=headers, json=body | invalid)
        assert response.status_code == 422
        assert response.headers["cache-control"] == "no-store"
    missing = client.post("/api/v1/search", headers=headers, json=body)
    assert missing.status_code == 404
    assert missing.headers["cache-control"] == "no-store"
    monkeypatch.setattr(services.document_library, "list_documents", lambda _: [SimpleNamespace(document_id=doc, name="paper.pdf")])
    # Keep authentication real while isolating the external embedding backend.
    registry = services.session_registry
    token = client.cookies.get("zhiyan_session")
    session = registry.get_session(token)
    monkeypatch.setattr(session.runtime.rag_tool, "execute_result", lambda *a, **kw: SimpleNamespace(success=True, data={"results": []}))
    response = client.post("/api/v1/search", headers=headers, json=body)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["results"] == []


@pytest.mark.parametrize("error,status,code", [
    (DocumentSearchBusyError, 429, "SEARCH_BUSY"),
    (DocumentSearchUnavailableError, 503, "SEARCH_UNAVAILABLE"),
    (DocumentSearchScopeChangedError, 409, "SEARCH_SCOPE_CHANGED"),
])
def test_safe_search_errors(host, monkeypatch, error, status, code):
    services, client = host
    headers = authenticate(client)
    def fail(*a, **kw):
        raise error("private endpoint sk-secret C:/private")
    monkeypatch.setattr(services.document_search, "search", fail)
    response = client.post("/api/v1/search", headers=headers, json={"query": "source", "document_ids": [str(uuid4())]})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "private" not in response.text and "sk-secret" not in response.text
    assert response.headers["cache-control"] == "no-store"
