"""Shared auth/session behavior in a disposable PostgreSQL schema."""

from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import make_url

from app.auth import AuthError
from app.postgres import PostgresDatabase
from app.postgres_auth import PostgresAuthService
from app.postgres_sessions import PostgresSessionRepository
from app.session import InvalidCsrfTokenError, InvalidSessionError


@pytest.fixture
def shared_database(monkeypatch):
    base = os.environ.get("POSTGRES_TEST_URL")
    if not base:
        pytest.skip("POSTGRES_TEST_URL is required")
    schema = "auth_test_" + uuid.uuid4().hex
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
    url = make_url(base).set(drivername="postgresql+psycopg",
                             query={"options": f"-csearch_path={schema}"})
    url = url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    databases = []

    def open_pool():
        database = PostgresDatabase(url.replace("postgresql+psycopg://", "postgresql://"),
                                    min_size=1, max_size=3)
        database.open()
        databases.append(database)
        return database

    try:
        yield open_pool, url
    finally:
        for database in databases:
            database.close()
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))


def test_auth_normalization_password_and_status(shared_database):
    open_pool, _ = shared_database
    db = open_pool()
    auth = PostgresAuthService(db)
    user = auth.register("  Alice  ", "correct-password")
    assert auth.authenticate("ＡＬＩＣＥ", "correct-password") == user
    with pytest.raises(AuthError, match="already exists"):
        auth.register("alice", "another-password")
    with pytest.raises(AuthError, match="Invalid username or password"):
        auth.authenticate("Alice", "wrong-password")
    with db.transaction() as cursor:
        cursor.execute("update users set status='disabled' where id=%s", (user.id,))
    with pytest.raises(AuthError, match="Invalid username or password"):
        auth.authenticate("Alice", "correct-password")


def test_shared_session_csrf_logout_restart_and_hash(shared_database):
    open_pool, url = shared_database
    first = open_pool()
    second = open_pool()
    user = PostgresAuthService(first).register("Alice", "correct-password")
    creator = PostgresSessionRepository(first)
    reader = PostgresSessionRepository(second)
    session = creator.create(user.id)
    assert session.token not in repr(session)
    assert session.csrf_token not in repr(session)
    assert reader.validate_csrf(session.token, session.csrf_token).user_id == user.id
    with pytest.raises(InvalidCsrfTokenError):
        reader.validate_csrf(session.token, "bad")
    with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://")) as conn:
        stored = conn.execute("select token_hash from auth_sessions").fetchone()[0]
        assert stored == hashlib.sha256(session.token.encode()).hexdigest()
        assert session.token not in stored
    first.close()
    restarted = open_pool()
    assert PostgresSessionRepository(restarted).get(session.token).username == "Alice"
    reader.delete(session.token)
    reader.delete(session.token)
    with pytest.raises(InvalidSessionError):
        PostgresSessionRepository(restarted).get(session.token)


def test_expiry_refresh_capacity_and_disabled_user(shared_database):
    open_pool, _ = shared_database
    db = open_pool()
    user = PostgresAuthService(db).register("Alice", "correct-password")
    repo = PostgresSessionRepository(db, timedelta(hours=1), max_sessions=1)
    session = repo.create(user.id)
    with pytest.raises(InvalidSessionError, match="Too many"):
        repo.create(user.id)
    with db.transaction() as cursor:
        cursor.execute("update auth_sessions set expires_at=clock_timestamp()+interval '1 second'")
    refreshed = repo.get(session.token)
    with db.transaction() as cursor:
        expiry = cursor.execute("select expires_at from auth_sessions").fetchone()["expires_at"]
    assert expiry > refreshed.last_accessed_at + timedelta(minutes=59)
    with db.transaction() as cursor:
        cursor.execute("update auth_sessions set expires_at=clock_timestamp()-interval '1 second'")
    with pytest.raises(InvalidSessionError):
        repo.get(session.token)
    replacement = repo.create(user.id)
    with db.transaction() as cursor:
        cursor.execute("update users set status='disabled' where id=%s", (user.id,))
    with pytest.raises(InvalidSessionError):
        repo.get(replacement.token)


def test_concurrent_creates_obey_global_capacity(shared_database):
    open_pool, _ = shared_database
    db1, db2 = open_pool(), open_pool()
    user = PostgresAuthService(db1).register("Alice", "correct-password")
    barrier = threading.Barrier(2)

    def create(db):
        barrier.wait()
        try:
            return PostgresSessionRepository(db, max_sessions=1).create(user.id)
        except InvalidSessionError:
            return None

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(create, (db1, db2)))
    assert sum(result is not None for result in results) == 1


def test_waiting_refresh_cannot_revive_expired_session(shared_database):
    open_pool, url = shared_database
    db1, db2 = open_pool(), open_pool()
    user = PostgresAuthService(db1).register("Alice", "correct-password")
    session = PostgresSessionRepository(db1).create(user.id)
    started = threading.Event()
    outcomes = []

    def refresh():
        started.set()
        try:
            PostgresSessionRepository(db2).get(session.token)
            outcomes.append("refreshed")
        except InvalidSessionError:
            outcomes.append("expired")

    with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://")) as holder:
        holder.execute("select token_hash from auth_sessions where token_hash=%s for update",
                       (hashlib.sha256(session.token.encode()).hexdigest(),))
        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(refresh)
            assert started.wait(timeout=5)
            with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://"),
                                 autocommit=True) as observer:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    blocked = observer.execute(
                        "select exists(select 1 from pg_stat_activity "
                        "where wait_event_type='Lock' and query like "
                        "'select s.user_id,s.csrf_token,s.expires_at,u.username,u.status%')"
                    ).fetchone()[0]
                    if blocked:
                        break
                if not blocked:
                    holder.commit()
                    future.result(timeout=10)
                assert blocked, "refresh did not wait for the session row lock"
            holder.execute("update auth_sessions set expires_at=clock_timestamp()-interval '1 second'")
            holder.commit()
            future.result(timeout=10)
    assert outcomes == ["expired"]


def test_downgrade_refuses_live_sessions(shared_database):
    open_pool, _ = shared_database
    db = open_pool()
    user = PostgresAuthService(db).register("Alice", "correct-password")
    PostgresSessionRepository(db).create(user.id)
    with pytest.raises(RuntimeError, match="live session data"):
        command.downgrade(Config("alembic.ini"), "20260926_01")
