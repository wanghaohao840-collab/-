from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.app import create_api_app
from api.schemas.auth import Credentials
from app.auth import AuthError
from app.session import InvalidCsrfTokenError, InvalidSessionError


SESSION_COOKIE = "zhiyan_session"


@dataclass
class FakeServices:
    session_registry: "FakeSessionRegistry"
    start_calls: int = 0
    stop_calls: int = 0

    def start(self) -> None:
        self.start_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1


class FakeSessionRegistry:
    def __init__(self) -> None:
        self.sessions = {
            "login-token": SimpleNamespace(
                username="reader",
                csrf_token="returned-csrf-token",
            ),
        }
        self.logged_out_tokens: list[str | None] = []

    def register(self, username: str, password: str) -> str:
        if username == "reader":
            raise AuthError("Username already exists")
        token = "register-token"
        self.sessions[token] = SimpleNamespace(
            username=username,
            csrf_token="returned-csrf-token",
        )
        return token

    def login(self, username: str, password: str) -> str:
        if username == "explode":
            raise RuntimeError(f"secret={password} path=C:\\private\\users")
        if username != "reader" or password != "correct horse battery":
            raise AuthError("Invalid username or password")
        return "login-token"

    def get_session(self, token: str | None):
        try:
            return self.sessions[token]
        except (KeyError, TypeError) as exc:
            raise InvalidSessionError("Session expired or logged out") from exc

    def validate_csrf(self, token: str | None, csrf_token: str | None):
        session = self.get_session(token)
        if csrf_token != session.csrf_token:
            raise InvalidCsrfTokenError("Invalid CSRF token")
        return session

    def logout(self, token: str | None) -> None:
        self.logged_out_tokens.append(token)
        self.sessions.pop(token, None)


@pytest.fixture
def services() -> FakeServices:
    return FakeServices(session_registry=FakeSessionRegistry())


@pytest.fixture
def client(services: FakeServices, monkeypatch):
    monkeypatch.delenv("APP_COOKIE_SECURE", raising=False)
    with TestClient(
        create_api_app(services),
        raise_server_exceptions=False,
    ) as test_client:
        yield test_client


def assert_error_envelope(response, *, status: int, code: str) -> None:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {
        "code",
        "message",
        "retryable",
        "field_errors",
    }
    assert body["error"]["code"] == code
    assert body["error"]["retryable"] is False
    assert isinstance(body["error"]["message"], str)
    assert body["error"]["message"]
    assert isinstance(body["error"]["field_errors"], dict)


def test_login_sets_protected_cookie_and_returns_only_session_dto(client):
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "reader", "password": "correct horse battery"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "username": "reader",
        "csrf_token": "returned-csrf-token",
    }
    assert "login-token" not in response.text
    set_cookie = response.headers["set-cookie"].lower()
    assert "zhiyan_session=login-token" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "path=/" in set_cookie
    assert "secure" not in set_cookie


def test_register_sets_cookie_and_returns_only_session_dto(client):
    response = client.post(
        "/api/v1/auth/register",
        json={"username": "new_reader", "password": "correct horse battery"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "username": "new_reader",
        "csrf_token": "returned-csrf-token",
    }
    assert "register-token" not in response.text
    assert response.cookies.get(SESSION_COOKIE) == "register-token"


def test_duplicate_username_uses_conflict_error_envelope(client):
    response = client.post(
        "/api/v1/auth/register",
        json={"username": "reader", "password": "correct horse battery"},
    )

    assert_error_envelope(response, status=409, code="username_exists")


def test_bad_credentials_use_unauthorized_error_envelope(client):
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "reader", "password": "wrong password"},
    )

    assert_error_envelope(response, status=401, code="invalid_credentials")


@pytest.mark.parametrize("token", [None, "expired-token"])
def test_missing_or_expired_session_uses_unauthorized_envelope(client, token):
    if token is not None:
        client.cookies.set(SESSION_COOKIE, token)

    response = client.get("/api/v1/auth/session")

    assert_error_envelope(response, status=401, code="invalid_session")


def test_session_returns_only_safe_dto(client):
    client.cookies.set(SESSION_COOKIE, "login-token")

    response = client.get("/api/v1/auth/session")

    assert response.status_code == 200
    assert response.json() == {
        "username": "reader",
        "csrf_token": "returned-csrf-token",
    }
    assert "login-token" not in response.text


def test_validation_error_uses_common_envelope_without_password_leak(client):
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "reader", "password": "short"},
    )

    assert_error_envelope(response, status=422, code="validation_error")
    assert "password" in response.json()["error"]["field_errors"]
    assert "short" not in response.text


def test_credentials_repr_masks_password():
    credentials = Credentials(
        username="reader",
        password="do-not-expose-this-password",
    )

    assert "do-not-expose-this-password" not in repr(credentials)


@pytest.mark.parametrize("csrf_token", [None, "wrong-token"])
def test_logout_rejects_missing_or_wrong_csrf(client, csrf_token):
    client.cookies.set(SESSION_COOKIE, "login-token")
    headers = {"X-CSRF-Token": csrf_token} if csrf_token is not None else {}

    response = client.post("/api/v1/auth/logout", headers=headers)

    assert_error_envelope(response, status=403, code="invalid_csrf_token")


def test_logout_invalidates_session_and_expires_cookie(client, services):
    client.cookies.set(SESSION_COOKIE, "login-token")

    response = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": "returned-csrf-token"},
    )

    assert response.status_code == 204
    assert response.content == b""
    assert services.session_registry.logged_out_tokens == ["login-token"]
    set_cookie = response.headers["set-cookie"].lower()
    assert "zhiyan_session=" in set_cookie
    assert "max-age=0" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "path=/" in set_cookie


def test_secure_cookie_flag_comes_from_environment(services, monkeypatch):
    monkeypatch.setenv("APP_COOKIE_SECURE", "TrUe")

    with TestClient(create_api_app(services)) as secure_client:
        response = secure_client.post(
            "/api/v1/auth/login",
            json={"username": "reader", "password": "correct horse battery"},
        )

    assert "secure" in response.headers["set-cookie"].lower()


def test_unexpected_error_returns_generic_envelope_and_logs_no_secrets(
    services,
    caplog,
):
    caplog.set_level(logging.ERROR)

    with TestClient(create_api_app(services)) as default_client:
        response = default_client.post(
            "/api/v1/auth/login",
            json={"username": "explode", "password": "do-not-log-password"},
        )

    assert_error_envelope(response, status=500, code="internal_error")
    assert "do-not-log-password" not in response.text
    assert "do-not-log-password" not in caplog.text
    assert "C:\\private\\users" not in caplog.text
    error_records = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(error_records) == 1
    assert error_records[0].name == "api.errors"
    assert error_records[0].getMessage().startswith(
        "Unhandled API request; request_id="
    )


def test_unknown_route_uses_common_error_envelope(client):
    response = client.get("/api/v1/not-found")

    assert_error_envelope(response, status=404, code="not_found")
