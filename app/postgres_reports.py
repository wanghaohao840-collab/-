"""Synchronous report snapshots with PostgreSQL publication and pinned S3 bytes.

Uploads precede atomic publication. Failed publications may leave invisible
objects for future reference-aware reconciliation; they must not delete bytes
that another transaction could have published. No filesystem cache is authority.
This service is not a Worker lease boundary and is not yet wired into bootstrap.
"""
import hashlib
from uuid import uuid4

from app.object_store import ObjectRef, S3ObjectStore, artifact_key
from app.postgres import PostgresDatabase
from app.reports import ReportRecord


class ReportPublicationError(RuntimeError):
    """Report metadata lacks a usable, correctly configured object reference."""


class PostgresReportService:
    def __init__(self, database: PostgresDatabase, store: S3ObjectStore):
        self.database = database
        self.store = store

    def create_markdown_snapshot(self, user_id: str, title: str, content: str) -> ReportRecord:
        with self.database.transaction() as cursor:
            if cursor.execute("select id from users where id=%s and status='active'", (user_id,)).fetchone() is None:
                raise FileNotFoundError(user_id)
        report_id = str(uuid4())
        raw = content.encode('utf-8')
        key = artifact_key(user_id, 'reports', report_id, '.md', hashlib.sha256(raw).hexdigest())
        ref = self.store.put_immutable(user_id, key, raw).ref
        with self.database.transaction() as cursor:
            # User first, then report metadata, matching other domain writers.
            if cursor.execute("select id from users where id=%s and status='active' for update", (user_id,)).fetchone() is None:
                raise FileNotFoundError(user_id)
            created_at = cursor.execute('select clock_timestamp() as now').fetchone()['now'].isoformat()
            record = ReportRecord(report_id, user_id, title, f'reports/{report_id}.md', created_at)
            cursor.execute('''insert into report_records(id,user_id,title,relative_path,created_at)
                values(%s,%s,%s,%s,%s)''',
                (record.id, record.user_id, record.title, record.relative_path, record.created_at))
            cursor.execute('''insert into report_objects(report_id,user_id,bucket,object_key,version_id,sha256,size_bytes)
                values(%s,%s,%s,%s,%s,%s,%s)''',
                (record.id, user_id, self.store.bucket, ref.key, ref.version_id, ref.sha256, ref.size_bytes))
        return record

    def _check_reference(self, row):
        if row['published_report_id'] is None:
            raise ReportPublicationError('Report has no published object reference')
        if row['bucket'] != self.store.bucket:
            raise ReportPublicationError('Report object bucket differs from configured storage')

    def list_reports(self, user_id: str) -> list[ReportRecord]:
        with self.database.transaction() as cursor:
            rows = cursor.execute('''select r.*,o.report_id as published_report_id,o.bucket
                from report_records r left join report_objects o on o.report_id=r.id and o.user_id=r.user_id
                where r.user_id=%s order by r.created_at desc,r.id desc''', (user_id,)).fetchall()
        for row in rows:
            self._check_reference(row)
        return [ReportRecord(**{key: row[key] for key in ('id', 'user_id', 'title', 'relative_path', 'created_at')})
                for row in rows]

    def read_report_bytes(self, user_id: str, report_id: str) -> bytes:
        with self.database.transaction() as cursor:
            row = cursor.execute('''select o.report_id as published_report_id,o.bucket,o.object_key,
                o.version_id,o.sha256,o.size_bytes from report_records r
                left join report_objects o on o.report_id=r.id and o.user_id=r.user_id
                where r.user_id=%s and r.id=%s''', (user_id, report_id)).fetchone()
        if row is None:
            raise FileNotFoundError(report_id)
        self._check_reference(row)
        return self.store.read_verified(user_id, ObjectRef(row['object_key'], row['sha256'], row['size_bytes'], row['version_id']))

    def read_report(self, user_id: str, report_id: str) -> str:
        return self.read_report_bytes(user_id, report_id).decode('utf-8')
