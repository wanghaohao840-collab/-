from pathlib import Path
from types import SimpleNamespace
import sqlite3
import uuid

import pytest

import app.import_worker as import_worker
from app.auth import AuthService
from app.database import initialize_database
from app.history import HistoryRepository
from app.import_models import ImportTaskCreate
from app.import_repository import ImportTaskRepository
from app.storage import UserStorage
from assistants.pdf_learning_assistant import (
    ImportMemoryEventError,
    ImportRAGError,
    PDFLearningAssistant,
)
from hello_agents.memory.base import MemoryConfig
from hello_agents.memory.manager import MemoryManager
from hello_agents.memory.rag.prepare import PROJECT_POINT_NAMESPACE_UUID
from hello_agents.tools.builtin.memory_tool import MemoryTool
from hello_agents.tools.builtin.rag_tool import RAGTool


class TrackingLock:
    def __init__(self):
        self.depth = 0

    def __enter__(self):
        self.depth += 1
        return self

    def __exit__(self, *args):
        self.depth -= 1


class FakePipeline:
    def __init__(self, document_ids=()):
        self.document_ids = set(document_ids)

    def list_document_ids(self):
        return sorted(self.document_ids)


class FakeRAGTool:
    def __init__(self, document_ids=(), result=None):
        self.pipeline = FakePipeline(document_ids)
        self.calls = []
        self.result = result

    def _get_pipeline(self):
        return self.pipeline

    def execute_result(self, action, **kwargs):
        self.calls.append((action, kwargs))
        result = self.result or SimpleNamespace(
            success=True,
            message="loaded",
            data={"document_id": kwargs["document_id"]},
            error="",
            error_code="",
            retryable=False,
        )
        if result.success:
            self.pipeline.document_ids.add(kwargs["document_id"])
        return result

    def execute(self, action, **kwargs):
        self.calls.append((action, kwargs))
        if action == "delete_document":
            self.pipeline.document_ids.discard(kwargs["document_id"])
        return "ok"


class FakeMemoryTool:
    def __init__(self, lock, failure=None):
        self.lock = lock
        self.failure = failure
        self.calls = []

    def ensure_import_event(self, **kwargs):
        assert self.lock.depth == 1
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        return "import-event"

    def execute(self, *args, **kwargs):
        assert self.lock.depth == 1
        self.calls.append((args, kwargs))
        if self.failure is not None:
            raise self.failure
        return "memory-ok"


def make_assistant(tmp_path, *, history_documents=(), rag_document_ids=(), memory_failure=None):
    assistant = PDFLearningAssistant.__new__(PDFLearningAssistant)
    assistant.user_id = "user-a"
    assistant.session_id = "session-a"
    assistant._lock = TrackingLock()
    assistant.rag_tool = FakeRAGTool(rag_document_ids)
    assistant.memory_tool = FakeMemoryTool(assistant._lock, memory_failure)
    assistant.history_repository = HistoryRepository(tmp_path / "history.json")
    assistant.history_repository.save(
        {
            "documents": list(history_documents),
            "questions": [{"question": "keep"}],
            "notes": [{"note": "keep"}],
            "sessions": [{"session_id": "keep"}],
        }
    )
    assistant.coordinator = None
    assistant.history = assistant.history_repository.load()
    assistant.current_document = "/previous.md"
    assistant.current_document_id = "previous"
    assistant.stats = {
        "session_start": "now",
        "documents_loaded": 2,
        "questions_asked": 0,
        "notes_added": 0,
    }
    source = tmp_path / "a.md"
    source.write_text("alpha", encoding="utf-8")
    return assistant, source


def test_history_upsert_does_not_duplicate_document(tmp_path):
    repo = HistoryRepository(tmp_path / "history.json")
    repo.save(
        {
            "documents": [],
            "questions": [{"question": "keep"}],
            "notes": [{"note": "keep"}],
            "sessions": [{"session_id": "keep"}],
        }
    )
    item = {
        "document_id": "doc-1",
        "import_task_id": "task-1",
        "document_name": "a.md",
    }

    repo.upsert_document(item)
    repo.upsert_document({**item, "document_name": "a-renamed.md"})

    assert repo.load()["documents"] == [
        {
            "document_id": "doc-1",
            "import_task_id": "task-1",
            "document_name": "a-renamed.md",
        }
    ]
    assert repo.load()["questions"] == [{"question": "keep"}]
    assert repo.load()["notes"] == [{"note": "keep"}]
    assert repo.load()["sessions"] == [{"session_id": "keep"}]


