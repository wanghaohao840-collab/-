import json
import re
import sys
from types import SimpleNamespace

import pytest

from hello_agents.memory.rag.contracts import DocumentSegment, RAGActionResult
from hello_agents.memory.rag.errors import (
    RAGAuthenticationError,
    RAGCollectionError,
    RAGConfigError,
    RAGConnectionError,
    RAGDocumentTooLargeError,
    RAGEmbeddingError,
    RAGOperationError,
)
from hello_agents.memory.rag.pipeline import SimpleRAGPipeline
from hello_agents.memory.rag.prepare import prepare_document_chunks
from hello_agents.memory.rag.qdrant_pipeline import RAGPipeline
from hello_agents.memory.storage.vector_store import (
    InMemoryVectorStore,
    QdrantVectorStore,
)
from hello_agents.tools.builtin.rag_tool import RAGTool


def test_prepare_reports_monotonic_embedding_progress():
    updates = []

    chunks = prepare_document_chunks(
        document_id="doc-1",
        segments=[DocumentSegment("alpha beta", {"page_number": 1})],
        rag_namespace="user-a",
        split_text=lambda text: ["alpha", "beta"],
        embed_text=lambda text: [1.0, 0.0],
        progress_callback=lambda stage, done, total, message: updates.append(
            (stage, done, total, message)
        ),
    )

    assert len(chunks) == 2
    assert updates == [
        ("embedding", 1, 2, "embedding"),
        ("embedding", 2, 2, "embedding"),
    ]


def test_progress_callback_failure_does_not_abort_preparation():
    def fail(*args):
        raise RuntimeError("UI disconnected")

    chunks = prepare_document_chunks(
        document_id="doc-1",
        segments=[DocumentSegment("alpha", {})],
        rag_namespace="user-a",
        split_text=lambda text: [text],
        embed_text=lambda text: [1.0],
        progress_callback=fail,
    )

    assert len(chunks) == 1


def test_json_add_text_reports_complete_stage_order(tmp_path):
    updates = []
    pipeline = SimpleRAGPipeline(cache_path=str(tmp_path / "rag.json"))
    pipeline._split_text = lambda text: text.split()
    pipeline._to_vector = lambda text: [1.0] * pipeline.dimension

    result = pipeline.add_text(
        "alpha beta",
        document_id="doc-1",
        progress_callback=lambda *update: updates.append(update),
    )

    assert result["success"] is True
    assert updates == [
        ("chunking", 0, 1, "chunking"),
        ("chunking", 1, 1, "chunking"),
        ("embedding", 1, 2, "embedding"),
        ("embedding", 2, 2, "embedding"),
        ("persisting", 1, 2, "persisting"),
        ("persisting", 2, 2, "persisting"),
    ]
    assert pipeline.cache_path.exists()


def test_json_replace_document_reports_vector_and_cache_persistence(tmp_path):
    updates = []
    pipeline = SimpleRAGPipeline(cache_path=str(tmp_path / "rag.json"))
    pipeline._split_text = lambda text: text.split()
    pipeline._to_vector = lambda text: [1.0] * pipeline.dimension

    result = pipeline.replace_document(
        "doc-1",
        [
            DocumentSegment("alpha beta", {"page_number": 1}),
            DocumentSegment("gamma", {"page_number": 2}),
        ],
        progress_callback=lambda *update: updates.append(update),
    )

    assert result["success"] is True
    assert updates == [
        ("chunking", 0, 2, "chunking"),
        ("chunking", 2, 2, "chunking"),
        ("embedding", 1, 3, "embedding"),
        ("embedding", 2, 3, "embedding"),
        ("embedding", 3, 3, "embedding"),
        ("persisting", 1, 2, "persisting"),
        ("persisting", 2, 2, "persisting"),
    ]


def test_json_pipeline_ignores_callback_failures_at_every_stage(tmp_path):
    pipeline = SimpleRAGPipeline(cache_path=str(tmp_path / "rag.json"))
    pipeline._split_text = lambda text: [text]
    pipeline._to_vector = lambda text: [1.0] * pipeline.dimension

    def fail(*args):
        raise RuntimeError("UI disconnected")

    result = pipeline.replace_document(
        "doc-1",
        [DocumentSegment("alpha", {})],
        progress_callback=fail,
    )

    assert result["success"] is True
    assert pipeline.cache_path.exists()


