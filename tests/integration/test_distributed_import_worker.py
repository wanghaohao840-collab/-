"""Task 04 worker and provider contracts; real cases use disposable services."""

from dataclasses import asdict, replace
from contextlib import contextmanager
from inspect import signature
import json
import io
import os
from pathlib import Path
from threading import Event, Thread
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.distributed_import_worker import IsolatedImportWorker, WorkerExit
from app.isolated_import_provider import (
    IsolatedProviderConfigurationError, open_isolated_providers,
)
from app.isolated_import_runtime import IsolatedImportRuntime, IsolatedImportSettings
from app.postgres_import_leases import ImportAttempt
from hello_agents.memory.rag.embedding_profile import EmbeddingSettings
from hello_agents.memory.rag.embedding_runtime import profile_for_backend


def _settings():
    profile = profile_for_backend(EmbeddingSettings.from_env({}).profile, "qdrant")
    users = (str(uuid4()), str(uuid4()))
    values = {
        "ISOLATED_IMPORT_ENABLED": "1",
        "ISOLATED_IMPORT_DATABASE_URL":
            "postgresql://worker:private@localhost/test?options=-csearch_path%3Dp4_unit",
        "ISOLATED_IMPORT_SCHEMA": "p4_unit",
        "ISOLATED_IMPORT_S3_ENDPOINT_URL": "http://127.0.0.1:9000",
        "ISOLATED_IMPORT_S3_BUCKET": "p4-unit-bucket",
        "ISOLATED_IMPORT_QDRANT_URL": "http://127.0.0.1:6333",
        "ISOLATED_IMPORT_RAG_COLLECTION": "p4_rag",
        "ISOLATED_IMPORT_EPISODE_COLLECTION": "p4_episode",
        "ISOLATED_IMPORT_RAG_PROFILE_JSON": json.dumps(asdict(profile)),
        "ISOLATED_IMPORT_EPISODE_PROFILE_JSON": json.dumps(asdict(profile)),
        "ISOLATED_IMPORT_TRUSTED_USER_IDS": ",".join(users),
        "ISOLATED_IMPORT_PROVIDER_TIMEOUT_SECONDS": "1",
        "ISOLATED_IMPORT_STOP_WAIT_SECONDS": "1",
    }
    return IsolatedImportSettings.from_env("worker", values)


def _providers(settings):
    return open_isolated_providers(settings, env={
        "ISOLATED_IMPORT_RAG_EMBEDDING_PROVIDER": "simple",
        "ISOLATED_IMPORT_EPISODE_EMBEDDING_PROVIDER": "simple",
    })


def _worker(settings=None, providers=True):
    settings = settings or _settings()
    runtime = IsolatedImportRuntime(settings, SimpleNamespace(), None)
    bundle = _providers(settings) if providers else None
    return IsolatedImportWorker(runtime, bundle, worker_id="unit-worker")


def _attempt(user):
    return ImportAttempt(
        SimpleNamespace(user_id=user, task_id=str(uuid4()), document_id=str(uuid4()),
                        original_name="source.txt", file_suffix=".txt"),
        "unit-worker", uuid4(), 1, SimpleNamespace(), SimpleNamespace(),
        "p4-unit-bucket", None)


def test_provider_contract_import():
    from app.isolated_import_provider import IsolatedImportProviders, open_isolated_providers

    assert IsolatedImportProviders is not None
    assert callable(open_isolated_providers)
    assert tuple(signature(open_isolated_providers).parameters) == (
        "settings", "env", "rag_transport", "episode_transport"
    )


def test_worker_contract_import():
    from app.distributed_import_worker import IsolatedImportWorker, WorkerExit

    assert WorkerExit is not None
    assert tuple(signature(IsolatedImportWorker).parameters) == (
        "runtime", "providers", "worker_id"
    )


def test_exact_simple_provider_and_invalid_configuration_are_code_only():
    settings = _settings()
    bundle = _providers(settings)
    assert bundle.rag_runtime.profile == settings.rag_profile
    assert bundle.episode_runtime.profile == settings.episode_profile
    assert len(bundle.rag_runtime.embed_query("document")) == 384
    assert bundle.probe().available
    assert bundle.close(timeout_seconds=0)
    assert not bundle.probe().available

    for values in ({}, {"ISOLATED_IMPORT_RAG_EMBEDDING_PROVIDER": "simple"},
                   {"ISOLATED_IMPORT_RAG_EMBEDDING_PROVIDER": "simple",
                    "ISOLATED_IMPORT_EPISODE_EMBEDDING_PROVIDER": "simple",
                    "ISOLATED_IMPORT_EPISODE_EMBEDDING_DIMENSION": "4"}):
        with pytest.raises(IsolatedProviderConfigurationError) as refused:
            open_isolated_providers(settings, env=values)
        assert refused.value.code in {"provider_configuration", "provider_profile_mismatch"}
        assert "private" not in repr(refused.value)
    wrong = replace(settings, episode_profile=replace(settings.episode_profile,
                                                       dimension=4))
    with pytest.raises(IsolatedProviderConfigurationError, match="provider_profile_mismatch"):
        _providers(wrong)


def test_provider_probe_busy_late_and_close_are_retained(monkeypatch):
    settings = _settings()
    bundle = _providers(settings)
    entered, release = Event(), Event()
    real_encode = bundle.rag_runtime._engine.encode
    episode_calls = []
    monkeypatch.setattr(bundle.episode_runtime._engine, "encode",
                        lambda text: episode_calls.append(text))

    def delayed(text):
        entered.set()
        assert release.wait(5)
        return real_encode(text)

    monkeypatch.setattr(bundle.rag_runtime._engine, "encode", delayed)
    try:
        first = bundle.probe()
        assert entered.is_set() and not first.available
        live = bundle.active_threads
        assert len(live) == 1 and live[0].is_alive()
        assert not bundle.probe().available
        assert bundle.active_threads == live
        assert not bundle.close(timeout_seconds=0)
        release.set()
        live[0].join(5)
        assert not bundle.active_threads
        assert episode_calls == []
        assert bundle.close(timeout_seconds=0)
        assert not bundle.probe().available
    finally:
        release.set()
        for thread in bundle.active_threads:
            thread.join(5)


def test_provider_timeout_without_close_never_starts_late_second_engine(monkeypatch):
    bundle = _providers(_settings())
    entered, release = Event(), Event()
    episode_calls = []
    original = bundle.rag_runtime._engine.encode

    def held(text):
        entered.set()
        assert release.wait(5)
        return original(text)

    monkeypatch.setattr(bundle.rag_runtime._engine, "encode", held)
    monkeypatch.setattr(bundle.episode_runtime._engine, "encode",
                        lambda text: episode_calls.append(text))
    try:
        assert not bundle.probe().available
        assert entered.is_set()
        release.set()
        for thread in bundle.active_threads:
            thread.join(5)
        assert episode_calls == []
        assert bundle.close(timeout_seconds=0)
    finally:
        release.set()
        for thread in bundle.active_threads:
            thread.join(5)


def test_delayed_probe_observer_cannot_report_late_success(monkeypatch):
    import app.isolated_import_provider as provider_module

    actual_thread = provider_module.Thread

    class DelayedJoin(actual_thread):
        def join(self, timeout=None):
            super().join(timeout)
            time.sleep(1.1)

    monkeypatch.setattr(provider_module, "Thread", DelayedJoin)
    bundle = _providers(_settings())
    try:
        assert not bundle.probe().available
        assert bundle.active_threads == ()
    finally:
        assert bundle.close(timeout_seconds=0)


def test_later_tenant_probe_remains_visible_and_recovery_reachable(monkeypatch):
    worker = _worker()
    first, second = sorted(worker.runtime.trusted_user_ids)
    entered, release = Event(), Event()
    original_encode = worker.providers.rag_runtime._engine.encode
    claims = []

    def delayed(text):
        entered.set()
        assert release.wait(5)
        return original_encode(text)

    monkeypatch.setattr(worker.providers.rag_runtime._engine, "encode", delayed)
    worker.runtime.ordinary_ready = lambda user: (
        True if user == first else worker.providers.probe().available)
    worker.leases.claim_next = lambda *_a, **_kw: claims.append(True)
    worker.runtime.recovery_ready = lambda: True
    worker.leases.recover_expired = lambda **_kw: 0
    worker.recovery.claim_next = lambda *_a, **_kw: None
    try:
        assert worker.run_one_ordinary() is False
        assert entered.is_set() and claims == []
        assert len(worker.providers.active_threads) == 1
        assert worker.run_one_recovery() is None
        assert worker.run_one_ordinary() is False
        report = worker.close()
        assert not report.clean and report.active_threads == 2
    finally:
        release.set()
        for thread in worker.providers.active_threads:
            thread.join(5)
        worker._lanes["cleanup"].thread.join(5)
    assert worker.close().clean


def test_mock_remote_transport_checks_exact_profile_without_live_claim():
    import httpx

    selected = {
        "RAG_EMBEDDING_PROVIDER": "siliconflow",
        "RAG_EMBEDDING_API_KEY": "test-only-key",
    }
    profile = profile_for_backend(EmbeddingSettings.from_env(selected).profile,
                                  "qdrant")
    settings = replace(_settings(), rag_profile=profile, episode_profile=profile)
    seen = []

    def response(request):
        seen.append(request)
        return httpx.Response(200, json={"model": "BAAI/bge-m3",
                                         "data": [{"index": 0,
                                                   "embedding": [1.0] + [0.0] * 1023}]})

    env = {f"ISOLATED_IMPORT_{kind}_EMBEDDING_PROVIDER": "siliconflow"
           for kind in ("RAG", "EPISODE")}
    for kind in ("RAG", "EPISODE"):
        env[f"ISOLATED_IMPORT_{kind}_EMBEDDING_API_KEY"] = "test-only-key"
        env[f"ISOLATED_IMPORT_{kind}_EMBEDDING_TIMEOUT_SECONDS"] = "20"
        env[f"ISOLATED_IMPORT_{kind}_EMBEDDING_MAX_RETRIES"] = "2"
    bundle = open_isolated_providers(settings, env=env,
        rag_transport=httpx.MockTransport(response),
        episode_transport=httpx.MockTransport(response))
    try:
        assert bundle.rag_runtime._engine.settings.timeout_seconds <= 1
        assert bundle.rag_runtime._engine.settings.max_retries <= 2
        assert bundle.probe().available
        assert len(seen) == 2
        assert all(request.url.host == "api.siliconflow.cn" for request in seen)
    finally:
        assert bundle.close(timeout_seconds=1)


@pytest.mark.parametrize("bad", (
    {"ISOLATED_IMPORT_RAG_EMBEDDING_API_KEY": ""},
    {"ISOLATED_IMPORT_RAG_EMBEDDING_MAX_RETRIES": "3"},
    {"ISOLATED_IMPORT_RAG_EMBEDDING_TIMEOUT_SECONDS": "nan"},
    {"ISOLATED_IMPORT_RAG_EMBEDDING_BATCH_SIZE": "9"},
))
def test_remote_configuration_refusal_is_code_only(bad):
    selected = {"RAG_EMBEDDING_PROVIDER": "siliconflow",
                "RAG_EMBEDDING_API_KEY": "test-only-secret"}
    profile = profile_for_backend(EmbeddingSettings.from_env(selected).profile,
                                  "qdrant")
    settings = replace(_settings(), rag_profile=profile, episode_profile=profile)
    env = {f"ISOLATED_IMPORT_{kind}_EMBEDDING_PROVIDER": "siliconflow"
           for kind in ("RAG", "EPISODE")}
    for kind in ("RAG", "EPISODE"):
        env[f"ISOLATED_IMPORT_{kind}_EMBEDDING_API_KEY"] = "test-only-secret"
    env.update(bad)
    with pytest.raises(IsolatedProviderConfigurationError) as error:
        open_isolated_providers(settings, env=env)
    assert error.value.code in {"provider_configuration", "provider_profile_mismatch"}
    assert "test-only-secret" not in str(error.value) + repr(error.value)


def test_ambient_provider_config_ignored_and_profile_transform_rejected(monkeypatch):
    settings = _settings()
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "siliconflow")
    monkeypatch.setenv("RAG_EMBEDDING_API_KEY", "ambient-secret")
    bundle = _providers(settings)
    try:
        assert bundle.probe().available
        assert bundle.rag_runtime.profile == settings.rag_profile
    finally:
        assert bundle.close(timeout_seconds=0)
    wrong = replace(settings, rag_profile=replace(settings.rag_profile,
                                                  query_preprocessing="new-transform"))
    with pytest.raises(IsolatedProviderConfigurationError) as error:
        _providers(wrong)
    assert error.value.code in {"provider_configuration", "provider_profile_mismatch"}


