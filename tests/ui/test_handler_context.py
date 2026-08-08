from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from gradio.utils import SyncToAsyncIterator

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


class _ProtocolSignal(Exception):
    pass


def test_generator_wrapper_scopes_next_send_throw_and_stop_value(monkeypatch):
    fallback_registry = object()
    bound_registry = object()
    finalized = []
    monkeypatch.setattr(gradio_app, "session_registry", fallback_registry)

    def handler():
        try:
            received = yield ("next", gradio_app._current_session_registry())
            try:
                yield (
                    "send",
                    received,
                    gradio_app._current_session_registry(),
                )
            except _ProtocolSignal as exc:
                yield (
                    "throw",
                    str(exc),
                    gradio_app._current_session_registry(),
                )
            return "stop-value"
        finally:
            finalized.append(gradio_app._current_session_registry())

    bound = gradio_app._bind_handler(
        handler,
        lambda: _services(bound_registry),
    )

    assert inspect.isgeneratorfunction(bound)
    assert inspect.signature(bound) == inspect.signature(handler)
    iterator = bound()
    assert gradio_app._current_session_registry() is fallback_registry
    caller_contexts = []
    try:
        assert next(iterator) == ("next", bound_registry)
        caller_contexts.append(gradio_app._current_session_registry())
        assert iterator.send("payload") == (
            "send",
            "payload",
            bound_registry,
        )
        caller_contexts.append(gradio_app._current_session_registry())
        assert iterator.throw(_ProtocolSignal("boom")) == (
            "throw",
            "boom",
            bound_registry,
        )
        caller_contexts.append(gradio_app._current_session_registry())
        with pytest.raises(StopIteration) as stopped:
            next(iterator)
    finally:
        iterator.close()

    assert gradio_app._current_session_registry() is fallback_registry
    assert caller_contexts == [fallback_registry] * 3
    assert stopped.value.value == "stop-value"
    assert finalized == [bound_registry]


@pytest.mark.parametrize("termination", ["exception", "close"])
def test_generator_wrapper_resets_after_exception_or_close(
    monkeypatch,
    termination,
):
    fallback_registry = object()
    bound_registry = object()
    finalized = []
    monkeypatch.setattr(gradio_app, "session_registry", fallback_registry)

    def handler():
        try:
            yield gradio_app._current_session_registry()
            raise RuntimeError("generator failure")
        finally:
            finalized.append(gradio_app._current_session_registry())

    bound = gradio_app._bind_handler(
        handler,
        lambda: _services(bound_registry),
    )
    iterator = bound()

    assert next(iterator) is bound_registry
    caller_context = gradio_app._current_session_registry()
    if termination == "exception":
        with pytest.raises(RuntimeError, match="generator failure"):
            next(iterator)
    else:
        iterator.close()
    assert gradio_app._current_session_registry() is fallback_registry
    assert caller_context is fallback_registry
    assert finalized == [bound_registry]


def test_sync_generator_survives_gradio_cross_worker_iteration(monkeypatch):
    fallback_registry = object()
    bound_registry = object()
    finalized = []
    monkeypatch.setattr(gradio_app, "session_registry", fallback_registry)

    def handler():
        try:
            for index in range(3):
                yield index, gradio_app._current_session_registry()
        finally:
            finalized.append(gradio_app._current_session_registry())

    bound = gradio_app._bind_handler(
        handler,
        lambda: _services(bound_registry),
    )

    async def scenario():
        iterator = SyncToAsyncIterator(bound(), limiter=None)
        try:
            for index in range(3):
                assert await anext(iterator) == (index, bound_registry)
                assert (
                    gradio_app._current_session_registry()
                    is fallback_registry
                )
            with pytest.raises(StopAsyncIteration):
                await anext(iterator)
        finally:
            await iterator.aclose()

        assert gradio_app._current_session_registry() is fallback_registry
        assert finalized == [bound_registry]

    asyncio.run(scenario())


async def _in_new_task(awaitable):
    async def await_operation():
        return await awaitable

    return await asyncio.create_task(await_operation())


def test_async_generator_scopes_cross_task_anext_asend_athrow_and_aclose(
    monkeypatch,
):
    fallback_registry = object()
    bound_registry = object()
    operation_tasks = []
    finalized = []
    monkeypatch.setattr(gradio_app, "session_registry", fallback_registry)

    async def handler():
        try:
            operation_tasks.append(asyncio.current_task())
            received = yield ("anext", gradio_app._current_session_registry())
            operation_tasks.append(asyncio.current_task())
            try:
                yield (
                    "asend",
                    received,
                    gradio_app._current_session_registry(),
                )
            except _ProtocolSignal as exc:
                operation_tasks.append(asyncio.current_task())
                yield (
                    "athrow",
                    str(exc),
                    gradio_app._current_session_registry(),
                )
        finally:
            operation_tasks.append(asyncio.current_task())
            finalized.append(gradio_app._current_session_registry())

    bound = gradio_app._bind_handler(
        handler,
        lambda: _services(bound_registry),
    )

    assert inspect.isasyncgenfunction(bound)
    assert inspect.signature(bound) == inspect.signature(handler)

    async def scenario():
        iterator = bound()
        assert gradio_app._current_session_registry() is fallback_registry
        assert await _in_new_task(anext(iterator)) == (
            "anext",
            bound_registry,
        )
        assert gradio_app._current_session_registry() is fallback_registry
        assert await _in_new_task(iterator.asend("payload")) == (
            "asend",
            "payload",
            bound_registry,
        )
        assert gradio_app._current_session_registry() is fallback_registry
        assert await _in_new_task(iterator.athrow(_ProtocolSignal("boom"))) == (
            "athrow",
            "boom",
            bound_registry,
        )
        assert gradio_app._current_session_registry() is fallback_registry
        await _in_new_task(iterator.aclose())
        assert gradio_app._current_session_registry() is fallback_registry
        assert finalized == [bound_registry]
        assert len(set(operation_tasks)) == 4

    asyncio.run(scenario())


@pytest.mark.parametrize("termination", ["normal", "exception"])
def test_async_generator_resets_after_completion_or_exception(
    monkeypatch,
    termination,
):
    fallback_registry = object()
    bound_registry = object()
    finalized = []
    monkeypatch.setattr(gradio_app, "session_registry", fallback_registry)

    async def handler():
        try:
            yield gradio_app._current_session_registry()
            if termination == "exception":
                raise RuntimeError("async generator failure")
        finally:
            finalized.append(gradio_app._current_session_registry())

    bound = gradio_app._bind_handler(
        handler,
        lambda: _services(bound_registry),
    )

    async def scenario():
        iterator = bound()
        assert await _in_new_task(anext(iterator)) is bound_registry
        assert gradio_app._current_session_registry() is fallback_registry
        if termination == "normal":
            with pytest.raises(StopAsyncIteration):
                await _in_new_task(anext(iterator))
        else:
            with pytest.raises(RuntimeError, match="async generator failure"):
                await _in_new_task(anext(iterator))
        assert gradio_app._current_session_registry() is fallback_registry
        assert finalized == [bound_registry]

    asyncio.run(scenario())
