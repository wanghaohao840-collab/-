from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import uuid
from types import SimpleNamespace

import gradio as gr
import pytest

from app.import_models import ImportBatchSummary, ImportTaskRecord
from app.import_models import ImportTaskCreate
from app.import_repository import ImportTaskRepository, InvalidImportTransition
from app.import_service import ImportTaskService
from app.database import initialize_database
from app.session import SessionRegistry
from app.storage import UserStorage


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

    def retry_task(self, token, task_id, expected_batch_id=None):
        self.calls.append(("retry_task", token, task_id, expected_batch_id))
        return _summary(_task(status="queued", stage="queued", progress=0))

    def retry_failed_in_batch(self, token, batch_id):
        self.calls.append(("retry_failed_in_batch", token, batch_id))
        return _summary(_task(status="queued", stage="queued", progress=0))

    def pause_task(self, token, task_id):
        self.calls.append(("pause_task", token, task_id))
        return _summary(_task(status="paused", stage="paused"))

    def resume_task(self, token, task_id):
        self.calls.append(("resume_task", token, task_id))
        return _summary(_task(status="queued", stage="queued"))

    def cancel_task(self, token, task_id):
        self.calls.append(("cancel_task", token, task_id))
        return _summary(_task(status="cancel_requested", stage="queued"))

    def pause_batch(self, token, batch_id):
        self.calls.append(("pause_batch", token, batch_id))
        return _summary(_task(status="paused", stage="paused"))

    def resume_batch(self, token, batch_id):
        self.calls.append(("resume_batch", token, batch_id))
        return _summary(_task(status="queued", stage="queued"))

    def cancel_batch(self, token, batch_id):
        self.calls.append(("cancel_batch", token, batch_id))
        return _summary(_task(status="cancel_requested", stage="queued"))


class _RecordingWorkerPool:
    def __init__(self):
        self.notify_count = 0

    def notify(self):
        self.notify_count += 1


def _real_control_harness(tmp_path, monkeypatch):
    import ui.gradio_app as module

    database_path = tmp_path / "app.db"
    storage = UserStorage(tmp_path / "data")
    initialize_database(database_path)
    registry = SessionRegistry(db_path=database_path, storage=storage)
    owner_token = registry.register("ImportOwner", "correct horse battery")
    other_token = registry.register("ImportOther", "correct horse battery")
    repository = ImportTaskRepository(database_path)
    workers = _RecordingWorkerPool()
    service = ImportTaskService(registry, repository, storage, workers)
    monkeypatch.setattr(module, "session_registry", registry)
    monkeypatch.setattr(module, "import_service", service)
    return SimpleNamespace(
        module=module,
        storage=storage,
        registry=registry,
        repository=repository,
        workers=workers,
        owner_token=owner_token,
        other_token=other_token,
    )


def _seed_real_import_task(harness, token):
    session = harness.registry.get_session(token)
    batch_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    document_id = str(uuid.uuid4())
    staged = harness.storage.staged_import_path(session.user_id, batch_id, task_id, ".md")
    staged.write_bytes(b"private staged import")
    harness.repository.create_batch(
        session.user_id,
        [
            ImportTaskCreate(
                task_id=task_id,
                batch_id=batch_id,
                user_id=session.user_id,
                document_id=document_id,
                original_name="private.md",
                file_suffix=".md",
                size_bytes=21,
                staged_relative_path=str(
                    staged.relative_to(harness.storage.user_paths(session.user_id).root)
                ),
            )
        ],
    )
    return SimpleNamespace(
        user_id=session.user_id,
        batch_id=batch_id,
        task_id=task_id,
        document_id=document_id,
        staged=staged,
    )


def _real_task_snapshot(harness, seeded):
    return (
        harness.repository.get_batch(seeded.user_id, seeded.batch_id),
        harness.repository.list_task_events(seeded.user_id, seeded.task_id),
        seeded.staged.read_bytes(),
        harness.workers.notify_count,
    )


