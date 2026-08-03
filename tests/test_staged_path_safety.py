from __future__ import annotations

import os
import subprocess
import uuid

import pytest

from app.auth import AuthService
from app.database import initialize_database
from app.import_models import ImportTaskCreate
from app.import_repository import ImportTaskRepository
from app.storage import UnsafePathError, UserStorage


def _succeeded_task(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    user_id = AuthService(db_path).register("path-user", "passphrase").id
    storage = UserStorage(tmp_path / "data")
    repository = ImportTaskRepository(db_path)
    batch_id, task_id, document_id = (str(uuid.uuid4()) for _ in range(3))
    repository.create_batch(
        user_id,
        [
            ImportTaskCreate(
                task_id=task_id,
                batch_id=batch_id,
                user_id=user_id,
                document_id=document_id,
                original_name="notes.md",
                file_suffix=".md",
                size_bytes=1,
                staged_relative_path=f"imports/{batch_id}/{task_id}.md",
            )
        ],
    )
    task = repository.claim_next(set())
    assert task is not None
    repository.mark_succeeded(user_id, task_id)
    document = storage.document_path(user_id, document_id, ".md")
    document.write_text("must remain", encoding="utf-8")
    return storage, repository, user_id, batch_id, task_id, document


def _replace_batch_with_symlink(storage, user_id, batch_id, target):
    batch = storage.user_paths(user_id).imports / batch_id
    batch.parent.mkdir(parents=True, exist_ok=True)
    if batch.exists():
        batch.rmdir()
    try:
        batch.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symbolic links are unavailable: {error}")


def test_cleanup_rejects_symlinked_batch_and_preserves_document(tmp_path):
    storage, repository, user_id, batch_id, task_id, document = _succeeded_task(tmp_path)
    _replace_batch_with_symlink(storage, user_id, batch_id, document.parent)

    with pytest.raises(UnsafePathError, match="link or reparse"):
        storage.resolve_staged_import_path(
            user_id,
            batch_id,
            task_id,
            ".md",
            f"imports/{batch_id}/{task_id}.md",
        )

    assert repository.cleanup_succeeded_staging(storage) == 0
    assert document.read_text(encoding="utf-8") == "must remain"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_cleanup_rejects_junction_batch_and_preserves_document(tmp_path):
    storage, repository, user_id, batch_id, task_id, document = _succeeded_task(tmp_path)
    batch = storage.user_paths(user_id).imports / batch_id
    batch.parent.mkdir(parents=True, exist_ok=True)
    if batch.exists():
        batch.rmdir()
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(batch), str(document.parent)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("Windows junction creation is unavailable")

    assert repository.cleanup_succeeded_staging(storage) == 0
    assert document.read_text(encoding="utf-8") == "must remain"
