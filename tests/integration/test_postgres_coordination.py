"""User mutation fencing against disposable real PostgreSQL schemas."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Event
from datetime import timedelta
import time
from uuid import uuid4

import pytest

from app.postgres_coordination import MutationLeaseLost, PostgresUserMutationCoordinator
from app.postgres import PostgresDatabase
from app.postgres_snapshots import PostgresSnapshotRepository
from tests.integration.test_postgres_auth_sessions import shared_database


@pytest.fixture
def coordination(shared_database):
    open_pool, _ = shared_database
    first, second = open_pool(), open_pool()
    with first.transaction() as cursor:
        for user in ('owner', 'other'):
            cursor.execute('insert into users values (%s,%s,%s,%s,%s,%s,%s)',
                           (user, user, user, 'hash', 'active', 'now', 'now'))
    return first, second, PostgresUserMutationCoordinator(first), PostgresUserMutationCoordinator(second)


def expire(database):
    with database.transaction() as cursor:
        cursor.execute("update user_mutation_leases set lease_expires_at=clock_timestamp()-interval '1 second'")


def test_independent_pool_winner_and_unrelated_user_progress(coordination):
    database, other_db, first, second = coordination
    assert database._pool is not other_db._pool
    barrier = Barrier(2)
    def acquire(coordinator):
        barrier.wait()
        return coordinator.acquire('owner', 'worker')
    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(acquire, (first, second)))
    assert sum(handle is not None for handle in outcomes) == 1
    with database.transaction() as cursor:
        cursor.execute("select id from users where id='owner' for update")
        assert second.acquire('owner', 'different') is None
        assert second.acquire('other', 'different') is not None
        with pytest.raises(ValueError):
            second.acquire('missing', 'worker')


def test_renew_release_and_aba_generation(coordination):
    database, _, first, second = coordination
    lease = first.acquire('owner', 'worker')
    renewed = second.heartbeat(lease, 120)
    assert renewed.expires_at > lease.expires_at
    assert renewed.lease_token == lease.lease_token
    assert second.release(renewed)
    assert not first.release(lease)
    acquired = first.acquire('owner', 'worker')
    assert acquired.lease_version == lease.lease_version + 1
    assert acquired.lease_token != lease.lease_token
    with pytest.raises(MutationLeaseLost):
        first.heartbeat(lease)
    assert not first.release(lease)
    assert second.release(acquired)
    with database.transaction() as cursor:
        row = cursor.execute('select * from user_mutation_leases').fetchone()
        assert row['lease_version'] == acquired.lease_version


def test_expired_replaced_and_tampered_handles_never_write(coordination):
    database, _, first, second = coordination
    old = first.acquire('owner', 'worker')
    expire(database)
    with pytest.raises(MutationLeaseLost):
        first.heartbeat(old)
    assert not first.release(old)
    current = second.acquire('owner', 'worker')
    assert current.lease_version == old.lease_version + 1
    for invalid in (old, replace(current, user_id='other'), replace(current, owner='bad'),
                    replace(current, lease_token=uuid4()), replace(current, lease_version=1)):
        with pytest.raises(MutationLeaseLost):
            first.heartbeat(invalid)
        assert not first.release(invalid)
        with pytest.raises(MutationLeaseLost):
            with first.publication(invalid):
                pytest.fail('invalid lease yielded a cursor')
    assert second.heartbeat(current).lease_token == current.lease_token


@pytest.mark.parametrize('failure', ['expiry', 'error', 'tuple', 'disabled'])
def test_publication_postcheck_and_error_rollback(coordination, failure):
    database, other_db, first, _ = coordination
    lease = first.acquire('owner', 'worker')
    snapshots = PostgresSnapshotRepository(database)
    reader = PostgresSnapshotRepository(other_db)
    with pytest.raises((MutationLeaseLost, RuntimeError)):
        with first.publication(lease) as cursor:
            snapshots.compare_and_swap_in_transaction(cursor, 'owner', 'memory',
                {'user_id': 'owner', 'memories': []}, expected_version=0)
            assert reader.read('owner', 'memory') is None
            if failure == 'expiry':
                cursor.execute("update user_mutation_leases set lease_expires_at=clock_timestamp()-interval '1 second'")
            elif failure == 'tuple':
                cursor.execute('update user_mutation_leases set lease_version=lease_version+1')
            elif failure == 'disabled':
                cursor.execute("update users set status='disabled' where id='owner'")
            else:
                raise RuntimeError('abort')
    assert reader.read('owner', 'memory') is None
    with first.publication(lease) as cursor:
        snapshots.compare_and_swap_in_transaction(cursor, 'owner', 'memory',
            {'user_id': 'owner', 'memories': []}, expected_version=0)
    assert reader.read('owner', 'memory').version == 1
    with database.transaction() as cursor:
        assert cursor.execute('show transaction_isolation').fetchone()['transaction_isolation'] == 'read committed'


def test_expired_publication_rejected_before_body(coordination):
    database, _, first, _ = coordination
    lease = first.acquire('owner', 'worker')
    expire(database)
    with pytest.raises(MutationLeaseLost):
        with first.publication(lease):
            pytest.fail('expired publication entered')


@pytest.mark.parametrize('duration', [0, -1, True, 1.5, float('inf'), float('nan'), 86401, '60'])
def test_invalid_duration_has_no_writes(coordination, duration):
    database, _, first, _ = coordination
    with pytest.raises(ValueError):
        first.acquire('owner', 'worker', duration)
    lease = first.acquire('owner', 'worker')
    with pytest.raises(ValueError):
        first.heartbeat(lease, duration)
    with database.transaction() as cursor:
        row = cursor.execute('select * from user_mutation_leases').fetchone()
        assert row['lease_expires_at'] == lease.expires_at


def test_invalid_identity_and_disabled_user(coordination):
    database, _, first, _ = coordination
    for user, owner in [('', 'worker'), ('owner', ''), ('owner', '  '), (None, 'worker')]:
        with pytest.raises(ValueError):
            first.acquire(user, owner)
    with database.transaction() as cursor:
        cursor.execute("update users set status='disabled' where id='owner'")
    with pytest.raises(ValueError):
        first.acquire('owner', 'worker')
    with database.transaction() as cursor:
        assert cursor.execute('select count(*) as n from user_mutation_leases').fetchone()['n'] == 0


def test_caller_owned_acquisition_atomic_commit_and_rollback(coordination):
    database, other_db, first, second = coordination
    with database.connection() as connection:
        with connection.cursor() as cursor:
            with pytest.raises(ValueError, match='caller-owned'):
                first.acquire_in_transaction(cursor, 'owner', 'worker')
    with pytest.raises(RuntimeError, match='abort'):
        with database.transaction() as cursor:
            cursor.execute('begin')
            lease = first.acquire_in_transaction(cursor, 'owner', 'worker')
            assert lease.lease_version == 1
            assert second.acquire('owner', 'other') is None
            with other_db.transaction() as reader:
                assert reader.execute('select count(*) as n from user_mutation_leases').fetchone()['n'] == 0
            PostgresSnapshotRepository(database).compare_and_swap_in_transaction(
                cursor, 'owner', 'memory', {'user_id': 'owner', 'memories': []}, expected_version=0)
            raise RuntimeError('abort')
    assert PostgresSnapshotRepository(other_db).read('owner', 'memory') is None
    with other_db.transaction() as cursor:
        assert cursor.execute('select count(*) as n from user_mutation_leases').fetchone()['n'] == 0
    with database.transaction() as cursor:
        cursor.execute('begin')
        lease = first.acquire_in_transaction(cursor, 'owner', 'worker')
    assert second.heartbeat(lease).lease_token == lease.lease_token


@pytest.mark.parametrize('change', ['disabled', 'expired'])
@pytest.mark.parametrize('operation', ['heartbeat', 'release', 'publication'])
def test_waiter_refreshes_status_and_time_after_lock(coordination, monkeypatch, change, operation):
    database, other_db, first, second = coordination
    lease = first.acquire('owner', 'worker')
    entered = Event()
    backend = []
    original = second._lock_user
    def lock_user(cursor, user_id, **kwargs):
        backend.append(cursor.execute('select pg_backend_pid() as pid').fetchone()['pid'])
        entered.set()
        return original(cursor, user_id, **kwargs)
    monkeypatch.setattr(second, '_lock_user', lock_user)
    def action():
        if operation == 'publication':
            with second.publication(lease):
                pytest.fail('lost lease entered publication')
        else:
            return getattr(second, operation)(lease)
    with ThreadPoolExecutor(1) as pool:
        with database.transaction() as cursor:
            cursor.execute("select id from users where id='owner' for update")
            if change == 'disabled':
                cursor.execute("update users set status='disabled' where id='owner'")
            else:
                cursor.execute("update user_mutation_leases set lease_expires_at=clock_timestamp()-interval '1 second'")
            future = pool.submit(action)
            assert entered.wait(3)
            # Observe the actual server lock wait. The local monotonic clock
            # only bounds test polling; lease decisions never use it.
            deadline = time.monotonic() + 3
            while True:
                with other_db.transaction() as observer:
                    waiting = observer.execute('select wait_event_type from pg_stat_activity where pid=%s',
                                               (backend[0],)).fetchone()
                if waiting['wait_event_type'] == 'Lock':
                    break
                assert time.monotonic() < deadline
                time.sleep(.01)
        if operation == 'release':
            assert future.result(timeout=3) is False
        else:
            with pytest.raises(MutationLeaseLost):
                future.result(timeout=3)


def test_non_read_committed_is_rejected_and_pool_default_preserved(coordination):
    from psycopg import IsolationLevel
    database, _, _, _ = coordination
    isolated = PostgresDatabase(database._pool.conninfo, min_size=1, max_size=1)
    isolated.open()
    try:
        coordinator = PostgresUserMutationCoordinator(isolated)
        lease = coordinator.acquire('owner', 'worker')
        with isolated.connection() as connection:
            connection.isolation_level = IsolationLevel.REPEATABLE_READ
        for operation in ('acquire', 'heartbeat', 'release', 'publication'):
            with pytest.raises(ValueError, match='READ COMMITTED'):
                if operation == 'acquire':
                    coordinator.acquire('owner', 'worker')
                elif operation == 'publication':
                    with coordinator.publication(lease):
                        pytest.fail('unsupported isolation entered publication')
                else:
                    getattr(coordinator, operation)(lease)
        with isolated.connection() as connection:
            assert connection.isolation_level == IsolationLevel.REPEATABLE_READ
    finally:
        isolated.close()


def test_lease_expiry_uses_database_clock(coordination, monkeypatch):
    import app.postgres_coordination as module
    class NoClientClock:
        @staticmethod
        def now(*args, **kwargs):
            pytest.fail('application wall clock used for lease')
        utcnow = now
    monkeypatch.setattr(module, 'datetime', NoClientClock)
    database, _, first, _ = coordination
    before = database.server_now()
    lease = first.acquire('owner', 'worker', 60)
    after = database.server_now()
    assert before + timedelta(seconds=60) <= lease.expires_at <= after + timedelta(seconds=60)
    before = database.server_now()
    renewed = first.heartbeat(lease, 120)
    after = database.server_now()
    assert before + timedelta(seconds=120) <= renewed.expires_at <= after + timedelta(seconds=120)


def test_migration_constraints_cascade_and_unsafe_downgrade(coordination):
    import importlib
    from psycopg.errors import CheckViolation, ForeignKeyViolation
    database, _, first, _ = coordination
    lease = first.acquire('owner', 'worker')
    for statement in ("update user_mutation_leases set owner=' '",
                      'update user_mutation_leases set lease_version=0'):
        with pytest.raises(CheckViolation):
            with database.transaction() as cursor:
                cursor.execute(statement)
    with pytest.raises(ForeignKeyViolation):
        with database.transaction() as cursor:
            cursor.execute("update user_mutation_leases set user_id='missing'")
    migration = importlib.import_module('migrations.versions.20260928_07_user_mutation_leases')
    assert migration.down_revision == '20260927_06'
    with pytest.raises(RuntimeError, match='recovery'):
        migration.downgrade()
    assert first.heartbeat(lease).lease_version == lease.lease_version
    with database.transaction() as cursor:
        cursor.execute("delete from users where id='owner'")
        assert cursor.execute('select count(*) as n from user_mutation_leases').fetchone()['n'] == 0


def test_autocommit_pool_fails_closed_without_explicit_transaction(coordination):
    database, _, _, _ = coordination
    isolated = PostgresDatabase(database._pool.conninfo, min_size=1, max_size=1)
    isolated.open()
    try:
        coordinator = PostgresUserMutationCoordinator(isolated)
        lease = coordinator.acquire('owner', 'worker')
        with isolated.connection() as connection:
            connection.autocommit = True
        with pytest.raises(ValueError, match='transaction'):
            with coordinator.publication(lease):
                pytest.fail('autocommit publication is unsafe')
    finally:
        isolated.close()


def test_caller_owned_live_heartbeat_release_are_atomic(coordination):
    database, other_db, first, second = coordination
    lease = first.acquire('owner','worker')
    for method in (first.require_live_in_transaction, first.heartbeat_in_transaction, first.release_in_transaction):
        with database.connection() as connection:
            with connection.cursor() as cursor:
                with pytest.raises(ValueError,match='transaction'):
                    method(cursor,lease)
    with pytest.raises(RuntimeError,match='abort'):
        with database.transaction() as cursor:
            cursor.execute('begin')
            assert first.require_live_in_transaction(cursor,lease)['lease_token']==lease.lease_token
            renewed=first.heartbeat_in_transaction(cursor,lease,120)
            assert renewed.expires_at>lease.expires_at
            assert first.release_in_transaction(cursor,renewed)
            with other_db.transaction() as reader:
                assert reader.execute('select lease_expires_at from user_mutation_leases').fetchone()['lease_expires_at']==lease.expires_at
            raise RuntimeError('abort')
    with database.transaction() as cursor:
        assert cursor.execute('select lease_expires_at from user_mutation_leases').fetchone()['lease_expires_at']==lease.expires_at
    with database.transaction() as cursor:
        cursor.execute('begin')
        assert first.release_in_transaction(cursor,lease)
    with pytest.raises(MutationLeaseLost):
        with database.transaction() as cursor:
            cursor.execute('begin')
            first.release_in_transaction(cursor,lease)
    assert second.release(lease) is False
