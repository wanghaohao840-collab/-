"""Opt-in checks against an isolated schema in disposable PostgreSQL."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def postgres_schema(monkeypatch):
    base_url = os.environ.get("POSTGRES_TEST_URL")
    if not base_url:
        pytest.skip("POSTGRES_TEST_URL is required for PostgreSQL integration")
    schema = "test_schema_" + uuid.uuid4().hex
    with psycopg.connect(base_url, autocommit=True) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
    url = make_url(base_url).set(
        drivername="postgresql+psycopg", query={"options": f"-csearch_path={schema}"}
    )
    monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
    try:
        yield schema, base_url, url.render_as_string(hide_password=False)
    finally:
        with psycopg.connect(base_url, autocommit=True) as admin:
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))


def test_upgrade_twice_and_business_constraints(postgres_schema):
    schema, base_url, _url = postgres_schema
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "20260926_01")
    command.upgrade(config, "20260926_01")
    with psycopg.connect(base_url, options=f"-csearch_path={schema}") as connection:
        rows = connection.execute(
            "select version_num from alembic_version"
        ).fetchall()
        assert rows == [("20260926_01",)]
        tables = {row[0] for row in connection.execute(
            "select tablename from pg_catalog.pg_tables where schemaname = %s", (schema,)
        )}
        assert len(tables) == 26  # 25 business tables plus Alembic's ledger.
        now = "2026-09-26T00:00:00Z"
        connection.execute(
            "insert into users(id, username, username_key, password_hash, created_at, updated_at) "
            "values (%s, %s, %s, %s, %s, %s), (%s, %s, %s, %s, %s, %s)",
            ("u1", "One", "one", "hash", now, now, "u2", "Two", "two", "hash", now, now),
        )
        connection.execute(
            "insert into import_batches(id,user_id,created_at,updated_at) "
            "values ('b1','u1',%s,%s), ('b2','u2',%s,%s)", (now, now, now, now)
        )

        def insert_task(task_id, batch_id, user_id, document_id, status="running"):
            connection.execute(
                "insert into import_tasks(id,batch_id,user_id,document_id,original_name,"
                "file_suffix,size_bytes,staged_relative_path,status,stage,progress,created_at,updated_at) "
                "values (%s,%s,%s,%s,'a.pdf','.pdf',1,'stage/a.pdf',%s,'parse',0,%s,%s)",
                (task_id, batch_id, user_id, document_id, status, now, now),
            )

        insert_task("t1", "b1", "u1", "d1")
        with pytest.raises(psycopg.errors.UniqueViolation):
            with connection.transaction():
                insert_task("t2", "b1", "u1", "d2")
        insert_task("t3", "b2", "u2", "d3")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with connection.transaction():
                insert_task("t4", "b1", "u2", "d4", "queued")
        assert connection.execute("select count(*) from import_tasks").fetchone() == (2,)
        connection.commit()

    with pytest.raises(RuntimeError, match="paired database backup"):
        command.downgrade(config, "base")
    with psycopg.connect(base_url, options=f"-csearch_path={schema}") as connection:
        assert connection.execute("select count(*) from import_tasks").fetchone() == (2,)


def test_existing_business_table_refused(postgres_schema):
    schema, base_url, _url = postgres_schema
    with psycopg.connect(base_url, options=f"-csearch_path={schema}") as connection:
        connection.execute("create table users (id integer primary key)")
        connection.commit()
    with pytest.raises(RuntimeError, match="Business tables already exist"):
        command.upgrade(Config(str(ROOT / "alembic.ini")), "20260926_01")
    with psycopg.connect(base_url, options=f"-csearch_path={schema}") as connection:
        assert connection.execute("select count(*) from users").fetchone() == (0,)
        assert connection.execute(
            "select count(*) from pg_catalog.pg_tables where schemaname = current_schema() "
            "and tablename = 'import_batches'"
        ).fetchone() == (0,)
