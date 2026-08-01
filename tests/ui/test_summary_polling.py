from ui import gradio_app


class FakeAssistant:
    def __init__(self):
        self.task = {
            "task_id": "task-1",
            "status": "running",
            "completed": 1,
            "total": 2,
            "stage": "mapping",
            "current_document_id": "doc-1",
            "result": None,
            "error": None,
        }

    def start_summary_task(self, question, selected_documents, limit):
        return dict(self.task)

    def get_summary_task(self, task_id):
        assert task_id == "task-1"
        return dict(self.task)

    def cancel_summary_task(self, task_id):
        assert task_id == "task-1"
        self.task.update(status="cancelled", stage="cancelled")
        return dict(self.task)


def test_background_summary_start_activates_polling(monkeypatch):
    assistant = FakeAssistant()
    monkeypatch.setattr(gradio_app, "_require_assistant", lambda token: assistant)

    task_id, output, timer_update = gradio_app.start_summary_pdf_auto(
        "session",
        "summary",
        ["One.md | doc-1", "Two.md | doc-2"],
    )

    assert task_id == "task-1"
    assert "进度: 1/2" in output
    assert timer_update["active"] is True


def test_polling_stops_after_terminal_status(monkeypatch):
    assistant = FakeAssistant()
    assistant.task.update(
        status="completed",
        stage="completed",
        completed=2,
        result="done",
    )
    monkeypatch.setattr(gradio_app, "_require_assistant", lambda token: assistant)

    output, timer_update = gradio_app.poll_summary_pdf_auto(
        "session",
        "task-1",
    )

    assert "任务状态: completed" in output
    assert "done" in output
    assert timer_update["active"] is False


def test_cancel_stops_polling(monkeypatch):
    assistant = FakeAssistant()
    monkeypatch.setattr(gradio_app, "_require_assistant", lambda token: assistant)

    output, timer_update = gradio_app.cancel_summary_pdf_auto(
        "session",
        "task-1",
    )

    assert "任务状态: cancelled" in output
    assert timer_update["active"] is False
