from __future__ import annotations

from typing import Annotated, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse

from api.dependencies import (
    get_csrf_validated_session,
    get_current_session,
    get_import_service,
    get_session_token,
)
from api.errors import error_response
from api.schemas.imports import ImportBatchResponse, import_batch_response
from app.import_repository import InvalidImportTransition
from app.import_service import (
    ImportBatchCommitConfirmationError,
    ImportLimitError,
    ImportStagingCleanupError,
    ImportTaskNotCancellableError,
    ImportTaskService,
    ImportUpload,
)
from app.session import UserSession


router = APIRouter(prefix="/api/v1/imports", tags=["imports"])


def _submission_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, ImportLimitError):
        code = {
            "import_no_files": "import_batch_empty",
            "import_too_many_files": "import_too_many_files",
            "import_file_too_large": "import_file_too_large",
            "import_batch_too_large": "import_batch_too_large",
        }.get(exc.code, "import_stage_failed")
        status_code = {
            "import_batch_empty": status.HTTP_422_UNPROCESSABLE_CONTENT,
            "import_too_many_files": status.HTTP_422_UNPROCESSABLE_CONTENT,
            "import_file_too_large": status.HTTP_413_CONTENT_TOO_LARGE,
            "import_batch_too_large": status.HTTP_413_CONTENT_TOO_LARGE,
        }.get(code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        return error_response(
            status_code,
            code,
            {
                "import_batch_empty": "请至少选择一个文件",
                "import_too_many_files": "单次导入的文件数量过多",
                "import_file_too_large": "单个文件超过大小限制",
                "import_batch_too_large": "导入批次超过大小限制",
            }.get(code, "文件暂存失败，请重试"),
            retryable=code == "import_stage_failed",
        )
    if isinstance(exc, ValueError) and str(exc).startswith(
        "Unsupported document type:"
    ):
        return error_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "unsupported_document_type",
            "不支持该文档类型",
        )
    return error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "import_stage_failed",
        "文件暂存失败，请重试",
        retryable=True,
    )


@router.post("", response_model=ImportBatchResponse, status_code=status.HTTP_202_ACCEPTED)
def submit_imports(
    request: Request,
    _session: Annotated[UserSession, Depends(get_csrf_validated_session)],
    service: Annotated[ImportTaskService, Depends(get_import_service)],
    files: Annotated[list[UploadFile] | None, File()] = None,
):
    if not files:
        return error_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "import_batch_empty",
            "请至少选择一个文件",
        )
    uploads = [
        ImportUpload(original_name=item.filename or "", stream=item.file)
        for item in files
    ]
    try:
        summary = service.submit_uploads(get_session_token(request), uploads)
    except (
        ImportLimitError,
        ImportStagingCleanupError,
        ImportBatchCommitConfirmationError,
        ValueError,
    ) as exc:
        return _submission_error(exc)
    return import_batch_response(summary)


@router.get("", response_model=list[ImportBatchResponse])
def list_imports(
    request: Request,
    _session: Annotated[UserSession, Depends(get_current_session)],
    service: Annotated[ImportTaskService, Depends(get_import_service)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[ImportBatchResponse]:
    return [
        import_batch_response(summary)
        for summary in service.list_batches(get_session_token(request), limit=limit)
    ]


@router.get("/{batch_id}", response_model=ImportBatchResponse)
def get_import(
    batch_id: UUID,
    request: Request,
    _session: Annotated[UserSession, Depends(get_current_session)],
    service: Annotated[ImportTaskService, Depends(get_import_service)],
):
    try:
        summary = service.get_batch(get_session_token(request), str(batch_id))
    except KeyError:
        return error_response(
            status.HTTP_404_NOT_FOUND,
            "import_batch_not_found",
            "导入批次不存在",
        )
    return import_batch_response(summary)


@router.post(
    "/{batch_id}/tasks/{task_id}/retry",
    response_model=ImportBatchResponse,
)
def retry_import_task(
    batch_id: UUID,
    task_id: UUID,
    request: Request,
    _session: Annotated[UserSession, Depends(get_csrf_validated_session)],
    service: Annotated[ImportTaskService, Depends(get_import_service)],
):
    try:
        summary = service.retry_task(
            get_session_token(request),
            str(task_id),
            expected_batch_id=str(batch_id),
        )
    except KeyError:
        return error_response(
            status.HTTP_404_NOT_FOUND,
            "import_task_not_found",
            "导入任务不存在",
        )
    except InvalidImportTransition:
        return error_response(
            status.HTTP_409_CONFLICT,
            "import_not_retryable",
            "该导入任务当前不可重试",
        )
    return import_batch_response(summary)


@router.post("/{batch_id}/retry-failed", response_model=ImportBatchResponse)
def retry_failed_imports(
    batch_id: UUID,
    request: Request,
    _session: Annotated[UserSession, Depends(get_csrf_validated_session)],
    service: Annotated[ImportTaskService, Depends(get_import_service)],
):
    try:
        summary = service.retry_failed_in_batch(
            get_session_token(request), str(batch_id)
        )
    except KeyError:
        return error_response(
            status.HTTP_404_NOT_FOUND,
            "import_batch_not_found",
            "导入批次不存在",
        )
    return import_batch_response(summary)


@router.post(
    "/{batch_id}/tasks/{task_id}/cancel",
    response_model=ImportBatchResponse,
)
def cancel_import_task(
    batch_id: UUID,
    task_id: UUID,
    request: Request,
    _session: Annotated[UserSession, Depends(get_csrf_validated_session)],
    service: Annotated[ImportTaskService, Depends(get_import_service)],
):
    try:
        summary = service.cancel_task(
            get_session_token(request), str(batch_id), str(task_id)
        )
    except KeyError:
        return error_response(
            status.HTTP_404_NOT_FOUND,
            "import_task_not_found",
            "导入任务不存在",
        )
    except ImportTaskNotCancellableError:
        return error_response(
            status.HTTP_409_CONFLICT,
            "import_not_cancellable",
            "该导入任务当前不可取消",
        )
    return import_batch_response(summary)
