from app.database import initialize_database
from app.auth import AuthService
from app.reports import ReportService
from app.qa_models import QaReportTurn
from app.storage import UserStorage
from assistants.pdf_learning_assistant import PDFLearningAssistant


def create_user(db_path):
    return AuthService(db_path).register("Alice", "correct horse battery").id


def test_report_service_saves_markdown_snapshot_and_index(tmp_path):
    db_path = tmp_path / "app.db"
    data_root = tmp_path / "data"
    initialize_database(db_path)
    storage = UserStorage(data_root)
    service = ReportService(db_path=db_path, storage=storage)
    user_id = create_user(db_path)

    record = service.create_markdown_snapshot(user_id, "Weekly", "# Report")

    assert storage.report_path(user_id, record.id).read_text(encoding="utf-8") == "# Report"
    assert service.list_reports(user_id)[0].id == record.id
    assert service.read_report(user_id, record.id) == "# Report"
    assert service.list_reports("user-2") == []


def test_report_service_hides_missing_files(tmp_path):
    db_path = tmp_path / "app.db"
    data_root = tmp_path / "data"
    initialize_database(db_path)
    storage = UserStorage(data_root)
    service = ReportService(db_path=db_path, storage=storage)
    user_id = create_user(db_path)
    record = service.create_markdown_snapshot(user_id, "Weekly", "# Report")
    storage.report_path(user_id, record.id).unlink()

    assert service.list_reports(user_id) == []


def test_learning_report_uses_qa_projection_without_exposing_history_path(
    tmp_path,
) -> None:
    assistant = object.__new__(PDFLearningAssistant)
    assistant.user_id = "user-1"
    assistant.session_id = "session-1"
    assistant.current_document = None
    assistant.history_path = tmp_path / "secret" / "history.json"
    assistant.history = {
        "documents": [],
        "questions": [{"question": "legacy stale", "answer": "stale"}],
        "notes": [],
    }
    assistant._load_latest_history = lambda: assistant.history
    assistant.memory_tool = type("Memory", (), {"execute": lambda *_: "memory"})()
    assistant.rag_tool = type("Rag", (), {"execute": lambda *_: "rag"})()
    turns = (
        QaReportTurn(
            "repository question",
            "repository answer",
            ("doc-1",),
            ("One.pdf",),
            "joint",
            "2026-08-27T00:00:00Z",
        ),
    )

    report = assistant.generate_report(turns)

    assert "repository question" in report
    assert "legacy stale" not in report
    assert str(assistant.history_path) not in report
