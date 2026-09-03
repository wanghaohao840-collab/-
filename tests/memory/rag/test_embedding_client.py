# tests/memory/rag/test_embedding_client.py
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import math
import threading
import time

import httpx
import pytest

from hello_agents.memory.rag.embedding_client import SiliconFlowEmbedding
from hello_agents.memory.rag.embedding_profile import EmbeddingFailure, EmbeddingSettings


def settings(**changes):
    values = {"RAG_EMBEDDING_API_KEY": "private-test-token"}
    values.update({"RAG_EMBEDDING_" + name: str(value)
                   for name, value in changes.items()})
    return EmbeddingSettings.from_env(values, candidate=True)


def payload(count=1):
    return {"model": "BAAI/bge-m3", "data": [
        {"index": i, "embedding": [float(i + 1)] + [0.0] * 1023}
        for i in reversed(range(count))
    ]}


def test_protocol_order_and_batches():
    seen = []
    def handler(request):
        body = json.loads(request.content)
        assert str(request.url) == "https://api.siliconflow.cn/v1/embeddings"
        assert request.headers["Authorization"] == "Bearer private-test-token"
        assert set(body) == {"model", "input", "encoding_format"}
        assert body["model"] == "BAAI/bge-m3"
        assert body["encoding_format"] == "float"
        seen.append(body["input"])
        data = payload(len(body["input"]))
        for item in data["data"]:
            item["embedding"] = [0.0] * 1024
            item["embedding"][item["index"]] = 1.0
        return httpx.Response(200, json=data)
    client = SiliconFlowEmbedding(settings(), transport=httpx.MockTransport(handler))
    vectors = client.embed_documents([str(i) for i in range(10)])
    assert [len(batch) for batch in seen] == [8, 2]
    assert vectors[0][0] == 1 and vectors[1][1] == 1
    assert len(vectors) == 10
    assert all(math.isclose(math.hypot(*v), 1) for v in vectors)
    assert client.embed_query("query")[0] == 1


@pytest.mark.parametrize("kind", [
    "missing", "duplicate", "out_of_range", "bool_index", "wrong_model",
    "wrong_dim", "zero", "nan", "infinity", "not_number", "not_json",
])
def test_bad_response_never_retries_or_substitutes(kind):
    calls = []
    def handler(request):
        calls.append(request)
        data = payload(2)
        if kind == "missing": data["data"].pop()
        elif kind == "duplicate": data["data"][0]["index"] = 0
        elif kind == "out_of_range": data["data"][0]["index"] = 2
        elif kind == "bool_index": data["data"][0]["index"] = True
        elif kind == "wrong_model": data["model"] = "other"
        elif kind == "wrong_dim": data["data"][0]["embedding"] = [1]
        elif kind == "zero": data["data"][0]["embedding"] = [0] * 1024
        elif kind == "nan": data["data"][0]["embedding"][0] = float("nan")
        elif kind == "infinity": data["data"][0]["embedding"][0] = float("inf")
        elif kind == "not_number": data["data"][0]["embedding"][0] = "1"
        if kind == "not_json": return httpx.Response(200, content=b"secret")
        return httpx.Response(200, content=json.dumps(data).encode())
    client = SiliconFlowEmbedding(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(EmbeddingFailure):
        client.embed_documents(["one", "two"])
    assert len(calls) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 302, 500])
def test_non_retryable_errors_do_not_leak_or_redirect(status, caplog):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="private-test-token sensitive-document",
                              headers={"Location": "https://other.invalid/"})
    client = SiliconFlowEmbedding(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(EmbeddingFailure) as caught:
        client.embed_query("sensitive-document")
    assert len(calls) == 1
    assert caught.value.status_code == status
    assert not caught.value.retryable
    assert "private-test-token" not in str(caught.value) + caplog.text
    assert "sensitive-document" not in str(caught.value) + caplog.text


@pytest.mark.parametrize("failure", [429, 502, 503, 504, "connection", "timeout"])
def test_transient_failure_is_bounded(failure, monkeypatch):
    import hello_agents.memory.rag.embedding_client as module
    monkeypatch.setattr(module, "retry_delay", lambda value, attempt: 0)
    calls = []
    def handler(request):
        calls.append(request)
        if failure == "connection": raise httpx.ConnectError("secret", request=request)
        if failure == "timeout": raise httpx.ReadTimeout("secret", request=request)
        return httpx.Response(failure, text="secret")
    client = SiliconFlowEmbedding(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(EmbeddingFailure) as caught:
        client.embed_query("query")
    assert len(calls) == 3
    assert caught.value.retryable
    assert "secret" not in str(caught.value)


def test_retry_can_recover(monkeypatch):
    import hello_agents.memory.rag.embedding_client as module
    monkeypatch.setattr(module, "retry_delay", lambda value, attempt: 0)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429 if len(calls) == 1 else 200,
                              json=payload())
    client = SiliconFlowEmbedding(settings(), transport=httpx.MockTransport(handler))
    assert len(client.embed_query("query")) == 1024
    assert len(calls) == 2


@pytest.mark.parametrize("texts", ["abc", [""], ["  "], [None], ["x" * 6001]])
def test_input_is_checked_before_network(texts):
    def handler(request):
        pytest.fail("invalid input caused network request")
    client = SiliconFlowEmbedding(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(EmbeddingFailure): client.embed_documents(texts)
    assert client.embed_documents([]) == []


def test_wall_clock_deadline_interrupts_slow_stream():
    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.01)
                yield b" "
    async def handler(request):
        return httpx.Response(200, stream=SlowBody())
    client = SiliconFlowEmbedding(settings(TIMEOUT_SECONDS=0.05, MAX_RETRIES=0),
                                  transport=httpx.MockTransport(handler))
    start = time.monotonic()
    with pytest.raises(EmbeddingFailure): client.embed_query("query")
    assert time.monotonic() - start < 1


def test_process_wide_limit_across_clients():
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}
    async def handler(request):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            await asyncio.sleep(0.03)
            return httpx.Response(200, json=payload())
        finally:
            with lock: state["active"] -= 1
    def run(_):
        client = SiliconFlowEmbedding(settings(), transport=httpx.MockTransport(handler))
        return client.embed_query("query")
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert len(list(pool.map(run, range(6)))) == 6
    assert state["peak"] <= 2


