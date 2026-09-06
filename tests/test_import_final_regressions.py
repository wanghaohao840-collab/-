"""Concrete offline regressions for the final integration review."""
from pathlib import Path
from threading import Event, RLock, Thread
from types import SimpleNamespace
import os
import uuid

import pytest

from app.auth import AuthService
from app.database import connect, initialize_database
from app.history import HistoryRepository
from app.import_maintenance import ImportHistoryMaintenance
from app.import_models import ImportTaskCreate
from app.import_repository import ImportTaskRepository, InvalidImportTransition
from app.import_service import ImportTaskService
from app.import_worker import ImportTaskRunner
from app.memory_repository import MemorySnapshotRepository
from app.storage import UserStorage
from assistants.pdf_learning_assistant import PDFLearningAssistant
from hello_agents.memory.base import MemoryConfig
from hello_agents.memory.rag.pipeline import SimpleRAGPipeline
from hello_agents.tools.builtin.memory_tool import MemoryTool
from hello_agents.tools.builtin.rag_tool import RAGTool


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "app.db"
    initialize_database(db)
    user = AuthService(db).register("final-fixes", "correct horse battery").id
    repo = ImportTaskRepository(db)
    storage = UserStorage(tmp_path / "data")
    paths = storage.ensure_user_dirs(user)
    runtime = SimpleNamespace(
        paths=paths, lock=RLock(), import_control_lock=RLock(),
        history=HistoryRepository(paths.history),
        memory_tool=SimpleNamespace(remove_import_event=lambda task: None),
        rag_tool=None,
    )
    session = SimpleNamespace(user_id=user, runtime=runtime)
    service = ImportTaskService(
        SimpleNamespace(get_session=lambda token: session), repo, storage,
        SimpleNamespace(notify=lambda: None),
    )
    runtime.import_task_service = service
    registry = SimpleNamespace(
        acquire_background=lambda user_id: runtime,
        release_background=lambda user_id: None,
    )
    return SimpleNamespace(repo=repo, user=user, storage=storage, runtime=runtime,
                           service=service, runner=ImportTaskRunner(repo, registry, storage))


def create(env, count=1):
    batch = str(uuid.uuid4())
    tasks = []
    for _ in range(count):
        task = str(uuid.uuid4())
        path = env.storage.staged_import_path(env.user, batch, task, ".md")
        path.write_text("offline input", encoding="utf-8")
        tasks.append(ImportTaskCreate(
            task_id=task, batch_id=batch, user_id=env.user,
            document_id=str(uuid.uuid4()), original_name="notes.md",
            file_suffix=".md", size_bytes=13,
            staged_relative_path=str(path.relative_to(env.runtime.paths.root)),
        ))
    return env.repo.create_batch(env.user, tasks)


@pytest.mark.parametrize("method", ["pause_task", "cancel_task", "pause_batch", "cancel_batch"])
def test_real_assistant_controls_commit_before_rag_barrier_release(env, method):
    batch = create(env, 2)
    task = env.repo.claim_next(set())
    entered, release, returned = Event(), Event(), Event()
    documents = set()
    errors = []

    class OfflineRAG:
        def _get_pipeline(self):
            return SimpleNamespace(list_document_ids=lambda: list(documents))

        def execute_result(self, action, **kwargs):
            assert action == "add_document"
            documents.add(kwargs["document_id"])
            entered.set()
            assert release.wait(10)
            kwargs["control_checkpoint"]("persisting")
            return SimpleNamespace(success=True, message="ok")

        def execute(self, action, **kwargs):
            assert action == "delete_document"
            documents.discard(kwargs["document_id"])
            return "ok"

    env.runtime.rag_tool = OfflineRAG()
    worker = Thread(target=env.runner.run, args=(task,))
    worker.start()
    assert entered.wait(5)

    def request():
        try:
            getattr(env.service, method)("token", batch.batch_id if method.endswith("batch") else task.task_id)
        except Exception as error:
            errors.append(error)
        finally:
            returned.set()

    control = Thread(target=request)
    control.start()
    try:
        assert returned.wait(2), "request blocked behind real Assistant RAG write lock"
        assert errors == []
        assert env.repo.get_task(env.user, task.task_id).status == method.split("_")[0] + "_requested"
        sibling = next(item for item in batch.tasks if item.task_id != task.task_id)
        expected = ("paused" if method == "pause_batch" else "cancel_requested") if method.endswith("batch") else "queued"
        assert env.repo.get_task(env.user, sibling.task_id).status == expected
    finally:
        release.set()
        worker.join(5)
        control.join(5)
    assert not worker.is_alive() and not control.is_alive()
    assert env.repo.get_task(env.user, task.task_id).status == ("paused" if method.startswith("pause") else "cancelled")
    assert documents == set()
    assert not env.storage.document_path(env.user, task.document_id, ".md").exists()


