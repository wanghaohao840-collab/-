from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DocumentSegment:
    content: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PreparedChunk:
    id: str
    document_id: str
    content: str
    vector: list[float]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class RAGActionResult:
    action: str
    success: bool
    message: str
    data: dict[str, Any]
    error: str = ""
    error_code: str = ""
    retryable: bool = False