def test_remote_semaphore_and_retry_work_remain_visible_outside_one_second_budget(
        monkeypatch):
    import httpx
    from threading import current_thread
    import hello_agents.memory.rag.embedding_client as client_module

    selected = {"RAG_EMBEDDING_PROVIDER": "siliconflow",
                "RAG_EMBEDDING_API_KEY": "mock-key"}
    profile = profile_for_backend(EmbeddingSettings.from_env(selected).profile,
                                  "qdrant")
    settings = replace(_settings(), rag_profile=profile, episode_profile=profile)
    env = {f"ISOLATED_IMPORT_{kind}_EMBEDDING_PROVIDER": "siliconflow"
           for kind in ("RAG", "EPISODE")}
    for kind in ("RAG", "EPISODE"):
        env[f"ISOLATED_IMPORT_{kind}_EMBEDDING_API_KEY"] = "mock-key"
        env[f"ISOLATED_IMPORT_{kind}_EMBEDDING_BATCH_SIZE"] = "8"
    document_requests = []
    probe_requests = []

    def response(request):
        inputs = json.loads(request.content)["input"]
        if inputs[0] == "document":
            document_requests.append((len(inputs), time.monotonic()))
            if len(document_requests) == 1:
                return httpx.Response(503, headers={"Retry-After": "0.2"})
        else:
            probe_requests.append(len(inputs))
        return httpx.Response(200, json={"model": "BAAI/bge-m3", "data": [
            {"index": index, "embedding": [1.0] + [0.0] * 1023}
            for index in range(len(inputs))]})

    bundle = open_isolated_providers(settings, env=env,
        rag_transport=httpx.MockTransport(response),
        episode_transport=httpx.MockTransport(response))
    slots = client_module._SLOTS
    document_enter, probe_enter, release = Event(), Event(), Event()

    class HeldSlots:
        def acquire(self, timeout):
            (probe_enter if current_thread().name == "isolated-provider-probe"
             else document_enter).set()
            return release.wait(5)

        def release(self):
            pass

    monkeypatch.setattr(client_module, "_SLOTS", HeldSlots())
    result = []
    thread = Thread(target=lambda: result.append(
        bundle.rag_runtime.embed_documents(["document"] * 9)))
    thread.start()
    try:
        assert document_enter.wait(2)
        assert thread.is_alive()
        assert not bundle.probe().available
        assert probe_enter.is_set()
        assert thread.is_alive() and len(bundle.active_threads) == 1
        release.set()
        thread.join(5)
        assert not thread.is_alive()
        for active in bundle.active_threads:
            active.join(5)
        assert len(result[0]) == 9
        assert [size for size, _at in document_requests] == [8, 8, 1]
        assert document_requests[1][1] - document_requests[0][1] >= 0.18
        assert probe_requests == [1]
    finally:
        release.set()
        thread.join(5)
        for active in bundle.active_threads:
            active.join(5)
        assert bundle.close(timeout_seconds=0)
        monkeypatch.setattr(client_module, "_SLOTS", slots)


def test_remote_multibatch_outlives_worker_outer_budget_without_late_stage():
    import httpx

    selected = {"RAG_EMBEDDING_PROVIDER": "siliconflow",
                "RAG_EMBEDDING_API_KEY": "mock-key"}
    profile = profile_for_backend(EmbeddingSettings.from_env(selected).profile,
                                  "qdrant")
    settings = replace(_settings(), rag_profile=profile, episode_profile=profile,
                       provider_timeout_seconds=1, attempt_seconds=4)
    env = {f"ISOLATED_IMPORT_{kind}_EMBEDDING_PROVIDER": "siliconflow"
           for kind in ("RAG", "EPISODE")}
    for kind in ("RAG", "EPISODE"):
        env[f"ISOLATED_IMPORT_{kind}_EMBEDDING_API_KEY"] = "mock-key"
    entered, release = Event(), Event()
    sizes, next_stages = [], []

    def response(request):
        inputs = json.loads(request.content)["input"]
        sizes.append(len(inputs))
        if len(sizes) == 1:
            entered.set()
            assert release.wait(5)
        return httpx.Response(200, json={"model": "BAAI/bge-m3", "data": [
            {"index": index, "embedding": [1.0] + [0.0] * 1023}
            for index in range(len(inputs))]})

    bundle = open_isolated_providers(settings, env=env,
        rag_transport=httpx.MockTransport(response),
        episode_transport=httpx.MockTransport(response))
    runtime = IsolatedImportRuntime(settings, SimpleNamespace(close=lambda: None), None)
    worker = IsolatedImportWorker(runtime, bundle, worker_id="p4-remote-budget")
    original = _attempt(min(settings.trusted_user_ids))
    runtime.ordinary_ready = lambda _user: True
    runtime.recovery_ready = lambda: True
    worker.leases.claim_next = lambda *_a, **_kw: original
    worker.leases.recover_expired = lambda **_kw: 0
    worker.recovery.claim_next = lambda *_a, **_kw: None

    def forward(_original, abort, deadline):
        if not worker._stage("prepare", abort, deadline, provider=True):
            return
        bundle.rag_runtime.embed_documents(["chunk"] * 9)
        worker._stage_end()
        if worker._stage("issue", abort, deadline):
            next_stages.append(True)

    worker._forward = forward
    try:
        assert worker.run_one_ordinary()
        assert entered.is_set() and worker._lanes["attempt"].thread.is_alive()
        assert worker.run_one_recovery() is None
        release.set()
        worker._lanes["attempt"].thread.join(5)
        assert not worker._lanes["attempt"].thread.is_alive()
        assert sizes == [8, 1] and next_stages == []
    finally:
        release.set()
        if "attempt" in worker._lanes:
            worker._lanes["attempt"].thread.join(5)
        assert worker.close().clean


def test_recovery_only_has_no_ordinary_reads_or_claims():
    worker = _worker(providers=False)
    worker.runtime.ordinary_ready = lambda *_: pytest.fail("ordinary readiness")
    worker.leases.claim_next = lambda *_a, **_k: pytest.fail("ordinary claim")
    assert worker.recovery_only
    assert worker.run_one_ordinary() is False
    exit_state = worker.close()
    assert exit_state.clean and exit_state.reason == "recovery_only"


def test_preissue_authority_uses_original_combined_live_contract():
    worker = _worker()
    original = _attempt(min(worker.runtime.trusted_user_ids))
    seen = []

    class Cursor:
        def execute(self, sql):
            seen.append(sql)

    @contextmanager
    def transaction():
        yield Cursor()

    worker.runtime.database.transaction = transaction
    worker.leases._live = lambda cursor, attempt, **kw: seen.append(
        (cursor, attempt, kw))
    try:
        worker._authority(original)
        assert seen[0] == "begin"
        assert seen[1][1] is original
        assert seen[1][2] == {"status": "running", "ended": False}
    finally:
        worker.close()


def test_heartbeat_keeps_original_attempt_after_returned_renewal():
    settings = replace(_settings(), heartbeat_seconds=1)
    worker = _worker(settings)
    original = _attempt(min(settings.trusted_user_ids))
    abort = Event()
    calls = []

    def heartbeat(attempt, *, lease_seconds):
        calls.append(attempt)
        if len(calls) == 2:
            abort.set()
        return object()

    worker.leases.heartbeat = heartbeat
    owner = Thread(target=worker._heartbeat,
                   args=(original, abort, Event(), time.monotonic() + 5))
    owner.start()
    try:
        owner.join(4)
        assert not owner.is_alive()
        assert len(calls) == 2 and all(value is original for value in calls)
    finally:
        abort.set()
        owner.join(5)
        worker.close()


def test_ready_subset_is_fresh_and_late_claim_cannot_start_forward(monkeypatch):
    worker = _worker()
    allowed = min(worker.runtime.trusted_user_ids)
    selected = []
    entered, release = Event(), Event()
    worker.runtime.ordinary_ready = lambda user: user == allowed

    def claim(_worker_id, *, lease_seconds, allowed_user_ids):
        selected.append(allowed_user_ids)
        entered.set()
        assert release.wait(5)
        return _attempt(allowed)

    worker.leases.claim_next = claim
    forwarded = []
    monkeypatch.setattr(worker, "_forward", lambda *_: forwarded.append(True))
    try:
        assert worker.run_one_ordinary() is False
        assert entered.is_set()
        assert selected == [frozenset({allowed})]
        assert worker.run_one_ordinary() is False
        late = worker._lanes["preflight"].thread
        assert late.is_alive()
        release.set()
        late.join(5)
        assert forwarded == []
    finally:
        release.set()
        report = worker.close()
    assert report.ordinary_attempts == 1
    assert worker.close().ordinary_attempts == 1


def test_delayed_observer_cannot_transfer_finished_claim(monkeypatch):
    worker = _worker()
    user = min(worker.runtime.trusted_user_ids)
    original = _attempt(user)
    entered, release = Event(), Event()
    calls = []
    worker.runtime.ordinary_ready = lambda selected: selected == user
    worker.leases.claim_next = lambda *_a, **_kw: (calls.append("claim"), original)[1]
    monkeypatch.setattr(worker, "_forward", lambda *_a: calls.append("forward"))
    actual_observe = worker._observe

    def late_observe(operation):
        entered.set()
        assert release.wait(5)
        return actual_observe(operation)

    monkeypatch.setattr(worker, "_observe", late_observe)
    caller = Thread(target=worker.run_one_ordinary)
    caller.start()
    try:
        assert entered.wait(5)
        time.sleep(1.1)
        release.set()
        caller.join(5)
        assert not caller.is_alive()
        assert calls == ["claim"]
        assert worker.close().ordinary_attempts == 1
    finally:
        release.set()
        caller.join(5)
        worker.close()


def test_late_readiness_cannot_start_ordinary_claim_even_with_delayed_observer(
        monkeypatch):
    worker = _worker()
    entered, release = Event(), Event()
    observer_enter, observer_release = Event(), Event()
    claims = []

    def ready(_user):
        entered.set()
        assert release.wait(5)
        return True

    worker.runtime.ordinary_ready = ready
    worker.leases.claim_next = lambda *_a, **_kw: claims.append(True)
    actual_observe = worker._observe

    def held_observer(operation):
        observer_enter.set()
        assert observer_release.wait(5)
        return actual_observe(operation)

    monkeypatch.setattr(worker, "_observe", held_observer)
    caller = Thread(target=worker.run_one_ordinary)
    caller.start()
    try:
        assert entered.wait(5) and observer_enter.wait(5)
        time.sleep(1.1)
        release.set()
        preflight = worker._lanes["preflight"]
        preflight.thread.join(5)
        assert not preflight.thread.is_alive()
        assert not preflight.abort.is_set() and claims == []
        observer_release.set()
        caller.join(5)
        assert not caller.is_alive() and claims == []
    finally:
        release.set()
        observer_release.set()
        caller.join(5)
        worker.close()


@pytest.mark.parametrize("blocked_stage,next_stage", [
    ("ready", "expiry"), ("expiry", "claim"),
    ("claim", "renew"), ("renew", "prove"),
])
def test_recovery_late_stage_cannot_start_next_stage(blocked_stage, next_stage):
    worker = _worker(providers=False)
    entered, release = Event(), Event()
    observer_enter, observer_release = Event(), Event()
    seen = []

    def stage(name, value):
        seen.append(name)
        if name == blocked_stage:
            entered.set()
            assert release.wait(5)
        return value

    worker.runtime.recovery_ready = lambda: stage("ready", True)
    worker.leases.recover_expired = lambda **_kw: stage("expiry", 0)
    worker.recovery.claim_next = lambda *_a, **_kw: stage("claim", object())
    worker.recovery.renew = lambda *_a, **_kw: stage("renew", object())
    worker.recovery.prove_or_hold = lambda *_a: stage("prove", "retry_later")
    actual_observe = worker._observe

    def held_observer(operation):
        observer_enter.set()
        assert observer_release.wait(5)
        return actual_observe(operation)

    worker._observe = held_observer
    caller = Thread(target=worker.run_one_recovery)
    caller.start()
    try:
        assert entered.wait(5) and observer_enter.wait(5)
        time.sleep(1.1)
        release.set()
        recovery = worker._lanes["recovery"]
        recovery.thread.join(5)
        assert not recovery.thread.is_alive()
        assert not recovery.abort.is_set() and next_stage not in seen
        observer_release.set()
        caller.join(5)
        assert not caller.is_alive()
        assert next_stage not in seen
    finally:
        release.set()
        observer_release.set()
        caller.join(5)
        worker.close()


def test_two_ordinary_callers_share_one_claim_through_transfer_and_attempt(
        monkeypatch):
    worker = _worker(replace(_settings(), provider_timeout_seconds=3))
    user = min(worker.runtime.trusted_user_ids)
    original = _attempt(user)
    pending, transfer, active, finish = Event(), Event(), Event(), Event()
    claims = []
    worker.runtime.ordinary_ready = lambda selected: selected == user
    worker.leases.claim_next = lambda *_a, **_kw: (claims.append(original), original)[1]
    actual_observe = worker._observe

    def held_observe(operation):
        pending.set()
        assert transfer.wait(5)
        return actual_observe(operation)

    def held_forward(*_args):
        active.set()
        assert finish.wait(5)

    monkeypatch.setattr(worker, "_observe", held_observe)
    monkeypatch.setattr(worker, "_forward", held_forward)
    caller = Thread(target=worker.run_one_ordinary)
    caller.start()
    try:
        assert pending.wait(5)
        assert worker.run_one_ordinary() is False
        transfer.set()
        assert active.wait(5)
        assert worker.run_one_ordinary() is False
        assert claims == [original]
    finally:
        transfer.set()
        finish.set()
        caller.join(5)
        worker.close()
    assert claims == [original]


