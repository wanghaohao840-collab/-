from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_api_app
from app.bootstrap import ApplicationServices
from app.note_models import NoteFilters


def test_legacy_assistant_and_notes_service_share_rows_without_history_note_write(tmp_path: Path):
    services = ApplicationServices.create(tmp_path / "data")
    token = services.session_registry.register("Alice", "correct horse battery")
    session = services.session_registry.get_session(token)
    history_before = session.assistant._load_latest_history()["notes"]

    result = session.assistant.add_note("间隔复习可以降低遗忘", "学习策略")
    page = services.note_service.list_notes(session, NoteFilters())

    assert "保存成功" in result
    assert [note.body_markdown for note in page.items] == ["间隔复习可以降低遗忘"]
    assert session.assistant._load_latest_history()["notes"] == history_before
    assert services.note_repository.count(session.user_id) == 1
    services.stop()


def test_legacy_note_operations_are_shared_across_sessions_and_isolated_by_user(tmp_path: Path):
    services = ApplicationServices.create(tmp_path / "data")
    alice_token = services.session_registry.register("Alice", "correct horse battery")
    alice_second_token = services.session_registry.login("Alice", "correct horse battery")
    bob_token = services.session_registry.register("Bob", "correct horse battery")
    alice = services.session_registry.get_session(alice_token)
    alice_second = services.session_registry.get_session(alice_second_token)
    bob = services.session_registry.get_session(bob_token)
    alice_history_path = alice.assistant.history_repository.path
    bob_history_path = bob.assistant.history_repository.path
    assert not alice_history_path.exists()
    assert not bob_history_path.exists()

    alice.assistant.add_note("Alice first")
    alice_second.assistant.add_note("Alice second")
    bob.assistant.add_note("Bob only")

    assert services.note_repository.count(alice.user_id) == 2
    assert services.note_repository.count(bob.user_id) == 1
    assert not alice_history_path.exists()
    assert not bob_history_path.exists()

    alice_second.assistant.clear_all_notes()

    assert services.note_repository.count(alice.user_id) == 0
    assert services.note_repository.count(bob.user_id) == 1
    services.stop()


def test_authenticated_notes_endpoint_lists_assistant_rows_and_assistant_recalls_api_rows(tmp_path: Path):
    services = ApplicationServices.create(tmp_path / "data")
    with TestClient(create_api_app(services), raise_server_exceptions=False) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={"username": "Alice", "password": "correct horse battery"},
        )
        assert registered.status_code == 200
        csrf = registered.json()["csrf_token"]
        token = client.cookies.get("zhiyan_session")
        session = services.session_registry.get_session(token)

        assert "保存成功" in session.assistant.add_note("assistant-created row", "shared")
        listed = client.get("/api/v1/notes")
        assert listed.status_code == 200
        assert [item["body_markdown"] for item in listed.json()["items"]] == ["assistant-created row"]

        created = client.post(
            "/api/v1/notes",
            headers={"X-CSRF-Token": csrf},
            json={
                "body_markdown": "api-created recallable row",
                "concept": "shared",
                "tags": [],
                "client_request_id": "cross-entry-api-row",
            },
        )
        assert created.status_code == 201
        later_token = services.session_registry.login("Alice", "correct horse battery")
        later_session = services.session_registry.get_session(later_token)
        assert "api-created recallable row" in later_session.assistant.recall("recallable", limit=5)
    services.stop()
