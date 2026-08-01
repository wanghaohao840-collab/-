from __future__ import annotations

from pathlib import Path

from hello_agents.memory.rag.pipeline import SimpleRAGPipeline
from hello_agents.memory.rag.qdrant_pipeline import RAGPipeline
from hello_agents.memory.storage.vector_store import InMemoryVectorStore
from hello_agents.tools.builtin.rag_tool import RAGTool


class FakeGraphService:
    def __init__(
        self,
        *,
        build_success=True,
        delete_success=True,
        delete_error=None,
        context_success=True,
    ):
        self.build_success = build_success
        self.delete_success = delete_success
        self.delete_error = delete_error
        self.context_success = context_success
        self.build_calls = []
        self.delete_calls = []
        self.retry_calls = []
        self.query_calls = []
        self.context_calls = []
        self.closed = False

    def close(self):
        self.closed = True

    def build_document_graph(self, document_id, chunks, metadata):
        self.build_calls.append((document_id, chunks, metadata))
        return {
            "success": self.build_success,
            "document_id": document_id,
            "status": "ready" if self.build_success else "failed",
            "data": {"build_id": "b1"},
            "error": None if self.build_success else {
                "type": "FixtureError",
                "message": "graph failed",
            },
            "page": None,
        }

    def delete_document_graph(self, document_id):
        if self.delete_error:
            raise self.delete_error
        self.delete_calls.append(document_id)
        return {
            "success": self.delete_success,
            "document_id": document_id,
            "status": "deleted" if self.delete_success else "cleanup_pending",
            "data": {},
            "error": None if self.delete_success else {
                "type": "FixtureError",
                "message": "cleanup failed",
            },
            "page": None,
        }

    def retry_document_graph(self, document_id):
        self.retry_calls.append(document_id)
        return {
            "success": True,
            "document_id": document_id,
            "status": "ready",
            "data": {},
            "error": None,
            "page": None,
        }

    def get_graph_status(self, document_id):
        self.query_calls.append(("status", document_id))
        return {
            "success": True,
            "document_id": document_id,
            "status": "ready",
            "data": {"build_id": "b1"},
            "error": None,
            "page": None,
        }

    def get_document_graph(self, document_id, **kwargs):
        self.query_calls.append(("graph", document_id, kwargs))
        return {
            "success": True,
            "document_id": document_id,
            "status": "ready",
            "data": {"nodes": [], "relations": []},
            "error": None,
            "page": {"next_node_cursor": None, "next_relation_cursor": None},
        }

    def get_graph_context(self, document_id, query, **kwargs):
        self.context_calls.append((document_id, query, kwargs))
        return {
            "success": self.context_success,
            "document_id": document_id,
            "status": "ready" if self.context_success else "failed",
            "data": (
                {
                    "entities": [
                        {
                            "id": f"{document_id}:concept:neo4j",
                            "type": "Concept",
                            "name": "Neo4j",
                        }
                    ],
                    "relations": [
                        {
                            "source_id": f"{document_id}:concept:neo4j",
                            "target_id": f"{document_id}:person:alice",
                            "type": "RELATED_TO",
                            "properties": {"evidence": "Alice uses Neo4j"},
                        }
                    ],
                }
                if self.context_success
                else {}
            ),
            "error": None if self.context_success else {
                "type": "GraphNotReady",
                "message": "Document graph is not ready",
            },
        }


def make_tool(tmp_path, graph_service):
    tool = RAGTool(
        rag_namespace="test-user",
        cache_path=str(tmp_path / "rag.json"),
        graph_service=graph_service,
    )
    pipeline = tool._pipelines["test-user"]
    pipeline.dimension = 2
    pipeline._to_vector = lambda _: [1.0, 0.0]
    pipeline.add_text(
        "Neo4j graph fixture",
        document_id="doc-1",
        metadata={"file_name": "graph.md"},
    )
    return tool


