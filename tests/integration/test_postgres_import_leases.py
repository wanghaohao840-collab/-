"""Real database authority for staged import attempts."""
import io
from dataclasses import replace
from uuid import uuid4

import pytest
import psycopg

from app.postgres_import_leases import ImportLeaseLost, PostgresImportLeaseRepository
from app.postgres_import_artifacts import PostgresImportArtifactService
from app.import_uploads import ImportUpload
from tests.integration.test_postgres_import_artifacts import fixture
from tests.integration.test_postgres_auth_sessions import shared_database
from tests.integration.test_s3_object_store import store


def submit(fixture, user=None, count=1):
    db, _, store, owner, _, _ = fixture
    return PostgresImportArtifactService(db, store).submit_uploads(
        user or owner, [ImportUpload(f'{i}.txt', io.BytesIO(b'source')) for i in range(count)])


def test_claim_complete_atomic_publication(fixture):
    db, other, store, owner, _, _ = fixture
    submit(fixture)
    repo = PostgresImportLeaseRepository(db)
    attempt = repo.claim_next('worker')
    assert attempt.task.user_id == owner
    assert attempt.lease_version == 1
    assert store.read_verified(owner, attempt.source) == b'source'
    assert repo.try_begin_committing(attempt)
    def publish(cursor):
        cursor.execute("update users set updated_at='published' where id=%s", (owner,))
        with other.transaction() as reader:
            assert reader.execute('select updated_at from users where id=%s', (owner,)).fetchone()['updated_at'] != 'published'
    done = repo.complete(attempt, publish)
    assert (done.status, done.progress) == ('succeeded', 100)
    with db.transaction() as cursor:
        assert cursor.execute('select end_reason from import_task_attempts').fetchone()['end_reason'] == 'succeeded'
        assert not cursor.execute('select lease_expires_at>clock_timestamp() as live from user_mutation_leases').fetchone()['live']
    with pytest.raises(ImportLeaseLost):
        repo.heartbeat(attempt)

def expire(db, authority='task'):
    with db.transaction() as cursor:
        table = 'import_tasks' if authority=='task' else 'user_mutation_leases'
        cursor.execute(f"update {table} set lease_expires_at=clock_timestamp()-interval '1 second'")


def test_concurrent_claim_fairness_busy_and_lock_skip(fixture):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.postgres_coordination import PostgresUserMutationCoordinator
    db, other, _, owner, second_user, _ = fixture
    submit(fixture, count=2)
    first, second = PostgresImportLeaseRepository(db), PostgresImportLeaseRepository(other)
    barrier = Barrier(2)
    def claim(repo):
        barrier.wait()
        return repo.claim_next('worker')
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(claim,(first,second)))
    assert sum(x is not None for x in results)==1
    attempt = next(x for x in results if x)
    submit(fixture, second_user)
    first.release_unstarted(attempt)
    next_attempt = second.claim_next('worker')
    assert next_attempt.task.user_id==second_user
    second.release_unstarted(next_attempt)
    # Every locked/busy candidate is skipped, with no candidate cap.
    with db.transaction() as cursor:
        cursor.execute('select id from users where id=%s for update',(owner,))
        assert second.claim_next('worker').task.user_id==second_user
    lease = PostgresUserMutationCoordinator(db).acquire(owner,'different-mutation')
    assert first.claim_next('worker') is None
    expire(db,'user')
    assert first.claim_next('worker').task.user_id==owner


@pytest.mark.parametrize('authority',['task','user'])
def test_expiry_recovery_aba_and_all_old_operations(fixture,authority):
    db, _, _, owner, _, _ = fixture
    submit(fixture)
    repo = PostgresImportLeaseRepository(db)
    old = repo.claim_next('same-worker')
    expire(db,authority)
    operations = [lambda a:repo.heartbeat(a),lambda a:repo.update_progress(a,'parsing',2),
        repo.try_begin_committing,lambda a:repo.complete(a,lambda c:pytest.fail('stale callback')),
        lambda a:repo.fail(a,'safe','summary'),repo.cancel,repo.release_unstarted]
    for operation in operations:
        with pytest.raises(ImportLeaseLost): operation(old)
    assert repo.recover_expired()==1
    assert repo.recover_expired()==0
    current = repo.claim_next('same-worker')
    assert current.lease_version==old.lease_version+1
    assert current.lease_token!=old.lease_token
    assert current.task.total_attempt_count==2 and current.task.auto_retry_count==0
    for operation in operations:
        with pytest.raises(ImportLeaseLost): operation(old)
    assert repo.recover_expired()==0
    with db.transaction() as cursor:
        assert cursor.execute('select end_reason from import_task_attempts where lease_version=1').fetchone()['end_reason']=='lease_expired'