def _assert_safe_control_error(exc_info, *private_values):
    rendered = str(exc_info.value)
    for value in private_values:
        assert str(value) not in rendered


def test_real_task_and_batch_cross_user_controls_have_zero_side_effects(tmp_path, monkeypatch):
    harness = _real_control_harness(tmp_path, monkeypatch)
    seeded = _seed_real_import_task(harness, harness.owner_token)
    before = _real_task_snapshot(harness, seeded)

    with pytest.raises(gr.Error) as task_error:
        harness.module.pause_import_task(
            harness.other_token, seeded.batch_id, (seeded.batch_id, seeded.task_id)
        )
    with pytest.raises(gr.Error) as batch_error:
        harness.module.cancel_import_batch(harness.other_token, seeded.batch_id, True)

    _assert_safe_control_error(task_error, seeded.batch_id, seeded.task_id, seeded.user_id, seeded.staged)
    _assert_safe_control_error(batch_error, seeded.batch_id, seeded.task_id, seeded.user_id, seeded.staged)
    assert _real_task_snapshot(harness, seeded) == before


def test_real_wrong_batch_task_membership_has_zero_side_effects(tmp_path, monkeypatch):
    harness = _real_control_harness(tmp_path, monkeypatch)
    displayed = _seed_real_import_task(harness, harness.owner_token)
    hidden_other = _seed_real_import_task(harness, harness.owner_token)
    before = (_real_task_snapshot(harness, displayed), _real_task_snapshot(harness, hidden_other))

    with pytest.raises(gr.Error) as exc_info:
        harness.module.pause_import_task(
            harness.owner_token,
            displayed.batch_id,
            (displayed.batch_id, hidden_other.task_id),
        )

    _assert_safe_control_error(
        exc_info, displayed.batch_id, hidden_other.batch_id, hidden_other.task_id, hidden_other.staged
    )
    assert (_real_task_snapshot(harness, displayed), _real_task_snapshot(harness, hidden_other)) == before


def test_real_duplicate_transition_has_zero_side_effects_after_first_control(tmp_path, monkeypatch):
    harness = _real_control_harness(tmp_path, monkeypatch)
    seeded = _seed_real_import_task(harness, harness.owner_token)

    harness.module.pause_import_task(
        harness.owner_token, seeded.batch_id, (seeded.batch_id, seeded.task_id)
    )
    after_first = _real_task_snapshot(harness, seeded)

    with pytest.raises(gr.Error) as exc_info:
        harness.module.pause_import_task(
            harness.owner_token, seeded.batch_id, (seeded.batch_id, seeded.task_id)
        )

    _assert_safe_control_error(exc_info, seeded.batch_id, seeded.task_id, seeded.staged)
    assert _real_task_snapshot(harness, seeded) == after_first


def test_real_successful_cancel_resets_confirmation_and_duplicate_is_side_effect_free(
    tmp_path, monkeypatch,
):
    harness = _real_control_harness(tmp_path, monkeypatch)
    seeded = _seed_real_import_task(harness, harness.owner_token)

    output = harness.module.cancel_import_task(
        harness.owner_token, seeded.batch_id, (seeded.batch_id, seeded.task_id), True
    )
    after_first = _real_task_snapshot(harness, seeded)

    assert output[-1] is False
    assert after_first[0].tasks[0].status == "cancel_requested"
    assert after_first[2] == b"private staged import"
    assert after_first[3] == 1
    with pytest.raises(gr.Error) as exc_info:
        harness.module.cancel_import_task(
            harness.owner_token, seeded.batch_id, (seeded.batch_id, seeded.task_id), True
        )

    _assert_safe_control_error(exc_info, seeded.batch_id, seeded.task_id, seeded.staged)
    assert _real_task_snapshot(harness, seeded) == after_first


