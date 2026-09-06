from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def _import_tasks_table_sql(table_name: str) -> str:
    return f"""
create table {table_name} (
    id text primary key,
    batch_id text not null,
    user_id text not null,
    document_id text not null,
    original_name text not null,
    file_suffix text not null,
    size_bytes integer not null,
    staged_relative_path text not null,
    status text not null check(status in (
        'queued','running','retry_wait','pause_requested','paused',
        'cancel_requested','cancelled','succeeded','failed'
    )),
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
    control_requested_at text,
    control_claimed_at text,
    foreign key(batch_id, user_id) references import_batches(id, user_id)
        on delete cascade,
    unique(user_id, document_id)
);
"""


IMPORT_TASK_INDEXES = """
create unique index if not exists uq_import_tasks_running_user
on import_tasks(user_id) where status in ('running', 'pause_requested');
create index if not exists ix_import_tasks_scheduler
on import_tasks(status, next_attempt_at, created_at);
create index if not exists ix_import_tasks_user_created
on import_tasks(user_id, created_at);
"""


IMPORT_TASK_EVENTS_SCHEMA = """
create table if not exists import_task_events (
    id integer primary key autoincrement,
    batch_id text not null,
    task_id text not null,
    user_id text not null,
    event_type text not null,
    status text not null,
    stage text not null,
    message text,
    created_at text not null,
    foreign key(batch_id, user_id) references import_batches(id, user_id)
        on delete cascade,
    foreign key(task_id) references import_tasks(id) on delete cascade
);
create index if not exists ix_import_task_events_user_task_created
on import_task_events(user_id, task_id, created_at, id);
create index if not exists ix_import_task_events_user_batch_created
on import_task_events(user_id, batch_id, created_at, id);
"""


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
    lifecycle_state text not null default 'active',
    delete_requested_at text,
    cleanup_error_code text,
    cleanup_error_summary text,
    unique(id, user_id)
);

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
""" + _import_tasks_table_sql("if not exists import_tasks") + IMPORT_TASK_INDEXES


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
        _ensure_import_control_schema(conn)
        conn.executescript(IMPORT_TASK_EVENTS_SCHEMA)
        # F1: idempotent upgrade for existing databases missing
        # the conflict_summary column.
        _ensure_column(conn, "data_migrations", "conflict_summary", "text")


def _ensure_import_control_schema(conn: sqlite3.Connection) -> None:
    table = conn.execute(
        "select sql from sqlite_master where type = 'table' and name = 'import_tasks'"
    ).fetchone()
    if table is not None and "pause_requested" not in table["sql"].lower():
        _rebuild_import_tasks_with_controls(conn)
    _ensure_column(
        conn, "import_batches", "lifecycle_state", "text not null default 'active'"
    )
    _ensure_column(conn, "import_batches", "delete_requested_at", "text")
    _ensure_column(conn, "import_batches", "cleanup_error_code", "text")
    _ensure_column(conn, "import_batches", "cleanup_error_summary", "text")
    _ensure_column(conn, "import_batches", "cleanup_attempt_count", "integer not null default 0")
    conn.execute(
        """create index if not exists ix_import_batches_deletion_recovery
           on import_batches(cleanup_attempt_count, delete_requested_at, created_at, id)
           where lifecycle_state = 'deleting'"""
    )
    conn.executescript(IMPORT_TASK_INDEXES)


def _rebuild_import_tasks_with_controls(conn: sqlite3.Connection) -> None:
    columns = [row["name"] for row in conn.execute("pragma table_info(import_tasks)")]
    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    conn.execute(_import_tasks_table_sql("import_tasks_control_upgrade"))
    conn.execute(
        "insert into import_tasks_control_upgrade "
        f"({quoted_columns}, control_requested_at, control_claimed_at) "
        f"select {quoted_columns}, null, null from import_tasks"
    )
    conn.execute("drop table import_tasks")
    conn.execute("alter table import_tasks_control_upgrade rename to import_tasks")


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