@pytest.mark.parametrize('failure',['error','user_expiry','task_expiry','task_token','user_token','audit_token','disabled','stage','status'])
def test_callback_rollback_full_tuple_and_clock(fixture,failure):
    db, other, _, owner, _, _ = fixture
    submit(fixture)
    repo = PostgresImportLeaseRepository(db)
    attempt = repo.claim_next('worker')
    assert repo.try_begin_committing(attempt)
    def publish(cursor):
        cursor.execute("update users set updated_at='published' where id=%s",(owner,))
        if failure=='error': raise RuntimeError('abort')
        statements = {
            'user_expiry':"update user_mutation_leases set lease_expires_at=clock_timestamp()-interval '1 second'",
            'task_expiry':"update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second'",
            'task_token':"update import_tasks set lease_token=gen_random_uuid()",
            'user_token':"update user_mutation_leases set lease_token=gen_random_uuid()",
            'audit_token':"update import_task_attempts set lease_token=gen_random_uuid()",
            'disabled':"update users set status='disabled'",
            'stage':"update import_tasks set stage='parsing'",
            'status':"update import_tasks set status='queued'",
        }
        cursor.execute(statements[failure])
    expected = (ImportLeaseLost, RuntimeError, psycopg.errors.RaiseException) if failure == 'audit_token' else (ImportLeaseLost, RuntimeError)
    with pytest.raises(expected): repo.complete(attempt,publish)
    with other.transaction() as cursor:
        assert cursor.execute('select updated_at from users where id=%s',(owner,)).fetchone()['updated_at']!='published'
        assert cursor.execute('select status from import_tasks').fetchone()['status']=='running'
        assert cursor.execute('select ended_at from import_task_attempts').fetchone()['ended_at'] is None
    assert repo.heartbeat(attempt).lease_token==attempt.lease_token


def test_malformed_crossuser_and_renewed_handles(fixture):
    db, _, _, owner, other, _ = fixture
    submit(fixture)
    repo = PostgresImportLeaseRepository(db)
    attempt = repo.claim_next('worker')
    invalid = [None,replace(attempt,worker_id='wrong'),replace(attempt,lease_token=uuid4()),
        replace(attempt,lease_version=0),replace(attempt,lease_version=True),
        replace(attempt,lease_token=str(attempt.lease_token)),
        replace(attempt,task=replace(attempt.task,user_id=other)),
        replace(attempt,task=replace(attempt.task,task_id=str(uuid4()))),
        replace(attempt,user_lease=replace(attempt.user_lease,lease_version=99)),
        replace(attempt,user_lease=replace(attempt.user_lease,lease_token=uuid4()))]
    for bad in invalid:
        with pytest.raises(ImportLeaseLost): repo.heartbeat(bad)
    renewed = repo.heartbeat(attempt,120)
    assert renewed.expires_at>attempt.expires_at
    assert renewed.user_lease.expires_at>attempt.user_lease.expires_at
    assert renewed.lease_token==attempt.lease_token
    assert repo.update_progress(attempt,'parsing',10).progress==10
    with pytest.raises(ValueError): repo.release_unstarted(attempt)
    for stage,progress in [('succeeded',10),('queued',0),('parsing',True),('parsing',1.5),('parsing',101),('committing',5)]:
        with pytest.raises(ValueError): repo.update_progress(attempt,stage,progress)
    assert repo.try_begin_committing(attempt)
    assert not repo.try_begin_committing(attempt)
    with pytest.raises(ValueError): repo.update_progress(attempt,'embedding',30)
    assert repo.update_progress(attempt,'committing',70).progress==70


