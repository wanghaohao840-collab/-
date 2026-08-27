from __future__ import annotations

import json

from app.auth import AuthService
from app.database import initialize_database
from app.history import EMPTY_HISTORY, HistoryRepository
from app.qa_migration import QaLegacyMigrationService
from app.qa_repository import QaRepository
from app.storage import UserStorage, write_json_atomic


def legacy_question(question, answer, **overrides):
    item = {
        "question": question,
        "answer": answer,
        "document_ids": ["doc-b", "doc-a"],
        "document_names": ["B.pdf", "A.pdf"],
        "mode": "joint",
        "asked_at": "2026-08-01T08:00:00Z",
    }
    item.update(overrides)
    return item


def parts(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    user_id = AuthService(db_path).register(
        "Owner", "correct horse battery"
    ).id
    history = HistoryRepository(tmp_path / "history.json")
    history.save(dict(EMPTY_HISTORY))
    return (
        user_id,
        history,
        QaLegacyMigrationService(db_path),
        QaRepository(db_path),
    )


def test_each_flat_question_becomes_one_truthful_single_turn_conversation(
    tmp_path,
) -> None:
    user_id, history, migrator, repository = parts(tmp_path)
    history.save(
        {
            **EMPTY_HISTORY,
            "questions": [
                legacy_question("Q1", "A1"),
                legacy_question("Q2", "A2", mode="compare"),
            ],
        }
    )

    first = migrator.ensure_user_migrated(user_id, history)
    second = migrator.ensure_user_migrated(user_id, history)

    assert first.imported_count == second.imported_count == 2
    conversations = repository.list_conversations(user_id).items
    assert len(conversations) == 2
    assert all(item.conversation.origin == "legacy_json" for item in conversations)
    assert all(
        tuple(document.document_id for document in item.documents)
        == ("doc-a", "doc-b")
        for item in conversations
    )
    for conversation in conversations:
        messages = repository.list_messages(user_id, conversation.id).items
        assert len(messages) == 2
        assert messages[1].source_state == "legacy_unavailable"
    report_turns = repository.list_completed_turns_for_report(user_id)
    assert {turn.question for turn in report_turns} == {"Q1", "Q2"}
    assert all(turn.document_ids == ("doc-a", "doc-b") for turn in report_turns)


def test_invalid_records_are_counted_without_fabricating_document_ids(
    tmp_path,
) -> None:
    user_id, history, migrator, repository = parts(tmp_path)
    history.save(
        {
            **EMPTY_HISTORY,
            "questions": [
                legacy_question("valid", "answer", document_ids=["real-doc"]),
                legacy_question("no identity", "answer", document_ids=[]),
                legacy_question("", "answer"),
                {"question": "missing answer"},
            ],
        }
    )

    result = migrator.ensure_user_migrated(user_id, history)

    assert result.imported_count == 1
    assert result.skipped_count == 3
    conversation = repository.list_conversations(user_id).items[0]
    assert tuple(item.document_id for item in conversation.documents) == ("real-doc",)


def test_changed_digest_replaces_only_legacy_json_projection(tmp_path) -> None:
    user_id, history, migrator, repository = parts(tmp_path)
    history.save({**EMPTY_HISTORY, "questions": [legacy_question("old", "A")]})
    first = migrator.ensure_user_migrated(user_id, history)
    first_id = repository.list_conversations(user_id).items[0].id

    history.save({**EMPTY_HISTORY, "questions": [legacy_question("new", "B")]})
    second = migrator.ensure_user_migrated(user_id, history)
    conversations = repository.list_conversations(user_id).items

    assert second.source_digest != first.source_digest
    assert len(conversations) == 1
    assert conversations[0].id != first_id
    messages = repository.list_messages(user_id, conversations[0].id).items
    assert [item.content for item in messages] == ["new", "B"]


def test_privacy_scrub_prevents_deleted_legacy_conversation_resurrection(
    tmp_path,
) -> None:
    user_id, history, migrator, repository = parts(tmp_path)
    history.save(
        {
            **EMPTY_HISTORY,
            "questions": [
                legacy_question("delete me", "A"),
                legacy_question("keep me", "B"),
            ],
        }
    )
    migrator.ensure_user_migrated(user_id, history)
    conversations = repository.list_conversations(user_id).items
    target = next(
        item
        for item in conversations
        if repository.list_messages(user_id, item.id).items[0].content
        == "delete me"
    )

    assert migrator.scrub_conversations(user_id, history, (target.id,)) == 1
    assert [item["question"] for item in history.load()["questions"]] == [
        "keep me"
    ]
    migrator.ensure_user_migrated(user_id, history)
    turns = repository.list_completed_turns_for_report(user_id)
    assert [turn.question for turn in turns] == ["keep me"]


def test_privacy_scrub_updates_owned_legacy_rollback_copy(tmp_path) -> None:
    user_id, history, _, repository = parts(tmp_path)
    source_data = {
        **EMPTY_HISTORY,
        "questions": [
            legacy_question("delete me", "A"),
            legacy_question("keep me", "B"),
        ],
    }
    history.save(source_data)
    storage = UserStorage(tmp_path / "data")
    backup = storage.data_root / "legacy_backups" / "run"
    backup_history = backup / "source" / "memory_data" / "history.json"
    backup_history.parent.mkdir(parents=True)
    HistoryRepository(backup_history).save(source_data)
    manifest = {
        "user_id": user_id,
        "files": [
            {
                "kind": "history",
                "relative": "memory_data/history.json",
                "size": backup_history.stat().st_size,
                "sha256": "old",
            }
        ],
    }
    write_json_atomic(backup / "manifest.json", manifest)
    migrator = QaLegacyMigrationService(repository.db_path, storage)
    migrator.ensure_user_migrated(user_id, history)
    target = next(
        item
        for item in repository.list_conversations(user_id).items
        if repository.list_messages(user_id, item.id).items[0].content
        == "delete me"
    )

    migrator.scrub_conversations(user_id, history, (target.id,))

    assert [
        item["question"]
        for item in HistoryRepository(backup_history).load()["questions"]
    ] == ["keep me"]
    updated_manifest = json.loads(
        (backup / "manifest.json").read_text(encoding="utf-8")
    )
    assert updated_manifest["files"][0]["sha256"] != "old"
