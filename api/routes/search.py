from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from api.dependencies import get_csrf_validated_session, get_document_search_service, get_session_token
from api.errors import error_response
from api.schemas.search import SearchRequest, SearchResponse
from app.document_search import (
    DocumentSearchService, DocumentSearchRequest, DocumentSearchError,
    DocumentSearchValidationError, DocumentSearchScopeError,
    DocumentSearchBusyError, DocumentSearchUnavailableError,
)
from app.session import UserSession


router = APIRouter(prefix="/api/v1/search", tags=["search"])


class SearchNoStoreMiddleware:
    """Also protect dependency/validation failures, before the endpoint runs."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def private_send(message):
            if message["type"] == "http.response.start":
                headers = [(key, value) for key, value in message.get("headers", []) if key.lower() != b"cache-control"]
                message = {**message, "headers": [*headers, (b"cache-control", b"no-store")]}
            await send(message)
        path = scope.get("path", "")
        is_search = path == "/api/v1/search" or path.startswith("/api/v1/search/")
        await self.app(scope, receive, private_send if scope["type"] == "http" and is_search else send)


@router.post("", response_model=SearchResponse)
def search(
    body: SearchRequest, request: Request, response: Response,
    _session: Annotated[UserSession, Depends(get_csrf_validated_session)],
    service: Annotated[DocumentSearchService, Depends(get_document_search_service)],
):
    response.headers["Cache-Control"] = "no-store"
    try:
        return service.search(get_session_token(request), DocumentSearchRequest(
            body.query, tuple(body.document_ids), body.limit,
        ))
    except (DocumentSearchError, DocumentSearchValidationError) as exc:
        status = 409
        message = "文档范围或来源已变化，请重新选择后检索"
        if isinstance(exc, DocumentSearchValidationError):
            status, message = 422, "检索参数无效"
        elif isinstance(exc, DocumentSearchScopeError):
            status, message = 404, "所选文档不可用"
        elif isinstance(exc, DocumentSearchBusyError):
            status, message = 429, "检索正在进行，请稍后重试"
        elif isinstance(exc, DocumentSearchUnavailableError):
            status, message = 503, "检索服务暂不可用，请稍后重试"
        failure = error_response(status, exc.code, message, retryable=exc.retryable)
        failure.headers["Cache-Control"] = "no-store"
        if exc.retryable:
            failure.headers["Retry-After"] = "2"
        return failure