def test_heartbeat_atomic_rollback(fixture):
    db, _, _, _, _, _ = fixture
    submit(fixture)
    repo = PostgresImportLeaseRepository(db)
    attempt = repo.claim_next('worker')
    with db.transaction() as cursor:
        cursor.execute('''create function expire_task_after_user_renewal() returns trigger language plpgsql as $$
            begin update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second'; return new; end $$''')
        cursor.execute('''create trigger expire_task after update on user_mutation_leases
            for each row execute function expire_task_after_user_renewal()''')
    with pytest.raises(ImportLeaseLost): repo.heartbeat(attempt,120)
    with db.transaction() as cursor:
        assert cursor.execute('select lease_expires_at from user_mutation_leases').fetchone()['lease_expires_at']==attempt.user_lease.expires_at
        assert cursor.execute('select lease_expires_at from import_tasks').fetchone()['lease_expires_at']==attempt.expires_at
        cursor.execute('drop trigger expire_task on user_mutation_leases')
    assert repo.heartbeat(attempt)


def test_retry_db_clock_limits_manual_retry_and_unstarted(fixture):
    from datetime import timedelta,timezone
    from app.import_repository import PostgresImportTaskRepository
    db, _, _, owner, _, _ = fixture
    submit(fixture)
    repo = PostgresImportLeaseRepository(db)
    attempt = repo.claim_next('worker')
    assert repo.release_unstarted(attempt).total_attempt_count==0
    attempt = repo.claim_next('worker')
    assert attempt.lease_version==2 and attempt.task.total_attempt_count==1
    before = db.server_now()
    failed = repo.fail(attempt,'network','token=secret /private/path ' + 'x'*1000,retry_delay_seconds=60)
    after = db.server_now()
    from datetime import datetime
    assert before+timedelta(seconds=60)<=datetime.fromisoformat(failed.next_attempt_at)<=after+timedelta(seconds=60)
    assert failed.status=='retry_wait' and failed.auto_retry_count==1 and len(failed.error_summary)<=500
    assert repo.claim_next('worker') is None
    # The positive offset would be lexically in the future, but is actually due.
    with db.transaction() as cursor:
        due = (db.server_now()-timedelta(seconds=1)).astimezone(timezone(timedelta(hours=12))).isoformat()
        cursor.execute('update import_tasks set next_attempt_at=%s,max_auto_retries=1',(due,))
    attempt=repo.claim_next('worker')
    assert attempt.task.auto_retry_count==1
    failed=repo.fail(attempt,'network','summary',0)
    assert failed.status=='failed' and failed.auto_retry_count==1 and failed.total_attempt_count==2
    control=PostgresImportTaskRepository(db)
    retried=control.retry_task(owner,failed.task_id)
    assert retried.manual_retry_count==1 and retried.auto_retry_count==0
    attempt=repo.claim_next('worker')
    assert attempt.lease_version==4 and attempt.task.total_attempt_count==3
    for duration in [-1,True,1.5,float('nan'),float('inf'),86401]:
        with pytest.raises(ValueError): repo.fail(attempt,'code','summary',duration)


def test_cancel_vs_committing_and_recovery_preserves_request(fixture):
    from app.import_repository import PostgresImportTaskRepository
    db, _, _, owner, _, _=fixture
    submit(fixture,count=2)
    repo=PostgresImportLeaseRepository(db)
    control=PostgresImportTaskRepository(db)
    attempt=repo.claim_next('worker')
    with pytest.raises(ValueError): repo.cancel(attempt)
    decision=control.request_cancel(owner,attempt.task.batch_id,attempt.task.task_id)
    assert decision.outcome=='cancel_requested'
    assert not repo.try_begin_committing(attempt)
    expire(db)
    assert repo.recover_expired()==1
    recovered=repo.claim_next('worker')
    assert recovered.task.task_id==attempt.task.task_id
    assert recovered.task.cancel_requested_at
    assert repo.cancel(recovered).status=='cancelled'
    attempt=repo.claim_next('worker')
    assert repo.try_begin_committing(attempt)
    assert control.request_cancel(owner,attempt.task.batch_id,attempt.task.task_id).outcome=='not_cancellable'
    with pytest.raises(ValueError): repo.cancel(attempt)
    assert repo.complete(attempt,lambda c:None).status=='succeeded'


