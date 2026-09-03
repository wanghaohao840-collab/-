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
