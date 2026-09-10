from __future__ import annotations

import base64
import binascii
import json
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

from app.document_search import SearchChunkLocator, DocumentSearchValidationError


ProjectionState = Literal["pending", "ready", "failed"]
SourceKind = Literal["qa_message", "qa_citation", "document_chunk"]
ProjectionOperation = Literal["upsert", "delete"]
ProjectionTaskStatus = Literal["queued", "running", "failed", "completed"]


class NoteError(RuntimeError):
    code = "NOTE_ERROR"


class NoteValidationError(ValueError):
    code = "NOTE_VALIDATION_ERROR"


class NoteNotFoundError(NoteError):
    code = "NOTE_NOT_FOUND"


class NoteSourceNotFoundError(NoteNotFoundError):
    code = "NOTE_SOURCE_NOT_FOUND"


class NoteSourceDeletingError(NoteError):
    code = "NOTE_SOURCE_DELETING"


class NoteSourceUnavailableError(NoteError):
    code = "NOTE_SOURCE_UNAVAILABLE"


class NoteVersionConflict(NoteError):
    code = "NOTE_VERSION_CONFLICT"

    def __init__(self, note_id: str, current_version: int | None = None) -> None:
        super().__init__(note_id)
        self.note_id = note_id
        self.current_version = current_version


class NoteIdempotencyConflict(NoteError):
    code = "NOTE_IDEMPOTENCY_CONFLICT"


class NoteProjectionUnavailable(NoteError):
    code = "NOTE_PROJECTION_UNAVAILABLE"


@dataclass(frozen=True)
class NewNoteSource:
    kind: SourceKind
    qa_thread_id: str | None
    qa_message_id: str | None
    citation_id: str | None
    document_id: str | None
    locator: Mapping[str, object] | None
    title_snapshot: str | None
    excerpt_snapshot: str | None

    def __post_init__(self) -> None:
        if self.kind not in {"qa_message", "qa_citation", "document_chunk"}:
            raise NoteValidationError("source kind is invalid")
        if self.kind == "document_chunk":
            if any(value is not None for value in (self.qa_thread_id, self.qa_message_id, self.citation_id)):
                raise NoteValidationError("QA fields are not allowed for document sources")
            if not isinstance(self.locator, dict):
                raise NoteValidationError("document source locator is invalid")
            locator = self.locator
            if type(locator.get("chunk_index")) is not int:
                raise NoteValidationError("document source chunk index is invalid")
            try:
                SearchChunkLocator(self.document_id, locator.get("chunk_id"), locator.get("chunk_index"), locator.get("content_sha256"))
            except (DocumentSearchValidationError, TypeError) as exc:
                raise NoteValidationError("document source locator is invalid") from exc
            if self.excerpt_snapshot is None or len(self.excerpt_snapshot) > 1200:
                raise NoteValidationError("document source excerpt is invalid")
        object.__setattr__(self, "locator", freeze_locator(self.locator))


@dataclass(frozen=True)
class NoteSource:
    id: str
    kind: SourceKind
    deleted: bool
    qa_thread_id: str | None
    qa_message_id: str | None
    citation_id: str | None
    document_id: str | None
    locator: Mapping[str, object] | None
    title_snapshot: str | None
    excerpt_snapshot: str | None
    created_at: str
    source_deleted_at: str | None


@dataclass(frozen=True)
class Note:
    id: str
    user_id: str
    body_markdown: str
    concept: str | None
    tags: tuple[str, ...]
    sources: tuple[NoteSource, ...]
    version: int
    projection_state: ProjectionState
    created_at: str
    updated_at: str
    deleted_at: str | None


@dataclass(frozen=True)
class NotePage:
    items: tuple[Note, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class NoteProjectionTask:
    id: str
    user_id: str
    note_id: str
    note_version: int
    operation: ProjectionOperation
    status: ProjectionTaskStatus
    attempt_count: int
    available_at: str
    lease_owner: str | None
    lease_expires_at: str | None
    last_error_code: str | None
    created_at: str
    finished_at: str | None


@dataclass(frozen=True)
class NoteMigrationResult:
    imported_count: int
    skipped_count: int
    matched_legacy_memory_count: int
    unmatched_legacy_memory_count: int


@dataclass(frozen=True)
class NoteFilters:
    cursor: str | None = None
    limit: int = 20
    query: str | None = None
    tags: tuple[str, ...] = ()
    source_kind: SourceKind | None = None
    sort: Literal["updated_desc"] = "updated_desc"

    def as_repository_kwargs(self) -> dict[str, object]:
        return {
            "cursor": self.cursor,
            "limit": self.limit,
            "query": self.query,
            "tags": self.tags,
            "source_kind": self.source_kind,
            "sort": self.sort,
        }


@dataclass(frozen=True)
class NoteSourceSelector:
    kind: Literal["qa_answer", "qa_citation"]
    qa_message_id: str
    citation_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"qa_answer", "qa_citation"}:
            raise NoteValidationError("source kind is invalid")
        if not str(self.qa_message_id or "").strip():
            raise NoteValidationError("qa_message_id is required")
        if self.kind == "qa_citation" and not str(self.citation_id or "").strip():
            raise NoteValidationError("citation_id is required")
        if self.kind == "qa_answer" and self.citation_id is not None:
            raise NoteValidationError("citation_id is not allowed for an answer")


@dataclass(frozen=True)
class DocumentChunkSourceSelector:
    kind: Literal["document_chunk"]
    locator: SearchChunkLocator

    def __post_init__(self) -> None:
        if self.kind != "document_chunk" or not isinstance(self.locator, SearchChunkLocator):
            raise NoteValidationError("document source selector is invalid")


def normalize_tag(tag: str) -> tuple[str, str]:
    display = unicodedata.normalize("NFKC", str(tag or "")).strip()
    return display.casefold(), display


def validate_note_input(
    body_markdown: str,
    concept: str | None,
    tags: tuple[str, ...],
) -> tuple[str, str | None, tuple[str, ...]]:
    body = str(body_markdown or "").strip()
    if not 1 <= len(body) <= 20_000:
        raise NoteValidationError("body_markdown must contain 1 to 20000 characters")
    normalized_concept = str(concept).strip() if concept is not None else None
    normalized_concept = normalized_concept or None
    if normalized_concept and len(normalized_concept) > 120:
        raise NoteValidationError("concept must contain at most 120 characters")
    if not isinstance(tags, tuple):
        raise NoteValidationError("tags must be a tuple")
    by_key: dict[str, str] = {}
    for raw_tag in tags:
        key, display = normalize_tag(raw_tag)
        if not display:
            continue
        if len(display) > 32:
            raise NoteValidationError("tags must contain at most 10 values of 32 characters")
        by_key.setdefault(key, display)
    if len(by_key) > 10:
        raise NoteValidationError("tags must contain at most 10 values of 32 characters")
    return body, normalized_concept, tuple(by_key.values())


def freeze_locator(value: object) -> Mapping[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise NoteValidationError("source locator is invalid")
    return MappingProxyType(dict(value))


def encode_note_cursor(updated_at: str, note_id: str) -> str:
    payload = json.dumps([updated_at, note_id], separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_note_cursor(cursor: str) -> tuple[str, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(cursor + padding).decode())
        if (
            not isinstance(value, list)
            or len(value) != 2
            or any(not isinstance(item, str) or not item for item in value)
        ):
            raise ValueError
        return value[0], value[1]
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise NoteValidationError("cursor is invalid") from exc