def test_real_successful_batch_cancel_resets_confirmation(tmp_path, monkeypatch):
    harness = _real_control_harness(tmp_path, monkeypatch)
    seeded = _seed_real_import_task(harness, harness.owner_token)

    output = harness.module.cancel_import_batch(
        harness.owner_token, seeded.batch_id, True
    )
    after_cancel = _real_task_snapshot(harness, seeded)

    assert output[-1] is False
    assert after_cancel[0].tasks[0].status == "cancel_requested"
    assert after_cancel[2] == b"private staged import"
    assert after_cancel[3] == 1


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


@pytest.mark.parametrize(
    ("error_text", "raw_values"),
    [
        ("password=correct-horse", ("correct-horse",)),
        ('{"passwd": "open-sesame"}', ("open-sesame",)),
        ("pwd: letmein", ("letmein",)),
        ("secret=shh client_secret: client-value", ("shh", "client-value")),
        ("credential: credential-value", ("credential-value",)),
        ("Authorization: Bearer auth-token", ("auth-token",)),
        ("Bearer standalone-token", ("standalone-token",)),
        (
            "https://alice:correct-horse@example.test/import",
            ("alice", "correct-horse"),
        ),
    ],
)
def test_task_error_redacts_credentials_without_hiding_safe_text(error_text, raw_values):
    from ui.gradio_app import _format_import_error

    rendered = _format_import_error(
        _task(error_summary=f"Import notes.md failed: {error_text}; retry later")
    )

    assert "Import notes.md failed" in rendered
    assert "retry later" in rendered
    assert len(rendered) <= 500
    for value in raw_values:
        assert value not in rendered


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


@pytest.mark.parametrize(
    ("status", "stage", "label"),
    [
        ("pause_requested", "queued", "暂停请求中"),
        ("paused", "paused", "已暂停"),
        ("cancel_requested", "queued", "取消请求中"),
        ("cancelled", "cancelled", "已取消"),
    ],
)
def test_task_table_localizes_active_control_states(status, stage, label):
    from ui.gradio_app import format_task_table

    row = format_task_table(_summary(_task(status=status, stage=stage)))[0]

    assert row[1] == label
    assert row[2] != "未知"


def test_batch_summary_includes_localized_active_control_counts():
    from ui.gradio_app import format_batch_summary

    summary = _summary(
        _task(status="paused", stage="paused"),
        _task(status="pause_requested", stage="queued"),
        _task(status="cancel_requested", stage="queued"),
        _task(status="cancelled", stage="cancelled"),
    )
    summary = replace(
        summary,
        paused=1,
        pause_requested=1,
        cancel_requested=1,
        cancelled=1,
    )

    rendered = format_batch_summary(summary)

    for label in ("已暂停：1", "暂停请求中：1", "取消请求中：1", "已取消：1"):
        assert label in rendered
    assert all(value not in rendered for value in ("batch-a", "user-id-must-not-render"))


def test_retry_handler_passes_only_current_session_token(monkeypatch):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)
    monkeypatch.setattr(module, "_require_session", lambda token: object())

    result = module.retry_import_task(
        "token-a", "batch-a", ("batch-a", "task-a")
    )

    assert service.calls == [("retry_task", "token-a", "task-a", "batch-a")]
    assert result[-1] == ""


@pytest.mark.parametrize(
    ("handler_name", "status", "confirmed", "expected_call"),
    [
        ("pause_import_task", "queued", None, ("pause_task", "token-a", "task-a")),
        ("resume_import_task", "paused", None, ("resume_task", "token-a", "task-a")),
        ("cancel_import_task", "queued", True, ("cancel_task", "token-a", "task-a")),
    ],
)
def test_task_controls_validate_selection_and_pass_only_token_and_hidden_task_id(
    monkeypatch, handler_name, status, confirmed, expected_call,
):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)
    monkeypatch.setattr(module, "_require_session", lambda token: object())
    monkeypatch.setattr(service, "get_batch", lambda token, batch_id: _summary(_task(status=status)))

    args = ["token-a", "batch-a", ("batch-a", "task-a")]
    if confirmed is not None:
        args.append(confirmed)
    result = getattr(module, handler_name)(*args)

    assert service.calls == [expected_call]
    if handler_name == "cancel_import_task":
        assert result[-2] in {"", ("batch-a", "task-a")}
        assert result[-1] is False
    else:
        assert result[-1] in {"", ("batch-a", "task-a")}