@pytest.mark.parametrize("operation", ["clear_all_documents", "delete_current_document"])
def test_cleanup_failure_cancel_cannot_cross_destructive_guard(env, monkeypatch, operation):
    task = create(env).tasks[0]
    env.repo.request_cancel(env.user, task.task_id)
    env.repo.mark_control_failed(env.user, task.task_id, "cancel_cleanup_failed", "failed")
    assistant = PDFLearningAssistant(user_id=env.user, runtime=env.runtime)
    assistant.current_document_id = task.document_id
    entered, release, returned = Event(), Event(), Event()
    failures = []

    def mutation(*args):
        entered.set()
        assert release.wait(10)
        assert env.repo.get_task(env.user, task.task_id).status == "failed"
        return "ok"

    monkeypatch.setattr(assistant, "_clear_documents_coordinated", mutation)
    monkeypatch.setattr(assistant, "_delete_document_coordinated", mutation)
    def destroy():
        try:
            getattr(assistant, operation)()
        except Exception as error:
            failures.append(error)
    worker = Thread(target=destroy)
    worker.start()
    assert entered.wait(5)
    control = Thread(target=lambda: (env.service.cancel_task("token", task.task_id), returned.set()))
    control.start()
    try:
        assert not returned.wait(.2), "cancel entered after destructive guard but before mutation"
    finally:
        release.set()
        worker.join(5)
        control.join(5)
        assistant.close()
    assert not worker.is_alive() and not control.is_alive()
    assert failures == [] and returned.is_set()
    assert env.repo.get_task(env.user, task.task_id).status == "cancel_requested"


def json_tool(path):
    pipeline = SimpleRAGPipeline(cache_path=str(path))
    pipeline._to_vector = lambda text: [1.0] * pipeline.dimension
    tool = RAGTool.__new__(RAGTool)
    tool._get_pipeline = lambda *args: pipeline
    tool._delete_graph_after_rag = lambda *args: {"status": "disabled"}
    return pipeline, tool


@pytest.mark.parametrize("failure", ["write_text", "replace"])
@pytest.mark.parametrize("action", ["pause", "cancel"])
def test_json_compensation_failure_retry_and_reopen(env, monkeypatch, failure, action):
    task = create(env).tasks[0]
    pipeline, tool = json_tool(env.runtime.paths.rag_cache)
    env.runtime.rag_tool = tool
    pipeline.add_text("target", document_id=task.document_id)
    pipeline.add_text("unrelated", document_id="unrelated")
    env.repo.claim_next(set())
    getattr(env.repo, "request_" + action)(env.user, task.task_id)
    original = getattr(Path, failure)

    def fail(path, *args, **kwargs):
        if path == pipeline.cache_path.with_suffix(".json.tmp"):
            raise OSError("durable JSON cleanup failed")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, failure, fail)
        env.runner.run(env.repo.claim_next(set()))
    current = env.repo.get_task(env.user, task.task_id)
    assert (current.status, current.error_code) == ("failed", action + "_cleanup_failed")
    assert (env.runtime.paths.root / task.staged_relative_path).exists()
    reopened, _ = json_tool(pipeline.cache_path)
    assert set(reopened.list_document_ids()) == {task.document_id, "unrelated"}
    env.service.cancel_task("token", task.task_id)
    env.runner.run(env.repo.claim_next(set()))
    assert env.repo.get_task(env.user, task.task_id).status == "cancelled"
    reopened, _ = json_tool(pipeline.cache_path)
    assert reopened.list_document_ids() == ["unrelated"]


def test_memory_snapshot_cleanup_failure_retries_even_when_live_event_absent(env, monkeypatch):
    task = create(env).tasks[0]
    snapshot = MemorySnapshotRepository(env.runtime.paths.memory_snapshot, env.user)
    config = MemoryConfig(database_path=str(env.runtime.paths.root / "memory.db"))
    tool = MemoryTool(user_id=env.user, memory_config=config,
                      memory_types=["episodic"], memory_repository=snapshot)
    pipeline, env.runtime.rag_tool = json_tool(env.runtime.paths.rag_cache)
    env.runtime.memory_tool = tool
    target = tool.ensure_import_event(task.task_id, "target")
    unrelated = tool.ensure_import_event("other-task", "unrelated")
    env.service.cancel_task("token", task.task_id)
    calls = []

    replace = os.replace

    def fail(source, destination, *args, **kwargs):
        if Path(destination) == snapshot.path:
            calls.append(True)
            raise OSError("snapshot write failed")
        return replace(source, destination, *args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "replace", fail)
            for _ in range(2):
                env.runner.run(env.repo.claim_next(set()))
                current = env.repo.get_task(env.user, task.task_id)
                assert (current.status, current.error_code) == ("failed", "cancel_cleanup_failed")
                assert (env.runtime.paths.root / task.staged_relative_path).exists()
                assert target not in tool.memory_manager.memory_types["episodic"]._episodes
                env.service.cancel_task("token", task.task_id)
        assert len(calls) == 2
        env.runner.run(env.repo.claim_next(set()))
        assert env.repo.get_task(env.user, task.task_id).status == "cancelled"
    finally:
        tool.close()
    reopened = MemoryTool(user_id=env.user, memory_config=config,
                          memory_types=["episodic"], memory_repository=snapshot)
    try:
        assert set(reopened.memory_manager.memory_types["episodic"]._episodes) == {unrelated}
    finally:
        reopened.close()


