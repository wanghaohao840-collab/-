from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.database import connect, initialize_database
from app.learning_models import (
    CreateLearningPlan, SetLearningTaskState, LearningNotFound,
    LearningVersionConflict, LearningIdempotencyConflict,
    LearningDocumentDeleting, LearningValidationError,
)
from app.learning_repository import LearningRepository

NOW = datetime(2026, 9, 6, 16, 30, tzinfo=timezone.utc)


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / 'app.db'
    initialize_database(path)
    with connect(path) as db:
        for user in ('owner', 'other'):
            db.execute('insert into users values (?, ?, ?, ?, ?, ?, ?)',
                       (user, user, user, 'hash', 'active', 'now', 'now'))
    return path, LearningRepository(path)


def command(**changes):
    return replace(CreateLearningPlan(str(uuid4()), 'doc', '学习', 1, 30, 'Asia/Shanghai'), **changes)


def first_task(path):
    with connect(path) as db:
        return db.execute("select id from learning_tasks where user_id='owner'").fetchone()[0]


def test_create_replay_and_isolation(setup):
    path, repo = setup
    cmd = command()
    result = repo.create_plan('owner', cmd, document_name='资料', now=NOW)
    assert result.plan.start_date == '2026-09-07'
    assert result.plan.task_count == 1
    repeated = repo.create_plan('owner', cmd, document_name='新名称', now=NOW + timedelta(days=1))
    assert repeated.replayed and repeated.plan == result.plan
    with pytest.raises(LearningIdempotencyConflict):
        repo.create_plan('owner', replace(cmd, days=2), document_name='资料', now=NOW)
    with pytest.raises(LearningNotFound):
        repo.get_plan('other', result.plan.id)
    with pytest.raises(LearningNotFound):
        repo.get_task('other', first_task(path))


def test_complete_undo_noop_and_replay_current_state(setup):
    path, repo = setup
    plan = repo.create_plan('owner', command(), document_name='资料', now=NOW).plan
    task_id = first_task(path)
    cmd = SetLearningTaskState(str(uuid4()), 1, True)
    done = repo.set_task_state('owner', task_id, cmd, now=NOW)
    assert done.task.version == 2 and done.task.completed
    assert repo.get_plan('owner', plan.id).status == 'completed'
    repo.set_task_state('owner', task_id, SetLearningTaskState(str(uuid4()), 2, True), now=NOW)
    undo = repo.set_task_state('owner', task_id, SetLearningTaskState(str(uuid4()), 2, False), now=NOW)
    assert undo.task.version == 3 and undo.task.completed_at is None
    replay = repo.set_task_state('owner', task_id, cmd, now=NOW)
    assert replay.replayed and replay.task == undo.task
    with connect(path) as db:
        assert db.execute('select count(*) from learning_task_events').fetchone()[0] == 2
    assert repo.get_plan('owner', plan.id).status == 'active'


def test_concurrent_version_single_winner(setup):
    path, repo = setup
    repo.create_plan('owner', command(), document_name='资料', now=NOW)
    task_id = first_task(path)
    def update(_):
        try:
            repo.set_task_state('owner', task_id, SetLearningTaskState(str(uuid4()), 1, True), now=NOW)
            return 'done'
        except LearningVersionConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, range(2))) == ['conflict', 'done']
    with connect(path) as db:
        assert db.execute('select count(*) from learning_task_events').fetchone()[0] == 1
        assert db.execute('select count(*) from learning_requests').fetchone()[0] == 2


def test_delete_rollback_and_no_resurrection(setup):
    path, repo = setup
    cmd = command()
    plan = repo.create_plan('owner', cmd, document_name='资料', now=NOW).plan
    other = repo.create_plan('other', cmd, document_name='资料', now=NOW).plan
    with connect(path) as db:
        db.execute('begin immediate')
        repo.delete_document_in_transaction(db, user_id='owner', document_id='doc')
        db.rollback()
    assert repo.get_plan('owner', plan.id) == plan
    with connect(path) as db:
        db.execute('begin immediate')
        repo.delete_document_in_transaction(db, user_id='owner', document_id='doc')
    with pytest.raises(LearningNotFound):
        repo.create_plan('owner', cmd, document_name='资料', now=NOW)
    assert repo.get_plan('other', other.id) == other


def test_completed_fence_and_failed_transaction(setup):
    path, repo = setup
    cmd = command()
    plan = repo.create_plan('owner', cmd, document_name='资料', now=NOW).plan
    task_id = first_task(path)
    with connect(path) as db:
        db.execute("create trigger fail_event before insert on learning_task_events begin select raise(abort, 'test'); end")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        repo.set_task_state('owner', task_id, SetLearningTaskState(str(uuid4()), 1, True), now=NOW)
    assert repo.get_task('owner', task_id).version == 1
    with connect(path) as db:
        assert db.execute('select count(*) from learning_requests').fetchone()[0] == 1
        db.execute("insert into qa_deletion_fences (id,user_id,target_type,target_id,status,stage,created_at,updated_at) values ('f','owner','document','doc','completed','completed','now','now')")
    with pytest.raises(LearningNotFound):
        repo.get_plan('owner', plan.id)
    with pytest.raises(LearningDocumentDeleting):
        repo.create_plan('owner', command(), document_name='资料', now=NOW)


@pytest.mark.parametrize('changes', [{'days':True}, {'daily_minutes':True}, {'days':366}, {'title':' '}, {'timezone':'invalid'}, {'request_id':'bad'}])
def test_validation(setup, changes):
    _, repo = setup
    with pytest.raises(LearningValidationError):
        repo.create_plan('owner', command(**changes), document_name='资料', now=NOW)


def test_concurrent_duplicate_create(setup):
    path, repo = setup
    cmd = command(days=3)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: repo.create_plan('owner', cmd, document_name='资料', now=NOW), range(2)))
    assert results[0].plan.id == results[1].plan.id
    assert sorted(item.replayed for item in results) == [False, True]
    with connect(path) as db:
        assert db.execute('select count(*) from learning_tasks').fetchone()[0] == 3


def test_task_request_conflict_and_invalid_state(setup):
    path, repo = setup
    repo.create_plan('owner', command(), document_name='资料', now=NOW)
    task_id = first_task(path)
    cmd = SetLearningTaskState(str(uuid4()), 1, True)
    repo.set_task_state('owner', task_id, cmd, now=NOW)
    with pytest.raises(LearningIdempotencyConflict):
        repo.set_task_state('owner', task_id, replace(cmd, completed=False), now=NOW)
    for bad in (replace(cmd, completed=1), replace(cmd, expected_version=True), replace(cmd, expected_version=0)):
        with pytest.raises(LearningValidationError):
            repo.set_task_state('owner', task_id, bad, now=NOW)
    with connect(path) as db:
        with pytest.raises(ValueError):
            repo.delete_document_in_transaction(db, user_id='owner', document_id='doc')


def test_create_failure_rolls_back_all_records(setup):
    import sqlite3
    path, repo = setup
    with connect(path) as db:
        db.execute("create trigger fail_request before insert on learning_requests begin select raise(abort, 'test'); end")
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_plan('owner', command(), document_name='资料', now=NOW)
    with connect(path) as db:
        for table in ('learning_plans', 'learning_plan_documents', 'learning_tasks', 'learning_requests'):
            assert db.execute(f'select count(*) from {table}').fetchone()[0] == 0
