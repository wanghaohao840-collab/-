from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.config import ApiConfig
from api.errors import register_error_handlers
from api.routes.auth import router as auth_router
from app.bootstrap import ApplicationServices, get_application_services


def create_api_app(services: ApplicationServices | None = None) -> FastAPI:
    config = ApiConfig.from_environment()

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI) -> AsyncIterator[None]:
        resolved_services = services or get_application_services()
        fastapi_app.state.services = resolved_services
        fastapi_app.state.api_config = config
        resolved_services.start()
        try:
            yield
        finally:
            resolved_services.stop()

    api_app = FastAPI(lifespan=lifespan)

    @api_app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    register_error_handlers(api_app)
    api_app.include_router(auth_router)
    return api_app


app = create_api_app()