def test_retry_reuses_one_import_memory_event(tmp_path):
    manager = MemoryManager(
        config=MemoryConfig(database_path=str(tmp_path / "memory.db")),
        user_id="user-a",
        enable_working=False,
        enable_episodic=True,
        enable_semantic=False,
    )

    first = manager.add_memory(
        content="用户导入了文档：a.md",
        memory_type="episodic",
        metadata={
            "user_id": "user-a",
            "import_task_id": "task-1",
            "session_id": "first",
        },
        memory_id="import-task-1",
    )
    second = manager.add_memory(
        content="用户导入了文档：a.md",
        memory_type="episodic",
        metadata={
            "user_id": "user-a",
            "import_task_id": "task-1",
            "session_id": "second",
        },
        memory_id="import-task-1",
    )

    assert first == second == "import-task-1"
    assert len(manager.memory_types["episodic"]._episodes) == 1
    assert manager.memory_types["episodic"].sessions["first"] == []
    assert manager.memory_types["episodic"].sessions["second"] == ["import-task-1"]
    manager.close()


def test_ensure_import_event_uses_stable_uuid_and_deduplicates(tmp_path):
    tool = MemoryTool(
        user_id="user-a",
        memory_config=MemoryConfig(database_path=str(tmp_path / "memory.db")),
        memory_types=["episodic"],
    )
    try:
        first = tool.ensure_import_event(
            import_task_id="task-1",
            content="用户导入了文档：a.md",
            metadata={"document_id": "doc-1"},
            session_id="session-1",
        )
        second = tool.ensure_import_event(
            import_task_id="task-1",
            content="different retry content",
            metadata={"document_id": "doc-1"},
            session_id="session-2",
        )

        expected = "import-" + str(
            uuid.uuid5(PROJECT_POINT_NAMESPACE_UUID, "user-a:task-1")
        )
        episodic = tool.memory_manager.memory_types["episodic"]
        assert first == second == expected
        assert list(episodic._episodes) == [expected]
        assert episodic._episodes[expected].context["import_task_id"] == "task-1"
    finally:
        tool.close()


def test_duplicate_import_skips_rag_and_history_writes(tmp_path, monkeypatch):
    existing = {
        "document_id": "doc-1",
        "document_name": "a.md",
        "document_path": "old-path",
        "import_task_id": "task-1",
        "loaded_at": "original",
    }
    assistant, source = make_assistant(
        tmp_path,
        history_documents=[existing],
        rag_document_ids=["doc-1"],
    )

    def fail_if_upserted(_item):
        raise AssertionError("already imported retry must not rewrite History")

    monkeypatch.setattr(assistant.history_repository, "upsert_document", fail_if_upserted)

    result = assistant.load_document(
        str(source),
        document_id="doc-1",
        original_name="a.md",
        import_task_id="task-1",
    )

    assert result == "✅ 文档已导入\n- document_id: doc-1"
    assert assistant.rag_tool.calls == []
    assert assistant.history_repository.load()["documents"] == [existing]
    assert len(assistant.memory_tool.calls) == 1


@pytest.mark.parametrize(
    ("history_documents", "rag_document_ids"),
    [
        (
            [
                {
                    "document_id": "doc-1",
                    "document_name": "a.md",
                    "import_task_id": "task-1",
                }
            ],
            [],
        ),
        ([], ["doc-1"]),
    ],
)
def test_one_sided_history_rag_state_is_repaired(
    tmp_path, history_documents, rag_document_ids
):
    assistant, source = make_assistant(
        tmp_path,
        history_documents=history_documents,
        rag_document_ids=rag_document_ids,
    )

    assistant.load_document(
        str(source),
        document_id="doc-1",
        original_name="a.md",
        import_task_id="task-1",
    )

    add_calls = [
        call for call in assistant.rag_tool.calls if call[0] == "add_document"
    ]
    assert len(add_calls) == 1
    assert assistant.rag_tool.pipeline.document_ids == {"doc-1"}
    documents = assistant.history_repository.load()["documents"]
    assert len(documents) == 1
    assert documents[0]["document_id"] == "doc-1"
    assert documents[0]["import_task_id"] == "task-1"
    assert len(assistant.memory_tool.calls) == 1


