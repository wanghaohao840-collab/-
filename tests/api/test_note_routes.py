from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.app import create_api_app
from app.note_models import Note, NotePage
from app.session import InvalidCsrfTokenError, InvalidSessionError
from app.bootstrap import ApplicationServices


@dataclass
class _SessionRegistry:
    def get_session(self, token):
        if token != "token":
            raise InvalidSessionError("expired")
        return SimpleNamespace(user_id="alice", csrf_token="csrf", runtime=None)

    def validate_csrf(self, token, csrf_token):
        session = self.get_session(token)
        if csrf_token != session.csrf_token:
            raise InvalidCsrfTokenError("bad csrf")
        return session


class _NoteService:
    def list_notes(self, session, filters):
        return NotePage((), None)


@dataclass
class _Services:
    session_registry: _SessionRegistry
    note_service: _NoteService

    def start(self):
        return None

    def stop(self):
        return None


def test_notes_routes_are_registered_and_require_authentication():
    services = _Services(_SessionRegistry(), _NoteService())
    with TestClient(create_api_app(services), raise_server_exceptions=False) as client:
        assert client.get("/api/v1/notes/capabilities").status_code == 401
        client.cookies.set("zhiyan_session", "token")
        response = client.get("/api/v1/notes/capabilities")
        assert response.status_code == 200
        assert response.json() == {"enabled": True}


def test_note_request_models_reject_untrusted_snapshot_fields():
    services = _Services(_SessionRegistry(), _NoteService())
    with TestClient(create_api_app(services), raise_server_exceptions=False) as client:
        client.cookies.set("zhiyan_session", "token")
        response = client.post(
            "/api/v1/notes",
            headers={"X-CSRF-Token": "csrf"},
            json={
                "body_markdown": "body",
                "concept": None,
                "tags": [],
                "client_request_id": "request",
                "source": {
                    "kind": "qa_citation",
                    "qa_message_id": "message",
                    "citation_id": "citation",
                    "excerpt_snapshot": "attacker controlled",
                },
            },
        )
        assert response.status_code == 422


def test_notes_route_flag_disables_access_but_keeps_authenticated_capability(monkeypatch):
    monkeypatch.setenv("NOTES_ROUTE_ENABLED", "false")
    services = _Services(_SessionRegistry(), _NoteService())
    with TestClient(create_api_app(services), raise_server_exceptions=False) as client:
        client.cookies.set("zhiyan_session", "token")
        assert client.get("/api/v1/notes/capabilities").json() == {"enabled": False}
        assert client.get("/api/v1/notes").status_code == 503


@pytest.fixture
def real_note_client(tmp_path):
    services = ApplicationServices.create(tmp_path / "data")
    with TestClient(create_api_app(services), raise_server_exceptions=False) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={"username": "alice", "password": "correct horse battery"},
        )
        assert registered.status_code == 200
        yield client, registered.json()["csrf_token"]


def test_real_notes_routes_cover_crud_replay_conflict_clear_and_retry(real_note_client):
    client, csrf = real_note_client
    headers = {"X-CSRF-Token": csrf}
    payload = {
        "body_markdown": "first body",
        "concept": "concept",
        "tags": ["tag"],
        "client_request_id": "request-1",
    }
    created = client.post("/api/v1/notes", headers=headers, json=payload)
    assert created.status_code == 201
    note = created.json()
    replay = client.post("/api/v1/notes", headers=headers, json=payload)
    assert replay.status_code == 200 and replay.json()["id"] == note["id"]
    assert client.get("/api/v1/notes").json()["items"][0]["id"] == note["id"]
    assert client.get(f"/api/v1/notes/{note['id']}").status_code == 200
    updated = client.patch(
        f"/api/v1/notes/{note['id']}", headers=headers,
        json={"body_markdown": "second body", "concept": "concept", "tags": ["tag"], "expected_version": 1},
    )
    assert updated.status_code == 200 and updated.json()["version"] == 2
    stale = client.patch(
        f"/api/v1/notes/{note['id']}", headers=headers,
        json={"body_markdown": "stale", "concept": None, "tags": [], "expected_version": 1},
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "NOTE_VERSION_CONFLICT"
    assert client.post("/api/v1/notes/projections/retry", headers=headers).status_code == 200
    deleted = client.request(
        "DELETE",
        f"/api/v1/notes/{note['id']}", headers=headers,
        json={"expected_version": 2},
    )
    assert deleted.status_code == 204 and not deleted.content
    assert client.get(f"/api/v1/notes/{note['id']}").json()["error"]["code"] == "NOTE_NOT_FOUND"
    bob = client.post(
        "/api/v1/auth/register",
        json={"username": "bob", "password": "correct horse battery"},
    )
    assert bob.status_code == 200
    assert client.get(f"/api/v1/notes/{note['id']}").status_code == 404
    assert client.post(
        "/api/v1/notes/clear", headers={"X-CSRF-Token": bob.json()["csrf_token"]},
        json={"confirmation": "清空全部笔记"},
    ).status_code == 200


@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/v1/notes", {"body_markdown": "body", "tags": [], "client_request_id": "csrf"}),
    ("patch", "/api/v1/notes/missing", {"body_markdown": "body", "tags": [], "expected_version": 1}),
    ("delete", "/api/v1/notes/missing", {"expected_version": 1}),
    ("post", "/api/v1/notes/clear", {"confirmation": "清空全部笔记"}),
    ("post", "/api/v1/notes/projections/retry", None),
])
def test_every_note_mutation_requires_csrf(real_note_client, method, path, body):
    client, _csrf = real_note_client
    if method == "delete":
        response = client.request("DELETE", path, json=body)
    else:
        response = getattr(client, method)(path, json=body)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_csrf_token"
