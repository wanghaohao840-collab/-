from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from app.postgres import PostgresDatabase


class FakeCursor:
    def __init__(self, row=None):
        self.row = row
        self.statements = []
        self.closed = False

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        return self

    def fetchone(self):
        return self.row

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakePool:
    def __init__(self, connection):
        self._connection = connection
        self.opens = 0
        self.closes = 0

    def open(self, *, wait):
        self.opens += 1

    def close(self):
        self.closes += 1

    @contextmanager
    def connection(self):
        yield self._connection


def test_pool_lifecycle_and_secret_redaction():
    pool = FakePool(FakeConnection(FakeCursor()))
    database = PostgresDatabase("postgresql://user:secret@host/db", pool=pool)

    database.open()
    database.open()
    database.close()
    database.close()

    assert (pool.opens, pool.closes) == (1, 1)
    assert "secret" not in repr(database)


def test_transaction_commits_and_rolls_back():
    connection = FakeConnection(FakeCursor())
    database = PostgresDatabase("postgresql://redacted", pool=FakePool(connection))

    with database.transaction() as cursor:
        cursor.execute("select %s", (1,))
    assert connection.commits == 1
    assert connection._cursor.closed

    with pytest.raises(RuntimeError):
        with database.transaction():
            raise RuntimeError("boom")
    assert connection.rollbacks == 1


def test_ping_and_server_clock_are_database_queries():
    expected = datetime(2026, 9, 26, tzinfo=timezone.utc)
    cursor = FakeCursor({"server_now": expected})
    database = PostgresDatabase(
        "postgresql://redacted", pool=FakePool(FakeConnection(cursor))
    )

    assert database.ping()
    assert database.server_now() == expected
    assert cursor.statements == [
        ("select 1", None),
        ("select clock_timestamp() as server_now", None),
    ]


def test_invalid_pool_size_is_rejected():
    with pytest.raises(ValueError, match="pool sizes"):
        PostgresDatabase("postgresql://redacted", min_size=5, max_size=3)