def test_qdrant_pipeline_forwards_progress_through_preparation_and_upsert():
    updates = []
    pipeline = RAGPipeline(
        collection_name="progress",
        rag_namespace="user-a",
        vector_store=InMemoryVectorStore(),
    )
    pipeline._split_text = lambda text: text.split()
    pipeline._to_vector = lambda text: [1.0] * pipeline.dimension

    result = pipeline.replace_document(
        "doc-1",
        [DocumentSegment("alpha beta", {})],
        progress_callback=lambda *update: updates.append(update),
    )

    assert result["success"] is True
    assert updates == [
        ("chunking", 0, 1, "chunking"),
        ("chunking", 1, 1, "chunking"),
        ("embedding", 1, 2, "embedding"),
        ("embedding", 2, 2, "embedding"),
        ("persisting", 1, 1, "persisting"),
    ]


def test_structured_authentication_error_is_not_retryable(monkeypatch):
    tool = RAGTool()

    def fail(**kwargs):
        raise RAGAuthenticationError("api_key=secret")

    monkeypatch.setattr(tool, "_add_document", fail)
    result = tool.execute_result("add_document", file_path="ignored.md")

    assert result.success is False
    assert result.error_code == "rag_authentication"
    assert result.retryable is False
    assert "secret" not in result.error
    assert result.data["error_code"] == "rag_authentication"


@pytest.mark.parametrize(
    ("error", "error_code", "retryable"),
    [
        (RAGConnectionError("offline"), "rag_connection", True),
        (RAGAuthenticationError("denied"), "rag_authentication", False),
        (RAGConfigError("invalid"), "rag_config", False),
        (RAGCollectionError("missing"), "rag_collection", False),
        (RAGDocumentTooLargeError("large"), "rag_document_too_large", False),
        (RAGEmbeddingError("bad vector"), "rag_embedding", False),
        (RAGOperationError("failed"), "rag_operation", False),
        (RAGOperationError("HTTP 503 temporary outage"), "rag_operation", True),
        (ValueError("bad document"), "document_invalid", False),
        (FileNotFoundError("missing document"), "document_invalid", False),
        (RuntimeError("boom"), "unexpected_error", False),
    ],
)
def test_execute_result_maps_backend_failures(
    monkeypatch, error, error_code, retryable
):
    tool = RAGTool()

    def fail(**kwargs):
        raise error

    monkeypatch.setattr(tool, "_add_document", fail)

    result = tool.execute_result("add_document", file_path="document.md")

    assert result.error_code == error_code
    assert result.retryable is retryable
    assert result.data["error_code"] == error_code
    assert result.data["retryable"] is retryable


def test_structured_error_omits_paths_credentials_urls_and_tracebacks(
    tmp_path, monkeypatch
):
    credential = "top-secret-token"
    file_path = tmp_path / "private" / "document.md"
    tool = RAGTool(
        qdrant_api_key=credential,
        qdrant_url=f"https://user:password@example.com?api_key={credential}",
    )

    def fail(**kwargs):
        tool._last_action_data = {
            "file_path": str(file_path),
            "traceback": "full stack",
            "nested": {
                "message": (
                    f"failed {file_path} at "
                    f"https://user:password@example.com?api_key={credential}"
                )
            },
        }
        raise RAGConnectionError(
            f"failed {file_path} at "
            f"https://user:password@example.com?api_key={credential}"
        )

    monkeypatch.setattr(tool, "_add_document", fail)

    result = tool.execute_result("add_document", file_path=str(file_path))
    rendered_data = json.dumps(result.data)

    assert str(file_path) not in result.error
    assert credential not in result.error
    assert "password" not in result.error
    assert str(file_path) not in result.data["nested"]["message"]
    assert credential not in result.data["nested"]["message"]
    assert "password" not in result.data["nested"]["message"]
    assert str(file_path) not in rendered_data
    assert credential not in rendered_data
    assert "password" not in rendered_data
    assert "traceback" not in result.data


def test_safe_action_data_recursively_redacts_known_path_and_credentials(
    tmp_path
):
    credential = "nested-secret"
    file_path = tmp_path / "private" / "document.md"
    tool = RAGTool(qdrant_api_key=credential)

    sanitized = tool._safe_action_data(
        {
            "nested": [
                {
                    "message": (
                        f"failed {file_path}; api_key={credential}; "
                        f"https://user:password@example.com?token={credential}"
                    )
                }
            ]
        },
        file_path=file_path,
    )
    message = sanitized["nested"][0]["message"]

    assert str(file_path) not in message
    assert credential not in message
    assert "password" not in message


@pytest.mark.parametrize(
    ("status_code", "message"),
    [
        (503, "Service unavailable"),
        (429, "Too Many Requests"),
    ],
)
def test_wrapped_qdrant_operation_errors_remain_retryable(
    monkeypatch, status_code, message
):
    store = QdrantVectorStore(client=object(), retry_delays=())

    def fail():
        error = RuntimeError(message)
        error.status_code = status_code
        raise error

    with pytest.raises(RAGOperationError) as captured:
        store._call("upsert", fail)

    tool = RAGTool()

    def raise_wrapped(**kwargs):
        raise captured.value

    monkeypatch.setattr(tool, "_add_document", raise_wrapped)
    result = tool.execute_result("add_document", file_path="document.md")

    assert re.search(r"\b[45]\d{2}\b", str(captured.value)) is None
    assert result.error_code == "rag_operation"
    assert result.retryable is True


