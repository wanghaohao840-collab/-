from types import SimpleNamespace

from ui import gradio_app


class FakeAssistant:
    def ask(self, *args, **kwargs):
        raise AssertionError("product path must not call Assistant.ask")

    def start_summary_task(self, *args, **kwargs):
        raise AssertionError("product path must not use in-memory summaries")

    get_summary_task = start_summary_task
    cancel_summary_task = start_summary_task


class FakeQaService:
    def __init__(self):
        self.job = SimpleNamespace(
            id="task-1",
            status="running",
            progress=50,
            stage="mapping",
            safe_error_code=None,
        )

    def create_legacy_single_turn_conversation(self, token, documents):
        assert token == "session"
        assert documents
        return SimpleNamespace(id="conversation-1")

    def start_summary(self, token, conversation_id, question, request_id):
        assert conversation_id == "conversation-1"
        assert question == "summary"
        assert request_id
        return self.job

    def get_job(self, token, task_id):
        assert task_id == "task-1"
        return self.job

    def cancel_job(self, token, task_id):
        assert task_id == "task-1"
        self.job = SimpleNamespace(
            **{**vars(self.job), "status": "cancelled", "stage": "cancelled"}
        )
        return self.job


def bind(monkeypatch, assistant, qa_service):
    monkeypatch.setattr(gradio_app, "_require_assistant", lambda token: assistant)
    monkeypatch.setattr(gradio_app, "_current_qa_service", lambda: qa_service)


def test_background_summary_start_activates_polling(monkeypatch):
    assistant = FakeAssistant()
    qa_service = FakeQaService()
    bind(monkeypatch, assistant, qa_service)

    task_id, output, timer_update = gradio_app.start_summary_pdf_auto(
        "session",
        "summary",
        ["One.md | doc-1", "Two.md | doc-2"],
    )

    assert task_id == "task-1"
    assert "进度: 50/100" in output
    assert timer_update["active"] is True


def test_polling_stops_after_terminal_status(monkeypatch):
    assistant = FakeAssistant()
    qa_service = FakeQaService()
    qa_service.job = SimpleNamespace(
        **{
            **vars(qa_service.job),
            "status": "completed",
            "stage": "completed",
            "progress": 100,
        }
    )
    bind(monkeypatch, assistant, qa_service)

    output, timer_update = gradio_app.poll_summary_pdf_auto(
        "session",
        "task-1",
    )

    assert "任务状态: completed" in output
    assert timer_update["active"] is False


def test_cancel_stops_polling(monkeypatch):
    assistant = FakeAssistant()
    qa_service = FakeQaService()
    bind(monkeypatch, assistant, qa_service)

    output, timer_update = gradio_app.cancel_summary_pdf_auto(
        "session",
        "task-1",
    )

    assert "任务状态: cancelled" in output
    assert timer_update["active"] is False


def test_ask_handler_uses_shared_qa_service(monkeypatch):
    assistant = FakeAssistant()

    class Qa(FakeQaService):
        def ask(self, token, conversation_id, question, mode, request_id):
            assert (conversation_id, question, mode) == (
                "conversation-1",
                "what?",
                "joint",
            )
            return SimpleNamespace(content="shared answer", sources=())

    bind(monkeypatch, assistant, Qa())

    assert gradio_app.ask_pdf(
        "session", "what?", ["One.md | doc-1"], "joint"
    ) == "shared answer"
