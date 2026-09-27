"""Versioned snapshot authority across independent PostgreSQL connections."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from psycopg.conninfo import make_conninfo
from sqlalchemy.engine import make_url

from app.postgres import PostgresDatabase
from app.postgres_snapshots import PostgresSnapshotRepository, SnapshotConflict


@pytest.fixture
def snapshots(monkeypatch):
    base = os.getenv('POSTGRES_TEST_URL')
    if not base:
        pytest.skip('POSTGRES_TEST_URL must name a disposable database')
    schema = 'snapshots_' + uuid4().hex
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(sql.SQL('create schema {}').format(sql.Identifier(schema)))
    url = make_url(base).set(drivername='postgresql+psycopg', query={'options': f'-csearch_path={schema}'})
    monkeypatch.setenv('DATABASE_URL', url.render_as_string(hide_password=False))
    pools = []
    try:
        command.upgrade(Config('alembic.ini'), 'head')
        conninfo = make_conninfo(base, options=f'-csearch_path={schema}')
        for _ in range(2):
            pool = PostgresDatabase(conninfo, min_size=1, max_size=3)
            pool.open()
            pools.append(pool)
        users = [str(uuid4()), str(uuid4())]
        with pools[0].transaction() as cursor:
            for user in users:
                cursor.execute('insert into users(id,username,username_key,password_hash,created_at,updated_at) values(%s,%s,%s,%s,%s,%s)',
                               (user, user, user, 'unused', '2026-09-27', '2026-09-27'))
        yield [PostgresSnapshotRepository(pool) for pool in pools], users, pools
    finally:
        for pool in pools:
            pool.close()
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(sql.SQL('drop schema {} cascade').format(sql.Identifier(schema)))


def history():
    return {'documents': [], 'questions': [], 'notes': [], 'sessions': []}


def test_snapshot_cas_persistence_and_tenant_isolation(snapshots):
    repos, (user, other), pools = snapshots
    assert repos[0].read(user, 'history') is None
    value = history()
    value['documents'].append({'document_id': 'source-doc', 'name': '中文资料'})
    saved = repos[0].compare_and_swap(user, 'history', value, expected_version=0)
    assert saved.version == 1
    assert repos[1].read(user, 'history').data == value
    assert repos[1].read(other, 'history') is None
    with pytest.raises(SnapshotConflict):
        repos[1].compare_and_swap(user, 'history', history(), expected_version=0)
    pools[0].close()
    assert repos[1].read(user, 'history').data == value


def test_concurrent_updates_do_not_lose_history(snapshots):
    repos, (user, _), _pools = snapshots
    barrier = Barrier(2)

    def append(index):
        barrier.wait(timeout=5)
        return repos[index].update(user, 'history', lambda data: data['questions'].append({'id': index}))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(append, (0, 1)))
    assert sorted(item.version for item in results) == [1, 2]
    assert sorted(item['id'] for item in repos[0].read(user, 'history').data['questions']) == [0, 1]


def test_cas_race_has_only_one_winner(snapshots):
    repos, (user, _), _pools = snapshots
    barrier = Barrier(2)

    def write(index):
        barrier.wait(timeout=5)
        try:
            return repos[index].compare_and_swap(user, 'history', history(), expected_version=0)
        except SnapshotConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, (0, 1)))
    assert sum(item is not None for item in results) == 1


def test_invalid_or_cross_user_memory_cannot_replace_saved_state(snapshots):
    repos, (user, other), _pools = snapshots
    original = {'user_id': user, 'memories': []}
    repos[0].compare_and_swap(user, 'memory', original, expected_version=0)
    for invalid in (
        {'user_id': other, 'memories': []},
        {'user_id': user, 'memories': [{'id': 'm', 'metadata': {'user_id': other}}]},
        {'user_id': user, 'memories': 'broken'},
    ):
        with pytest.raises(ValueError):
            repos[0].compare_and_swap(user, 'memory', invalid, expected_version=1)
    assert repos[0].read(user, 'memory').data == original
    assert repos[0].read(user, 'memory').version == 1


def test_mutation_failure_and_caller_transaction_rollback(snapshots):
    repos, (user, _), pools = snapshots

    def fail(data):
        data['documents'].append({'id': 'invisible'})
        raise RuntimeError('injected')

    with pytest.raises(RuntimeError, match='injected'):
        repos[0].update(user, 'history', fail)
    assert repos[1].read(user, 'history') is None
    with pytest.raises(RuntimeError):
        with pools[0].transaction() as cursor:
            repos[0].compare_and_swap_in_transaction(cursor, user, 'history', history(), expected_version=0)
            raise RuntimeError('abort composite publication')
    assert repos[1].read(user, 'history') is None


def test_unknown_kind_and_noninteger_version_are_rejected(snapshots):
    repos, (user, _), _pools = snapshots
    with pytest.raises(ValueError):
        repos[0].read(user, 'other')
    for version in (-1, True, 1.5):
        with pytest.raises(ValueError):
            repos[0].compare_and_swap(user, 'history', history(), expected_version=version)


def test_autocommit_requires_explicit_transaction(snapshots):
    repos, (user, _), pools = snapshots
    with pools[0].connection() as connection:
        connection.autocommit = True
        try:
            with connection.cursor() as cursor:
                with pytest.raises(ValueError, match='Caller-owned transaction'):
                    repos[0].compare_and_swap_in_transaction(cursor, user, 'history', history(), expected_version=0)
                assert repos[1].read(user, 'history') is None
                with pytest.raises(RuntimeError, match='abort'):
                    with connection.transaction():
                        repos[0].compare_and_swap_in_transaction(cursor, user, 'history', history(), expected_version=0)
                        raise RuntimeError('abort')
        finally:
            connection.autocommit = False
    assert repos[1].read(user, 'history') is None


def test_positive_cas_and_json_copies_preserve_winner(snapshots):
    repos, (user, _), _pools = snapshots
    original = history()
    original['documents'] = [{'document_id': 'd', 'pages': [3, 1], 'nested': {'text': '中文', 'flag': True, 'missing': None}}]
    first = repos[0].compare_and_swap(user, 'history', original, expected_version=0)
    original['documents'][0]['pages'].append(99)
    assert first.data['documents'][0]['pages'] == [3, 1]
    first.data['documents'][0]['pages'].append(2)
    second = repos[1].compare_and_swap(user, 'history', first.data, expected_version=1)
    assert second.version == 2
    with pytest.raises(SnapshotConflict):
        repos[0].compare_and_swap(user, 'history', history(), expected_version=1)
    second.data['documents'].clear()
    saved = repos[0].read(user, 'history')
    assert saved.version == 2
    assert saved.data['documents'][0]['pages'] == [3, 1, 2]
    for value in (float('nan'), float('inf'), (1, 2), {1: 'integer key'}):
        invalid = history()
        invalid['extra'] = value
        with pytest.raises(ValueError):
            repos[0].compare_and_swap(user, 'history', invalid, expected_version=2)
    assert repos[1].read(user, 'history').data == saved.data
