from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal


ControlAction = Literal["pause", "cancel"]
ControlCheckpoint = Callable[[str], None]
ProgressCallback = Callable[[str, int, int, str], None]


class ImportControlSignal(RuntimeError):
    retryable = False

    def __init__(self, action: ControlAction):
        super().__init__(action)
        self.action = action


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
