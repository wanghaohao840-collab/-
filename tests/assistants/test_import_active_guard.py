import threading
from types import SimpleNamespace

import pytest

from app.import_repository import ACTIVE_STATUSES
from app.import_service import ImportTaskService
from assistants.pdf_learning_assistant import PDFLearningAssistant


class ActiveImportService:
    def __init__(self, active=None, status="running"):
        self.active = status in ACTIVE_STATUSES if active is None else active
        self.status = status
        self.calls = []

    def has_active_tasks(self, user_id):
        self.calls.append(user_id)
        return self.active

    def has_active_task_for_document(self, user_id, document_id):
        self.calls.append((user_id, document_id))
        return self.active


@pytest.mark.parametrize(
    "status", ["pause_requested", "paused", "cancel_requested"]
)
def test_clear_all_documents_refuses_while_controlled_import_is_active(
    monkeypatch, status
):
    service = ActiveImportService(status=status)
    assistant = object.__new__(PDFLearningAssistant)
    assistant.user_id = "user-a"
    assistant.runtime = SimpleNamespace(import_task_service=service)
    monkeypatch.setattr(
        assistant,
        "_clear_documents_coordinated",
        lambda: (_ for _ in ()).throw(AssertionError("clear must not run")),
    )

    result = assistant.clear_all_documents()

    assert "imports are active" in result
    assert service.calls == ["user-a"]


@pytest.mark.parametrize(
    "status", ["pause_requested", "paused", "cancel_requested"]
)
def test_delete_current_document_refuses_while_matching_control_is_active(
    monkeypatch, status
):
    service = ActiveImportService(status=status)
    assistant = object.__new__(PDFLearningAssistant)
    assistant.user_id = "user-a"
    assistant.current_document_id = "document-a"
    assistant.current_document = "/documents/a.md"
    assistant.runtime = SimpleNamespace(
        import_task_service=service, lock=threading.RLock()
    )
    monkeypatch.setattr(
        assistant,
        "_delete_document_coordinated",
        lambda _document_id: (_ for _ in ()).throw(
            AssertionError("delete must not run")
        ),
    )

    result = assistant.delete_current_document()

    assert "import is active" in result
    assert service.calls == [("user-a", "document-a")]
    assert assistant.current_document_id == "document-a"
    assert assistant.current_document == "/documents/a.md"


@pytest.mark.parametrize("status", ["cancelled", "succeeded", "failed"])
def test_delete_current_document_allows_terminal_control_compensation(
    monkeypatch, status
):
    service = ActiveImportService(active=False, status=status)
    assistant = object.__new__(PDFLearningAssistant)
    assistant.user_id = "user-a"
    assistant.current_document_id = "document-a"
    assistant.current_document = "/documents/a.md"
    assistant.runtime = SimpleNamespace(
        import_task_service=service, lock=threading.RLock()
    )
    monkeypatch.setattr(
        assistant, "_delete_document_coordinated", lambda document_id: document_id
    )

    assert assistant.delete_current_document() == "document-a"
    assert service.calls == [("user-a", "document-a")]
    assert assistant.current_document_id is None
    assert assistant.current_document is None


def test_clear_all_documents_keeps_legacy_behavior_without_runtime(monkeypatch):
    assistant = object.__new__(PDFLearningAssistant)
    assistant.user_id = "user-a"
    monkeypatch.setattr(assistant, "_clear_documents_coordinated", lambda: "cleared")

    assert assistant.clear_all_documents() == "cleared"


