"""Authorized learning use cases; no automatic legacy import or background work."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import stat

from app.database import connect
from app.learning_models import LearningMigrationRequired, LearningNotFound, Page


class LearningService:
    def __init__(self, repository, session_registry, document_library, storage, *, now=None):
        self.repository = repository
        self.session_registry = session_registry
        self.document_library = document_library
        self.storage = storage
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _legacy_gate(self, user_id):
        root = self.storage.data_root
        source = self.storage.user_paths(user_id).root / 'learning.json'
        try:
            relative = source.relative_to(root)
            if '..' in relative.parts:
                raise ValueError('unsafe source')
            current = root
            for component in relative.parts:
                current = current / component
                try:
                    info = current.lstat()
                except FileNotFoundError:
                    return
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024):
                    raise ValueError('unsafe source')
            if not stat.S_ISREG(info.st_mode) or info.st_size > 16 * 1024 * 1024:
                raise ValueError('source is not bounded regular file')
            digest = sha256()
            size = 0
            with source.open('rb') as stream:
                while chunk := stream.read(65536):
                    size += len(chunk)
                    if size > 16 * 1024 * 1024:
                        raise ValueError('source too large')
                    digest.update(chunk)
            with closing(connect(self.repository.db_path)) as conn:
                row = conn.execute('select source_sha256 from learning_migrations where user_id=? and migration_version=1',
                                   (user_id,)).fetchone()
            if row is None or row['source_sha256'] != digest.hexdigest():
                raise ValueError('source requires migration')
        except (OSError, ValueError):
            raise LearningMigrationRequired() from None

    @contextmanager
    def _context(self, session):
        current = self.session_registry.get_session(session.token)
        with current.runtime.lock:
            self._legacy_gate(str(current.user_id))
            yield current

    def _documents(self, current):
        return {doc.document_id: doc for doc in self.document_library.list_documents(current.token)
                if doc.status == 'ready'}

    def create_plan(self, session, command):
        with self._context(session) as current:
            doc = self._documents(current).get(command.document_id)
            if doc is None:
                raise LearningNotFound()
            return self.repository.create_plan(str(current.user_id), command, document_name=doc.name, now=self.now())

    def get_plan(self, session, plan_id):
        with self._context(session) as current:
            plan = self.repository.get_plan(str(current.user_id), plan_id)
            if plan.document_id not in self._documents(current):
                raise LearningNotFound()
            return plan

    def list_plans(self, session, *, cursor=None, limit=20):
        with self._context(session) as current:
            page = self.repository.list_plans(str(current.user_id), cursor=cursor, limit=limit)
            documents = self._documents(current)
            return Page(tuple(item for item in page.items if item.document_id in documents), page.next_cursor)

    def list_tasks(self, session, plan_id, *, cursor=None, limit=20):
        with self._context(session) as current:
            plan = self.repository.get_plan(str(current.user_id), plan_id)
            if plan.document_id not in self._documents(current):
                raise LearningNotFound()
            return self.repository.list_tasks(str(current.user_id), plan_id, cursor=cursor, limit=limit)

    def today(self, session, *, bucket, cursor=None, limit=20):
        with self._context(session) as current:
            page = self.repository.today(str(current.user_id), bucket=bucket, cursor=cursor, limit=limit, now=self.now())
            documents = self._documents(current)
            return Page(tuple(item for item in page.items if item.document_id in documents), page.next_cursor)

    def set_task_state(self, session, task_id, command):
        with self._context(session) as current:
            task = self.repository.get_task(str(current.user_id), task_id)
            if task.document_id not in self._documents(current):
                raise LearningNotFound()
            return self.repository.set_task_state(str(current.user_id), task_id, command, now=self.now())
