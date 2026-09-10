import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.database import connect, initialize_database
from app.learning_migration import MigrationImportError, import_legacy_plans
from app.learning_repository import LearningRepository

NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / 'app.db'
    initialize_database(path)
    owner, other = str(uuid4()), str(uuid4())
    with connect(path) as db:
        for user in (owner, other):
            db.execute('insert into users values (?,?,?,?,?,?,?)',
                       (user, user, user, 'hash', 'active', 'now', 'now'))
    source = dict(version=1, cards=[], exercises=[], review_logs=[], plans=[dict(
        id=str(uuid4()), document_id='doc', document_name='资料.md', title='学习',
        target_date='2026-09-07', daily_minutes=30, status='completed',
        created_at=NOW.isoformat(), updated_at=NOW.isoformat(), tasks=[dict(
            id=str(uuid4()), due_date='2026-09-07', phase='review', title='复习',
            duration_minutes=30, completed=True, completed_at=NOW.isoformat())])])
    return path, owner, other, source


def run(setup, **overrides):
    path, owner, _, source = setup
    args = dict(user_id=owner, payload=json.dumps(source).encode(), timezone_name='Asia/Shanghai',
                ready_document_ids=frozenset({'doc'}), now=NOW)
    return import_legacy_plans(path, **(args | overrides))


def count(path, table, user):
    with connect(path) as db:
        return db.execute(f'select count(*) from {table} where user_id=?', (user,)).fetchone()[0]


def test_roundtrip_and_two_users(setup):
    path, owner, other, source = setup
    assert not run(setup).replayed
    plan = LearningRepository(path).get_plan(owner, source['plans'][0]['id'])
    assert plan.completed_count == plan.task_count == 1
    task = LearningRepository(path).get_task(owner, source['plans'][0]['tasks'][0]['id'])
    assert task.completed_at == NOW.isoformat()
    assert task.version == 1
    assert count(path, 'learning_task_events', owner) == 0
    assert count(path, 'learning_requests', owner) == 0
    assert count(path, 'learning_plans', other) == 0
    assert not run(setup, user_id=other).replayed
    assert count(path, 'learning_plans', owner) == 1


def test_replay_after_delete_does_not_resurrect(setup):
    path, owner, _, _ = setup
    run(setup)
    with connect(path) as db:
        db.execute('delete from learning_plans where user_id=?', (owner,))
    result = run(setup, ready_document_ids=frozenset())
    assert result.replayed and result.plan_count == result.task_count == 1
    assert count(path, 'learning_plans', owner) == 0


@pytest.mark.parametrize('change', ['source', 'zone'])
def test_marker_conflict(setup, change):
    run(setup)
    if change == 'source':
        setup[3]['plans'][0]['title'] = 'changed'
    with pytest.raises(MigrationImportError, match='MIGRATION_CONFLICT'):
        run(setup, **({'timezone_name': 'UTC'} if change == 'zone' else {}))


@pytest.mark.parametrize('table', ['learning_tasks', 'learning_migrations'])
def test_injected_failure_rolls_back_whole_batch(setup, table):
    path, owner, _, _ = setup
    with connect(path) as db:
        db.execute(f"create trigger fail before insert on {table} begin select raise(abort, 'private-detail'); end")
    with pytest.raises(MigrationImportError, match='^DATABASE_FAILURE$'):
        run(setup)
    for name in ('learning_plans', 'learning_plan_documents', 'learning_tasks', 'learning_migrations'):
        assert count(path, name, owner) == 0


def test_nonempty_target_without_marker_refused(setup):
    path, owner, _, _ = setup
    run(setup)
    with connect(path) as db:
        db.execute('delete from learning_migrations where user_id=?', (owner,))
    with pytest.raises(MigrationImportError, match='TARGET_NOT_EMPTY'):
        run(setup)


def test_unready_document_refused(setup):
    with pytest.raises(MigrationImportError, match='DOCUMENT_UNAVAILABLE'):
        run(setup, ready_document_ids=frozenset())
    assert count(setup[0], 'learning_migrations', setup[1]) == 0


@pytest.mark.parametrize('change', ['unknown', 'inactive', 'invalid'])
def test_user_context_rejected(setup, change):
    path, owner, _, _ = setup
    if change == 'inactive':
        with connect(path) as db:
            db.execute("update users set status='disabled' where id=?", (owner,))
    with pytest.raises(MigrationImportError):
        run(setup, user_id=str(uuid4()) if change == 'unknown' else '../unsafe' if change == 'invalid' else owner)


def test_missing_database_not_created(setup, tmp_path):
    path = tmp_path / 'missing.db'
    with pytest.raises(MigrationImportError, match='DATABASE_FAILURE'):
        import_legacy_plans(path, user_id=setup[1], payload=json.dumps(setup[3]).encode(),
                            timezone_name='UTC', ready_document_ids=frozenset({'doc'}), now=NOW)
    assert not path.exists()


def test_concurrent_import_replays(setup):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(setup), range(2)))
    assert sorted(result.replayed for result in results) == [False, True]
    assert count(setup[0], 'learning_plans', setup[1]) == 1


@pytest.mark.parametrize('status', ['queued', 'running', 'failed', 'completed'])
def test_all_document_fences_block_import(setup, status):
    path, owner, _, _ = setup
    with connect(path) as db:
        db.execute('''insert into qa_deletion_fences
            (id,user_id,target_type,target_id,status,stage,created_at,updated_at)
            values (?,?,'document','doc',?,'fenced','now','now')''', (str(uuid4()), owner, status))
    with pytest.raises(MigrationImportError, match='DOCUMENT_UNAVAILABLE'):
        run(setup)
    assert count(path, 'learning_migrations', owner) == 0


def test_readback_mismatch_rolls_back(setup):
    path, owner, _, _ = setup
    with connect(path) as db:
        db.execute("""create trigger change_title after insert on learning_tasks
            begin update learning_tasks set title='changed' where user_id=new.user_id and id=new.id; end""")
    with pytest.raises(MigrationImportError, match='READBACK_MISMATCH'):
        run(setup)
    assert count(path, 'learning_plans', owner) == 0
    assert count(path, 'learning_migrations', owner) == 0


def test_later_plan_failure_rolls_back_earlier_plan(setup):
    path, owner, _, source = setup
    later = json.loads(json.dumps(source['plans'][0]))
    later.update(id=str(uuid4()), document_id='missing')
    later['tasks'][0]['id'] = str(uuid4())
    source['plans'].append(later)
    with pytest.raises(MigrationImportError, match='DOCUMENT_UNAVAILABLE'):
        run(setup)
    assert count(path, 'learning_plans', owner) == 0


def test_empty_source_gets_marker(setup):
    setup[3]['plans'] = []
    result = run(setup, ready_document_ids=frozenset())
    assert result.plan_count == result.task_count == 0
    assert run(setup).replayed
