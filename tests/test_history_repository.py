import pytest

from app.history import CorruptHistoryError, HistoryRepository
from assistants.pdf_learning_assistant import PDFLearningAssistant


def test_history_repository_persists_documents_questions_and_notes(tmp_path):
    repo = HistoryRepository(tmp_path / "history.json")

    repo.add_document({"document_id": "doc-1", "document_name": "A.pdf"})
    repo.add_question({"question": "Q", "answer": "A", "document_id": "doc-1"})
    repo.add_note({"note": "N", "concept": "C"})

    loaded = repo.load()
    assert loaded["documents"][0]["document_id"] == "doc-1"
    assert loaded["questions"][0]["question"] == "Q"
    assert loaded["notes"][0]["note"] == "N"


def test_history_repository_delete_semantics_are_structured(tmp_path):
    repo = HistoryRepository(tmp_path / "history.json")
    repo.add_document({"document_id": "doc-1", "document_name": "A.pdf"})
    repo.add_document({"document_id": "doc-2", "document_name": "B.pdf"})
    repo.add_question({"question": "Q1", "document_id": "doc-1"})
    repo.add_question({"question": "Q2", "document_ids": ["doc-2"]})
    repo.add_note({"note": "keep"})

    removed_docs, removed_questions = repo.delete_document("doc-1")

    loaded = repo.load()
    assert (removed_docs, removed_questions) == (1, 1)
    assert [item["document_id"] for item in loaded["documents"]] == ["doc-2"]
    assert [item["question"] for item in loaded["questions"]] == ["Q2"]
    assert loaded["notes"] == [{"note": "keep"}]


def test_history_repository_blocks_writes_after_corrupt_json(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("{not-json", encoding="utf-8")
    repo = HistoryRepository(path)

    with pytest.raises(CorruptHistoryError):
        repo.load()
    with pytest.raises(CorruptHistoryError):
        repo.add_note({"note": "must not overwrite"})
    assert path.read_text(encoding="utf-8") == "{not-json"


def test_assistant_uses_fail_closed_history_repository(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("{not-json", encoding="utf-8")
    assistant = PDFLearningAssistant.__new__(PDFLearningAssistant)
    assistant.history_path = path
    assistant.history_repository = HistoryRepository(path)
    assistant.coordinator = None

    with pytest.raises(CorruptHistoryError):
        assistant._load_history()

    assert path.read_text(encoding="utf-8") == "{not-json"


def test_validate_schema_accepts_valid_history(tmp_path):
    from copy import deepcopy
    from app.history import EMPTY_HISTORY
    valid = deepcopy(EMPTY_HISTORY)
    valid["notes"].append({"note": "ok"})
    # Must not raise.
    HistoryRepository.validate_schema(valid)


def test_validate_schema_rejects_non_dict():
    with pytest.raises(CorruptHistoryError, match="must be a dict"):
        HistoryRepository.validate_schema("not-a-dict")


def test_validate_schema_rejects_missing_field():
    with pytest.raises(CorruptHistoryError, match="not a list"):
        HistoryRepository.validate_schema({"documents": [], "questions": []})


def test_restore_validates_backup_before_writing(tmp_path):
    """Invalid backup must leave the active file unchanged."""
    active = HistoryRepository(tmp_path / "history.json")
    active.add_note({"note": "original"})

    backup_path = tmp_path / "bad_backup.json"
    backup_path.write_text('{"documents": "not-a-list"}', encoding="utf-8")

    with pytest.raises(CorruptHistoryError):
        active.restore(backup_path)

    # Active file still has original data.
    loaded = active.load()
    assert loaded["notes"] == [{"note": "original"}]


# ── Finding 5 & 6: collision-resistant IDs + failure-atomic quarantine ──


def test_quarantine_ids_are_collision_resistant(tmp_path):
    """Two immediate quarantines must produce two distinct backup IDs."""
    from app.storage import write_json_atomic
    repo = HistoryRepository(tmp_path / "history.json")
    repo.add_note({"note": "first"})

    b1 = repo.quarantine_and_reset()
    # Re-seed.
    write_json_atomic(repo.path, {"documents": [], "questions": [],
                                   "notes": [{"note": "second"}],
                                   "sessions": []})
    b2 = repo.quarantine_and_reset()

    assert b1.name != b2.name
    assert b1.exists()
    assert b2.exists()
    for b in (b1, b2):
        assert "corrupt-" in b.name
        parts = b.name.rsplit("-", 1)
        assert len(parts) == 2
        assert len(parts[1]) >= 8  # UUID portion


def test_quarantine_is_failure_atomic(tmp_path, monkeypatch):
    """If the clean write fails, the active file is left untouched."""
    from app.storage import write_json_atomic
    repo = HistoryRepository(tmp_path / "history.json")
    repo.add_note({"note": "original"})
    original_bytes = repo.path.read_bytes()

    # Force save (which calls write_json_atomic) to fail.
    import app.history as mod
    real_write = mod.write_json_atomic

    def _failing_write(path, data):
        raise OSError("simulated disk full")

    monkeypatch.setattr(mod, "write_json_atomic", _failing_write)

    with pytest.raises(OSError, match="simulated disk full"):
        repo.quarantine_and_reset()

    # Active file untouched.
    assert repo.path.read_bytes() == original_bytes


def test_quarantine_cleanup_does_not_leave_stranded_backup(tmp_path, monkeypatch):
    """When quarantine fails, the staged backup is removed."""
    repo = HistoryRepository(tmp_path / "history.json")
    repo.add_note({"note": "original"})

    import app.history as mod

    def _failing_write(path, data):
        raise OSError("simulated disk full")

    monkeypatch.setattr(mod, "write_json_atomic", _failing_write)

    with pytest.raises(OSError):
        repo.quarantine_and_reset()

    # No .corrupt-* backup file left behind.
    siblings = list(repo.path.parent.glob(repo.path.name + ".corrupt-*"))
    assert len(siblings) == 0
