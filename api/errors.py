from __future__ import annotations

import logging
import secrets
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth import AuthError
from app.session import InvalidCsrfTokenError, InvalidSessionError


logger = logging.getLogger(__name__)


class UnexpectedExceptionBoundary:
    """Consume unexpected HTTP exceptions before the hosting server sees them."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        except Exception:
            response = _unexpected_error_response()
            await response(scope, receive, send)


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    field_errors: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "field_errors": dict(field_errors or {}),
            }
        },
    )


async def handle_auth_error(_request: Request, exc: AuthError) -> JSONResponse:
    if str(exc) == "Username already exists":
        return error_response(
            status.HTTP_409_CONFLICT,
            "username_exists",
            "用户名已存在",
        )
    if str(exc) == "Invalid username or password":
        return error_response(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_credentials",
            "用户名或密码错误",
        )
    return error_response(
        status.HTTP_400_BAD_REQUEST,
        "invalid_auth_input",
        "用户名或密码不符合要求",
    )


async def handle_invalid_session(
    _request: Request,
    _exc: InvalidSessionError,
) -> JSONResponse:
    return error_response(
        status.HTTP_401_UNAUTHORIZED,
        "invalid_session",
        "会话无效或已过期，请重新登录",
    )


async def handle_invalid_csrf(
    _request: Request,
    _exc: InvalidCsrfTokenError,
) -> JSONResponse:
    return error_response(
        status.HTTP_403_FORBIDDEN,
        "invalid_csrf_token",
        "请求校验失败，请刷新后重试",
    )


def _validation_field_errors(exc: RequestValidationError) -> dict[str, str]:
    localized_messages = {
        "username": "用户名格式无效",
        "password": "密码格式无效",
    }
    field_errors: dict[str, str] = {}
    for error in exc.errors():
        location = [str(part) for part in error.get("loc", ()) if part != "body"]
        field = ".".join(location) or "request"
        field_errors.setdefault(
            field,
            localized_messages.get(field, str(error.get("msg", "输入无效"))),
        )
    return field_errors


async def handle_validation_error(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    return error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "validation_error",
        "请修正表单错误",
        field_errors=_validation_field_errors(exc),
    )


async def handle_http_error(
    _request: Request,
    exc: HTTPException,
) -> JSONResponse:
    if exc.status_code == status.HTTP_404_NOT_FOUND:
        code, message = "not_found", "请求的资源不存在"
    elif exc.status_code == status.HTTP_405_METHOD_NOT_ALLOWED:
        code, message = "method_not_allowed", "请求方法不受支持"
    else:
        code, message = "http_error", "请求无法处理"
    return error_response(exc.status_code, code, message)


async def handle_unexpected_error(
    _request: Request,
    _exc: Exception,
) -> JSONResponse:
    return _unexpected_error_response()


def _unexpected_error_response() -> JSONResponse:
    request_id = secrets.token_urlsafe(12)
    logger.error("Unhandled API request; request_id=%s", request_id)
    return error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal_error",
        "服务暂时不可用，请稍后重试",
    )


def register_error_handlers(app: FastAPI) -> None:
    handlers: tuple[tuple[type[Exception], Any], ...] = (
        (AuthError, handle_auth_error),
        (InvalidSessionError, handle_invalid_session),
        (InvalidCsrfTokenError, handle_invalid_csrf),
        (RequestValidationError, handle_validation_error),
        (HTTPException, handle_http_error),
        (Exception, handle_unexpected_error),
    )
    for exception_type, handler in handlers:
        app.add_exception_handler(exception_type, handler)
