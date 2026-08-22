from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.document_library import DocumentLibraryItem


class DocumentResponse(BaseModel):
    document_id: str
    name: str
    file_suffix: str
    size_bytes: int | None
    loaded_at: str | None
    status: Literal["ready"]


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]


def document_response(item: DocumentLibraryItem) -> DocumentResponse:
    return DocumentResponse(
        document_id=item.document_id,
        name=item.name,
        file_suffix=item.file_suffix,
        size_bytes=item.size_bytes,
        loaded_at=item.loaded_at,
        status=item.status,
    )
