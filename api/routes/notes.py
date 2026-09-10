from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import JSONResponse

from api.dependencies import (
    get_csrf_validated_session,
    get_current_session,
    get_note_service,
)
from api.errors import error_response
from app.document_search import SearchChunkLocator, DocumentSearchValidationError
from api.schemas.notes import (
    NoteCapabilitiesResponse,
    NoteClearRequest,
    NoteClearResponse,
    NoteCreateRequest,
    NoteDeleteRequest,
    NotePageResponse,
    NoteProjectionRetryResponse,
    NoteResponse,
    NoteUpdateRequest,
    note_page_response,
    note_response,
)
from app.note_models import (
    NoteError,
    NoteFilters,
    NoteIdempotencyConflict,
    NoteNotFoundError,
    NoteProjectionUnavailable,
    NoteSourceDeletingError,
    NoteSourceNotFoundError,
    NoteValidationError,
    NoteVersionConflict,
    NoteSourceSelector,
    DocumentChunkSourceSelector,
    NoteSourceUnavailableError,
    validate_note_input,
)
from app.note_service import NoteService, _service_request_digest
from app.session import UserSession, InvalidSessionError


router = APIRouter(prefix="/api/v1/notes", tags=["notes"])


def _disabled(request: Request) -> JSONResponse | None:
    if request.app.state.api_config.notes_route_enabled:
        return None
    return error_response(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "NOTES_ROUTE_DISABLED",
        "学习笔记功能当前未启用",
    )


def _domain_error(error: Exception) -> JSONResponse:
    if isinstance(error, InvalidSessionError):
        return error_response(401, "invalid_session", "会话无效或已过期，请重新登录")
    if isinstance(error, NoteSourceUnavailableError):
        return error_response(503, error.code, "笔记来源暂时无法验证，请稍后重试", retryable=True)
    if isinstance(error, DocumentSearchValidationError):
        return error_response(422, "NOTE_VALIDATION_ERROR", "笔记来源参数无效")
    if isinstance(error, NoteNotFoundError):
        code = "NOTE_SOURCE_NOT_FOUND" if isinstance(error, NoteSourceNotFoundError) else "NOTE_NOT_FOUND"
        return error_response(status.HTTP_404_NOT_FOUND, code, "笔记资源不存在")
    if isinstance(error, NoteVersionConflict):
        return error_response(status.HTTP_409_CONFLICT, error.code, "笔记版本已变化，请重新加载")
    if isinstance(error, NoteSourceDeletingError):
        return error_response(status.HTTP_409_CONFLICT, error.code, "笔记来源正在删除")
    if isinstance(error, NoteIdempotencyConflict):
        return error_response(status.HTTP_409_CONFLICT, error.code, "请求标识已用于其他笔记内容")
    if isinstance(error, NoteProjectionUnavailable):
        return error_response(status.HTTP_503_SERVICE_UNAVAILABLE, error.code, "笔记投影暂时不可用", retryable=True)
    if isinstance(error, NoteValidationError):
        return error_response(status.HTTP_422_UNPROCESSABLE_CONTENT, error.code, "笔记请求无效")
    if isinstance(error, NoteError):
        return error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "NOTE_OPERATION_FAILED", "笔记操作失败，请稍后重试", retryable=True)
    return error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "NOTE_OPERATION_FAILED", "笔记操作失败，请稍后重试", retryable=True)


@router.get("/capabilities", response_model=NoteCapabilitiesResponse)
def capabilities(
    request: Request,
    _session: Annotated[UserSession, Depends(get_current_session)],
) -> NoteCapabilitiesResponse:
    return NoteCapabilitiesResponse(enabled=request.app.state.api_config.notes_route_enabled)


