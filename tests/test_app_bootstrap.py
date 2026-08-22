from __future__ import annotations

import threading
from unittest.mock import Mock

import pytest

import app.bootstrap as bootstrap
from app.bootstrap import (
    ApplicationServices,
    get_application_services,
    reset_application_services_for_tests,
)


@pytest.fixture(autouse=True)
def reset_services():
    reset_application_services_for_tests()
    yield
    reset_application_services_for_tests()


def test_create_uses_explicit_absolute_data_root_before_environment(tmp_path, monkeypatch):
    explicit_root = tmp_path / "explicit-data"
    monkeypatch.setenv("PDF_ASSISTANT_DATA_DIR", str(tmp_path / "environment-data"))

    services = ApplicationServices.create(explicit_root)

    assert services.data_root == explicit_root.resolve()
    assert services.db_path == explicit_root.resolve() / "app.db"
    assert services.storage.data_root == explicit_root.resolve()
    assert services.session_registry.storage is services.storage
    assert services.import_worker_pool.repository is services.import_repository
    assert services.document_library.session_registry is services.session_registry
    assert services.document_library.storage is services.storage
    assert services.document_library.import_service is services.import_service


def test_create_uses_environment_data_root_when_not_explicit(tmp_path, monkeypatch):
    environment_root = tmp_path / "environment-data"
    monkeypatch.setenv("PDF_ASSISTANT_DATA_DIR", str(environment_root))

    services = ApplicationServices.create()

    assert services.data_root == environment_root.resolve()
    assert services.db_path == environment_root.resolve() / "app.db"


def test_create_uses_project_data_root_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("PDF_ASSISTANT_DATA_DIR", raising=False)
    monkeypatch.setattr(bootstrap, "PROJECT_ROOT", tmp_path / "project")

    services = ApplicationServices.create()

    assert services.data_root == (tmp_path / "project" / "data").resolve()
    assert services.db_path == services.data_root / "app.db"


def test_start_and_stop_are_idempotent(tmp_path):
    services = ApplicationServices.create(tmp_path / "data")
    services.import_worker_pool.start = start = Mock()
    services.import_worker_pool.stop = stop = Mock()

    services.start()
    services.start()
    services.stop()
    services.stop()

    start.assert_called_once_with()
    stop.assert_called_once_with()


def test_document_library_is_one_stable_service_without_extra_lifecycle(tmp_path):
    services = ApplicationServices.create(tmp_path / "data")
    start = services.import_worker_pool.start = Mock()
    stop = services.import_worker_pool.stop = Mock()

    first = services.document_library
    services.start()
    assert services.document_library is first
    services.stop()

    start.assert_called_once_with()
    stop.assert_called_once_with()


def test_concurrent_lifecycle_transitions_call_pool_once(tmp_path):
    services = ApplicationServices.create(tmp_path / "data")
    start_calls = []
    first_start_entered = threading.Event()
    release_start = threading.Event()
    second_start_attempted = threading.Event()
    second_start_returned = threading.Event()

    def start():
        start_calls.append("start")
        if len(start_calls) == 1:
            first_start_entered.set()
            assert release_start.wait(timeout=2)

    services.import_worker_pool.start = start
    first_start = threading.Thread(target=services.start)
    second_start = threading.Thread(
        target=lambda: (
            second_start_attempted.set(),
            services.start(),
            second_start_returned.set(),
        )
    )
    first_start.start()
    assert first_start_entered.wait(timeout=1)
    second_start.start()
    assert second_start_attempted.wait(timeout=1)
    try:
        assert not second_start_returned.wait(timeout=0.2)
    finally:
        release_start.set()
        first_start.join(timeout=2)
        second_start.join(timeout=2)
    assert not first_start.is_alive()
    assert not second_start.is_alive()
    assert start_calls == ["start"]

    stop_calls = []
    first_stop_entered = threading.Event()
    release_stop = threading.Event()
    second_stop_attempted = threading.Event()
    second_stop_returned = threading.Event()

    def stop():
        stop_calls.append("stop")
        if len(stop_calls) == 1:
            first_stop_entered.set()
            assert release_stop.wait(timeout=2)

    services.import_worker_pool.stop = stop
    first_stop = threading.Thread(target=services.stop)
    second_stop = threading.Thread(
        target=lambda: (
            second_stop_attempted.set(),
            services.stop(),
            second_stop_returned.set(),
        )
    )
    first_stop.start()
    assert first_stop_entered.wait(timeout=1)
    second_stop.start()
    assert second_stop_attempted.wait(timeout=1)
    try:
        assert not second_stop_returned.wait(timeout=0.2)
    finally:
        release_stop.set()
        first_stop.join(timeout=2)
        second_stop.join(timeout=2)
    assert not first_stop.is_alive()
    assert not second_stop.is_alive()
    assert stop_calls == ["stop"]


def test_singleton_reuses_registry_and_worker_pool(tmp_path, monkeypatch):
    monkeypatch.setenv("PDF_ASSISTANT_DATA_DIR", str(tmp_path / "data"))

    first = get_application_services()
    second = get_application_services()

    assert second is first
    assert first.data_root == (tmp_path / "data").resolve()
    assert second.session_registry is first.session_registry
    assert second.import_worker_pool is first.import_worker_pool


def test_reset_stops_a_started_singleton(tmp_path, monkeypatch):
    monkeypatch.setenv("PDF_ASSISTANT_DATA_DIR", str(tmp_path / "data"))
    services = get_application_services()
    services.import_worker_pool.start = Mock()
    services.import_worker_pool.stop = stop = Mock()
    services.start()

    reset_application_services_for_tests()

    stop.assert_called_once_with()
    assert get_application_services() is not services


def test_ui_initialization_refreshes_shared_service_aliases(tmp_path, monkeypatch):
    import ui.gradio_app as gradio_app

    monkeypatch.setenv("PDF_ASSISTANT_DATA_DIR", str(tmp_path / "data"))
    first = gradio_app.initialize_app_services()
    second = gradio_app.initialize_app_services()

    assert second is first
    assert gradio_app.services is first
    assert gradio_app.session_registry is first.session_registry
    assert gradio_app.import_worker_pool is first.import_worker_pool
