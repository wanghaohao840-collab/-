from __future__ import annotations

import json

import pytest

from hello_agents.memory.graph.extractor import (
    GraphExtractionError,
    GraphExtractor,
    normalize_name,
    stable_graph_id,
)


class FixedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def response_payload():
    return {
        "concepts": [
            {
                "name": " Graph Database ",
                "description": "A graph model",
            },
            {
                "name": "graph   database",
                "description": "duplicate",
            },
        ],
        "knowledge_points": [
            {"name": "Cypher", "description": "Query language"}
        ],
        "persons": [
            {"name": "Alice", "description": "Teacher"},
            {"name": "Bob", "description": "Student"},
        ],
        "concept_relations": [],
        "knowledge_dependencies": [],
        "person_relations": [
            {
                "source": "Alice",
                "target": "Bob",
                "relation_name": "teaches",
                "chunk_id": "c1",
                "evidence": "Alice teaches Bob",
                "confidence": 1.4,
            }
        ],
        "mentions": [
            {
                "chunk_id": "c1",
                "target_type": "concept",
                "target": "Graph Database",
                "evidence": "graph database",
                "confidence": -0.2,
            }
        ],
    }


def chunks(count=1):
    return [
        {
            "id": f"c{index + 1}",
            "content": f"chunk {index + 1}",
            "metadata": {
                "chunk_index": index,
                "page_number": index + 1,
                "heading_path": ["Part", f"Section {index + 1}"],
            },
        }
        for index in range(count)
    ]


def test_extracts_stable_deduplicated_graph_with_evidence_and_chapters():
    llm = FixedLLM([json.dumps(response_payload())])
    extractor = GraphExtractor(llm, sleep=lambda _: None, random=lambda: 0)

    graph = extractor.extract(
        "doc-1",
        chunks(),
        {"name": "Fixture", "source": "unit"},
    )
    payload = graph.to_store_payload()

    assert graph.llm_attempt_count == 1
    assert len(payload["concepts"]) == 1
    assert len(payload["chapters"]) == 2
    assert payload["chunks"][0]["chapter_id"] == payload["chapters"][-1]["chapter_id"]
    assert payload["relations"][0]["type"] == "RELATED_TO"
    person_relation = next(
        relation for relation in payload["relations"]
        if relation["properties"].get("relation_name") == "teaches"
    )
    assert person_relation["properties"]["confidence"] == 1.0
    mention = next(
        relation for relation in payload["relations"]
        if relation["type"] == "MENTIONS"
    )
    assert mention["properties"]["confidence"] == 0.0
    assert mention["properties"]["chunk_id"] == "c1"

    assert stable_graph_id("doc-1", "concept", " Graph Database ") == stable_graph_id(
        "doc-1", "concept", "graph   database"
    )
    assert stable_graph_id(
        "doc-1", "concept", "Graph Database", "tenant-a"
    ) != stable_graph_id(
        "doc-1", "concept", "Graph Database", "tenant-b"
    )
    assert normalize_name(" Graph   Database ") == "graph database"


def test_batches_by_five_chunks_and_token_budget():
    empty = json.dumps(
        {
            "concepts": [],
            "knowledge_points": [],
            "persons": [],
            "concept_relations": [],
            "knowledge_dependencies": [],
            "person_relations": [],
            "mentions": [],
        }
    )
    llm = FixedLLM([empty, empty, empty])
    extractor = GraphExtractor(
        llm,
        max_batch_chunks=5,
        max_batch_tokens=20,
        sleep=lambda _: None,
        random=lambda: 0,
    )

    graph = extractor.extract("doc-1", chunks(6), {})

    assert graph.llm_attempt_count == 3
    assert len(llm.calls) == 3


def test_long_chunk_is_windowed_without_losing_original_chunk_id():
    payload = response_payload()
    payload["mentions"][0]["chunk_id"] = "original"
    payload["person_relations"][0]["chunk_id"] = "original"
    llm = FixedLLM([json.dumps(payload), json.dumps({**payload, "mentions": []})])
    extractor = GraphExtractor(
        llm,
        max_batch_tokens=10,
        sleep=lambda _: None,
        random=lambda: 0,
    )

    graph = extractor.extract(
        "doc-1",
        [{"id": "original", "content": "x" * 18, "metadata": {"chunk_index": 0}}],
        {},
    )

    assert graph.llm_attempt_count == 2
    assert all("original" in call[0] for call in llm.calls)


def test_invalid_json_retries_three_times_then_fails_with_count():
    llm = FixedLLM(["not-json", "still-bad", "{}"])
    extractor = GraphExtractor(llm, sleep=lambda _: None, random=lambda: 0)

    with pytest.raises(GraphExtractionError) as caught:
        extractor.extract("doc-1", chunks(), {})

    assert caught.value.llm_attempt_count == 3
    assert len(llm.calls) == 3


def test_failed_later_batch_counts_calls_from_prior_batches():
    valid = json.dumps({key: [] for key in (
        "concepts",
        "knowledge_points",
        "persons",
        "concept_relations",
        "knowledge_dependencies",
        "person_relations",
        "mentions",
    )})
    llm = FixedLLM([valid, "bad", "bad", "bad"])
    extractor = GraphExtractor(
        llm,
        max_batch_tokens=7,
        sleep=lambda _: None,
        random=lambda: 0,
    )

    with pytest.raises(GraphExtractionError) as caught:
        extractor.extract("doc-1", chunks(2), {})

    assert caught.value.llm_attempt_count == 4


def test_retry_after_header_is_preferred_and_capped_at_thirty_seconds():
    class Response:
        headers = {"retry-after": "45"}

    error = TimeoutError("temporary timeout")
    error.response = Response()
    sleeps = []
    valid = json.dumps({key: [] for key in (
        "concepts",
        "knowledge_points",
        "persons",
        "concept_relations",
        "knowledge_dependencies",
        "person_relations",
        "mentions",
    )})
    extractor = GraphExtractor(
        FixedLLM([error, valid]),
        sleep=sleeps.append,
        random=lambda: 0,
    )

    graph = extractor.extract("doc-1", chunks(), {})

    assert graph.llm_attempt_count == 2
    assert sleeps == [30.0]


def test_authentication_failure_does_not_retry():
    llm = FixedLLM(["[LLM调用失败] AuthenticationError: invalid key"])
    extractor = GraphExtractor(llm, sleep=lambda _: None, random=lambda: 0)

    with pytest.raises(GraphExtractionError) as caught:
        extractor.extract("doc-1", chunks(), {})

    assert caught.value.llm_attempt_count == 1


@pytest.mark.parametrize(
    "field,value,error_text",
    [
        (
            "concept_relations",
            [{
                "source": "Graph Database",
                "target": "missing",
                "type": "RELATED_TO",
                "chunk_id": "c1",
            }],
            "dangling",
        ),
        (
            "concept_relations",
            [{
                "source": "Graph Database",
                "target": "Graph Database",
                "type": "DYNAMIC_USER_TYPE",
                "chunk_id": "c1",
            }],
            "unsupported",
        ),
    ],
)
def test_rejects_dangling_and_unknown_relationships(field, value, error_text):
    payload = response_payload()
    payload[field] = value
    llm = FixedLLM([json.dumps(payload)])
    extractor = GraphExtractor(llm, sleep=lambda _: None, random=lambda: 0)

    with pytest.raises(GraphExtractionError, match=error_text):
        extractor.extract("doc-1", chunks(), {})
