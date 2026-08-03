from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import gradio as gr
import pytest

from app.import_models import ImportBatchSummary, ImportTaskRecord


def _task(**changes):
    base = ImportTaskRecord(
        task_id="task-a",
        batch_id="batch-a",
        user_id="user-id-must-not-render",
        document_id="document-a",
        original_name="notes.md",
        file_suffix=".md",
        size_bytes=5,
        staged_relative_path=r"D:\data\users\user-id-must-not-render\imports\a.md",
        status="failed",
        stage="failed",
        progress=40,
        total_attempt_count=2,
        auto_retry_count=1,
        manual_retry_count=0,
        max_auto_retries=3,
        next_attempt_at=None,
        error_code="document_invalid",
        error_summary=r"D:\data\users\user-id-must-not-render\imports\a.md api_key=secret",
        created_at="2026-08-01T00:00:00Z",
        started_at="2026-08-01T00:00:01Z",
        finished_at="2026-08-01T00:00:02Z",
        updated_at="2026-08-01T00:00:02Z",
    )
    return replace(base, **changes)


def _summary(*tasks):
    items = tasks or (_task(),)
    return ImportBatchSummary(
        batch_id="batch-a",
        user_id="user-id-must-not-render",
        created_at="2026-08-01T00:00:00Z",
        updated_at="2026-08-01T00:00:02Z",
        total=len(items),
        queued=sum(task.status == "queued" for task in items),
        running=sum(task.status == "running" for task in items),
        retry_wait=sum(task.status == "retry_wait" for task in items),
        succeeded=sum(task.status == "succeeded" for task in items),
        failed=sum(task.status == "failed" for task in items),
        tasks=tuple(items),
    )


class FakeImportService:
    def __init__(self):
        self.calls = []

    def submit_batch(self, token, files, progress=None):
        self.calls.append(("submit_batch", token, files))
        return _summary(_task(status="queued", stage="queued", progress=10))

    def list_batches(self, token, limit=50):
        self.calls.append(("list_batches", token, limit))
        return [_summary()]

    def get_batch(self, token, batch_id):
        self.calls.append(("get_batch", token, batch_id))
        return _summary()

    def retry_task(self, token, task_id):
        self.calls.append(("retry_task", token, task_id))
        return _summary(_task(status="queued", stage="queued", progress=0))

    def retry_failed_in_batch(self, token, batch_id):
        self.calls.append(("retry_failed_in_batch", token, batch_id))
        return _summary(_task(status="queued", stage="queued", progress=0))


def test_submit_import_batch_rejects_missing_token_before_service(monkeypatch):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)

    with pytest.raises(gr.Error, match="log in"):
        module.submit_import_batch("", ["a.md"], progress=None)

    assert service.calls == []


def test_task_table_and_summary_do_not_render_private_fields():
    from ui.gradio_app import format_batch_summary, format_task_table

    rendered = repr(format_task_table(_summary())) + format_batch_summary(_summary())

    assert "user-id-must-not-render" not in rendered
    assert r"D:\data\users" not in rendered
    assert "secret" not in rendered
    assert "api_key" not in rendered


def test_task_table_localizes_status_stage_and_retry_time():
    from ui.gradio_app import format_task_table

    row = format_task_table(
        _summary(
            _task(
                status="retry_wait",
                stage="embedding",
                next_attempt_at="2026-08-01T00:00:03Z",
            )
        )
    )[0]

    assert row[1:5] == ["等待重试", "生成嵌入", 40, 2]
    assert row[5] != "2026-08-01T00:00:03Z"


def test_retry_handler_passes_only_current_session_token(monkeypatch):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)
    monkeypatch.setattr(module, "_require_session", lambda token: object())

    module.retry_import_task("token-a", "task-a")

    assert service.calls == [("retry_task", "token-a", "task-a")]


def test_selected_row_resolves_to_server_owned_task_id(monkeypatch):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)
    monkeypatch.setattr(module, "_require_session", lambda token: object())

    task_id = module.select_import_task(
        "token-a",
        "batch-a",
        SimpleNamespace(index=(0, 0)),
    )

    assert task_id == "task-a"
    assert service.calls == [("get_batch", "token-a", "batch-a")]


def test_empty_poll_does_not_query_service(monkeypatch):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)

    assert module.refresh_import_batch("", "batch-a") == ("", [])
    assert service.calls == []


def test_empty_batch_list_refresh_does_not_query_service(monkeypatch):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)

    update, summary, rows = module.refresh_import_batches("")

    assert update == gr.update(choices=[], value=None)
    assert summary == ""
    assert rows == []
    assert service.calls == []


def test_upload_document_delegates_single_file_after_authentication(monkeypatch):
    import ui.gradio_app as module

    source = Path("notes.md")
    monkeypatch.setattr(module, "_require_session", lambda token: object())
    monkeypatch.setattr(
        module,
        "submit_import_batch",
        lambda token, files, progress=None: (token, files, progress),
    )

    assert module.upload_document("token-a", source) == (
        "token-a",
        [source],
        None,
    )


def test_upload_document_rejects_empty_file_after_authentication(monkeypatch):
    import ui.gradio_app as module

    checked = []
    monkeypatch.setattr(module, "_require_session", lambda token: checked.append(token))

    with pytest.raises(gr.Error, match="select"):
        module.upload_document("token-a", None)

    assert checked == ["token-a"]


def test_batch_refresh_is_authenticated_and_user_scoped(monkeypatch):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)
    monkeypatch.setattr(module, "_require_session", lambda token: object())

    update, text, rows = module.refresh_import_batches("token-a")

    assert update["value"] == "batch-a"
    assert "总数" in text
    assert rows[0][0] == "notes.md"
    assert service.calls == [("list_batches", "token-a", 50)]
