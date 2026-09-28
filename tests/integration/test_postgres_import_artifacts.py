"""Real PostgreSQL and versioned S3 import publication contract."""

import hashlib
import io
from array import array
from uuid import UUID, uuid4

import pytest
from psycopg.errors import RaiseException

from app.import_models import ImportLimits
from app.import_service import ImportLimitError, ImportUpload
from app.postgres_import_artifacts import PostgresImportArtifactService
from tests.integration.test_postgres_auth_sessions import shared_database
from tests.integration.test_s3_object_store import store


@pytest.fixture
def fixture(shared_database, store):
    open_pool, _ = shared_database
    first, second = open_pool(), open_pool()
    owner, other, disabled = [str(uuid4()) for _ in range(3)]
    with first.transaction() as cursor:
        for user, status in ((owner, "active"), (other, "active"), (disabled, "disabled")):
            cursor.execute(
                "insert into users values (%s,%s,%s,%s,%s,%s,%s)",
                (user, user, user, "hash", status, "now", "now"),
            )
    return first, second, store, owner, other, disabled


def counts(database):
    with database.transaction() as cursor:
        return tuple(cursor.execute(f"select count(*) as n from {table}").fetchone()["n"]
                     for table in ("import_batches", "import_tasks", "import_objects"))


def test_batch_publication_metadata_and_pinned_bytes_across_pools(fixture, tmp_path, monkeypatch):
    first, second, store, owner, other, _ = fixture
    monkeypatch.chdir(tmp_path)
    content = b"first source\x00\xff"
    summary = PostgresImportArtifactService(first, store).submit_uploads(
        owner, [ImportUpload(r"..\private\notes.MD", io.BytesIO(content)),
                ImportUpload("second.txt", io.BytesIO(b"second"))])
    assert list(tmp_path.iterdir()) == []
    assert (summary.total, summary.queued, counts(second)) == (2, 2, (1, 2, 2))
    assert all(task.staged_relative_path == "" for task in summary.tasks)
    tasks_by_name = {task.original_name: task for task in summary.tasks}
    assert set(tasks_by_name) == {"notes.MD", "second.txt"}
    assert (tasks_by_name["notes.MD"].file_suffix,
            tasks_by_name["notes.MD"].size_bytes) == (".md", len(content))
    assert (tasks_by_name["second.txt"].file_suffix,
            tasks_by_name["second.txt"].size_bytes) == (".txt", 6)
    assert len({task.task_id for task in summary.tasks}) == 2
    for task in summary.tasks:
        assert str(UUID(task.task_id)) == task.task_id
        assert str(UUID(task.document_id)) == task.document_id
    task = tasks_by_name["notes.MD"]
    with second.transaction() as cursor:
        row = cursor.execute("select * from import_objects where task_id=%s", (task.task_id,)).fetchone()
    assert row["user_id"] == owner and row["bucket"] == store.bucket
    assert row["sha256"] == hashlib.sha256(content).hexdigest()
    assert row["size_bytes"] == len(content)
    assert "notes" not in row["object_key"]
    reader = PostgresImportArtifactService(second, store)
    assert reader.read_source_bytes(owner, task.task_id) == content
    with pytest.raises(FileNotFoundError):
        reader.read_source_bytes(other, task.task_id)
    store.client.put_object(Bucket=store.bucket, Key=row["object_key"], Body=b"later")
    store.client.delete_object(Bucket=store.bucket, Key=row["object_key"])
    assert reader.read_source_bytes(owner, task.task_id) == content


def test_invalid_input_and_user_gate_do_not_publish_or_upload(fixture, monkeypatch):
    first, _, store, owner, _, disabled = fixture
    service = PostgresImportArtifactService(first, store, ImportLimits(2, 4, 6))
    original = store.put_immutable
    uploaded = []

    def tracked(*args):
        uploaded.append(args)
        return original(*args)

    monkeypatch.setattr(store, "put_immutable", tracked)
    for user in (str(uuid4()), disabled):
        stream = io.BytesIO(b"ok")
        with pytest.raises(FileNotFoundError):
            service.submit_uploads(user, [ImportUpload("a.txt", stream)])
        assert stream.tell() == 0
    assert uploaded == []
    invalid = [([], "import_no_files"),
               ([ImportUpload(f"{i}.txt", io.BytesIO(b"x")) for i in range(3)], "import_too_many_files")]
    for uploads, code in invalid:
        with pytest.raises(ImportLimitError) as caught:
            service.submit_uploads(owner, uploads)
        assert caught.value.code == code
    with pytest.raises(ValueError, match="Unsupported document type"):
        service.submit_uploads(owner, [ImportUpload("bad.exe", io.BytesIO(b"x"))])
    with pytest.raises(ValueError, match="binary"):
        service.submit_uploads(owner, [ImportUpload("bad.txt", io.StringIO("text"))])
    with pytest.raises(ImportLimitError) as caught:
        service.submit_uploads(owner, [ImportUpload("big.txt", io.BytesIO(b"12345"))])
    assert caught.value.code == "import_file_too_large"
    with pytest.raises(ImportLimitError) as caught:
        service.submit_uploads(owner, [ImportUpload("a.txt", io.BytesIO(b"1234")),
                                       ImportUpload("b.txt", io.BytesIO(b"123"))])
    assert caught.value.code == "import_batch_too_large"
    assert counts(first) == (0, 0, 0)


