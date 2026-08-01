from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any

from app.import_models import ProgressCallback
from hello_agents.memory.rag.contracts import DocumentSegment, PreparedChunk


PROJECT_POINT_NAMESPACE_UUID = uuid.UUID("c273c00a-40ac-47a9-b475-164f135ada18")
logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def default_chunk_id(rag_namespace: str, document_id: str, chunk_index: int) -> str:
    return f"{document_id}_{chunk_index}"


def qdrant_point_id(rag_namespace: str, document_id: str, chunk_index: int) -> str:
    point_name = canonical_json([rag_namespace, document_id, chunk_index])
    return str(uuid.uuid5(PROJECT_POINT_NAMESPACE_UUID, point_name))


def json_safe_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [json_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe_value(item) for key, item in value.items()}
    return str(value)


def json_safe_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {str(key): json_safe_value(value) for key, value in data.items()}


def normalize_vector(vector: Any) -> list[float]:
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    if isinstance(vector, list) and vector and isinstance(vector[0], list):
        vector = vector[0]
    return [float(item) for item in vector]


def report_progress(
    callback: ProgressCallback | None,
    stage: str,
    done: int,
    total: int,
    message: str,
) -> None:
    """Report best-effort progress without allowing UI callbacks to break imports."""

    if callback is None:
        return
    try:
        callback(stage, done, total, message)
    except Exception:
        logger.warning("RAG progress callback failed", exc_info=True)


def prepare_document_chunks(
    document_id: str,
    segments: Sequence[DocumentSegment],
    rag_namespace: str,
    split_text: Callable[[str], list[str]],
    embed_text: Callable[[str], list[float]],
    id_for_chunk: Callable[[str, str, int], str] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> list[PreparedChunk]:
    if not document_id:
        raise ValueError("document_id is required")

    id_for_chunk = id_for_chunk or qdrant_point_id
    pending: list[tuple[dict[str, Any], str]] = []

    for segment in segments:
        if not segment.content or not segment.content.strip():
            continue

        segment_metadata = json_safe_dict(segment.metadata or {})
        for chunk_text in split_text(segment.content):
            chunk_text = str(chunk_text).strip()
            if not chunk_text:
                continue

            pending.append((segment_metadata, chunk_text))

    prepared: list[PreparedChunk] = []
    total_chunks = len(pending)
    for chunk_index, (segment_metadata, chunk_text) in enumerate(pending):
        chunk_id = id_for_chunk(rag_namespace, document_id, chunk_index)
        now = utc_now_iso()
        metadata = {
                "memory_id": chunk_id,
                "document_id": document_id,
                "chunk_index": chunk_index,
                "content": chunk_text,
                "memory_type": "rag_chunk",
                "is_rag_data": True,
                "data_source": "rag_pipeline",
                "rag_namespace": rag_namespace,
                "created_at": now,
                "updated_at": now,
                "document_version": 1,
                **segment_metadata,
            }

        prepared.append(
            PreparedChunk(
                id=chunk_id,
                document_id=document_id,
                content=chunk_text,
                vector=normalize_vector(embed_text(chunk_text)),
                metadata=json_safe_dict(metadata),
            )
        )
        report_progress(
            progress_callback,
            "embedding",
            chunk_index + 1,
            total_chunks,
            "embedding",
        )

    return prepared