@pytest.mark.parametrize("stop_in_handoff", [False, True])
def test_preflight_transfer_rechecks_original_deadline_and_stop(
        monkeypatch, stop_in_handoff):
    worker = _worker()
    user = min(worker.runtime.trusted_user_ids)
    original = _attempt(user)
    entered, release = Event(), Event()
    calls = []
    worker.runtime.ordinary_ready = lambda selected: selected == user
    worker.leases.claim_next = lambda *_a, **_kw: (calls.append("claim"), original)[1]
    monkeypatch.setattr(worker, "_forward", lambda *_a: calls.append("forward"))
    actual_start = worker._start

    def held_start(lane, target, **kwargs):
        if lane == "attempt":
            entered.set()
            assert release.wait(5)
        return actual_start(lane, target, **kwargs)

    monkeypatch.setattr(worker, "_start", held_start)
    caller = Thread(target=worker.run_one_ordinary)
    caller.start()
    try:
        assert entered.wait(5)
        assert worker.run_one_ordinary() is False
        if stop_in_handoff:
            assert worker.close().ordinary_attempts == 1
        else:
            time.sleep(1.1)
        release.set()
        caller.join(5)
        assert not caller.is_alive()
        assert calls == ["claim"]
        assert "heartbeat" not in worker._lanes
    finally:
        release.set()
        caller.join(5)
        worker.close()
    assert worker.close().ordinary_attempts == 1


def test_recovery_uses_full_trust_and_returned_renewed_claim():
    worker = _worker(providers=False)
    seen = []
    original, renewed = object(), object()
    worker.runtime.recovery_ready = lambda: True
    worker.leases.recover_expired = lambda **kw: seen.append(("expire", kw))
    worker.recovery.claim_next = lambda *_a, **kw: (seen.append(("claim", kw)), original)[1]
    worker.recovery.renew = lambda claim, **kw: (seen.append(("renew", claim)), renewed)[1]
    worker.recovery.prove_or_hold = lambda claim: (seen.append(("prove", claim)), "retry_later")[1]
    try:
        assert worker.run_one_recovery() == "retry_later"
        assert seen[0][1]["allowed_user_ids"] == worker.runtime.trusted_user_ids
        assert seen[1][1]["allowed_user_ids"] == worker.runtime.trusted_user_ids
        assert seen[2:] == [("renew", original), ("prove", renewed)]
    finally:
        worker.close()


def test_blocked_recovery_reports_unclean_and_finishes_cleanup_later():
    worker = _worker(providers=False)
    entered, release = Event(), Event()

    def blocked():
        entered.set()
        assert release.wait(5)
        return True

    worker.runtime.recovery_ready = blocked
    owner = Thread(target=worker.run_one_recovery)
    owner.start()
    try:
        assert entered.wait(5)
        started = time.monotonic()
        report = worker.close()
        assert time.monotonic() - started < 1.5
        assert not report.clean and report.active_threads >= 1
    finally:
        release.set()
        owner.join(5)
        worker._lanes["recovery"].thread.join(5)
        worker._lanes["cleanup"].thread.join(5)
    assert worker.close().clean


def test_heartbeat_loss_blocks_source_after_selected_readiness():
    worker = _worker()
    user = min(worker.runtime.trusted_user_ids)
    original = _attempt(user)
    entered, release, lost = Event(), Event(), Event()
    source_calls = []

    def ready(_user):
        entered.set()
        assert release.wait(5)
        return True

    def lose(_original, _hb_abort, forward_abort, _deadline):
        assert entered.wait(5)
        forward_abort.set()
        lost.set()

    worker.runtime.ordinary_ready = ready
    worker._heartbeat = lose
    worker.runtime.storage_clients = lambda: source_calls.append(True)
    operation = worker._start("attempt", lambda abort: worker._forward(
        original, abort, time.monotonic() + 5))
    try:
        assert operation is not None and lost.wait(5)
        release.set()
        operation.thread.join(5)
        assert not operation.thread.is_alive()
        assert source_calls == []
    finally:
        release.set()
        worker.close()


@pytest.mark.parametrize("blocked_stage", ["source", "adapter"])
def test_heartbeat_loss_reports_while_forward_borrower_is_still_blocked(
        monkeypatch, blocked_stage):
    import app.distributed_import_worker as worker_module

    settings = replace(_settings(), provider_timeout_seconds=3,
                       heartbeat_seconds=1, attempt_seconds=8)
    worker = _worker(settings)
    user = min(settings.trusted_user_ids)
    original = _attempt(user)
    entered, release, lost = Event(), Event(), Event()
    calls = []
    worker.runtime.ordinary_ready = lambda selected: selected == user
    worker.runtime.recovery_ready = lambda: True
    worker.leases.claim_next = lambda *_a, **_kw: original
    worker.leases.recover_expired = lambda **_kw: calls.append("expiry")
    worker.recovery.claim_next = lambda *_a, **_kw: None
    worker._authority = lambda _original: None
    worker._scopes = lambda _user: (object(), object())
    worker.runtime.storage_clients = lambda: (object(), object())
    worker._source = lambda *_a: b"source"

    if blocked_stage == "source":
        worker._source = lambda *_a: (entered.set(), release.wait(5), b"source")[2]
    else:
        monkeypatch.setattr(worker_module, "prepare_import_document",
                            lambda **_kw: [object()])
        worker.providers.episode_runtime._engine.encode = lambda _text: [1.0] + [0.0] * 383

        class Publication:
            def __init__(self, *_a, **_kw):
                pass

            def _issue_live_publication(self, *_a, **_kw):
                calls.append("issue")
                return object()

        class Adapter:
            def __init__(self, *_a, **_kw):
                pass

            def run_once(self, _live):
                entered.set()
                assert release.wait(5)
                return SimpleNamespace(status="committed_success")

        monkeypatch.setattr(worker_module, "ImportMemoryPublicationService", Publication)
        monkeypatch.setattr(worker_module, "ImportPublicationWorkerAdapter", Adapter)

    def lose(attempt, **_kwargs):
        assert attempt is original
        assert entered.wait(5)
        lost.set()
        raise RuntimeError("injected_heartbeat_loss")

    worker.leases.heartbeat = lose
    caller = Thread(target=worker.run_one_ordinary)
    caller.start()
    try:
        assert entered.wait(5)
        assert lost.wait(5)
        worker._lanes["heartbeat"].thread.join(5)
        assert not worker._lanes["heartbeat"].thread.is_alive()
        caller.join(1)
        assert not caller.is_alive()
        assert worker._lanes["attempt"].thread.is_alive()
        assert worker.run_one_ordinary() is False
        assert worker.run_one_recovery() is None
        assert "expiry" in calls
        assert worker._ordinary_count == 1
    finally:
        release.set()
        caller.join(5)
        worker._lanes["attempt"].thread.join(5)
        if "heartbeat" in worker._lanes:
            worker._lanes["heartbeat"].thread.join(5)
        worker.close()
    if blocked_stage == "adapter":
        assert worker._last_disposition == "committed_success"


def test_late_preparation_cannot_start_episode_or_issue(monkeypatch):
    import app.distributed_import_worker as worker_module

    worker = _worker()
    user = min(worker.runtime.trusted_user_ids)
    original = _attempt(user)
    entered, release = Event(), Event()
    next_steps = []
    worker.runtime.ordinary_ready = lambda selected: selected == user
    worker.leases.claim_next = lambda *_a, **_kw: original
    worker._authority = lambda _original: None
    worker._source = lambda _original, _store: b"prepared source"
    worker._scopes = lambda _user: (object(), object())
    worker.runtime.storage_clients = lambda: (object(), object())
    worker.providers.episode_runtime._engine.encode = lambda _text: next_steps.append(
        "episode")

    def blocked_prepare(**_kwargs):
        entered.set()
        assert release.wait(5)
        return [object()]

    monkeypatch.setattr(worker_module, "prepare_import_document", blocked_prepare)
    monkeypatch.setattr(worker_module.ImportMemoryPublicationService,
                        "_issue_live_publication", lambda *_a, **_kw:
                        next_steps.append("issue"))
    try:
        assert worker.run_one_ordinary()
        assert entered.is_set()
        assert worker._lanes["attempt"].thread.is_alive()
        release.set()
        worker._lanes["attempt"].thread.join(5)
        assert not worker._lanes["attempt"].thread.is_alive()
        assert next_steps == []
    finally:
        release.set()
        worker.close()


@pytest.mark.parametrize("late", [False, True])
def test_deterministic_prepare_refusal_checks_stage_deadline_before_fail(
        monkeypatch, late):
    import app.distributed_import_worker as worker_module
    from app.import_document_preparation import ImportDocumentPreparationError

    settings = replace(_settings(), provider_timeout_seconds=1,
                       heartbeat_seconds=2, attempt_seconds=5)
    worker = _worker(settings)
    original = _attempt(min(settings.trusted_user_ids))
    entered, release = Event(), Event()
    calls = []
    worker.runtime.ordinary_ready = lambda _user: True
    worker._authority = lambda _original: None
    worker._source = lambda *_a: b"source"
    worker._scopes = lambda _user: (object(), object())
    worker.runtime.storage_clients = lambda: (object(), object())
    worker._heartbeat = lambda *_a: None
    worker.leases.fail = lambda *_a: calls.append("fail")

    def refuse(**_kwargs):
        entered.set()
        if late:
            assert release.wait(5)
        raise ImportDocumentPreparationError("parse")

    monkeypatch.setattr(worker_module, "prepare_import_document", refuse)
    monkeypatch.setattr(worker_module.ImportMemoryPublicationService,
                        "_issue_live_publication", lambda *_a, **_kw: calls.append("issue"))
    operation = worker._start("attempt", lambda abort: worker._forward(
        original, abort, time.monotonic() + settings.attempt_seconds))
    try:
        assert operation is not None and entered.wait(5)
        if late:
            time.sleep(1.1)
            release.set()
        operation.thread.join(5)
        assert not operation.thread.is_alive()
        assert calls == ([] if late else ["fail"])
    finally:
        release.set()
        worker.close()


def test_provider_stage_timeout_stops_renewal_while_recovery_runs(monkeypatch):
    import app.distributed_import_worker as worker_module

    settings = replace(_settings(), provider_timeout_seconds=2,
                       heartbeat_seconds=1, attempt_seconds=8)
    worker = _worker(settings)
    user = min(settings.trusted_user_ids)
    original = _attempt(user)
    entered, release = Event(), Event()
    renewals = []
    worker.runtime.ordinary_ready = lambda selected: selected == user
    worker.runtime.recovery_ready = lambda: True
    worker.leases.claim_next = lambda *_a, **_kw: original
    worker.leases.heartbeat = lambda attempt, **_kw: renewals.append(attempt)
    worker.leases.recover_expired = lambda **_kw: 0
    worker.recovery.claim_next = lambda *_a, **_kw: None
    worker._authority = lambda _original: None
    worker._source = lambda _original, _store: b"source"
    worker._scopes = lambda _user: (object(), object())
    worker.runtime.storage_clients = lambda: (object(), object())

    def blocked(**_kwargs):
        entered.set()
        assert release.wait(10)
        return [object()]

    monkeypatch.setattr(worker_module, "prepare_import_document", blocked)
    try:
        assert worker.run_one_ordinary()
        assert entered.is_set() and worker._lanes["attempt"].thread.is_alive()
        count_at_abort = len(renewals)
        assert worker.run_one_recovery() is None
        time.sleep(1.2)
        assert len(renewals) == count_at_abort
        assert all(attempt is original for attempt in renewals)
    finally:
        release.set()
        worker._lanes["attempt"].thread.join(5)
        worker.close()


def test_stop_during_blocked_heartbeat_retains_one_inflight_renewal():
    settings = replace(_settings(), heartbeat_seconds=1)
    worker = _worker(settings)
    original = _attempt(min(settings.trusted_user_ids))
    entered, release, forward_abort = Event(), Event(), Event()
    calls = []

    def heartbeat(attempt, **_kw):
        calls.append(attempt)
        entered.set()
        assert release.wait(5)
        return object()

    worker.leases.heartbeat = heartbeat
    operation = worker._start("heartbeat", lambda abort: worker._heartbeat(
        original, abort, forward_abort, time.monotonic() + 5))
    try:
        assert operation is not None and entered.wait(3)
        report = worker.close()
        assert not report.clean and report.active_threads >= 1
        assert calls == [original]
    finally:
        release.set()
        operation.thread.join(5)
        worker._lanes["cleanup"].thread.join(5)
    assert worker.close().clean
    assert calls == [original]


def test_slow_adapter_remains_visible_after_stop_without_replay(monkeypatch):
    import app.distributed_import_worker as worker_module

    worker = _worker()
    user = min(worker.runtime.trusted_user_ids)
    original = _attempt(user)
    entered, release = Event(), Event()
    issued, executed = [], []
    live = object()
    worker.runtime.ordinary_ready = lambda selected: selected == user
    worker.leases.claim_next = lambda *_a, **_kw: original
    worker._authority = lambda _original: None
    worker._source = lambda _original, _store: b"source"
    worker._scopes = lambda _user: (object(), object())
    worker.runtime.storage_clients = lambda: (object(), object())
    monkeypatch.setattr(worker_module, "prepare_import_document",
                        lambda **_kwargs: [object()])

    class Publication:
        def __init__(self, *_args, **_kwargs):
            pass

        def _issue_live_publication(self, *_args, **_kwargs):
            issued.append(live)
            return live

    class Adapter:
        def __init__(self, *_args, **_kwargs):
            pass

        def run_once(self, original_live):
            executed.append(original_live)
            entered.set()
            assert release.wait(5)
            return SimpleNamespace(status="needs_reconciliation")

    monkeypatch.setattr(worker_module, "ImportMemoryPublicationService", Publication)
    monkeypatch.setattr(worker_module, "ImportPublicationWorkerAdapter", Adapter)
    runner = Thread(target=worker.run_one_ordinary)
    runner.start()
    try:
        assert entered.wait(5)
        report = worker.close()
        assert not report.clean and report.active_threads >= 1
        assert issued == executed == [live]
    finally:
        release.set()
        runner.join(5)
        worker._lanes["attempt"].thread.join(5)
        worker._lanes["cleanup"].thread.join(5)
    assert worker.close().clean
    assert issued == executed == [live]


