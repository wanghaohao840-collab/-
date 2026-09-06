import sqlite3

import pytest

from app.database import connect, initialize_database


def _create_legacy_import_schema_and_task(db_path):
    with connect(db_path) as connection:
        connection.executescript(
            """
            create table users (
                id text primary key,
                username text not null,
                username_key text not null unique,
                password_hash text not null,
                status text not null default 'active',
                created_at text not null,
                updated_at text not null
            );

            create table import_batches (
                id text primary key,
                user_id text not null references users(id) on delete cascade,
                created_at text not null,
                updated_at text not null,
                unique(id, user_id)
            );

            create table import_tasks (
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
            """
        )
        connection.execute(
            """
            insert into users (id, username, username_key, password_hash, created_at, updated_at)
            values ('user-a', 'user-a', 'user-a', 'hash', '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z')
            """
        )
        connection.execute(
            """
            insert into import_batches (id, user_id, created_at, updated_at)
            values ('batch-a', 'user-a', '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z')
            """
        )
        _insert_task(connection, "task-a", "document-a", "queued")


def _insert_task(connection, task_id, document_id, status):
    connection.execute(
        """
        insert into import_tasks (
            id, batch_id, user_id, document_id, original_name, file_suffix,
            size_bytes, staged_relative_path, status, stage, progress,
            created_at, updated_at
        ) values (?, 'batch-a', 'user-a', ?, 'document.md', '.md', 1,
                  'imports/batch-a/document.md', ?, 'queued', 0,
                  '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z')
        """,
        (task_id, document_id, status),
    )


def test_initialize_database_upgrades_legacy_import_tasks(tmp_path):
    db_path = tmp_path / "legacy.db"
    _create_legacy_import_schema_and_task(db_path)

    initialize_database(db_path)

    with connect(db_path) as connection:
        connection.execute("update import_tasks set status = 'paused' where id = 'task-a'")
        task = connection.execute(
            "select status, control_requested_at, control_claimed_at "
            "from import_tasks where id = 'task-a'"
        ).fetchone()
        batch = connection.execute(
            "select lifecycle_state from import_batches where id = 'batch-a'"
        ).fetchone()
        event_table = connection.execute(
            "select name from sqlite_master where name = 'import_task_events'"
        ).fetchone()
        connection.execute(
            """
            insert into import_task_events (
                batch_id, task_id, user_id, event_type, status, stage, message, created_at
            ) values ('batch-a', 'task-a', 'user-a', 'paused', 'paused', 'paused', null,
                      '2026-08-01T00:01:00Z')
            """
        )

    initialize_database(db_path)

    with connect(db_path) as connection:
        preserved_task = connection.execute(
            "select status from import_tasks where id = 'task-a'"
        ).fetchone()
        preserved_events = connection.execute(
            "select count(*) as count from import_task_events where task_id = 'task-a'"
        ).fetchone()
        _insert_task(connection, "task-running", "document-running", "running")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_task(
                connection,
                "task-pause-requested",
                "document-pause-requested",
                "pause_requested",
            )
        _insert_task(
            connection,
            "task-cancel-requested-a",
            "document-cancel-requested-a",
            "cancel_requested",
        )
        _insert_task(
            connection,
            "task-cancel-requested-b",
            "document-cancel-requested-b",
            "cancel_requested",
        )

    assert tuple(task) == ("paused", None, None)
    assert batch["lifecycle_state"] == "active"
    assert event_table is not None
    assert preserved_task["status"] == "paused"
    assert preserved_events["count"] == 1


def test_initialize_database_creates_control_schema_for_new_databases(tmp_path):
    db_path = tmp_path / "fresh.db"

    initialize_database(db_path)

    with connect(db_path) as connection:
        task_columns = {
            row["name"] for row in connection.execute("pragma table_info(import_tasks)")
        }
        batch_columns = {
            row["name"] for row in connection.execute("pragma table_info(import_batches)")
        }
        event_indexes = {
            row["name"] for row in connection.execute("pragma index_list(import_task_events)")
        }

    assert {"control_requested_at", "control_claimed_at"} <= task_columns
    assert {
        "lifecycle_state",
        "delete_requested_at",
        "cleanup_error_code",
        "cleanup_error_summary",
        "cleanup_attempt_count",
    } <= batch_columns
    assert {"ix_import_task_events_user_task_created", "ix_import_task_events_user_batch_created"} <= event_indexes
