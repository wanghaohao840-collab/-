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