def test_import_forwards_progress_callback_to_rag(tmp_path):
    assistant, source = make_assistant(tmp_path)
    callback = object()

    assistant.load_document(
        str(source),
        document_id="doc-1",
        import_task_id="task-1",
        progress_callback=callback,
    )

    _, kwargs = assistant.rag_tool.calls[0]
    assert kwargs["progress_callback"] is callback


def test_runner_cancellation_signal_crosses_real_assistant_rag_forwarding(tmp_path):
    assistant, source = make_assistant(tmp_path)
    assistant.rag_tool = RAGTool(
        cache_path=str(tmp_path / "rag.json"),
        rag_namespace="user-a",
        enable_graph=False,
    )
    pipeline = assistant.rag_tool._get_pipeline()
    pipeline._split_text = lambda text: [text]
    pipeline._to_vector = lambda text: [1.0] * pipeline.dimension
    updates = []

    def cancel_at_commit(stage, done, total, message):
        updates.append((stage, done, total, message))
        if stage == "committing":
            raise import_worker.ImportCancelled()

    try:
        with pytest.raises(import_worker.ImportCancelled):
            assistant.load_document(
                str(source),
                document_id="doc-cancel",
                import_task_id="task-cancel",
                progress_callback=cancel_at_commit,
            )
    finally:
        assistant.rag_tool.close()

    assert [update[0] for update in updates] == [
        "parsing",
        "chunking",
        "chunking",
        "embedding",
        "committing",
    ]
    assert pipeline.get_document_chunks("doc-cancel") == []
    assert assistant.history_repository.load()["documents"] == []
    assert assistant.memory_tool.calls == []


def test_runner_commit_gate_database_failure_aborts_real_rag_before_any_mutation(
    tmp_path, monkeypatch
):
    assert issubclass(import_worker.ImportCommitGateFailure, BaseException)
    assert not issubclass(import_worker.ImportCommitGateFailure, Exception)
    assert import_worker.ImportCommitGateFailure is not import_worker.ImportCancelled
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    user_id = AuthService(db_path).register(
        "gate-user", "correct horse battery"
    ).id
    repository = ImportTaskRepository(db_path)
    storage = UserStorage(tmp_path / "data")
    batch_id, task_id, document_id = (str(uuid.uuid4()) for _ in range(3))
    staged = storage.staged_import_path(user_id, batch_id, task_id, ".md")
    staged.write_text("alpha", encoding="utf-8")
    repository.create_batch(
        user_id,
        [
            ImportTaskCreate(
                task_id=task_id,
                batch_id=batch_id,
                user_id=user_id,
                document_id=document_id,
                original_name="alpha.md",
                file_suffix=".md",
                size_bytes=5,
                staged_relative_path=str(
                    staged.relative_to(storage.user_paths(user_id).root)
                ),
            )
        ],
    )
    task = repository.claim_next(set())
    assert task is not None

    assistant, _ = make_assistant(tmp_path / "assistant")
    assistant.user_id = user_id
    assistant.runtime = SimpleNamespace()
    assistant.rag_tool = RAGTool(
        cache_path=str(tmp_path / "rag.json"),
        rag_namespace=user_id,
        enable_graph=False,
    )
    pipeline = assistant.rag_tool._get_pipeline()
    pipeline._split_text = lambda text: [text]
    pipeline._to_vector = lambda text: [1.0] * pipeline.dimension

    class RuntimeRegistry:
        def acquire_background(self, selected_user_id):
            assert selected_user_id == user_id
            return SimpleNamespace(paths=storage.ensure_user_dirs(user_id))

        def release_background(self, selected_user_id):
            assert selected_user_id == user_id

    runner = import_worker.ImportTaskRunner(
        repository,
        RuntimeRegistry(),
        storage,
        assistant_factory=lambda **_kwargs: assistant,
    )

    def fail_gate(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repository, "try_begin_committing", fail_gate)

    try:
        runner.run(task)
    finally:
        assistant.rag_tool.close()

    failed = repository.get_task(user_id, task_id)
    assert failed.status == "retry_wait"
    assert failed.error_code == "database_busy"
    assert failed.error_summary == "Import database is temporarily busy"
    assert "CommitGate" not in failed.error_summary
    assert pipeline.get_document_chunks(document_id) == []
    assert assistant.history_repository.load()["documents"] == []
    assert assistant.memory_tool.calls == []
    assert not storage.document_path(user_id, document_id, ".md").exists()


