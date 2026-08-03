from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
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
        error_summary=(
            r"D:\data\users\user-id-must-not-render\imports\a.md "
            "api_key=secret details={user-id-must-not-render,document-a,"
            "task-a,batch-a}"
        ),
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
    assert "document-a" not in rendered
    assert "task-a" not in rendered
    assert "batch-a" not in rendered
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


def test_plain_module_import_creates_no_data_or_workers(tmp_path):
    data_root = tmp_path / "not-created"
    environment = os.environ | {"PDF_ASSISTANT_DATA_DIR": str(data_root)}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import ui.gradio_app as app; print(type(app.demo).__name__)",
        ],
        cwd=Path(__file__).parents[2],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Blocks"
    assert not data_root.exists()


def test_initialize_services_is_idempotent_without_starting_workers(monkeypatch):
    import ui.gradio_app as module

    calls = []
    storage = object()
    registry = SimpleNamespace(storage=storage, runtime_registry=object())
    pool = SimpleNamespace(start=lambda: calls.append("start"), stop=lambda: None)
    monkeypatch.setattr(module, "session_registry", None)
    monkeypatch.setattr(module, "legacy_migration", None)
    monkeypatch.setattr(module, "import_repository", None)
    monkeypatch.setattr(module, "import_worker_pool", None)
    monkeypatch.setattr(module, "import_service", None)
    monkeypatch.setattr(module, "initialize_database", lambda path: calls.append(("db", path)))
    monkeypatch.setattr(module, "UserStorage", lambda root: storage)
    monkeypatch.setattr(module, "SessionRegistry", lambda **kwargs: registry)
    monkeypatch.setattr(module, "LegacyMigrationService", lambda *args: object())
    monkeypatch.setattr(module, "ImportTaskRepository", lambda path: object())
    monkeypatch.setattr(module, "ImportWorkerPool", lambda *args: pool)
    monkeypatch.setattr(module, "ImportTaskService", lambda *args: object())

    module.initialize_app_services()
    module.initialize_app_services()

    assert [call for call in calls if call == "start"] == []
    assert len([call for call in calls if isinstance(call, tuple)]) == 1
    assert module.import_worker_pool is pool


def test_script_worker_startup_is_idempotent(monkeypatch):
    import ui.gradio_app as module

    calls = []
    monkeypatch.setattr(module, "_import_workers_started", False)
    pool = SimpleNamespace(start=lambda: calls.append("start"), stop=lambda: None)
    monkeypatch.setattr(module, "import_worker_pool", pool)
    monkeypatch.setattr(module.atexit, "register", lambda callback: calls.append(callback))

    module.start_import_workers()
    module.start_import_workers()

    assert calls == ["start", pool.stop]


def test_submit_batch_id_is_bound_to_hidden_state():
    import ui.gradio_app as module

    binding = next(
        block_fn
        for block_fn in module.demo.fns.values()
        if block_fn.fn is module.submit_import_batch
    )

    assert len(binding.outputs) == 4
    assert binding.outputs[0].__class__.__name__ == "State"


def test_empty_poll_does_not_query_service(monkeypatch):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)

    assert module.refresh_import_batch("", "batch-a") == ("", [])
    assert service.calls == []


@pytest.mark.parametrize(
    "handler,args",
    [
        ("refresh_import_batch", ("token-a", "")),
        (
            "select_import_task",
            ("token-a", "", SimpleNamespace(index=(0, 0))),
        ),
    ],
)
def test_missing_batch_authenticates_nonblank_token_without_query(
    monkeypatch, handler, args,
):
    import ui.gradio_app as module

    service = FakeImportService()
    checked = []
    monkeypatch.setattr(module, "import_service", service)
    monkeypatch.setattr(module, "_require_session", lambda token: checked.append(token))

    assert getattr(module, handler)(*args) in [("", []), ""]
    assert checked == ["token-a"]
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


def test_logout_clear_chain_resets_selected_import_task_state():
    import ui.gradio_app as module

    clear_binding = next(
        block_fn
        for block_fn in module.demo.fns.values()
        if block_fn.fn is module.clear_import_ui
    )

    assert module.clear_import_ui()[-1] == ""
    assert len(clear_binding.outputs) == 4
    assert clear_binding.outputs[-1].__class__.__name__ == "State"
    assert clear_binding.outputs[-1].value == ""


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
