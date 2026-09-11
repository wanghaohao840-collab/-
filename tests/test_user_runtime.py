from app.database import initialize_database
from app.runtime import UserRuntimeRegistry
from app.storage import UserStorage


def test_product_runtime_does_not_connect_optional_graph(tmp_path, monkeypatch):
    from hello_agents.tools.builtin.rag_tool import RAGTool

    monkeypatch.setenv("NEO4J_URI", "neo4j://unavailable.invalid:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "test-only-password")
    attempts = []
    monkeypatch.setattr(RAGTool, "_configure_graph_service", lambda *a, **kw: attempts.append(kw))
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    runtime = UserRuntimeRegistry(db_path=db_path, storage=UserStorage(tmp_path / "data")).get_or_create("user-1")

    assert attempts == []
    assert runtime.rag_tool.graph_service is None


def test_user_runtime_registry_reuses_runtime_per_user(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    registry = UserRuntimeRegistry(db_path=db_path, storage=UserStorage(tmp_path / "data"))

    first = registry.get_or_create("user-1")
    second = registry.get_or_create("user-1")
    other = registry.get_or_create("user-2")

    assert first is second
    assert first is not other
    assert first.lock is second.lock
    assert first.paths.history == tmp_path / "data" / "users" / "user-1" / "history.json"
    assert first.active_session_count == 0
    assert first.active_background_count == 0
    assert first.rag_tool.data_root == (tmp_path / "data").resolve()


def test_runtime_lock_guards_history_writes(tmp_path):
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    runtime = UserRuntimeRegistry(db_path=db_path, storage=UserStorage(tmp_path / "data")).get_or_create("user-1")

    with runtime.lock:
        runtime.history.add_note({"note": "inside lock", "concept": "runtime"})

    assert runtime.history.load()["notes"][0]["note"] == "inside lock"