def test_external_stop_interrupts_blocked_recovery_observation():
    worker = _worker(providers=False)
    entered, release, stop = Event(), Event(), Event()
    reports = []

    def blocked():
        entered.set()
        assert release.wait(5)
        return True

    worker.runtime.recovery_ready = blocked
    runner = Thread(target=lambda: reports.append(worker.run_forever(stop)))
    runner.start()
    try:
        assert entered.wait(5)
        started = time.monotonic()
        stop.set()
        runner.join(2)
        assert not runner.is_alive()
        assert time.monotonic() - started < 1.5
        assert len(reports) == 1 and not reports[0].clean
        assert reports[0].active_threads >= 1
    finally:
        stop.set()
        release.set()
        runner.join(5)
        worker._lanes["recovery"].thread.join(5)
        worker._lanes["cleanup"].thread.join(5)
    assert worker.close().clean


def test_late_recovery_proof_never_starts_second_round():
    worker = _worker(providers=False)
    entered, release = Event(), Event()
    claims, proofs = [], []
    first, refreshed = object(), object()
    worker.runtime.recovery_ready = lambda: True
    worker.leases.recover_expired = lambda **_kw: 0

    def claim(*_a, **_kw):
        claims.append(True)
        return first

    def prove(claimed):
        proofs.append(claimed)
        entered.set()
        assert release.wait(5)
        return "retry_later"

    worker.recovery.claim_next = claim
    worker.recovery.renew = lambda *_a, **_kw: refreshed
    worker.recovery.prove_or_hold = prove
    runner = Thread(target=worker.run_one_recovery)
    runner.start()
    try:
        assert entered.wait(5)
        assert worker.run_one_recovery() is None
        report = worker.close()
        assert not report.clean and report.active_threads >= 1
        assert claims == [True] and proofs == [refreshed]
    finally:
        release.set()
        runner.join(5)
        worker._lanes["recovery"].thread.join(5)
        worker._lanes["cleanup"].thread.join(5)
    assert worker.close().clean
    assert worker.close().recovery_attempts == 1


def test_startup_close_is_bounded_and_retained():
    from scripts.run_isolated_import_worker import _close_startup

    entered, release, finished = Event(), Event(), Event()

    def slow_close():
        entered.set()
        assert release.wait(5)
        finished.set()

    runtime = SimpleNamespace(close=slow_close)
    try:
        started = time.monotonic()
        assert not _close_startup(runtime, None, 0.05)
        assert entered.is_set()
        assert time.monotonic() - started < 0.5
    finally:
        release.set()
    assert finished.wait(5)


def test_worker_slow_runtime_close_is_counted_until_completion():
    worker = _worker(providers=False)
    entered, release = Event(), Event()

    def slow_close():
        entered.set()
        assert release.wait(5)

    worker.runtime.close = slow_close
    try:
        started = time.monotonic()
        report = worker.close()
        assert entered.is_set()
        assert time.monotonic() - started < 1.5
        assert not report.clean and report.active_threads == 1
    finally:
        release.set()
        worker._lanes["cleanup"].thread.join(5)
    assert worker.close().clean


def test_one_stop_deadline_owns_preflight_recovery_probe_and_runtime_close(
        monkeypatch):
    settings = replace(_settings(), provider_timeout_seconds=3,
                       stop_wait_seconds=1)
    worker = _worker(settings)
    ready_enter, recovery_enter, probe_enter = Event(), Event(), Event()
    release, close_enter, close_release = Event(), Event(), Event()
    close_calls = []
    original_encode = worker.providers.rag_runtime._engine.encode

    def held_ready(_user):
        ready_enter.set()
        assert release.wait(5)
        return False

    def held_recovery():
        recovery_enter.set()
        assert release.wait(5)
        return True

    def held_encode(value):
        probe_enter.set()
        assert release.wait(5)
        return original_encode(value)

    def held_close():
        close_calls.append(True)
        close_enter.set()
        assert close_release.wait(5)

    worker.runtime.ordinary_ready = held_ready
    worker.runtime.recovery_ready = held_recovery
    worker.runtime.close = held_close
    monkeypatch.setattr(worker.providers.rag_runtime._engine, "encode", held_encode)
    ordinary = Thread(target=worker.run_one_ordinary)
    recovery = Thread(target=worker.run_one_recovery)
    probe = Thread(target=worker.providers.probe)
    ordinary.start()
    recovery.start()
    try:
        assert ready_enter.wait(5) and recovery_enter.wait(5)
        probe.start()
        assert probe_enter.wait(5)
        started = time.monotonic()
        first = worker.close()
        assert time.monotonic() - started < 1.5
        assert not first.clean and first.active_threads >= 4
        release.set()
        ordinary.join(5)
        recovery.join(5)
        probe.join(5)
        assert close_enter.wait(5)
        started = time.monotonic()
        assert not worker.close().clean
        assert time.monotonic() - started < 0.3
        assert close_calls == [True]
    finally:
        release.set()
        close_release.set()
        ordinary.join(5)
        recovery.join(5)
        if probe.ident is not None:
            probe.join(5)
        worker.close()
        worker._lanes["cleanup"].thread.join(5)
    assert worker.close().clean


def test_first_observed_external_stop_owns_close_budget_and_cleanup(monkeypatch):
    import app.distributed_import_worker as worker_module

    settings = replace(_settings(), stop_wait_seconds=1)
    worker = _worker(settings, providers=False)
    clock = [100.0]
    monkeypatch.setattr(worker_module, "time", SimpleNamespace(
        monotonic=lambda: clock[0], sleep=time.sleep))
    external = Event()
    worker._external_stop = external
    close_enter, close_release = Event(), Event()
    close_calls = []

    def held_close():
        close_calls.append(True)
        close_enter.set()
        assert close_release.wait(5)

    worker.runtime.close = held_close
    external.set()
    assert worker._stopping()
    first_deadline = worker._close_deadline
    try:
        clock[0] = 102.0
        started = time.monotonic()
        first = worker.close()
        assert time.monotonic() - started < 0.3
        assert not first.clean and first.active_threads == 1
        assert worker._close_deadline == first_deadline
        assert close_enter.wait(5)
        reports = []
        callers = [Thread(target=lambda: reports.append(worker.close()))
                   for _ in range(2)]
        for caller in callers:
            caller.start()
        for caller in callers:
            caller.join(1)
        assert all(not caller.is_alive() for caller in callers)
        assert len(reports) == 2 and all(not report.clean for report in reports)
        assert close_calls == [True]
    finally:
        close_release.set()
        worker._lanes["cleanup"].thread.join(5)
    assert worker.close().clean


def test_cli_typed_provider_refusal_selects_recovery_only(monkeypatch, capsys):
    import scripts.run_isolated_import_worker as cli

    settings = _settings()
    monkeypatch.setattr(cli.IsolatedImportSettings, "from_env",
                        lambda role: settings if role == "worker" else None)
    monkeypatch.setattr(cli, "open_isolated_providers", lambda *_:
                        (_ for _ in ()).throw(IsolatedProviderConfigurationError(
                            "provider_configuration")))
    seen = []

    def open_runtime(_settings, *, provider_probe):
        seen.append(provider_probe)
        return SimpleNamespace(close=lambda: None)

    class FakeWorker:
        def __init__(self, runtime, providers, *, worker_id):
            assert providers is None and runtime is not None

        def run_forever(self, _stop):
            return WorkerExit(True, "recovery_only", 0, 0, 0)

    monkeypatch.setattr(cli, "open_isolated_runtime", open_runtime)
    monkeypatch.setattr(cli, "IsolatedImportWorker", FakeWorker)
    assert cli.main() == 0
    assert seen == [None]
    output = capsys.readouterr().out
    assert "recovery_only:provider_configuration" in output
    assert "private" not in output


def test_cli_unexpected_provider_failure_is_fatal(monkeypatch, capsys):
    import scripts.run_isolated_import_worker as cli

    settings = _settings()
    monkeypatch.setattr(cli.IsolatedImportSettings, "from_env", lambda _role: settings)
    monkeypatch.setattr(cli, "open_isolated_providers", lambda *_:
                        (_ for _ in ()).throw(RuntimeError("private value")))
    monkeypatch.setattr(cli, "open_isolated_runtime", lambda *_a, **_kw:
                        pytest.fail("unexpected provider failure reached database"))
    assert cli.main() == 2
    assert capsys.readouterr().out == "provider_open_failed\n"


def test_cli_invalid_worker_role_or_schema_refuses_before_io(monkeypatch, capsys):
    import scripts.run_isolated_import_worker as cli
    from app.isolated_import_runtime import IsolatedImportConfigurationError

    def refuse(role):
        assert role == "worker"
        raise IsolatedImportConfigurationError("schema_identity")

    monkeypatch.setattr(cli.IsolatedImportSettings, "from_env", refuse)
    monkeypatch.setattr(cli, "open_isolated_providers", lambda *_a:
                        pytest.fail("configuration must precede provider"))
    monkeypatch.setattr(cli, "open_isolated_runtime", lambda *_a, **_kw:
                        pytest.fail("configuration must precede database"))
    assert cli.main() == 2
    assert capsys.readouterr().out == "configuration_refused:schema_identity\n"


def test_cli_main_open_failure_internal_close_is_retained_and_handlers_restored(
        monkeypatch, capsys):
    import signal
    from threading import enumerate as enumerate_threads
    import scripts.run_isolated_import_worker as cli

    settings = replace(_settings(), provider_timeout_seconds=1,
                       stop_wait_seconds=1)
    entered, release, provider_closed = Event(), Event(), Event()
    old_handlers = (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM))
    monkeypatch.setattr(cli.IsolatedImportSettings, "from_env", lambda _role: settings)
    monkeypatch.setattr(cli, "open_isolated_providers", lambda *_a:
                        SimpleNamespace(probe=lambda: None,
                                        close=lambda **_kw: provider_closed.set() or True))

    def fail_after_internal_close(*_a, **_kw):
        entered.set()
        assert release.wait(5)
        raise RuntimeError("private_connection")

    monkeypatch.setattr(cli, "open_isolated_runtime", fail_after_internal_close)
    started = time.monotonic()
    try:
        assert cli.main() == 3
        assert entered.is_set()
        assert time.monotonic() - started < 3
        assert "runtime_open_active" in capsys.readouterr().out
        assert (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)) == old_handlers
    finally:
        release.set()
        for thread in enumerate_threads():
            if thread.name == "isolated-runtime-open":
                thread.join(5)
    assert provider_closed.wait(5)


@pytest.mark.parametrize("stop_kind", ["timeout", "signal"])
def test_cli_late_successful_open_is_abandoned_and_closed_once(
        monkeypatch, capsys, stop_kind):
    import signal
    from threading import enumerate as enumerate_threads
    import scripts.run_isolated_import_worker as cli

    settings = replace(_settings(), provider_timeout_seconds=(3 if stop_kind == "signal" else 1),
                       stop_wait_seconds=1)
    entered, release = Event(), Event()
    signal_injected = Event()
    runtime_closes, provider_closes, compositions = [], [], []
    old_handlers = (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM))
    runtime = SimpleNamespace(close=lambda: runtime_closes.append(True))
    providers = SimpleNamespace(
        probe=lambda: None,
        close=lambda **_kw: provider_closes.append(True) or True)
    monkeypatch.setattr(cli.IsolatedImportSettings, "from_env", lambda _role: settings)
    monkeypatch.setattr(cli, "open_isolated_providers", lambda *_a: providers)

    def delayed_open(*_a, **_kw):
        entered.set()
        assert release.wait(5)
        return runtime

    monkeypatch.setattr(cli, "open_isolated_runtime", delayed_open)
    monkeypatch.setattr(cli, "IsolatedImportWorker", lambda *_a, **_kw:
                        compositions.append(True))

    def signal_stop():
        assert entered.wait(5)
        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        signal_injected.set()

    trigger = Thread(target=signal_stop) if stop_kind == "signal" else None
    if trigger is not None:
        trigger.start()
    try:
        assert cli.main() == 3
        assert "runtime_open_active" in capsys.readouterr().out
        assert compositions == []
        assert stop_kind == "timeout" or signal_injected.is_set()
        assert (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)) == old_handlers
    finally:
        release.set()
        if trigger is not None:
            trigger.join(5)
        for thread in enumerate_threads():
            if thread.name == "isolated-runtime-open":
                thread.join(5)
                assert not thread.is_alive()
    assert runtime_closes == [True]
    assert provider_closes == [True]


