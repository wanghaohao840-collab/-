from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.app import create_api_app
from app.note_models import Note, NotePage, NoteProjectionUnavailable, NoteSource
from app.session import InvalidCsrfTokenError, InvalidSessionError
from app.bootstrap import ApplicationServices
from app.qa_models import QaDocumentCandidate
from api.schemas.notes import source_response
from app.database import connect as connect_for_test


def test_document_chunk_note_api_uses_verified_source_and_rejects_tampering(tmp_path, monkeypatch):
    from uuid import uuid4
    from app.note_repository import scrub_sources_in_transaction
    services = ApplicationServices.create(tmp_path / "data")
    doc = str(uuid4())
    with TestClient(create_api_app(services), raise_server_exceptions=False) as client:
        registered = client.post("/api/v1/auth/register", json={"username": "alice", "password": "correct horse battery"})
        headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
        session = services.session_registry.get_session(client.cookies.get("zhiyan_session"))
        monkeypatch.setattr(services.document_library, "list_documents", lambda token: (
            [SimpleNamespace(document_id=doc, name="authoritative.pdf")]
            if services.session_registry.get_session(token).user_id == session.user_id else []
        ))
        calls = []
        def execute(action, **kwargs):
            calls.append(action)
            assert action == "get_document_chunk"
            return SimpleNamespace(success=True, data={"chunk": {
                "document_id": doc, "chunk_id": "chunk-0", "chunk_index": 0,
                "content_sha256": "a" * 64, "content": "verified source text",
                "page_number": 2, "section": "Methods",
            }})
        monkeypatch.setattr(session.runtime.rag_tool, "execute_result", execute)
        source = {"kind": "document_chunk", "locator": {
            "document_id": doc, "chunk_id": "chunk-0", "chunk_index": 0, "content_sha256": "a" * 64,
        }}
        body = {"body_markdown": "my own edited note", "client_request_id": "doc-note", "source": source}
        assert client.post("/api/v1/notes", json=body).status_code == 403
        for bad in (source | {"title_snapshot": "fake"}, source | {"qa_message_id": "fake"}, source | {"locator": source["locator"] | {"content": "fake"}}):
            assert client.post("/api/v1/notes", headers=headers, json=body | {"source": bad}).status_code == 422
        assert calls == []
        response = client.post("/api/v1/notes", headers=headers, json=body)
        assert response.status_code == 201, response.text
        note = response.json()
        assert note["body_markdown"] == "my own edited note"
        assert note["sources"][0]["kind"] == "document_chunk"
        assert note["sources"][0]["title_snapshot"] == "authoritative.pdf"
        assert note["sources"][0]["excerpt_snapshot"] == "verified source text"
        assert client.get("/api/v1/notes?source_kind=document_chunk").json()["items"][0]["id"] == note["id"]
        stale = body | {"client_request_id": "stale", "source": source | {"locator": source["locator"] | {"content_sha256": "b" * 64}}}
        assert client.post("/api/v1/notes", headers=headers, json=stale).status_code == 404
        with connect_for_test(services.db_path) as conn:
            scrub_sources_in_transaction(conn, user_id=session.user_id, document_id=doc, deleted_at="2026-09-04T12:00:00Z")
        before_replay = len(calls)
        replay = client.post("/api/v1/notes", headers=headers, json=body)
        assert replay.status_code == 200
        assert replay.json()["sources"][0]["deleted"]
        assert replay.json()["sources"][0]["locator"] is None
        assert len(calls) == before_replay
        other = client.post("/api/v1/auth/register", json={"username": "bob", "password": "correct horse battery"})
        assert client.get(f"/api/v1/notes/{note['id']}").status_code == 404
        foreign = client.post("/api/v1/notes", headers={"X-CSRF-Token": other.json()["csrf_token"]}, json=body)
        assert foreign.status_code == 404
        assert len(calls) == before_replay


