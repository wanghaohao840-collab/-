from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request

from api.config import ApiConfig
from app.session import SessionRegistry, UserSession


def get_session_registry(request: Request) -> SessionRegistry:
    return request.app.state.services.session_registry


def get_session_token(request: Request) -> str | None:
    config: ApiConfig = request.app.state.api_config
    return request.cookies.get(config.cookie_name)


def get_current_session(
    request: Request,
    registry: Annotated[SessionRegistry, Depends(get_session_registry)],
) -> UserSession:
    return registry.get_session(get_session_token(request))


def get_csrf_validated_session(
    request: Request,
    registry: Annotated[SessionRegistry, Depends(get_session_registry)],
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> UserSession:
    return registry.validate_csrf(get_session_token(request), csrf_token)
