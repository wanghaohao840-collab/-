# BGE-M3 Provider and Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Execution mode is an operator choice; no agents have been dispatched. If executing-plans is unavailable, explicitly report that and execute inline with these checkpoints.

**Goal:** Deliver E1: a separately configured, validated BGE-M3 embedding client and a safe public-text probe, then prepare the operator's local API-key entry without activating document retrieval.

**Architecture:** Immutable RAG-only configuration and embedding identity are separate from the legacy Memory singleton. A synchronous client facade runs bounded asynchronous HTTP requests, preserving a process-wide concurrency limit. A standalone CLI consumes an explicitly selected environment file, never mutates it, and sends only fixed public test sentences when explicitly invoked with `--probe`.

**Tech Stack:** Existing Python 3.11+ runtime, HTTPX 0.28.1, python-dotenv, pytest; PowerShell for operator commands. No model downloads, GPUs, new services, database changes or frontend changes.

## Global Constraints

- Governing approved spec: `docs/superpowers/specs/2026-09-03-bge-m3-rag-embedding-design.md`.
- Target model exactly `BAAI/bge-m3`, endpoint `https://api.siliconflow.cn/v1`, 1024 dimensions; no `Pro/` automatic substitution.
- Every request uses float encoding; do not send `dimensions` to BGE-M3.
- Batch size initially 8, at most 6000 UTF-8 bytes per text, at most two concurrent embedding batches per process.
- Per-attempt timeout 20 seconds, at most two retries, per-batch budget 75 seconds including concurrency waiting; retry waits at most 10 seconds.
- Never silently pad/truncate vectors, substitute models, or fall back to SimpleEmbedding after a remote failure.
- Keep existing `LLM_*`, Memory provider, source vector collections, Notes data and running containers unchanged.
- Do not load a real `.env` in unit tests. Use fake keys and in-memory transports. Never print a key, key prefix/suffix or raw upstream error body.
- Use `D:\python_self_agent\venv\Scripts\python.exe`; all paths below are relative to the execution worktree unless identified as stable runtime paths.
- Only task-owned paths may be staged. Four existing GraphRAG packet edits and the existing Windows operations report edit are outside E1.
- E1 does not mean production embedding is active. E2 and E3 remain required; no migration or deep smoke until their gates are complete.

## Execution boundary and file map

The planning checkout is `D:\python_self_agent` at `dda8527`. The runtime remains there.
At execution, use the using-git-worktrees skill and honor the operator's worktree preference.
Do not assume another feature's existing worktree is available for reuse. A plan/record
commit is not authorization to publish another feature's changes.

| Task | Owned files | Observable deliverable |
| --- | --- | --- |
| 1 | `hello_agents/memory/rag/embedding_profile.py`, `tests/memory/rag/test_embedding_profile.py` | RAG-only settings, key-free fingerprint and strict vector validation |
| 2 | `hello_agents/memory/rag/embedding_client.py`, `tests/memory/rag/test_embedding_client.py`, `requirements.txt` | Real protocol adapter exercised entirely with mock HTTP |
| 3 | `deploy/embedding_probe.py`, `tests/deploy/test_embedding_probe.py`, `deploy/.env.example`, `deploy/README.md`, `Dockerfile` | Offline configuration check and explicit public-text probe, candidate config handoff |
| 4 | Runtime-only `D:\python_self_agent\deploy\.env`; this plan/spec progress | Stable integration of E1 only, open blank key field and wait for operator |

No task in E1 changes `embedding.py`, RAG pipeline factories, `app/runtime.py`,
Qdrant stores or the health probe. The client is dormant until explicitly used by
the CLI. This boundary makes the credential handoff useful without partial index activation.

## Task 1: Settings, immutable identity and vector validation

**Files:** Create the two Task 1 files in the table above.

**Interfaces:** `EmbeddingSettings.from_env(values, candidate=False)` consumes an explicit
mapping. Candidate mode selects the remote profile without changing any environment.
`EmbeddingProfile.fingerprint` identifies non-secret vector-space settings.
`normalize_vector(value, dimension)` returns finite, nonzero, L2-normalized floats.
`EmbeddingFailure` extends existing `RAGEmbeddingError` with safe code/status/retryable fields.

- [ ] **Step 1: Add the complete failing contract tests.**

