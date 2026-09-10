from __future__ import annotations

import sqlite3

import pytest

from app.database import connect, initialize_database


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "app.db"
    initialize_database(path)
    conn = connect(path)
    for user in ("owner", "other"):
        conn.execute(
            "insert into users values (?, ?, ?, ?, ?, ?, ?)",
            (user, user, user, "hash", "active", "2026-09-06", "2026-09-06"),
        )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def plan(db, user="owner", identifier="p"):
    db.execute(
        "insert into learning_plans values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user, identifier, "学习计划", "Asia/Shanghai", "2026-09-06", "2026-09-07",
         30, "active", 1, "2026-09-06T00:00:00Z", "2026-09-06T00:00:00Z"),
    )
    db.execute("insert into learning_plan_documents values (?, ?, ?, ?)",
                 (user, identifier, "d", "资料"))


def task(db, user="owner", identifier="t"):
    db.execute(
        "insert into learning_tasks values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user, identifier, "p", "d", "2026-09-06", "reading", "阅读", 30, 0, None, 1),
    )


def test_schema_replay_preserves_existing_data(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    # Build the immediately preceding schema, without learning tables.
    with monkeypatch.context() as patch:
        patch.setattr("app.database.LEARNING_SCHEMA", "")
        initialize_database(path)
    with connect(path) as db:
        db.execute("insert into users values ('owner','owner','owner','hash','active','now','now')")
        db.execute("insert into notes (id,user_id,body_markdown,client_request_id,request_digest,created_at,updated_at) values ('n','owner','保留学习笔记','r','digest','now','now')")
        before = {row[0] for row in db.execute("select name from sqlite_master where type='table'")}
        note_before = tuple(db.execute("select * from notes where id='n'").fetchone())
    initialize_database(path)
    initialize_database(path)
    with connect(path) as db:
        after = {row[0] for row in db.execute("select name from sqlite_master where type='table'")}
        assert after - before == {
            "learning_plans", "learning_plan_documents", "learning_tasks",
            "learning_task_events", "learning_requests", "learning_migrations",
        }
        assert before <= after
        assert tuple(db.execute("select * from notes where id='n'").fetchone()) == note_before
        assert db.execute("select password_hash from users where id='owner'").fetchone()[0] == "hash"
        assert {name for name in after if name.startswith("learning_")} == {
            "learning_plans", "learning_plan_documents", "learning_tasks",
            "learning_task_events", "learning_requests", "learning_migrations",
        }
        assert len(db.execute("select name from sqlite_master where type='index' and name like 'ix_learning_%'").fetchall()) == 5
        assert db.execute("pragma foreign_key_check").fetchall() == []


def test_cross_user_and_document_relationships_rejected(db):
    plan(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("insert into learning_plan_documents values ('other','p','d','资料')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("insert into learning_tasks values ('owner','t','p','wrong','2026-09-06','reading','阅读',30,0,null,1)")
    task(db)
    with pytest.raises(sqlite3.IntegrityError):
        task(db, identifier="duplicate-date")


@pytest.mark.parametrize("column,value", [
    ("duration_minutes", 4), ("duration_minutes", 481), ("completed", 2),
    ("completed", 1), ("completed_at", "2026-09-06T00:00:00Z"),
    ("version", 0), ("phase", "unknown"),
])
def test_invalid_task_state_rejected(db, column, value):
    plan(db)
    task(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(f"update learning_tasks set {column}=? where user_id='owner'", (value,))


def test_cascade_retains_request_and_migration_not_other_user(db):
    for user in ("owner", "other"):
        plan(db, user)
        task(db, user)
        db.execute("insert into learning_requests values (?, 'r','set_task_state','digest','t',2,'now')", (user,))
        db.execute("insert into learning_task_events values (?, 'e','p','t','r',0,1,'now',2)", (user,))
        db.execute("insert into learning_migrations values (?, 'hash',1,1,'Asia/Shanghai',1,1,'normalized','now')", (user,))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("insert into learning_requests values ('owner','r','set_task_state','other','t',2,'now')")
    db.execute("delete from learning_plans where user_id='owner'")
    for table in ("learning_plans", "learning_plan_documents", "learning_tasks", "learning_task_events"):
        assert db.execute(f"select count(*) from {table} where user_id='owner'").fetchone()[0] == 0
        assert db.execute(f"select count(*) from {table} where user_id='other'").fetchone()[0] == 1
    for table in ("learning_requests", "learning_migrations"):
        assert db.execute(f"select count(*) from {table} where user_id='owner'").fetchone()[0] == 1
    db.execute("delete from users where id='other'")
    assert db.execute("pragma foreign_key_check").fetchall() == []
    for table in ("learning_plans", "learning_plan_documents", "learning_tasks", "learning_task_events", "learning_requests", "learning_migrations"):
        assert db.execute(f"select count(*) from {table} where user_id='other'").fetchone()[0] == 0
