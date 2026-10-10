"""Immutable upload staging and atomic PostgreSQL import publication."""

from __future__ import annotations

import hashlib
import uuid
from typing import Iterable

from app.import_models import ImportBatchSummary, ImportLimits, ImportTaskCreate
from app.import_repository import PostgresImportTaskRepository
from app.import_uploads import (
    STAGING_CHUNK_BYTES,
    ImportUpload,
    StreamSizeCounter,
    inspect_upload,
    validate_file_count,
)
from app.object_store import ObjectRef, S3ObjectStore, artifact_key
from app.postgres import PostgresDatabase


class ImportPublicationError(RuntimeError):
    """A task has no usable source object reference."""


class PostgresImportArtifactService:
    def __init__(
        self,
        database: PostgresDatabase,
        store: S3ObjectStore,
        limits: ImportLimits = ImportLimits(),
    ) -> None:
        self.database = database
        self.store = store
        self.limits = limits
        self.repository = PostgresImportTaskRepository(database)

    def submit_uploads(
        self, user_id: str, uploads: Iterable[ImportUpload]
    ) -> ImportBatchSummary:
        # The identity is supplied by the authorized caller. Deny it before
        # touching a caller-owned stream or object storage.
        self._require_active(user_id)
        upload_list = list(uploads or [])
        validate_file_count(len(upload_list), self.limits)
        pending = [inspect_upload(upload) for upload in upload_list]
        batch_id = str(uuid.uuid4())
        counter = StreamSizeCounter(self.limits)
        creates: list[ImportTaskCreate] = []
        references: list[tuple[str, ObjectRef]] = []

        for item in pending:
            file_bytes, ref = self._upload_one(user_id, item, counter)
            references.append((item.task_id, ref))
            creates.append(ImportTaskCreate(
                task_id=item.task_id,
                batch_id=batch_id,
                user_id=user_id,
                document_id=item.document_id,
                original_name=item.original_name,
                file_suffix=item.suffix,
                size_bytes=file_bytes,
                staged_relative_path="",
            ))

        with self.database.transaction() as cursor:
            # Keep user lock first; the repository reuses this same cursor.
            self._require_active_locked(cursor, user_id)
            now = cursor.execute("select clock_timestamp() as now").fetchone()["now"].isoformat()
            summary = self.repository.create_batch_in_transaction(
                cursor, user_id, creates, now=now
            )
            for task_id, ref in references:
                cursor.execute("""insert into import_objects
                    (task_id,user_id,bucket,object_key,version_id,sha256,size_bytes)
                    values (%s,%s,%s,%s,%s,%s,%s)""", (
                    task_id, user_id, self.store.bucket, ref.key,
                    ref.version_id, ref.sha256, ref.size_bytes,
                ))
        return summary

    def _upload_one(self, user_id, item, counter: StreamSizeCounter) -> tuple[int, ObjectRef]:
        file_bytes = 0
        content = bytearray()
        while True:
            chunk = item.upload.stream.read(STAGING_CHUNK_BYTES)
            if not chunk and isinstance(chunk, (bytes, bytearray, memoryview)):
                break
            file_bytes = counter.check(chunk, file_bytes)
            content.extend(bytes(chunk))
        raw = bytes(content)
        del content
        key = artifact_key(
            user_id, "imports", item.task_id, item.suffix,
            hashlib.sha256(raw).hexdigest(),
        )
        ref = self.store.put_immutable(user_id, key, raw).ref
        return file_bytes, ref

    def read_source_bytes(self, user_id: str, task_id: str) -> bytes:
        with self.database.transaction() as cursor:
            row = cursor.execute("""select t.id as selected_task_id,
                    o.task_id as referenced_task_id,o.bucket,o.object_key,
                    o.version_id,o.sha256,o.size_bytes
                from import_tasks t left join import_objects o
                    on o.task_id=t.id and o.user_id=t.user_id
                where t.id=%s and t.user_id=%s""", (task_id, user_id)).fetchone()
        if row is None:
            raise FileNotFoundError(task_id)
        if row["referenced_task_id"] is None:
            raise ImportPublicationError("Import source has no published reference")
        if row["bucket"] != self.store.bucket:
            raise ImportPublicationError("Import source bucket differs from configured storage")
        return self.store.read_verified(user_id, ObjectRef(
            row["object_key"], row["sha256"], row["size_bytes"], row["version_id"]
        ))

    def _require_active(self, user_id: str) -> None:
        with self.database.transaction() as cursor:
            if cursor.execute(
                "select id from users where id=%s and status='active'", (user_id,)
            ).fetchone() is None:
                raise FileNotFoundError(user_id)

    @staticmethod
    def _require_active_locked(cursor, user_id: str) -> None:
        if cursor.execute(
            "select id from users where id=%s and status='active' for update", (user_id,)
        ).fetchone() is None:
            raise FileNotFoundError(user_id)
        from app.import_publication_evidence import require_no_gate_in_transaction
        require_no_gate_in_transaction(cursor, user_id)
