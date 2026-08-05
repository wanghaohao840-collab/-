from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from fastapi.testclient import TestClient

from api.app import create_api_app
from api.config import ApiConfig

from tests.api.test_auth_routes import FakeServices, FakeSessionRegistry


def test_health_and_lifespan_start_and_stop_shared_services_once():
    services = FakeServices(session_registry=FakeSessionRegistry())
    api_app = create_api_app(services)

    with TestClient(api_app) as client:
        assert services.start_calls == 1
        assert services.stop_calls == 0
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/healthz").json() == {"status": "ok"}
        assert services.start_calls == 1
        assert api_app.state.services is services

    assert services.stop_calls == 1


def test_api_config_defaults_and_true_only_environment_parsing(monkeypatch):
    monkeypatch.delenv("APP_COOKIE_SECURE", raising=False)
    assert ApiConfig.from_environment() == ApiConfig(
        cookie_name="zhiyan_session",
        cookie_secure=False,
        cookie_samesite="lax",
    )

    monkeypatch.setenv("APP_COOKIE_SECURE", "TRUE")
    assert ApiConfig.from_environment().cookie_secure is True

    monkeypatch.setenv("APP_COOKIE_SECURE", "1")
    assert ApiConfig.from_environment().cookie_secure is False


def test_api_config_is_frozen():
    config = ApiConfig()

    with pytest.raises(FrozenInstanceError):
        config.cookie_secure = True