```python
# tests/memory/rag/test_embedding_profile.py
from dataclasses import replace
import math
import pytest

from hello_agents.memory.rag.embedding_profile import (
    EmbeddingFailure, EmbeddingSettings, normalize_vector,
)


def test_default_is_legacy_and_candidate_is_independent():
    values = {"LLM_API_KEY": "not-an-embedding-key"}
    assert EmbeddingSettings.from_env(values).profile.dimension == 384
    with pytest.raises(EmbeddingFailure, match="configuration"):
        EmbeddingSettings.from_env(values, candidate=True)
    values["RAG_EMBEDDING_API_KEY"] = "private-test-token"
    active = EmbeddingSettings.from_env(values)
    candidate = EmbeddingSettings.from_env(values, candidate=True)
    assert active.profile.provider == "simple"
    assert candidate.profile.model == "BAAI/bge-m3"
    assert candidate.profile.dimension == 1024
    assert "private-test-token" not in repr(candidate)
    assert "not-an-embedding-key" not in repr(candidate)
    assert "RAG_EMBEDDING_PROVIDER" not in values


def test_profile_changes_only_for_vector_space_changes():
    first = EmbeddingSettings.from_env(
        {"RAG_EMBEDDING_API_KEY": "one"}, candidate=True
    )
    second = EmbeddingSettings.from_env(
        {"RAG_EMBEDDING_API_KEY": "two"}, candidate=True
    )
    assert first.profile.fingerprint == second.profile.fingerprint
    assert first.profile.fingerprint != replace(
        first.profile, revision="next"
    ).fingerprint
    assert first.profile.fingerprint != replace(
        first.profile, query_preprocessing="query-v2"
    ).fingerprint
    assert len(first.profile.fingerprint) == 64


@pytest.mark.parametrize("key,value", [
    ("PROVIDER", "unknown"), ("MODEL", "Pro/BAAI/bge-m3"),
    ("DIMENSION", "384"), ("DIMENSION", "bad"),
    ("BASE_URL", "http://api.siliconflow.cn/v1"),
    ("BASE_URL", "https://secret@api.siliconflow.cn/v1"),
    ("BASE_URL", "https://api.siliconflow.cn/v1?key=secret"),
    ("BASE_URL", "https://api.siliconflow.cn/v1#secret"),
    ("BASE_URL", "https://other.invalid/v1"),
    ("TIMEOUT_SECONDS", "nan"), ("TIMEOUT_SECONDS", "0"),
    ("TIMEOUT_SECONDS", "21"), ("MAX_RETRIES", "3"),
    ("MAX_RETRIES", "-1"), ("BATCH_SIZE", "0"),
    ("BATCH_SIZE", "9"), ("REVISION", ""),
    ("API_KEY", ""), ("API_KEY", "bad\nheader"),
])
def test_invalid_remote_configuration_is_safe(key, value):
    values = {"RAG_EMBEDDING_PROVIDER": "siliconflow",
              "RAG_EMBEDDING_API_KEY": "private-test-token"}
    values["RAG_EMBEDDING_" + key] = value
    with pytest.raises(EmbeddingFailure) as caught:
        EmbeddingSettings.from_env(values)
    assert "private-test-token" not in str(caught.value)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("value", [
    [], [1], [0, 0], [float("nan"), 1], [float("inf"), 1],
    [True, 1], ["1", 2], [[1], [2]], None,
])
def test_bad_vectors_are_rejected(value):
    with pytest.raises(EmbeddingFailure):
        normalize_vector(value, 2)


def test_normalize_without_padding_or_overflow():
    assert normalize_vector([3, 4], 2) == [0.6, 0.8]
    result = normalize_vector([1e308, 1e308], 2)
    assert math.isclose(math.hypot(*result), 1.0)
```

- [ ] **Step 2: Run red, then implement the complete module below.**

Run `D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/memory/rag/test_embedding_profile.py -q --basetemp=.pytest-tmp-bge-profile-red`.
Expected first failure: missing `embedding_profile` module, not a real API request.

