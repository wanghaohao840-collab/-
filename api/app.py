from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from threading import RLock
from typing import Callable

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp

from api.config import ApiConfig
from api.errors import (
    UnexpectedExceptionBoundary,
    error_response,
    register_error_handlers,
)
from api.routes.auth import router as auth_router
from app.bootstrap import ApplicationServices, get_application_services


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _ApplicationServicesBinding:
    """Resolve default services once, when the application lifespan starts."""

    def __init__(self, services: ApplicationServices | None = None) -> None:
        self._services = services
        self._lock = RLock()

    def resolve(self) -> ApplicationServices:
        with self._lock:
            if self._services is None:
                self._services = get_application_services()
            return self._services

    def get_resolved(self) -> ApplicationServices:
        if self._services is None:
            raise RuntimeError("Application services are not initialized")
        return self._services


def create_api_app(
    services: ApplicationServices | None = None,
    *,
    _services_binding: _ApplicationServicesBinding | None = None,
) -> FastAPI:
    config = ApiConfig.from_environment()
    services_binding = _services_binding or _ApplicationServicesBinding(services)

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI) -> AsyncIterator[None]:
        resolved_services = services_binding.resolve()
        fastapi_app.state.services = resolved_services
        for consumer in fastapi_app.state.service_consumers:
            consumer(resolved_services)
        resolved_services.start()
        try:
            yield
        finally:
            resolved_services.stop()

    api_app = FastAPI(lifespan=lifespan)
    api_app.state.api_config = config
    api_app.state.service_consumers: list[
        Callable[[ApplicationServices], None]
    ] = []
    if services is not None:
        api_app.state.services = services
    api_app.add_middleware(UnexpectedExceptionBoundary)

    @api_app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    register_error_handlers(api_app)
    api_app.include_router(auth_router)
    return api_app


def _accepts_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    if not accept:
        return True
    for item in accept.split(","):
        media_type, *parameters = item.split(";")
        quality = 1.0
        for parameter in parameters:
            name, separator, value = parameter.strip().partition("=")
            if separator and name.lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if quality > 0 and media_type.strip().lower() in {"text/html", "*/*"}:
            return True
    return False


def _has_reserved_prefix(path: str) -> bool:
    return any(
        path == prefix or path.startswith(f"{prefix}/")
        for prefix in ("api", "legacy", "assets")
    )


def create_application(
    services: ApplicationServices | None = None,
    legacy_app: ASGIApp | None = None,
    dist_dir: Path | str | None = None,
) -> FastAPI:
    """Compose the JSON API, legacy Gradio UI, assets, and React SPA."""

    services_binding = _ApplicationServicesBinding(services)
    application = create_api_app(
        services,
        _services_binding=services_binding,
    )

    if legacy_app is None:
        import gradio as gr

        from ui.gradio_app import create_gradio_app

        application = gr.mount_gradio_app(
            application,
            create_gradio_app(
                services,
                _service_provider=services_binding.get_resolved,
            ),
            path="/legacy",
        )
    else:
        legacy_state = getattr(legacy_app, "state", None)
        if legacy_state is not None:
            if services is not None:
                legacy_state.services = services
            application.state.service_consumers.append(
                lambda resolved_services: setattr(
                    legacy_state,
                    "services",
                    resolved_services,
                )
            )
        application.mount("/legacy", legacy_app)

    resolved_dist = Path(dist_dir or PROJECT_ROOT / "web" / "dist").resolve()
    assets_dir = resolved_dist / "assets"
    index_path = resolved_dist / "index.html"
    if assets_dir.is_dir():
        application.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @application.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str, request: Request) -> Response:
        if _has_reserved_prefix(full_path) or not _accepts_html(request):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        if not index_path.is_file():
            return error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "frontend_unavailable",
                "React frontend build is unavailable",
                retryable=True,
            )
        return FileResponse(index_path, media_type="text/html")

    return application


app = create_api_app()