@pytest.mark.parametrize(
    ("handler_name", "status", "confirmed"),
    [
        ("pause_import_task", "paused", None),
        ("resume_import_task", "queued", None),
        ("cancel_import_task", "cancel_requested", True),
    ],
)
def test_task_controls_reject_stale_or_duplicate_selection_before_mutation(
    monkeypatch, handler_name, status, confirmed,
):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)
    monkeypatch.setattr(module, "_require_session", lambda token: object())
    monkeypatch.setattr(service, "get_batch", lambda token, batch_id: _summary(_task(status=status)))

    args = ["token-a", "batch-a", ("batch-a", "task-a")]
    if confirmed is not None:
        args.append(confirmed)
    with pytest.raises(gr.Error, match="cannot"):
        getattr(module, handler_name)(*args)

    assert all(call[0] != "pause_task" for call in service.calls)


def test_task_controls_reject_wrong_batch_and_missing_or_forged_selection_safely(monkeypatch):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)
    monkeypatch.setattr(module, "_require_session", lambda token: object())

    for selection in ("", ("batch-other", "task-a"), ("batch-a", "forged-task")):
        with pytest.raises(gr.Error) as exc_info:
            module.pause_import_task("token-a", "batch-a", selection)
        assert "task-a" not in str(exc_info.value)
        assert "batch-a" not in str(exc_info.value)

    assert all(call[0] != "pause_task" for call in service.calls)


def test_task_control_converts_expected_service_errors_without_leaking_identifiers(monkeypatch):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)
    monkeypatch.setattr(module, "_require_session", lambda token: object())
    monkeypatch.setattr(service, "get_batch", lambda token, batch_id: _summary(_task(status="queued")))

    def rejected(token, task_id):
        raise InvalidImportTransition(f"task {task_id} cannot change in batch batch-a")

    monkeypatch.setattr(service, "pause_task", rejected)
    with pytest.raises(gr.Error) as exc_info:
        module.pause_import_task("token-a", "batch-a", ("batch-a", "task-a"))

    assert "task-a" not in str(exc_info.value)
    assert "batch-a" not in str(exc_info.value)


def test_task_control_does_not_hide_unexpected_service_errors(monkeypatch):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)
    monkeypatch.setattr(module, "_require_session", lambda token: object())
    monkeypatch.setattr(service, "get_batch", lambda token, batch_id: _summary(_task(status="queued")))
    monkeypatch.setattr(
        service, "pause_task", lambda token, task_id: (_ for _ in ()).throw(RuntimeError("unexpected"))
    )

    with pytest.raises(RuntimeError, match="unexpected"):
        module.pause_import_task("token-a", "batch-a", ("batch-a", "task-a"))


def test_task_control_rejects_cross_user_batch_without_disclosing_or_mutating(monkeypatch):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)
    monkeypatch.setattr(module, "_require_session", lambda token: object())

    def missing_for_current_user(token, batch_id):
        raise KeyError(f"batch {batch_id} belongs to another user")

    monkeypatch.setattr(service, "get_batch", missing_for_current_user)
    with pytest.raises(gr.Error) as exc_info:
        module.pause_import_task("token-a", "private-batch", ("private-batch", "private-task"))

    rendered = str(exc_info.value)
    assert "private-batch" not in rendered
    assert "private-task" not in rendered
    assert service.calls == []


def test_task_cancel_requires_explicit_confirmation_before_validation_or_mutation(monkeypatch):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)
    monkeypatch.setattr(module, "_require_session", lambda token: object())

    with pytest.raises(gr.Error, match="confirm"):
        module.cancel_import_task("token-a", "batch-a", ("batch-a", "task-a"), False)

    assert service.calls == []