@router.get("", response_model=NotePageResponse)
def list_notes(
    request: Request,
    _session: Annotated[UserSession, Depends(get_current_session)],
    service: Annotated[NoteService, Depends(get_note_service)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    query: str | None = None,
    tags: str | None = None,
    source_kind: str | None = None,
    sort: str = "updated_desc",
) -> NotePageResponse | JSONResponse:
    if disabled := _disabled(request):
        return disabled
    try:
        normalized_tags = tuple(item.strip() for item in (tags or "").split(",") if item.strip())
        if len(normalized_tags) > 10 or any(len(item) > 32 for item in normalized_tags):
            raise NoteValidationError("tags are invalid")
        return note_page_response(service.list_notes(_session, NoteFilters(
            cursor=cursor, limit=limit, query=query, tags=normalized_tags,
            source_kind=source_kind, sort=sort,  # type: ignore[arg-type]
        )))
    except Exception as error:
        return _domain_error(error)


@router.post("", response_model=NoteResponse, status_code=status.HTTP_201_CREATED)
def create_note(
    body: NoteCreateRequest,
    request: Request,
    _session: Annotated[UserSession, Depends(get_csrf_validated_session)],
    service: Annotated[NoteService, Depends(get_note_service)],
) -> NoteResponse | JSONResponse:
    if disabled := _disabled(request):
        return disabled
    try:
        if body.source is not None and body.source.kind == "document_chunk":
            source = DocumentChunkSourceSelector(
                kind="document_chunk", locator=SearchChunkLocator(**body.source.locator.model_dump()),
            )
        else:
            source = NoteSourceSelector(
                kind=body.source.kind, qa_message_id=body.source.qa_message_id,
                citation_id=body.source.citation_id,
            ) if body.source is not None else None
        replay = False
        repository = getattr(service, "repository", None)
        if repository is not None:
            normalized_body, normalized_concept, normalized_tags = validate_note_input(
                body.body_markdown, body.concept, tuple(body.tags)
            )
            replay = repository.get_by_client_request_id(
                _session.user_id,
                body.client_request_id,
                _service_request_digest(normalized_body, normalized_concept, normalized_tags, source),
            ) is not None
        result = note_response(service.create(
            _session, body_markdown=body.body_markdown, concept=body.concept,
            tags=tuple(body.tags), client_request_id=body.client_request_id, source=source,
        ))
        if replay:
            return JSONResponse(status_code=status.HTTP_200_OK, content=result.model_dump(mode="json"))
        return result
    except Exception as error:
        return _domain_error(error)


@router.get("/{note_id}", response_model=NoteResponse)
def get_note(
    note_id: str,
    request: Request,
    _session: Annotated[UserSession, Depends(get_current_session)],
    service: Annotated[NoteService, Depends(get_note_service)],
) -> NoteResponse | JSONResponse:
    if disabled := _disabled(request):
        return disabled
    try:
        return note_response(service.get_note(_session, note_id))
    except Exception as error:
        return _domain_error(error)


@router.patch("/{note_id}", response_model=NoteResponse)
def update_note(
    note_id: str,
    body: NoteUpdateRequest,
    request: Request,
    _session: Annotated[UserSession, Depends(get_csrf_validated_session)],
    service: Annotated[NoteService, Depends(get_note_service)],
) -> NoteResponse | JSONResponse:
    if disabled := _disabled(request):
        return disabled
    try:
        return note_response(service.update(
            _session, note_id, expected_version=body.expected_version,
            body_markdown=body.body_markdown, concept=body.concept, tags=tuple(body.tags),
        ))
    except Exception as error:
        return _domain_error(error)


@router.delete("/{note_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    note_id: str,
    request: Request,
    _session: Annotated[UserSession, Depends(get_csrf_validated_session)],
    service: Annotated[NoteService, Depends(get_note_service)],
    body: NoteDeleteRequest | None = None,
) -> Response | JSONResponse:
    if disabled := _disabled(request):
        return disabled
    try:
        expected_version = body.expected_version if body is not None else _expected_version(request)
        service.delete(_session, note_id, expected_version=expected_version)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as error:
        return _domain_error(error)


def _expected_version(request: Request) -> int:
    value = request.query_params.get("expected_version")
    if value is None:
        raise NoteValidationError("expected_version is required")
    try:
        version = int(value)
    except ValueError as exc:
        raise NoteValidationError("expected_version is invalid") from exc
    if version < 1:
        raise NoteValidationError("expected_version is invalid")
    return version


@router.post("/clear", response_model=NoteClearResponse)
def clear_notes(
    body: NoteClearRequest,
    request: Request,
    _session: Annotated[UserSession, Depends(get_csrf_validated_session)],
    service: Annotated[NoteService, Depends(get_note_service)],
) -> NoteClearResponse | JSONResponse:
    if disabled := _disabled(request):
        return disabled
    try:
        return NoteClearResponse(cleared_count=service.clear_all(_session, confirmation=body.confirmation))
    except Exception as error:
        return _domain_error(error)


@router.post("/projections/retry", response_model=NoteProjectionRetryResponse)
def retry_projections(
    request: Request,
    _session: Annotated[UserSession, Depends(get_csrf_validated_session)],
    service: Annotated[NoteService, Depends(get_note_service)],
) -> NoteProjectionRetryResponse | JSONResponse:
    if disabled := _disabled(request):
        return disabled
    try:
        return NoteProjectionRetryResponse(requeued_count=service.retry_projection(_session))
    except Exception as error:
        return _domain_error(error)