def test_document_source_backend_error_is_safe_503(tmp_path, monkeypatch):
    from uuid import uuid4
    from app.document_search import DocumentSearchUnavailableError
    services = ApplicationServices.create(tmp_path / "data")
    with TestClient(create_api_app(services), raise_server_exceptions=False) as client:
        registered = client.post("/api/v1/auth/register", json={"username": "alice", "password": "correct horse battery"})
        def fail(*args):
            raise DocumentSearchUnavailableError("sk-private-key endpoint")
        monkeypatch.setattr(services.document_search, "resolve_chunk", fail)
        response = client.post("/api/v1/notes", headers={"X-CSRF-Token": registered.json()["csrf_token"]}, json={
            "body_markdown": "body", "client_request_id": "unavailable", "source": {"kind": "document_chunk", "locator": {
                "document_id": str(uuid4()), "chunk_id": "chunk", "chunk_index": 0, "content_sha256": "a" * 64,
            }},
        })
        assert response.status_code == 503
        assert response.json()["error"]["retryable"]
        assert "sk-private-key" not in response.text


def test_session_expiry_during_document_resolution_remains_401(tmp_path, monkeypatch):
    from uuid import uuid4
    services = ApplicationServices.create(tmp_path / "data")
    with TestClient(create_api_app(services), raise_server_exceptions=False) as client:
        registered = client.post("/api/v1/auth/register", json={"username": "alice", "password": "correct horse battery"})
        def expire(*args):
            raise InvalidSessionError("expired")
        monkeypatch.setattr(services.document_search, "resolve_chunk", expire)
        response = client.post("/api/v1/notes", headers={"X-CSRF-Token": registered.json()["csrf_token"]}, json={
            "body_markdown": "body", "client_request_id": "expired", "source": {"kind": "document_chunk", "locator": {
                "document_id": str(uuid4()), "chunk_id": "chunk", "chunk_index": 0, "content_sha256": "a" * 64,
            }},
        })
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_session"


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


class _UnavailableProjectionService(_NoteService):
    def retry_projection(self, _session):
        raise NoteProjectionUnavailable("worker")


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


def test_tombstone_source_dto_scrubs_malformed_legacy_payload():
    response = source_response(NoteSource(
        id="source-id", kind="qa_citation", deleted=True,
        qa_thread_id="thread", qa_message_id="message", citation_id="citation",
        document_id="document", locator={"page": 1}, title_snapshot="title",
        excerpt_snapshot="excerpt", created_at="t", source_deleted_at="deleted",
    ))
    assert response.id is None
    assert response.deleted is True
    assert response.qa_thread_id is None
    assert response.qa_message_id is None
    assert response.citation_id is None
    assert response.document_id is None
    assert response.locator is None
    assert response.title_snapshot is None
    assert response.excerpt_snapshot is None


def test_projection_unavailable_uses_approved_retryable_error():
    services = _Services(_SessionRegistry(), _UnavailableProjectionService())
    with TestClient(create_api_app(services), raise_server_exceptions=False) as client:
        client.cookies.set("zhiyan_session", "token")
        response = client.post(
            "/api/v1/notes/projections/retry",
            headers={"X-CSRF-Token": "csrf"},
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "NOTE_PROJECTION_UNAVAILABLE"
    assert response.json()["error"]["retryable"] is True


@pytest.fixture
def real_note_client(tmp_path):
    services = ApplicationServices.create(tmp_path / "data")
    with TestClient(create_api_app(services), raise_server_exceptions=False) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={"username": "alice", "password": "correct horse battery"},
        )
        assert registered.status_code == 200
        yield client, registered.json()["csrf_token"], services