def test_async_facade_and_sync_loop_guard():
    client = SiliconFlowEmbedding(settings(), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload())))
    async def run():
        with pytest.raises(EmbeddingFailure, match="sync_in_async"):
            client.embed_query("query")
        assert len(await client.aembed_query("query")) == 1024
    asyncio.run(run())


def test_retry_after_and_budget(monkeypatch):
    import hello_agents.memory.rag.embedding_client as module
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime
    assert module.retry_delay("3", 0) == 3
    assert module.retry_delay("999", 0) is None
    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=4))
    assert 0 <= module.retry_delay(future, 0) <= 5
    monkeypatch.setattr(module, "BATCH_BUDGET_SECONDS", 0.05)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "5"})
    client = SiliconFlowEmbedding(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(EmbeddingFailure): client.embed_query("query")
    assert len(calls) == 1


def test_oversized_response_is_rejected():
    client = SiliconFlowEmbedding(settings(), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))))
    with pytest.raises(EmbeddingFailure, match="response_size"):
        client.embed_query("query")


@pytest.mark.parametrize("invalid", ["\ud800", "文" * 2001, ""])
def test_all_inputs_are_validated_before_sending_any_batch(invalid):
    def handler(request):
        pytest.fail("a preceding valid batch was sent before input validation")
    client = SiliconFlowEmbedding(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(EmbeddingFailure, match="input"):
        client.embed_documents(["valid"] * 8 + [invalid])


def test_second_batch_failure_never_returns_partial_results():
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json=payload(8))
        return httpx.Response(401, text="upstream-private-error")
    client = SiliconFlowEmbedding(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(EmbeddingFailure, match="http"):
        client.embed_documents(["valid"] * 9)
    assert len(calls) == 2


def test_long_retry_after_fails_without_retrying_early():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "11"})
    client = SiliconFlowEmbedding(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(EmbeddingFailure) as caught:
        client.embed_query("query")
    assert caught.value.status_code == 429
    assert len(calls) == 1


def test_slot_wait_consumes_batch_budget_and_slot_is_released(monkeypatch):
    import hello_agents.memory.rag.embedding_client as module
    events = []
    class Slot:
        def acquire(self, timeout):
            events.append(("acquire", timeout))
            time.sleep(0.04)
            return True
        def release(self):
            events.append(("release",))
    monkeypatch.setattr(module, "_SLOTS", Slot())
    monkeypatch.setattr(module, "BATCH_BUDGET_SECONDS", 0.03)
    def forbidden(request):
        pytest.fail("expired batch made a request")
    client = SiliconFlowEmbedding(settings(), transport=httpx.MockTransport(forbidden))
    with pytest.raises(EmbeddingFailure, match="deadline"):
        client.embed_query("query")
    assert events == [("acquire", 0.03), ("release",)]


def test_transport_errors_do_not_leak_details_or_leak_slots(monkeypatch):
    import hello_agents.memory.rag.embedding_client as module
    slots = threading.BoundedSemaphore(2)
    monkeypatch.setattr(module, "_SLOTS", slots)
    def handler(request):
        raise httpx.RemoteProtocolError("private-test-token document-secret")
    client = SiliconFlowEmbedding(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(EmbeddingFailure, match="transport") as caught:
        client.embed_query("query")
    assert "private-test-token" not in str(caught.value)
    assert "document-secret" not in str(caught.value)
    assert slots.acquire(blocking=False) and slots.acquire(blocking=False)
    slots.release()
    slots.release()


def test_http_client_never_inherits_proxies_or_follows_redirects(monkeypatch):
    import hello_agents.memory.rag.embedding_client as module
    original = httpx.AsyncClient
    options = []
    def factory(**kwargs):
        options.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(module.httpx, "AsyncClient", factory)
    client = SiliconFlowEmbedding(settings(), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload())))
    assert len(client.embed_query("query")) == 1024
    assert options[0]["trust_env"] is False
    assert options[0]["follow_redirects"] is False
