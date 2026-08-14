from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.history import HistoryRepository
from assistants.pdf_learning_assistant import PDFLearningAssistant
from hello_agents.memory.rag.contracts import (
    DocumentSegment,
    ImportControlSignal,
    RAGActionResult,
)
from hello_agents.memory.rag.pipeline import SimpleRAGPipeline
from hello_agents.memory.rag.prepare import prepare_document_chunks, report_progress
from hello_agents.memory.rag.qdrant_pipeline import RAGPipeline
from hello_agents.memory.storage.vector_store import InMemoryVectorStore
from hello_agents.tools.builtin.rag_tool import RAGTool


def test_import_control_signal_exposes_non_retryable_action():
    signal = ImportControlSignal("pause")

    assert str(signal) == "pause"
    assert signal.action == "pause"
    assert signal.retryable is False


def test_progress_helper_does_not_swallow_control_signal():
    def checkpoint(*args):
        raise ImportControlSignal("pause")

    with pytest.raises(ImportControlSignal, match="pause"):
        report_progress(checkpoint, "embedding", 1, 1, "embedding")


def test_prepare_checks_after_chunking_and_before_each_embedding():
    events = []

    chunks = prepare_document_chunks(
        document_id="doc-1",
        segments=[DocumentSegment("alpha beta", {})],
        rag_namespace="user-a",
        split_text=lambda text: text.split(),
        embed_text=lambda text: events.append(f"embed:{text}") or [1.0],
        control_checkpoint=lambda stage: events.append(stage),
    )

    assert len(chunks) == 2
    assert events == [
        "chunking",
        "embedding",
        "embed:alpha",
        "embedding",
        "embed:beta",
    ]


def test_json_import_checks_each_persistence_batch(tmp_path):
    observed = []
    pipeline = SimpleRAGPipeline(cache_path=str(tmp_path / "rag.json"))
    pipeline._split_text = lambda text: text.split()
    pipeline._to_vector = lambda text: [1.0] * pipeline.dimension

    result = pipeline.replace_document(
        "doc-1",
        [DocumentSegment("alpha beta", {})],
        control_checkpoint=lambda stage: observed.append(stage),
    )

    assert result["success"] is True
    assert observed == [
        "chunking",
        "embedding",
        "embedding",
        "persisting",
        "persisting",
        "persisting",
    ]


def test_json_add_text_forwards_checkpoint_to_each_import_batch(tmp_path):
    observed = []
    pipeline = SimpleRAGPipeline(cache_path=str(tmp_path / "rag.json"))
    pipeline._split_text = lambda text: text.split()
    pipeline._to_vector = lambda text: [1.0] * pipeline.dimension

    result = pipeline.add_text(
        "alpha beta",
        document_id="doc-1",
        control_checkpoint=lambda stage: observed.append(stage),
    )

    assert result["success"] is True
    assert observed == [
        "persisting",
        "chunking",
        "embedding",
        "embedding",
        "persisting",
        "persisting",
    ]


def test_json_add_text_checks_before_replacing_existing_chunks(tmp_path):
    pipeline = SimpleRAGPipeline(cache_path=str(tmp_path / "rag.json"))
    pipeline._split_text = lambda text: [text]
    pipeline._to_vector = lambda text: [1.0] * pipeline.dimension
    pipeline.add_text("old", document_id="doc-1", replace_existing=False)
    before = pipeline.get_document_chunks("doc-1")

    def checkpoint(stage):
        if stage == "persisting":
            raise ImportControlSignal("pause")

    with pytest.raises(ImportControlSignal, match="pause"):
        pipeline.add_text(
            "new",
            document_id="doc-1",
            replace_existing=True,
            save_cache=False,
            control_checkpoint=checkpoint,
        )

    assert pipeline.get_document_chunks("doc-1") == before


def test_json_replace_checks_before_deletion_only_batch(tmp_path):
    pipeline = SimpleRAGPipeline(cache_path=str(tmp_path / "rag.json"))
    pipeline._split_text = lambda text: [text] if text.strip() else []
    pipeline._to_vector = lambda text: [1.0] * pipeline.dimension
    pipeline.add_text("old", document_id="doc-1", replace_existing=False)
    before = pipeline.get_document_chunks("doc-1")

    def checkpoint(stage):
        if stage == "persisting":
            raise ImportControlSignal("cancel")

    with pytest.raises(ImportControlSignal, match="cancel"):
        pipeline.replace_document(
            "doc-1",
            [DocumentSegment(" ", {})],
            allow_empty=True,
            save_cache=False,
            control_checkpoint=checkpoint,
        )

    assert pipeline.get_document_chunks("doc-1") == before


