from pathlib import Path

import pytest

from evals.rag_retrieval_quality import (
    QualityFailure,
    RetrievalQuality,
    evaluate_retrieval,
    load_dataset,
)


DATA = Path(__file__).parents[2] / "evals" / "data" / "bge_m3_retrieval.json"


def test_dataset_and_perfect_scoped_search_pass() -> None:
    items = load_dataset(DATA)
    assert len(items) == 20
    by_question = {item["question"]: item for item in items}

    def search(question, namespace, documents, limit):
        item = by_question[question]
        assert limit == 5
        return [{"chunk_id": item["chunk_id"], "namespace": namespace, "document_id": documents[0]}]

    result = evaluate_retrieval(items, search, baseline=RetrievalQuality(20, 0.9, 0.75, 0))
    assert result == RetrievalQuality(20, 1.0, 1.0, 0)


def test_gate_rejects_misses_leaks_and_regression() -> None:
    items = load_dataset(DATA)
    with pytest.raises(QualityFailure, match="gate"):
        evaluate_retrieval(items, lambda *args: [])
    with pytest.raises(QualityFailure, match="gate"):
        evaluate_retrieval(
            items,
            lambda _q, _n, documents, _k: [
                {"chunk_id": "x", "namespace": "other", "document_id": documents[0]}
            ],
        )

    def mostly_good(question, namespace, documents, limit):
        item = next(candidate for candidate in items if candidate["question"] == question)
        if item["id"] == "zh-1":
            return []
        return [{"chunk_id": item["chunk_id"], "namespace": namespace, "document_id": documents[0]}]

    with pytest.raises(QualityFailure, match="gate"):
        evaluate_retrieval(items, mostly_good, baseline=RetrievalQuality(20, 1.0, 1.0, 0))


def test_dataset_rejects_undercoverage(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"schema_version":1,"items":[]}', encoding="utf-8")
    with pytest.raises(QualityFailure):
        load_dataset(path)


def test_metrics_and_duplicate_hits_are_rejected() -> None:
    with pytest.raises(QualityFailure, match="metrics"):
        RetrievalQuality(20, 1.0, 1.01, 0)

    item = load_dataset(DATA)[0]
    hit = {
        "chunk_id": item["chunk_id"],
        "namespace": item["namespace"],
        "document_id": item["document_id"],
    }
    with pytest.raises(QualityFailure, match="search"):
        evaluate_retrieval((item,), lambda *args: [hit, hit])
