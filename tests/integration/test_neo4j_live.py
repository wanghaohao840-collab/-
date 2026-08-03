from __future__ import annotations

import os
import uuid

import pytest

from hello_agents.memory.storage.neo4j_store import Neo4jGraphStore


def _live_graph(document_id: str) -> dict:
    return {
        "document": {"name": "Live fixture", "source": "pytest"},
        "chapters": [],
        "chunks": [{
            "chunk_id": f"{document_id}:chunk:0",
            "content": "Neo4j live fixture",
            "page_number": 1,
            "chunk_index": 0,
            "chapter_id": None,
        }],
        "concepts": [{
            "concept_id": f"{document_id}:concept:neo4j",
            "name": "Neo4j",
            "normalized_name": "neo4j",
            "description": "Graph database",
        }],
        "knowledge_points": [],
        "persons": [],
        "relations": [{
            "source_id": f"{document_id}:chunk:0",
            "target_id": f"{document_id}:concept:neo4j",
            "type": "MENTIONS",
            "properties": {
                "chunk_id": f"{document_id}:chunk:0",
                "evidence": "Neo4j",
                "confidence": 1.0,
            },
        }],
    }


@pytest.mark.skipif(
    not os.getenv("NEO4J_TEST_URI"),
    reason="NEO4J_TEST_URI is not configured",
)
def test_live_neo4j_replace_query_and_delete():
    document_id = f"codex-live-{uuid.uuid4()}"
    second_document_id = f"codex-live-{uuid.uuid4()}"
    namespace = "neo4j-live-tests"
    store = Neo4jGraphStore(
        uri=os.environ["NEO4J_TEST_URI"],
        username=os.getenv("NEO4J_TEST_USERNAME", "neo4j"),
        password=os.environ["NEO4J_TEST_PASSWORD"],
        database=os.getenv("NEO4J_TEST_DATABASE", "neo4j"),
    )
    graph = _live_graph(document_id)
    second_graph = _live_graph(second_document_id)
    try:
        first = store.replace_document_graph(
            document_id,
            "build-1",
            graph,
            rag_namespace=namespace,
        )
        queried = store.get_document_graph(
            document_id,
            rag_namespace=namespace,
            node_limit=100,
            relation_limit=100,
        )
        graph_context = store.get_graph_context(
            document_id,
            rag_namespace=namespace,
            query_terms=["neo4j"],
            node_limit=8,
            relation_limit=16,
        )
        second = store.replace_document_graph(
            document_id,
            "build-2",
            graph,
            rag_namespace=namespace,
        )
        store.replace_document_graph(
            second_document_id,
            "build-1",
            second_graph,
            rag_namespace=namespace,
        )
        shared = store.get_cross_document_entities(
            [document_id, second_document_id],
            query_terms=["neo4j"],
            rag_namespace=namespace,
        )
        build = store.get_document_build(
            document_id,
            rag_namespace=namespace,
        )

        assert first["node_count"] == second["node_count"] == 3
        assert len(queried["nodes"]) == 3
        assert any(
            entity["name"] == "Neo4j"
            for entity in graph_context["entities"]
        )
        assert any(
            relation["type"] == "MENTIONS"
            for relation in graph_context["relations"]
        )
        assert build == {"build_id": "build-2", "graph_status": "ready"}
        assert len(shared["entities"]) == 1
        assert {
            member["document_id"]
            for member in shared["entities"][0]["members"]
        } == {document_id, second_document_id}

        removed_first = store.delete_document(
            document_id,
            rag_namespace=namespace,
        )
        assert removed_first["nodes_removed"] >= 0
        assert store.get_cross_document_entities(
            [document_id, second_document_id],
            query_terms=["neo4j"],
            rag_namespace=namespace,
        ) == {"entities": []}
    finally:
        try:
            removed = store.delete_document(
                document_id,
                rag_namespace=namespace,
            )
            assert removed["nodes_removed"] >= 0
        finally:
            store.close()
