from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from api.app import create_api_app
from app.bootstrap import ApplicationServices
from app.qa_models import QaDocumentCandidate


def register(client: TestClient, username: str) -> str:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "correct horse battery"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_overview_stats_reports_and_user_isolation(tmp_path):
    services = ApplicationServices.create(tmp_path / "data")
    with TestClient(create_api_app(services), raise_server_exceptions=False) as client:
        csrf = register(client, "alice")
        overview = client.get("/api/v1/overview")
        assert overview.status_code == 200
        assert overview.headers["cache-control"] == "no-store"
        assert overview.json()["stats"] | {"activity": []} == {
            "document_count": 0, "completed_question_count": 0,
            "note_count": 0, "report_count": 0, "active_days": 0,
            "window_days": 30, "activity": [],
        }
        assert len(overview.json()["stats"]["activity"]) == 30

        note = client.post(
            "/api/v1/notes", headers={"X-CSRF-Token": csrf},
            json={"body_markdown": "真实笔记", "tags": [], "client_request_id": "insight-note"},
        )
        assert note.status_code == 201
        populated = client.get("/api/v1/insights/stats?days=7").json()
        assert populated["note_count"] == 1
        assert populated["active_days"] == 1
        assert sum(day["notes"] for day in populated["activity"]) == 1

        missing_csrf = client.post("/api/v1/insights/reports", json={})
        assert missing_csrf.status_code == 403
        created = client.post(
            "/api/v1/insights/reports",
            headers={"X-CSRF-Token": csrf}, json={},
        )
        assert created.status_code == 201
        report_id = created.json()["id"]
        assert "不推断阅读时长" in created.json()["content"]
        assert client.get(f"/api/v1/insights/reports/{report_id}").status_code == 200
        download = client.get(f"/api/v1/insights/reports/{report_id}/download?format=md")
        assert download.status_code == 200
        assert download.headers["cache-control"] == "no-store"
        assert "zhiyan-learning-report" in download.headers["content-disposition"]

        deleted = client.request(
            "DELETE", f"/api/v1/notes/{note.json()['id']}",
            headers={"X-CSRF-Token": csrf}, json={"expected_version": 1},
        )
        assert deleted.status_code == 204
        after_delete = client.get("/api/v1/insights/stats").json()
        assert after_delete["note_count"] == 0
        assert sum(day["notes"] for day in after_delete["activity"]) == 0

        register(client, "bob")
        assert client.get(f"/api/v1/insights/reports/{report_id}").status_code == 404
        assert client.get("/api/v1/insights/reports").json() == []
        assert client.get("/api/v1/insights/stats").json()["note_count"] == 0


def test_insight_window_is_bounded_and_auth_is_required(tmp_path):
    services = ApplicationServices.create(tmp_path / "data")
    with TestClient(create_api_app(services), raise_server_exceptions=False) as client:
        assert client.get("/api/v1/overview").status_code == 401
        register(client, "alice")
        assert client.get("/api/v1/insights/stats?days=6").status_code == 422
        assert client.get("/api/v1/insights/stats?days=91").status_code == 422


def test_completed_qa_and_documents_disappear_from_insights_under_deletion_fence(tmp_path):
    services = ApplicationServices.create(tmp_path / "data")
    with TestClient(create_api_app(services), raise_server_exceptions=False) as client:
        services.qa_deletion_worker.stop()
        services.qa_worker_pool.stop()
        register(client, "alice")
        token = client.cookies["zhiyan_session"]
        session = services.session_registry.get_session(token)
        user_id = str(session.user_id)
        document_id = str(uuid4())
        source = session.runtime.paths.documents / f"{document_id}.md"
        source.write_text("真实文档", encoding="utf-8")
        session.runtime.history.add_document({
            "document_id": document_id, "document_name": "学习文档.md",
            "document_path": str(source), "file_suffix": ".md",
            "loaded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        })
        # Establish normal legacy-migration state before seeding repository rows.
        assert client.get("/api/v1/overview").status_code == 200
        repository = services.qa_service.repository
        conversation = repository.create_conversation(
            user_id, (QaDocumentCandidate(document_id, "学习文档.md", user_id),),
        )
        pending = repository.create_pending_turn(user_id, conversation.id, "真实问题", "auto", "insight-turn")
        assert repository.complete_turn(user_id, pending.assistant_message.id, pending.assistant_message.version, "真实回答", (), "none", None)
        before = client.get("/api/v1/overview").json()
        assert before["stats"]["document_count"] == 1
        assert before["stats"]["completed_question_count"] == 1
        assert sum(day["questions"] for day in before["stats"]["activity"]) == 1
        assert before["recent_questions"][0]["question"] == "真实问题"
        assert services.qa_deletion_service.request_document(token, document_id)
        after = client.get("/api/v1/overview").json()
        assert after["stats"]["document_count"] == 0
        assert after["stats"]["completed_question_count"] == 0
        assert after["stats"]["active_days"] == 0
        assert after["recent_documents"] == []
        assert after["recent_questions"] == []