def test_clear_all_documents_holds_runtime_lock_across_guard_and_clear(monkeypatch):
    checked = threading.Event()
    allow_guard = threading.Event()
    clear_started = threading.Event()
    allow_clear = threading.Event()
    submit_entered = threading.Event()

    class InactiveImportService:
        def has_active_tasks(self, _user_id):
            checked.set()
            assert allow_guard.wait(timeout=3)
            return False

    runtime = SimpleNamespace(
        import_task_service=InactiveImportService(), lock=threading.RLock()
    )
    assistant = object.__new__(PDFLearningAssistant)
    assistant.user_id = "user-a"
    assistant.runtime = runtime
    def blocked_clear():
        clear_started.set()
        assert allow_clear.wait(timeout=3)
        return "cleared"

    monkeypatch.setattr(assistant, "_clear_documents_coordinated", blocked_clear)

    clear_thread = threading.Thread(target=assistant.clear_all_documents)
    clear_thread.start()
    assert checked.wait(timeout=3)
    submit_thread = threading.Thread(
        target=lambda: _enter_runtime_lock(runtime, submit_entered)
    )
    submit_thread.start()
    assert not submit_entered.wait(timeout=0.1)
    allow_guard.set()
    assert clear_started.wait(timeout=3)
    assert not submit_entered.wait(timeout=0.1)
    allow_clear.set()
    clear_thread.join(timeout=3)
    submit_thread.join(timeout=3)
    assert submit_entered.is_set()


def _enter_runtime_lock(runtime, entered):
    with runtime.lock:
        entered.set()


class _TwoPartyProbeLock:
    def __init__(self):
        self._lock = threading.Lock()
        self._counter_lock = threading.Lock()
        self._attempts = 0
        self.second_attempted = threading.Event()

    def __enter__(self):
        with self._counter_lock:
            self._attempts += 1
            if self._attempts == 2:
                self.second_attempted.set()
        self._lock.acquire()
        return self

    def __exit__(self, *_args):
        self._lock.release()


@pytest.mark.parametrize(
    ("destructive_method", "coordinated_method"),
    [
        ("clear_all_documents", "_clear_documents_coordinated"),
        ("delete_current_document", "_delete_document_coordinated"),
    ],
)
def test_task_control_shares_destructive_guard_request_gate(
    monkeypatch, destructive_method, coordinated_method
):
    destructive_started = threading.Event()
    allow_destructive = threading.Event()
    transition_called = threading.Event()
    probe_lock = _TwoPartyProbeLock()
    summary = SimpleNamespace(batch_id="batch-a", user_id="user-a")

    class Repository:
        def has_active_tasks(self, _user_id):
            return False

        def has_active_task_for_document(self, _user_id, _document_id):
            return False

        def request_cancel(self, user_id, task_id):
            assert (user_id, task_id) == ("user-a", "task-a")
            transition_called.set()
            return SimpleNamespace(batch_id="batch-a")

        def get_batch(self, user_id, batch_id):
            assert (user_id, batch_id) == ("user-a", "batch-a")
            return summary

    runtime = SimpleNamespace(lock=threading.RLock(), import_control_lock=probe_lock)
    sessions = SimpleNamespace(
        get_session=lambda token: SimpleNamespace(user_id="user-a", runtime=runtime)
    )
    workers = SimpleNamespace(notify=lambda: None)
    service = ImportTaskService(
        sessions,
        Repository(),
        storage=SimpleNamespace(),
        worker_pool=workers,
    )
    runtime.import_task_service = service
    assistant = object.__new__(PDFLearningAssistant)
    assistant.user_id = "user-a"
    assistant.current_document_id = "document-a"
    assistant.current_document = "/documents/a.md"
    assistant.runtime = runtime

    def blocked_destructive(*_args):
        destructive_started.set()
        assert allow_destructive.wait(timeout=3)
        return "done"

    monkeypatch.setattr(assistant, coordinated_method, blocked_destructive)
    destructive_errors = []
    control_errors = []
    destructive_thread = threading.Thread(
        target=lambda: _capture_error(
            destructive_errors, getattr(assistant, destructive_method)
        )
    )
    control_thread = threading.Thread(
        target=lambda: _capture_error(
            control_errors, service.cancel_task, "valid-token", "task-a"
        )
    )

    destructive_thread.start()
    assert destructive_started.wait(timeout=3)
    control_thread.start()
    assert probe_lock.second_attempted.wait(timeout=3)
    assert transition_called.is_set() is False
    allow_destructive.set()
    destructive_thread.join(timeout=3)
    control_thread.join(timeout=3)

    assert not destructive_thread.is_alive()
    assert not control_thread.is_alive()
    assert destructive_errors == []
    assert control_errors == []
    assert transition_called.is_set()


def _capture_error(errors, call, *args):
    try:
        call(*args)
    except Exception as error:  # pragma: no cover - surfaced by assertion
        errors.append(error)
