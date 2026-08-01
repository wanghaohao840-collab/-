from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ExtractedGraph:
    document: dict[str, Any]
    chapters: list[dict[str, Any]] = field(default_factory=list)
    chunks: list[dict[str, Any]] = field(default_factory=list)
    concepts: list[dict[str, Any]] = field(default_factory=list)
    knowledge_points: list[dict[str, Any]] = field(default_factory=list)
    persons: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    llm_attempt_count: int = 0

    def to_store_payload(self) -> dict[str, Any]:
        return {
            "document": dict(self.document),
            "chapters": [dict(value) for value in self.chapters],
            "chunks": [dict(value) for value in self.chunks],
            "concepts": [dict(value) for value in self.concepts],
            "knowledge_points": [
                dict(value) for value in self.knowledge_points
            ],
            "persons": [dict(value) for value in self.persons],
            "relations": [
                {
                    "source_id": value["source_id"],
                    "target_id": value["target_id"],
                    "type": value["type"],
                    "properties": dict(value.get("properties") or {}),
                }
                for value in self.relations
            ],
        }


def graph_response(
    *,
    success: bool,
    document_id: str,
    status: str,
    data: Optional[dict[str, Any]] = None,
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
    page: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "success": bool(success),
        "document_id": str(document_id),
        "status": str(status),
        "data": dict(data or {}),
        "error": (
            None
            if success
            else {
                "type": str(error_type or "GraphError"),
                "message": str(error_message or "Graph operation failed"),
            }
        ),
        "page": dict(page) if page is not None else None,
    }