def test_memory_failure_is_structured_and_leaves_durable_import_for_retry(tmp_path):
    assistant, source = make_assistant(
        tmp_path,
        memory_failure=TimeoutError("memory backend unavailable"),
    )

    with pytest.raises(ImportMemoryEventError) as captured:
        assistant.load_document(
            str(source),
            document_id="doc-1",
            import_task_id="task-1",
        )

    assert captured.value.error_code == "memory_import_event"
    assert captured.value.retryable is True
    assert isinstance(captured.value.__cause__, TimeoutError)
    assert assistant.rag_tool.pipeline.document_ids == {"doc-1"}
    assert assistant.history_repository.load()["documents"][0]["document_id"] == "doc-1"
    assert assistant.current_document_id == "previous"
    assert assistant.current_document == "/previous.md"
    assert assistant.stats["documents_loaded"] == 2

    assistant.memory_tool.failure = None
    assistant.rag_tool.calls.clear()
    assistant.load_document(
        str(source),
        document_id="doc-1",
        import_task_id="task-1",
    )
    assert assistant.rag_tool.calls == []
    assert len(assistant.history_repository.load()["documents"]) == 1


def test_already_imported_retry_never_deletes_preexisting_rag_document(
    tmp_path, monkeypatch
):
    assistant, source = make_assistant(
        tmp_path,
        history_documents=[
            {
                "document_id": "doc-1",
                "document_name": "a.md",
                "import_task_id": "task-1",
            }
        ],
        rag_document_ids=["doc-1"],
    )

    monkeypatch.setattr(
        assistant.history_repository,
        "upsert_document",
        lambda _item: (_ for _ in ()).throw(RuntimeError("history unavailable")),
    )

    assistant.load_document(
        str(source),
        document_id="doc-1",
        import_task_id="task-1",
    )

    assert assistant.rag_tool.pipeline.document_ids == {"doc-1"}
    assert not any(call[0] == "delete_document" for call in assistant.rag_tool.calls)


def test_split_brain_history_failure_preserves_preexisting_rag_document(
    tmp_path, monkeypatch
):
    assistant, source = make_assistant(
        tmp_path,
        history_documents=[],
        rag_document_ids=["doc-1"],
    )
    monkeypatch.setattr(
        assistant.history_repository,
        "upsert_document",
        lambda _item: (_ for _ in ()).throw(RuntimeError("history unavailable")),
    )

    with pytest.raises(RuntimeError, match="history unavailable"):
        assistant.load_document(
            str(source),
            document_id="doc-1",
            import_task_id="task-1",
        )

    assert assistant.rag_tool.pipeline.document_ids == {"doc-1"}
    assert [call[0] for call in assistant.rag_tool.calls] == ["add_document"]
    assert assistant.memory_tool.calls == []


def test_history_failure_compensates_only_rag_written_by_this_call(
    tmp_path, monkeypatch
):
    assistant, source = make_assistant(tmp_path)
    monkeypatch.setattr(
        assistant.history_repository,
        "upsert_document",
        lambda _item: (_ for _ in ()).throw(RuntimeError("history unavailable")),
    )

    with pytest.raises(RuntimeError, match="history unavailable"):
        assistant.load_document(
            str(source),
            document_id="doc-1",
            import_task_id="task-1",
        )

    assert assistant.rag_tool.pipeline.document_ids == set()
    assert [call[0] for call in assistant.rag_tool.calls] == [
        "add_document",
        "delete_document",
    ]
    assert assistant.memory_tool.calls == []


def test_structured_rag_failure_raises_with_task3_metadata(tmp_path):
    assistant, source = make_assistant(tmp_path)
    assistant.rag_tool.result = SimpleNamespace(
        success=False,
        message="RAG unavailable",
        data={"document_id": "doc-1"},
        error="connection failed",
        error_code="rag_connection",
        retryable=True,
    )

    with pytest.raises(ImportRAGError) as captured:
        assistant.load_document(
            str(source),
            document_id="doc-1",
            import_task_id="task-1",
        )

    assert captured.value.error_code == "rag_connection"
    assert captured.value.retryable is True
    assert assistant.history_repository.load()["documents"] == []
    assert assistant.memory_tool.calls == []
