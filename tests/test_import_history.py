from __future__ import annotations

import base64
import json
import threading
import uuid
from types import SimpleNamespace

import pytest

from app.auth import AuthService
from app.database import connect, initialize_database
from app.import_models import ImportHistoryFilters, ImportTaskCreate
from app.import_repository import ImportTaskRepository
from app.import_service import ImportTaskService
from app.storage import UserStorage


NOW = "2026-08-20T12:00:00Z"


class _Sessions:
    def __init__(self, sessions):
        self.sessions = sessions

    def get_session(self, token):
        if token not in self.sessions:
            raise ValueError("invalid session")
        return self.sessions[token]


class _Workers:
    def notify(self):
        pass


def _history_harness(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    auth = AuthService(db_path)
    user_a = auth.register("history-a", "correct horse battery").id
    user_b = auth.register("history-b", "correct horse battery").id
    repository = ImportTaskRepository(db_path)
    storage = UserStorage(tmp_path / "data")
    return db_path, repository, storage, user_a, user_b


def _create_terminal_batch(
    repository,
    user_id,
    *,
    name,
    status="failed",
    created_at=NOW,
    batch_id=None,
):
    batch_id = batch_id or str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    repository.create_batch(
        user_id,
        [
            ImportTaskCreate(
                task_id=task_id,
                batch_id=batch_id,
                user_id=user_id,
                document_id=str(uuid.uuid4()),
                original_name=name,
                file_suffix=".md",
                size_bytes=1,
                staged_relative_path=f"imports/{batch_id}/{task_id}.md",
            )
        ],
        now=created_at,
    )
    with connect(repository.db_path) as connection:
        connection.execute(
            """
            update import_tasks
            set status = ?, stage = ?, progress = ?, finished_at = ?, updated_at = ?
            where id = ? and user_id = ?
            """,
            (
                status,
                status,
                100 if status == "succeeded" else 0,
                created_at,
                created_at,
                task_id,
                user_id,
            ),
        )
    return batch_id, task_id


def _cursor(payload):
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_filtered_history_cursor_is_stable_and_user_scoped(tmp_path):
    _, repository, _, user_a, user_b = _history_harness(tmp_path)
    expected = {
        _create_terminal_batch(repository, user_a, name=f"failed-{index}.md")[0]
        for index in range(5)
    }
    _create_terminal_batch(repository, user_a, name="success.md", status="succeeded")
    foreign, _ = _create_terminal_batch(repository, user_b, name="failed-foreign.md")

    page1 = repository.list_history(
        user_a, ImportHistoryFilters(statuses=("failed",)), None, 2
    )
    page2 = repository.list_history(
        user_a, ImportHistoryFilters(statuses=("failed",)), page1.next_cursor, 2
    )
    page3 = repository.list_history(
        user_a, ImportHistoryFilters(statuses=("failed",)), page2.next_cursor, 2
    )
    seen = {batch.batch_id for page in (page1, page2, page3) for batch in page.batches}

    assert seen == expected
    assert foreign not in seen
    assert len(seen) == sum(len(page.batches) for page in (page1, page2, page3))
    assert all(
        batch.user_id == user_a
        for page in (page1, page2, page3)
        for batch in page.batches
    )
    assert page3.next_cursor is None


def test_history_applies_filters_before_cursor_and_treats_like_wildcards_literally(
    tmp_path,
):
    _, repository, _, user_a, _ = _history_harness(tmp_path)
    literal, _ = _create_terminal_batch(
        repository, user_a, name="budget%_final.md", created_at="2026-08-20T12:00:03Z"
    )
    _create_terminal_batch(
        repository, user_a, name="budgetXXfinal.md", created_at="2026-08-20T12:00:02Z"
    )
    _create_terminal_batch(
        repository, user_a, name="budget%_old.md", created_at="2026-08-01T00:00:00Z"
    )

    page = repository.list_history(
        user_a,
        ImportHistoryFilters(
            statuses=("failed",),
            filename_query="%_final",
            created_from="2026-08-10T00:00:00Z",
            created_to="2026-08-21T00:00:00Z",
        ),
        None,
        10,
    )

    assert [batch.batch_id for batch in page.batches] == [literal]


@pytest.mark.parametrize(
    "cursor",
    [
        "not-base64!",
        _cursor({"created_at": NOW}),
        _cursor({"created_at": NOW, "batch_id": "x", "extra": 1}),
        _cursor({"created_at": 123, "batch_id": "x"}),
        _cursor({"created_at": NOW, "batch_id": 123}),
        _cursor({"created_at": "2026-08-20T12:00:00+00:00", "batch_id": "x"}),
        _cursor({"created_at": "2026-08-20", "batch_id": "x"}),
    ],
)
def test_history_rejects_malformed_or_non_exact_cursor(tmp_path, cursor):
    _, repository, _, user_a, _ = _history_harness(tmp_path)

    with pytest.raises(ValueError, match="cursor"):
        repository.list_history(user_a, ImportHistoryFilters(), cursor, 10)


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_history_rejects_out_of_range_limit(tmp_path, limit):
    _, repository, _, user_a, _ = _history_harness(tmp_path)

    with pytest.raises(ValueError, match="limit"):
        repository.list_history(user_a, ImportHistoryFilters(), None, limit)


@pytest.mark.parametrize("field", ["created_from", "created_to"])
def test_history_rejects_invalid_dates(tmp_path, field):
    _, repository, _, user_a, _ = _history_harness(tmp_path)
    values = {field: "2026-99-99"}

    with pytest.raises(ValueError, match="date"):
        repository.list_history(
            user_a, ImportHistoryFilters(**values), None, 10
        )


def test_deleting_batch_is_absent_from_regular_batch_and_history_queries(tmp_path):
    _, repository, _, user_a, _ = _history_harness(tmp_path)
    batch_id, _ = _create_terminal_batch(repository, user_a, name="delete.md")

    repository.mark_batch_deleting(user_a, batch_id, now=NOW)

    assert repository.list_batches(user_a) == []
    assert repository.list_history(user_a, ImportHistoryFilters(), None, 10).batches == ()


def test_authenticated_history_and_event_service_never_accepts_a_user_id(tmp_path):
    _, repository, storage, user_a, user_b = _history_harness(tmp_path)
    batch_id, task_id = _create_terminal_batch(repository, user_a, name="private.md")
    foreign_batch, foreign_task = _create_terminal_batch(
        repository, user_b, name="foreign.md"
    )
    sessions = _Sessions(
        {
            "a": SimpleNamespace(user_id=user_a, runtime=SimpleNamespace(lock=threading.RLock())),
            "b": SimpleNamespace(user_id=user_b, runtime=SimpleNamespace(lock=threading.RLock())),
        }
    )
    service = ImportTaskService(sessions, repository, storage, _Workers())

    page = service.list_task_history(
        "a", {"statuses": ["failed"], "filename_query": "private"}, None, 10
    )

    assert [batch.batch_id for batch in page.batches] == [batch_id]
    assert foreign_batch not in {batch.batch_id for batch in page.batches}
    assert service.list_task_events("a", foreign_task) == []
    assert service.list_task_events("b", task_id) == []
