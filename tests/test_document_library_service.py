from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.document_library import (
    DocumentDeleteFailedError,
    DocumentImportActiveError,
    DocumentLibraryService,
    DocumentNotFoundError,
)
from app.history import HistoryRepository
from app.session import SessionRegistry
from app.storage import UserStorage
from assistants.pdf_learning_assistant import (
    DocumentDeleteResult,
    PDFLearningAssistant,
)


class TrackingLock:
    def __init__(self) -> None:
        self.depth = 0

    def __enter__(self):
        self.depth += 1
        return self

    def __exit__(self, *_args):
        self.depth -= 1


class FakeHistory:
    def __init__(self, lock: TrackingLock, documents: list[object]) -> None:
        self.lock = lock
        self.documents = documents
        self.loads = 0

    def load(self):
        assert self.lock.depth == 1
        self.loads += 1
        return {
            "documents": list(self.documents),
            "questions": [],
            "notes": [],
            "sessions": [],
        }


class FakeRegistry:
    def __init__(self, session) -> None:
        self.session = session
        self.tokens: list[str] = []
        self.clear_calls: list[tuple[str, str]] = []

    def get_session(self, token: str):
        self.tokens.append(token)
        return self.session

    def clear_document_selection(self, user_id: str, document_id: str) -> int:
        self.clear_calls.append((user_id, document_id))
        return 2


class FakeImportService:
    def __init__(self, active_document_id: str | None = None) -> None:
        self.active_document_id = active_document_id
        self.calls: list[tuple[str, str]] = []

    def has_active_task_for_document(self, user_id: str, document_id: str) -> bool:
        self.calls.append((user_id, document_id))
        return document_id == self.active_document_id


@pytest.fixture
def service_fixture(tmp_path):
    user_id = "11111111-1111-4111-8111-111111111111"
    storage = UserStorage(tmp_path / "data")
    paths = storage.ensure_user_dirs(user_id)
    lock = TrackingLock()
    history = FakeHistory(lock, [])
    assistant = SimpleNamespace(
        delete_document=Mock(
            side_effect=lambda document_id: DocumentDeleteResult(
                document_id=document_id,
                rag_message="deleted",
                documents_removed=1,
                questions_removed=0,
            )
        )
    )
    runtime = SimpleNamespace(lock=lock, history=history)
    session = SimpleNamespace(user_id=user_id, runtime=runtime, assistant=assistant)
    registry = FakeRegistry(session)
    imports = FakeImportService()
    service = DocumentLibraryService(registry, storage, imports)
    return SimpleNamespace(
        user_id=user_id,
        storage=storage,
        paths=paths,
        lock=lock,
        history=history,
        assistant=assistant,
        registry=registry,
        imports=imports,
        service=service,
    )


def _record(document_id: str, path: Path, **overrides):
    item = {
        "document_id": document_id,
        "document_name": path.name,
        "document_path": str(path),
        "file_suffix": path.suffix,
        "loaded_at": "2026-08-15T08:30:00+00:00",
    }
    item.update(overrides)
    return item