def test_successful_task_cancel_clears_confirmation_state(monkeypatch):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)
    monkeypatch.setattr(module, "_require_session", lambda token: object())
    monkeypatch.setattr(service, "get_batch", lambda token, batch_id: _summary(_task(status="queued")))

    result = module.cancel_import_task("token-a", "batch-a", ("batch-a", "task-a"), True)

    assert result[-1] is False
    assert service.calls == [("cancel_task", "token-a", "task-a")]


def test_task_cancel_authenticates_before_reporting_missing_confirmation(monkeypatch):
    import ui.gradio_app as module

    checked = []
    monkeypatch.setattr(module, "_require_session", lambda token: checked.append(token))

    with pytest.raises(gr.Error, match="confirm"):
        module.cancel_import_task("token-a", "batch-a", ("batch-a", "task-a"), False)

    assert checked == ["token-a"]


@pytest.mark.parametrize(
    ("handler_name", "status", "confirmed", "expected_call"),
    [
        ("pause_import_batch", "queued", None, ("pause_batch", "token-a", "batch-a")),
        ("resume_import_batch", "paused", None, ("resume_batch", "token-a", "batch-a")),
        ("cancel_import_batch", "queued", True, ("cancel_batch", "token-a", "batch-a")),
    ],
)
def test_batch_controls_pass_only_token_and_visible_batch_id(
    monkeypatch, handler_name, status, confirmed, expected_call,
):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)
    monkeypatch.setattr(module, "_require_session", lambda token: object())
    monkeypatch.setattr(service, "get_batch", lambda token, batch_id: _summary(_task(status=status)))

    args = ["token-a", "batch-a"]
    if confirmed is not None:
        args.append(confirmed)
    result = getattr(module, handler_name)(*args)

    assert service.calls == [expected_call]
    if handler_name == "cancel_import_batch":
        assert result[-2:] == ("", False)
    else:
        assert result[-1] == ""


@pytest.mark.parametrize(
    ("handler_name", "status", "confirmed"),
    [
        ("pause_import_batch", "paused", None),
        ("resume_import_batch", "queued", None),
        ("cancel_import_batch", "cancel_requested", True),
    ],
)
def test_batch_controls_reject_duplicate_or_wrong_state_before_mutation(
    monkeypatch, handler_name, status, confirmed,
):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)
    monkeypatch.setattr(module, "_require_session", lambda token: object())
    monkeypatch.setattr(service, "get_batch", lambda token, batch_id: _summary(_task(status=status)))

    args = ["token-a", "batch-a"]
    if confirmed is not None:
        args.append(confirmed)
    with pytest.raises(gr.Error, match="changed"):
        getattr(module, handler_name)(*args)

    assert service.calls == []


def test_selected_row_resolves_to_server_owned_task_id(monkeypatch):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)
    monkeypatch.setattr(module, "_require_session", lambda token: object())

    selection = module.select_import_task(
        "token-a",
        "batch-a",
        SimpleNamespace(index=(0, 0)),
    )

    assert selection == ("batch-a", "task-a")
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


