from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.auth import AuthService
from threading import RLock

from app.coordination import UserMutationCoordinator
from app.database import initialize_database
from app.history import EMPTY_HISTORY, HistoryRepository
from app.reports import ReportService
from app.storage import UserStorage
from assistants.pdf_learning_assistant import PDFLearningAssistant


class FakeTool:
    def execute(self, action, **kwargs):
        return f"{action}-ok"


def make_runtime(tmp_path, user_id="user-1", with_reports=False):
    storage = UserStorage(tmp_path / "data")
    paths = storage.ensure_user_dirs(user_id)
    from threading import RLock

    reports = None
    if with_reports:
        db_path = tmp_path / "app.db"
        initialize_database(db_path)
        user_id = AuthService(db_path).register("Alice", "correct horse battery").id
        paths = storage.ensure_user_dirs(user_id)
        reports = ReportService(db_path, storage)
    lock = RLock()
    history_repo = HistoryRepository(paths.history)
    history_repo.save({
        "documents": [],
        "questions": [],
        "notes": [],
        "sessions": [],
    })
    coordinator = UserMutationCoordinator(
        user_id=user_id,
        lock=lock,
        history=history_repo,
        document_root=paths.documents,
    )
    return SimpleNamespace(
        paths=paths,
        lock=lock,
        coordinator=coordinator,
        rag_tool=FakeTool(),
        memory_tool=FakeTool(),
        history=history_repo,
        reports=reports,
    ), user_id


def test_two_sessions_merge_concurrent_notes(tmp_path):
    runtime, user_id = make_runtime(tmp_path)
    first = PDFLearningAssistant(user_id=user_id, runtime=runtime)
    second = PDFLearningAssistant(user_id=user_id, runtime=runtime)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda pair: pair[0].add_note(pair[1]), [
            (first, "first-note"),
            (second, "second-note"),
        ]))

    notes = runtime.history.load()["notes"]
    assert {item["note"] for item in notes} == {"first-note", "second-note"}


def test_delete_and_clear_remove_original_uploads(tmp_path):
    runtime, user_id = make_runtime(tmp_path)
    assistant = PDFLearningAssistant(user_id=user_id, runtime=runtime)
    first = runtime.paths.documents / "doc-1.md"
    second = runtime.paths.documents / "doc-2.md"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    runtime.history.save({
        "documents": [
            {"document_id": "doc-1", "document_path": str(first)},
            {"document_id": "doc-2", "document_path": str(second)},
        ],
        "questions": [{"document_ids": ["doc-1"]}, {"document_ids": ["doc-2"]}],
        "notes": [{"note": "keep"}],
        "sessions": [],
    })

    assistant.current_document_id = "doc-1"
    assistant.current_document = str(first)
    assistant.delete_current_document()
    assert not first.exists()
    assert second.exists()

    assistant.clear_all_documents()
    assert not second.exists()
    assert runtime.history.load()["notes"] == [{"note": "keep"}]


def test_word_export_uses_selected_immutable_snapshot(tmp_path):
    runtime, user_id = make_runtime(tmp_path, with_reports=True)
    assistant = PDFLearningAssistant(user_id=user_id, runtime=runtime)
    assistant.add_note("snapshot-note")
    markdown_path = Path(assistant.export_report_markdown())
    report_id = markdown_path.stem
    assistant.add_note("later-note")

    docx_path = Path(assistant.export_report_docx(report_id))
    from docx import Document

    text = "\n".join(paragraph.text for paragraph in Document(docx_path).paragraphs)
    assert "snapshot-note" in text
    assert "later-note" not in text


# ── packet 04 additions: cross-user access denial ───────────────────────


def test_cross_user_cannot_read_others_report(tmp_path):
    """User A's report ID must raise FileNotFoundError for user B."""
    runtime_a, user_a = make_runtime(tmp_path / "a", user_id="alice",
                                      with_reports=True)
    assistant_a = PDFLearningAssistant(user_id=user_a, runtime=runtime_a)
    report_path = Path(assistant_a.export_report_markdown())
    report_id = report_path.stem

    # User B tries to read user A's report — must fail.
    with pytest.raises(FileNotFoundError):
        runtime_a.reports.read_report("bob", report_id)


def test_cross_user_document_not_accessible_via_direct_path(tmp_path):
    """User storage paths validate caller identity.  A path under user A's
    root must not be accessible through user B's storage methods."""
    storage = UserStorage(tmp_path / "data")
    paths_a = storage.ensure_user_dirs("alice")
    (paths_a.documents / "a-file.pdf").write_text("alice content",
                                                   encoding="utf-8")

    from app.storage import UnsafePathError
    with pytest.raises(UnsafePathError):
        storage.assert_within_user("bob", paths_a.documents / "a-file.pdf")


def test_clear_all_preserves_other_scoped_files(tmp_path):
    """clear_all_documents for one user must not touch another user's
    uploads."""
    storage = UserStorage(tmp_path / "data")
    paths_a = storage.ensure_user_dirs("alice")
    paths_b = storage.ensure_user_dirs("bob")

    (paths_a.documents / "a.pdf").write_text("A", encoding="utf-8")
    (paths_b.documents / "b.pdf").write_text("B", encoding="utf-8")

    repo_a = HistoryRepository(paths_a.history)
    from copy import deepcopy
    repo_a.save(deepcopy(EMPTY_HISTORY))
    repo_a.add_document({"document_id": "doc-a",
                         "document_path": str(paths_a.documents / "a.pdf")})

    coord_a = UserMutationCoordinator(
        user_id="alice", lock=RLock(), history=repo_a,
        document_root=paths_a.documents,
    )
    assistant_a = PDFLearningAssistant(user_id="alice", runtime_dir=paths_a.root)
    assistant_a.coordinator = coord_a
    assistant_a.history_repository = repo_a
    assistant_a.current_document_id = "doc-a"
    assistant_a.clear_all_documents()

    # Bob's file must still exist.
    assert (paths_b.documents / "b.pdf").exists(), (
        "Bob's upload was deleted by Alice's clear_all_documents"
    )