def test_list_documents_projects_latest_safe_records_under_runtime_lock(
    service_fixture,
):
    first = service_fixture.paths.documents / "first.md"
    newest = service_fixture.paths.documents / "newest.txt"
    duplicate_old = service_fixture.paths.documents / "old-name.md"
    duplicate_new = service_fixture.paths.documents / "new-name.md"
    first.write_bytes(b"first")
    newest.write_bytes(b"newest-content")
    duplicate_old.write_bytes(b"old")
    duplicate_new.write_bytes(b"latest")
    service_fixture.history.documents = [
        _record(
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            first,
            loaded_at=None,
        ),
        _record(
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            duplicate_old,
            document_name="obsolete.md",
            loaded_at="2026-08-15T07:00:00+00:00",
        ),
        _record(
            "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            newest,
            loaded_at="2026-08-15T10:00:00+00:00",
        ),
        _record(
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            duplicate_new,
            document_name="latest.md",
            loaded_at="2026-08-15T09:00:00+00:00",
        ),
    ]

    items = service_fixture.service.list_documents("cookie-token")

    assert [item.document_id for item in items] == [
        "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    ]
    assert items[1].name == "latest.md"
    assert items[1].size_bytes == len(b"latest")
    assert items[2].loaded_at is None
    assert items[2].status == "ready"
    assert not hasattr(items[0], "document_path")
    assert not hasattr(items[0], "user_id")
    assert service_fixture.history.loads == 1
    assert service_fixture.registry.tokens == ["cookie-token"]


def test_list_documents_skips_malformed_and_escaping_records_without_path_leak(
    service_fixture,
    caplog,
):
    outside = service_fixture.paths.root.parent / "other-user" / "secret.md"
    outside.parent.mkdir(parents=True)
    outside.write_text("secret", encoding="utf-8")
    safe = service_fixture.paths.documents / "safe.md"
    safe.write_text("safe", encoding="utf-8")
    service_fixture.history.documents = [
        "not-a-record",
        {"document_id": "missing-fields"},
        _record("dddddddd-dddd-4ddd-8ddd-dddddddddddd", outside),
        _record("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", safe),
    ]

    items = service_fixture.service.list_documents("cookie-token")

    assert [item.document_id for item in items] == [
        "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    ]
    assert "secret.md" not in caplog.text
    assert str(outside) not in caplog.text


def test_list_documents_derives_user_scope_from_session_and_keeps_missing_size_null(
    service_fixture,
):
    missing = service_fixture.paths.documents / "missing.md"
    service_fixture.history.documents = [
        _record(
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            missing,
            loaded_at=None,
        )
    ]

    item = service_fixture.service.list_documents("cookie-token")[0]
    assert item.size_bytes is None
    assert item.loaded_at is None

    service_fixture.registry.session.user_id = (
        "22222222-2222-4222-8222-222222222222"
    )
    service_fixture.storage.ensure_user_dirs(
        service_fixture.registry.session.user_id
    )
    assert service_fixture.service.list_documents("cookie-token") == ()


def test_list_documents_sorts_valid_offsets_by_utc_and_nulls_malformed_timestamp(
    service_fixture,
    caplog,
):
    early = service_fixture.paths.documents / "early.md"
    later = service_fixture.paths.documents / "later.md"
    malformed = service_fixture.paths.documents / "malformed.md"
    for path in (early, later, malformed):
        path.write_text(path.stem, encoding="utf-8")
    service_fixture.history.documents = [
        _record(
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            early,
            loaded_at="2026-08-15T10:00:00+08:00",
        ),
        _record(
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            later,
            loaded_at="2026-08-15T03:00:00Z",
        ),
        _record(
            "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            malformed,
            loaded_at="not-a-timestamp",
        ),
    ]

    items = service_fixture.service.list_documents("cookie-token")

    assert [item.document_id for item in items] == [
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    ]
    assert items[0].loaded_at == "2026-08-15T03:00:00Z"
    assert items[1].loaded_at == "2026-08-15T10:00:00+08:00"
    assert items[2].loaded_at is None
    assert "not-a-timestamp" not in caplog.text


def test_list_documents_skips_reparse_source(service_fixture, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    linked = service_fixture.paths.documents / "linked"
    if os.name == "nt":
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(linked), str(outside)],
            capture_output=True,
            check=False,
        )
        assert created.returncode == 0, created.stderr.decode(errors="replace")
    else:  # pragma: no cover - Windows is the mandated project runtime
        os.symlink(outside, linked, target_is_directory=True)
    service_fixture.history.documents = [
        _record(
            "ffffffff-ffff-4fff-8fff-ffffffffffff",
            linked / "secret.md",
        )
    ]

    assert service_fixture.service.list_documents("cookie-token") == ()


def test_delete_document_blocks_only_active_exact_document(service_fixture):
    target = service_fixture.paths.documents / "target.md"
    target.write_text("target", encoding="utf-8")
    document_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    service_fixture.history.documents = [_record(document_id, target)]
    service_fixture.imports.active_document_id = document_id

    with pytest.raises(DocumentImportActiveError):
        service_fixture.service.delete_document("cookie-token", document_id)

    service_fixture.assistant.delete_document.assert_not_called()
    assert service_fixture.registry.clear_calls == []


def test_delete_document_coordinates_then_invalidates_exact_same_user_selection(
    service_fixture,
):
    target = service_fixture.paths.documents / "target.md"
    target.write_text("target", encoding="utf-8")
    document_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    service_fixture.history.documents = [_record(document_id, target)]

    service_fixture.service.delete_document("cookie-token", document_id)

    service_fixture.assistant.delete_document.assert_called_once_with(document_id)
    assert service_fixture.registry.clear_calls == [
        (service_fixture.user_id, document_id)
    ]


def test_delete_preflights_every_duplicate_source_before_assistant_or_clear(
    service_fixture,
    tmp_path,
):
    document_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    escaping_old = tmp_path / "outside" / "old.md"
    escaping_old.parent.mkdir()
    escaping_old.write_text("outside", encoding="utf-8")
    safe_latest = service_fixture.paths.documents / "latest.md"
    safe_latest.write_text("safe", encoding="utf-8")
    service_fixture.history.documents = [
        _record(document_id, escaping_old),
        _record(document_id, safe_latest),
    ]

    with pytest.raises(DocumentDeleteFailedError):
        service_fixture.service.delete_document("cookie-token", document_id)

    service_fixture.assistant.delete_document.assert_not_called()
    assert service_fixture.registry.clear_calls == []


def test_delete_rejects_structured_partial_result_without_clearing_selection(
    service_fixture,
):
    target = service_fixture.paths.documents / "target.md"
    target.write_text("target", encoding="utf-8")
    document_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    service_fixture.history.documents = [_record(document_id, target)]
    service_fixture.assistant.delete_document.side_effect = None
    service_fixture.assistant.delete_document.return_value = DocumentDeleteResult(
        document_id=document_id,
        rag_message="partial",
        documents_removed=1,
        questions_removed=0,
        skipped_source_files=1,
    )

    with pytest.raises(DocumentDeleteFailedError):
        service_fixture.service.delete_document("cookie-token", document_id)

    assert service_fixture.registry.clear_calls == []


@pytest.mark.parametrize(
    ("returned_id", "documents_removed"),
    [
        ("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", 1),
        ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", 0),
    ],
)
def test_delete_rejects_mismatched_or_zero_removal_result(
    service_fixture,
    returned_id,
    documents_removed,
):
    target = service_fixture.paths.documents / "target.md"
    target.write_text("target", encoding="utf-8")
    document_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    service_fixture.history.documents = [_record(document_id, target)]
    service_fixture.assistant.delete_document.side_effect = None
    service_fixture.assistant.delete_document.return_value = DocumentDeleteResult(
        document_id=returned_id,
        rag_message="deleted",
        documents_removed=documents_removed,
        questions_removed=0,
    )

    with pytest.raises(DocumentDeleteFailedError):
        service_fixture.service.delete_document("cookie-token", document_id)

    assert service_fixture.registry.clear_calls == []


@pytest.mark.parametrize("records", [[], [{"document_id": "other-document"}]])
def test_delete_missing_and_unowned_document_are_same_not_found(
    service_fixture,
    records,
):
    service_fixture.history.documents = records

    with pytest.raises(DocumentNotFoundError) as exc_info:
        service_fixture.service.delete_document(
            "cookie-token", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        )

    assert str(exc_info.value) == "document was not found"
    service_fixture.assistant.delete_document.assert_not_called()
    assert service_fixture.registry.clear_calls == []


def test_delete_failure_is_typed_safe_and_does_not_clear_selection(
    service_fixture,
):
    target = service_fixture.paths.documents / "target.md"
    target.write_text("target", encoding="utf-8")
    document_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    service_fixture.history.documents = [_record(document_id, target)]
    service_fixture.assistant.delete_document.side_effect = RuntimeError(
        f"secret path {target}"
    )

    with pytest.raises(DocumentDeleteFailedError) as exc_info:
        service_fixture.service.delete_document("cookie-token", document_id)

    assert str(target) not in str(exc_info.value)
    assert "secret" not in str(exc_info.value)
    assert service_fixture.registry.clear_calls == []


def test_selection_invalidation_failure_is_safe_after_coordinated_delete(
    service_fixture,
):
    target = service_fixture.paths.documents / "target.md"
    target.write_text("target", encoding="utf-8")
    document_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    service_fixture.history.documents = [_record(document_id, target)]
    service_fixture.registry.clear_document_selection = Mock(
        side_effect=RuntimeError(f"secret {target}")
    )

    with pytest.raises(DocumentDeleteFailedError) as exc_info:
        service_fixture.service.delete_document("cookie-token", document_id)

    service_fixture.assistant.delete_document.assert_called_once_with(document_id)
    assert "secret" not in str(exc_info.value)
    assert str(target) not in str(exc_info.value)


def test_structured_assistant_delete_removes_rag_history_questions_and_source(
    tmp_path,
):
    documents = tmp_path / "documents"
    documents.mkdir()
    source = documents / "doc.md"
    source.write_text("content", encoding="utf-8")
    history = HistoryRepository(tmp_path / "history.json")
    history.save(
        {
            "documents": [
                {
                    "document_id": "doc-1",
                    "document_path": str(source),
                }
            ],
            "questions": [
                {"document_id": "doc-1"},
                {"document_ids": ["doc-1", "doc-2"]},
                {"document_id": "doc-2"},
            ],
            "notes": [],
            "sessions": [],
        }
    )
    assistant = object.__new__(PDFLearningAssistant)
    assistant.user_id = "user-1"
    assistant._lock = __import__("threading").RLock()
    assistant.runtime = None
    assistant.history_repository = history
    assistant.coordinator = SimpleNamespace(
        delete_document=history.delete_document,
        load_history=history.load,
        safe_unlink=lambda path: path.unlink(),
    )
    assistant.rag_tool = SimpleNamespace(
        execute=Mock(return_value="deleted from rag")
    )
    assistant.history = history.load()

    result = assistant.delete_document("doc-1")

    assert result.document_id == "doc-1"
    assert result.documents_removed == 1
    assert result.questions_removed == 2
    assistant.rag_tool.execute.assert_called_once_with(
        "delete_document", document_id="doc-1"
    )
    assert history.load()["documents"] == []
    assert history.load()["questions"] == [{"document_id": "doc-2"}]
    assert not source.exists()


def test_session_registry_clears_only_exact_same_user_document_selection():
    registry = object.__new__(SessionRegistry)
    registry._lock = __import__("threading").RLock()
    same_target = SimpleNamespace(
        user_id="user-a",
        assistant=SimpleNamespace(
            current_document_id="doc-1", current_document="/safe/doc-1.md"
        ),
    )
    same_other = SimpleNamespace(
        user_id="user-a",
        assistant=SimpleNamespace(
            current_document_id="doc-2", current_document="/safe/doc-2.md"
        ),
    )
    other_target = SimpleNamespace(
        user_id="user-b",
        assistant=SimpleNamespace(
            current_document_id="doc-1", current_document="/other/doc-1.md"
        ),
    )
    registry._sessions = {
        "one": same_target,
        "two": same_other,
        "three": other_target,
    }

    cleared = registry.clear_document_selection("user-a", "doc-1")

    assert cleared == 1
    assert (same_target.assistant.current_document_id, same_target.assistant.current_document) == (
        None,
        None,
    )
    assert same_other.assistant.current_document_id == "doc-2"
    assert other_target.assistant.current_document_id == "doc-1"
