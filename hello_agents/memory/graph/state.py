from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GraphStateCorruptionError(RuntimeError):
    """Raised instead of silently discarding an invalid graph manifest."""


def sanitize_error(
    message: Any,
    *,
    secrets: Optional[Iterable[str]] = None,
) -> str:
    text = str(message or "")
    text = re.sub(
        r"\b(?:neo4j|bolt)(?:\+s|\+ssc)?://[^\s]+",
        "[REDACTED_NEO4J_URI]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?i)\b(?:password|token|api[_-]?key)\s*[:=]\s*\S+",
        "[REDACTED_SECRET]",
        text,
    )
    for secret in secrets or ():
        secret = str(secret or "")
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = " ".join(text.split())
    if len(text) > 500:
        text = text[:499] + "…"
    return text


class GraphStateRepository:
    """Atomic JSON manifest for graph lifecycle state."""

    VALID_STATUSES = {
        "pending",
        "building",
        "ready",
        "failed",
        "cleanup_pending",
        "deleted",
    }

    def __init__(
        self,
        path: str | Path,
        *,
        secrets: Optional[Iterable[str]] = None,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._secrets = tuple(str(value) for value in (secrets or ()) if value)
        self._lock = RLock()
        if not self.path.exists():
            self._write({"version": 1, "documents": {}})

    def _load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "documents": {}}
        except json.JSONDecodeError as error:
            raise GraphStateCorruptionError(
                "Graph state manifest contains invalid JSON"
            ) from error
        if not isinstance(value, dict) or not isinstance(
            value.get("documents"), dict
        ):
            raise GraphStateCorruptionError(
                "Graph state manifest has an invalid schema"
            )
        return value

    def _write(self, value: dict[str, Any]) -> None:
        temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def get(self, document_id: str) -> Optional[dict[str, Any]]:
        document_id = str(document_id or "").strip()
        if not document_id:
            raise ValueError("document_id is required")
        with self._lock:
            value = self._load()["documents"].get(document_id)
            return dict(value) if value else None

    def upsert(
        self,
        document_id: str,
        *,
        status: str,
        build_id: Optional[str] = None,
        attempt_count: Optional[int] = None,
        llm_attempt_count: Optional[int] = None,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        updated_at: Optional[str] = None,
    ) -> dict[str, Any]:
        document_id = str(document_id or "").strip()
        if not document_id:
            raise ValueError("document_id is required")
        status = str(status or "").strip()
        if status not in self.VALID_STATUSES:
            raise ValueError(f"invalid graph status: {status}")
        with self._lock:
            manifest = self._load()
            existing = dict(manifest["documents"].get(document_id) or {})
            value = {
                "document_id": document_id,
                "build_id": (
                    str(build_id) if build_id is not None
                    else existing.get("build_id")
                ),
                "status": status,
                "error_type": (
                    str(error_type) if error_type else None
                ),
                "error_message": (
                    sanitize_error(error_message, secrets=self._secrets)
                    if error_message
                    else None
                ),
                "attempt_count": (
                    int(attempt_count)
                    if attempt_count is not None
                    else int(existing.get("attempt_count", 0))
                ),
                "llm_attempt_count": (
                    int(llm_attempt_count)
                    if llm_attempt_count is not None
                    else int(existing.get("llm_attempt_count", 0))
                ),
                "updated_at": updated_at or utc_now(),
            }
            manifest["documents"][document_id] = value
            self._write(manifest)
            return dict(value)

    def list_by_status(self, status: str) -> list[dict[str, Any]]:
        if status not in self.VALID_STATUSES:
            raise ValueError(f"invalid graph status: {status}")
        with self._lock:
            values = [
                dict(value)
                for value in self._load()["documents"].values()
                if value.get("status") == status
            ]
        values.sort(key=lambda value: value["document_id"])
        return values
