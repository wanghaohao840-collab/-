from __future__ import annotations

from app.auth import AuthService
from app.database import initialize_database
from app.import_models import ImportTaskCreate
from app.import_repository import ImportTaskRepository
from hello_agents.memory.rag.errors import sanitize_error_message


def _running_task(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    user_id = AuthService(db_path).register("sanitize-user", "passphrase").id
    repository = ImportTaskRepository(db_path)
    repository.create_batch(
        user_id,
        [
            ImportTaskCreate(
                task_id="task-a",
                batch_id="batch-a",
                user_id=user_id,
                document_id="document-a",
                original_name="notes.md",
                file_suffix=".md",
                size_bytes=1,
                staged_relative_path="imports/batch-a/task-a.md",
            )
        ],
    )
    task = repository.claim_next(set())
    assert task is not None
    return repository, user_id, task


def test_sanitize_error_message_redacts_quoted_multiword_and_escaped_values():
    message = (
        'backend rejected password: "correct horse battery" '
        "client_secret: 'escaped\\' multi word secret'"
    )

    sanitized = sanitize_error_message(message)

    assert sanitized.startswith("backend rejected")
    for secret in (
        "correct horse battery",
        "horse battery",
        "escaped\\' multi word secret",
        "multi word secret",
    ):
        assert secret not in sanitized


def test_repository_never_persists_quoted_multiword_secret(tmp_path):
    repository, user_id, task = _running_task(tmp_path)
    summary = 'import failed: {"client_secret": "correct horse battery"}'

    repository.mark_failed(user_id, task.task_id, "unexpected_error", summary)

    persisted = repository.get_task(user_id, task.task_id)
    assert persisted is not None
    assert "import failed" in persisted.error_summary
    assert "correct horse battery" not in persisted.error_summary
    assert "horse battery" not in persisted.error_summary
