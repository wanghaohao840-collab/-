"""RAGTool action lifetime over disposable PostgreSQL and Qdrant heads."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from hello_agents.memory.storage.generation_vector_store import GenerationVectorStore
from hello_agents.tools.builtin import rag_tool as rag_tool_module
from tests.integration.test_postgres_auth_sessions import shared_database
from tests.integration.test_published_rag_reads import _publish, rag_setup


def _tool(factory, tenant, monkeypatch):
    def no_local_pipeline(**_kwargs):
        pytest.fail("distributed reads constructed local authority")

    monkeypatch.setattr(rag_tool_module, "create_rag_pipeline", no_local_pipeline)
    tool = rag_tool_module.RAGTool(
        rag_namespace="documents", published_read_factory=factory,
        authenticated_tenant_id=tenant,
    )
    assert tool._pipelines == {}
    return tool


def test_tool_pins_stats_and_resolves_canonical_source(rag_setup, monkeypatch):
    service, factory, db, _, identity, runtime, users, document_id, leases = rag_setup
    user = users[0]
    _publish(service, db, identity, user, document_id, runtime, "alpha", leases,
             payload_id="conflicting-payload-id")
    tool = _tool(factory, user, monkeypatch)
    original_scroll = GenerationVectorStore.scroll
    published = []

    def publish_between_stats_subreads(view, *args, **kwargs):
        result = original_scroll(view, *args, **kwargs)
        if not published:
            published.append(_publish(service, db, identity, user, document_id,
                                      runtime, "bravo", leases, extra_marker="bravo two",
                                      payload_id="conflicting-payload-id"))
        return result

    monkeypatch.setattr(GenerationVectorStore, "scroll", publish_between_stats_subreads)
    first = tool.execute_result("stats")
    assert first.success and "chunk_count: 1" in first.message
    assert published
    monkeypatch.setattr(GenerationVectorStore, "scroll", original_scroll)
    second = tool.execute_result("stats")
    assert second.success and "chunk_count: 2" in second.message

    hit = tool.execute_result("search", query="shared search terms",
                              document_ids=[document_id], limit=5)
    assert hit.success and hit.data["results"]
    locator = hit.data["results"][0]
    assert locator["chunk_id"] in {"same-chunk-id", "second-chunk-id"}
    assert locator["chunk_id"] != "conflicting-payload-id"
    source = tool.execute_result("get_document_chunk", document_id=document_id,
                                 chunk_id=locator["chunk_id"],
                                 chunk_index=locator["chunk_index"])
    assert source.success
    assert source.data["chunk"]["content_sha256"] == locator["content_sha256"]
    assert tool.execute_result("get_document_chunk", document_id=document_id,
                               chunk_id="conflicting-payload-id",
                               chunk_index=0).success is False


def test_concurrent_calls_keep_separate_operations_and_tenants(rag_setup, monkeypatch):
    service, factory, db, _, identity, runtime, users, document_id, leases = rag_setup
    first_user, second_user = users
    _publish(service, db, identity, first_user, document_id, runtime, "alpha", leases)
    _publish(service, db, identity, second_user, document_id, runtime, "private", leases)
    entered = Event()
    release = Event()
    original_open = factory.open_operation
    calls = []

    def paused_open(tenant, namespace):
        operation = original_open(tenant, namespace)
        calls.append((tenant, operation.head.revision))
        if tenant == first_user and len([call for call in calls if call[0] == first_user]) == 1:
            entered.set()
            assert release.wait(15)
        return operation

    monkeypatch.setattr(factory, "open_operation", paused_open)
    first_tool = _tool(factory, first_user, monkeypatch)
    second_tool = _tool(factory, second_user, monkeypatch)
    with ThreadPoolExecutor(max_workers=3) as executor:
        old_future = executor.submit(first_tool.execute_result, "search", query="shared")
        assert entered.wait(15)
        _publish(service, db, identity, first_user, document_id, runtime, "bravo", leases)
        new_future = executor.submit(first_tool.execute_result, "search", query="shared")
        private_future = executor.submit(second_tool.execute_result, "search", query="shared")
        new = new_future.result(timeout=15)
        private = private_future.result(timeout=15)
        release.set()
        old = old_future.result(timeout=15)
    assert old.success and old.data["results"][0]["content"].startswith("alpha")
    assert new.success and new.data["results"][0]["content"].startswith("bravo")
    assert private.success and private.data["results"][0]["content"].startswith("private")
    assert [revision for tenant, revision in calls if tenant == first_user] == [1, 2]
    assert [revision for tenant, revision in calls if tenant == second_user] == [1]


def test_summary_reopens_after_publication_and_has_no_shared_cache(rag_setup, monkeypatch):
    service, factory, db, _, identity, runtime, users, document_id, leases = rag_setup
    user = users[0]
    _publish(service, db, identity, user, document_id, runtime, "alpha", leases)
    tool = _tool(factory, user, monkeypatch)
    prompts = []

    def generate(prompt):
        prompts.append(prompt)
        return "summary"

    monkeypatch.setattr(tool, "_generate", generate)
    first = tool.execute_result("ask", query="总结", document_ids=[document_id],
                                mode="summary")
    assert first.success and any("alpha shared" in prompt for prompt in prompts)
    assert first.data["summary_cache_hits"] == 0
    _publish(service, db, identity, user, document_id, runtime, "bravo", leases)
    prompts.clear()
    second = tool.execute_result("ask", query="总结", document_ids=[document_id],
                                 mode="summary")
    assert second.success and any("bravo shared" in prompt for prompt in prompts)
    assert all("alpha shared" not in prompt for prompt in prompts)
    assert second.data["summary_cache_hits"] == 0
    assert tool._document_summary_cache == {}


def test_published_mode_rejects_writes_and_graph_before_local_authority(rag_setup, monkeypatch):
    _, factory, _, _, _, _, users, _, _ = rag_setup
    tool = _tool(factory, users[0], monkeypatch)
    for action in ("add_text", "add_document", "delete_document", "clear",
                   "graph_status", "retry_document_graph"):
        result = tool.execute_result(action)
        assert not result.success and result.error_code == "rag_operation"
    assert not tool.execute_result("ask", query="x", graph_mode="required").success
    assert tool._pipelines == {}
