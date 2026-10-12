"""Run the opt-in isolated import role without opening the ordinary app."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import sys
from threading import Event, Thread
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.distributed_import_worker import IsolatedImportWorker
from app.isolated_import_provider import (
    IsolatedProviderConfigurationError, open_isolated_providers,
)
from app.isolated_import_runtime import (
    IsolatedImportConfigurationError, IsolatedImportSettings,
    open_isolated_runtime,
)


def _close_startup(runtime, providers, timeout_seconds: float) -> bool:
    """Retain a slow startup closer and bound only the reporting wait."""
    finished = Event()
    failed = Event()

    def close_owned() -> None:
        try:
            if providers is not None and not providers.close(timeout_seconds=0):
                failed.set()
            if runtime is not None:
                runtime.close()
        except Exception:
            failed.set()
        finally:
            finished.set()

    closer = Thread(target=close_owned, name="isolated-startup-close", daemon=False)
    closer.start()
    closer.join(timeout_seconds)
    return finished.is_set() and not failed.is_set()


def _open_runtime_supervised(settings, providers, stop: Event):
    """Keep opening and its internal failure close on one retained lane."""
    opened = Event()
    accepted = Event()
    abandoned = Event()
    finished = Event()
    result = []
    errors = []
    cleanup_incomplete = Event()

    def open_owned() -> None:
        runtime = None
        try:
            runtime = open_isolated_runtime(
                settings, provider_probe=providers.probe if providers is not None else None)
            result.append(runtime)
        except Exception:
            errors.append(True)
        finally:
            opened.set()
        if runtime is not None:
            while not accepted.is_set() and not abandoned.is_set():
                abandoned.wait(0.02)
        try:
            if runtime is None or abandoned.is_set():
                if providers is not None and not providers.close(timeout_seconds=0):
                    cleanup_incomplete.set()
                if runtime is not None:
                    runtime.close()
        except Exception:
            errors.append(True)
        finally:
            finished.set()

    thread = Thread(target=open_owned, name="isolated-runtime-open", daemon=False)
    deadline = time.monotonic() + settings.provider_timeout_seconds
    thread.start()
    while not opened.is_set() and not stop.is_set() and time.monotonic() < deadline:
        opened.wait(min(0.05, max(0.0, deadline - time.monotonic())))
    if stop.is_set() or time.monotonic() >= deadline or not opened.is_set():
        abandoned.set()
        thread.join(settings.stop_wait_seconds)
        return None, ("runtime_open_active" if thread.is_alive()
                      or cleanup_incomplete.is_set() else "runtime_open_timeout")
    if errors:
        thread.join(settings.stop_wait_seconds)
        return None, ("runtime_open_active" if thread.is_alive()
                      or cleanup_incomplete.is_set() else "runtime_open_failed")
    accepted.set()
    # Ownership transfers here. The retained opener has no remaining external
    # calls after accepted and must never run a second closer for this runtime.
    return result[0], "opened"


def main() -> int:
    stop = Event()
    try:
        settings = IsolatedImportSettings.from_env("worker")
    except IsolatedImportConfigurationError as error:
        print(f"configuration_refused:{error.code}")
        return 2

    providers = None
    refusal = None
    try:
        providers = open_isolated_providers(settings)
    except IsolatedProviderConfigurationError as error:
        refusal = error.code
        print(f"recovery_only:{refusal}")
    except Exception:
        print("provider_open_failed")
        return 2

    def request_stop(_number, _frame) -> None:
        stop.set()

    previous_int = signal.signal(signal.SIGINT, request_stop)
    previous_term = signal.signal(signal.SIGTERM, request_stop)
    try:
        runtime, status = _open_runtime_supervised(settings, providers, stop)
        if runtime is None:
            print(status)
            return 3 if status != "runtime_open_failed" else 2
        try:
            worker = IsolatedImportWorker(
                runtime, providers, worker_id=os.environ.get("ISOLATED_IMPORT_WORKER_ID")
                or f"isolated-{os.getpid()}")
        except Exception:
            cleaned = _close_startup(runtime, providers, settings.stop_wait_seconds)
            print("worker_composition_failed")
            return 2 if cleaned else 3
        result = worker.run_forever(stop)
    except Exception:
        if "worker" not in locals():
            print("worker_runtime_failed")
            return 2
        result = worker.close()
        print("worker_runtime_failed")
        return 3 if not result.clean else 2
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
    print(result.reason)
    return 0 if result.clean else 3


if __name__ == "__main__":
    raise SystemExit(main())
