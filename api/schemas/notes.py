from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.note_models import Note, NotePage, NoteSource


class NoteSourceSelectorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["qa_answer", "qa_citation"]
    qa_message_id: str = Field(min_length=1, max_length=128)
    citation_id: str | None = Field(default=None, min_length=1, max_length=128)


class NoteCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body_markdown: str = Field(min_length=1, max_length=20_000)
    concept: str | None = Field(default=None, max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=10)
    client_request_id: str = Field(min_length=1, max_length=128)
    source: NoteSourceSelectorRequest | None = None


class NoteUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body_markdown: str = Field(min_length=1, max_length=20_000)
    concept: str | None = Field(default=None, max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=10)
    expected_version: int = Field(ge=1)


class NoteDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class NoteClearRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: Literal["清空全部笔记"]


class NoteCapabilitiesResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool


class NoteSourceResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: Literal["qa_message", "qa_citation"]
    deleted: bool
    qa_thread_id: str | None
    qa_message_id: str | None
    citation_id: str | None
    document_id: str | None
    locator: dict[str, object] | None
    title_snapshot: str | None
    excerpt_snapshot: str | None
    created_at: str
    source_deleted_at: str | None


class NoteResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    body_markdown: str
    concept: str | None
    tags: list[str]
    sources: list[NoteSourceResponse]
    version: int
    projection_state: Literal["pending", "ready", "failed"]
    created_at: str
    updated_at: str
    deleted_at: str | None


class NoteListSourceResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: Literal["qa_message", "qa_citation"]
    deleted: bool
    document_id: str | None
    source_deleted_at: str | None


class NoteListItemResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    body_markdown: str
    concept: str | None
    tags: list[str]
    sources: list[NoteListSourceResponse]
    version: int
    projection_state: Literal["pending", "ready", "failed"]
    created_at: str
    updated_at: str
    deleted_at: str | None


class NotePageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[NoteListItemResponse]
    next_cursor: str | None


class NoteClearResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    cleared_count: int


class NoteProjectionRetryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    requeued_count: int


def source_response(source: NoteSource) -> NoteSourceResponse:
    return NoteSourceResponse(
        id=source.id,
        kind=source.kind,
        deleted=source.deleted,
        qa_thread_id=source.qa_thread_id,
        qa_message_id=source.qa_message_id,
        citation_id=source.citation_id,
        document_id=source.document_id,
        locator=dict(source.locator) if source.locator is not None else None,
        title_snapshot=source.title_snapshot,
        excerpt_snapshot=source.excerpt_snapshot,
        created_at=source.created_at,
        source_deleted_at=source.source_deleted_at,
    )


def note_response(note: Note) -> NoteResponse:
    return NoteResponse(
        id=note.id,
        body_markdown=note.body_markdown,
        concept=note.concept,
        tags=list(note.tags),
        sources=[source_response(source) for source in note.sources],
        version=note.version,
        projection_state=note.projection_state,
        created_at=note.created_at,
        updated_at=note.updated_at,
        deleted_at=note.deleted_at,
    )


def note_page_response(page: NotePage) -> NotePageResponse:
    return NotePageResponse(
        items=[
            NoteListItemResponse(
                id=note.id,
                body_markdown=note.body_markdown,
                concept=note.concept,
                tags=list(note.tags),
                sources=[
                    NoteListSourceResponse(
                        id=source.id,
                        kind=source.kind,
                        deleted=source.deleted,
                        document_id=source.document_id,
                        source_deleted_at=source.source_deleted_at,
                    )
                    for source in note.sources
                ],
                version=note.version,
                projection_state=note.projection_state,
                created_at=note.created_at,
                updated_at=note.updated_at,
                deleted_at=note.deleted_at,
            )
            for note in page.items
        ],
        next_cursor=page.next_cursor,
    )
