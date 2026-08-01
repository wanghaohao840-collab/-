from app.database import initialize_database
from app.auth import AuthService
from app.reports import ReportService
from app.storage import UserStorage


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
