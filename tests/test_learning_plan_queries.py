from datetime import timedelta
import pytest

from tests.test_learning_plan_repository import setup, command, NOW
from app.learning_models import LearningValidationError, LearningNotFound, SetLearningTaskState
from uuid import uuid4


def test_plan_and_task_pages(setup):
    _, repo = setup
    plans = [repo.create_plan('owner', command(days=3), document_name='资料', now=NOW).plan for _ in range(3)]
    seen = []
    cursor = None
    while True:
        page = repo.list_plans('owner', limit=1, cursor=cursor)
        seen.extend(item.id for item in page.items)
        cursor = page.next_cursor
        if not cursor:
            break
    assert seen == sorted([p.id for p in plans], reverse=True)
    first = repo.list_tasks('owner', plans[0].id, limit=2)
    last = repo.list_tasks('owner', plans[0].id, limit=2, cursor=first.next_cursor)
    assert len(first.items) == 2 and len(last.items) == 1 and last.next_cursor is None
    with pytest.raises(LearningNotFound):
        repo.list_tasks('other', plans[0].id)


def test_timezones_and_buckets(setup):
    _, repo = setup
    a = repo.create_plan('owner', command(days=2), document_name='资料', now=NOW).plan
    b = repo.create_plan('owner', command(days=2, timezone='America/Los_Angeles'), document_name='资料', now=NOW).plan
    today = repo.today('owner', bucket='today', now=NOW)
    assert {(t.plan_id, t.due_date) for t in today.items} == {(a.id, '2026-09-07'), (b.id, '2026-09-06')}
    assert not repo.today('owner', bucket='overdue', now=NOW).items
    task = today.items[0]
    repo.set_task_state('owner', task.id, SetLearningTaskState(str(uuid4()), 1, True), now=NOW)
    assert len(repo.today('owner', bucket='completed', now=NOW).items) == 1
    assert len(repo.today('owner', bucket='overdue', now=NOW + timedelta(days=1)).items) == 1


def test_scope_expiry_and_fence(setup):
    path, repo = setup
    for _ in range(2):
        repo.create_plan('owner', command(), document_name='资料', now=NOW)
    cursor = repo.today('owner', bucket='today', now=NOW, limit=1).next_cursor
    for user, bucket, now in [('other', 'today', NOW), ('owner', 'overdue', NOW),
                              ('owner', 'today', NOW + timedelta(seconds=301)), ('owner', 'today', NOW - timedelta(seconds=1))]:
        with pytest.raises(LearningValidationError):
            repo.today(user, bucket=bucket, now=now, cursor=cursor)
    from app.database import connect
    with connect(path) as db:
        db.execute("insert into qa_deletion_fences (id,user_id,target_type,target_id,status,stage,created_at,updated_at) values ('f','owner','document','doc','completed','completed','now','now')")
    assert not repo.list_plans('owner').items
    assert not repo.today('owner', bucket='today', now=NOW).items


@pytest.mark.parametrize('limit', [True, 0, 51, '20'])
def test_limit(setup, limit):
    with pytest.raises(LearningValidationError):
        setup[1].list_plans('owner', limit=limit)


@pytest.mark.parametrize('cursor', ['!', 'e30=', 'x' * 2049, 12])
def test_bad_cursor(setup, cursor):
    with pytest.raises(LearningValidationError):
        setup[1].list_plans('owner', cursor=cursor)


def test_query_limit_and_cross_query_cursor(setup, monkeypatch):
    _, repo = setup
    for _ in range(3):
        repo.create_plan('owner', command(), document_name='资料', now=NOW)
    import app.learning_queries as queries
    original = queries.connect
    statements = []
    def traced(path):
        conn = original(path)
        conn.set_trace_callback(statements.append)
        return conn
    monkeypatch.setattr(queries, 'connect', traced)
    page = repo.list_plans('owner', limit=1)
    assert len(page.items) == 1
    assert any('sort_key' in sql and sql.endswith('limit 2') for sql in statements)
    with pytest.raises(LearningValidationError):
        repo.list_tasks('owner', page.items[0].id, cursor=page.next_cursor)


def test_dst_day_uses_calendar_not_24_hours(setup):
    from datetime import datetime, timezone
    _, repo = setup
    before = datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)  # Los Angeles March 7
    plan = repo.create_plan('owner', command(days=3, timezone='America/Los_Angeles'), document_name='资料', now=before).plan
    after = datetime(2026, 3, 9, 7, 15, tzinfo=timezone.utc)  # March 9 after spring transition
    today = repo.today('owner', bucket='today', now=after)
    assert [(t.plan_id,t.due_date) for t in today.items] == [(plan.id,'2026-03-09')]
    assert len(repo.today('owner', bucket='overdue', now=after).items) == 2


def test_strict_cursor_types(setup):
    import base64
    import json
    _, repo = setup
    for _ in range(2):
        repo.create_plan('owner', command(), document_name='资料', now=NOW)
    cursor = repo.list_plans('owner', limit=1).next_cursor
    raw = json.loads(base64.urlsafe_b64decode(cursor))
    for key,value in [('v',True), ('last',[42,'id']), ('as_of','2026-09-07'), ('extra',1)]:
        changed = dict(raw)
        changed[key] = value
        invalid = base64.urlsafe_b64encode(json.dumps(changed).encode()).decode()
        with pytest.raises(LearningValidationError):
            repo.list_plans('owner', cursor=invalid)
