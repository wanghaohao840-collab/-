"""Executable, exact-profile embedding engines for the isolated import role."""

from __future__ import annotations

import math
import os
import time
from threading import Lock, Thread
from typing import Mapping

from app.isolated_import_runtime import IsolatedImportSettings, ProviderReadiness
from hello_agents.memory.rag.embedding_profile import EmbeddingFailure
from hello_agents.memory.rag.embedding_runtime import RAGEmbeddingRuntime, build_rag_embedding


_SUFFIXES = ("PROVIDER", "BASE_URL", "MODEL", "REVISION", "DIMENSION",
             "API_KEY", "TIMEOUT_SECONDS", "MAX_RETRIES", "BATCH_SIZE")
_PROBE_TEXT = "isolated import provider readiness"


class IsolatedProviderConfigurationError(ValueError):
    """Code-only refusal; never includes credentials or configured values."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _mapping(env: Mapping[str, str] | None, prefix: str,
             settings: IsolatedImportSettings) -> dict[str, str]:
    values = os.environ if env is None else env
    provider = values.get(prefix + "PROVIDER")
    if provider not in ("simple", "siliconflow"):
        raise IsolatedProviderConfigurationError("provider_configuration")
    mapped = {"RAG_EMBEDDING_" + suffix: values[prefix + suffix]
              for suffix in _SUFFIXES if prefix + suffix in values}
    try:
        if provider == "simple":
            canonical = {"BASE_URL": "", "MODEL": "SimpleEmbedding",
                         "REVISION": "simple-v1", "DIMENSION": "384", "API_KEY": ""}
            if any(suffix in canonical and values.get(prefix + suffix) != expected
                   for suffix, expected in canonical.items()
                   if prefix + suffix in values):
                raise ValueError()
            batch = int(mapped.get("RAG_EMBEDDING_BATCH_SIZE", "8"))
            if not 1 <= batch <= 8:
                raise ValueError()
        supplied_timeout = float(mapped.get("RAG_EMBEDDING_TIMEOUT_SECONDS", "20"))
        supplied_retries = int(mapped.get("RAG_EMBEDDING_MAX_RETRIES", "2"))
        if (not math.isfinite(supplied_timeout) or supplied_timeout <= 0
                or not 0 <= supplied_retries <= 2):
            raise ValueError()
        mapped["RAG_EMBEDDING_TIMEOUT_SECONDS"] = str(min(
            supplied_timeout, 20, settings.provider_timeout_seconds))
        mapped["RAG_EMBEDDING_MAX_RETRIES"] = str(min(
            supplied_retries, settings.provider_max_retries, 2))
    except (TypeError, ValueError, OverflowError):
        raise IsolatedProviderConfigurationError("provider_configuration") from None
    return mapped


class IsolatedImportProviders:
    def __init__(self, settings: IsolatedImportSettings, rag_runtime: RAGEmbeddingRuntime,
                 episode_runtime: RAGEmbeddingRuntime):
        if (rag_runtime.profile != settings.rag_profile
                or episode_runtime.profile != settings.episode_profile):
            raise IsolatedProviderConfigurationError("provider_profile_mismatch")
        self.settings = settings
        self.rag_runtime = rag_runtime
        self.episode_runtime = episode_runtime
        self._lock = Lock()
        self._closed = False
        self._probe_thread: Thread | None = None

    @property
    def active_threads(self) -> tuple[Thread, ...]:
        with self._lock:
            thread = self._probe_thread
            return (thread,) if thread is not None and thread.is_alive() else ()

    def _unavailable(self) -> ProviderReadiness:
        return ProviderReadiness(False, self.rag_runtime.profile,
                                 self.episode_runtime.profile)

    def probe(self) -> ProviderReadiness:
        deadline = time.monotonic() + self.settings.provider_timeout_seconds
        result: list[tuple[bool, float]] = []

        def check() -> None:
            try:
                for runtime in (self.rag_runtime, self.episode_runtime):
                    with self._lock:
                        if self._closed or time.monotonic() >= deadline:
                            return
                    vector = runtime.embed_query(_PROBE_TEXT)
                    if (len(vector) != runtime.profile.dimension
                            or not all(type(value) in (int, float) and math.isfinite(value)
                                       for value in vector)
                            or not math.isclose(math.hypot(*vector), 1.0,
                                                rel_tol=1e-5, abs_tol=1e-5)):
                        return
                with self._lock:
                    if not self._closed and time.monotonic() < deadline:
                        result.append((True, time.monotonic()))
            except Exception:
                return

        with self._lock:
            if (self._closed or self._probe_thread is not None
                    and self._probe_thread.is_alive()):
                return self._unavailable()
            thread = Thread(target=check, name="isolated-provider-probe", daemon=False)
            self._probe_thread = thread
            thread.start()
        thread.join(max(0.0, deadline - time.monotonic()))
        with self._lock:
            available = (not self._closed and not thread.is_alive()
                         and time.monotonic() < deadline and bool(result)
                         and result[0][0] and result[0][1] < deadline)
        return ProviderReadiness(available, self.rag_runtime.profile,
                                 self.episode_runtime.profile)

    def close(self, *, timeout_seconds: float) -> bool:
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or timeout_seconds < 0):
            raise ValueError("provider_close_timeout")
        with self._lock:
            self._closed = True
            thread = self._probe_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout_seconds)
        return thread is None or not thread.is_alive()


def open_isolated_providers(settings: IsolatedImportSettings, *, env=None,
                            rag_transport=None, episode_transport=None) -> IsolatedImportProviders:
    if type(settings) is not IsolatedImportSettings:
        raise IsolatedProviderConfigurationError("provider_settings")
    try:
        rag = build_rag_embedding(_mapping(env, "ISOLATED_IMPORT_RAG_EMBEDDING_", settings),
                                  backend="qdrant", transport=rag_transport)
        episode = build_rag_embedding(_mapping(env, "ISOLATED_IMPORT_EPISODE_EMBEDDING_", settings),
                                      backend="qdrant", transport=episode_transport)
    except EmbeddingFailure as error:
        if error.code == "configuration":
            raise IsolatedProviderConfigurationError("provider_configuration") from None
        raise
    return IsolatedImportProviders(settings, rag, episode)
