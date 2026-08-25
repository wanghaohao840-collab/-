from types import SimpleNamespace

import pytest

from app.history import HistoryRepository
from assistants.pdf_learning_assistant import PDFLearningAssistant
from hello_agents.memory.base import MemoryConfig
from hello_agents.tools.builtin.memory_tool import MemoryTool


class TrackingLock:
    def __init__(self):
        self.depth = 0

    def __enter__(self):
        self.depth += 1
        return self

    def __exit__(self, *args):
        self.depth -= 1


class FakeRAGTool:
    def __init__(self, document_ids=(), failure=None):
        self.pipeline = SimpleNamespace(document_ids=set(document_ids))
        self.failure = failure
        self.calls = []

    def execute(self, action, **kwargs):
        self.calls.append((action, kwargs))
        if self.failure is not None:
            raise self.failure
        assert action == "delete_document"
        self.pipeline.document_ids.discard(kwargs["document_id"])
        return "ok"


class FakeMemoryTool:
    def __init__(self, lock, import_task_ids=(), failure=None):
        self.lock = lock
        self.import_task_ids = set(import_task_ids)
        self.failure = failure
        self.calls = []

    def remove_import_event(self, import_task_id):
        assert self.lock.depth == 1
        self.calls.append(import_task_id)
        if self.failure is not None:
            raise self.failure
        existed = import_task_id in self.import_task_ids
        self.import_task_ids.discard(import_task_id)
        return existed


def make_assistant(tmp_path, *, rag_failure=None, memory_failure=None):
    assistant = PDFLearningAssistant.__new__(PDFLearningAssistant)
    assistant.user_id = "user-a"
    assistant._lock = TrackingLock()
    assistant.rag_tool = FakeRAGTool(["doc-a", "doc-b"], rag_failure)
    assistant.memory_tool = FakeMemoryTool(
        assistant._lock,
        ["task-a", "task-b"],
        memory_failure,
    )
    assistant.history_repository = HistoryRepository(tmp_path / "history.json")
    assistant.history_repository.save(
        {
            "documents": [
                {"document_id": "doc-a", "import_task_id": "task-a"},
                {"document_id": "doc-b", "import_task_id": "task-b"},
            ],
            "questions": [
                {"question": "a", "document_id": "doc-a"},
                {"question": "b", "document_id": "doc-b"},
                {"question": "compare", "document_ids": ["doc-a", "doc-b"]},
            ],
            "notes": [{"note": "keep", "document_id": "doc-a"}],
            "sessions": [{"session_id": "keep"}],
        }
    )
    assistant.history = assistant.history_repository.load()
    assistant.coordinator = None
    return assistant


def test_compensate_import_removes_only_task_owned_artifacts_and_is_idempotent(
    tmp_path,
):
    assistant = make_assistant(tmp_path)
    original_notes = assistant.history_repository.load()["notes"]

    assistant.compensate_import(document_id="doc-a", import_task_id="task-a")

    history = assistant.history_repository.load()
    assert assistant.rag_tool.pipeline.document_ids == {"doc-b"}
    assert [item["document_id"] for item in history["documents"]] == ["doc-b"]
    assert history["questions"] == [{"question": "b", "document_id": "doc-b"}]
    assert history["notes"] == original_notes
    assert history["sessions"] == [{"session_id": "keep"}]
    assert assistant.memory_tool.import_task_ids == {"task-b"}
    assert assistant.history == history

    assistant.compensate_import(document_id="doc-a", import_task_id="task-a")
    assert assistant.history_repository.load() == history
    assert assistant.memory_tool.import_task_ids == {"task-b"}


def test_compensate_import_rejects_document_owned_by_another_task(tmp_path):
    assistant = make_assistant(tmp_path)
    before = assistant.history_repository.load()

    with pytest.raises(ValueError, match="owned by another import task"):
        assistant.compensate_import(
            document_id="doc-a",
            import_task_id="task-other",
        )

    assert assistant.history_repository.load() == before
    assert assistant.rag_tool.pipeline.document_ids == {"doc-a", "doc-b"}
    assert assistant.memory_tool.calls == []


@pytest.mark.parametrize("layer", ["rag", "history", "memory"])
def test_compensate_import_propagates_cleanup_failures(tmp_path, monkeypatch, layer):
    assistant = make_assistant(
        tmp_path,
        rag_failure=RuntimeError("rag cleanup failed") if layer == "rag" else None,
        memory_failure=(
            RuntimeError("memory cleanup failed") if layer == "memory" else None
        ),
    )
    if layer == "history":
        monkeypatch.setattr(
            assistant.history_repository,
            "delete_document",
            lambda _document_id: (_ for _ in ()).throw(
                RuntimeError("history cleanup failed")
            ),
        )

    with pytest.raises(RuntimeError, match=f"{layer} cleanup failed"):
        assistant.compensate_import(document_id="doc-a", import_task_id="task-a")


def test_remove_import_event_deletes_exact_event_and_saves_snapshot(tmp_path, monkeypatch):
    tool = MemoryTool(
        user_id="user-a",
        memory_config=MemoryConfig(database_path=str(tmp_path / "memory.db")),
        memory_types=["episodic"],
    )
    try:
        task_a_id = tool.ensure_import_event("task-a", "a")
        task_b_id = tool.ensure_import_event("task-b", "b")
        saved = []
        monkeypatch.setattr(tool.memory_manager, "_save_snapshot", lambda: saved.append(True))

        assert tool.remove_import_event("task-a") is True
        assert tool.remove_import_event("task-a") is False

        episodic = tool.memory_manager.memory_types["episodic"]
        assert set(episodic._episodes) == {task_b_id}
        assert episodic.doc_store.get_document(task_a_id) is None
        assert episodic.doc_store.get_document(task_b_id) is not None
        assert saved == [True]
    finally:
        tool.close()


def test_remove_import_event_keeps_memory_state_when_vector_cleanup_fails(
    tmp_path, monkeypatch
):
    tool = MemoryTool(
        user_id="user-a",
        memory_config=MemoryConfig(database_path=str(tmp_path / "memory.db")),
        memory_types=["episodic"],
    )
    try:
        memory_id = tool.ensure_import_event("task-a", "a")
        episodic = tool.memory_manager.memory_types["episodic"]
        before_sessions = {key: list(value) for key, value in episodic.sessions.items()}

        def fail_delete(*_args, **_kwargs):
            raise RuntimeError("vector cleanup failed")

        monkeypatch.setattr(episodic.vector_store, "delete_by_filter", fail_delete)

        with pytest.raises(RuntimeError, match="vector cleanup failed"):
            tool.remove_import_event("task-a")

        assert memory_id in episodic._episodes
        assert episodic.sessions == before_sessions
        assert episodic.doc_store.get_document(memory_id) is not None
    finally:
        tool.close()