def test_ordinary_events_are_atomic_deduplicated_and_restart_persistent(env):
    task = create(env).tasks[0]
    repo = env.repo
    repo.claim_next(set())
    repo.update_progress(env.user, task.task_id, "parsing", 15)
    repo.update_progress(env.user, task.task_id, "parsing", 20)
    repo.update_progress(env.user, task.task_id, "embedding", 50)
    repo.mark_retry_wait(env.user, task.task_id, "2000-01-01T00:00:00Z", "rag_connection", "token=private")
    repo.claim_next(set())
    repo.mark_failed(env.user, task.task_id, "document_invalid", "secret /private/file")
    repo.retry_task(env.user, task.task_id)
    repo.claim_next(set())
    repo.mark_failed(env.user, task.task_id, "document_invalid", "safe")
    assert repo.retry_failed_in_batch(env.user, task.batch_id) == 1
    assert repo.retry_failed_in_batch(env.user, task.batch_id) == 0
    repo.claim_next(set())
    repo.mark_succeeded(env.user, task.task_id)
    expected = ["submitted", "claimed", "progress", "progress", "auto_retry", "claimed", "failed",
                "manual_retry", "claimed", "failed", "manual_retry", "claimed", "succeeded"]
    events = ImportTaskRepository(repo.db_path).list_task_events(env.user, task.task_id)
    assert [event.event_type for event in events] == expected
    assert "private" not in str(events)
    for transition in [repo.mark_succeeded, repo.retry_task]:
        with pytest.raises(InvalidImportTransition):
            transition(env.user, task.task_id)
    assert repo.list_task_events(env.user, task.task_id) == events
    assert repo.list_task_events("other-user", task.task_id) == []


@pytest.mark.parametrize("outcome", ["succeeded", "failed"])
def test_normal_lifecycle_records_complete_sequence(env, outcome):
    task = create(env).tasks[0]
    env.repo.claim_next(set())
    env.repo.update_progress(env.user, task.task_id, "parsing", 15)
    if outcome == "succeeded":
        env.repo.mark_succeeded(env.user, task.task_id)
    else:
        env.repo.mark_failed(env.user, task.task_id, "document_invalid", "safe failure")
    events = env.repo.list_task_events(env.user, task.task_id)
    assert [event.event_type for event in events] == ["submitted", "claimed", "progress", outcome]
    assert [event.status for event in events] == ["queued", "running", "running", outcome]


@pytest.mark.parametrize("transition", ["submission", "claim", "progress", "success", "failure", "auto_retry", "manual_retry", "batch_retry"])
def test_event_failure_rolls_back_ordinary_transition(env, monkeypatch, transition):
    task = create(env).tasks[0]
    if transition not in {"submission", "claim"}:
        env.repo.claim_next(set())
    if transition in {"manual_retry", "batch_retry"}:
        env.repo.mark_failed(env.user, task.task_id, "document_invalid", "safe")
    before = env.repo.get_batch(env.user, task.batch_id)
    events = env.repo.list_task_events(env.user, task.task_id)
    batches = env.repo.list_batches(env.user)
    def fail(*args):
        raise OSError("event insertion failed")
    monkeypatch.setattr(env.repo, "_insert_event", fail)
    actions = {
        "submission": lambda: create(env),
        "claim": lambda: env.repo.claim_next(set()),
        "progress": lambda: env.repo.update_progress(env.user, task.task_id, "parsing", 15),
        "success": lambda: env.repo.mark_succeeded(env.user, task.task_id),
        "failure": lambda: env.repo.mark_failed(env.user, task.task_id, "document_invalid", "safe"),
        "auto_retry": lambda: env.repo.mark_retry_wait(env.user, task.task_id, "2000-01-01T00:00:00Z", "rag_connection", "safe"),
        "manual_retry": lambda: env.repo.retry_task(env.user, task.task_id),
        "batch_retry": lambda: env.repo.retry_failed_in_batch(env.user, task.batch_id),
    }
    with pytest.raises(OSError, match="event insertion"):
        actions[transition]()
    assert env.repo.get_batch(env.user, task.batch_id) == before
    assert env.repo.list_task_events(env.user, task.task_id) == events
    assert env.repo.list_batches(env.user) == batches


