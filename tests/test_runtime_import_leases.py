from app.database import initialize_database
from app.runtime import UserRuntimeRegistry
from app.session import SessionRegistry
from app.storage import UserStorage


def make_registry(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    return UserRuntimeRegistry(db_path, UserStorage(tmp_path / "data"))


def test_runtime_stays_open_until_background_lease_is_released(tmp_path):
    registry = make_registry(tmp_path)
    runtime = registry.acquire_session("user-a")
    registry.acquire_background("user-a")

    registry.release_session("user-a")

    assert registry.get_or_create("user-a") is runtime
    assert runtime.active_session_count == 0
    assert runtime.active_background_count == 1

    registry.release_background("user-a")

    assert registry.has_runtime("user-a") is False


def test_logout_does_not_close_runtime_used_by_import_worker(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    sessions = SessionRegistry(db_path, UserStorage(tmp_path / "data"))
    token = sessions.register("UserA", "correct horse battery")
    user_id = sessions.get_session(token).user_id
    runtime = sessions.runtime_registry.get_or_create(user_id)
    sessions.runtime_registry.acquire_background(user_id)

    sessions.logout(token)

    assert sessions.runtime_registry.get_or_create(user_id) is runtime
    assert runtime.active_session_count == 0
    assert runtime.active_background_count == 1

    sessions.runtime_registry.release_background(user_id)

    assert sessions.runtime_registry.has_runtime(user_id) is False


def test_releases_do_not_make_lease_counts_negative(tmp_path):
    registry = make_registry(tmp_path)
    runtime = registry.acquire_session("user-a")
    registry.acquire_background("user-a")

    registry.release_session("user-a")
    registry.release_session("user-a")
    registry.release_background("user-a")
    registry.release_background("user-a")

    assert runtime.active_session_count == 0
    assert runtime.active_background_count == 0
    assert registry.has_runtime("user-a") is False


def test_two_sessions_release_the_runtime_only_after_both_logout(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    sessions = SessionRegistry(db_path, UserStorage(tmp_path / "data"))
    first_token = sessions.register("UserA", "correct horse battery")
    second_token = sessions.login("usera", "correct horse battery")
    runtime = sessions.get_session(first_token).runtime

    sessions.logout(first_token)

    assert sessions.runtime_registry.has_runtime(runtime.user_id)
    assert runtime.active_session_count == 1

    sessions.logout(second_token)

    assert sessions.runtime_registry.has_runtime(runtime.user_id) is False
