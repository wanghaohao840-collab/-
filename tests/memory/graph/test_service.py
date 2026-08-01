from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock

from hello_agents.memory.graph.contracts import ExtractedGraph
from hello_agents.memory.graph.extractor import GraphExtractionError
from hello_agents.memory.graph.service import KnowledgeGraphService
from hello_agents.memory.graph.state import GraphStateRepository


class FakeExtractor:
    def __init__(self, graph=None, error=None):
        self.graph = graph or ExtractedGraph(
            document={"name": "Fixture"},
            chunks=[{
                "chunk_id": "c1",
                "content": "content",
                "page_number": 1,
                "chunk_index": 0,
                "chapter_id": None,
            }],
            llm_attempt_count=2,
        )
        self.error = error
        self.calls = []

    def extract(self, document_id, chunks, metadata, **kwargs):
        self.calls.append((document_id, chunks, metadata))
        if self.error:
            raise self.error
        return self.graph


class FakeStore:
    def __init__(self):
        self.replacements = []
        self.deletions = []
        self.builds = {}
        self.graph = {"nodes": [], "relations": [], "page": {}}
        self.chapters = []
        self.typed = {"nodes": [], "relations": [], "page": {}}
        self.replace_error = None
        self.delete_error = None
        self.build_error = None
        self.context = {"entities": [], "relations": []}
        self.context_error = None
        self.context_calls = []

    def replace_document_graph(self, document_id, build_id, graph, **kwargs):
        if self.replace_error:
            raise self.replace_error
        self.replacements.append((document_id, build_id, graph))
        self.builds[document_id] = {
            "build_id": build_id,
            "graph_status": "ready",
        }
        return {"node_count": 2, "relation_count": 1}

    def get_document_build(self, document_id, **kwargs):
        if self.build_error:
            raise self.build_error
        return self.builds.get(document_id)

    def get_document_graph(self, document_id, **kwargs):
        return dict(self.graph)

    def get_chapters(self, document_id, **kwargs):
        return list(self.chapters)

    def get_typed_relations(self, document_id, **kwargs):
        return dict(self.typed)

    def get_graph_context(self, document_id, **kwargs):
        if self.context_error:
            raise self.context_error
        self.context_calls.append((document_id, kwargs))
        return dict(self.context)

    def delete_document(self, document_id, **kwargs):
        if self.delete_error:
            raise self.delete_error
        self.deletions.append(document_id)
        return {"nodes_removed": 2, "relations_removed": 1}

    def close(self):
        pass


def make_service(tmp_path, *, store=None, extractor=None, loader=None):
    return KnowledgeGraphService(
        store=store or FakeStore(),
        extractor=extractor or FakeExtractor(),
        state_repository=GraphStateRepository(tmp_path / "graph-state.json"),
        chunk_loader=loader or (lambda document_id: [{
            "id": "c1",
            "content": document_id,
            "metadata": {"document_id": document_id, "chunk_index": 0},
        }]),
        uuid_factory=lambda: "build-fixed",
        now=lambda: "2026-07-29T00:00:00Z",
    )


def test_build_transitions_to_ready_after_store_commit(tmp_path):
    service = make_service(tmp_path)

    result = service.build_document_graph(
        "doc-1",
        [{"id": "c1", "content": "content", "metadata": {}}],
        {"file_name": "fixture.txt"},
    )

    assert result["success"] is True
    assert result["status"] == "ready"
    assert result["data"] == {
        "build_id": "build-fixed",
        "node_count": 2,
        "relation_count": 1,
        "attempt_count": 1,
        "llm_attempt_count": 2,
        "updated_at": "2026-07-29T00:00:00Z",
    }
    assert service.get_graph_status("doc-1")["status"] == "ready"


def test_extraction_and_store_failures_become_safe_failed_state(tmp_path):
    secret = "neo4j://user:password@secret-host"
    extractor = FakeExtractor(
        error=GraphExtractionError(secret, llm_attempt_count=3)
    )
    service = make_service(tmp_path, extractor=extractor)

    result = service.build_document_graph("doc-1", [{"id": "c1", "content": "x"}], {})

    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["data"]["llm_attempt_count"] == 3
    assert "password" not in result["error"]["message"]
    assert service.lock_registry_size == 0


def test_recovery_matches_committed_build_and_fails_mismatch(tmp_path):
    state = GraphStateRepository(tmp_path / "state.json")
    state.upsert("matched", status="building", build_id="b1")
    state.upsert("mismatch", status="building", build_id="b2")
    store = FakeStore()
    store.builds["matched"] = {"build_id": "b1", "graph_status": "ready"}
    store.builds["mismatch"] = {"build_id": "other", "graph_status": "ready"}

    service = KnowledgeGraphService(
        store=store,
        extractor=FakeExtractor(),
        state_repository=state,
        chunk_loader=lambda _: [],
    )

    assert service.get_graph_status("matched")["status"] == "ready"
    mismatch = service.get_graph_status("mismatch")
    assert mismatch["status"] == "failed"
    assert mismatch["error"]["type"] == "InterruptedBuild"


def test_recovery_check_failure_is_retryable_failed_state(tmp_path):
    state = GraphStateRepository(tmp_path / "state.json")
    state.upsert("doc-1", status="building", build_id="b1")
    store = FakeStore()
    store.build_error = RuntimeError("database unavailable")

    service = KnowledgeGraphService(
        store=store,
        extractor=FakeExtractor(),
        state_repository=state,
        chunk_loader=lambda _: [],
    )

    result = service.get_graph_status("doc-1")
    assert result["status"] == "failed"
    assert result["error"]["type"] == "RecoveryCheckFailed"


