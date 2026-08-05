from __future__ import annotations

from unittest.mock import Mock

import pytest

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