def test_no_source_legacy_disabled_and_constraint(fixture):
    from psycopg.errors import UniqueViolation
    db, _, _, owner, other, disabled=fixture
    tasks=submit(fixture,count=2).tasks
    repo=PostgresImportLeaseRepository(db)
    with db.transaction() as cursor:
        cursor.execute('delete from import_objects')
    assert repo.claim_next('worker') is None
    submit(fixture,other)
    with db.transaction() as cursor:
        cursor.execute("update users set status='disabled' where id=%s",(other,))
        cursor.execute("update import_tasks set status='running' where id=%s",(tasks[0].task_id,))
    assert repo.claim_next('worker') is None
    assert repo.recover_expired()==0
    with pytest.raises(UniqueViolation):
        with db.transaction() as cursor:
            cursor.execute("update import_tasks set status='running' where id=%s",(tasks[1].task_id,))


def test_recovery_does_not_release_newer_mutation_lease(fixture):
    from app.postgres_coordination import PostgresUserMutationCoordinator
    db, _, _, owner, _, _=fixture
    submit(fixture)
    repo=PostgresImportLeaseRepository(db)
    old=repo.claim_next('worker')
    expire(db,'user')
    coordinator=PostgresUserMutationCoordinator(db)
    newer=coordinator.acquire(owner,'another-kind')
    assert newer.lease_version>old.user_lease.lease_version
    assert repo.recover_expired()==1
    assert coordinator.heartbeat(newer)
    assert repo.claim_next('worker') is None


