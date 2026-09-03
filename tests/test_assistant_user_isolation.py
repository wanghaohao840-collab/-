from app.bootstrap import ApplicationServices
from app.note_models import NoteFilters


def test_assistant_uses_explicit_user_runtime_files(tmp_path):
    services = ApplicationServices.create(tmp_path / "data")
    alice_token = services.session_registry.register("Alice", "correct horse battery")
    bob_token = services.session_registry.register("Bob", "correct horse battery")
    alice = services.session_registry.get_session(alice_token)
    bob = services.session_registry.get_session(bob_token)

    try:
        assert alice.assistant.rag_tool.cache_path == str(alice.runtime.paths.root / "rag" / "rag_cache.json")
        assert bob.assistant.rag_tool.cache_path == str(bob.runtime.paths.root / "rag" / "rag_cache.json")
        assert alice.assistant.history_path == alice.runtime.paths.history
        assert bob.assistant.history_path == bob.runtime.paths.history

        alice.assistant.add_note("alice-only note", concept="scope")
        bob.assistant.add_note("bob-only note", concept="scope")

        alice_notes = services.note_service.list_for_user(alice.user_id, NoteFilters()).items
        bob_notes = services.note_service.list_for_user(bob.user_id, NoteFilters()).items
        assert [note.body_markdown for note in alice_notes] == ["alice-only note"]
        assert [note.body_markdown for note in bob_notes] == ["bob-only note"]
        assert not alice.runtime.paths.history.exists()
        assert not bob.runtime.paths.history.exists()
    finally:
        services.session_registry.logout(alice_token)
        services.session_registry.logout(bob_token)
        services.stop()
