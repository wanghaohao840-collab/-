from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.database import connect, initialize_database
from app.qa_deletion import QaDeletionRepository
from app.qa_memory import QaMemoryLinker, qa_memory_id
from app.qa_models import QaDocumentCandidate
from app.qa_repository import QaRepository


OWNER = "owner"
NOW = datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc)


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


@pytest.fixture
def memory_parts(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            insert into users (
                id, username, username_key, password_hash, created_at, updated_at
            ) values ('owner', 'Owner', 'owner', 'hash', ?, ?)
            """,
            (iso(NOW), iso(NOW)),
        )
    repository = QaRepository(db_path)
    conversation = repository.create_conversation(
        OWNER, (QaDocumentCandidate("doc", "文档.pdf", OWNER),), now=iso(NOW)
    )
    turn = repository.create_pending_turn(
        OWNER, conversation.id, "问题", "joint", "client", now=iso(NOW)
    )
    repository.complete_turn(
        OWNER,
        turn.assistant_message.id,
        0,
        "回答",
        (),
        "none",
        None,
        now=iso(NOW),
    )
    return repository, conversation, turn


def test_memory_claim_is_leased_reclaimable_and_stale_owner_loses(memory_parts) -> None:
    repository, _, turn = memory_parts
    first = repository.claim_next_memory_sync(
        "worker-a", lease_seconds=30, now=iso(NOW)
    )
    assert first.id == turn.assistant_message.id
    assert first.memory_sync_status == "running"
    assert repository.claim_next_memory_sync(
        "worker-b", lease_seconds=30, now=iso(NOW)
    ) is None

    later = NOW + timedelta(seconds=31)
    second = repository.claim_next_memory_sync(
        "worker-b", lease_seconds=30, now=iso(later)
    )
    assert second.id == first.id
    assert second.memory_sync_attempt_count == 2
    assert not repository.complete_memory_sync(
        OWNER, second.id, "worker-a", "wrong", first.version, now=iso(later)
    )
    assert repository.complete_memory_sync(
        OWNER,
        second.id,
        "worker-b",
        qa_memory_id(OWNER, second.id),
        second.version,
        now=iso(later),
    )


def test_memory_linker_uses_deterministic_id_and_compensates_lost_attach(
    memory_parts,
) -> None:
    repository, conversation, _ = memory_parts
    claimed = repository.claim_next_memory_sync(
        "worker", lease_seconds=30, now=iso(NOW)
    )

    class Manager:
        def __init__(self):
            self.added = []
            self.removed = []

        def add_memory(self, *args, **kwargs):
            self.added.append((args, kwargs))
            return kwargs["memory_id"]

        def remove_memory(self, memory_id, *, memory_type):
            self.removed.append((memory_id, memory_type))
            return True

    manager = Manager()
    linker = QaMemoryLinker(repository)
    assert linker.sync(
        SimpleNamespace(memory_tool=SimpleNamespace(memory_manager=manager)),
        conversation,
        claimed,
        "worker",
        now=iso(NOW),
    )
    expected = qa_memory_id(OWNER, claimed.id)
    assert manager.added[0][1]["memory_id"] == expected
    assert manager.added[0][1]["metadata"]["document_ids"] == ["doc"]
    assert repository.get_message(OWNER, claimed.id).memory_id == expected
    assert manager.removed == []


def test_memory_linker_failure_records_safe_state_without_losing_answer(
    memory_parts,
) -> None:
    repository, conversation, _ = memory_parts
    claimed = repository.claim_next_memory_sync(
        "worker", lease_seconds=30, now=iso(NOW)
    )

    class Manager:
        def add_memory(self, *args, **kwargs):
            raise RuntimeError("raw secret")

    linker = QaMemoryLinker(repository)
    assert not linker.sync(
        SimpleNamespace(memory_tool=SimpleNamespace(memory_manager=Manager())),
        conversation,
        claimed,
        "worker",
        now=iso(NOW),
    )
    message = repository.get_message(OWNER, claimed.id)
    assert message.status == "completed"
    assert message.memory_sync_status == "failed"
    assert message.safe_error_code is None


def test_memory_linker_removes_write_when_deletion_fence_wins(
    memory_parts,
) -> None:
    repository, conversation, _ = memory_parts
    claimed = repository.claim_next_memory_sync(
        "worker", lease_seconds=30, now=iso(NOW)
    )
    deletion = QaDeletionRepository(repository.db_path)
    deletion.create_conversation_deletion(
        OWNER, conversation.id, now=iso(NOW)
    )

    class Manager:
        def __init__(self):
            self.removed = []

        def add_memory(self, *args, **kwargs):
            return kwargs["memory_id"]

        def remove_memory(self, memory_id, *, memory_type):
            self.removed.append((memory_id, memory_type))
            return True

    manager = Manager()
    linker = QaMemoryLinker(repository)
    assert not linker.sync(
        SimpleNamespace(memory_tool=SimpleNamespace(memory_manager=manager)),
        conversation,
        claimed,
        "worker",
        now=iso(NOW),
    )
    assert manager.removed == [
        (qa_memory_id(OWNER, claimed.id), "episodic")
    ]
