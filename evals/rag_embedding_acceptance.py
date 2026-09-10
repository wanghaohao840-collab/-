"""Run and persist an isolated old/new embedding retrieval comparison."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Mapping, Sequence
import uuid

from deploy.embedding_probe import read_values
from evals.rag_retrieval_quality import (
    QualityFailure,
    RetrievalQuality,
    evaluate_retrieval,
    load_dataset,
    measure_retrieval,
)
from hello_agents.memory.rag.embedding_profile import EmbeddingFailure
from hello_agents.memory.rag.embedding_runtime import RAGEmbeddingRuntime, build_rag_embedding


class AcceptanceFailure(RuntimeError):
    """The live comparison or its audit report is invalid."""


@dataclass(frozen=True)
class AcceptanceResult:
    baseline: RetrievalQuality
    candidate: RetrievalQuality
    candidate_fingerprint: str
    candidate_model: str
    dataset_digest: str


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _dataset_digest(items: Sequence[Mapping[str, str]]) -> str:
    return hashlib.sha256(_canonical(items).encode("utf-8")).hexdigest()


def _search_for_runtime(items, runtime: RAGEmbeddingRuntime):
    vectors = runtime.embed_documents([item["content"] for item in items])
    indexed = tuple(zip(items, vectors, strict=True))

    def search(question: str, namespace: str, documents: Sequence[str], limit: int):
        if len(documents) != 1 or type(limit) is not int or limit != 5:
            raise QualityFailure("search")
        query = runtime.embed_query(question)
        ranked = []
        for item, vector in indexed:
            if item["namespace"] != namespace or item["document_id"] not in documents:
                continue
            score = math.fsum(left * right for left, right in zip(query, vector, strict=True))
            ranked.append((score, item["chunk_id"], item))
        ranked.sort(key=lambda value: (-value[0], value[1]))
        return [
            {
                "chunk_id": item["chunk_id"],
                "namespace": item["namespace"],
                "document_id": item["document_id"],
            }
            for _, _, item in ranked[:limit]
        ]

    return search


def compare_runtimes(items, baseline_runtime, candidate_runtime) -> AcceptanceResult:
    baseline = measure_retrieval(items, _search_for_runtime(items, baseline_runtime))
    candidate = evaluate_retrieval(
        items,
        _search_for_runtime(items, candidate_runtime),
        baseline=baseline,
    )
    return AcceptanceResult(
        baseline=baseline,
        candidate=candidate,
        candidate_fingerprint=candidate_runtime.profile.fingerprint,
        candidate_model=candidate_runtime.profile.model,
        dataset_digest=_dataset_digest(items),
    )


def write_report(path: Path | str, result: AcceptanceResult) -> str:
    path = Path(path)
    if not path.is_absolute() or not path.parent.is_dir() or path.exists():
        raise AcceptanceFailure("report_path")
    checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    body = {
        "schema_version": 1,
        "checked_at": checked_at,
        "dataset_digest": result.dataset_digest,
        "candidate_fingerprint": result.candidate_fingerprint,
        "candidate_model": result.candidate_model,
        "baseline": asdict(result.baseline),
        "candidate": asdict(result.candidate),
    }
    body["report_digest"] = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(_canonical(body))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except (OSError, UnicodeError):
        raise AcceptanceFailure("report_write") from None
    finally:
        temporary.unlink(missing_ok=True)
    return body["report_digest"]


def inspect_report(path: Path | str, *, expected_fingerprint: str) -> AcceptanceResult:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        fields = {
            "schema_version", "checked_at", "dataset_digest", "candidate_fingerprint",
            "candidate_model", "baseline", "candidate", "report_digest",
        }
        if not isinstance(document, dict) or set(document) != fields:
            raise ValueError()
        digest = document.pop("report_digest")
        if (
            type(document["schema_version"]) is not int
            or document["schema_version"] != 1
            or document["candidate_fingerprint"] != expected_fingerprint
            or digest != hashlib.sha256(_canonical(document).encode("utf-8")).hexdigest()
        ):
            raise ValueError()
        baseline = RetrievalQuality(**document["baseline"])
        candidate = RetrievalQuality(**document["candidate"])
        if (
            candidate.scope_leaks
            or candidate.recall_at_5 < 0.90
            or candidate.mrr_at_5 < 0.75
            or candidate.recall_at_5 < baseline.recall_at_5
            or candidate.mrr_at_5 < baseline.mrr_at_5
        ):
            raise ValueError()
        return AcceptanceResult(
            baseline=baseline,
            candidate=candidate,
            candidate_fingerprint=document["candidate_fingerprint"],
            candidate_model=document["candidate_model"],
            dataset_digest=document["dataset_digest"],
        )
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise AcceptanceFailure("report") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Compare candidate RAG embedding quality")
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        items = load_dataset(args.dataset)
        baseline_runtime = build_rag_embedding(
            {"RAG_EMBEDDING_PROVIDER": "simple"}, backend="qdrant"
        )
        candidate_values = read_values(args.env_file)
        candidate_values["RAG_EMBEDDING_PROVIDER"] = "siliconflow"
        candidate_runtime = build_rag_embedding(candidate_values, backend="qdrant")
        result = compare_runtimes(items, baseline_runtime, candidate_runtime)
        report_digest = write_report(args.report, result)
        print(_canonical({
            "status": "passed",
            "candidate_model": result.candidate_model,
            "candidate_fingerprint": result.candidate_fingerprint,
            "baseline": asdict(result.baseline),
            "candidate": asdict(result.candidate),
            "report_digest": report_digest,
        }))
        return 0
    except (AcceptanceFailure, EmbeddingFailure, QualityFailure) as exc:
        print(_canonical({"status": "failed", "code": getattr(exc, "code", str(exc))}))
        return 1
    except Exception:
        print(_canonical({"status": "failed", "code": "internal"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
