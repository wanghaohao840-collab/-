from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from ui import gradio_app


def _services(registry):
    return SimpleNamespace(
        session_registry=registry,
        legacy_migration=object(),
        import_repository=object(),
        import_worker_pool=object(),
        import_service=object(),
    )


def test_sync_wrapper_preserves_kind_signature_and_resets_after_exception(
    monkeypatch,
):
    fallback_registry = object()
    bound_registry = object()
    monkeypatch.setattr(gradio_app, "session_registry", fallback_registry)

    def failing_handler(value: str) -> str:
        assert gradio_app._current_session_registry() is bound_registry
        raise RuntimeError(value)

    bound = gradio_app._bind_handler(
        failing_handler,
        lambda: _services(bound_registry),
    )

    assert not inspect.iscoroutinefunction(bound)
    assert not inspect.isgeneratorfunction(bound)
    assert not inspect.isasyncgenfunction(bound)
    assert inspect.signature(bound) == inspect.signature(failing_handler)
    with pytest.raises(RuntimeError, match="sync failure"):
        bound("sync failure")
    assert gradio_app._current_session_registry() is fallback_registry


def test_coroutine_wrappers_isolate_interleaved_apps_and_reset_on_exception(
    monkeypatch,
):
    fallback_registry = object()
    registry_a = object()
    registry_b = object()
    monkeypatch.setattr(gradio_app, "session_registry", fallback_registry)

    async def interleaved_handler(started, release):
        before = gradio_app._current_session_registry()
        started.set()
        await release.wait()
        return before, gradio_app._current_session_registry()

    async def failing_handler():
        assert gradio_app._current_session_registry() is registry_a
        await asyncio.sleep(0)
        raise RuntimeError("async failure")

    bound_a = gradio_app._bind_handler(
        interleaved_handler,
        lambda: _services(registry_a),
    )
    bound_b = gradio_app._bind_handler(
        interleaved_handler,
        lambda: _services(registry_b),
    )
    bound_failure = gradio_app._bind_handler(
        failing_handler,
        lambda: _services(registry_a),
    )

    assert inspect.iscoroutinefunction(bound_a)
    assert inspect.signature(bound_a) == inspect.signature(interleaved_handler)
    assert inspect.iscoroutinefunction(bound_failure)

    async def scenario():
        started_a = asyncio.Event()
        started_b = asyncio.Event()
        release = asyncio.Event()
        task_a = asyncio.create_task(bound_a(started_a, release))
        await started_a.wait()
        task_b = asyncio.create_task(bound_b(started_b, release))
        await started_b.wait()
        release.set()
        result_a, result_b = await asyncio.gather(task_a, task_b)
        assert result_a == (registry_a, registry_a)
        assert result_b == (registry_b, registry_b)
        assert gradio_app._current_session_registry() is fallback_registry

        with pytest.raises(RuntimeError, match="async failure"):
            await bound_failure()
        assert gradio_app._current_session_registry() is fallback_registry

    asyncio.run(scenario())


@pytest.mark.parametrize("termination", ["normal", "exception", "close"])
def test_generator_wrapper_keeps_iteration_context_and_resets(
    monkeypatch,
    termination,
):
    fallback_registry = object()
    bound_registry = object()
    finalized: list[str] = []
    monkeypatch.setattr(gradio_app, "session_registry", fallback_registry)

    def handler(mode: str):
        try:
            yield gradio_app._current_session_registry()
            if mode == "exception":
                raise RuntimeError("generator failure")
            yield gradio_app._current_session_registry()
        finally:
            finalized.append(mode)

    bound = gradio_app._bind_handler(
        handler,
        lambda: _services(bound_registry),
    )

    assert inspect.isgeneratorfunction(bound)
    assert inspect.signature(bound) == inspect.signature(handler)
    iterator = bound(termination)
    assert gradio_app._current_session_registry() is fallback_registry
    assert next(iterator) is bound_registry
    assert gradio_app._current_session_registry() is bound_registry

    if termination == "normal":
        assert next(iterator) is bound_registry
        with pytest.raises(StopIteration):
            next(iterator)
    elif termination == "exception":
        with pytest.raises(RuntimeError, match="generator failure"):
            next(iterator)
    else:
        iterator.close()

    assert gradio_app._current_session_registry() is fallback_registry
    assert finalized == [termination]


@pytest.mark.parametrize("termination", ["normal", "exception", "aclose"])
def test_async_generator_wrapper_keeps_iteration_context_and_resets(
    monkeypatch,
    termination,
):
    fallback_registry = object()
    bound_registry = object()
    finalized: list[str] = []
    monkeypatch.setattr(gradio_app, "session_registry", fallback_registry)

    async def handler(mode: str):
        try:
            yield gradio_app._current_session_registry()
            if mode == "exception":
                raise RuntimeError("async generator failure")
            yield gradio_app._current_session_registry()
        finally:
            finalized.append(mode)

    bound = gradio_app._bind_handler(
        handler,
        lambda: _services(bound_registry),
    )

    assert inspect.isasyncgenfunction(bound)
    assert inspect.signature(bound) == inspect.signature(handler)

    async def scenario():
        iterator = bound(termination)
        assert gradio_app._current_session_registry() is fallback_registry
        assert await anext(iterator) is bound_registry
        assert gradio_app._current_session_registry() is bound_registry

        if termination == "normal":
            assert await anext(iterator) is bound_registry
            with pytest.raises(StopAsyncIteration):
                await anext(iterator)
        elif termination == "exception":
            with pytest.raises(RuntimeError, match="async generator failure"):
                await anext(iterator)
        else:
            await iterator.aclose()

        assert gradio_app._current_session_registry() is fallback_registry
        assert finalized == [termination]

    asyncio.run(scenario())