def test_lock_wait_rechecks_task_clock(fixture,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import time
    db,other,_,_,_,_=fixture
    submit(fixture)
    repo=PostgresImportLeaseRepository(db)
    repo2=PostgresImportLeaseRepository(other)
    attempt=repo.claim_next('worker')
    original=repo2.coordinator.require_live_in_transaction
    entered=Event()
    backend=[]
    def observed(cursor,handle):
        backend.append(cursor.connection.info.backend_pid)
        entered.set()
        return original(cursor,handle)
    monkeypatch.setattr(repo2.coordinator,'require_live_in_transaction',observed)
    with ThreadPoolExecutor(1) as pool:
        with db.transaction() as cursor:
            cursor.execute('select id from import_tasks where id=%s for update',(attempt.task.task_id,))
            cursor.execute("update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second'")
            future=pool.submit(repo2.update_progress,attempt,'parsing',10)
            assert entered.wait(3)
            deadline=time.monotonic()+3
            while True:
                with db.transaction() as observer:
                    waiting=observer.execute('select wait_event_type from pg_stat_activity where pid=%s',(backend[0],)).fetchone()
                if waiting['wait_event_type']=='Lock': break
                assert time.monotonic()<deadline
                time.sleep(.01)
        with pytest.raises(ImportLeaseLost): future.result(timeout=3)

@pytest.mark.parametrize('operation',['complete','fail','retry','cancel','release_unstarted'])
def test_terminal_release_task_expiry_rolls_back_everything(fixture,operation):
    from app.import_repository import PostgresImportTaskRepository
    db,_,_,owner,_,_=fixture
    submit(fixture)
    repo=PostgresImportLeaseRepository(db)
    attempt=repo.claim_next('worker')
    if operation=='complete': assert repo.try_begin_committing(attempt)
    if operation=='cancel':
        PostgresImportTaskRepository(db).request_cancel(owner,attempt.task.batch_id,attempt.task.task_id)
    with db.transaction() as cursor:
        before=cursor.execute('select * from import_tasks').fetchone()
        cursor.execute('''create function expire_during_user_release() returns trigger language plpgsql as $$
            begin update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second'; return new; end $$''')
        cursor.execute('''create trigger expire_during_release after update on user_mutation_leases
            for each row execute function expire_during_user_release()''')
    def publish(cursor): cursor.execute("update users set updated_at='published' where id=%s",(owner,))
    actions={'complete':lambda:repo.complete(attempt,publish),'fail':lambda:repo.fail(attempt,'safe','summary'),
        'retry':lambda:repo.fail(attempt,'safe','summary',0),'cancel':lambda:repo.cancel(attempt),
        'release_unstarted':lambda:repo.release_unstarted(attempt)}
    with pytest.raises(ImportLeaseLost,match='during user lease release'): actions[operation]()
    with db.transaction() as cursor:
        assert cursor.execute('select * from import_tasks').fetchone()==before
        assert cursor.execute('select ended_at from import_task_attempts').fetchone()['ended_at'] is None
        assert cursor.execute('select lease_expires_at from user_mutation_leases').fetchone()['lease_expires_at']==attempt.user_lease.expires_at
        assert cursor.execute('select updated_at from users where id=%s',(owner,)).fetchone()['updated_at']!='published'


@pytest.mark.parametrize('winner',['cancel','committing'])
def test_cancel_committing_race_serializes_on_user(fixture,monkeypatch,winner):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import time
    from app.import_persistence import ImportStore
    from app.import_repository import PostgresImportTaskRepository
    db,other,_,owner,_,_=fixture
    submit(fixture)
    repo=PostgresImportLeaseRepository(db)
    control=PostgresImportTaskRepository(other)
    attempt=repo.claim_next('worker')
    entered,allow=Event(),Event()
    backend=[]
    if winner=='committing':
        original_touch=repo._touch
        def pause_touch(cursor,row):
            original_touch(cursor,row)
            entered.set()
            assert allow.wait(10)
        monkeypatch.setattr(repo,'_touch',pause_touch)
        original_lock=control._imports.lock_user
        def observe_control_lock(cursor,user_id):
            backend.append(cursor.connection.info.backend_pid)
            return original_lock(cursor,user_id)
        monkeypatch.setattr(control._imports,'lock_user',observe_control_lock)
    else:
        original_touch=ImportStore.touch_batch
        def pause_control_touch(store,*args):
            original_touch(store,*args)
            entered.set()
            assert allow.wait(10)
        monkeypatch.setattr(ImportStore,'touch_batch',pause_control_touch)
        original_live=repo.coordinator.require_live_in_transaction
        def observe_attempt_lock(cursor,handle):
            backend.append(cursor.connection.info.backend_pid)
            return original_live(cursor,handle)
        monkeypatch.setattr(repo.coordinator,'require_live_in_transaction',observe_attempt_lock)
    actions={
        'committing':lambda:repo.try_begin_committing(attempt),
        'cancel':lambda:control.request_cancel(owner,attempt.task.batch_id,attempt.task.task_id).outcome,
    }
    with ThreadPoolExecutor(2) as pool:
        leading=pool.submit(actions[winner])
        try:
            assert entered.wait(5)
            following=pool.submit(actions['cancel' if winner=='committing' else 'committing'])
            deadline=time.monotonic()+5
            while True:
                if backend:
                    with db.transaction() as observer:
                        waiting=observer.execute('select wait_event_type from pg_stat_activity where pid=%s',(backend[0],)).fetchone()
                    if waiting['wait_event_type']=='Lock': break
                assert time.monotonic()<deadline
                time.sleep(.01)
        finally:
            allow.set()
        leading_result=leading.result(timeout=5)
        trailing_result=following.result(timeout=5)
    assert leading_result==(True if winner=='committing' else 'cancel_requested')
    assert trailing_result==('not_cancellable' if winner=='committing' else False)


@pytest.mark.parametrize('shared_database',['20260928_08'],indirect=True)
def test_migration_preserves_legacy_rows_and_audit_constraints(fixture):
    import importlib
    from alembic import command
    from alembic.config import Config
    from psycopg.errors import CheckViolation,UniqueViolation,RaiseException
    db,_,_,owner,other,_=fixture
    task=submit(fixture).tasks[0]
    with db.transaction() as cursor:
        cursor.execute("update import_tasks set status='running',total_attempt_count=7 where id=%s",(task.task_id,))
    command.upgrade(Config('alembic.ini'),'head')
    command.upgrade(Config('alembic.ini'),'head')
    repo=PostgresImportLeaseRepository(db)
    assert repo.recover_expired()==0 and repo.claim_next('worker') is None
    with db.transaction() as cursor:
        row=cursor.execute('select * from import_tasks').fetchone()
        assert row['lease_version']==0 and row['total_attempt_count']==7 and row['lease_token'] is None
        cursor.execute("update import_tasks set status='queued'")
    attempt=repo.claim_next('worker')
    with db.transaction() as cursor:
        audit_before = cursor.execute('''select * from import_task_attempts
            where task_id=%s and lease_version=%s''',
            (attempt.task.task_id,attempt.lease_version)).fetchone()
    for statement,error,message in [
        ('update import_tasks set lease_version=-1',CheckViolation,None),
        ('update import_task_attempts set lease_version=0',RaiseException,
         'Import audit identity is immutable'),
        ("update import_task_attempts set worker_id=' '",RaiseException,
         'Import audit identity is immutable'),
        ("update import_task_attempts set user_id='missing'",RaiseException,
         'Import audit identity is immutable'),
        ('insert into import_task_attempts select * from import_task_attempts',
         UniqueViolation,None),
        ("update import_task_attempts set error_summary=repeat('x',501)",
         CheckViolation,None),
        ("update import_task_attempts set end_reason='ended'",RaiseException,
         'Import audit end fields must agree'),
    ]:
        with pytest.raises(error, match=message):
            with db.transaction() as cursor: cursor.execute(statement)
        with db.transaction() as cursor:
            assert cursor.execute('''select * from import_task_attempts
                where task_id=%s and lease_version=%s''',
                (attempt.task.task_id,attempt.lease_version)).fetchone() == audit_before
    with pytest.raises(RaiseException,match='Import audit identity is immutable'):
        with db.transaction() as cursor:
            cursor.execute('update import_task_attempts set user_id=%s',(other,))
    with db.transaction() as cursor:
        assert cursor.execute('''select * from import_task_attempts
            where task_id=%s and lease_version=%s''',
            (attempt.task.task_id,attempt.lease_version)).fetchone() == audit_before
    migration=importlib.import_module('migrations.versions.20260928_09_import_leases')
    assert migration.down_revision=='20260928_08'
    with pytest.raises(RuntimeError,match='recovery'): migration.downgrade()
    assert repo.heartbeat(attempt).lease_version==1


def test_joint_clock_validation_after_user_check(fixture,monkeypatch):
    db,_,_,_,_,_=fixture
    submit(fixture)
    repo=PostgresImportLeaseRepository(db)
    attempt=repo.claim_next('worker')
    original=repo.coordinator.require_live_in_transaction
    calls=0
    def invalidate_after_final_user_check(cursor,handle):
        nonlocal calls
        result=original(cursor,handle)
        calls+=1
        if calls==4:
            cursor.execute("update user_mutation_leases set lease_expires_at=clock_timestamp()-interval '1 second'")
        return result
    monkeypatch.setattr(repo.coordinator,'require_live_in_transaction',invalidate_after_final_user_check)
    with pytest.raises(ImportLeaseLost):
        repo.update_progress(attempt,'parsing',10)
    with db.transaction() as cursor:
        assert cursor.execute('select stage from import_tasks').fetchone()['stage']=='queued'
        assert cursor.execute('select lease_expires_at from user_mutation_leases').fetchone()['lease_expires_at']==attempt.user_lease.expires_at


@pytest.mark.parametrize('mode',['autocommit','repeatable_read','serializable'])
@pytest.mark.parametrize('bad_transaction',[1,2],ids=['scan','candidate'])
def test_recovery_rejects_unsupported_transactions_without_writes(fixture,monkeypatch,mode,bad_transaction):
    from contextlib import contextmanager
    from psycopg import IsolationLevel
    from app.postgres import PostgresDatabase
    db,_,_,_,_,_=fixture
    submit(fixture)
    repo=PostgresImportLeaseRepository(db)
    repo.claim_next('worker')
    expire(db)
    tables=('import_tasks','import_task_attempts','user_mutation_leases','import_batches','import_user_schedule')
    def snapshot():
        with db.transaction() as cursor:
            return [cursor.execute(f'select * from {table}').fetchall() for table in tables]
    before=snapshot()
    isolated=PostgresDatabase(db._pool.conninfo,min_size=1,max_size=1)
    isolated.open()
    original_transaction=isolated.transaction
    calls=0
    @contextmanager
    def selected_mode():
        nonlocal calls
        calls+=1
        if calls==bad_transaction:
            with isolated.connection() as connection:
                if mode=='autocommit': connection.autocommit=True
                else: connection.isolation_level=getattr(IsolationLevel,mode.upper())
        with original_transaction() as cursor:
            yield cursor
    monkeypatch.setattr(isolated,'transaction',selected_mode)
    try:
        with pytest.raises(ValueError,match='transaction|READ COMMITTED'):
            PostgresImportLeaseRepository(isolated).recover_expired()
        assert calls==bad_transaction
        assert snapshot()==before
    finally:
        isolated.close()
