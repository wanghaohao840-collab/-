import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals.rag_embedding_acceptance import (
    AcceptanceFailure,
    compare_runtimes,
    inspect_report,
    write_report,
)
from evals.rag_retrieval_quality import QualityFailure, load_dataset


DATA = Path(__file__).parents[2] / "evals" / "data" / "bge_m3_retrieval.json"


class KeywordRuntime:
    def __init__(self, items, *, bad=False, fingerprint="a" * 64):
        self.bad = bad
        self.profile = SimpleNamespace(fingerprint=fingerprint, model="test-model")
        self.documents = {item["content"]: index for index, item in enumerate(items)}
        self.questions = {item["question"]: index for index, item in enumerate(items)}

    def _vector(self, index, *, bad=False):
        vector = [0.0] * len(self.documents)
        vector[index] = -1.0 if bad else 1.0
        return vector

    def embed_documents(self, texts):
        return [self._vector(self.documents[text]) for text in texts]

    def embed_query(self, text):
        return self._vector(self.questions[text], bad=self.bad)


def test_comparison_uses_competing_chunks_in_each_document() -> None:
    items = load_dataset(DATA)
    assert len({item["document_id"] for item in items}) == 2

    runtime = KeywordRuntime(items)
    result = compare_runtimes(items, runtime, runtime)

    assert result.baseline == result.candidate
    assert result.candidate.cases == 20
    assert result.candidate.scope_leaks == 0


def test_candidate_below_baseline_is_rejected() -> None:
    items = load_dataset(DATA)
    with pytest.raises(QualityFailure, match="gate"):
        compare_runtimes(items, KeywordRuntime(items), KeywordRuntime(items, bad=True))


def test_report_round_trip_and_tamper_rejection(tmp_path: Path) -> None:
    items = load_dataset(DATA)
    result = compare_runtimes(items, KeywordRuntime(items), KeywordRuntime(items))
    report = (tmp_path / "quality.json").resolve()

    digest = write_report(report, result)

    assert len(digest) == 64
    assert inspect_report(report, expected_fingerprint="a" * 64) == result
    body = json.loads(report.read_text(encoding="utf-8"))
    body["candidate"]["scope_leaks"] = 1
    report.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(AcceptanceFailure, match="report"):
        inspect_report(report, expected_fingerprint="a" * 64)


def test_report_never_overwrites_existing_file(tmp_path: Path) -> None:
    items = load_dataset(DATA)
    result = compare_runtimes(items, KeywordRuntime(items), KeywordRuntime(items))
    report = (tmp_path / "quality.json").resolve()
    report.write_text("keep", encoding="utf-8")

    with pytest.raises(AcceptanceFailure, match="report_path"):
        write_report(report, result)

    assert report.read_text(encoding="utf-8") == "keep"
