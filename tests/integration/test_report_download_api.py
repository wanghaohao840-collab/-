"""Authenticated API downloads against disposable PostgreSQL and versioned S3."""
from io import BytesIO

from docx import Document
from fastapi.testclient import TestClient

from api.app import create_api_app
from app.bootstrap import ApplicationServices
from app.postgres_reports import PostgresReportService
from tests.integration.test_postgres_auth_sessions import shared_database
from tests.integration.test_s3_object_store import store


def test_authenticated_download_reads_pinned_s3_snapshot_without_local_export(
    tmp_path, shared_database, store, monkeypatch,
):
    open_pool, _ = shared_database
    db = open_pool()
    services = ApplicationServices.create(tmp_path / "local-auth")
    with TestClient(create_api_app(services), raise_server_exceptions=False) as client:
        registered = client.post("/api/v1/auth/register", json={
            "username": "report-owner", "password": "correct horse battery",
        })
        assert registered.status_code == 200
        token = client.cookies["zhiyan_session"]
        session = services.session_registry.get_session(token)
        user_id = session.user_id
        with db.transaction() as cursor:
            cursor.execute("insert into users values(%s,%s,%s,%s,%s,%s,%s)", (
                user_id, user_id, user_id, "unused", "active", "now", "now",
            ))
        reports = PostgresReportService(db, store)
        content = "# 已保存快照\r\n\n一、记录\n- 固定来源 [1]\n"
        record = reports.create_markdown_snapshot(user_id, "Pinned", content)
        session.runtime.reports = reports
        with db.transaction() as cursor:
            reference = cursor.execute(
                "select object_key from report_objects where report_id=%s", (record.id,),
            ).fetchone()
            before = cursor.execute("select count(*) as n from report_records").fetchone()["n"]
            refs_before = cursor.execute("select count(*) as n from report_objects").fetchone()["n"]
        key = reference["object_key"]
        store.client.put_object(Bucket=store.bucket, Key=key, Body=b"new unpinned bytes")
        store.client.delete_object(Bucket=store.bucket, Key=key)

        def forbidden(*_args, **_kwargs):
            raise AssertionError("API download touched local report export")

        monkeypatch.setattr(session.assistant, "export_report_docx", forbidden)
        monkeypatch.setattr(reports, "report_file_path", forbidden, raising=False)
        markdown = client.get(f"/api/v1/insights/reports/{record.id}/download?format=md")
        assert markdown.status_code == 200
        assert markdown.content == content.encode("utf-8")
        assert markdown.headers["cache-control"] == "no-store"
        word = client.get(f"/api/v1/insights/reports/{record.id}/download?format=docx")
        assert word.status_code == 200
        assert word.headers["content-type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        text = "\n".join(p.text for p in Document(BytesIO(word.content)).paragraphs)
        assert "固定来源 [1]" in text
        assert f"用户：{user_id}" in text
        assert "new unpinned bytes" not in text
        with db.transaction() as cursor:
            assert cursor.execute("select count(*) as n from report_records").fetchone()["n"] == before
            assert cursor.execute("select count(*) as n from report_objects").fetchone()["n"] == refs_before
        assert not list(session.runtime.paths.reports.glob("*.docx"))