def test_json_pipeline_returns_all_and_only_target_document_chunks(tmp_path):
    pipeline = SimpleRAGPipeline(cache_path=str(tmp_path / "rag.json"))
    pipeline.dimension = 2
    pipeline._to_vector = lambda _: [1.0, 0.0]
    pipeline.add_text("one", document_id="doc-1")
    pipeline.add_text("two", document_id="doc-2")

    chunks = pipeline.get_document_chunks("doc-1")

    assert [chunk["document_id"] for chunk in chunks] == ["doc-1"]
    assert chunks[0]["content"] == "one"


def test_qdrant_pipeline_returns_all_and_only_target_document_chunks():
    store = InMemoryVectorStore()
    pipeline = RAGPipeline(
        vector_store=store,
        collection_name="graph_chunks",
        rag_namespace="tenant-a",
    )
    pipeline._to_vector = lambda _: [1.0] + [0.0] * 383
    pipeline.add_text("one", document_id="doc-1")
    pipeline.add_text("two", document_id="doc-2")

    chunks = pipeline.get_document_chunks("doc-1")

    assert [chunk["document_id"] for chunk in chunks] == ["doc-1"]
    assert chunks[0]["metadata"]["rag_namespace"] == "tenant-a"


def test_successful_document_import_builds_graph_and_reports_ready(tmp_path):
    graph = FakeGraphService()
    tool = make_tool(tmp_path, graph)
    path = tmp_path / "fixture.md"
    path.write_text("# Heading\n\nbody", encoding="utf-8")

    result = tool.execute_result(
        "add_document",
        file_path=str(path),
        document_id="doc-1",
    )

    assert result.success is True
    assert result.data["graph"]["status"] == "ready"
    assert graph.build_calls[0][0] == "doc-1"
    assert {
        chunk["document_id"] for chunk in graph.build_calls[0][1]
    } == {"doc-1"}


def test_graph_build_failure_does_not_change_rag_import_success(tmp_path):
    graph = FakeGraphService(build_success=False)
    tool = make_tool(tmp_path, graph)
    path = tmp_path / "fixture.md"
    path.write_text("body", encoding="utf-8")

    result = tool.execute_result(
        "add_document",
        file_path=str(path),
        document_id="doc-1",
    )

    assert result.success is True
    assert result.data["graph"]["status"] == "failed"
    assert tool._pipelines["test-user"].get_document_chunks("doc-1")


def test_rag_delete_succeeds_when_graph_cleanup_becomes_pending(tmp_path):
    graph = FakeGraphService(delete_success=False)
    tool = make_tool(tmp_path, graph)
    pipeline = tool._pipelines["test-user"]
    pipeline.add_text("body", document_id="doc-1")

    result = tool.execute_result("delete_document", document_id="doc-1")

    assert result.success is True
    assert result.data["chunks_removed"] == 1
    assert result.data["graph"]["status"] == "cleanup_pending"
    assert pipeline.get_document_chunks("doc-1") == []
    assert graph.delete_calls == ["doc-1"]


def test_unexpected_graph_delete_exception_still_preserves_rag_success(tmp_path):
    graph = FakeGraphService(delete_error=RuntimeError("driver exploded"))
    tool = make_tool(tmp_path, graph)
    pipeline = tool._pipelines["test-user"]
    pipeline.add_text("body", document_id="doc-1")

    result = tool.execute_result("delete_document", document_id="doc-1")

    assert result.success is True
    assert result.data["graph"]["status"] == "cleanup_pending"
    assert pipeline.get_document_chunks("doc-1") == []


def test_clear_removes_each_existing_document_graph_after_rag_clear(tmp_path):
    graph = FakeGraphService()
    tool = make_tool(tmp_path, graph)
    pipeline = tool._pipelines["test-user"]
    pipeline.add_text("one", document_id="doc-1")
    pipeline.add_text("two", document_id="doc-2")

    result = tool.execute_result("clear")

    assert result.success is True
    assert pipeline.get_document_chunks("doc-1") == []
    assert pipeline.get_document_chunks("doc-2") == []
    assert sorted(graph.delete_calls) == ["doc-1", "doc-2"]
    assert len(result.data["graphs"]) == 2


