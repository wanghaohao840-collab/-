import pytest

from app.database import initialize_database
from app.database import transaction
from app.auth import AuthService
from app.reports import ReportService
from app.qa_models import QaReportTurn
from app.storage import UnsafePathError, UserStorage
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
    assert service.read_report_bytes(user_id, record.id) == b"# Report"
    assert service.list_reports("user-2") == []
    with pytest.raises(FileNotFoundError):
        service.read_report_bytes("user-2", record.id)
    with pytest.raises(FileNotFoundError):
        service.read_report_bytes(user_id, "missing")


def test_report_bytes_reject_path_outside_owner_root(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    storage = UserStorage(tmp_path / "data")
    service = ReportService(db_path, storage)
    owner = create_user(db_path)
    record = service.create_markdown_snapshot(owner, "Weekly", "private")
    with transaction(db_path) as conn:
        conn.execute("update report_records set relative_path = ? where id = ?", ("../escape.md", record.id))
    with pytest.raises(UnsafePathError):
        service.read_report_bytes(owner, record.id)


def test_report_text_normalizes_newlines_while_bytes_remain_exact(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    storage = UserStorage(tmp_path / "data")
    service = ReportService(db_path, storage)
    owner = create_user(db_path)
    record = service.create_markdown_snapshot(owner, "Weekly", "temporary")
    saved = b"first\r\nsecond\rthird\n"
    storage.report_path(owner, record.id).write_bytes(saved)

    assert service.read_report_bytes(owner, record.id) == saved
    assert service.read_report(owner, record.id) == "first\nsecond\nthird\n"


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
