from __future__ import annotations

from typing import Annotated, Callable

from fastapi import APIRouter, Depends, Request, Response, status

from api.config import ApiConfig
from api.dependencies import (
    get_csrf_validated_session,
    get_current_session,
    get_session_registry,
    get_session_token,
)
from api.schemas.auth import Credentials, SessionResponse
from app.session import SessionRegistry, UserSession


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _session_response(session: UserSession) -> SessionResponse:
    return SessionResponse(
        username=session.username,
        csrf_token=session.csrf_token,
    )


def _set_session_cookie(
    request: Request,
    response: Response,
    token: str,
) -> None:
    config: ApiConfig = request.app.state.api_config
    response.set_cookie(
        key=config.cookie_name,
        value=token,
        httponly=True,
        secure=config.cookie_secure,
        samesite=config.cookie_samesite,
        path="/",
    )


def _authenticate(
    credentials: Credentials,
    request: Request,
    response: Response,
    registry: SessionRegistry,
    action: Callable[[str, str], str],
) -> SessionResponse:
    token = action(
        credentials.username,
        credentials.password.get_secret_value(),
    )
    session = registry.get_session(token)
    _set_session_cookie(request, response, token)
    return _session_response(session)


@router.post("/register", response_model=SessionResponse)
def register(
    credentials: Credentials,
    request: Request,
    response: Response,
    registry: Annotated[SessionRegistry, Depends(get_session_registry)],
) -> SessionResponse:
    return _authenticate(
        credentials,
        request,
        response,
        registry,
        registry.register,
    )


@router.post("/login", response_model=SessionResponse)
def login(
    credentials: Credentials,
    request: Request,
    response: Response,
    registry: Annotated[SessionRegistry, Depends(get_session_registry)],
) -> SessionResponse:
    return _authenticate(
        credentials,
        request,
        response,
        registry,
        registry.login,
    )


@router.get("/session", response_model=SessionResponse)
def session(
    current_session: Annotated[UserSession, Depends(get_current_session)],
) -> SessionResponse:
    return _session_response(current_session)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    registry: Annotated[SessionRegistry, Depends(get_session_registry)],
    _current_session: Annotated[
        UserSession,
        Depends(get_csrf_validated_session),
    ],
) -> Response:
    token = get_session_token(request)
    registry.logout(token)
    config: ApiConfig = request.app.state.api_config
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        key=config.cookie_name,
        path="/",
        secure=config.cookie_secure,
        httponly=True,
        samesite=config.cookie_samesite,
    )
    return response