def test_cli_timely_open_transfers_owned_runtime_without_second_opener_close(
        monkeypatch, capsys):
    import signal
    from threading import enumerate as enumerate_threads
    import scripts.run_isolated_import_worker as cli

    settings = _settings()
    runtime_closes, provider_closes, compositions = [], [], []
    old_handlers = (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM))
    runtime = SimpleNamespace(close=lambda: runtime_closes.append(True))
    providers = SimpleNamespace(
        probe=lambda: None,
        close=lambda **_kw: provider_closes.append(True) or True)
    monkeypatch.setattr(cli.IsolatedImportSettings, "from_env", lambda _role: settings)
    monkeypatch.setattr(cli, "open_isolated_providers", lambda *_a: providers)
    monkeypatch.setattr(cli, "open_isolated_runtime", lambda *_a, **_kw: runtime)

    class OwnedWorker:
        def __init__(self, accepted, bundle, *, worker_id):
            assert accepted is runtime and bundle is providers
            compositions.append(worker_id)

        def run_forever(self, _stop):
            assert providers.close(timeout_seconds=0)
            runtime.close()
            return WorkerExit(True, "stopped", 0, 0, 0)

    monkeypatch.setattr(cli, "IsolatedImportWorker", OwnedWorker)
    assert cli.main() == 0
    for thread in enumerate_threads():
        if thread.name == "isolated-runtime-open":
            thread.join(5)
            assert not thread.is_alive()
    assert len(compositions) == 1
    assert runtime_closes == [True] and provider_closes == [True]
    assert capsys.readouterr().out == "stopped\n"
    assert (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)) == old_handlers


def test_cli_main_worker_composition_slow_close_returns_active_exit3(
        monkeypatch, capsys):
    import signal
    import scripts.run_isolated_import_worker as cli

    settings = replace(_settings(), stop_wait_seconds=1)
    entered, release, closed = Event(), Event(), Event()
    old_handlers = (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM))
    monkeypatch.setattr(cli.IsolatedImportSettings, "from_env", lambda _role: settings)
    monkeypatch.setattr(cli, "open_isolated_providers", lambda *_a: None)

    def close_runtime():
        entered.set()
        assert release.wait(5)
        closed.set()

    monkeypatch.setattr(cli, "open_isolated_runtime", lambda *_a, **_kw:
                        SimpleNamespace(close=close_runtime))
    monkeypatch.setattr(cli, "IsolatedImportWorker", lambda *_a, **_kw:
                        (_ for _ in ()).throw(ValueError("worker_configuration")))
    try:
        assert cli.main() == 3
        assert entered.is_set()
        assert "worker_composition_failed" in capsys.readouterr().out
        assert (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)) == old_handlers
    finally:
        release.set()
    assert closed.wait(5)


def _text_pdf(text: str) -> bytes:
    """One extractable page; offsets are computed from the final byte stream."""
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    start = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
               f"startxref\n{start}\n%%EOF\n".encode())
    return bytes(pdf)


def _resource_receipt(token: str, kind: str, name: str, event: str) -> None:
    target = Path(".runtime/isolated-import-runtime/p4-resources.jsonl")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as receipt:
        receipt.write(json.dumps({"fixture": token, "kind": kind,
                                  "name": name, "event": event},
                                 sort_keys=True) + "\n")


@pytest.fixture
def real_worker_resources(monkeypatch):
    """Generated PG schema, versioned S3 bucket and two 384D Q collections."""
    base = os.environ.get("POSTGRES_TEST_URL")
    endpoint = os.environ.get("S3_TEST_ENDPOINT")
    qdrant_url = os.environ.get("GENERATION_QDRANT_TEST_URL")
    access = os.environ.get("S3_TEST_ACCESS_KEY")
    secret = os.environ.get("S3_TEST_SECRET_KEY")
    if not all((base, endpoint, qdrant_url, access, secret)):
        pytest.skip("Dedicated PG/S3/target-Q resources are required")

    import boto3
    import psycopg
    from alembic import command
    from alembic.config import Config
    from botocore.config import Config as BotoConfig
    from psycopg import sql
    from sqlalchemy.engine import make_url

    from app.history import EMPTY_HISTORY
    from app.postgres import PostgresDatabase
    from app.postgres_coordination import PostgresUserMutationCoordinator
    from app.postgres_snapshots import PostgresSnapshotRepository
    from app.postgres_vector_generations import PostgresVectorGenerationAuthority, VectorScope
    from app.vector_generation_service import VectorGenerationService
    from hello_agents.memory.rag.index_identity import IndexIdentity
    from hello_agents.memory.storage.vector_store import QdrantVectorStore
    from tests.integration.test_published_episode_reads import _publish

    token = uuid4().hex
    schema, bucket = "p4_worker_" + token, "p4-worker-" + token
    rag_base, episode_base = "p4_rag_" + token, "p4_episode_" + token
    assert schema != "cutover_combined_refs_20260930_0f7592dc3978"
    assert bucket != "cutover-paired-bf20-69fcab0f0cbd4300a0545f2008c15779"
    assert qdrant_url != os.environ.get("QDRANT_SOURCE_TEST_URL", "protected-source")
    profile = profile_for_backend(EmbeddingSettings.from_env({}).profile, "qdrant")
    rag_identity = IndexIdentity("qdrant", rag_base, profile)
    episode_identity = IndexIdentity("qdrant", episode_base, profile)
    migrated = make_url(base).set(drivername="postgresql+psycopg",
                                  query={"options": f"-csearch_path={schema}"})
    migrated = migrated.render_as_string(hide_password=False)
    db = raw = client = None
    schema_created = False
    bucket_created = False
    collections = []
    try:
        with psycopg.connect(base, autocommit=True) as admin:
            assert admin.execute("select to_regnamespace(%s)", (schema,)).fetchone()[0] is None
            admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
        schema_created = True
        _resource_receipt(token, "schema", schema, "created")
        with monkeypatch.context() as isolated_migration:
            isolated_migration.setenv("DATABASE_URL", migrated)
            command.upgrade(Config("alembic.ini"), "head")
        dsn = migrated.replace("postgresql+psycopg://", "postgresql://")
        client = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1",
                              aws_access_key_id=access, aws_secret_access_key=secret,
                              config=BotoConfig(s3={"addressing_style": "path"}))
        client.create_bucket(Bucket=bucket)
        bucket_created = True
        _resource_receipt(token, "bucket", bucket, "created")
        client.put_bucket_versioning(Bucket=bucket,
                                     VersioningConfiguration={"Status": "Enabled"})
        raw = QdrantVectorStore(url=qdrant_url, retry_delays=())
        for identity in (rag_identity, episode_identity):
            raw.ensure_collection(identity.physical_collection, profile.dimension)
            collections.append(identity.physical_collection)
            _resource_receipt(token, "collection", identity.physical_collection,
                              "created")
        db = PostgresDatabase(dsn, min_size=1, max_size=4)
        db.open()
        users = (str(uuid4()), str(uuid4()))
        excluded = str(uuid4())
        with db.transaction() as cursor:
            for user in (*users, excluded):
                cursor.execute("insert into users values (%s,%s,%s,%s,%s,%s,%s)",
                               (user, user, user, "hash", "active", "now", "now"))
        vectors = VectorGenerationService(PostgresVectorGenerationAuthority(db), raw)
        snapshots = PostgresSnapshotRepository(db)
        for user in (*users, excluded):
            rag_scope = VectorScope(user, "rag", f"pdf_{user}", rag_identity)
            episode_scope = VectorScope(user, "episode", "episodes", episode_identity)
            coordinator = PostgresUserMutationCoordinator(db)
            lease = coordinator.acquire(user, "p4-baseline", lease_seconds=120)
            assert lease is not None
            try:
                def history(cursor):
                    snapshots.compare_and_swap_in_transaction(
                        cursor, user, "history", dict(EMPTY_HISTORY), expected_version=0)
                vectors.publish_complete(rag_scope, lease,
                                         vectors.authority.read_head(rag_scope), [],
                                         domain_publish=history, snapshot_version=1)
            finally:
                coordinator.release(lease)
            _publish(db, vectors, episode_scope, [])
        values = {
            "ISOLATED_IMPORT_ENABLED": "1", "ISOLATED_IMPORT_DATABASE_URL": dsn,
            "ISOLATED_IMPORT_SCHEMA": schema,
            "ISOLATED_IMPORT_S3_ENDPOINT_URL": endpoint,
            "ISOLATED_IMPORT_S3_BUCKET": bucket,
            "ISOLATED_IMPORT_QDRANT_URL": qdrant_url,
            "ISOLATED_IMPORT_RAG_COLLECTION": rag_base,
            "ISOLATED_IMPORT_EPISODE_COLLECTION": episode_base,
            "ISOLATED_IMPORT_RAG_PROFILE_JSON": json.dumps(asdict(profile)),
            "ISOLATED_IMPORT_EPISODE_PROFILE_JSON": json.dumps(asdict(profile)),
            "ISOLATED_IMPORT_TRUSTED_USER_IDS": ",".join(users),
            "S3_ACCESS_KEY_ID": access, "S3_SECRET_ACCESS_KEY": secret,
            "ISOLATED_IMPORT_PROVIDER_TIMEOUT_SECONDS": "30",
        }
        settings = IsolatedImportSettings.from_env("worker", values)
        yield settings, db, raw, client, users, excluded
    finally:
        cleanup_errors = []

        def cleanup(kind, name, action):
            try:
                action()
            except Exception as error:
                cleanup_errors.append(error)
                event = "remove_failed"
            else:
                event = "removed"
            try:
                _resource_receipt(token, kind, name, event)
            except Exception as error:
                cleanup_errors.append(error)

        if db is not None:
            cleanup("database", schema, db.close)
        if raw is not None:
            for collection in collections:
                cleanup("collection", collection,
                        lambda name=collection: raw.client.delete_collection(name))
            closer = getattr(raw.client, "close", None)
            if callable(closer):
                cleanup("vector_client", token, closer)
        if client is not None and bucket_created:
            def remove_bucket():
                for page in client.get_paginator("list_object_versions").paginate(Bucket=bucket):
                    versions = page.get("Versions", []) + page.get("DeleteMarkers", [])
                    if versions:
                        client.delete_objects(Bucket=bucket, Delete={"Objects": [
                            {"Key": item["Key"], "VersionId": item["VersionId"]}
                            for item in versions]})
                client.delete_bucket(Bucket=bucket)
            cleanup("bucket", bucket, remove_bucket)
        if client is not None:
            cleanup("storage_client", token, client.close)
        if schema_created:
            def remove_schema():
                with psycopg.connect(base, autocommit=True) as admin:
                    admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))
            cleanup("schema", schema, remove_schema)
        if cleanup_errors:
            raise ExceptionGroup("real_worker_resources_cleanup", cleanup_errors)


def _upload(db, client, settings, user, name, content):
    from app.import_uploads import ImportUpload
    from app.object_store import S3ObjectStore
    from app.postgres_import_artifacts import PostgresImportArtifactService

    return PostgresImportArtifactService(
        db, S3ObjectStore(client, settings.s3_bucket)).submit_uploads(
            user, [ImportUpload(name, io.BytesIO(content))]).tasks[0].task_id


def _held_real_intent(resources, monkeypatch, *, name="held.txt"):
    from app.import_publication_worker_adapter import (
        FrozenDisposition, ImportPublicationWorkerAdapter,
    )
    from app.isolated_import_runtime import open_isolated_runtime

    base, db, _raw, client, users, _excluded = resources
    task = _upload(db, client, base, users[0], name,
                   b"A generated durable intent for caller-level recovery evidence.")
    captured = []

    def hold(adapter, live):
        issue = adapter.service._require_live_issue(live)
        captured.append((adapter.service, live, issue))
        key = issue.key
        return FrozenDisposition(key, "needs_reconciliation", key.task_id,
                                 "recovery_unavailable")

    monkeypatch.setattr(ImportPublicationWorkerAdapter, "run_once", hold)
    providers = _providers(base)
    worker = IsolatedImportWorker(open_isolated_runtime(base,
        provider_probe=providers.probe), providers, worker_id="p4-held-intent")
    assert worker.run_one_ordinary()
    worker._lanes["heartbeat"].thread.join(5)
    assert not worker._lanes["heartbeat"].thread.is_alive()
    assert len(captured) == 1
    return worker, task, captured[0]


def _due_intent(db, user, task, *, expire_both=True):
    with db.transaction() as cursor:
        if expire_both:
            cursor.execute("update import_tasks set lease_expires_at="
                           "clock_timestamp()-interval '1 second' where id=%s", (task,))
            cursor.execute("update user_mutation_leases set lease_expires_at="
                           "clock_timestamp()-interval '1 second' where user_id=%s",
                           (user,))
        cursor.execute("update import_publication_recovery_queue "
                       "set due_at=clock_timestamp()-interval '1 second', "
                       "queue_version=queue_version+1 "
                       "where task_id=%s", (task,))


@pytest.mark.parametrize("failure", ("issue_after_commit", "durable_unknown",
                                             "schedule_unknown"))