def test_retry_only_allows_failed_and_cleanup_pending(tmp_path):
    loaded = []
    store = FakeStore()
    extractor = FakeExtractor()
    service = make_service(
        tmp_path,
        store=store,
        extractor=extractor,
        loader=lambda document_id: loaded.append(document_id) or [{
            "id": "c1",
            "content": document_id,
            "metadata": {"document_id": document_id},
        }],
    )
    service.state_repository.upsert("ready", status="ready", build_id="r")
    service.state_repository.upsert("failed", status="failed", build_id="f")
    service.state_repository.upsert(
        "cleanup", status="cleanup_pending", build_id="c"
    )

    rejected = service.retry_document_graph("ready")
    rebuilt = service.retry_document_graph("failed")
    cleaned = service.retry_document_graph("cleanup")

    assert rejected["success"] is False
    assert loaded == ["failed"]
    assert rebuilt["status"] == "ready"
    assert cleaned["status"] == "deleted"
    assert store.deletions == ["cleanup"]


def test_query_requires_ready_and_builds_chapter_tree(tmp_path):
    store = FakeStore()
    store.chapters = [
        {
            "chapter_id": "root",
            "title": "Root",
            "level": 1,
            "order": 0,
            "heading_path": ["Root"],
            "parent_id": None,
            "chunk_ids": ["c1"],
        },
        {
            "chapter_id": "child",
            "title": "Child",
            "level": 2,
            "order": 1,
            "heading_path": ["Root", "Child"],
            "parent_id": "root",
            "chunk_ids": ["c2"],
        },
    ]
    service = make_service(tmp_path, store=store)

    assert service.get_document_graph("doc-1")["success"] is False
    service.state_repository.upsert("doc-1", status="ready", build_id="b1")
    tree = service.get_chapter_tree("doc-1")

    assert tree["success"] is True
    assert tree["data"]["chapters"][0]["children"][0]["chapter_id"] == "child"


def test_graph_context_requires_ready_and_normalizes_terms(tmp_path):
    store = FakeStore()
    store.context = {
        "entities": [{"id": "concept-1", "type": "Concept", "name": "Neo4j"}],
        "relations": [],
    }
    service = make_service(tmp_path, store=store)

    not_ready = service.get_graph_context("doc-1", "Neo4j 数据库")
    assert not_ready["success"] is False
    assert not_ready["error"]["type"] == "GraphNotReady"
    assert store.context_calls == []

    service.state_repository.upsert("doc-1", status="ready", build_id="b1")
    result = service.get_graph_context(
        "doc-1",
        "Neo4j 数据库",
        node_limit=2,
        relation_limit=3,
    )

    assert result["success"] is True
    assert result["data"]["entities"][0]["name"] == "Neo4j"
    assert "neo4j" in result["data"]["query_terms"]
    assert "数据库" in result["data"]["query_terms"]
    assert store.context_calls[0][0] == "doc-1"
    assert store.context_calls[0][1]["rag_namespace"] == "default"
    assert store.context_calls[0][1]["node_limit"] == 2


def test_graph_context_failure_is_sanitized(tmp_path):
    store = FakeStore()
    store.context_error = RuntimeError(
        "neo4j://user:password@secret-host unavailable"
    )
    service = make_service(tmp_path, store=store)
    service.state_repository.upsert("doc-1", status="ready", build_id="b1")

    result = service.get_graph_context("doc-1", "Neo4j")

    assert result["success"] is False
    assert result["error"]["type"] == "RuntimeError"
    assert "password" not in result["error"]["message"]


def test_delete_failure_marks_cleanup_pending(tmp_path):
    store = FakeStore()
    store.delete_error = RuntimeError("offline")
    service = make_service(tmp_path, store=store)

    result = service.delete_document_graph("doc-1")

    assert result["success"] is False
    assert result["status"] == "cleanup_pending"


def test_same_document_mutations_serialize_and_lock_entries_are_reclaimed(tmp_path):
    entered = Event()
    release = Event()
    active = 0
    max_active = 0
    guard = Lock()

    class BlockingExtractor(FakeExtractor):
        def extract(self, document_id, chunks, metadata, **kwargs):
            nonlocal active, max_active
            with guard:
                active += 1
                max_active = max(max_active, active)
            entered.set()
            release.wait(timeout=5)
            with guard:
                active -= 1
            return self.graph

    service = make_service(tmp_path, extractor=BlockingExtractor())
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            service.build_document_graph,
            "doc-1",
            [{"id": "c1", "content": "x"}],
            {},
        )
        entered.wait(timeout=5)
        second = pool.submit(
            service.build_document_graph,
            "doc-1",
            [{"id": "c1", "content": "x"}],
            {},
        )
        release.set()
        assert first.result()["success"]
        assert second.result()["success"]

    assert max_active == 1
    assert service.lock_registry_size == 0


def test_different_documents_can_build_independently(tmp_path):
    barrier = Barrier(2)
    entered = []
    guard = Lock()

    class ParallelExtractor(FakeExtractor):
        def extract(self, document_id, chunks, metadata, **kwargs):
            with guard:
                entered.append(document_id)
            barrier.wait(timeout=5)
            return self.graph

    service = make_service(tmp_path, extractor=ParallelExtractor())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            pool.submit(
                service.build_document_graph,
                document_id,
                [{"id": "c1", "content": document_id}],
                {},
            )
            for document_id in ("doc-1", "doc-2")
        ]
        assert all(result.result()["success"] for result in results)

    assert sorted(entered) == ["doc-1", "doc-2"]
    assert service.lock_registry_size == 0
