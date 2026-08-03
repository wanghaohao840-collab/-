import threading
from types import SimpleNamespace

from assistants.pdf_learning_assistant import PDFLearningAssistant


class ActiveImportService:
    def __init__(self, active=True):
        self.active = active
        self.calls = []

    def has_active_tasks(self, user_id):
        self.calls.append(user_id)
        return self.active


def test_clear_all_documents_refuses_while_user_has_active_imports(monkeypatch):
    service = ActiveImportService()
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
