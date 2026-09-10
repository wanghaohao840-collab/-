from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.database import connect, transaction
from app.learning_models import (
    CreateLearningPlan, SetLearningTaskState, LearningPlan, LearningTask,
    LearningPlanMutation, LearningTaskMutation, LearningValidationError,
    LearningNotFound, LearningVersionConflict, LearningIdempotencyConflict,
    LearningDocumentDeleting,
)
from app.learning_schedule import allocate_phases, plan_dates
from app.learning_queries import read_page
from app.learning_models import Page


def _timestamp(now: datetime) -> str:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise LearningValidationError('aware datetime required')
    return now.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _request_id(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise LearningValidationError('invalid request id') from None


def _digest(payload: dict) -> str:
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _fenced(conn, user_id, document_id) -> bool:
    return conn.execute(
        "select 1 from qa_deletion_fences where user_id=? and target_type='document' and target_id=? limit 1",
        (user_id, document_id),
    ).fetchone() is not None


def _replay(conn, user_id, request_id, operation, digest):
    row = conn.execute('select * from learning_requests where user_id=? and request_id=?',
                       (user_id, request_id)).fetchone()
    if row and (row['operation'] != operation or row['request_digest'] != digest):
        raise LearningIdempotencyConflict()
    return row


def _record(conn, user_id, request_id, operation, digest, resource_id, version, timestamp):
    conn.execute('insert into learning_requests values (?,?,?,?,?,?,?)',
                 (user_id, request_id, operation, digest, resource_id, version, timestamp))


class LearningRepository:
    """Persistence only: callers must authorize the user and ready document."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)

    def _plan(self, conn, user_id, plan_id) -> LearningPlan:
        row = conn.execute(
            '''select p.*, d.document_id,d.document_name,
               (select count(*) from learning_tasks t where t.user_id=p.user_id and t.plan_id=p.id) task_count,
               (select count(*) from learning_tasks t where t.user_id=p.user_id and t.plan_id=p.id and t.completed=1) completed_count
               from learning_plans p join learning_plan_documents d on d.user_id=p.user_id and d.plan_id=p.id
               where p.user_id=? and p.id=?''', (user_id, plan_id),
        ).fetchone()
        if row is None or _fenced(conn, user_id, row['document_id']):
            raise LearningNotFound()
        return LearningPlan(**{key: row[key] for key in LearningPlan.__dataclass_fields__})

    def _task(self, conn, user_id, task_id) -> LearningTask:
        row = conn.execute('select * from learning_tasks where user_id=? and id=?',
                           (user_id, task_id)).fetchone()
        if row is None or _fenced(conn, user_id, row['document_id']):
            raise LearningNotFound()
        values = {key: row[key] for key in LearningTask.__dataclass_fields__}
        values['completed'] = bool(values['completed'])
        return LearningTask(**values)

    def get_plan(self, user_id: str, plan_id: str) -> LearningPlan:
        with closing(connect(self.db_path)) as conn:
            conn.execute('begin')
            return self._plan(conn, user_id, plan_id)

    def list_plans(self, user_id: str, *, cursor: str | None = None, limit: int = 20) -> Page[LearningPlan]:
        return read_page(self, user_id, kind='plans', cursor=cursor, limit=limit)

    def list_tasks(self, user_id: str, plan_id: str, *, cursor: str | None = None, limit: int = 20) -> Page[LearningTask]:
        return read_page(self, user_id, kind='tasks', plan_id=plan_id, cursor=cursor, limit=limit)

    def today(self, user_id: str, *, bucket: str, now: datetime, cursor: str | None = None, limit: int = 20) -> Page[LearningTask]:
        return read_page(self, user_id, kind='today', bucket=bucket, now=now, cursor=cursor, limit=limit)

    def get_task(self, user_id: str, task_id: str) -> LearningTask:
        with closing(connect(self.db_path)) as conn:
            conn.execute('begin')
            return self._task(conn, user_id, task_id)

    def create_plan(self, user_id: str, command: CreateLearningPlan, *,
                    document_name: str, now: datetime) -> LearningPlanMutation:
        timestamp = _timestamp(now)
        request_id = _request_id(command.request_id)
        if (type(command.days) is not int or not 1 <= command.days <= 365
                or type(command.daily_minutes) is not int or not 5 <= command.daily_minutes <= 480
                or not isinstance(command.title, str) or not 1 <= len(command.title.strip()) <= 100
                or not isinstance(command.document_id, str) or not command.document_id.strip()
                or not isinstance(document_name, str) or not document_name.strip()):
            raise LearningValidationError('invalid plan')
        try:
            zone = ZoneInfo(command.timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise LearningValidationError('invalid timezone') from None
        command = replace(command, request_id=request_id, title=command.title.strip())
        digest = _digest(asdict(command))
        with transaction(self.db_path) as conn:
            conn.execute('begin immediate')
            previous = _replay(conn, user_id, request_id, 'create_plan', digest)
            if previous:
                return LearningPlanMutation(self._plan(conn, user_id, previous['resource_id']), True)
            if _fenced(conn, user_id, command.document_id):
                raise LearningDocumentDeleting()
            dates = plan_dates(now.astimezone(zone).date(), command.days)
            phases = allocate_phases(command.days)
            plan_id = str(uuid4())
            conn.execute('insert into learning_plans values (?,?,?,?,?,?,?,?,?,?,?)',
                         (user_id, plan_id, command.title, command.timezone, dates[0].isoformat(),
                          dates[-1].isoformat(), command.daily_minutes, 'active', 1, timestamp, timestamp))
            conn.execute('insert into learning_plan_documents values (?,?,?,?)',
                         (user_id, plan_id, command.document_id, document_name))
            titles = {'reading':'阅读理解', 'cards':'整理知识卡片', 'exercises':'练习巩固', 'review':'综合复习'}
            conn.executemany('insert into learning_tasks values (?,?,?,?,?,?,?,?,?,?,?)', [
                (user_id, str(uuid4()), plan_id, command.document_id, day.isoformat(), phase,
                 titles[phase], command.daily_minutes, 0, None, 1) for day, phase in zip(dates, phases)
            ])
            _record(conn, user_id, request_id, 'create_plan', digest, plan_id, 1, timestamp)
            return LearningPlanMutation(self._plan(conn, user_id, plan_id), False)

    def set_task_state(self, user_id: str, task_id: str, command: SetLearningTaskState, *,
                       now: datetime) -> LearningTaskMutation:
        timestamp = _timestamp(now)
        request_id = _request_id(command.request_id)
        if type(command.completed) is not bool or type(command.expected_version) is not int or command.expected_version < 1:
            raise LearningValidationError('invalid task state')
        digest = _digest({**asdict(replace(command, request_id=request_id)), 'task_id': task_id})
        with transaction(self.db_path) as conn:
            conn.execute('begin immediate')
            previous = _replay(conn, user_id, request_id, 'set_task_state', digest)
            if previous:
                return LearningTaskMutation(self._task(conn, user_id, previous['resource_id']), True)
            task = self._task(conn, user_id, task_id)
            if task.version != command.expected_version:
                raise LearningVersionConflict()
            changed = task.completed != command.completed
            version = task.version + int(changed)
            if changed:
                conn.execute('update learning_tasks set completed=?,completed_at=?,version=? where user_id=? and id=?',
                             (int(command.completed), timestamp if command.completed else None, version, user_id, task_id))
            _record(conn, user_id, request_id, 'set_task_state', digest, task_id, version, timestamp)
            if changed:
                conn.execute('insert into learning_task_events values (?,?,?,?,?,?,?,?,?)',
                             (user_id, str(uuid4()), task.plan_id, task_id, request_id,
                              int(task.completed), int(command.completed), timestamp, version))
                incomplete = conn.execute('select 1 from learning_tasks where user_id=? and plan_id=? and completed=0 limit 1',
                                          (user_id, task.plan_id)).fetchone()
                conn.execute('update learning_plans set status=?,version=version+1,updated_at=? where user_id=? and id=?',
                             ('active' if incomplete else 'completed', timestamp, user_id, task.plan_id))
            return LearningTaskMutation(self._task(conn, user_id, task_id), False)

    def delete_document_in_transaction(self, conn: sqlite3.Connection, *, user_id: str, document_id: str) -> None:
        if not conn.in_transaction:
            raise ValueError('caller-owned transaction required')
        conn.execute('''delete from learning_plans where user_id=? and id in
                        (select plan_id from learning_plan_documents where user_id=? and document_id=?)''',
                     (user_id, user_id, document_id))
