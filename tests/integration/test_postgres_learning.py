"""Real PostgreSQL learning contract, isolated with an Alembic-head schema."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from tests.integration.test_postgres_auth_sessions import shared_database
from app.learning_models import (CreateLearningPlan, SetLearningTaskState,
    LearningNotFound, LearningIdempotencyConflict, LearningVersionConflict,
    LearningDocumentDeleting, LearningValidationError)
from app.learning_repository import PostgresLearningRepository

NOW = datetime(2026, 9, 6, 16, 30, tzinfo=timezone.utc)


@pytest.fixture
def repositories(shared_database):
    open_pool, _ = shared_database
    first, second = open_pool(), open_pool()
    with first.transaction() as cur:
        for user in ('owner', 'other'):
            cur.execute('insert into users values (%s,%s,%s,%s,%s,%s,%s)',
                        (user, user, user, 'hash', 'active', 'now', 'now'))
    return first, PostgresLearningRepository(first), PostgresLearningRepository(second), open_pool


def command(**changes):
    return replace(CreateLearningPlan(str(uuid4()), 'doc', '学习', 1, 30, 'Asia/Shanghai'), **changes)


def test_replay_concurrency_and_restart(repositories):
    db, first, second, open_pool = repositories
    cmd = command()
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda repo: repo.create_plan('owner', cmd, document_name='资料', now=NOW), (first, second)))
    assert sorted(r.replayed for r in results) == [False, True]
    plan = results[0].plan
    assert results[1].plan == plan
    assert plan.start_date == '2026-09-07'
    with pytest.raises(LearningIdempotencyConflict):
        second.create_plan('owner', replace(cmd, days=2), document_name='资料', now=NOW)
    task = first.list_tasks('owner', plan.id).items[0]
    def finish(repo):
        try:
            return repo.set_task_state('owner', task.id, SetLearningTaskState(str(uuid4()), 1, True), now=NOW).task.version
        except LearningVersionConflict:
            return 'conflict'
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(finish, (first, second)))
    assert 2 in results and 'conflict' in results
    assert second.get_plan('owner', plan.id).status == 'completed'
    with db.transaction() as cur:
        assert cur.execute('select count(*) as n from learning_task_events').fetchone()['n'] == 1
    restarted = PostgresLearningRepository(open_pool())
    assert restarted.get_task('owner', task.id).completed
    with pytest.raises(LearningNotFound):
        second.get_plan('other', plan.id)
    with pytest.raises(LearningNotFound):
        second.get_task('other', task.id)
    with pytest.raises(LearningNotFound):
        first.create_plan('missing', command(), document_name='资料', now=NOW)


def add_fence(db):
    with db.transaction() as cur:
        cur.execute("select id from users where id='owner' for update")
        cur.execute("""insert into qa_deletion_fences
            (id,user_id,target_type,target_id,status,stage,created_at,updated_at)
            values ('f','owner','document','doc','completed','completed','now','now')""")


def test_caller_owned_delete_rollback_fence_and_replay(repositories):
    db, repo, other_repo, _ = repositories
    cmd = command()
    plan = repo.create_plan('owner', cmd, document_name='资料', now=NOW).plan
    other = repo.create_plan('other', command(), document_name='资料', now=NOW).plan
    task = repo.list_tasks('owner', plan.id).items[0]
    repo.set_task_state('owner', task.id, SetLearningTaskState(str(uuid4()), 1, True), now=NOW)
    with pytest.raises(RuntimeError, match='rollback'):
        with db.transaction() as cur:
            cur.execute("select id from users where id='owner' for update")
            repo.delete_document_in_transaction(cur, user_id='owner', document_id='doc')
            assert cur.execute("select count(*) as n from learning_tasks where user_id='owner'").fetchone()['n'] == 0
            raise RuntimeError('rollback')
    assert other_repo.get_task('owner', task.id).completed
    add_fence(db)
    assert not other_repo.list_plans('owner').items
    assert not other_repo.today('owner', bucket='completed', now=NOW).items
    with pytest.raises(LearningNotFound):
        repo.get_task('owner', task.id)
    with pytest.raises(LearningDocumentDeleting):
        repo.create_plan('owner', command(), document_name='资料', now=NOW)
    with db.transaction() as cur:
        cur.execute("select id from users where id='owner' for update")
        repo.delete_document_in_transaction(cur, user_id='owner', document_id='doc')
    assert other_repo.get_plan('other', other.id).id == other.id
    with pytest.raises(LearningNotFound):
        repo.create_plan('owner', cmd, document_name='资料', now=NOW)
    with db.transaction() as cur:
        assert cur.execute("select count(*) as n from learning_requests where user_id='owner'").fetchone()['n'] == 2
        assert cur.execute("select count(*) as n from learning_task_events where user_id='owner'").fetchone()['n'] == 0
    with db.connection() as conn:
        with conn.cursor() as cur:
            with pytest.raises(ValueError, match='caller-owned'):
                repo.delete_document_in_transaction(cur, user_id='owner', document_id='doc')


def test_timezone_frozen_cursor_and_scope(repositories):
    _, repo, second, _ = repositories
    instant = datetime(2026, 9, 6, 15, 59, 59, tzinfo=timezone.utc)
    plans = [repo.create_plan('owner', command(days=2, timezone=zone), document_name='资料', now=instant).plan
             for zone in ('Asia/Shanghai', 'Asia/Shanghai', 'America/Los_Angeles')]
    first = repo.today('owner', bucket='today', now=instant, limit=1)
    seen = list(first.items)
    cursor = first.next_cursor
    while cursor:
        page = second.today('owner', bucket='today', now=instant + timedelta(seconds=2), cursor=cursor, limit=1)
        seen.extend(page.items)
        cursor = page.next_cursor
    assert {t.plan_id for t in seen} == {p.id for p in plans}
    assert {t.due_date for t in seen} == {'2026-09-06'}
    assert len(repo.today('owner', bucket='overdue', now=instant + timedelta(seconds=2)).items) == 2
    for user, bucket, now in [('other', 'today', instant), ('owner', 'overdue', instant),
                              ('owner', 'today', instant + timedelta(seconds=301)),
                              ('owner', 'today', instant - timedelta(seconds=1))]:
        with pytest.raises(LearningValidationError):
            repo.today(user, bucket=bucket, now=now, cursor=first.next_cursor)
    for index, task in enumerate(seen):
        repo.set_task_state('owner', task.id, SetLearningTaskState(str(uuid4()), 1, True), now=instant + timedelta(seconds=index))
    page = repo.today('owner', bucket='completed', now=instant + timedelta(seconds=2), limit=2)
    last = repo.today('owner', bucket='completed', now=instant + timedelta(seconds=3), limit=2, cursor=page.next_cursor)
    assert [t.id for t in (*page.items, *last.items)] == [t.id for t in reversed(seen)]
    assert last.next_cursor is None
    assert not repo.list_plans('other').items


def test_noop_undo_validation_and_bounded_pages(repositories):
    db, repo, _, _ = repositories
    plans = [repo.create_plan('owner', command(days=3), document_name='资料', now=NOW).plan for _ in range(3)]
    seen, cursor = [], None
    while True:
        page = repo.list_plans('owner', limit=1, cursor=cursor)
        seen.extend(p.id for p in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert seen == sorted((p.id for p in plans), reverse=True)
    page = repo.list_tasks('owner', plans[0].id, limit=2)
    assert len(repo.list_tasks('owner', plans[0].id, limit=2, cursor=page.next_cursor).items) == 1
    task = page.items[0]
    cmd = SetLearningTaskState(str(uuid4()), 1, True)
    repo.set_task_state('owner', task.id, cmd, now=NOW)
    noop = repo.set_task_state('owner', task.id, SetLearningTaskState(str(uuid4()), 2, True), now=NOW)
    assert noop.task.version == 2
    repo.set_task_state('owner', task.id, SetLearningTaskState(str(uuid4()), 2, False), now=NOW)
    replay = repo.set_task_state('owner', task.id, cmd, now=NOW)
    assert replay.replayed and replay.task.version == 3 and replay.task.completed_at is None
    with pytest.raises(LearningIdempotencyConflict):
        repo.set_task_state('owner', task.id, replace(cmd, completed=False), now=NOW)
    with db.transaction() as cur:
        assert cur.execute('select count(*) as n from learning_task_events').fetchone()['n'] == 2
    for invalid in (True, 0, 51, '20'):
        with pytest.raises(LearningValidationError):
            repo.list_plans('owner', limit=invalid)
    for invalid in ('!', 'e30=', 'x' * 2049, 12):
        with pytest.raises(LearningValidationError):
            repo.list_plans('owner', cursor=invalid)
    with pytest.raises(LearningValidationError):
        repo.create_plan('owner', command(days=True), document_name='资料', now=NOW)
    with pytest.raises(LearningValidationError):
        repo.list_tasks('owner', plans[0].id, cursor=repo.list_plans('owner', limit=1).next_cursor)
    with pytest.raises(LearningNotFound):
        repo.list_tasks('other', plans[0].id)


@pytest.mark.parametrize('concurrent_change', ['delete', 'fence'])
def test_page_and_dto_share_snapshot(repositories, monkeypatch, concurrent_change):
    from app.learning_persistence import LearningStore
    _, repo, second, _ = repositories
    plan = repo.create_plan('owner', command(), document_name='资料', now=NOW).plan
    original = LearningStore.page
    def page_then_delete(store, *args, **kwargs):
        rows = original(store, *args, **kwargs)
        if concurrent_change == 'fence':
            add_fence(second.persistence.database)
        else:
            with second.persistence.database.transaction() as cur:
                cur.execute("select id from users where id='owner' for update")
                second.delete_document_in_transaction(cur, user_id='owner', document_id='doc')
        return rows
    monkeypatch.setattr(LearningStore, 'page', page_then_delete)
    assert repo.list_plans('owner').items == (plan,)
    with pytest.raises(LearningNotFound):
        second.get_plan('owner', plan.id)