@pytest.mark.parametrize("operation", ["clear_all_documents", "delete_current_document"])
def test_cancel_winning_guard_race_blocks_destructive_mutation(env, monkeypatch, operation):
    task = create(env).tasks[0]
    env.repo.request_cancel(env.user, task.task_id)
    env.repo.mark_control_failed(env.user, task.task_id, "cancel_cleanup_failed", "safe")
    assistant = PDFLearningAssistant(user_id=env.user, runtime=env.runtime)
    assistant.current_document_id = task.document_id
    monkeypatch.setattr(assistant, "_clear_documents_coordinated", lambda: pytest.fail("clear crossed active guard"))
    monkeypatch.setattr(assistant, "_delete_document_coordinated", lambda doc: pytest.fail("delete crossed active guard"))
    try:
        env.service.cancel_task("token", task.task_id)
        assert "active" in getattr(assistant, operation)()
    finally:
        assistant.close()


@pytest.mark.parametrize("winner", ["delete", "cancel"])
def test_history_delete_and_cleanup_retry_keep_conditional_guards(env, winner):
    task = create(env).tasks[0]
    env.repo.request_cancel(env.user, task.task_id)
    env.repo.mark_control_failed(env.user, task.task_id, "cancel_cleanup_failed", "safe")
    if winner == "delete":
        env.repo.mark_batch_deleting(env.user, task.batch_id)
        with pytest.raises(InvalidImportTransition):
            env.service.cancel_task("token", task.task_id)
        assert env.repo.get_deleting_batch(env.user, task.batch_id) is not None
    else:
        env.service.cancel_task("token", task.task_id)
        with pytest.raises(InvalidImportTransition):
            env.service.delete_batch_history("token", task.batch_id)
        assert env.repo.get_task(env.user, task.task_id).status == "cancel_requested"


def test_deletion_recovery_rotates_full_failed_page_durably_and_wraps(env, monkeypatch):
    batches = []
    for index in range(23):
        task = create(env).tasks[0]
        env.repo.request_cancel(env.user, task.task_id)
        env.repo.mark_cancelled(env.user, task.task_id)
        env.repo.mark_batch_deleting(env.user, task.batch_id)
        if index < 20:
            with connect(env.repo.db_path) as conn:
                conn.execute("update import_tasks set staged_relative_path='../unsafe' where id=?", (task.task_id,))
        batches.append(task.batch_id)
    monkeypatch.setattr("app.import_repository._utc_now", lambda: "2000-01-01T00:00:00Z")
    attempted = []
    original = ImportHistoryMaintenance.resume_batch_deletion
    def tracked(self, user_id, batch_id):
        attempted.append((user_id, batch_id))
        return original(self, user_id, batch_id)
    monkeypatch.setattr(ImportHistoryMaintenance, "resume_batch_deletion", tracked)
    for index in range(3):
        # Reconstruct both objects each pass, as after a process restart.
        initialize_database(env.repo.db_path)
        maintenance = ImportHistoryMaintenance(ImportTaskRepository(env.repo.db_path), env.storage)
        before = len(attempted)
        recovered = maintenance.recover_deleting(limit=20)
        assert len(attempted) - before <= 20
        assert recovered == (3 if index == 1 else 0)
    assert {batch for user, batch in attempted} == set(batches)
    assert {user for user, batch in attempted} == {env.user}
    assert all(env.repo.get_batch(env.user, batch) is None for batch in batches[20:])
    assert all(env.repo.get_deleting_batch(env.user, batch) for batch in batches[:20])
    assert all(sum(batch == item for _, item in attempted) >= 2 for batch in batches[:20])
    with connect(env.repo.db_path) as conn:
        counts = conn.execute(
            "select cleanup_attempt_count from import_batches where lifecycle_state='deleting'"
        ).fetchall()
        assert all(row[0] >= 2 for row in counts)
        plan = conn.execute(
            """explain query plan select id, user_id from import_batches
               where lifecycle_state='deleting'
               order by cleanup_attempt_count, delete_requested_at, created_at, id limit 20"""
        ).fetchall()
        assert any("ix_import_batches_deletion_recovery" in row[3] for row in plan)
        assert not any("TEMP B-TREE" in row[3] for row in plan)
