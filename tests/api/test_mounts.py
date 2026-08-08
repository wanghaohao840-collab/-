from __future__ import annotations

import json
import os
import subprocess
import sys
from inspect import unwrap
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from api.app import create_application
from tests.api.test_auth_routes import FakeServices, FakeSessionRegistry


class FakeLegacyApp:
    def __init__(self, expected_services: FakeServices | None = None) -> None:
        self.expected_services = expected_services
        self.requests: list[tuple[str, str]] = []
        self.state = SimpleNamespace()

    async def __call__(self, scope, receive, send) -> None:
        assert scope["type"] == "http"
        self.requests.append((scope["root_path"], scope["path"]))
        body = json.dumps(
            {
                "legacy": True,
                "shared_services": (
                    self.expected_services is None
                    or scope["app"].state.services is self.expected_services
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

    assert application.state.services is services
    assert legacy.state.services is services

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


class _FactoryAssistant:
    def __init__(self, label: str) -> None:
        self.current_document_id = f"{label}-document"
        self._label = label

    def get_documents(self) -> list[str]:
        return [f"{self._label}.txt | {self.current_document_id}"]


class _FactoryRegistry:
    def __init__(self, label: str) -> None:
        self.label = label
        self.tokens: dict[str, _FactoryAssistant] = {}

    def register(self, username: str, _password: str) -> str:
        token = f"{self.label}:{username}"
        self.tokens[token] = _FactoryAssistant(self.label)
        return token

    def get_session(self, token: str):
        if token not in self.tokens:
            raise AssertionError(f"{self.label} received foreign token {token}")
        return SimpleNamespace(username=token.split(":", 1)[1])

    def get_assistant(self, token: str) -> _FactoryAssistant:
        if token not in self.tokens:
            raise AssertionError(f"{self.label} received foreign token {token}")
        return self.tokens[token]


def _factory_services(label: str, lifecycle_calls: list[str]):
    return SimpleNamespace(
        session_registry=_FactoryRegistry(label),
        legacy_migration=object(),
        import_repository=object(),
        import_worker_pool=object(),
        import_service=object(),
        start=lambda: lifecycle_calls.append(f"{label}:start"),
        stop=lambda: lifecycle_calls.append(f"{label}:stop"),
    )


def _bound_handler(blocks, target):
    return next(
        block_fn.fn
        for block_fn in blocks.fns.values()
        if unwrap(block_fn.fn) is target
    )


def test_gradio_factory_isolates_two_live_apps_without_starting_workers():
    from ui import gradio_app

    lifecycle_calls: list[str] = []
    services_a = _factory_services("A", lifecycle_calls)
    services_b = _factory_services("B", lifecycle_calls)

    blocks_a = gradio_app.create_gradio_app(services_a)
    blocks_b = gradio_app.create_gradio_app(services_b)

    assert blocks_a is not blocks_b
    assert set(blocks_a.blocks.values()).isdisjoint(blocks_b.blocks.values())
    register_a = _bound_handler(blocks_a, gradio_app.register_user)
    register_b = _bound_handler(blocks_b, gradio_app.register_user)
    refresh_a = _bound_handler(blocks_a, gradio_app.refresh_documents)
    refresh_b = _bound_handler(blocks_b, gradio_app.refresh_documents)

    token_a, status_a, *_ = register_a("alice", "password")
    token_b, status_b, *_ = register_b("bob", "password")
    assert (token_a, status_a) == ("A:alice", "Logged in as alice")
    assert (token_b, status_b) == ("B:bob", "Logged in as bob")
    assert refresh_b(token_b)["choices"] == ["B.txt | B-document"]
    assert refresh_a(token_a)["choices"] == ["A.txt | A-document"]
    assert lifecycle_calls == []


def test_default_services_resolve_once_in_lifespan_and_bind_api_and_legacy(
    monkeypatch,
    tmp_path,
):
    import api.app as api_app

    lifecycle_calls: list[str] = []
    services = _factory_services("default", lifecycle_calls)
    resolution_calls: list[str] = []
    legacy = FakeLegacyApp()
    monkeypatch.setattr(
        api_app,
        "get_application_services",
        lambda: resolution_calls.append("resolve") or services,
    )

    application = create_application(
        legacy_app=legacy,
        dist_dir=_fake_dist(tmp_path),
    )

    assert resolution_calls == []
    assert not hasattr(application.state, "services")
    assert not hasattr(legacy.state, "services")

    with TestClient(application) as client:
        assert resolution_calls == ["resolve"]
        assert application.state.services is services
        assert legacy.state.services is services
        response = client.get("/legacy/")
        assert response.status_code == 200
        assert response.json()["shared_services"] is True
        assert lifecycle_calls == ["default:start"]

    assert lifecycle_calls == ["default:start", "default:stop"]


def test_importing_server_does_not_create_db_and_honors_late_data_dir(tmp_path):
    import_root = tmp_path / "import-root"
    late_root = tmp_path / "late-root"
    code = """
import os
from pathlib import Path
from fastapi.testclient import TestClient

import server

import_root = Path(os.environ["IMPORT_DATA_ROOT"])
late_root = Path(os.environ["LATE_DATA_ROOT"])
assert not import_root.exists()
assert not late_root.exists()
os.environ["PDF_ASSISTANT_DATA_DIR"] = str(late_root)
with TestClient(server.app):
    assert server.app.state.services.data_root == late_root.resolve()
    assert server.app.state.services.db_path == late_root.resolve() / "app.db"
print("late-binding-ok")
"""
    environment = os.environ | {
        "PDF_ASSISTANT_DATA_DIR": str(import_root),
        "IMPORT_DATA_ROOT": str(import_root),
        "LATE_DATA_ROOT": str(late_root),
    }

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parents[2],
        env=environment,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "late-binding-ok"
    assert not import_root.exists()
    assert (late_root / "app.db").is_file()
