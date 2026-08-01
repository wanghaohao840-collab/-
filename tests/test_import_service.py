from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.auth import AuthService
from app.database import initialize_database
from app.import_repository import ImportTaskRepository, InvalidImportTransition
from app.import_service import ImportTaskService
from app.storage import UserStorage


class FakeSessionRegistry:
    def __init__(self, sessions):
        self.sessions = sessions
        self.calls = []

    def get_session(self, token):
        self.calls.append(token)
        if token not in self.sessions:
            raise ValueError("invalid session")
        return SimpleNamespace(user_id=self.sessions[token])


class FakeWorkerPool:
    def __init__(self):
        self.notify_count = 0

    def notify(self):
        self.notify_count += 1


class FakeFile:
    def __init__(self, path: Path):
        self.name = str(path)


def make_import_service(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    user = AuthService(db_path).register("import-user", "correct horse battery")
    other = AuthService(db_path).register("other-user", "correct horse battery")
    repository = ImportTaskRepository(db_path)
    storage = UserStorage(tmp_path / "data")
    sessions = FakeSessionRegistry({"valid-token": user.id, "other-token": other.id})
    workers = FakeWorkerPool()
    service = ImportTaskService(sessions, repository, storage, workers)
    return service, repository, storage, workers, user.id, other.id


def uploaded_file(tmp_path: Path, name: str, content: bytes) -> FakeFile:
    path = tmp_path / "uploads" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return FakeFile(path)


def test_submit_batch_stages_all_files_before_creating_tasks(tmp_path):
    service, repo, storage, workers, user_id, _ = make_import_service(tmp_path)
    files = [
        uploaded_file(tmp_path, "a.md", b"alpha"),
        uploaded_file(tmp_path, "b.md", b"beta"),
    ]
    progress = []

    result = service.submit_batch(
        "valid-token", files, progress=lambda value, **kwargs: progress.append((value, kwargs))
    )

    assert result.total == 2
    assert repo.get_batch(user_id, result.batch_id).queued == 2
    staged = list((storage.user_paths(user_id).imports / result.batch_id).iterdir())
    assert sorted(path.read_bytes() for path in staged) == [b"alpha", b"beta"]
    assert {task.original_name for task in result.tasks} == {"a.md", "b.md"}
    assert all(Path(task.staged_relative_path).parts[0] == "imports" for task in result.tasks)
    assert progress[-1][0] == (2, 2)
    assert workers.notify_count == 1


def test_submit_batch_cleans_partial_stage_on_copy_failure(tmp_path, monkeypatch):
    service, repo, storage, workers, user_id, _ = make_import_service(tmp_path)
    files = [
        uploaded_file(tmp_path, "a.md", b"a"),
        uploaded_file(tmp_path, "b.md", b"b"),
    ]
    import app.import_service as import_service

    real_copyfile = import_service.shutil.copyfile

    def fail_second(source, target):
        if Path(source).name == "b.md":
            raise OSError("sensitive server detail")
        return real_copyfile(source, target)

    monkeypatch.setattr(import_service.shutil, "copyfile", fail_second)

    with pytest.raises(ValueError, match="could not stage") as error:
        service.submit_batch("valid-token", files)

    assert "sensitive" not in str(error.value)
    assert repo.list_batches(user_id) == []
    imports = storage.user_paths(user_id).imports
    assert not imports.exists() or list(imports.iterdir()) == []
    assert workers.notify_count == 0


def test_submit_validates_entire_batch_before_creating_stage_directory(tmp_path):
    service, repo, storage, workers, user_id, _ = make_import_service(tmp_path)
    files = [
        uploaded_file(tmp_path, "a.md", b"a"),
        uploaded_file(tmp_path, "bad.exe", b"b"),
    ]

    with pytest.raises(ValueError, match="Unsupported document type"):
        service.submit_batch("valid-token", files)

    assert repo.list_batches(user_id) == []
    assert not storage.user_paths(user_id).imports.exists()
    assert workers.notify_count == 0


def test_queries_and_retries_are_scoped_to_authenticated_user(tmp_path):
    service, repo, _, workers, user_id, _ = make_import_service(tmp_path)
    result = service.submit_batch(
        "valid-token", [uploaded_file(tmp_path, "a.md", b"alpha")]
    )
    claimed = repo.claim_next(set())
    repo.mark_failed(user_id, claimed.task_id, "document_invalid", "bad document")

    with pytest.raises(KeyError):
        service.get_batch("other-token", result.batch_id)
    with pytest.raises(KeyError):
        service.retry_task("other-token", claimed.task_id)

    retried = service.retry_task("valid-token", claimed.task_id)

    assert retried.queued == 1
    assert retried.tasks[0].manual_retry_count == 1
    assert workers.notify_count == 2


def test_succeeded_task_cannot_be_retried(tmp_path):
    service, repo, _, _, user_id, _ = make_import_service(tmp_path)
    result = service.submit_batch(
        "valid-token", [uploaded_file(tmp_path, "a.md", b"alpha")]
    )
    claimed = repo.claim_next(set())
    repo.mark_succeeded(user_id, claimed.task_id)

    with pytest.raises(InvalidImportTransition):
        service.retry_task("valid-token", result.tasks[0].task_id)
