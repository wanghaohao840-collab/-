from pathlib import Path
from types import SimpleNamespace

from app.history import HistoryRepository
from assistants.pdf_learning_assistant import PDFLearningAssistant


class NoteServiceSpy:
    def __init__(self, items=None):
        self.created = []
        self.cleared = []
        self.searches = []
        self.counts = []
        self.recent_calls = []
        self.items = items or (SimpleNamespace(concept="RAG", body_markdown="fts note"),)

    def create_for_user(self, user_id, **kwargs):
        self.created.append((user_id, kwargs))
        return SimpleNamespace(id="note-1", projection_state="pending")

    def clear_for_user(self, user_id):
        self.cleared.append(user_id)
        return 2

    def search_for_user(self, user_id, query, *, limit=20):
        self.searches.append((user_id, query, limit))
        return SimpleNamespace(items=self.items)

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


def assistant(tmp_path: Path, *, with_note_service=True):
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
        note_service=note_service if with_note_service else None,
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


def test_recall_reserves_a_result_for_note_service_fts_hits(tmp_path):
    assistant_instance, notes, _memory, history = assistant(tmp_path)
    history.save({
        "documents": [
            {"document_name": f"RAG document {index}", "document_path": f"doc-{index}.md"}
            for index in range(5)
        ],
        "questions": [{"question": "RAG question", "answer": "RAG answer"}],
        "notes": [],
        "sessions": [],
    })
    notes.items = (SimpleNamespace(concept="RAG", body_markdown="FTS result"),)

    recalled = assistant_instance.recall("RAG", limit=3)

    assert notes.searches == [("alice", "RAG", 3)]
    assert "[历史笔记] 【RAG】FTS result" in recalled
    assert recalled.count("[历史文档]") + recalled.count("[历史问答]") + recalled.count("[历史笔记]") == 3


def test_recall_uses_full_limit_for_note_only_results(tmp_path):
    assistant_instance, notes, _memory, history = assistant(tmp_path)
    history.save({"documents": [], "questions": [], "notes": [], "sessions": []})
    notes.items = tuple(
        SimpleNamespace(concept="NotesOnly", body_markdown=f"note-{index}")
        for index in range(6)
    )

    recalled = assistant_instance.recall("NotesOnly", limit=5)

    assert recalled.count("[历史笔记]") == 5
    assert "note-4" in recalled
    assert "note-5" not in recalled


def test_recall_backfills_sparse_legacy_results_with_notes(tmp_path):
    assistant_instance, notes, _memory, history = assistant(tmp_path)
    history.save({
        "documents": [{"document_name": "Sparse RAG document", "document_path": "doc.md"}],
        "questions": [],
        "notes": [],
        "sessions": [],
    })
    notes.items = tuple(
        SimpleNamespace(concept="RAG", body_markdown=f"note-{index}")
        for index in range(6)
    )

    recalled = assistant_instance.recall("RAG", limit=5)

    assert "[历史文档] Sparse RAG document" in recalled
    assert recalled.count("[历史文档]") + recalled.count("[历史问答]") + recalled.count("[历史笔记]") == 5
    assert recalled.count("[历史笔记]") == 4
    assert "note-3" in recalled
    assert "note-4" not in recalled


def test_missing_note_service_fails_safely_without_legacy_note_writes_or_reads(tmp_path):
    assistant_instance, _notes, memory, history = assistant(tmp_path, with_note_service=False)
    before = history.path.read_bytes()

    assert "笔记服务不可用" in assistant_instance.add_note("new body", concept="RAG")
    assert "笔记服务不可用" in assistant_instance.clear_all_notes()
    recalled = assistant_instance.recall("RAG", limit=5)
    assert "笔记服务不可用" in recalled
    assert "[历史文档]" in recalled
    assert "[历史问答]" in recalled
    assert "[历史笔记]" not in recalled
    assert "学习笔记数: 服务不可用" in assistant_instance.get_stats()
    assert "笔记服务不可用，未读取旧笔记" in assistant_instance.generate_report()

    assert history.path.read_bytes() == before
    assert not [call for call in memory.calls if call[0] in {"add", "clear", "search"}]
