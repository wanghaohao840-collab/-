import pytest
from datetime import timedelta

from app.database import initialize_database
from app.session import InvalidCsrfTokenError, InvalidSessionError, SessionRegistry
from app.storage import UserStorage


def test_register_login_creates_token_and_user_scoped_assistant(tmp_path):
    db_path = tmp_path / "app.db"
    data_root = tmp_path / "data"
    initialize_database(db_path)
    registry = SessionRegistry(db_path=db_path, storage=UserStorage(data_root))

    token = registry.register("Alice", "correct horse battery")
    session = registry.get_session(token)

    assert token
    assert session.username == "Alice"
    assert session.assistant.user_id == session.user_id
    assert session.assistant.rag_tool.cache_path == str(
        data_root / "users" / session.user_id / "rag" / "rag_cache.json"
    )
    assert session.assistant.history_path == data_root / "users" / session.user_id / "history.json"


def test_two_users_get_isolated_runtime_paths(tmp_path):
    db_path = tmp_path / "app.db"
    data_root = tmp_path / "data"
    initialize_database(db_path)
    registry = SessionRegistry(db_path=db_path, storage=UserStorage(data_root))

    alice_token = registry.register("Alice", "correct horse battery")
    bob_token = registry.register("Bob", "correct horse battery")

    alice = registry.get_session(alice_token)
    bob = registry.get_session(bob_token)

    assert alice.user_id != bob.user_id
    assert alice.assistant.rag_tool.cache_path != bob.assistant.rag_tool.cache_path
    assert alice.assistant.history_path != bob.assistant.history_path
    assert str(alice.assistant.history_path).startswith(str(data_root / "users" / alice.user_id))
    assert str(bob.assistant.history_path).startswith(str(data_root / "users" / bob.user_id))


def test_logout_invalidates_session(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    registry = SessionRegistry(db_path=db_path, storage=UserStorage(tmp_path / "data"))
    token = registry.register("Alice", "correct horse battery")

    registry.logout(token)

    with pytest.raises(InvalidSessionError):
        registry.get_session(token)


def test_created_session_has_distinct_secure_csrf_token(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    registry = SessionRegistry(db_path=db_path, storage=UserStorage(tmp_path / "data"))

    token = registry.register("Alice", "correct horse battery")
    session = registry.get_session(token)

    assert session.csrf_token
    assert session.csrf_token != token
    assert len(session.csrf_token) >= 32


def test_validate_csrf_returns_current_session_and_rejects_invalid_values(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    registry = SessionRegistry(db_path=db_path, storage=UserStorage(tmp_path / "data"))
    token = registry.register("Alice", "correct horse battery")
    session = registry.get_session(token)

    assert registry.validate_csrf(token, session.csrf_token) is session

    for csrf_token in (None, "", "wrong-token"):
        with pytest.raises(InvalidCsrfTokenError, match="Invalid CSRF token"):
            registry.validate_csrf(token, csrf_token)


def test_validate_csrf_rejects_another_session_token_and_logged_out_session(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    registry = SessionRegistry(db_path=db_path, storage=UserStorage(tmp_path / "data"))
    alice_token = registry.register("Alice", "correct horse battery")
    bob_token = registry.register("Bob", "correct horse battery")
    alice = registry.get_session(alice_token)
    bob = registry.get_session(bob_token)

    with pytest.raises(InvalidCsrfTokenError, match="Invalid CSRF token"):
        registry.validate_csrf(alice_token, bob.csrf_token)

    registry.logout(alice_token)

    with pytest.raises(InvalidSessionError):
        registry.get_session(alice_token)
    with pytest.raises(InvalidSessionError):
        registry.validate_csrf(alice_token, alice.csrf_token)


def test_same_user_sessions_share_one_runtime_but_keep_separate_assistants(tmp_path):
    db_path = tmp_path / "app.db"
    data_root = tmp_path / "data"
    initialize_database(db_path)
    registry = SessionRegistry(db_path=db_path, storage=UserStorage(data_root))

    first_token = registry.register("Alice", "correct horse battery")
    second_token = registry.login("alice", "correct horse battery")

    first = registry.get_session(first_token)
    second = registry.get_session(second_token)

    assert first.assistant is not second.assistant
    assert first.runtime is second.runtime
    assert first.assistant.runtime is first.runtime
    assert second.assistant.runtime is first.runtime
    assert first.runtime.active_session_count == 2

    registry.logout(first_token)

    assert first.runtime.active_session_count == 1
    assert registry.runtime_registry.has_runtime(first.user_id)

    registry.logout(second_token)

    assert registry.runtime_registry.has_runtime(first.user_id) is False


def test_session_expiration_and_unknown_token_are_rejected(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    registry = SessionRegistry(
        db_path=db_path,
        storage=UserStorage(tmp_path / "data"),
        idle_timeout=timedelta(seconds=0),
    )
    token = registry.register("Alice", "correct horse battery")

    with pytest.raises(InvalidSessionError):
        registry.get_session(token)

    with pytest.raises(InvalidSessionError):
        registry.get_session("forged-token")

    assert registry.runtime_registry.has_runtime(registry.auth.authenticate("alice", "correct horse battery").id) is False


def test_session_limit_rejects_new_login_without_evicting_active_sessions(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    registry = SessionRegistry(
        db_path=db_path,
        storage=UserStorage(tmp_path / "data"),
        max_sessions=1,
    )
    token = registry.register("Alice", "correct horse battery")

    with pytest.raises(InvalidSessionError, match="Too many active sessions"):
        registry.login("alice", "correct horse battery")

    assert registry.get_session(token).username == "Alice"
