from __future__ import annotations

from types import SimpleNamespace

import gradio as gr
import pytest

from app.import_models import (
    ImportBatchSummary,
    ImportHistoryPage,
    ImportTaskEventRecord,
    ImportTaskRecord,
)


def _bindings(module, function):
    return [block_fn for block_fn in module.demo.fns.values() if block_fn.fn is function]


def _task(**overrides):
    values = {
        "task_id": "task-private",
        "batch_id": "batch-private",
        "user_id": "user-private",
        "document_id": "document-private",
        "original_name": "notes.md",
        "file_suffix": ".md",
        "size_bytes": 5,
        "staged_relative_path": "imports/private/task-private.md",
        "status": "failed",
        "stage": "failed",
        "progress": 40,
        "total_attempt_count": 1,
        "auto_retry_count": 0,
        "manual_retry_count": 0,
        "max_auto_retries": 3,
        "next_attempt_at": None,
        "error_code": "unexpected_error",
        "error_summary": "safe failure",
        "created_at": "2026-08-20T12:00:00Z",
        "started_at": "2026-08-20T12:00:01Z",
        "finished_at": "2026-08-20T12:00:02Z",
        "updated_at": "2026-08-20T12:00:02Z",
    }
    values.update(overrides)
    return ImportTaskRecord(**values)


def _batch(**overrides):
    task = overrides.pop("task", _task())
    values = {
        "batch_id": task.batch_id,
        "user_id": task.user_id,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "total": 1,
        "queued": 0,
        "running": 0,
        "retry_wait": 0,
        "succeeded": 0,
        "failed": 1,
        "tasks": (task,),
    }
    values.update(overrides)
    return ImportBatchSummary(**values)


class _HistoryService:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []
        self.deleted = []

    def list_task_history(self, token, filters, cursor, limit):
        self.calls.append((token, filters, cursor, limit))
        return self.pages[cursor]

    def get_batch(self, token, batch_id):
        for page in self.pages.values():
            for batch in page.batches:
                if batch.batch_id == batch_id:
                    return batch
        raise KeyError("not found")

    def list_task_events(self, token, task_id, limit=200):
        assert task_id == "task-private"
        return [
            ImportTaskEventRecord(
                event_id=1,
                batch_id="batch-private",
                task_id="task-private",
                user_id="user-private",
                event_type="failed",
                status="failed",
                stage="failed",
                message=(
                    "token=top-secret C:\\Users\\private\\secret.txt "
                    "task-private batch-private user-private document-private "
                    "imports/private/task-private.md"
                ),
                created_at="2026-08-20T12:00:02Z",
            )
        ]

    def delete_batch_history(self, token, batch_id):
        self.deleted.append((token, batch_id))
        self.pages[None] = ImportHistoryPage(batches=(), next_cursor=None)


def test_history_filter_parser_and_first_next_previous_cursor_state(monkeypatch):
    import ui.gradio_app as module

    batch = _batch()
    service = _HistoryService(
        {
            None: ImportHistoryPage(batches=(batch,), next_cursor="cursor-2"),
            "cursor-2": ImportHistoryPage(batches=(), next_cursor=None),
        }
    )
    monkeypatch.setattr(module, "_require_session", lambda token: object())
    monkeypatch.setattr(module, "import_service", service)

    first = module.refresh_import_history(
        "token", ["failed", "cancelled"], "notes", "2026-08-01", "2026-08-31"
    )
    assert first[1:4] == ("", [], "cursor-2")
    assert first[4:] == ("", [], "", [], False, "")
    assert service.calls[-1][1] == {
        "statuses": ["failed", "cancelled"],
        "filename_query": "notes",
        "created_from": "2026-08-01",
        "created_to": "2026-08-31",
    }

    second = module.next_import_history_page(
        "token", ["failed"], "notes", "", "", "", [], "cursor-2"
    )
    assert second[0] == []
    assert second[1:4] == ("cursor-2", [None], "")

    previous = module.previous_import_history_page(
        "token", ["failed"], "notes", "", "", "cursor-2", [None]
    )
    assert previous[1:4] == ("", [], "cursor-2")
    assert service.calls[-1][2] is None