def test_real_worker_ambiguity_has_one_issue_and_no_ordinary_terminal_writer(
        real_worker_resources, monkeypatch, failure):
    from app.import_memory_publication import (
        DurablePublicationUnknown, ImportMemoryPublicationService,
    )
    from app.import_publication_recovery import PostgresImportPublicationRecoveryRepository
    from app.import_publication_worker_adapter import ImportPublicationWorkerAdapter
    from app.isolated_import_runtime import open_isolated_runtime

    settings, db, _raw, client, users, _excluded = real_worker_resources
    task = _upload(db, client, settings, users[0], f"ambiguous-{failure}.txt",
                   b"A durable issue must remain discoverable after ambiguity.")
    issue_calls, adapter_calls, terminal_calls = [], [], []
    original_issue = ImportMemoryPublicationService._issue_live_publication
    original_adapter = ImportPublicationWorkerAdapter.run_once

    def issue(service, *args, **kwargs):
        issue_calls.append(True)
        live = original_issue(service, *args, **kwargs)
        if failure == "issue_after_commit":
            raise RuntimeError("injected_after_durable_issue")
        return live

    def adapter_run(adapter, live):
        adapter_calls.append(True)
        return original_adapter(adapter, live)

    def unknown(service, live):
        issued = service._require_live_issue(live)
        raise DurablePublicationUnknown(issued.key, "document_put")

    monkeypatch.setattr(ImportMemoryPublicationService, "_issue_live_publication", issue)
    monkeypatch.setattr(ImportPublicationWorkerAdapter, "run_once", adapter_run)
    if failure != "issue_after_commit":
        monkeypatch.setattr(ImportMemoryPublicationService,
                            "_execute_live_publication", unknown)
    if failure == "schedule_unknown":
        monkeypatch.setattr(PostgresImportPublicationRecoveryRepository,
                            "schedule_unknown", lambda *_a, **_kw:
                            (_ for _ in ()).throw(RuntimeError("schedule_uncertain")))
    providers = _providers(settings)
    worker = IsolatedImportWorker(open_isolated_runtime(settings,
        provider_probe=providers.probe), providers, worker_id="p4-ambiguous")
    for name in ("fail", "cancel", "release_unstarted"):
        monkeypatch.setattr(worker.leases, name,
                            lambda *_a, _name=name, **_kw: terminal_calls.append(_name))
    try:
        assert worker.run_one_ordinary()
        worker._lanes["heartbeat"].thread.join(5)
        assert not worker._lanes["heartbeat"].thread.is_alive()
        assert worker.run_one_ordinary() is False
        assert len(issue_calls) == 1
        assert len(adapter_calls) == (0 if failure == "issue_after_commit" else 1)
        assert terminal_calls == []
        with db.transaction() as cursor:
            assert cursor.execute("select phase from import_publication_evidence "
                                  "where task_id=%s", (task,)).fetchone()["phase"] == "intent"
            assert cursor.execute("select task_id from import_publication_recovery_queue "
                                  "where task_id=%s", (task,)).fetchone()["task_id"] == task
            assert cursor.execute("select status from import_tasks where id=%s",
                                  (task,)).fetchone()["status"] == "running"
    finally:
        assert worker.close().clean


def test_real_committed_success_survives_heartbeat_loss_before_adapter_return(
        real_worker_resources, monkeypatch):
    from app.import_publication_proof import ImportPublicationProofService
    from app.import_publication_worker_adapter import ImportPublicationWorkerAdapter
    from app.isolated_import_runtime import open_isolated_runtime

    settings, db, _raw, client, users, _excluded = real_worker_resources
    task = _upload(db, client, settings, users[0], "committed-race.txt",
                   b"The exact committed publication remains authoritative.")
    executed, lose = Event(), Event()
    providers = _providers(settings)
    worker = IsolatedImportWorker(open_isolated_runtime(settings,
        provider_probe=providers.probe), providers, worker_id="p4-committed-race")
    original = ImportPublicationWorkerAdapter.run_once
    terminal_calls = []

    def execute_then_lose(adapter, live):
        result = original(adapter, live)
        assert result.status == "committed_success"
        executed.set()
        assert lose.wait(5)
        return result

    def heartbeat_loss(_original, _abort, forward_abort, _deadline):
        assert executed.wait(10)
        forward_abort.set()
        lose.set()

    monkeypatch.setattr(ImportPublicationWorkerAdapter, "run_once", execute_then_lose)
    monkeypatch.setattr(worker, "_heartbeat", heartbeat_loss)
    monkeypatch.setattr(worker.leases, "fail",
                        lambda *_a, **_kw: terminal_calls.append(True))
    try:
        assert worker.run_one_ordinary()
        worker._lanes["attempt"].thread.join(10)
        assert not worker._lanes["attempt"].thread.is_alive()
        assert worker._last_disposition == "committed_success"
        exact = ImportPublicationProofService(db).prove_exact(users[0], task, 1)
        assert exact is not None and exact.proof.phase == "terminal_committed"
        assert terminal_calls == []
    finally:
        lose.set()
        assert worker.close().clean


def test_real_two_tenant_text_pdf_exact_publication_and_exclusion(
        real_worker_resources, monkeypatch):
    from app.import_memory_publication import ImportMemoryPublicationService
    from app.import_publication_worker_adapter import ImportPublicationWorkerAdapter
    from app.import_publication_proof import ImportPublicationProofService
    from app.isolated_import_runtime import open_isolated_runtime
    from app.postgres_snapshots import PostgresSnapshotRepository
    from app.postgres_vector_generations import PostgresVectorGenerationAuthority
    from app.vector_generation_service import VectorGenerationService

    settings, db, raw, client, users, excluded = real_worker_resources
    text_task = _upload(db, client, settings, users[0], "text.txt",
                        b"A complete isolated document for the first tenant.")
    pdf_task = _upload(db, client, settings, users[1], "pages.pdf",
                       _text_pdf("Extractable tenant two PDF page"))
    excluded_task = _upload(db, client, settings, excluded, "excluded.txt",
                            b"An excluded tenant cannot be selected.")
    with db.transaction() as cursor:
        excluded_before = cursor.execute(
            "select * from import_tasks where id=%s", (excluded_task,)).fetchone()
    original_issue = ImportMemoryPublicationService._issue_live_publication
    original_run = ImportPublicationWorkerAdapter.run_once
    issued, executed = [], []

    def count_issue(service, scope, attempt, points, *, event_vector, event_profile):
        live = original_issue(service, scope, attempt, points,
                              event_vector=event_vector, event_profile=event_profile)
        issued.append((attempt.task.task_id, live))
        return live

    def count_run(adapter, live):
        executed.append(live)
        return original_run(adapter, live)

    monkeypatch.setattr(ImportMemoryPublicationService, "_issue_live_publication",
                        count_issue)
    monkeypatch.setattr(ImportPublicationWorkerAdapter, "run_once", count_run)
    providers = _providers(settings)
    runtime = open_isolated_runtime(settings, provider_probe=providers.probe)
    worker = IsolatedImportWorker(runtime, providers, worker_id="p4-real-worker")
    try:
        assert runtime.ordinary_ready(users[0])
        assert runtime.ordinary_ready(users[1])
        assert worker.run_one_ordinary()
        heartbeat = worker._lanes["heartbeat"].thread
        heartbeat.join(5)
        assert not heartbeat.is_alive()
        assert worker.run_one_ordinary()
        assert {task_id for task_id, _live in issued} == {text_task, pdf_task}
        assert len(issued) == len(executed) == 2
        assert all(live is executed[index] for index, (_task_id, live) in enumerate(issued))
        proof = ImportPublicationProofService(db)
        vectors = VectorGenerationService(PostgresVectorGenerationAuthority(db), raw)
        for user, task_id in zip(users, (text_task, pdf_task)):
            exact = proof.prove_exact(user, task_id, 1)
            assert exact is not None and exact.proof.phase == "terminal_committed"
            assert exact.pair.import_task.status == "succeeded"
            assert len(exact.proof.rag_receipt) == len(exact.proof.episode_receipt) == 20
            rag_scope, episode_scope = worker._scopes(user)
            assert rag_scope.identity.profile.dimension == 384
            assert episode_scope.identity.profile.dimension == 384
            rag_points = vectors.read_view(rag_scope).scroll(
                rag_scope.identity.physical_collection, with_vectors=True)
            episode_points = vectors.read_view(episode_scope).scroll(
                episode_scope.identity.physical_collection, with_vectors=True)
            assert rag_points and all(len(point.vector) == 384 for point in rag_points)
            assert all(point.payload["document_id"] == exact.document_ref[1]
                       and point.payload["rag_namespace"] == f"pdf_{user}"
                       for point in rag_points)
            owned_events = [point for point in episode_points
                            if point.payload.get("import_task_id") == task_id]
            assert len(owned_events) == 1
            assert owned_events[0].id == exact.event_id
            assert owned_events[0].payload["content"] == (
                f"用户导入了文档：{exact.pair.import_task.original_name}")
            assert owned_events[0].payload["user_id"] == user
            assert len(owned_events[0].vector) == 384
            with db.transaction() as cursor:
                pinned = cursor.execute("select bucket,object_key,version_id,sha256,"
                    "size_bytes from document_objects where user_id=%s and document_id=%s",
                    (user, exact.document_ref[1])).fetchone()
            assert pinned is not None
            assert exact.document_ref == (user, exact.document_ref[1],
                pinned["bucket"], pinned["object_key"], pinned["version_id"],
                pinned["sha256"], pinned["size_bytes"])
            assert PostgresSnapshotRepository(db).read(user, "history") is not None
            assert PostgresSnapshotRepository(db).read(user, "memory") is not None
        with db.transaction() as cursor:
            assert cursor.execute("select * from import_tasks where id=%s",
                                  (excluded_task,)).fetchone() == excluded_before
            assert cursor.execute("select count(*) as n from import_task_attempts "
                                  "where task_id=%s", (excluded_task,)).fetchone()["n"] == 0
            assert cursor.execute("select count(*) as n from document_objects "
                                  "where user_id=any(%s)", (list(users),)).fetchone()["n"] == 2
            assert cursor.execute("select count(*) as n from history_document_witnesses "
                                  "where tenant_id=any(%s)", (list(users),)).fetchone()["n"] >= 2
        assert worker.run_one_ordinary() is False
    finally:
        report = worker.close()
        assert report.clean and report.ordinary_attempts == 2


def test_real_missing_pinned_source_cannot_issue(real_worker_resources):
    from app.isolated_import_runtime import open_isolated_runtime

    settings, db, _raw, client, users, _excluded = real_worker_resources
    task = _upload(db, client, settings, users[0], "missing.txt",
                   b"The pinned version must be present.")
    with db.transaction() as cursor:
        row = cursor.execute("select object_key,version_id from import_objects "
                             "where task_id=%s", (task,)).fetchone()
    client.delete_object(Bucket=settings.s3_bucket, Key=row["object_key"],
                         VersionId=row["version_id"])
    providers = _providers(settings)
    worker = IsolatedImportWorker(open_isolated_runtime(settings,
        provider_probe=providers.probe), providers, worker_id="p4-missing-source")
    try:
        assert worker.run_one_ordinary()
        with db.transaction() as cursor:
            assert cursor.execute("select status from import_tasks where id=%s",
                                  (task,)).fetchone()["status"] == "running"
            assert cursor.execute("select count(*) as n from import_publication_evidence "
                                  "where task_id=%s", (task,)).fetchone()["n"] == 0
    finally:
        assert worker.close().clean


@pytest.mark.parametrize("changed", ("sha256", "size_bytes"))
def test_real_changed_pinned_hash_or_size_refuses_before_issue(
        real_worker_resources, monkeypatch, changed):
    from app.import_memory_publication import ImportMemoryPublicationService
    from app.isolated_import_runtime import open_isolated_runtime

    settings, db, _raw, client, users, _excluded = real_worker_resources
    task = _upload(db, client, settings, users[0], f"changed-{changed}.txt",
                   b"The original object hash and size are immutable.")
    providers = _providers(settings)
    worker = IsolatedImportWorker(open_isolated_runtime(settings,
        provider_probe=providers.probe), providers, worker_id="p4-changed-pin")
    original_authority = worker._authority
    changed_once = []
    issued = []

    def change_then_authorize(original):
        if not changed_once:
            changed_once.append(True)
            with db.transaction() as cursor:
                if changed == "sha256":
                    cursor.execute("update import_objects set sha256=%s where task_id=%s",
                                   ("0" * 64, task))
                else:
                    cursor.execute("update import_objects set size_bytes=size_bytes+1 "
                                   "where task_id=%s", (task,))
        original_authority(original)

    monkeypatch.setattr(worker, "_authority", change_then_authorize)
    monkeypatch.setattr(ImportMemoryPublicationService, "_issue_live_publication",
                        lambda *_a, **_kw: issued.append(True))
    try:
        assert worker.run_one_ordinary()
        assert changed_once == [True] and issued == []
        with db.transaction() as cursor:
            assert cursor.execute("select count(*) as n from import_publication_evidence "
                                  "where task_id=%s", (task,)).fetchone()["n"] == 0
    finally:
        assert worker.close().clean


def test_real_deterministic_parse_refusal_has_no_intent(real_worker_resources):
    from app.isolated_import_runtime import open_isolated_runtime

    settings, db, _raw, client, users, _excluded = real_worker_resources
    task = _upload(db, client, settings, users[0], "broken.pdf", b"invalid pdf bytes")
    providers = _providers(settings)
    worker = IsolatedImportWorker(open_isolated_runtime(settings,
        provider_probe=providers.probe), providers, worker_id="p4-broken-pdf")
    try:
        assert worker.run_one_ordinary()
        with db.transaction() as cursor:
            assert cursor.execute("select status from import_tasks where id=%s",
                                  (task,)).fetchone()["status"] == "failed"
            assert cursor.execute("select count(*) as n from import_publication_evidence "
                                  "where task_id=%s", (task,)).fetchone()["n"] == 0
    finally:
        assert worker.close().clean