def test_json_import_does_not_swallow_control_signal(tmp_path):
    pipeline = SimpleRAGPipeline(cache_path=str(tmp_path / "rag.json"))
    pipeline._split_text = lambda text: [text]
    pipeline._to_vector = lambda text: [1.0] * pipeline.dimension

    def checkpoint(stage):
        if stage == "persisting":
            raise ImportControlSignal("cancel")

    with pytest.raises(ImportControlSignal, match="cancel"):
        pipeline.replace_document(
            "doc-1",
            [DocumentSegment("alpha", {})],
            control_checkpoint=checkpoint,
        )


def test_qdrant_import_checks_upsert_and_does_not_swallow_control_signal():
    observed = []
    pipeline = RAGPipeline(
        collection_name="control",
        rag_namespace="user-a",
        vector_store=InMemoryVectorStore(),
    )
    pipeline._split_text = lambda text: text.split()
    pipeline._to_vector = lambda text: [1.0] * pipeline.dimension

    def checkpoint(stage):
        observed.append(stage)
        if stage == "embedding":
            raise ImportControlSignal("pause")

    with pytest.raises(ImportControlSignal, match="pause"):
        pipeline.replace_document(
            "doc-1",
            [DocumentSegment("alpha beta", {})],
            control_checkpoint=checkpoint,
        )

    assert observed == ["chunking", "embedding"]


def test_qdrant_import_checks_before_upsert():
    observed = []
    pipeline = RAGPipeline(
        collection_name="control_upsert",
        rag_namespace="user-a",
        vector_store=InMemoryVectorStore(),
    )
    pipeline._split_text = lambda text: [text]
    pipeline._to_vector = lambda text: [1.0] * pipeline.dimension

    result = pipeline.replace_document(
        "doc-1",
        [DocumentSegment("alpha", {})],
        control_checkpoint=lambda stage: observed.append(stage),
    )

    assert result["success"] is True
    assert observed == ["chunking", "embedding", "persisting"]


def test_qdrant_add_text_forwards_checkpoint_to_upsert_batch():
    observed = []
    pipeline = RAGPipeline(
        collection_name="control_add_text",
        rag_namespace="user-a",
        vector_store=InMemoryVectorStore(),
    )
    pipeline._split_text = lambda text: [text]
    pipeline._to_vector = lambda text: [1.0] * pipeline.dimension

    result = pipeline.add_text(
        "alpha",
        document_id="doc-1",
        replace_existing=False,
        control_checkpoint=lambda stage: observed.append(stage),
    )

    assert result["success"] is True
    assert observed == ["chunking", "embedding", "persisting"]


def test_qdrant_replace_checks_before_orphan_deletion():
    pipeline = RAGPipeline(
        collection_name="control_orphan_delete",
        rag_namespace="user-a",
        vector_store=InMemoryVectorStore(),
    )
    pipeline._split_text = lambda text: text.split()
    pipeline._to_vector = lambda text: [1.0] * pipeline.dimension
    pipeline.replace_document("doc-1", [DocumentSegment("old-0 old-1", {})])
    persisting = 0

    def checkpoint(stage):
        nonlocal persisting
        if stage == "persisting":
            persisting += 1
            if persisting == 2:
                raise ImportControlSignal("pause")

    with pytest.raises(ImportControlSignal, match="pause"):
        pipeline.replace_document(
            "doc-1",
            [DocumentSegment("new", {})],
            control_checkpoint=checkpoint,
        )

    chunks = pipeline.get_document_chunks("doc-1")
    assert [chunk["content"] for chunk in chunks] == ["new", "old-1"]


def test_qdrant_empty_replace_checks_before_orphan_deletion():
    pipeline = RAGPipeline(
        collection_name="control_empty_orphan_delete",
        rag_namespace="user-a",
        vector_store=InMemoryVectorStore(),
    )
    pipeline._split_text = lambda text: text.split()
    pipeline._to_vector = lambda text: [1.0] * pipeline.dimension
    pipeline.replace_document("doc-1", [DocumentSegment("old-0 old-1", {})])
    before = pipeline.get_document_chunks("doc-1")

    def checkpoint(stage):
        if stage == "persisting":
            raise ImportControlSignal("cancel")

    with pytest.raises(ImportControlSignal, match="cancel"):
        pipeline.replace_document(
            "doc-1",
            [DocumentSegment(" ", {})],
            allow_empty=True,
            control_checkpoint=checkpoint,
        )

    assert pipeline.get_document_chunks("doc-1") == before


class _RecordingPipeline:
    def __init__(self):
        self.control_checkpoint = None

    def replace_document(
        self,
        document_id,
        segments,
        save_cache=True,
        *,
        control_checkpoint=None,
    ):
        self.control_checkpoint = control_checkpoint
        return {
            "success": True,
            "document_id": document_id,
            "chunks_added": len(segments),
            "chunks_removed": 0,
        }