def test_upload_and_reference_failure_are_atomic(fixture, monkeypatch):
    first, _, store, owner, _, _ = fixture
    service = PostgresImportArtifactService(first, store)
    original = store.put_immutable
    calls = 0

    def fail_second(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("upload failed")
        return original(*args)

    monkeypatch.setattr(store, "put_immutable", fail_second)
    uploads = lambda: [ImportUpload(f"{i}.txt", io.BytesIO(b"x")) for i in range(2)]
    with pytest.raises(RuntimeError, match="upload failed"):
        service.submit_uploads(owner, uploads())
    assert counts(first) == (0, 0, 0)
    monkeypatch.setattr(store, "put_immutable", original)
    with first.transaction() as cursor:
        cursor.execute("""create function reject_second_import() returns trigger language plpgsql as $$
            begin if new.object_key like '%/imports/%' and
              (select count(*) from import_objects) >= 1 then
                raise exception 'second reference rejected'; end if; return new; end $$""")
        cursor.execute("create trigger reject_second before insert on import_objects for each row execute function reject_second_import()")
    with pytest.raises(RaiseException, match="second reference rejected"):
        service.submit_uploads(owner, uploads())
    assert counts(first) == (0, 0, 0)


def test_recheck_active_user_after_upload_and_bad_reference_fails_closed(fixture, monkeypatch):
    first, second, store, owner, _, _ = fixture
    service = PostgresImportArtifactService(first, store)
    original = store.put_immutable

    def disable_after_upload(*args):
        written = original(*args)
        with second.transaction() as cursor:
            cursor.execute("update users set status='disabled' where id=%s", (owner,))
        return written

    monkeypatch.setattr(store, "put_immutable", disable_after_upload)
    with pytest.raises(FileNotFoundError):
        service.submit_uploads(owner, [ImportUpload("a.txt", io.BytesIO(b"x"))])
    assert counts(first) == (0, 0, 0)
    with second.transaction() as cursor:
        cursor.execute("update users set status='active' where id=%s", (owner,))
    monkeypatch.setattr(store, "put_immutable", original)
    task = service.submit_uploads(owner, [ImportUpload("a.txt", io.BytesIO(b"x"))]).tasks[0]
    with second.transaction() as cursor:
        cursor.execute("update import_objects set bucket='wrong' where task_id=%s", (task.task_id,))
    with pytest.raises(RuntimeError, match="bucket"):
        service.read_source_bytes(owner, task.task_id)
    with second.transaction() as cursor:
        cursor.execute("delete from import_objects where task_id=%s", (task.task_id,))
    with pytest.raises(RuntimeError, match="reference"):
        service.read_source_bytes(owner, task.task_id)


def test_stream_bounds_ownership_and_integrity(fixture):
    first, second, store, owner, _, _ = fixture

    class CheckedStream(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 1024 * 1024
            return super().read(size)

    stream = CheckedStream(b"exact source")
    service = PostgresImportArtifactService(first, store)
    task = service.submit_uploads(owner, [ImportUpload("exact.pdf", stream)]).tasks[0]
    assert not stream.closed
    assert service.read_source_bytes(owner, task.task_id) == b"exact source"
    with second.transaction() as cursor:
        cursor.execute("update import_objects set size_bytes=size_bytes+1 where task_id=%s", (task.task_id,))
    with pytest.raises(ValueError, match="reference|bytes"):
        service.read_source_bytes(owner, task.task_id)

    class EmptyTextStream:
        def read(self, size):
            return ""

    with pytest.raises(ValueError, match="binary"):
        service.submit_uploads(owner, [ImportUpload("empty.txt", EmptyTextStream())])
    assert counts(first) == (1, 1, 1)


def test_multibyte_memoryview_counts_actual_bytes(fixture):
    first, _, store, owner, _, _ = fixture

    class ViewStream:
        def __init__(self):
            self.reads = 0

        def read(self, size):
            assert 0 < size <= 1024 * 1024
            self.reads += 1
            return memoryview(array("I", [1, 2])) if self.reads == 1 else b""

    stream = ViewStream()
    service = PostgresImportArtifactService(first, store, ImportLimits(2, 7, 20))
    with pytest.raises(ImportLimitError) as caught:
        service.submit_uploads(owner, [ImportUpload("view.txt", stream)])
    assert caught.value.code == "import_file_too_large"
    assert counts(first) == (0, 0, 0)

    service = PostgresImportArtifactService(first, store, ImportLimits(2, 8, 20))
    task = service.submit_uploads(owner, [ImportUpload("view.txt", ViewStream())]).tasks[0]
    raw = array("I", [1, 2]).tobytes()
    with first.transaction() as cursor:
        ref = cursor.execute("select size_bytes from import_objects where task_id=%s", (task.task_id,)).fetchone()
    assert task.size_bytes == ref["size_bytes"] == len(raw) == 8
    assert service.read_source_bytes(owner, task.task_id) == raw
