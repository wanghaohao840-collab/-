"""Fixed import SQL operations and shared submission/control rules."""

from __future__ import annotations

from contextlib import closing, contextmanager
from typing import Iterable

from app.database import connect, transaction
from app.import_models import ImportBatchSummary, ImportCancelDecision, ImportTaskCreate


class ImportStore:
    def __init__(self, cursor, *, postgres=False):
        self.cursor = cursor
        self.bind = "%s" if postgres else "?"
        self.postgres = postgres

    def insert_batch(self, batch_id, user_id, timestamp):
        b = self.bind
        self.cursor.execute(f"insert into import_batches(id,user_id,created_at,updated_at) values({b},{b},{b},{b})",
                     (batch_id, user_id, timestamp, timestamp))

    def insert_tasks(self, tasks, timestamp):
        b = self.bind
        for task in tasks:
            self.cursor.execute(f"""insert into import_tasks
                (id,batch_id,user_id,document_id,original_name,file_suffix,size_bytes,
                 staged_relative_path,status,stage,progress,created_at,updated_at)
                values({','.join([b] * 8)},'queued','queued',0,{b},{b})""",
                (task.task_id, task.batch_id, task.user_id, task.document_id,
                 task.original_name, task.file_suffix, task.size_bytes,
                 task.staged_relative_path, timestamp, timestamp))

    def batch_ids(self, user_id, limit):
        b = self.bind
        return self.cursor.execute(f"""select id from import_batches where user_id={b}
            order by created_at desc,id desc limit {b}""", (user_id, limit)).fetchall()

    def batch_row(self, user_id, batch_id):
        b = self.bind
        term = (lambda status: f"sum(case when t.status='{status}' then 1 else 0 end)")
        return self.cursor.execute(f"""select b.id,b.user_id,b.created_at,b.updated_at,
            count(t.id) as total, {term('queued')} as queued,
            {term('running')} as running, {term('retry_wait')} as retry_wait,
            {term('succeeded')} as succeeded, {term('failed')} as failed,
            {term('cancelled')} as cancelled
            from import_batches b left join import_tasks t
              on t.batch_id=b.id and t.user_id=b.user_id
            where b.id={b} and b.user_id={b}
            group by b.id,b.user_id,b.created_at,b.updated_at""",
            (batch_id, user_id)).fetchone()

    def batch_tasks(self, user_id, batch_id):
        b = self.bind
        return self.cursor.execute(f"""select * from import_tasks where batch_id={b} and user_id={b}
            order by created_at,id""", (batch_id, user_id)).fetchall()

    def task(self, user_id, task_id, batch_id=None, *, lock=False):
        b = self.bind
        scope = f" and batch_id={b}" if batch_id is not None else ""
        suffix = " for update" if lock and self.postgres else ""
        params = (task_id, user_id, batch_id) if batch_id is not None else (task_id, user_id)
        return self.cursor.execute(f"select * from import_tasks where id={b} and user_id={b}{scope}{suffix}", params).fetchone()

    def cancel_requested(self, user_id, task_id):
        b = self.bind
        return self.cursor.execute(f"select cancel_requested_at from import_tasks where id={b} and user_id={b}",
                            (task_id, user_id)).fetchone()

    def cancel_queued(self, user_id, batch_id, task_id, timestamp):
        b = self.bind
        return self.cursor.execute(f"""update import_tasks set status='cancelled',stage='cancelled',
            next_attempt_at=null,cancel_requested_at={b},finished_at={b},updated_at={b}
            where id={b} and batch_id={b} and user_id={b}
              and status in ('queued','retry_wait')""",
            (timestamp, timestamp, timestamp, task_id, batch_id, user_id)).rowcount

    def request_running_cancel(self, user_id, batch_id, task_id, timestamp):
        b = self.bind
        return self.cursor.execute(f"""update import_tasks set cancel_requested_at={b},updated_at={b}
            where id={b} and batch_id={b} and user_id={b}
              and status='running' and stage!='committing' and cancel_requested_at is null""",
            (timestamp, timestamp, task_id, batch_id, user_id)).rowcount

    def retry_task(self, user_id, task_id, timestamp):
        b = self.bind
        return self.cursor.execute(f"""update import_tasks set status='queued',stage='queued',progress=0,
            auto_retry_count=0,manual_retry_count=manual_retry_count+1,
            next_attempt_at=null,error_code=null,error_summary=null,
            started_at=null,finished_at=null,updated_at={b}
            where id={b} and user_id={b} and status='failed'""",
            (timestamp, task_id, user_id)).rowcount

    def retry_batch(self, user_id, batch_id, timestamp):
        b = self.bind
        return self.cursor.execute(f"""update import_tasks set status='queued',stage='queued',progress=0,
            auto_retry_count=0,manual_retry_count=manual_retry_count+1,
            next_attempt_at=null,error_code=null,error_summary=null,
            started_at=null,finished_at=null,updated_at={b}
            where user_id={b} and batch_id={b} and status='failed'""",
            (timestamp, user_id, batch_id)).rowcount

    def touch_batch(self, user_id, batch_id, timestamp):
        b = self.bind
        self.cursor.execute(f"update import_batches set updated_at={b} where id={b} and user_id={b}",
                     (timestamp, batch_id, user_id))

    def active(self, user_id, document_id=None):
        b = self.bind
        scope = f" and document_id={b}" if document_id is not None else ""
        params = (user_id, document_id) if document_id is not None else (user_id,)
        return self.cursor.execute(f"""select 1 from import_tasks where user_id={b}{scope}
            and status in ('queued','running','retry_wait') limit 1""", params).fetchone() is not None