def test_rag_tool_checks_parsing_boundaries_and_forwards_checkpoint(
    tmp_path, monkeypatch
):
    path = tmp_path / "document.md"
    path.write_text("body", encoding="utf-8")
    observed = []
    pipeline = _RecordingPipeline()
    tool = RAGTool()
    monkeypatch.setattr(tool, "_get_pipeline", lambda namespace=None: pipeline)
    monkeypatch.setattr(
        tool,
        "_build_graph_after_import",
        lambda *args, **kwargs: {"status": "disabled"},
    )
    checkpoint = lambda stage: observed.append(stage)

    result = tool.execute(
        "add_document",
        file_path=str(path),
        document_id="doc-1",
        control_checkpoint=checkpoint,
    )

    assert "document_id" in result
    assert observed == ["parsing", "parsing"]
    assert pipeline.control_checkpoint is checkpoint


def test_rag_tool_broad_handlers_do_not_wrap_control_signal(tmp_path):
    path = tmp_path / "document.md"
    path.write_text("body", encoding="utf-8")

    def checkpoint(stage):
        raise ImportControlSignal("pause")

    with pytest.raises(ImportControlSignal, match="pause"):
        RAGTool().execute_result(
            "add_document",
            file_path=str(path),
            control_checkpoint=checkpoint,
        )


class _AssistantRAGTool:
    def __init__(self, document_ids=()):
        self.calls = []
        self.pipeline = SimpleNamespace(
            list_document_ids=lambda: list(document_ids)
        )

    def _get_pipeline(self):
        return self.pipeline

    def execute_result(self, action, **kwargs):
        self.calls.append((action, kwargs))
        return RAGActionResult(
            action=action,
            success=True,
            message="loaded",
            data={"document_id": kwargs["document_id"]},
        )


class _AssistantMemoryTool:
    def __init__(self):
        self.calls = []

    def execute(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "ok"

    def ensure_import_event(self, **kwargs):
        self.calls.append(("ensure_import_event", kwargs))
        return "ok"


def test_assistant_forwards_checkpoint_and_checks_before_history_commit(tmp_path):
    source = tmp_path / "document.md"
    source.write_text("body", encoding="utf-8")
    assistant = PDFLearningAssistant.__new__(PDFLearningAssistant)
    assistant.user_id = "user-a"
    assistant.session_id = "session-a"
    assistant.rag_tool = _AssistantRAGTool()
    assistant.memory_tool = _AssistantMemoryTool()
    assistant.history_repository = HistoryRepository(tmp_path / "history.json")
    assistant.history_repository.save(
        {"documents": [], "questions": [], "notes": [], "sessions": []}
    )
    assistant.coordinator = None
    assistant.history = assistant.history_repository.load()
    assistant.current_document = None
    assistant.current_document_id = None
    assistant.stats = {"documents_loaded": 0}
    observed = []

    def checkpoint(stage):
        observed.append(stage)
        if stage == "committing":
            raise ImportControlSignal("cancel")

    with pytest.raises(ImportControlSignal, match="cancel"):
        assistant.load_document(
            str(source),
            document_id="doc-1",
            control_checkpoint=checkpoint,
        )

    _, add_kwargs = assistant.rag_tool.calls[0]
    assert add_kwargs["control_checkpoint"] is checkpoint
    assert observed == ["committing"]
    assert assistant.history_repository.load()["documents"] == []
    assert assistant.memory_tool.calls == []


def test_assistant_idempotent_retry_checks_before_memory_repair(tmp_path):
    source = tmp_path / "document.md"
    source.write_text("body", encoding="utf-8")
    existing = {
        "document_id": "doc-1",
        "document_name": "document.md",
        "document_path": str(source),
        "import_task_id": "task-1",
        "loaded_at": "original",
    }
    assistant = PDFLearningAssistant.__new__(PDFLearningAssistant)
    assistant.user_id = "user-a"
    assistant.session_id = "session-a"
    assistant.rag_tool = _AssistantRAGTool(["doc-1"])
    assistant.memory_tool = _AssistantMemoryTool()
    assistant.history_repository = HistoryRepository(tmp_path / "history.json")
    assistant.history_repository.save(
        {"documents": [existing], "questions": [], "notes": [], "sessions": []}
    )
    assistant.coordinator = None
    assistant.history = assistant.history_repository.load()
    assistant.current_document = None
    assistant.current_document_id = None
    assistant.stats = {"documents_loaded": 0}
    observed = []

    def checkpoint(stage):
        observed.append(stage)
        if stage == "committing":
            raise ImportControlSignal("pause")

    with pytest.raises(ImportControlSignal, match="pause"):
        assistant.load_document(
            str(source),
            document_id="doc-1",
            import_task_id="task-1",
            control_checkpoint=checkpoint,
        )

    assert assistant.rag_tool.calls == []
    assert assistant.history_repository.load()["documents"] == [existing]
    assert assistant.memory_tool.calls == []
    assert observed == ["committing"]
