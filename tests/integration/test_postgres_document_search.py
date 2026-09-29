"""Authenticated search bridges over two pools and one disposable PG/Qdrant scope."""
from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from app.document_search import (
    DocumentSearchRequest, DocumentSearchScopeError, DocumentSearchSourceStaleError,
)
from app.history import EMPTY_HISTORY
from app.postgres_document_search import create_postgres_document_search
from app.postgres_sessions import PostgresSessionRepository
from app.postgres_snapshots import PostgresSnapshotRepository
from app.postgres_vector_generations import PostgresVectorGenerationAuthority
from app.published_rag_reads import PublishedRAGReadFactory
from app.session import InvalidSessionError
from app.vector_generation_service import VectorGenerationService
from hello_agents.memory.storage.vector_store import QdrantVectorStore
from tests.integration.test_postgres_auth_sessions import shared_database
from tests.integration.test_published_rag_reads import _publish, rag_setup


def _history(document_id: str, user_id: str) -> dict:
    history = {key: list(value) for key, value in EMPTY_HISTORY.items()}
    history["documents"].append({
        "user_id": user_id,
        "document_id": document_id,
        "document_name": "sample.txt",
        "file_suffix": ".txt",
        # Historical metadata is deliberately absent on this API host.
        "document_path": f"C:/missing-distributed-source/{document_id}.txt",
        "loaded_at": "2026-09-29T00:00:00+00:00",
    })
    return history


def test_two_bridges_authenticate_scope_fence_and_resolve_published_source(
    rag_setup, shared_database,
):
    service, factory_a, db_a, _, identity, runtime, users, owner_doc, leases = rag_setup
    owner, other = users
    other_doc = str(uuid4())
    open_pool, _ = shared_database
    db_b = open_pool()
    raw_b = QdrantVectorStore(url=os.environ["GENERATION_QDRANT_TEST_URL"], retry_delays=())
    factory_b = PublishedRAGReadFactory(
        VectorGenerationService(PostgresVectorGenerationAuthority(db_b), raw_b),
        runtime, identity,
    )
    owner_history = _history(owner_doc, owner)
    wrong_suffix = str(uuid4())
    wrong_owner = str(uuid4())
    owner_history["documents"].extend((
        {**owner_history["documents"][0], "document_id": wrong_suffix,
         "document_path": f"C:/missing-distributed-source/{wrong_suffix}.pdf"},
        {**owner_history["documents"][0], "document_id": wrong_owner,
         "user_id": other},
    ))
    PostgresSnapshotRepository(db_a).compare_and_swap(
        owner, "history", owner_history, expected_version=0,
    )
    PostgresSnapshotRepository(db_b).compare_and_swap(
        other, "history", _history(other_doc, other), expected_version=0,
    )
    assert not Path(f"C:/missing-distributed-source/{owner_doc}.txt").exists()

    _publish(service, db_a, identity, owner, owner_doc, runtime, "alpha", leases)
    _publish(service, db_a, identity, other, other_doc, runtime, "private", leases)
    owner_session = PostgresSessionRepository(db_a).create(owner)
    other_session = PostgresSessionRepository(db_b).create(other)
    bridge_a = create_postgres_document_search(
        PostgresSessionRepository(db_a), PostgresSnapshotRepository(db_a), factory_a,
    )
    bridge_b = create_postgres_document_search(
        PostgresSessionRepository(db_b), PostgresSnapshotRepository(db_b), factory_b,
    )

    request = DocumentSearchRequest("shared search terms", (owner_doc,))
    first = bridge_a.search(owner_session.token, request)
    second = bridge_b.search(owner_session.token, request)
    assert first.results and second.results
    assert first.results[0].locator == second.results[0].locator
    assert first.results[0].excerpt.startswith("alpha")
    assert bridge_b.resolve_chunk(owner_session.token, first.results[0].locator).content.startswith("alpha")
    assert bridge_b.search(
        other_session.token, DocumentSearchRequest("shared", (other_doc,))
    ).results[0].excerpt.startswith("private")
    with pytest.raises(DocumentSearchScopeError):
        bridge_b.search(other_session.token, request)
    with pytest.raises(DocumentSearchScopeError):
        bridge_b.resolve_chunk(other_session.token, first.results[0].locator)
    for excluded in (wrong_suffix, wrong_owner):
        with pytest.raises(DocumentSearchScopeError):
            bridge_b.search(owner_session.token, DocumentSearchRequest("shared", (excluded,)))

    _publish(service, db_a, identity, owner, owner_doc, runtime, "bravo", leases)
    with pytest.raises(DocumentSearchSourceStaleError):
        bridge_b.resolve_chunk(owner_session.token, first.results[0].locator)
    fresh = bridge_b.search(owner_session.token, request)
    assert fresh.results[0].excerpt.startswith("bravo")
    assert fresh.results[0].locator.content_sha256 != first.results[0].locator.content_sha256

    with db_a.transaction() as cursor:
        cursor.execute("""insert into qa_deletion_fences
            (id,user_id,target_type,target_id,status,stage,created_at,updated_at)
            values (%s,%s,'document',%s,'queued','fenced','now','now')""",
            (str(uuid4()), owner, owner_doc))
    for bridge in (bridge_a, bridge_b):
        with pytest.raises(DocumentSearchScopeError):
            bridge.search(owner_session.token, request)
        with pytest.raises(DocumentSearchScopeError):
            bridge.resolve_chunk(owner_session.token, fresh.results[0].locator)

    # A terminal failed deletion releases the same visibility fence as local QA.
    with db_a.transaction() as cursor:
        cursor.execute("""update qa_deletion_fences set status='failed',
            attempt_count=3 where user_id=%s and target_id=%s""", (owner, owner_doc))
    assert bridge_b.search(owner_session.token, request).results[0].excerpt.startswith("bravo")

    PostgresSessionRepository(db_a).delete(owner_session.token)
    with pytest.raises(InvalidSessionError):
        bridge_b.search(owner_session.token, request)