class ImportPersistence:
    def __init__(self, database, *, postgres=False):
        self.database = database
        self.postgres = postgres

    @contextmanager
    def read(self):
        if self.postgres:
            with self.database.transaction() as cursor:
                cursor.execute("set transaction isolation level repeatable read, read only")
                yield ImportStore(cursor, postgres=True)
        else:
            with closing(connect(self.database)) as conn:
                conn.execute("begin")
                yield ImportStore(conn)

    @contextmanager
    def write(self, user_id):
        if self.postgres:
            with self.database.transaction() as cursor:
                self.lock_user(cursor, user_id)
                yield ImportStore(cursor, postgres=True)
        else:
            with transaction(self.database) as conn:
                conn.execute("begin immediate")
                yield ImportStore(conn)

    @staticmethod
    def lock_user(cursor, user_id):
        if cursor.execute("select id from users where id=%s for update", (user_id,)).fetchone() is None:
            raise KeyError("import user was not found")

    def caller_owned(self, cursor, user_id):
        from psycopg.pq import TransactionStatus

        if not self.postgres or cursor.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("caller-owned transaction required")
        self.lock_user(cursor, user_id)
        return ImportStore(cursor, postgres=True)


class ImportControlRepository:
    def _init_import_control(self, database, *, postgres=False):
        self._imports = ImportPersistence(database, postgres=postgres)

    @staticmethod
    def _validate_tasks(user_id: str, tasks: Iterable[ImportTaskCreate]):
        task_list = list(tasks)
        if not task_list:
            raise ValueError("an import batch requires at least one task")
        batch_ids = {task.batch_id for task in task_list}
        if len(batch_ids) != 1 or any(task.user_id != user_id for task in task_list):
            raise ValueError("all import tasks must belong to one user and batch")
        return task_list

    def _create_batch(self, store, user_id, tasks, timestamp):
        from app.import_repository import _utc_now

        task_list = self._validate_tasks(user_id, tasks)
        timestamp = timestamp or _utc_now()
        batch_id = task_list[0].batch_id
        store.insert_batch(batch_id, user_id, timestamp)
        store.insert_tasks(task_list, timestamp)
        return self._batch(store, user_id, batch_id)

    def create_batch(self, user_id, tasks, now=None):
        task_list = self._validate_tasks(user_id, tasks)
        with self._imports.write(user_id) as store:
            return self._create_batch(store, user_id, task_list, now)

    def list_batches(self, user_id, limit=50):
        if limit < 1:
            return []
        with self._imports.read() as store:
            return [self._batch(store, user_id, row["id"])
                    for row in store.batch_ids(user_id, limit)]

    def get_batch(self, user_id, batch_id):
        with self._imports.read() as store:
            return self._batch(store, user_id, batch_id)

    def get_task(self, user_id, task_id):
        from app.import_repository import _task_from_row
        with self._imports.read() as store:
            row = store.task(user_id, task_id)
            return _task_from_row(row) if row is not None else None

    def _batch(self, store, user_id, batch_id):
        from app.import_repository import _task_from_row
        row = store.batch_row(user_id, batch_id)
        if row is None:
            return None
        tasks = store.batch_tasks(user_id, batch_id)
        return ImportBatchSummary(row["id"], row["user_id"], row["created_at"],
            row["updated_at"], row["total"], row["queued"], row["running"],
            row["retry_wait"], row["succeeded"], row["failed"],
            tuple(_task_from_row(item) for item in tasks), row["cancelled"])

    def request_cancel(self, user_id, batch_id, task_id, now=None):
        from app.import_repository import _task_from_row, _utc_now
        timestamp = now or _utc_now()
        with self._imports.write(user_id) as store:
            row = store.task(user_id, task_id, batch_id, lock=True)
            if row is None:
                raise KeyError("import task was not found")
            changed = False
            if row["status"] in ("queued", "retry_wait"):
                changed = bool(store.cancel_queued(user_id, batch_id, task_id, timestamp))
                outcome = "cancelled"
            elif row["status"] == "running" and row["stage"] == "committing":
                outcome = "not_cancellable"
            elif row["status"] == "running":
                changed = bool(store.request_running_cancel(user_id, batch_id, task_id, timestamp))
                outcome = "cancel_requested"
            else:
                outcome = "unchanged"
            updated = store.task(user_id, task_id, batch_id)
            if changed:
                store.touch_batch(user_id, batch_id, updated["updated_at"])
            return ImportCancelDecision(_task_from_row(updated), outcome)

    def is_cancel_requested(self, user_id, task_id):
        with self._imports.read() as store:
            row = store.cancel_requested(user_id, task_id)
            return row is not None and row["cancel_requested_at"] is not None

    def retry_task(self, user_id, task_id, now=None):
        from app.import_repository import InvalidImportTransition, _task_from_row, _utc_now
        timestamp = now or _utc_now()
        with self._imports.write(user_id) as store:
            row = store.task(user_id, task_id, lock=True)
            if row is None:
                raise KeyError("import task was not found")
            if row["status"] != "failed":
                raise InvalidImportTransition("import task is not in the required state")
            store.retry_task(user_id, task_id, timestamp)
            store.touch_batch(user_id, row["batch_id"], timestamp)
            return _task_from_row(store.task(user_id, task_id))

    def retry_failed_in_batch(self, user_id, batch_id, now=None):
        from app.import_repository import _utc_now
        timestamp = now or _utc_now()
        with self._imports.write(user_id) as store:
            count = store.retry_batch(user_id, batch_id, timestamp)
            if count:
                store.touch_batch(user_id, batch_id, timestamp)
            return count

    def has_active_tasks(self, user_id):
        with self._imports.read() as store:
            return store.active(user_id)

    def has_active_task_for_document(self, user_id, document_id):
        with self._imports.read() as store:
            return store.active(user_id, document_id)