```python
# hello_agents/memory/rag/embedding_profile.py
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import re
from typing import Mapping
from urllib.parse import urlsplit

from hello_agents.memory.rag.errors import RAGEmbeddingError


class EmbeddingFailure(RAGEmbeddingError):
    def __init__(self, code: str, *, status_code: int | None = None,
                 retryable: bool = False):
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(f"RAG embedding failed: {code}")


@dataclass(frozen=True)
class EmbeddingProfile:
    provider: str
    endpoint: str
    model: str
    revision: str
    dimension: int
    normalization: str = "l2-v1"
    document_preprocessing: str = "identity-v1"
    query_preprocessing: str = "identity-v1"
    chunking: str = "existing-rag-v1"
    distance: str = "Cosine"

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(asdict(self), sort_keys=True,
                             separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EmbeddingSettings:
    profile: EmbeddingProfile
    api_key: str = field(default="", repr=False, compare=False)
    timeout_seconds: float = 20
    max_retries: int = 2
    batch_size: int = 8

    @classmethod
    def from_env(cls, values: Mapping[str, str | None], *, candidate=False):
        def get(name, default):
            value = values.get("RAG_EMBEDDING_" + name, default)
            return str(value if value is not None else "").strip()

        provider = "siliconflow" if candidate else get("PROVIDER", "simple")
        if provider == "simple":
            return cls(EmbeddingProfile("simple", "", "SimpleEmbedding",
                                        "simple-v1", 384))
        if provider != "siliconflow":
            raise EmbeddingFailure("configuration")
        try:
            endpoint = get("BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
            parts = urlsplit(endpoint)
            if (parts.scheme != "https" or parts.hostname != "api.siliconflow.cn"
                    or parts.port not in (None, 443) or parts.username is not None
                    or parts.password is not None or parts.query or parts.fragment
                    or parts.path != "/v1" or "?" in endpoint or "#" in endpoint
                    or any(ch.isspace() for ch in endpoint)):
                raise ValueError()
            endpoint = "https://api.siliconflow.cn/v1"
            model = get("MODEL", "BAAI/bge-m3")
            dimension = int(get("DIMENSION", "1024"))
            revision = get("REVISION", "siliconflow-bge-m3-v1")
            api_key = get("API_KEY", "")
            timeout = float(get("TIMEOUT_SECONDS", "20"))
            retries = int(get("MAX_RETRIES", "2"))
            batch_size = int(get("BATCH_SIZE", "8"))
            if (model != "BAAI/bge-m3" or dimension != 1024
                    or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", revision)
                    or not api_key or not api_key.isascii()
                    or any(ord(ch) < 33 or ord(ch) == 127 for ch in api_key)
                    or not math.isfinite(timeout) or not 0 < timeout <= 20
                    or not 0 <= retries <= 2 or not 1 <= batch_size <= 8):
                raise ValueError()
        except (TypeError, ValueError, OverflowError):
            raise EmbeddingFailure("configuration") from None
        profile = EmbeddingProfile(provider, endpoint, model, revision, dimension)
        return cls(profile, api_key, timeout, retries, batch_size)


def normalize_vector(value: object, dimension: int) -> list[float]:
    if not isinstance(value, list) or len(value) != dimension:
        raise EmbeddingFailure("response_vector")
    if any(type(item) not in (float, int) for item in value):
        raise EmbeddingFailure("response_vector")
    try:
        vector = [float(item) for item in value]
    except (ValueError, OverflowError):
        raise EmbeddingFailure("response_vector") from None
    if not all(math.isfinite(item) for item in vector):
        raise EmbeddingFailure("response_vector")
    scale = max((abs(item) for item in vector), default=0)
    if scale == 0:
        raise EmbeddingFailure("response_vector")
    scaled = [item / scale for item in vector]
    norm = math.hypot(*scaled)
    return [item / norm for item in scaled]
```

The endpoint allowlist is specific to this provider adapter, not an assumption that
all future providers use SiliconFlow. Add future adapters under new provider names;
do not turn the key-bearing endpoint into an unrestricted user-facing proxy.
E2 will derive the actual pipeline-specific chunking/preprocessing identity before
activating an index; E1's profile describes the dormant client contract only.

- [ ] **Step 3: Run green and commit the Task 1 deliverable.**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/memory/rag/test_embedding_profile.py -q --basetemp=.pytest-tmp-bge-profile-green
git diff --check
git add -- hello_agents/memory/rag/embedding_profile.py tests/memory/rag/test_embedding_profile.py
git commit -m "feat(embedding): add isolated RAG embedding profiles"
```

Do not run the commit commands if verification fails. Record actual test counts in
the Progress section. There is intentionally no provider factory wired to the app yet.

## Task 2: Bounded remote client and protocol tests

**Files:** Create the Task 2 modules; add explicit `httpx==0.28.1` to `requirements.txt`.
The installed stable venv already has 0.28.1; this makes the runtime dependency direct,
not an accidental dependency of OpenAI/Gradio. Do not upgrade unrelated packages.

**Interfaces:** `SiliconFlowEmbedding(settings, transport=None)` provides
`embed_documents(texts: list[str]) -> list[list[float]]`, `embed_query(text: str) -> list[float]`,
and asynchronous counterparts `aembed_documents` / `aembed_query`. Async callers must
use the async API or an existing worker thread; sync invocation inside an event loop
fails explicitly instead of trying to nest `asyncio.run`.

- [ ] **Step 1: Add complete failing protocol and failure tests.**

```python
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
```

- [ ] **Step 2: Run red and add the complete client module.**

Run `D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/memory/rag/test_embedding_client.py -q --basetemp=.pytest-tmp-bge-client-red`.
Expected initial failure: missing `embedding_client` module.

```python
# hello_agents/memory/rag/embedding_client.py
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import random
import threading
import time

