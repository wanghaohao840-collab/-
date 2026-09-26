"""PostgreSQL connection lifecycle for distributed repositories."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from threading import Lock
from typing import Any, Iterator

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


class PostgresDatabase:
    def __init__(
        self,
        database_url: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
        timeout: float = 10.0,
        pool: Any | None = None,
    ) -> None:
        if not database_url:
            raise ValueError("database URL is required")
        if min_size < 1 or max_size < 1 or min_size > max_size:
            raise ValueError("PostgreSQL pool sizes are invalid")
        self._pool = pool or ConnectionPool(
            conninfo=database_url,
            min_size=min_size,
            max_size=max_size,
            timeout=timeout,
            kwargs={"row_factory": dict_row},
            open=False,
        )
        self._lock = Lock()
        self._opened = False

    def __repr__(self) -> str:
        return f"PostgresDatabase(opened={self._opened})"

    def open(self) -> None:
        with self._lock:
            if not self._opened:
                self._pool.open(wait=True)
                self._opened = True

    def close(self) -> None:
        with self._lock:
            if self._opened:
                self._pool.close()
                self._opened = False

    @contextmanager
    def connection(self) -> Iterator[Any]:
        with self._pool.connection() as connection:
            yield connection

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self.connection() as connection:
            cursor = connection.cursor()
            try:
                yield cursor
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()

    def ping(self) -> bool:
        with self.connection() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute("select 1")
            finally:
                cursor.close()
        return True

    def server_now(self) -> datetime:
        with self.connection() as connection:
            cursor = connection.cursor()
            try:
                row = cursor.execute("select clock_timestamp() as server_now").fetchone()
            finally:
                cursor.close()
        if row is None:
            raise RuntimeError("PostgreSQL did not return server time")
        return row["server_now"]