def test_real_expired_original_cannot_issue_after_delayed_preparation(
        real_worker_resources, monkeypatch):
    import app.distributed_import_worker as worker_module
    from app.isolated_import_runtime import open_isolated_runtime

    settings, db, _raw, client, users, _excluded = real_worker_resources
    task = _upload(db, client, settings, users[0], "delayed.txt",
                   b"A stale original attempt cannot publish after lease expiry.")
    entered, release = Event(), Event()
    original_prepare = worker_module.prepare_import_document

    def delayed(**kwargs):
        entered.set()
        assert release.wait(10)
        return original_prepare(**kwargs)

    monkeypatch.setattr(worker_module, "prepare_import_document", delayed)
    providers = _providers(settings)
    worker = IsolatedImportWorker(open_isolated_runtime(settings,
        provider_probe=providers.probe), providers, worker_id="p4-stale-original")
    runner = Thread(target=worker.run_one_ordinary)
    runner.start()
    try:
        assert entered.wait(20)
        with db.transaction() as cursor:
            cursor.execute("update import_tasks set lease_expires_at="
                           "clock_timestamp()-interval '1 second' where id=%s", (task,))
            cursor.execute("update user_mutation_leases set lease_expires_at="
                           "clock_timestamp()-interval '1 second' where user_id=%s",
                           (users[0],))
        release.set()
        runner.join(20)
        assert not runner.is_alive()
        with db.transaction() as cursor:
            assert cursor.execute("select count(*) as n from import_publication_evidence "
                                  "where task_id=%s", (task,)).fetchone()["n"] == 0
    finally:
        release.set()
        runner.join(20)
        assert worker.close().clean


def test_real_unready_tenant_is_absent_from_ordinary_claim(real_worker_resources):
    from app.isolated_import_runtime import open_isolated_runtime

    settings, db, _raw, client, users, _excluded = real_worker_resources
    denied, ready = sorted(users)
    denied_task = _upload(db, client, settings, denied, "denied.txt",
                          b"This tenant fails a fresh provider readiness gate.")
    ready_task = _upload(db, client, settings, ready, "ready.txt",
                         b"This tenant has a verified ready baseline.")
    with db.transaction() as cursor:
        before = cursor.execute("select * from import_tasks where id=%s",
                                (denied_task,)).fetchone()
    providers = _providers(settings)
    runtime = open_isolated_runtime(settings, provider_probe=providers.probe)
    real_ready = runtime.ordinary_ready
    runtime.ordinary_ready = lambda user: user == ready and real_ready(user)
    worker = IsolatedImportWorker(runtime, providers, worker_id="p4-ready-subset")
    try:
        assert worker.run_one_ordinary()
        with db.transaction() as cursor:
            assert cursor.execute("select * from import_tasks where id=%s",
                                  (denied_task,)).fetchone() == before
            assert cursor.execute("select count(*) as n from import_task_attempts "
                                  "where task_id=%s", (denied_task,)).fetchone()["n"] == 0
            assert cursor.execute("select status from import_tasks where id=%s",
                                  (ready_task,)).fetchone()["status"] == "succeeded"
    finally:
        assert worker.close().clean


def test_real_recovery_expiry_filters_excluded_tenant(real_worker_resources):
    from app.import_document_preparation import prepare_import_document
    from app.import_memory_publication import ImportMemoryPublicationService
    from app.isolated_import_runtime import open_isolated_runtime
    from app.object_store import S3ObjectStore
    from app.postgres_import_artifacts import PostgresImportArtifactService
    from app.postgres_import_leases import PostgresImportLeaseRepository
    from app.postgres_vector_generations import PostgresVectorGenerationAuthority, VectorScope
    from app.vector_generation_service import VectorGenerationService
    from hello_agents.memory.rag.index_identity import IndexIdentity

    settings, db, _raw, client, users, excluded = real_worker_resources
    trusted_task = _upload(db, client, settings, users[0], "trusted.txt",
                           b"Trusted expired task")
    excluded_task = _upload(db, client, settings, excluded, "excluded.txt",
                            b"Excluded expired task")
    leases = PostgresImportLeaseRepository(db)
    excluded_claim = None
    for user in (users[0], excluded):
        claim = leases.claim_next("p4-expiry-seed",
                                  allowed_user_ids=frozenset({user}))
        assert claim is not None and claim.task.user_id == user
        if user == excluded:
            excluded_claim = claim
    assert excluded_claim is not None
    store = S3ObjectStore(client, settings.s3_bucket)
    raw_vectors = VectorGenerationService(PostgresVectorGenerationAuthority(db), _raw)
    rag_scope = VectorScope(excluded, "rag", f"pdf_{excluded}",
        IndexIdentity("qdrant", settings.rag_collection, settings.rag_profile))
    episode_scope = VectorScope(excluded, "episode", "episodes",
        IndexIdentity("qdrant", settings.episode_collection, settings.episode_profile))
    provider = _providers(settings)
    source = PostgresImportArtifactService(db, store).read_source_bytes(
        excluded, excluded_task)
    points = prepare_import_document(
        source_bytes=source, task_id=excluded_task, user_id=excluded,
        document_id=excluded_claim.task.document_id,
        original_name=excluded_claim.task.original_name,
        suffix=excluded_claim.task.file_suffix, scope=rag_scope,
        embedding_runtime=provider.rag_runtime)
    service = ImportMemoryPublicationService(db, store, raw_vectors, raw_vectors,
                                              trusted_episode_scope=episode_scope)
    live = service._issue_live_publication(
        rag_scope, excluded_claim, points,
        event_vector=provider.episode_runtime.embed_query(
            f"用户导入了文档：{excluded_claim.task.original_name}"),
        event_profile=settings.episode_profile)
    assert live is not None
    assert provider.close(timeout_seconds=0)
    with db.transaction() as cursor:
        cursor.execute("update import_tasks set lease_expires_at="
                       "clock_timestamp()-interval '1 second' where id=any(%s)",
                       ([trusted_task, excluded_task],))
        cursor.execute("update user_mutation_leases set lease_expires_at="
                       "clock_timestamp()-interval '1 second' where user_id=any(%s)",
                       ([users[0], excluded],))
        excluded_before = cursor.execute("select * from import_tasks where id=%s",
                                         (excluded_task,)).fetchone()
        excluded_audit = cursor.execute("select * from import_task_attempts "
                                        "where task_id=%s", (excluded_task,)).fetchone()
        excluded_queue = cursor.execute("select * from "
            "import_publication_recovery_queue where task_id=%s", (excluded_task,)).fetchone()
        excluded_tokens = cursor.execute("select * from "
            "import_publication_recovery_token_issuance where task_id=%s "
            "order by token", (excluded_task,)).fetchall()
    worker = IsolatedImportWorker(open_isolated_runtime(settings), None,
                                  worker_id="p4-recovery-filter")
    try:
        assert worker.run_one_recovery() is None
        with db.transaction() as cursor:
            assert cursor.execute("select status from import_tasks where id=%s",
                                  (trusted_task,)).fetchone()["status"] == "queued"
            assert cursor.execute("select * from import_tasks where id=%s",
                                  (excluded_task,)).fetchone() == excluded_before
            assert cursor.execute("select * from import_task_attempts "
                                  "where task_id=%s", (excluded_task,)).fetchone() == excluded_audit
            assert cursor.execute("select * from import_publication_recovery_queue "
                                  "where task_id=%s", (excluded_task,)).fetchone() == excluded_queue
            assert cursor.execute("select * from "
                "import_publication_recovery_token_issuance where task_id=%s "
                "order by token", (excluded_task,)).fetchall() == excluded_tokens
    finally:
        assert worker.close().clean


def test_real_recovery_proves_committed_success_with_returned_claim(real_worker_resources):
    from app.isolated_import_runtime import open_isolated_runtime
    from app.import_publication_proof import ImportPublicationProofService

    settings, db, _raw, client, users, _excluded = real_worker_resources
    task = _upload(db, client, settings, users[0], "proved.txt",
                   b"The recovery worker can independently prove a committed publication.")
    providers = _providers(settings)
    worker = IsolatedImportWorker(open_isolated_runtime(settings,
        provider_probe=providers.probe), providers, worker_id="p4-recovery-proof")
    try:
        assert worker.run_one_ordinary()
        assert ImportPublicationProofService(db).prove_exact(users[0], task, 1) is not None
        with db.transaction() as cursor:
            cursor.execute("update import_publication_recovery_queue "
                           "set due_at=clock_timestamp()-interval '1 second', "
                           "queue_version=queue_version+1 "
                           "where task_id=%s", (task,))
        assert worker.run_one_recovery() == "proved_succeeded"
        with db.transaction() as cursor:
            assert cursor.execute("select status from import_tasks where id=%s",
                                  (task,)).fetchone()["status"] == "succeeded"
            assert cursor.execute("select phase from import_publication_evidence "
                                  "where task_id=%s", (task,)).fetchone()["phase"] == "proved_succeeded"
    finally:
        assert worker.close().clean


def test_real_intent_only_recovery_abandons_after_dual_expiry(
        real_worker_resources, monkeypatch):
    from app.postgres_import_leases import ImportLeaseLost, PostgresImportLeaseRepository
    from app.postgres_vector_generations import VectorAuthorityError
    from app.import_publication_worker_adapter import (
        FrozenDisposition, ImportPublicationWorkerAdapter,
    )
    from app.isolated_import_runtime import open_isolated_runtime

    settings, db, _raw, client, users, _excluded = real_worker_resources
    task = _upload(db, client, settings, users[0], "abandon.txt",
                   b"An intent is reserved, but its original execution is held.")
    issued = []

    def hold_without_execution(adapter, live):
        issue = adapter.service._require_live_issue(live)
        issued.append((adapter.service, live, issue))
        key = issue.key
        return FrozenDisposition(key, "needs_reconciliation", key.task_id,
                                 "recovery_unavailable")

    monkeypatch.setattr(ImportPublicationWorkerAdapter, "run_once",
                        hold_without_execution)
    providers = _providers(settings)
    worker = IsolatedImportWorker(open_isolated_runtime(settings,
        provider_probe=providers.probe), providers, worker_id="p4-recovery-abandon")
    try:
        assert worker.run_one_ordinary()
        assert len(issued) == 1
        service, live, issue = issued[0]
        stale_attempt = issue.original_attempt
        worker._lanes["heartbeat"].thread.join(5)
        assert not worker._lanes["heartbeat"].thread.is_alive()
        with db.transaction() as cursor:
            assert cursor.execute("select phase from import_publication_evidence "
                                  "where task_id=%s", (task,)).fetchone()["phase"] == "intent"
            cursor.execute("update import_tasks set lease_expires_at="
                           "clock_timestamp()-interval '1 second' where id=%s", (task,))
            cursor.execute("update user_mutation_leases set lease_expires_at="
                           "clock_timestamp()-interval '1 second' where user_id=%s",
                           (users[0],))
            cursor.execute("update import_publication_recovery_queue "
                           "set due_at=clock_timestamp()-interval '1 second', "
                           "queue_version=queue_version+1 "
                           "where task_id=%s", (task,))
        assert worker.run_one_recovery() == "abandoned"
        with db.transaction() as cursor:
            assert cursor.execute("select phase from import_publication_evidence "
                                  "where task_id=%s", (task,)).fetchone()["phase"] == "abandoned"
            assert cursor.execute("select count(*) as n from document_objects "
                                  "where user_id=%s", (users[0],)).fetchone()["n"] == 0
            reservations = cursor.execute("select vector_kind,state from "
                "generation_reservations where user_id=%s and task_id=%s "
                "and task_lease_version=%s order by vector_kind",
                (users[0], task, stale_attempt.lease_version)).fetchall()
            assert [(row["vector_kind"], row["state"]) for row in reservations] == [
                ("episode", "revoked"), ("rag", "revoked")]
        new_attempt = PostgresImportLeaseRepository(db).claim_next(
            "p4-fresh-after-abandon", allowed_user_ids=frozenset({users[0]}))
        assert new_attempt is not None
        assert new_attempt.lease_version > stale_attempt.lease_version
        assert new_attempt.lease_token != stale_attempt.lease_token
        for scope, authority, candidate in (
                (issue.plan.rag_scope, issue.rag_authority, issue.plan.candidate_ids[0]),
                (service.episode_scope, issue.episode_authority,
                 issue.plan.candidate_ids[1])):
            head = authority.read_head(scope)
            assert head.generation_id != candidate
            with pytest.raises(ImportLeaseLost):
                authority.stage(scope, stale_attempt,
                                expected_revision=head.revision,
                                generation_id=candidate)
            with pytest.raises(VectorAuthorityError, match="permanently reserved"):
                authority.stage(scope, new_attempt,
                                expected_revision=head.revision,
                                generation_id=candidate)
        assert service.pair.rag.read_view(issue.plan.rag_scope).scroll(
            issue.plan.rag_scope.identity.physical_collection) == []
    finally:
        assert worker.close().clean


