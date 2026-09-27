"""Transaction-bound learning storage; no validation or mutation business rules.

Statements are built from fixed identifiers and an explicit dialect bind marker.
This is a domain adapter, not a SQLite connection or SQL translation facade.
"""
from contextlib import closing, contextmanager
from zoneinfo import ZoneInfo

from app.database import connect, transaction
from app.learning_models import LearningNotFound


class LearningStore:
    def __init__(self, cursor, *, postgres=False):
        self.cursor = cursor
        self.postgres = postgres
        self.bind = '%s' if postgres else '?'

    def fenced(self, user_id, document_id):
        b = self.bind
        return self.cursor.execute(
            f"select 1 from qa_deletion_fences where user_id={b} and target_type='document' and target_id={b} limit 1",
            (user_id, document_id)).fetchone() is not None

    def request(self, user_id, request_id):
        b = self.bind
        return self.cursor.execute(
            f'select * from learning_requests where user_id={b} and request_id={b}',
            (user_id, request_id)).fetchone()

    def plan(self, user_id, plan_id):
        b = self.bind
        return self.cursor.execute(f'''select p.*, d.document_id,d.document_name,
            (select count(*) from learning_tasks t where t.user_id=p.user_id and t.plan_id=p.id) task_count,
            (select count(*) from learning_tasks t where t.user_id=p.user_id and t.plan_id=p.id and t.completed=1) completed_count
            from learning_plans p join learning_plan_documents d on d.user_id=p.user_id and d.plan_id=p.id
            where p.user_id={b} and p.id={b}''', (user_id, plan_id)).fetchone()

    def task(self, user_id, task_id):
        b = self.bind
        return self.cursor.execute(f'select * from learning_tasks where user_id={b} and id={b}',
                                   (user_id, task_id)).fetchone()

    def insert_plan(self, values):
        self.cursor.execute(f'insert into learning_plans values ({",".join([self.bind] * 11)})', values)

    def insert_document(self, values):
        self.cursor.execute(f'insert into learning_plan_documents values ({",".join([self.bind] * 4)})', values)

    def insert_tasks(self, values):
        self.cursor.executemany(f'insert into learning_tasks values ({",".join([self.bind] * 11)})', values)

    def insert_request(self, values):
        self.cursor.execute(f'insert into learning_requests values ({",".join([self.bind] * 7)})', values)

    def insert_event(self, values):
        self.cursor.execute(f'insert into learning_task_events values ({",".join([self.bind] * 9)})', values)

    def update_task(self, values):
        b = self.bind
        self.cursor.execute(f'update learning_tasks set completed={b},completed_at={b},version={b} where user_id={b} and id={b}', values)

    def incomplete(self, user_id, plan_id):
        b = self.bind
        return self.cursor.execute(f'select 1 from learning_tasks where user_id={b} and plan_id={b} and completed=0 limit 1',
                                   (user_id, plan_id)).fetchone() is not None

    def update_plan(self, values):
        b = self.bind
        self.cursor.execute(f'update learning_plans set status={b},version=version+1,updated_at={b} where user_id={b} and id={b}', values)

    def delete_document(self, user_id, document_id):
        b = self.bind
        self.cursor.execute(f'''delete from learning_plans where user_id={b} and id in
            (select plan_id from learning_plan_documents where user_id={b} and document_id={b})''',
            (user_id, user_id, document_id))

    def page(self, user_id, *, kind, plan_id, bucket, key, descending, last, instant, limit):
        b = self.bind
        is_plan = kind == 'plans'
        alias = 'p' if is_plan else 't'
        clauses = [f'{alias}.user_id={b}', '''not exists (select 1 from qa_deletion_fences f
            where f.user_id=d.user_id and f.target_type='document' and f.target_id=d.document_id)''']
        params = [user_id]
        if kind == 'tasks':
            clauses.append(f't.plan_id={b}')
            params.append(plan_id)
        if kind == 'today':
            if bucket == 'completed':
                clauses.append('t.completed=1')
            else:
                if self.postgres:
                    day = f"to_char(timezone(p.timezone, {b}::timestamptz), 'YYYY-MM-DD')"
                    params.append(instant)
                else:
                    self.cursor.create_function('learning_day', 1,
                        lambda zone: instant.astimezone(ZoneInfo(zone)).date().isoformat(), deterministic=True)
                    day = 'learning_day(p.timezone)'
                clauses.extend(['t.completed=0', 't.due_date ' + ('=' if bucket == 'today' else '<') + ' ' + day])
        if last:
            clauses.append(f'({alias}.{key},{alias}.id) ' + ('<' if descending else '>') + f' ({b},{b})')
            params.extend(last)
        params.append(limit + 1)
        joins = 'learning_plans p join learning_plan_documents d on d.user_id=p.user_id and d.plan_id=p.id'
        if not is_plan:
            joins += ' join learning_tasks t on t.user_id=p.user_id and t.plan_id=p.id'
        order = 'desc' if descending else 'asc'
        statement = f'select {alias}.id,{alias}.{key} sort_key from {joins} where ' + ' and '.join(clauses)
        statement += f' order by {alias}.{key} {order},{alias}.id {order} limit {b}'
        return self.cursor.execute(statement, params).fetchall()


class SQLiteLearningPersistence:
    def __init__(self, db_path):
        self.db_path = db_path

    @contextmanager
    def read(self):
        with closing(connect(self.db_path)) as conn:
            conn.execute('begin')
            yield LearningStore(conn)

    @contextmanager
    def write(self, user_id):
        with transaction(self.db_path) as conn:
            conn.execute('begin immediate')
            yield LearningStore(conn)


class PostgresLearningPersistence:
    def __init__(self, database):
        self.database = database

    @contextmanager
    def read(self):
        with self.database.transaction() as cursor:
            cursor.execute('set transaction isolation level repeatable read, read only')
            yield LearningStore(cursor, postgres=True)

    @contextmanager
    def write(self, user_id):
        with self.database.transaction() as cursor:
            # Shared lock order: user first, then domain state. Deletion must do the same.
            if cursor.execute('select id from users where id=%s for update', (user_id,)).fetchone() is None:
                raise LearningNotFound()
            yield LearningStore(cursor, postgres=True)
