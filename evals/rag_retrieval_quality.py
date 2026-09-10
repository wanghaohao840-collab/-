"""Deterministic retrieval metrics; the caller supplies a scoped search."""

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence


class QualityFailure(RuntimeError):
    """The dataset, search result, or quality gate is invalid."""


@dataclass(frozen=True)
class RetrievalQuality:
    cases: int
    recall_at_5: float
    mrr_at_5: float
    scope_leaks: int

    def __post_init__(self) -> None:
        if type(self.cases) is not int or self.cases <= 0:
            raise QualityFailure("metrics")
        if type(self.scope_leaks) is not int or self.scope_leaks < 0:
            raise QualityFailure("metrics")
        for value in (self.recall_at_5, self.mrr_at_5):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise QualityFailure("metrics")


def load_dataset(path: Path | str) -> tuple[dict[str, str], ...]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        raise QualityFailure("dataset") from None
    if (
        not isinstance(data, dict)
        or set(data) != {"schema_version", "items"}
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or not isinstance(data["items"], list)
    ):
        raise QualityFailure("dataset")
    seen_ids: set[str] = set()
    seen_chunks: set[str] = set()
    languages: list[str] = []
    required = {"id", "language", "namespace", "document_id", "chunk_id", "content", "question"}
    for item in data["items"]:
        if (
            not isinstance(item, dict)
            or set(item) != required
            or any(not isinstance(item[key], str) or not item[key].strip() for key in required)
            or item["id"] in seen_ids
            or item["chunk_id"] in seen_chunks
            or item["language"] not in {"zh", "en"}
        ):
            raise QualityFailure("dataset")
        seen_ids.add(item["id"])
        seen_chunks.add(item["chunk_id"])
        languages.append(item["language"])
    if languages.count("zh") < 10 or languages.count("en") < 10:
        raise QualityFailure("coverage")
    return tuple(data["items"])


def measure_retrieval(
    items: Iterable[Mapping[str, str]],
    search: Callable[[str, str, Sequence[str], int], Sequence[Mapping[str, object]]],
) -> RetrievalQuality:
    reciprocal = 0.0
    recalled = 0
    leaks = 0
    count = 0
    for item in items:
        try:
            question = item["question"]
            namespace = item["namespace"]
            document_id = item["document_id"]
            expected_chunk = item["chunk_id"]
        except (KeyError, TypeError):
            raise QualityFailure("dataset") from None
        hits = search(question, namespace, [document_id], 5)
        if not isinstance(hits, (list, tuple)) or len(hits) > 5:
            raise QualityFailure("search")
        rank = None
        seen_hits: set[str] = set()
        for position, hit in enumerate(hits, 1):
            if not isinstance(hit, Mapping) or not isinstance(hit.get("chunk_id"), str):
                raise QualityFailure("search")
            chunk_id = hit["chunk_id"]
            if chunk_id in seen_hits:
                raise QualityFailure("search")
            seen_hits.add(chunk_id)
            if hit.get("namespace") != namespace or hit.get("document_id") != document_id:
                leaks += 1
            if chunk_id == expected_chunk and rank is None:
                rank = position
        if rank is not None:
            recalled += 1
            reciprocal += 1 / rank
        count += 1
    if not count:
        raise QualityFailure("coverage")
    return RetrievalQuality(count, recalled / count, reciprocal / count, leaks)


def evaluate_retrieval(
    items: Iterable[Mapping[str, str]],
    search: Callable[[str, str, Sequence[str], int], Sequence[Mapping[str, object]]],
    *,
    baseline: RetrievalQuality | None = None,
) -> RetrievalQuality:
    if baseline is not None and not isinstance(baseline, RetrievalQuality):
        raise QualityFailure("baseline")
    result = measure_retrieval(items, search)
    if (
        result.scope_leaks
        or result.recall_at_5 < 0.90
        or result.mrr_at_5 < 0.75
        or baseline is not None
        and (result.recall_at_5 < baseline.recall_at_5 or result.mrr_at_5 < baseline.mrr_at_5)
    ):
        raise QualityFailure("gate")
    return result
