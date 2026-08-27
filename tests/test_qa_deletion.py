from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.database import connect, initialize_database
from app.qa_deletion import (
    QaDeletionRepository,
    QaDeletionService,
    QaDeletionWorker,
)
from app.qa_models import QaDocumentCandidate
from app.qa_repository import QaRepository


OWNER = "owner"
OTHER = "other"


class Sessions:
    def __init__(self, runtime):
        self.runtime_registry = RuntimeRegistry(runtime)
        self.sessions = {
            "token": SimpleNamespace(user_id=OWNER, runtime=runtime),
            "other": SimpleNamespace(user_id=OTHER, runtime=runtime),
        }
        self.cleared = []

    def get_session(self, token):
        return self.sessions[token]

    def clear_document_selection(self, user_id, document_id):
        self.cleared.append((user_id, document_id))
        return 1


class RuntimeRegistry:
    def __init__(self, runtime):
        self.runtime = runtime
        self.acquired = []
        self.released = []

    def acquire_background(self, user_id):
        self.acquired.append(user_id)
        return self.runtime

    def release_background(self, user_id):
        self.released.append(user_id)


class MemoryManager:
    def __init__(self):
        self.removed = []

    def remove_memory(self, memory_id, *, memory_type):
        self.removed.append((memory_id, memory_type))
        return True


class Documents:
    def __init__(self):
        self.deleted = []
        self.items = {OWNER: {"doc"}, OTHER: {"other-doc"}}

    def has_document(self, user_id, document_id):
        return document_id in self.items[user_id]

    def perform_document_delete(self, user_id, runtime, document_id):
        self.deleted.append((user_id, document_id))


class Wake:
    def __init__(self):
        self.count = 0

    def notify(self):
        self.count += 1


@pytest.fixture
def deletion_parts(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    with connect(db_path) as conn:
        conn.executemany(
            """
            insert into users (
                id, username, username_key, password_hash, created_at, updated_at
            ) values (?, ?, ?, 'hash', '2026-08-27T00:00:00Z', '2026-08-27T00:00:00Z')
            """,
            ((OWNER, "Owner", "owner"), (OTHER, "Other", "other")),
        )
    qa = QaRepository(db_path)
    deletion_repository = QaDeletionRepository(db_path)
    memory = MemoryManager()
    runtime = SimpleNamespace(
        memory_tool=SimpleNamespace(memory_manager=memory)
    )
    sessions = Sessions(runtime)
    documents = Documents()
    wake = Wake()
    service = QaDeletionService(
        sessions, documents, qa, deletion_repository, wake
    )
    worker = QaDeletionWorker(
        deletion_repository,
        sessions.runtime_registry,
        documents,
        sessions,
    )
    return qa, deletion_repository, service, worker, memory, documents, sessions


def completed_conversation(qa, *, document_id="doc", memory_id="qa-memory"):
    conversation = qa.create_conversation(
        OWNER, (QaDocumentCandidate(document_id, "文档.pdf", OWNER),)
    )
    turn = qa.create_pending_turn(
        OWNER, conversation.id, "问题", "joint", f"client-{conversation.id}"
    )
    qa.complete_turn(
        OWNER, turn.assistant_message.id, 0, "回答", (), "none", memory_id
    )
    return conversation, turn


def test_conversation_fence_hides_rows_and_blocks_late_completion(
    deletion_parts,
) -> None:
    qa, repository, service, worker, memory, _, _ = deletion_parts
    conversation = qa.create_conversation(
        OWNER, (QaDocumentCandidate("doc", "文档.pdf", OWNER),)
    )
    pending = qa.create_pending_turn(
        OWNER, conversation.id, "问题", "joint", "client"
    )

    deletion = service.request_conversation("token", conversation.id)
    duplicate = service.request_conversation("token", conversation.id)
    assert duplicate.id == deletion.id
    assert repository.has_active_fence(
        OWNER, "conversation", conversation.id
    )
    assert qa.get_conversation(OWNER, conversation.id) is None
    assert not qa.complete_turn(
        OWNER, pending.assistant_message.id, 0, "late", (), "none", None
    )
    assert service.request_conversation("other", conversation.id) is None

    assert worker.run_once("delete-worker")
    assert repository.get_deletion(OWNER, deletion.id).status == "completed"
    assert service.request_conversation("token", conversation.id).id == deletion.id
    assert memory.removed == []


def test_conversation_deletion_removes_only_linked_memory(deletion_parts) -> None:
    qa, repository, service, worker, memory, _, _ = deletion_parts
    conversation, _ = completed_conversation(qa, memory_id="qa-target")
    other, _ = completed_conversation(qa, memory_id="qa-other")
    deletion = service.request_conversation("token", conversation.id)
    assert worker.run_once("delete-worker")
    assert repository.get_deletion(OWNER, deletion.id).status == "completed"
    assert memory.removed == [("qa-target", "episodic")]
    assert qa.get_conversation(OWNER, other.id) is not None


def test_document_fence_cascades_related_conversations_then_document(
    deletion_parts,
) -> None:
    qa, repository, service, worker, memory, documents, sessions = deletion_parts
    first, _ = completed_conversation(qa, memory_id="qa-one")
    second, _ = completed_conversation(qa, memory_id="qa-two")
    deletion = service.request_document("token", "doc")
    assert deletion.affected_conversation_count == 2
    assert repository.has_active_fence(OWNER, "document", "doc")
    assert qa.list_conversations(OWNER).items == ()

    assert worker.run_once("delete-worker")
    completed = repository.get_deletion(OWNER, deletion.id)
    assert completed.status == "completed"
    assert documents.deleted == [(OWNER, "doc")]
    assert set(memory.removed) == {
        ("qa-one", "episodic"),
        ("qa-two", "episodic"),
    }
    assert sessions.cleared == [(OWNER, "doc")]
    assert sessions.runtime_registry.acquired == [OWNER]
    assert sessions.runtime_registry.released == [OWNER]
    assert qa.get_conversation(OWNER, first.id) is None
    assert qa.get_conversation(OWNER, second.id) is None


def test_failed_stage_keeps_active_retryable_fence(deletion_parts) -> None:
    qa, repository, service, worker, _, documents, _ = deletion_parts
    completed_conversation(qa)
    deletion = service.request_document("token", "doc")

    def fail(*args):
        raise OSError("raw path")

    documents.perform_document_delete = fail
    assert worker.run_once("delete-worker")
    failed = repository.get_deletion(OWNER, deletion.id)
    assert failed.status == "failed"
    assert failed.safe_error_code == "QA_DELETION_FAILED"
    assert "path" not in repr(failed)
    assert repository.has_active_fence(OWNER, "document", "doc")
