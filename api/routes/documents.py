from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status

from api.dependencies import (
    get_csrf_validated_session,
    get_current_session,
    get_document_library_service,
    get_session_token,
)
from api.errors import error_response
from api.schemas.documents import DocumentListResponse, document_response
from app.document_library import (
    DocumentDeleteFailedError,
    DocumentImportActiveError,
    DocumentLibraryService,
    DocumentNotFoundError,
)
from app.session import UserSession


router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


@router.get("", response_model=DocumentListResponse)
def list_documents(
    request: Request,
    _session: Annotated[UserSession, Depends(get_current_session)],
    service: Annotated[
        DocumentLibraryService,
        Depends(get_document_library_service),
    ],
) -> DocumentListResponse:
    return DocumentListResponse(
        items=[
            document_response(item)
            for item in service.list_documents(get_session_token(request))
        ]
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: UUID,
    request: Request,
    _session: Annotated[UserSession, Depends(get_csrf_validated_session)],
    service: Annotated[
        DocumentLibraryService,
        Depends(get_document_library_service),
    ],
) -> Response:
    try:
        service.delete_document(get_session_token(request), str(document_id))
    except DocumentNotFoundError:
        return error_response(
            status.HTTP_404_NOT_FOUND,
            "document_not_found",
            "文档不存在",
        )
    except DocumentImportActiveError:
        return error_response(
            status.HTTP_409_CONFLICT,
            "document_import_active",
            "文档仍在导入中，请稍后重试",
        )
    except DocumentDeleteFailedError:
        return error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "document_delete_failed",
            "文档删除失败，请重试",
            retryable=True,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
