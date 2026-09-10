"""Storage primitive only; never call without a verified maintenance coordinator.

The caller owns source-path validation, backup, stopping all writers, and a
fresh server-owned ready_document_ids inventory for the given user. This module
does not expose a CLI or HTTP entry point and cannot attest operational safety.
"""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import UUID

from app.learning_legacy import validate_legacy_payload


class MigrationImportError(ValueError):
    """Static safe error code."""


@dataclass(frozen=True)
class ImportResult:
    replayed: bool
    plan_count: int
    task_count: int


def _insert(conn, table, columns, values):
    # table/columns are internal constants; all data is bound.
    conn.execute(f'insert into {table} ({columns}) values ({",".join("?" for _ in values)})', values)
    actual = conn.execute(f'select {columns} from {table} where user_id=? and {"plan_id" if table == "learning_plan_documents" else "id"}=?', values[:2]).fetchone()
    if actual is None or tuple(actual) != values:
        raise MigrationImportError('READBACK_MISMATCH')


def import_legacy_plans(db_path, *, user_id: str, payload: bytes, timezone_name: str,
                        ready_document_ids: frozenset[str], now: datetime) -> ImportResult:
    try:
        if (str(UUID(user_id)) != user_id or not isinstance(now, datetime) or
                now.tzinfo is None or now.utcoffset() is None or
                type(ready_document_ids) is not frozenset or
                any(type(item) is not str or not item for item in ready_document_ids)):
            raise ValueError()
        timestamp = now.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise MigrationImportError('INVALID_CONTEXT') from None
    validated = validate_legacy_payload(payload, timezone_name=timezone_name)
    plans = json.loads(validated.normalized_json)['plans']
    uri = Path(db_path).resolve().as_uri() + '?mode=rw'
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=10)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('pragma foreign_keys=on')
            if conn.execute('pragma foreign_keys').fetchone()[0] != 1:
                raise MigrationImportError('DATABASE_FAILURE')
            with conn:
                conn.execute('begin immediate')
                user = conn.execute('select status from users where id=?', (user_id,)).fetchone()
                if user is None or user['status'] != 'active':
                    raise MigrationImportError('USER_UNAVAILABLE')
                marker = conn.execute('select * from learning_migrations where user_id=? and migration_version=1', (user_id,)).fetchone()
                if marker:
                    if (marker['source_sha256'], marker['normalized_sha256'], marker['timezone']) != (
                            validated.source_sha256, validated.normalized_sha256, validated.timezone):
                        raise MigrationImportError('MIGRATION_CONFLICT')
                    return ImportResult(True, marker['imported_plan_count'], marker['imported_task_count'])
                for table in ('learning_plans', 'learning_plan_documents', 'learning_tasks', 'learning_requests', 'learning_task_events'):
                    if conn.execute(f'select 1 from {table} where user_id=? limit 1', (user_id,)).fetchone():
                        raise MigrationImportError('TARGET_NOT_EMPTY')
                for plan in plans:
                    document = plan['document_id']
                    if document not in ready_document_ids or conn.execute(
                            "select 1 from qa_deletion_fences where user_id=? and target_type='document' and target_id=? limit 1",
                            (user_id, document)).fetchone():
                        raise MigrationImportError('DOCUMENT_UNAVAILABLE')
                    _insert(conn, 'learning_plans',
                            'user_id,id,title,timezone,start_date,target_date,daily_minutes,status,version,created_at,updated_at',
                            (user_id, plan['id'], plan['title'], plan['timezone'], plan['start_date'], plan['target_date'],
                             plan['daily_minutes'], plan['status'], 1, plan['created_at'], plan['updated_at']))
                    _insert(conn, 'learning_plan_documents', 'user_id,plan_id,document_id,document_name',
                            (user_id, plan['id'], document, plan['document_name']))
                    for task in plan['tasks']:
                        _insert(conn, 'learning_tasks',
                                'user_id,id,plan_id,document_id,due_date,phase,title,duration_minutes,completed,completed_at,version',
                                (user_id, task['id'], plan['id'], document, task['due_date'], task['phase'], task['title'],
                                 task['duration_minutes'], int(task['completed']), task['completed_at'], 1))
                for table, expected in (('learning_plans', validated.plan_count), ('learning_tasks', validated.task_count)):
                    if conn.execute(f'select count(*) from {table} where user_id=?', (user_id,)).fetchone()[0] != expected:
                        raise MigrationImportError('READBACK_MISMATCH')
                conn.execute('''insert into learning_migrations
                    (user_id,source_sha256,migration_version,source_schema_version,timezone,imported_plan_count,
                     imported_task_count,normalized_sha256,completed_at) values (?,?,1,1,?,?,?,?,?)''',
                    (user_id, validated.source_sha256, validated.timezone, validated.plan_count,
                     validated.task_count, validated.normalized_sha256, timestamp))
            return ImportResult(False, validated.plan_count, validated.task_count)
    except sqlite3.Error:
        raise MigrationImportError('DATABASE_FAILURE') from None