@pytest.mark.parametrize(
    ("pipeline_factory", "expected_updates"),
    [
        (
            lambda tmp_path: SimpleRAGPipeline(
                cache_path=str(tmp_path / "empty.json")
            ),
            [
                ("chunking", 0, 1, "chunking"),
                ("chunking", 1, 1, "chunking"),
                ("persisting", 1, 1, "persisting"),
            ],
        ),
        (
            lambda tmp_path: RAGPipeline(
                collection_name="empty_progress",
                rag_namespace="user-a",
                vector_store=InMemoryVectorStore(),
            ),
            [
                ("chunking", 0, 1, "chunking"),
                ("chunking", 1, 1, "chunking"),
            ],
        ),
    ],
)
def test_empty_replace_completes_chunking_without_embedding(
    tmp_path, pipeline_factory, expected_updates
):
    updates = []
    pipeline = pipeline_factory(tmp_path)

    result = pipeline.replace_document(
        "doc-empty",
        [DocumentSegment(" ", {})],
        allow_empty=True,
        progress_callback=lambda *update: updates.append(update),
    )

    assert result["success"] is True
    assert updates == expected_updates


class _RecordingPipeline:
    def __init__(self):
        self.progress_callback = None
        self.segments = []

    def replace_document(
        self,
        document_id,
        segments,
        save_cache=True,
        progress_callback=None,
    ):
        self.progress_callback = progress_callback
        self.segments = segments
        return {
            "success": True,
            "document_id": document_id,
            "chunks_added": len(segments),
            "chunks_removed": 0,
        }


@pytest.mark.parametrize("suffix", [".txt", ".md"])
def test_text_document_reports_parsing_and_forwards_callback(
    tmp_path, monkeypatch, suffix
):
    path = tmp_path / f"document{suffix}"
    path.write_text("body", encoding="utf-8")
    updates = []
    callback = lambda *update: updates.append(update)
    pipeline = _RecordingPipeline()
    tool = RAGTool()
    monkeypatch.setattr(tool, "_get_pipeline", lambda namespace=None: pipeline)
    monkeypatch.setattr(
        tool,
        "_build_graph_after_import",
        lambda *args, **kwargs: {"status": "disabled"},
    )

    result = tool.execute(
        "add_document",
        file_path=str(path),
        document_id="doc-1",
        progress_callback=callback,
    )

    assert "document_id" in result
    assert updates == [("parsing", 1, 1, "parsing")]
    assert pipeline.progress_callback is callback


def test_docx_document_reports_parsing_and_forwards_callback(
    tmp_path, monkeypatch
):
    from docx import Document

    path = tmp_path / "document.docx"
    document = Document()
    document.add_paragraph("body")
    document.save(path)
    updates = []
    callback = lambda *update: updates.append(update)
    pipeline = _RecordingPipeline()
    tool = RAGTool()
    monkeypatch.setattr(tool, "_get_pipeline", lambda namespace=None: pipeline)
    monkeypatch.setattr(
        tool,
        "_build_graph_after_import",
        lambda *args, **kwargs: {"status": "disabled"},
    )

    result = tool.execute(
        "add_document",
        file_path=str(path),
        document_id="doc-1",
        progress_callback=callback,
    )

    assert "document_id" in result
    assert updates == [("parsing", 1, 1, "parsing")]
    assert pipeline.progress_callback is callback


def test_pdf_document_reports_each_page_and_forwards_callback(
    tmp_path, monkeypatch
):
    class FakePdfReader:
        def __init__(self, path):
            self.pages = [
                SimpleNamespace(extract_text=lambda: "page one"),
                SimpleNamespace(extract_text=lambda: "page two"),
            ]

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=FakePdfReader))
    path = tmp_path / "document.pdf"
    path.write_bytes(b"%PDF-fake")
    updates = []
    callback = lambda *update: updates.append(update)
    pipeline = _RecordingPipeline()
    tool = RAGTool()
    monkeypatch.setattr(tool, "_get_pipeline", lambda namespace=None: pipeline)
    monkeypatch.setattr(
        tool,
        "_build_graph_after_import",
        lambda *args, **kwargs: {"status": "disabled"},
    )

    result = tool.execute(
        "add_document",
        file_path=str(path),
        document_id="doc-1",
        progress_callback=callback,
    )

    assert "PDF" in result
    assert updates == [
        ("parsing", 1, 2, "parsing"),
        ("parsing", 2, 2, "parsing"),
    ]
    assert pipeline.progress_callback is callback
