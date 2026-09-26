"""The first PostgreSQL revision remains a frozen copy of the SQLite schema."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "migrations/versions/20260926_01_business_schema.sql"
EXPECTED_TABLES = {
    "users", "report_records", "import_batches", "import_tasks",
    "qa_conversations", "qa_conversation_documents", "qa_messages",
    "qa_retry_requests", "qa_message_sources", "qa_jobs",
    "qa_deletion_fences", "qa_legacy_imports", "notes", "note_tags",
    "note_sources", "note_document_sources", "note_projection_tasks",
    "note_legacy_imports", "data_migrations", "learning_plans",
    "learning_plan_documents", "learning_tasks", "learning_requests",
    "learning_task_events", "learning_migrations",
}


def test_initial_snapshot_preserves_business_scope_and_isolation() -> None:
    snapshot = SNAPSHOT.read_text(encoding="utf-8")
    assert set(re.findall(r"create table if not exists (\w+)", snapshot)) == EXPECTED_TABLES
    assert "create virtual table" not in snapshot.lower()
    assert "id bigserial primary key" in snapshot
    assert "foreign key(batch_id, user_id) references import_batches(id, user_id)" in snapshot
    assert "foreign key(note_id, user_id) references notes(id, user_id)" in snapshot
    assert "references learning_plan_documents(user_id,plan_id,document_id)" in snapshot
    assert "on import_tasks(user_id) where status = 'running'" in snapshot
    assert "created_at text not null" in snapshot


def test_offline_sql_contains_business_tables(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@localhost/example")
    config = Config(str(ROOT / "alembic.ini"))
    output = tmp_path / "upgrade.sql"
    with output.open("w", encoding="utf-8") as stream:
        config.output_buffer = stream
        command.upgrade(config, "head", sql=True)
    sql = output.read_text(encoding="utf-8")
    assert "CREATE TABLE alembic_version" in sql
    assert set(re.findall(r"create table if not exists (\w+)", sql)) == EXPECTED_TABLES


@pytest.mark.parametrize("url", ["sqlite:///bad.db", "mysql://user@localhost/db"])
def test_non_postgres_url_rejected(monkeypatch, url) -> None:
    monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(RuntimeError, match="postgresql"):
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head", sql=True)


def test_missing_url_rejected(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head", sql=True)
