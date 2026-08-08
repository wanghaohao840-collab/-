from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from api.app import create_application
from tests.api.test_auth_routes import FakeServices, FakeSessionRegistry


class FakeLegacyApp:
    def __init__(self, expected_services: FakeServices) -> None:
        self.expected_services = expected_services
        self.requests: list[tuple[str, str]] = []

    async def __call__(self, scope, receive, send) -> None:
        assert scope["type"] == "http"
        self.requests.append((scope["root_path"], scope["path"]))
        body = json.dumps(
            {
                "legacy": True,
                "shared_services": (
                    scope["app"].state.services is self.expected_services
                ),
            }
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _fake_dist(tmp_path):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><main>SPA_INDEX</main>",
        encoding="utf-8",
    )
    (assets / "app.js").write_text(
        "globalThis.__FAKE_ASSET__ = true;",
        encoding="utf-8",
    )
    return dist


def test_unified_routes_preserve_precedence_and_share_one_lifespan(tmp_path):
    services = FakeServices(session_registry=FakeSessionRegistry())
    legacy = FakeLegacyApp(services)
    application = create_application(
        services=services,
        legacy_app=legacy,
        dist_dir=_fake_dist(tmp_path),
    )

    with TestClient(application) as client:
        assert services.start_calls == 1
        assert services.stop_calls == 0
        assert application.state.services is services

        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}

        auth = client.get("/api/v1/auth/session")
        assert auth.status_code == 401
        assert auth.headers["content-type"].startswith("application/json")
        assert auth.json()["error"]["code"] == "invalid_session"

        legacy_root = client.get("/legacy/")
        assert legacy_root.status_code == 200
        assert legacy_root.json() == {
            "legacy": True,
            "shared_services": True,
        }
        assert legacy.requests == [("/legacy", "/legacy/")]

        legacy_prefix = client.get("/legacy", headers={"Accept": "text/html"})
        assert legacy_prefix.status_code == 404
        assert legacy_prefix.headers["content-type"].startswith(
            "application/json"
        )
        assert "SPA_INDEX" not in legacy_prefix.text

        overview = client.get("/overview", headers={"Accept": "text/html"})
        assert overview.status_code == 200
        assert "SPA_INDEX" in overview.text
        assert overview.headers["content-type"].startswith("text/html")

        asset = client.get("/assets/app.js")
        assert asset.status_code == 200
        assert asset.text == "globalThis.__FAKE_ASSET__ = true;"
        assert "javascript" in asset.headers["content-type"]

        unknown_api = client.get(
            "/api/v1/not-a-route",
            headers={"Accept": "text/html"},
        )
        assert unknown_api.status_code == 404
        assert unknown_api.headers["content-type"].startswith(
            "application/json"
        )
        assert unknown_api.json()["error"]["code"] == "not_found"
        assert "SPA_INDEX" not in unknown_api.text

        json_navigation = client.get(
            "/overview",
            headers={"Accept": "application/json"},
        )
        assert json_navigation.status_code == 404
        assert json_navigation.headers["content-type"].startswith(
            "application/json"
        )
        assert "SPA_INDEX" not in json_navigation.text

        non_get_navigation = client.post(
            "/overview",
            headers={"Accept": "text/html"},
        )
        assert non_get_navigation.status_code == 405
        assert non_get_navigation.headers["content-type"].startswith(
            "application/json"
        )
        assert "SPA_INDEX" not in non_get_navigation.text

        assert services.start_calls == 1
        assert services.stop_calls == 0

    assert services.start_calls == 1
    assert services.stop_calls == 1


def test_missing_dist_is_explicit_without_swallowing_api_or_legacy(tmp_path):
    services = FakeServices(session_registry=FakeSessionRegistry())
    legacy = FakeLegacyApp(services)
    application = create_application(
        services=services,
        legacy_app=legacy,
        dist_dir=tmp_path / "missing-dist",
    )

    with TestClient(application) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/v1/auth/session").status_code == 401
        assert client.get("/legacy/").status_code == 200

        response = client.get("/overview", headers={"Accept": "text/html"})
        assert response.status_code == 503
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["error"]["code"] == "frontend_unavailable"

    assert services.start_calls == 1
    assert services.stop_calls == 1


def test_gradio_factory_binds_services_without_starting_workers(monkeypatch):
    from ui import gradio_app

    lifecycle_calls: list[str] = []
    application_services = SimpleNamespace(
        session_registry=object(),
        legacy_migration=object(),
        import_repository=object(),
        import_worker_pool=object(),
        import_service=object(),
        start=lambda: lifecycle_calls.append("start"),
        stop=lambda: lifecycle_calls.append("stop"),
    )
    for attribute in (
        "services",
        "session_registry",
        "legacy_migration",
        "import_repository",
        "import_worker_pool",
        "import_service",
    ):
        monkeypatch.setattr(
            gradio_app,
            attribute,
            getattr(gradio_app, attribute),
        )

    blocks = gradio_app.create_gradio_app(application_services)

    assert blocks.__class__.__name__ == "Blocks"
    assert gradio_app.services is application_services
    assert gradio_app.session_registry is application_services.session_registry
    assert gradio_app.import_worker_pool is application_services.import_worker_pool
    assert gradio_app.import_service is application_services.import_service
    assert lifecycle_calls == []