def test_history_selection_lazily_loads_safe_task_timeline(monkeypatch):
    import ui.gradio_app as module

    batch = _batch()
    service = _HistoryService(
        {None: ImportHistoryPage(batches=(batch,), next_cursor=None)}
    )
    monkeypatch.setattr(module, "_require_session", lambda token: object())
    monkeypatch.setattr(module, "import_service", service)

    selected_batch, task_rows, selected_task, timeline, confirmed = (
        module.select_import_history_batch(
            "token", [], "", "", "", "", SimpleNamespace(index=(0, 0))
        )
    )
    assert selected_batch == "batch-private"
    assert task_rows[0][0] == "notes.md"
    assert selected_task == ""
    assert timeline == []
    assert confirmed is False

    selected_task, timeline = module.select_import_history_task(
        "token", selected_batch, SimpleNamespace(index=(0, 0))
    )
    assert selected_task == ("batch-private", "task-private")
    rendered = " ".join(str(value) for row in timeline for value in row)
    assert "failed" in rendered
    assert "top-secret" not in rendered
    assert "task-private" not in rendered
    assert "batch-private" not in rendered
    assert "user-private" not in rendered
    assert "document-private" not in rendered
    assert "imports/private/task-private.md" not in rendered
    assert "Users" not in rendered


def test_history_delete_authenticates_before_confirmation_and_refreshes(monkeypatch):
    import ui.gradio_app as module

    calls = []
    monkeypatch.setattr(
        module,
        "_require_session",
        lambda token: calls.append(("auth", token)) or object(),
    )
    service = _HistoryService(
        {None: ImportHistoryPage(batches=(_batch(),), next_cursor=None)}
    )
    monkeypatch.setattr(module, "import_service", service)

    with pytest.raises(gr.Error, match="confirm deletion"):
        module.delete_import_history_batch(
            "token", [], "", "", "", "batch-private", False
        )
    assert calls == [("auth", "token")]
    assert service.deleted == []

    result = module.delete_import_history_batch(
        "token", [], "", "", "", "batch-private", True
    )
    assert service.deleted == [("token", "batch-private")]
    assert result[0] == []
    assert result[4:9] == ("", [], "", [], False)
    assert "deleted" in result[-1].lower()


def test_history_handlers_reject_missing_session_before_service_use(monkeypatch):
    import ui.gradio_app as module

    service = SimpleNamespace(
        list_task_history=lambda *_args, **_kwargs: pytest.fail("service called")
    )
    monkeypatch.setattr(module, "import_service", service)

    with pytest.raises(gr.Error, match="log in"):
        module.refresh_import_history("", [], "", "", "")


def test_history_bindings_are_manual_or_filter_driven_and_logout_clears_all_state():
    import ui.gradio_app as module

    refreshes = _bindings(module, module.refresh_import_history)
    assert len(refreshes) == 7  # login, register, button, and four filter changes
    assert all(binding.fn is not module.refresh_import_batch for binding in refreshes)

    timers = [
        block_fn
        for block_fn in module.demo.fns.values()
        if block_fn.fn is module.refresh_import_batch
        and getattr(block_fn, "queue", None) is False
    ]
    assert len(timers) == 1
    assert module.refresh_import_history not in [binding.fn for binding in timers]

    clear = _bindings(module, module.clear_import_history_ui)
    assert len(clear) == 1
    cleared = module.clear_import_history_ui()
    assert cleared == ([], "", "", "", [], "", [], "", "", [], "", [], False, "")
    assert len(clear[0].outputs) == len(cleared)


def test_history_bindings_accept_only_session_filters_cursors_and_opaque_ids():
    import ui.gradio_app as module

    expected_input_counts = {
        module.refresh_import_history: 5,
        module.next_import_history_page: 8,
        module.previous_import_history_page: 7,
        module.select_import_history_batch: 6,
        module.select_import_history_task: 2,
        module.delete_import_history_batch: 7,
    }
    for handler, expected_count in expected_input_counts.items():
        bindings = _bindings(module, handler)
        assert bindings
        for binding in bindings:
            assert len(binding.inputs) == expected_count
            assert binding.inputs[0].__class__.__name__ == "State"
            assert all(
                "path" not in (getattr(component, "label", "") or "").lower()
                and "user" not in (getattr(component, "label", "") or "").lower()
                for component in binding.inputs
            )
