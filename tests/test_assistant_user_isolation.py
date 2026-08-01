from assistants.pdf_learning_assistant import PDFLearningAssistant


def test_assistant_uses_explicit_user_runtime_files(tmp_path):
    alice_root = tmp_path / "data" / "users" / "alice"
    bob_root = tmp_path / "data" / "users" / "bob"

    alice = PDFLearningAssistant(user_id="alice", runtime_dir=alice_root)
    bob = PDFLearningAssistant(user_id="bob", runtime_dir=bob_root)

    assert alice.rag_tool.cache_path == str(alice_root / "rag" / "rag_cache.json")
    assert bob.rag_tool.cache_path == str(bob_root / "rag" / "rag_cache.json")
    assert alice.history_path == alice_root / "history.json"
    assert bob.history_path == bob_root / "history.json"

    alice.add_note("alice-only note", concept="scope")
    bob.add_note("bob-only note", concept="scope")

    assert "alice-only note" in alice.history_path.read_text(encoding="utf-8")
    assert "bob-only note" not in alice.history_path.read_text(encoding="utf-8")
    assert "bob-only note" in bob.history_path.read_text(encoding="utf-8")