def test_supported_launch_stops_real_workers_before_process_exit(tmp_path):
    data_root = tmp_path / "launch-data"
    environment = os.environ | {"PDF_ASSISTANT_DATA_DIR": str(data_root)}
    code = """
import threading
import ui.gradio_app as app

class NoServerDemo:
    def launch(self, **_kwargs):
        assert any(thread.name == "import-scheduler" for thread in threading.enumerate())
        print("launch-returning", flush=True)

app.demo = NoServerDemo()
app.launch_app()
assert not any(thread.name.startswith("import-") for thread in threading.enumerate())
print("launch-finished", flush=True)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parents[2],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["launch-returning", "launch-finished"]


def test_initialize_services_is_idempotent_without_starting_workers(monkeypatch):
    import ui.gradio_app as module

    calls = []
    storage = object()
    injected = []
    registry = SimpleNamespace(
        storage=storage,
        runtime_registry=SimpleNamespace(
            set_import_task_service=lambda service: injected.append(service)
        ),
    )
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
    assert injected == [module.import_service]


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

    assert len(binding.outputs) == 5
    assert binding.outputs[0].__class__.__name__ == "State"
    assert binding.outputs[-1].__class__.__name__ == "State"


def test_empty_poll_does_not_query_service(monkeypatch):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)

    assert module.refresh_import_batch("", "batch-a", ("batch-a", "task-a")) == (
        "",
        [],
        "",
    )
    assert service.calls == []


@pytest.mark.parametrize(
    "handler,args",
    [
        ("refresh_import_batch", ("token-a", "", ("batch-a", "task-a"))),
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

    assert getattr(module, handler)(*args) in [("", [], ""), ""]
    assert checked == ["token-a"]
    assert service.calls == []


def test_empty_batch_list_refresh_does_not_query_service(monkeypatch):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)

    update, summary, rows, selection = module.refresh_import_batches("")

    assert update == gr.update(choices=[], value=None)
    assert summary == ""
    assert rows == []
    assert selection == ""
    assert service.calls == []


def test_logout_clear_chain_resets_selected_import_task_state():
    import ui.gradio_app as module

    clear_binding = next(
        block_fn
        for block_fn in module.demo.fns.values()
        if block_fn.fn is module.clear_import_ui
    )

    assert module.clear_import_ui()[-2:] == ("", False)
    assert len(clear_binding.outputs) == 5
    assert clear_binding.outputs[-2].__class__.__name__ == "State"
    assert clear_binding.outputs[-2].value == ""
    assert clear_binding.outputs[-1].__class__.__name__ == "Checkbox"


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

    update, text, rows, selection = module.refresh_import_batches("token-a")

    assert update["value"] == "batch-a"
    assert "总数" in text
    assert rows[0][0] == "notes.md"
    assert selection == ""
    assert service.calls == [("list_batches", "token-a", 50)]


def test_timer_refresh_keeps_only_selection_tied_to_visible_failed_task(monkeypatch):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)
    monkeypatch.setattr(module, "_require_session", lambda token: object())

    kept = module.refresh_import_batch(
        "token-a", "batch-a", ("batch-a", "task-a")
    )
    stale = module.refresh_import_batch(
        "token-a", "batch-a", ("batch-b", "task-a")
    )

    assert kept[-1] == ("batch-a", "task-a")
    assert stale[-1] == ""


def test_retry_rejects_stale_selection_from_another_batch(monkeypatch):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)
    monkeypatch.setattr(module, "_require_session", lambda token: object())

    with pytest.raises(gr.Error, match="visible batch"):
        module.retry_import_task("token-a", "batch-b", ("batch-a", "task-a"))

    assert service.calls == []


def test_batch_retry_clears_selected_task(monkeypatch):
    import ui.gradio_app as module

    service = FakeImportService()
    monkeypatch.setattr(module, "import_service", service)
    monkeypatch.setattr(module, "_require_session", lambda token: object())

    result = module.retry_import_batch_failures("token-a", "batch-a")

    assert result[-1] == ""


def test_batch_table_refresh_bindings_update_selection_state():
    import ui.gradio_app as module

    refresh_bindings = [
        block_fn
        for block_fn in module.demo.fns.values()
        if block_fn.fn in {module.refresh_import_batch, module.refresh_import_batches}
    ]
    retry_bindings = [
        block_fn
        for block_fn in module.demo.fns.values()
        if block_fn.fn
        in {module.retry_import_task, module.retry_import_batch_failures}
    ]

    assert refresh_bindings
    assert retry_bindings
    assert all(
        binding.outputs[-1].__class__.__name__ == "State"
        for binding in refresh_bindings
    )
    assert all(
        binding.outputs[-1].__class__.__name__ == "State"
        for binding in retry_bindings
    )
