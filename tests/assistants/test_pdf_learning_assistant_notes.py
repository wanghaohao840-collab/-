from pathlib import Path
from types import SimpleNamespace

from app.history import HistoryRepository
from assistants.pdf_learning_assistant import PDFLearningAssistant


class NoteServiceSpy:
    def __init__(self):
        self.created = []
        self.cleared = []
        self.searches = []
        self.counts = []
        self.recent_calls = []

    def create_for_user(self, user_id, **kwargs):
        self.created.append((user_id, kwargs))
        return SimpleNamespace(id="note-1", projection_state="pending")

    def clear_for_user(self, user_id):
        self.cleared.append(user_id)
        return 2

    def search_for_user(self, user_id, query, *, limit=20):
        self.searches.append((user_id, query, limit))
        return SimpleNamespace(items=(SimpleNamespace(concept="RAG", body_markdown="fts note"),))

    def count_for_user(self, user_id):
        self.counts.append(user_id)
        return 2

    def recent_for_user(self, user_id, *, limit=10):
        self.recent_calls.append((user_id, limit))
        return (SimpleNamespace(concept="RAG", body_markdown="recent note"),)


class NoWriteMemory:
    def __init__(self):
        self.calls = []

    def execute(self, action, **kwargs):
        self.calls.append((action, kwargs))
        return "memory result"


def assistant(tmp_path: Path):
    root = tmp_path / "user"
    root.mkdir(parents=True)
    history = HistoryRepository(root / "history.json")
    history.save({"documents": [{"document_name": "RAG doc", "document_path": "doc.md"}], "questions": [{"question": "RAG q", "answer": "a"}], "notes": [{"note": "legacy"}], "sessions": []})
    note_service = NoteServiceSpy()
    memory = NoWriteMemory()
    runtime = SimpleNamespace(
        paths=SimpleNamespace(root=root, reports=root / "reports"),
        lock=__import__("threading").RLock(),
        memory_tool=memory,
        rag_tool=NoWriteMemory(),
        history=history,
        reports=None,
        coordinator=None,
        note_service=note_service,
    )
    return PDFLearningAssistant(user_id="alice", runtime=runtime), note_service, memory, history


def test_supported_add_clear_use_note_service_without_history_or_memory_dual_write(tmp_path):
    assistant_instance, notes, memory, history = assistant(tmp_path)
    before = history.path.read_bytes()

    result = assistant_instance.add_note("new body", concept="RAG")
    assert "保存成功" in result
    assert notes.created[0][0] == "alice"
    assert notes.created[0][1]["body_markdown"] == "new body"
    assert notes.created[0][1]["concept"] == "RAG"
    assert history.path.read_bytes() == before
    assert not [call for call in memory.calls if call[0] == "add"]

    assert "清空" in assistant_instance.clear_all_notes()
    assert notes.cleared == ["alice"]
    assert history.path.read_bytes() == before
    assert not [call for call in memory.calls if call[0] in {"add", "clear"}]


def test_supported_recall_stats_report_use_note_fact_source_and_keep_legacy_docs_questions(tmp_path):
    assistant_instance, notes, _memory, _history = assistant(tmp_path)
    recalled = assistant_instance.recall("RAG", limit=5)
    assert notes.searches == [("alice", "RAG", 5)]
    assert "[历史笔记]" in recalled and "fts note" in recalled
    assert "[历史文档]" in recalled
    assert "[历史问答]" in recalled

    stats = assistant_instance.get_stats()
    assert "学习笔记数: 2" in stats
    assert notes.counts == ["alice"]

    report = assistant_instance.generate_report()
    assert "历史学习笔记数: 2" in report
    assert "recent note" in report
    assert notes.recent_calls == [("alice", 10)]
