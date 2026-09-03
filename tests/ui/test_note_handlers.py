from types import SimpleNamespace

import pytest

import ui.gradio_app as gradio_app


class NoteAssistant:
    def __init__(self):
        self.calls = []

    def add_note(self, **kwargs):
        self.calls.append(("add", kwargs))
        return "✅ 保存成功"

    def clear_all_notes(self):
        self.calls.append(("clear", {}))
        return "✅ 已清空全部学习笔记"

    def recall(self, query, limit=5):
        self.calls.append(("recall", {"query": query, "limit": limit}))
        return "recall"

    def get_stats(self, turns):
        self.calls.append(("stats", {"turns": turns}))
        return "stats"

    def generate_report(self, turns):
        self.calls.append(("report", {"turns": turns}))
        return "report"


class Registry:
    def __init__(self, assistant):
        self.assistant = assistant

    def get_assistant(self, token):
        return self.assistant


def test_note_handlers_keep_names_and_arity_and_delegate_authenticated_assistant(monkeypatch):
    assistant = NoteAssistant()
    monkeypatch.setattr(gradio_app, "session_registry", Registry(assistant))
    monkeypatch.setattr(gradio_app, "services", SimpleNamespace(qa_service=SimpleNamespace(report_turns=lambda _: ["turn"])))

    assert gradio_app.add_note("token", "body", "concept") == "✅ 保存成功"
    assert gradio_app.clear_all_notes("token").startswith("✅")
    assert gradio_app.recall_memory("token", "query") == "recall"
    assert gradio_app.show_stats("token") == "stats"
    assert gradio_app.generate_report("token") == "report"
    assert [name for name, _ in assistant.calls] == ["add", "clear", "recall", "stats", "report"]