import httpx

from hello_agents.memory.rag.embedding_profile import (
    EmbeddingFailure, EmbeddingSettings, normalize_vector,
)

BATCH_BUDGET_SECONDS = 75.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_SLOTS = threading.BoundedSemaphore(2)
_RETRY_STATUSES = {429, 502, 503, 504}


def retry_delay(value: str | None, attempt: int) -> float | None:
    seconds = None
    if value:
        try:
            seconds = float(value)
        except ValueError:
            try:
                stamp = parsedate_to_datetime(value)
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                seconds = (stamp - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                pass
    if seconds is not None and math.isfinite(seconds):
        # Do not retry earlier than a long server-requested delay.
        return None if seconds > 10 else max(0, seconds)
    return min(10.0, 2 ** attempt + random.uniform(0, 0.2))


class SiliconFlowEmbedding:
    def __init__(self, settings: EmbeddingSettings, *, transport=None):
        if (settings.profile.provider != "siliconflow"
                or settings.profile.model != "BAAI/bge-m3"
                or settings.profile.dimension != 1024 or not settings.api_key):
            raise EmbeddingFailure("configuration")
        self.settings = settings
        self.profile = settings.profile
        self._transport = transport

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise EmbeddingFailure("sync_in_async")
        if not isinstance(texts, list):
            raise EmbeddingFailure("input")
        for text in texts:
            try:
                valid = (isinstance(text, str) and bool(text.strip())
                         and len(text.encode("utf-8")) <= 6000)
            except UnicodeEncodeError:
                valid = False
            if not valid:
                raise EmbeddingFailure("input")
        result = []
        for start in range(0, len(texts), self.settings.batch_size):
            deadline = time.monotonic() + BATCH_BUDGET_SECONDS
            if not _SLOTS.acquire(timeout=BATCH_BUDGET_SECONDS):
                raise EmbeddingFailure("concurrency_timeout", retryable=True)
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise EmbeddingFailure("deadline", retryable=True)
                result.extend(asyncio.run(self._batch(
                    texts[start:start + self.settings.batch_size], remaining)))
            finally:
                _SLOTS.release()
        return result

    async def aembed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_query, text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        # The worker retains its slot until its bounded request exits, even if
        # the caller cancels. No background worker writes to application data.
        return await asyncio.to_thread(self.embed_documents, texts)

    async def _batch(self, texts, remaining):
        try:
            async with asyncio.timeout(remaining):
                async with httpx.AsyncClient(
                    transport=self._transport,
                    timeout=self.settings.timeout_seconds,
                    trust_env=False, follow_redirects=False,
                    limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
                ) as client:
                    return await self._attempts(client, texts)
        except TimeoutError:
            raise EmbeddingFailure("deadline", retryable=True) from None

    async def _attempts(self, client, texts):
        last = EmbeddingFailure("unavailable", retryable=True)
        for attempt in range(self.settings.max_retries + 1):
            delay_header = None
            try:
                async with asyncio.timeout(self.settings.timeout_seconds):
                    async with client.stream(
                        "POST", self.profile.endpoint + "/embeddings",
                        headers={"Authorization": "Bearer " + self.settings.api_key,
                                 "Accept-Encoding": "identity"},
                        json={"model": self.profile.model, "input": texts,
                              "encoding_format": "float"},
                    ) as response:
                        status = response.status_code
                        if status != 200:
                            retryable = status in _RETRY_STATUSES
                            last = EmbeddingFailure("http", status_code=status,
                                                    retryable=retryable)
                            if not retryable:
                                raise last
                            delay_header = response.headers.get("Retry-After")
                        else:
                            body = bytearray()
                            async for block in response.aiter_bytes():
                                body.extend(block)
                                if len(body) > MAX_RESPONSE_BYTES:
                                    raise EmbeddingFailure("response_size")
                            return self._decode(bytes(body), len(texts))
            except (httpx.TimeoutException, httpx.ConnectError, TimeoutError):
                last = EmbeddingFailure("connection", retryable=True)
            except httpx.HTTPError:
                raise EmbeddingFailure("transport") from None
            if attempt == self.settings.max_retries:
                raise last from None
            delay = retry_delay(delay_header, attempt)
            if delay is None:
                raise last from None
            await asyncio.sleep(delay)
        raise last

    def _decode(self, body: bytes, count: int) -> list[list[float]]:
        try:
            data = json.loads(body)
        except (ValueError, UnicodeError, RecursionError):
            raise EmbeddingFailure("response_json") from None
        if (not isinstance(data, dict) or data.get("model") != self.profile.model
                or not isinstance(data.get("data"), list)
                or len(data["data"]) != count):
            raise EmbeddingFailure("response_schema")
        ordered = {}
        for item in data["data"]:
            if not isinstance(item, dict):
                raise EmbeddingFailure("response_schema")
            index = item.get("index")
            if type(index) is not int or not 0 <= index < count or index in ordered:
                raise EmbeddingFailure("response_index")
            ordered[index] = normalize_vector(item.get("embedding"), self.profile.dimension)
        return [ordered[index] for index in range(count)]
```

The asynchronous request deadline is intentional: HTTPX's read timeout alone is an
inactivity timeout, not a total-request deadline. Each call closes its HTTP client;
there is no cross-user embedding cache or long-lived event-loop thread.

- [ ] **Step 3: Verify, then commit Task 2.**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/memory/rag/test_embedding_profile.py tests/memory/rag/test_embedding_client.py -q --basetemp=.pytest-tmp-bge-client-green
D:\python_self_agent\venv\Scripts\python.exe -m pip check
git diff --check
git add -- hello_agents/memory/rag/embedding_client.py tests/memory/rag/test_embedding_client.py requirements.txt
git commit -m "feat(embedding): add bounded SiliconFlow BGE-M3 client"
```

Expected: all mock tests pass, including deadline/concurrency tests, and no new
dependency conflict. A fake-transport pass is not evidence that the real key works.

## Task 3: Explicit candidate probe and documented configuration

**Files:** Task 3 paths from the file map. Production `.env` is not edited until Task 4.

**Interfaces:** `python -m deploy.embedding_probe --env-file PATH` validates the candidate
configuration without network calls. Adding `--probe` sends exactly two fixed sentences
and one fixed query through BGE-M3. It exits 0 on success, 1 on known safe failures;
its JSON output never contains API keys or raw model responses. The parser reads only
the provided dotenv file with interpolation disabled; it does not merge LLM credentials
or unrelated process environment variables.

- [ ] **Step 1: Add complete failing probe/config tests.**

```python
# tests/deploy/test_embedding_probe.py
import json
from pathlib import Path
import httpx
import pytest

from deploy.embedding_probe import main, read_values
from hello_agents.memory.rag.embedding_client import SiliconFlowEmbedding


def write_env(tmp_path):
    path = tmp_path / ".env"
    path.write_text("LLM_API_KEY=keep-me\nRAG_EMBEDDING_PROVIDER=simple\n"
                    "RAG_EMBEDDING_API_KEY=private-test-token\n", encoding="utf-8")
    return path


def test_configuration_check_is_offline_and_does_not_modify_file(tmp_path, capsys):
    path = write_env(tmp_path)
    original = path.read_bytes()
    def forbidden(settings):
        pytest.fail("offline check constructed remote client")
    assert main(["--env-file", str(path)], client_factory=forbidden) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "configuration_valid"
    assert data["activation"] == "unchanged"
    assert data["key_configured"] is True
    assert "private-test-token" not in json.dumps(data)
    assert path.read_bytes() == original


def test_probe_uses_public_text_only_and_no_qdrant(tmp_path, capsys):
    path = write_env(tmp_path)
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body["input"])
        return httpx.Response(200, json={"model": "BAAI/bge-m3", "data": [
            {"index": i, "embedding": [1.0] + [0.0] * 1023}
            for i in range(len(body["input"]))
        ]})
    def factory(settings):
        return SiliconFlowEmbedding(settings, transport=httpx.MockTransport(handler))
    assert main(["--env-file", str(path), "--probe"], client_factory=factory) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "probe_passed"
    assert data["vector_count"] == 3
    assert calls == [["知研公开连通性测试：水在标准大气压下约一百度沸腾。",
                      "Public connectivity test: plants use sunlight for photosynthesis."],
                     ["What do plants use sunlight for?"]]
    assert "private-test-token" not in json.dumps(data)


def test_probe_errors_are_safe(tmp_path, capsys):
    path = write_env(tmp_path)
    def factory(settings):
        return SiliconFlowEmbedding(settings, transport=httpx.MockTransport(
            lambda request: httpx.Response(401, text="private-test-token")))
    assert main(["--env-file", str(path), "--probe"], client_factory=factory) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "failed" and data["http_status"] == 401
    assert "private-test-token" not in json.dumps(data)


def test_no_interpolation_or_duplicate_config(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET", "private-test-token")
    path = tmp_path / ".env"
    path.write_text("RAG_EMBEDDING_API_KEY=${SECRET}\n", encoding="utf-8")
    assert read_values(path)["RAG_EMBEDDING_API_KEY"] == "${SECRET}"
    path.write_text("RAG_EMBEDDING_API_KEY=one\nRAG_EMBEDDING_API_KEY=two\n",
                    encoding="utf-8")
    from hello_agents.memory.rag.embedding_profile import EmbeddingFailure
    with pytest.raises(EmbeddingFailure, match="environment_file"):
        read_values(path)


def test_missing_key_and_file_fail_without_network(tmp_path, capsys):
    missing = tmp_path / "missing.env"
    assert main(["--env-file", str(missing)]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "environment_file"
    path = tmp_path / ".env"
    path.write_text("LLM_API_KEY=keep-me\n", encoding="utf-8")
    assert main(["--env-file", str(path)]) == 1
    output = capsys.readouterr().out
    assert "keep-me" not in output
    assert json.loads(output)["code"] == "configuration"


def test_template_is_dormant_and_docker_copies_probe():
    root = Path(__file__).resolve().parents[2]
    template = (root / "deploy/.env.example").read_text(encoding="utf-8")
    assert "RAG_EMBEDDING_PROVIDER=simple" in template
    assert "RAG_EMBEDDING_MODEL=BAAI/bge-m3" in template
    assert "RAG_EMBEDDING_API_KEY=\n" in template
    assert "deploy/embedding_probe.py" in (root / "Dockerfile").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run red, then implement the complete probe.**

Run `D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/deploy/test_embedding_probe.py -q --basetemp=.pytest-tmp-bge-probe-red`.
Expected: missing probe module, later missing template/Docker additions until Step 3.

```python
# deploy/embedding_probe.py
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import re
import time

from dotenv import dotenv_values

from hello_agents.memory.rag.embedding_client import SiliconFlowEmbedding
from hello_agents.memory.rag.embedding_profile import EmbeddingFailure, EmbeddingSettings


def read_values(path: Path) -> dict[str, str | None]:
    try:
        text = path.read_text(encoding="utf-8-sig")
        seen = set()
        for line in text.splitlines():
            match = re.match(r"^\s*(?:export\s+)?(RAG_EMBEDDING_[A-Z_]+)\s*=", line)
            if match:
                if match[1] in seen:
                    raise ValueError()
                seen.add(match[1])
        return dict(dotenv_values(stream=io.StringIO(text), interpolate=False))
    except (OSError, UnicodeError, ValueError):
        raise EmbeddingFailure("environment_file") from None


def main(argv=None, *, client_factory=SiliconFlowEmbedding) -> int:
    parser = argparse.ArgumentParser(description="Check candidate RAG embedding settings")
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--probe", action="store_true",
                        help="Send fixed public test sentences to the candidate provider")
    args = parser.parse_args(argv)
    try:
        values = read_values(args.env_file)
        settings = EmbeddingSettings.from_env(values, candidate=True)
        report = {"status": "configuration_valid", "activation": "unchanged",
                  "key_configured": bool(settings.api_key),
                  "model": settings.profile.model,
                  "dimension": settings.profile.dimension,
                  "fingerprint": settings.profile.fingerprint}
        if args.probe:
            started = time.monotonic()
            client = client_factory(settings)
            documents = client.embed_documents([
                "知研公开连通性测试：水在标准大气压下约一百度沸腾。",
                "Public connectivity test: plants use sunlight for photosynthesis.",
            ])
            query = client.embed_query("What do plants use sunlight for?")
            report.update(status="probe_passed", vector_count=len(documents) + 1,
                          query_dimension=len(query),
                          elapsed_seconds=round(time.monotonic() - started, 3))
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except EmbeddingFailure as exc:
        print(json.dumps({"status": "failed", "code": exc.code,
                          "http_status": exc.status_code, "retryable": exc.retryable}))
        return 1
    except Exception:
        # The CLI boundary cannot emit a traceback containing request/file secrets.
        print(json.dumps({"status": "failed", "code": "internal"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Add the exact template, documentation and image-copy changes.**

Append this block to `deploy/.env.example` (after checking no duplicate RAG_EMBEDDING keys):

```dotenv
# Candidate embedding service. Keep simple until versioned-index migration passes.
# These settings never replace LLM_* or change personal Memory embeddings.
RAG_EMBEDDING_PROVIDER=simple
RAG_EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
RAG_EMBEDDING_MODEL=BAAI/bge-m3
RAG_EMBEDDING_API_KEY=
RAG_EMBEDDING_DIMENSION=1024
RAG_EMBEDDING_REVISION=siliconflow-bge-m3-v1
RAG_EMBEDDING_TIMEOUT_SECONDS=20
RAG_EMBEDDING_MAX_RETRIES=2
RAG_EMBEDDING_BATCH_SIZE=8
```

In `Dockerfile`, replace the existing deploy-file COPY instruction with exactly:

```dockerfile
COPY deploy/entrypoint.sh deploy/healthcheck.py deploy/backup.sh deploy/restore.sh deploy/smoke_test.py deploy/embedding_probe.py /app/deploy/
```

Append this operator section to `deploy/README.md`:

```markdown
## 候选文档嵌入配置与探测

免费模型使用 `BAAI/bge-m3`，不要选 `Pro/`。在本地 `deploy/.env` 的
`RAG_EMBEDDING_API_KEY=` 后填写密钥，不替换任何 `LLM_*`。
保持 `RAG_EMBEDDING_PROVIDER=simple`，直到版本化索引重建和切换验收完成。

仅校验配置，不联网：

    D:\python_self_agent\venv\Scripts\python.exe -m deploy.embedding_probe --env-file deploy/.env

明确发送内置公开句子进行候选模型连通性测试：

    D:\python_self_agent\venv\Scripts\python.exe -m deploy.embedding_probe --env-file deploy/.env --probe

探测输出不包含密钥、全文向量或业务文档。密钥只在本地填写，不发送到聊天。
API 费用以平台当前政策为准，免费服务也有限流。探测通过只证明候选配置
及请求协议可用，不代表文档检索已切换，不代替检索质量评测或 deep smoke。

客户端使用直接 HTTPS 连接，不继承系统代理。若网络必须使用企业代理，
应先评审代理配置与密钥传输边界，不能关闭 TLS 校验绕过连接失败。
```

- [ ] **Step 4: Run the complete E1 tests, then commit Task 3.**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/memory/rag/test_embedding_profile.py tests/memory/rag/test_embedding_client.py tests/deploy/test_embedding_probe.py tests/deploy/test_compose_contract.py -q --basetemp=.pytest-tmp-bge-probe-green
D:\python_self_agent\venv\Scripts\python.exe -m deploy.embedding_probe --help
git diff --check
git add -- deploy/embedding_probe.py tests/deploy/test_embedding_probe.py deploy/.env.example deploy/README.md Dockerfile
git commit -m "feat(embedding): add safe candidate probe and configuration guide"
```

Expected: all tests pass; help shows `--env-file` and `--probe` without reading files
or accessing the network. Do not run a real probe before the operator fills the key.

## Task 4: E1 integration and local credential handoff

**Files:** This plan/spec for progress; stable runtime-only `D:\python_self_agent\deploy\.env`.

**Interfaces:** Consume the verified E1 commits. Produce a stable checkout containing
the dormant CLI and a blank key field for the operator. This task does not activate
remote RAG or rebuild/restart the production image.

- [ ] **Step 1: Verify the full relevant baseline before stable integration.**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/memory tests/deploy -q --basetemp=.pytest-tmp-bge-e1-final
D:\python_self_agent\venv\Scripts\python.exe -m pip check
git diff --check
git status --short
```

If integration is needed from an execution worktree, inspect both branches/status and
integrate only verified E1 commits using the repository's agreed Git workflow. Preserve
the pre-existing stable edits listed in Global Constraints. Record actual commit IDs;
do not invent a branch or cherry-pick unrelated work. Rerun the E1 tests in stable cwd.

- [ ] **Step 2: Prepare the key field without reading secrets into output.**

Read only existing RAG_EMBEDDING key names and `git check-ignore deploy/.env`; do not
print the whole file or any existing value. If the segment is absent, use apply_patch
to add the exact dormant block from Task 3. If present, leave existing user values
unchanged and report only whether the key is set. Do not rewrite the whole `.env`,
change encoding/ACL or add a fake credential. Confirm no duplicate key names.

- [ ] **Step 3: Open the exact stable file and wait for the operator.**

Use `open_in_codex` for `D:\python_self_agent\deploy\.env` at the line containing
`RAG_EMBEDDING_API_KEY=`. Tell the operator to fill only that field and save; do not
request the key in chat. Report honestly: client/config prepared, runtime still legacy.

- [ ] **Step 4: After explicit “已配置”, run the public-text probe and record safe output.**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m deploy.embedding_probe --env-file deploy/.env
```

If exit code is zero, run:

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m deploy.embedding_probe --env-file deploy/.env --probe
```

Expected: `configuration_valid`, then `probe_passed`, 1024 dimensions and 3 vectors.
Neither command mutates Qdrant, registry, user data or `.env`. A 401 requires checking
the local credential; a 403 may require operator account action; rate limits must not
trigger a switch to paid models. Never print the key for diagnosis.

## Requirements remaining after E1 (E2 and E3 are not completed by this plan)

| Approved requirement | Detailed implementation stage and boundary |
| --- | --- |
| Per-pipeline profile, actual chunking/preprocessing identity | E2: RAG factories, `pipeline.py`, `qdrant_pipeline.py`, direct `document.py` helpers; no Memory singleton change |
| Prevent legacy padding/truncation and swallowed cache incompatibility | E2: both RAG paths; JSON versioned cache schema and new-cache paths |
| Protected fingerprint and user/document metadata | E2: prepare/batch integration and payload checks; system fields win over external metadata |
| Persisted registry and configuration/index mismatch rejection | E2: `data/vector_indexes/rag`, startup/read/write guards, explicit empty initialization |
| Legacy Memory isolation regression | E2: integration tests instantiate actual Memory modules with RAG config present and verify no remote requests |
| Source inventory, paginated rebuild, ownership checks, resumable digest/checkpoint | E3: separate admin CLI; no unbounded VectorStore.scroll over entire deployment |
| Cold backup and maintenance coordination | E3: existing Windows operations.lock/inherited-lock contract; stop/drain App, no simultaneous backup/update |
| Durable cutover journal and safe recovery | E3: paired registry/env transition and restart; no claim of cross-file atomicity |
| Rollback after new writes | E3: detect changes and rebuild from current chunk state; never revert to stale data blindly |
| 20-case bilingual Recall@5 ≥ 0.90 / MRR@5 ≥ 0.75 and no regression | E3: fixed fixture, isolated collections, zero scope leakage; no lowering gates to force a pass |
| Stable deployment, live retrieval, deep smoke and preservation of business data | E3: explicit staged acceptance after key configured and E2/E3 tests pass |
| Resource/disk/retention and host-binding follow-ons | Separate already-authorized subprojects after embedding acceptance; not silently bundled into E1 |

E2's plan must be written against the actual E1 code before its implementation. E3's
plan must similarly consume E2's tested registry/runtime interfaces. These are ordered
continuations of the approved spec, not reasons to ask again which embedding model to use.

## Plan self-review and progress

- [x] Re-read approved spec, repository context, current settings/deployment contracts.
- [x] Use a bounded E1 plan with named follow-on ownership for every remaining spec section.
- [x] Provide complete initial modules and tests, explicit commands and expected outcomes.
- [x] Static self-review: six Python code blocks parsed with the project venv in UTF-8 mode; no unfinished-code markers found. This is syntax validation, not executed unit tests.
- [ ] Execute Task 1 red/green and record results.
- [ ] Execute Task 2 red/green and record results.
- [ ] Execute Task 3 red/green and record results.
- [ ] Integrate E1, open key entry and record operator confirmation/probe result.

Planning is not implementation or live model verification. At plan creation the real
key has not been read and no remote inference, container restart or index write has run.

## References used to verify protocol choices

- [SiliconFlow embedding API](https://api-docs.siliconflow.cn/docs/api/embeddings-post):
  BGE-M3 request fields, fixed dimensions and response index contract.
- [HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/): separate connect,
  read, write and pool inactivity limits; use an outer async deadline as well.
- [HTTPX transports](https://www.python-httpx.org/advanced/transports/): mock transport
  tests and no implicit multi-layer retry policy.