def test_real_late_original_qdrant_write_stays_invisible_after_fresh_publication(
        real_worker_resources, monkeypatch):
    from app.import_memory_publication import ImportMemoryPublicationService
    from app.import_publication_proof import ImportPublicationProofService
    from app.import_publication_worker_adapter import ImportPublicationWorkerAdapter
    from app.isolated_import_runtime import open_isolated_runtime
    from app.postgres_vector_generations import PostgresVectorGenerationAuthority
    from app.vector_generation_service import VectorGenerationService

    base, db, raw, client, users, _excluded = real_worker_resources
    settings = replace(base, heartbeat_seconds=1, stop_wait_seconds=1)
    user = users[0]
    task = _upload(db, client, settings, user, "late-visible.txt",
                   b"A stale candidate must remain outside the fresh tenant view.")
    entered, release, written, heartbeat_lost = Event(), Event(), Event(), Event()
    issues, adapter_calls, admitted_generations = [], [], []
    original_issue = ImportMemoryPublicationService._issue_live_publication
    original_run = ImportPublicationWorkerAdapter.run_once

    def record_issue(service, scope, attempt, points, *, event_vector, event_profile):
        live = original_issue(service, scope, attempt, points,
                              event_vector=event_vector, event_profile=event_profile)
        issues.append((attempt, service, live, service._require_live_issue(live)))
        return live

    def record_adapter(adapter, live):
        adapter_calls.append(adapter.service._require_live_issue(live).original_attempt)
        return original_run(adapter, live)

    monkeypatch.setattr(ImportMemoryPublicationService, "_issue_live_publication",
                        record_issue)
    monkeypatch.setattr(ImportPublicationWorkerAdapter, "run_once", record_adapter)
    old_providers = _providers(settings)
    old_runtime = open_isolated_runtime(settings, provider_probe=old_providers.probe)
    old_raw = old_runtime.storage_clients()[1]
    original_upsert = type(old_raw.client).upsert

    def held_old_upsert(qdrant_client, *args, **kwargs):
        if qdrant_client is old_raw.client:
            points = kwargs["points"]
            admitted_generations.append(points[0].payload["_gv_generation"])
            entered.set()
            assert release.wait(60)
            result = original_upsert(qdrant_client, *args, **kwargs)
            written.set()
            return result
        return original_upsert(qdrant_client, *args, **kwargs)

    monkeypatch.setattr(type(old_raw.client), "upsert", held_old_upsert)
    old_worker = IsolatedImportWorker(old_runtime, old_providers,
                                      worker_id="p4-old-inflight")

    def lose_original_heartbeat(attempt, **_kwargs):
        assert entered.wait(10)
        assert attempt is issues[0][0]
        heartbeat_lost.set()
        raise RuntimeError("injected_old_lease_loss")

    old_worker.leases.heartbeat = lose_original_heartbeat
    old_caller = Thread(target=old_worker.run_one_ordinary)
    fresh_worker = None
    old_caller.start()
    try:
        assert entered.wait(20) and heartbeat_lost.wait(10)
        old_caller.join(5)
        assert not old_caller.is_alive()
        assert old_worker._lanes["attempt"].thread.is_alive()
        assert len(issues) == len(adapter_calls) == 1
        old_attempt, _old_service, old_live, old_issue = issues[0]
        old_candidates = old_issue.plan.candidate_ids
        assert admitted_generations == [str(old_candidates[0])]
        assert adapter_calls == [old_attempt]
        _due_intent(db, user, task)
        assert old_worker.run_one_recovery() == "abandoned"
        with db.transaction() as cursor:
            revoked = cursor.execute("select generation_id,state,revoked_at from "
                "generation_reservations where generation_id=any(%s)",
                (list(old_candidates),)).fetchall()
        assert {row["generation_id"] for row in revoked} == set(old_candidates)
        assert all(row["state"] == "revoked" and row["revoked_at"] is not None
                   for row in revoked)

        fresh_providers = _providers(settings)
        fresh_worker = IsolatedImportWorker(open_isolated_runtime(settings,
            provider_probe=fresh_providers.probe), fresh_providers,
            worker_id="p4-fresh-after-inflight")
        assert fresh_worker.run_one_ordinary()
        fresh_worker._lanes["heartbeat"].thread.join(10)
        assert not fresh_worker._lanes["heartbeat"].thread.is_alive()
        assert len(issues) == len(adapter_calls) == 2
        fresh_attempt, _fresh_service, fresh_live, fresh_issue = issues[1]
        assert fresh_attempt.task.task_id == old_attempt.task.task_id == task
        assert fresh_attempt.lease_version > old_attempt.lease_version
        assert fresh_attempt.lease_token != old_attempt.lease_token
        assert set(fresh_issue.plan.candidate_ids).isdisjoint(old_candidates)
        assert fresh_live is not old_live
        exact = ImportPublicationProofService(db).prove_exact(
            user, task, fresh_attempt.lease_version)
        assert exact is not None and exact.proof.phase == "terminal_committed"

        release.set()
        assert written.wait(20)
        old_worker._lanes["attempt"].thread.join(20)
        assert not old_worker._lanes["attempt"].thread.is_alive()
        assert len(issues) == len(adapter_calls) == 2
        authority = PostgresVectorGenerationAuthority(db)
        vectors = VectorGenerationService(authority, raw)
        rag_scope, episode_scope = fresh_worker._scopes(user)
        rag_head = authority.read_head(rag_scope)
        episode_head = authority.read_head(episode_scope)
        assert rag_head.generation_id == fresh_issue.plan.candidate_ids[0]
        assert episode_head.generation_id == fresh_issue.plan.candidate_ids[1]
        assert rag_head.generation_id not in old_candidates
        assert episode_head.generation_id not in old_candidates
        old_physical = raw.scroll(rag_scope.identity.physical_collection, {
            "_gv_tenant": user, "_gv_generation": str(old_candidates[0])})
        assert old_physical and all(point.payload["_gv_generation"] ==
                                    str(old_candidates[0]) for point in old_physical)
        visible_rag = vectors.read_view(rag_scope).scroll(
            rag_scope.identity.physical_collection)
        visible_episode = vectors.read_view(episode_scope).scroll(
            episode_scope.identity.physical_collection)
        assert {point.id for point in visible_rag} == {
            point.id for point in fresh_issue.plan.document.points}
        assert {point.id for point in visible_episode} == {
            point.id for point in fresh_issue.plan.bundle.points} | {
                fresh_issue.plan.event_id}
        assert all(point.payload["document_id"] == fresh_attempt.task.document_id
                   for point in visible_rag)
        assert ImportPublicationProofService(db).prove_exact(
            user, task, old_attempt.lease_version) is None
    finally:
        release.set()
        old_caller.join(20)
        if "attempt" in old_worker._lanes:
            old_worker._lanes["attempt"].thread.join(20)
        if fresh_worker is not None:
            assert fresh_worker.close().clean
        assert old_worker.close().clean


def test_real_recovery_retry_later_then_manual_hold_after_seven_unknowns(
        real_worker_resources, monkeypatch):
    from app.import_publication_evidence import AttemptKey
    from app.import_publication_proof import (
        ImportPublicationProofService, PublicationProofUnknown,
    )
    from app.import_publication_worker_adapter import (
        FrozenDisposition, ImportPublicationWorkerAdapter,
    )
    from app.isolated_import_runtime import open_isolated_runtime

    settings, db, _raw, client, users, _excluded = real_worker_resources
    task = _upload(db, client, settings, users[0], "unknown.txt",
                   b"A persistent proof outage must reach manual hold.")

    def hold_without_execution(adapter, live):
        key = adapter.service._require_live_issue(live).key
        return FrozenDisposition(key, "needs_reconciliation", key.task_id,
                                 "recovery_unavailable")

    monkeypatch.setattr(ImportPublicationWorkerAdapter, "run_once",
                        hold_without_execution)
    providers = _providers(settings)
    worker = IsolatedImportWorker(open_isolated_runtime(settings,
        provider_probe=providers.probe), providers, worker_id="p4-recovery-hold")
    try:
        assert worker.run_one_ordinary()
        worker._lanes["heartbeat"].thread.join(5)
        assert not worker._lanes["heartbeat"].thread.is_alive()
        with db.transaction() as cursor:
            cursor.execute("update import_tasks set lease_expires_at="
                           "clock_timestamp()-interval '1 second' where id=%s", (task,))
            cursor.execute("update user_mutation_leases set lease_expires_at="
                           "clock_timestamp()-interval '1 second' where user_id=%s",
                           (users[0],))

        def proof_unavailable(_self, user_id, task_id, lease_version):
            raise PublicationProofUnknown(AttemptKey(user_id, task_id, lease_version),
                                          "evidence")

        monkeypatch.setattr(ImportPublicationProofService, "prove_exact",
                            proof_unavailable)
        for count in range(1, 8):
            with db.transaction() as cursor:
                cursor.execute("update import_publication_recovery_queue "
                               "set due_at=clock_timestamp()-interval '1 second', "
                               "queue_version=queue_version+1 "
                               "where task_id=%s", (task,))
            assert worker.run_one_recovery() == (
                "manual_hold" if count == 7 else "retry_later")
        with db.transaction() as cursor:
            queue = cursor.execute("select state,transient_count from "
                                   "import_publication_recovery_queue "
                                   "where task_id=%s", (task,)).fetchone()
            assert queue == {"state": "manual_hold", "transient_count": 7}
    finally:
        assert worker.close().clean


def test_real_worker_renewal_rejects_stale_pre_renewal_capture(
        real_worker_resources, monkeypatch):
    from app.import_publication_recovery import RecoveryLeaseLost

    settings, db, _raw, _client, users, _excluded = real_worker_resources
    worker, task, _issued = _held_real_intent(real_worker_resources, monkeypatch)
    _due_intent(db, users[0], task)
    original_renew = worker.recovery.renew
    original_prove = worker.recovery.prove_or_hold
    captured = []

    def renew(old, **kwargs):
        captured.append(old)
        refreshed = original_renew(old, **kwargs)
        assert refreshed != old
        return refreshed

    def prove(refreshed):
        with pytest.raises(RecoveryLeaseLost):
            original_prove(captured[0])
        return original_prove(refreshed)

    worker.recovery.renew = renew
    worker.recovery.prove_or_hold = prove
    try:
        assert worker.run_one_recovery() == "abandoned"
        assert len(captured) == 1
    finally:
        assert worker.close().clean


def test_real_capture_first_user_lock_wait_has_bounded_stop(
        real_worker_resources, monkeypatch):
    from app.isolated_import_runtime import open_isolated_runtime

    settings, db, _raw, _client, users, _excluded = real_worker_resources
    settings = replace(settings, provider_timeout_seconds=2, stop_wait_seconds=1)
    setup_worker, task, _issued = _held_real_intent(
        real_worker_resources, monkeypatch, name="locked.txt")
    assert setup_worker.close().clean
    providers = _providers(settings)
    worker = IsolatedImportWorker(open_isolated_runtime(settings,
        provider_probe=providers.probe), providers, worker_id="p4-locked-recovery")
    _due_intent(db, users[0], task)
    worker.leases.recover_expired = lambda **_kw: 0
    entered = Event()
    original_claim = worker.recovery.claim_next
    worker.recovery.claim_next = lambda *_a, **_kw: (
        entered.set(), original_claim(*_a, **_kw))[1]
    caller = Thread(target=worker.run_one_recovery)
    try:
        with db.transaction() as locked:
            locked.execute("select id from users where id=%s for update", (users[0],))
            caller.start()
            assert entered.wait(5)
            captured = False
            until = time.monotonic() + 5
            while time.monotonic() < until:
                with db.transaction() as reader:
                    queue = reader.execute("select state from "
                        "import_publication_recovery_queue where task_id=%s",
                        (task,)).fetchone()
                    tokens = reader.execute("select count(*) as n from "
                        "import_publication_recovery_token_issuance "
                        "where task_id=%s and kind='queue'", (task,)).fetchone()["n"]
                if queue["state"] == "claimed" and tokens >= 1:
                    captured = True
                    break
                time.sleep(0.02)
            assert captured
            report = worker.close()
            assert not report.clean and report.active_threads >= 1
        caller.join(5)
        assert not caller.is_alive()
        assert worker._lanes["recovery"].result is None
    finally:
        if caller.is_alive():
            caller.join(5)
        worker._lanes["recovery"].thread.join(5)
        worker._lanes["cleanup"].thread.join(5)
    assert worker.close().clean


def test_real_proof_spanning_capture_expiry_and_stop_has_no_second_renewal(
        real_worker_resources, monkeypatch):
    from app.import_publication_recovery import RecoveryLeaseLost
    from app.isolated_import_runtime import open_isolated_runtime

    settings, db, _raw, _client, users, _excluded = real_worker_resources
    settings = replace(settings, lease_seconds=4, heartbeat_seconds=1,
                       provider_timeout_seconds=2, stop_wait_seconds=1)
    setup_worker, task, _issued = _held_real_intent(
        real_worker_resources, monkeypatch, name="lateproof.txt")
    assert setup_worker.close().clean
    providers = _providers(settings)
    worker = IsolatedImportWorker(open_isolated_runtime(settings,
        provider_probe=providers.probe), providers, worker_id="p4-late-proof-recovery")
    _due_intent(db, users[0], task)
    entered, release = Event(), Event()
    renewals = []
    actual_renew = worker.recovery.renew
    actual_prove = worker.recovery.prove_or_hold

    def renew(claim, **kwargs):
        renewals.append(claim)
        return actual_renew(claim, **kwargs)

    def delayed_proof(claim):
        entered.set()
        assert release.wait(10)
        return actual_prove(claim)

    worker.recovery.renew = renew
    worker.recovery.prove_or_hold = delayed_proof
    caller = Thread(target=worker.run_one_recovery)
    caller.start()
    try:
        assert entered.wait(5)
        report = worker.close()
        assert not report.clean and report.active_threads >= 1
        time.sleep(4.1)
        release.set()
        caller.join(5)
        worker._lanes["recovery"].thread.join(5)
        assert not worker._lanes["recovery"].thread.is_alive()
        assert isinstance(worker._lanes["recovery"].error, RecoveryLeaseLost)
        assert len(renewals) == 1
    finally:
        release.set()
        caller.join(5)
        worker._lanes["recovery"].thread.join(5)
        worker._lanes["cleanup"].thread.join(5)
    assert worker.close().clean
