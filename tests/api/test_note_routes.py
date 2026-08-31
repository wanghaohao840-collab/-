from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from fastapi.testclient import TestClient

from api.app import create_api_app
from app.note_models import Note, NotePage
from app.session import InvalidCsrfTokenError, InvalidSessionError


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
