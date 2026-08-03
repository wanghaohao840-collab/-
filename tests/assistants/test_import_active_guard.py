from types import SimpleNamespace

from assistants.pdf_learning_assistant import PDFLearningAssistant


class ActiveImportRepository:
    def __init__(self, active=True):
        self.active = active
        self.calls = []

    def has_active_tasks(self, user_id):
        self.calls.append(user_id)
        return self.active


def test_clear_all_documents_refuses_while_user_has_active_imports(monkeypatch):
    repository = ActiveImportRepository()
    assistant = object.__new__(PDFLearningAssistant)
    assistant.user_id = "user-a"
    assistant.runtime = SimpleNamespace(import_task_repository=repository)
    monkeypatch.setattr(
        assistant,
        "_clear_documents_coordinated",
        lambda: (_ for _ in ()).throw(AssertionError("clear must not run")),
    )

    result = assistant.clear_all_documents()

    assert "imports are active" in result
    assert repository.calls == ["user-a"]
