"""Real PostgreSQL acceptance checks for the isolated relational copy."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import uuid

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import make_url

from app.database import initialize_database
from deploy.migrate_relational_isolated import MigrationError, TABLES, migrate_relational


@pytest.fixture
def target(monkeypatch):
    base_url = os.environ.get("POSTGRES_TEST_URL")
    if not base_url:
        pytest.skip("POSTGRES_TEST_URL is required")
    schema = "cutover_" + uuid.uuid4().hex
    with psycopg.connect(base_url, autocommit=True) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
    url = make_url(base_url).set(
        drivername="postgresql+psycopg", query={"options": f"-csearch_path={schema}"}
    ).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    try:
        command.upgrade(Config("alembic.ini"), "head")
        yield schema, base_url
    finally:
        with psycopg.connect(base_url, autocommit=True) as admin:
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "app.db"
    initialize_database(path)
    now = "2026-09-26T01:02:03Z"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "insert into users(id,username,username_key,password_hash,created_at,updated_at) "
            "values (?,?,?,?,?,?)", ("u1", "甲", "user1", "hash", now, now)
        )
        connection.execute(
            "insert into qa_conversations(id,user_id,title,origin,created_at,updated_at,last_message_at) "
            "values (?,?,?,?,?,?,?)", ("q1", "u1", "Conversation", "product", now, now, now)
        )
        # Child sorts before parent by SQLite row order; the migration must reorder.
        connection.execute(
            "insert into qa_messages(id,conversation_id,user_id,turn_id,role,status,"
            "retry_of_message_id,created_at,updated_at) values (?,?,?,?,?,?,?,?,?)",
            ("child", "q1", "u1", "turn2", "assistant", "failed", "parent", now, now),
        )
        connection.execute(
            "insert into qa_messages(id,conversation_id,user_id,turn_id,role,status,"
            "created_at,updated_at) values (?,?,?,?,?,?,?,?)",
            ("parent", "q1", "u1", "turn1", "assistant", "failed", now, now),
        )
        connection.execute(
            "insert into notes(id,user_id,body_markdown,client_request_id,request_digest,created_at,updated_at) "
            "values (?,?,?,?,?,?,?)", ("n1", "u1", "body", "req1", "digest", now, now)
        )
        connection.execute(
            "insert into data_migrations(id,migration_key,status,started_at) values (?,?,?,?)",
            (19, "seed", "completed", now),
        )
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migrate(path, target, mode, **overrides):
    schema, url = target
    return migrate_relational(
        path, expected_sha256=overrides.get("expected_sha256", digest(path)),
        database_url=url, target_schema=schema, mode=mode,
    )


def test_complete_copy_idempotency_and_verify(source, target):
    schema, url = target
    before_sha = digest(source)
    dry = migrate(source, target, "dry-run")
    assert dry["status"] == "ready"
    assert dry["tables"]["users"]["target_count"] == 0
    with psycopg.connect(url) as conn:
        assert conn.execute(sql.SQL("select count(*) from {}").format(
            sql.Identifier(schema, "users")
        )).fetchone() == (0,)
    applied = migrate(source, target, "apply")
    assert applied["status"] == "applied"
    assert len(applied["tables"]) == len(TABLES) == 25
    assert not applied["discrepancies"]
    assert applied["inserted_row_counts"]["qa_messages"] == 2
    assert migrate(source, target, "apply")["status"] == "unchanged"
    assert migrate(source, target, "verify")["status"] == "equal"
    assert digest(source) == before_sha
    with psycopg.connect(url) as conn:
        assert conn.execute(sql.SQL("select nextval(pg_get_serial_sequence(%s,'id'))"),
                            (f'"{schema}"."data_migrations"',)).fetchone() == (20,)


def test_nonempty_target_is_preserved_and_verify_rejects(source, target):
    schema, url = target
    with psycopg.connect(url) as conn:
        conn.execute(sql.SQL("insert into {} (id,username,username_key,password_hash,created_at,updated_at) "
                             "values ('other','Other','other','hash','t','t')").format(
            sql.Identifier(schema, "users")
        ))
    with pytest.raises(MigrationError, match="Nonempty target"):
        migrate(source, target, "apply")
    with pytest.raises(MigrationError, match="differs"):
        migrate(source, target, "verify")
    with psycopg.connect(url) as conn:
        assert conn.execute(sql.SQL("select id from {}").format(
            sql.Identifier(schema, "users")
        )).fetchall() == [("other",)]


def test_wrong_digest_and_transaction_rollback(source, target):
    with pytest.raises(MigrationError, match="SHA-256"):
        migrate(source, target, "apply", expected_sha256="0" * 64)
    with sqlite3.connect(source) as conn:
        conn.execute("update users set username = ? where id = 'u1'", (b"not-text",))
    with pytest.raises(MigrationError, match="Post-copy verification failed"):
        migrate(source, target, "apply")
    schema, url = target
    with psycopg.connect(url) as conn:
        for table in TABLES:
            assert conn.execute(sql.SQL("select count(*) from {}").format(
                sql.Identifier(schema, table)
            )).fetchone() == (0,)


def test_unknown_source_table_is_rejected_before_target_contact(source):
    with sqlite3.connect(source) as conn:
        conn.execute("create table unexpected_business_data (value text)")
    with pytest.raises(MigrationError, match="baseline mismatch"):
        migrate_relational(source, expected_sha256=digest(source), database_url="unused",
                           target_schema="cutover_test", mode="dry-run")


def test_live_journal_sibling_is_rejected(source):
    journal = source.with_name(source.name + "-wal")
    journal.write_bytes(b"live")
    with pytest.raises(MigrationError, match="journal siblings"):
        migrate_relational(source, expected_sha256=digest(source), database_url="unused",
                           target_schema="cutover_test", mode="dry-run")
