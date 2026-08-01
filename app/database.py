from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
create table if not exists users (
    id text primary key,
    username text not null,
    username_key text not null unique,
    password_hash text not null,
    status text not null default 'active',
    created_at text not null,
    updated_at text not null
);

create table if not exists report_records (
    id text primary key,
    user_id text not null references users(id) on delete cascade,
    title text not null,
    relative_path text not null,
    created_at text not null
);

create table if not exists import_batches (
    id text primary key,
    user_id text not null references users(id) on delete cascade,
    created_at text not null,
    updated_at text not null,
    unique(id, user_id)
);

create table if not exists import_tasks (
    id text primary key,
    batch_id text not null,
    user_id text not null,
    document_id text not null,
    original_name text not null,
    file_suffix text not null,
    size_bytes integer not null,
    staged_relative_path text not null,
    status text not null check(status in ('queued','running','retry_wait','succeeded','failed')),
    stage text not null,
    progress integer not null check(progress between 0 and 100),
    total_attempt_count integer not null default 0,
    auto_retry_count integer not null default 0,
    manual_retry_count integer not null default 0,
    max_auto_retries integer not null default 3,
    next_attempt_at text,
    error_code text,
    error_summary text,
    created_at text not null,
    started_at text,
    finished_at text,
    updated_at text not null,
    foreign key(batch_id, user_id) references import_batches(id, user_id)
        on delete cascade,
    unique(user_id, document_id)
);

create unique index if not exists uq_import_tasks_running_user
on import_tasks(user_id) where status = 'running';
create index if not exists ix_import_tasks_scheduler
on import_tasks(status, next_attempt_at, created_at);
create index if not exists ix_import_tasks_user_created
on import_tasks(user_id, created_at);

create table if not exists data_migrations (
    id integer primary key autoincrement,
    migration_key text not null unique,
    claimed_by_user_id text references users(id),
    status text not null,
    backup_path text,
    manifest_path text,
    skipped_summary text,
    conflict_summary text,
    started_at text not null,
    completed_at text,
    error_summary text
);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    return conn


def initialize_database(db_path: Path | str) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        # F1: idempotent upgrade for existing databases missing
        # the conflict_summary column.
        _ensure_column(conn, "data_migrations", "conflict_summary", "text")


def _ensure_column(conn, table: str, column: str, col_type: str) -> None:
    """Add *column* to *table* if it does not already exist."""
    rows = conn.execute(f"pragma table_info('{table}')").fetchall()
    existing = {row["name"] for row in rows}
    if column not in existing:
        conn.execute(
            f"alter table {table} add column {column} {col_type}"
        )


@contextmanager
def transaction(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