def test_real_notes_routes_cover_crud_replay_conflict_clear_and_retry(real_note_client):
    client, csrf, _services = real_note_client
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
    invalid_cursor = client.get("/api/v1/notes?cursor=not-a-cursor")
    assert invalid_cursor.status_code == 422
    assert invalid_cursor.json()["error"]["code"] == "NOTE_VALIDATION_ERROR"
    invalid_source_kind = client.get("/api/v1/notes?source_kind=unknown")
    assert invalid_source_kind.status_code == 422
    assert invalid_source_kind.json()["error"]["code"] == "NOTE_VALIDATION_ERROR"
    listed = client.get("/api/v1/notes").json()["items"][0]
    assert listed["id"] == note["id"]
    assert "excerpt_snapshot" not in listed["sources"]
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
    client, _csrf, _services = real_note_client
    if method == "delete":
        response = client.request("DELETE", path, json=body)
    else:
        response = getattr(client, method)(path, json=body)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_csrf_token"


def test_real_active_source_fence_returns_note_source_deleting(real_note_client):
    client, csrf, services = real_note_client
    services.qa_deletion_worker.stop()
    user_id = services.session_registry.get_session(client.cookies.get("zhiyan_session")).user_id
    conversation = services.qa_repository.create_conversation(
        user_id, (QaDocumentCandidate("doc", "doc.md", user_id),)
    )
    turn = services.qa_repository.create_pending_turn(
        user_id, conversation.id, "question", "joint", "api-source"
    )
    services.qa_repository.complete_turn(
        user_id, turn.assistant_message.id, 0, "answer", (), "none", None
    )
    deletion = services.qa_deletion_repository.create_conversation_deletion(
        user_id, conversation.id
    )
    assert deletion is not None
    response = client.post(
        "/api/v1/notes",
        headers={"X-CSRF-Token": csrf},
        json={
            "body_markdown": "body",
            "concept": None,
            "tags": [],
            "client_request_id": "fenced-source",
            "source": {"kind": "qa_answer", "qa_message_id": turn.assistant_message.id},
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NOTE_SOURCE_DELETING"


def test_real_source_detail_and_deleted_tombstone_are_safe(real_note_client):
    client, csrf, services = real_note_client
    services.qa_deletion_worker.stop()
    token = client.cookies.get("zhiyan_session")
    user_id = services.session_registry.get_session(token).user_id
    conversation = services.qa_repository.create_conversation(
        user_id, (QaDocumentCandidate("doc", "doc.md", user_id),)
    )
    turn = services.qa_repository.create_pending_turn(
        user_id, conversation.id, "question", "joint", "api-source-detail"
    )
    from app.qa_models import QaSourceDraft
    services.qa_repository.complete_turn(
        user_id, turn.assistant_message.id, 0, "answer", (
            QaSourceDraft("citation", "doc", "doc.md", 2, "section", "excerpt", "ref"),
        ), "available", None
    )
    created = client.post(
        "/api/v1/notes", headers={"X-CSRF-Token": csrf},
        json={
            "body_markdown": "author body", "concept": "concept", "tags": ["tag"],
            "client_request_id": "source-detail",
            "source": {"kind": "qa_citation", "qa_message_id": turn.assistant_message.id, "citation_id": "citation"},
        },
    )
    assert created.status_code == 201
    note_id = created.json()["id"]
    detail = client.get(f"/api/v1/notes/{note_id}").json()
    assert detail["sources"][0]["excerpt_snapshot"] == "excerpt"
    deletion = services.qa_deletion_repository.create_conversation_deletion(user_id, conversation.id)
    assert services.qa_deletion_worker.run_once("api-source-delete")
    tombstone = client.get(f"/api/v1/notes/{note_id}").json()
    source = tombstone["sources"][0]
    assert source["deleted"] is True and source["source_deleted_at"]
    assert all(source[field] is None for field in (
        "id", "qa_thread_id", "qa_message_id", "citation_id", "document_id",
        "locator", "title_snapshot", "excerpt_snapshot",
    ))
    assert tombstone["body_markdown"] == "author body"
    assert tombstone["concept"] == "concept"
    assert tombstone["tags"] == ["tag"]
    assert services.qa_deletion_repository.get_deletion(user_id, deletion.id).status == "completed"
