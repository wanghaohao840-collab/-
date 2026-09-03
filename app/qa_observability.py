from __future__ import annotations

import logging
from collections import Counter
from threading import Lock
from typing import Mapping, Protocol


ALLOWED_QA_EVENTS = frozenset(
    {
        "qa_ask_success",
        "qa_ask_failure",
        "qa_idempotent_hit",
        "qa_busy",
        "qa_retry",
        "qa_cancel",
        "qa_summary_fallback",
        "qa_deletion_retry",
    }
)


class QaTelemetry(Protocol):
    def record(
        self,
        event: str,
        *,
        duration_ms: float | None = None,
        error_code: str | None = None,
        conversation_id: str | None = None,
        message_id: str | None = None,
        job_id: str | None = None,
    ) -> None: ...

    def snapshot(self) -> Mapping[str, int]: ...


class InProcessQaTelemetry:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._counts: Counter[str] = Counter()
        self._lock = Lock()
        self._logger = logger or logging.getLogger(__name__)

    def record(
        self,
        event: str,
        *,
        duration_ms: float | None = None,
        error_code: str | None = None,
        conversation_id: str | None = None,
        message_id: str | None = None,
        job_id: str | None = None,
    ) -> None:
        if event not in ALLOWED_QA_EVENTS:
            raise ValueError("unexpected QA telemetry event")
        with self._lock:
            self._counts[event] += 1
        self._logger.info(
            "qa_event",
            extra={
                "qa_event": event,
                "duration_ms": duration_ms,
                "error_code": error_code,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "job_id": job_id,
            },
        )

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)