def test_graph_actions_route_and_require_document_id(tmp_path):
    graph = FakeGraphService()
    tool = make_tool(tmp_path, graph)

    rejected = tool.execute_result("graph_status", document_id="")
    status = tool.execute_result("graph_status", document_id="doc-1")
    queried = tool.execute_result(
        "get_document_graph",
        document_id="doc-1",
        node_limit=10,
    )
    retried = tool.execute_result("retry_document_graph", document_id="doc-1")

    assert rejected.success is False
    assert status.data["status"] == "ready"
    assert queried.data["data"] == {"nodes": [], "relations": []}
    assert retried.data["status"] == "ready"
    assert graph.retry_calls == ["doc-1"]


def test_no_graph_configuration_keeps_rag_available(tmp_path):
    tool = RAGTool(
        rag_namespace="no-graph",
        cache_path=str(tmp_path / "rag.json"),
        enable_graph=False,
    )
    pipeline = tool._pipelines["no-graph"]
    pipeline.dimension = 2
    pipeline._to_vector = lambda _: [1.0, 0.0]
    path = Path(tmp_path) / "fixture.md"
    path.write_text("body", encoding="utf-8")

    result = tool.execute_result(
        "add_document",
        file_path=str(path),
        document_id="doc-1",
    )

    assert result.success is True
    assert result.data["graph"]["status"] == "unavailable"


def test_graph_action_rejects_a_different_rag_namespace(tmp_path):
    graph = FakeGraphService()
    tool = make_tool(tmp_path, graph)

    result = tool.execute_result(
        "graph_status",
        document_id="doc-1",
        rag_namespace="other-user",
    )

    assert result.success is False
    assert result.data["error"]["type"] == "GraphScopeMismatch"
    assert graph.query_calls == []


def test_tool_closes_graph_service(tmp_path):
    graph = FakeGraphService()
    tool = make_tool(tmp_path, graph)

    tool.close()

    assert graph.closed is True


def test_graph_actions_are_exposed_in_tool_schema(tmp_path):
    tool = make_tool(tmp_path, FakeGraphService())

    parameters = tool.get_parameters()["properties"]

    assert "get_document_graph" in parameters["action"]["description"]
    assert "include_chunk_content" in parameters
    assert "node_cursor" in parameters


def test_graph_auto_mode_augments_ordinary_ask_with_context_and_citations(tmp_path):
    graph = FakeGraphService()
    tool = make_tool(tmp_path, graph)
    tool.llm.generate = lambda prompt, **kwargs: (
        setattr(tool.llm, "last_prompt", str(prompt)) or "graph-answer"
    )

    result = tool.execute_result(
        "ask",
        query="Neo4j",
        document_id="doc-1",
        rag_namespace="test-user",
        graph_mode="auto",
    )

    assert result.success is True
    assert graph.context_calls[0][0] == "doc-1"
    assert "图谱上下文" in tool.llm.last_prompt
    assert "Neo4j" in tool.llm.last_prompt
    assert "G-" in result.message
    assert result.data["graph_context_count"] >= 1


def test_graph_off_mode_does_not_call_service(tmp_path):
    graph = FakeGraphService()
    tool = make_tool(tmp_path, graph)

    output = tool.execute(
        "ask",
        query="Neo4j",
        document_id="doc-1",
        rag_namespace="test-user",
        graph_mode="off",
    )

    assert "RAG回答" in output
    assert graph.context_calls == []


def test_graph_auto_mode_falls_back_when_context_is_unavailable(tmp_path):
    graph = FakeGraphService(context_success=False)
    tool = make_tool(tmp_path, graph)

    output = tool.execute(
        "ask",
        query="Neo4j",
        document_id="doc-1",
        rag_namespace="test-user",
        graph_mode="auto",
    )

    assert "RAG回答" in output
    assert graph.context_calls


def test_graph_required_mode_fails_before_llm_when_context_is_unavailable(tmp_path):
    graph = FakeGraphService(context_success=False)
    tool = make_tool(tmp_path, graph)
    before = len(getattr(tool.llm, "prompts", []))

    result = tool.execute_result(
        "ask",
        query="Neo4j",
        document_id="doc-1",
        rag_namespace="test-user",
        graph_mode="required",
    )

    assert result.success is False
    assert "GraphRAG" in result.message
    assert len(getattr(tool.llm, "prompts", [])) == before
