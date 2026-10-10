"""Restart recovery of an exact import publication attempt on disposable services."""

from __future__ import annotations

from copy import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import importlib
from threading import Event
import time
from uuid import uuid4

import pytest
import psycopg
from psycopg import IsolationLevel
from sqlalchemy.exc import DBAPIError
from alembic import command
from alembic.config import Config

from app.import_publication_evidence import (
    AttemptKey, PostgresImportPublicationEvidenceRepository, PublicationEvidenceError,
    encode_intent, _canonical, _validate_stored_slots,
)
import app.import_memory_publication as live_module
from app.import_memory_publication import (
    DurablePublicationUnknown, ImportMemoryPublicationError,
    ImportMemoryPublicationService,
)
from app.import_models import ImportTaskCreate
from app.import_persistence import ImportPersistence, ImportStore
from app.import_repository import PostgresImportTaskRepository
from app.history import EMPTY_HISTORY
from app.import_publication_recovery import (
    PostgresImportPublicationRecoveryRepository, RecoveryLeaseLost,
    RecoveryUnavailable, seed_missing_queue_in_transaction,
)
from app.import_publication_proof import (
    ImportPublicationProofService, PublicationProofUnknown,
)
from app.postgres_import_leases import ImportLeaseLost, PostgresImportLeaseRepository
from app.postgres_coordination import PostgresUserMutationCoordinator
from app.postgres_vector_generations import VectorAuthorityError, VectorScope
from app.postgres_snapshots import PostgresSnapshotRepository
from tests.integration.test_import_document_publication import _point, _task, publication
from tests.integration.test_import_memory_publication import memory_publication
from tests.integration.test_import_publication_proof import _issue
from tests.integration.test_postgres_auth_sessions import shared_database
from tests.integration.test_published_episode_reads import _publish as publish_episode_baseline
from tests.integration.test_s3_object_store import store


def test_fresh_16_upgrade_has_separate_source_isolation_guard(shared_database):
    open_pool, _ = shared_database
    db = open_pool()
    with db.transaction() as cursor:
        assert cursor.execute('select version_num from alembic_version').fetchone()[
            'version_num'] == '20261007_16'
        definitions = cursor.execute('''select tgname,
            pg_get_triggerdef(oid) as definition from pg_trigger
            where tgrelid='import_objects'::regclass and not tgisinternal
            order by tgname''').fetchall()
        by_name = {row['tgname']: row['definition'] for row in definitions}
        assert 'import_object_dependency_guard_15' in by_name
        assert 'aa_import_object_isolation_guard_16' in by_name
        assert 'BEFORE DELETE OR UPDATE' in by_name[
            'aa_import_object_isolation_guard_16']
        assert 'publication_dependency_isolation_guard_16()' in by_name[
            'aa_import_object_isolation_guard_16']
        audit = cursor.execute('''select tgname,pg_get_triggerdef(oid) as definition
            from pg_trigger where tgrelid='import_task_attempts'::regclass
            and not tgisinternal''').fetchall()
        audit_by_name = {row['tgname']: row['definition'] for row in audit}
        assert 'import_attempt_dependency_guard_15' in audit_by_name
        assert 'aa_import_attempt_isolation_guard_16' in audit_by_name
        assert 'BEFORE DELETE OR UPDATE' in audit_by_name[
            'aa_import_attempt_isolation_guard_16']
        assert 'publication_dependency_isolation_guard_16()' in audit_by_name[
            'aa_import_attempt_isolation_guard_16']
        function = cursor.execute('''select pg_get_functiondef(
            'publication_dependency_isolation_guard_16()'::regprocedure)
            as definition''').fetchone()['definition']
        assert "current_setting('transaction_isolation')" in function
        assert "'read committed'" in function


@pytest.mark.parametrize('shared_database', ['20261002_15'], indirect=True)
@pytest.mark.parametrize('phase', ['intent', 'terminal_committed'],
                         ids=['intent', 'terminal_committed'])
def test_populated_15_to_16_preserves_source_and_recovery_bytes(
        shared_database, memory_publication, phase):
    service, db, store, _, _, rag_scope, _, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    live = service._issue_live_publication(rag_scope, attempt,
        [_point(rag_scope, attempt.task.document_id, 'migration-preservation')],
        event_vector=[1., 0., 0., 0.], event_profile=profile)
    assert live is not None
    if phase == 'terminal_committed':
        result = service._execute_live_publication(live)
        assert result.proof.phase == 'terminal_committed'
    tables = ('import_objects', 'import_task_attempts', 'import_tasks',
              'import_batches', 'import_publication_evidence',
              'import_publication_private_payloads',
              'import_publication_recovery_queue', 'user_publication_gates',
              'generation_reservations', 'document_objects',
              'history_document_witnesses', 'vector_indexes',
              'vector_generations', 'vector_heads', 'user_snapshots',
              'memory_documents')
    def snapshot_rows(cursor):
        return {table: sorted(cursor.execute(f'select * from {table}').fetchall(),
                              key=lambda row: repr(sorted(row.items())))
                for table in tables}
    with db.transaction() as cursor:
        before = snapshot_rows(cursor)
        header = before['import_publication_evidence'][0]
        assert header['phase'] == phase
        if phase == 'terminal_committed':
            assert before['import_publication_private_payloads'][0]['terminal_slot']
            assert any(row['state'] == 'published'
                       for row in before['vector_generations'])
        assert cursor.execute('select version_num from alembic_version').fetchone()[
            'version_num'] == '20261002_15'
    command.upgrade(Config('alembic.ini'), '20261007_16')
    command.upgrade(Config('alembic.ini'), 'head')
    with db.transaction() as cursor:
        after = snapshot_rows(cursor)
        assert after == before
        assert cursor.execute('select version_num from alembic_version').fetchone()[
            'version_num'] == '20261007_16'
        assert cursor.execute('''select 1 from pg_trigger where tgname =
            'aa_import_object_isolation_guard_16' and not tgisinternal''').fetchone()
    migration = importlib.import_module(
        'migrations.versions.20261007_16_publication_dependency_isolation')
    with pytest.raises(RuntimeError, match='cannot be removed'):
        migration.downgrade()


@pytest.mark.parametrize('shared_database', ['20261002_15'], indirect=True)
def test_15_to_16_failed_second_trigger_rolls_back_transactional_ddl(
        shared_database, memory_publication):
    _, db, store, _, _, _, _, _, user, _ = memory_publication
    attempt = _task(db, store, user)
    with db.transaction() as cursor:
        cursor.execute('''create trigger aa_import_attempt_isolation_guard_16
            before update on import_task_attempts for each row
            execute function import_attempt_dependency_guard_15()''')
        original = cursor.execute('select * from import_objects where task_id=%s',
                                  (attempt.task.task_id,)).fetchone()
    with pytest.raises(DBAPIError, match='aa_import_attempt_isolation_guard_16'):
        command.upgrade(Config('alembic.ini'), '20261007_16')
    with db.transaction() as cursor:
        assert cursor.execute('select version_num from alembic_version').fetchone()[
            'version_num'] == '20261002_15'
        assert cursor.execute('''select to_regprocedure(
            'publication_dependency_isolation_guard_16()') as name''').fetchone()[
            'name'] is None
        assert cursor.execute('''select 1 from pg_trigger where tgname=
            'aa_import_object_isolation_guard_16' and not tgisinternal
            and tgrelid='import_objects'::regclass''').fetchone() is None
        assert cursor.execute('select * from import_objects where task_id=%s',
                              (attempt.task.task_id,)).fetchone() == original


@pytest.mark.parametrize('isolation', ['repeatable read', 'serializable',
                                       'read uncommitted'])
def test_source_mutation_requires_read_committed_even_without_gate(
        memory_publication, isolation):
    _, db, store, _, _, _, _, _, user, _ = memory_publication
    attempt = _task(db, store, user)
    with pytest.raises(psycopg.errors.RaiseException, match='READ COMMITTED'):
        with db.transaction() as cursor:
            cursor.execute(f'set transaction isolation level {isolation}')
            cursor.execute('''update import_objects set size_bytes=size_bytes
                where task_id=%s and user_id=%s''',
                (attempt.task.task_id, user))
    with db.transaction() as cursor:
        original = cursor.execute('select size_bytes from import_objects '
                                  'where task_id=%s',
                                  (attempt.task.task_id,)).fetchone()['size_bytes']
        cursor.execute('''update import_objects set size_bytes=size_bytes+1
            where task_id=%s and user_id=%s''', (attempt.task.task_id, user))
    with db.transaction() as cursor:
        assert cursor.execute('select size_bytes from import_objects '
                              'where task_id=%s',
                              (attempt.task.task_id,)).fetchone()['size_bytes'] == original + 1


@pytest.mark.parametrize('isolation', ['repeatable read', 'serializable',
                                       'read uncommitted'])
@pytest.mark.parametrize('mutation', ['delete', 'illegal_end_update'])
def test_audit_mutation_requires_read_committed_even_without_gate(
        memory_publication, isolation, mutation):
    _, db, store, _, _, _, _, _, user, _ = memory_publication
    attempt = _task(db, store, user)
    args = (attempt.task.task_id, attempt.lease_version)
    sql = ('delete from import_task_attempts where task_id=%s and lease_version=%s'
           if mutation == 'delete' else '''update import_task_attempts
             set ended_at=clock_timestamp(),end_reason='failed'
             where task_id=%s and lease_version=%s''')
    with db.transaction() as cursor:
        before = cursor.execute('''select * from import_task_attempts where
            task_id=%s and lease_version=%s''', args).fetchone()
    with pytest.raises(psycopg.errors.RaiseException, match='READ COMMITTED') as blocked:
        with db.transaction() as cursor:
            cursor.execute(f'set transaction isolation level {isolation}')
            cursor.execute(sql, args)
    assert blocked.value.sqlstate == 'P0001'
    with db.transaction() as cursor:
        assert cursor.execute('''select * from import_task_attempts where
            task_id=%s and lease_version=%s''', args).fetchone() == before


def test_audit_read_committed_legacy_heartbeat_update_is_allowed(
        memory_publication):
    _, db, store, _, _, _, _, _, user, _ = memory_publication
    attempt = _task(db, store, user)
    args = (attempt.task.task_id, attempt.lease_version)
    with db.transaction() as cursor:
        before = cursor.execute('''select * from import_task_attempts where
            task_id=%s and lease_version=%s''', args).fetchone()
        cursor.execute('''update import_task_attempts set
            heartbeat_at=clock_timestamp() where task_id=%s and lease_version=%s''', args)
    with db.transaction() as cursor:
        after = cursor.execute('''select * from import_task_attempts where
            task_id=%s and lease_version=%s''', args).fetchone()
    assert after['heartbeat_at'] >= before['heartbeat_at']
    assert {key: value for key, value in after.items() if key != 'heartbeat_at'} == {
        key: value for key, value in before.items() if key != 'heartbeat_at'}


def _gated_mutation_rows(db, user):
    tables = ('import_batches', 'import_tasks', 'import_task_attempts',
              'import_objects', 'import_publication_evidence',
              'import_publication_private_payloads',
              'user_publication_gates', 'import_publication_recovery_queue',
              'generation_reservations', 'user_mutation_leases')
    with db.transaction() as cursor:
        return {table: sorted((repr(dict(row)) for row in cursor.execute(
            f'select * from {table} where user_id=%s', (user,)).fetchall()))
            for table in tables}


def _ordinary_publish_rows(db, user):
    """Capture the database authorities an ordinary import could change."""
    tables = (
        'users', 'user_mutation_leases', 'import_batches', 'import_tasks',
        'import_task_attempts', 'import_objects', 'import_publication_evidence',
        'import_publication_private_payloads', 'user_publication_gates',
        'import_publication_recovery_queue', 'generation_reservations',
        'document_objects', 'history_document_witnesses', 'vector_indexes',
        'vector_generations', 'vector_heads', 'user_snapshots',
        'memory_documents',
    )
    with db.transaction() as cursor:
        return {table: sorted((repr(dict(row)) for row in cursor.execute(
            f'select * from {table}').fetchall())) for table in tables}


def test_ordinary_publish_refuses_unresolved_gate_before_planning_or_io(
        memory_publication, monkeypatch):
    service, db, store, _, _, rag_scope, _, profile, user, _ = memory_publication
    _, _, _, _, attempt, _ = _issue(memory_publication)
    key = (user, attempt.task.task_id, attempt.lease_version)
    before = _ordinary_publish_rows(db, user)
    monkeypatch.setattr(service, '_plan_intent', lambda *args, **kwargs:
                        pytest.fail('Unresolved gate reached publication planning'))
    monkeypatch.setattr(store, 'put_immutable', lambda *args, **kwargs:
                        pytest.fail('Unresolved gate reached external document I/O'))

    with pytest.raises(PublicationEvidenceError,
                       match='^Unresolved import publication gates this user$'):
        service.publish(rag_scope, attempt,
            [_point(rag_scope, attempt.task.document_id, 'blocked-gate')],
            event_vector=[1., 0., 0., 0.], event_profile=profile)

    assert _ordinary_publish_rows(db, user) == before
    with db.transaction() as cursor:
        assert cursor.execute('''select status from user_publication_gates
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            key).fetchone()['status'] == 'unresolved'


def test_ordinary_publish_rejects_non_read_committed_before_planning_or_io(
        memory_publication, monkeypatch):
    service, db, store, _, _, rag_scope, _, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    before = _ordinary_publish_rows(db, user)
    monkeypatch.setattr(service, '_plan_intent', lambda *args, **kwargs:
                        pytest.fail('Unsupported isolation reached publication planning'))
    monkeypatch.setattr(store, 'put_immutable', lambda *args, **kwargs:
                        pytest.fail('Unsupported isolation reached external document I/O'))

    with db.connection() as connection:
        original_isolation = connection.isolation_level
        try:
            assert connection.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
            connection.isolation_level = IsolationLevel.REPEATABLE_READ
            with monkeypatch.context() as patch:
                patch.setattr(db, 'connection', lambda: nullcontext(connection))
                with pytest.raises(ValueError,
                                   match='^User mutation leases require READ COMMITTED$'):
                    service.publish(rag_scope, attempt,
                        [_point(rag_scope, attempt.task.document_id, 'blocked-rr')],
                        event_vector=[1., 0., 0., 0.], event_profile=profile)
                assert connection.isolation_level == IsolationLevel.REPEATABLE_READ
                assert connection.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        finally:
            connection.rollback()
            connection.isolation_level = original_isolation

    assert _ordinary_publish_rows(db, user) == before


@pytest.mark.parametrize('mutation', [
    'insert_batch', 'insert_tasks', 'cancel_queued', 'request_running_cancel',
    'retry_task', 'retry_batch', 'touch_batch', 'direct_touch',
    'inherited_request_cancel', 'inherited_retry_task',
    'inherited_retry_failed_in_batch', 'inherited_create_batch',
    'inherited_create_batch_in_transaction', 'escaped_caller_owned',
    'coordinator_acquire',
])
def test_task9_direct_import_mutators_refuse_unresolved_user_without_writes(
        memory_publication, mutation):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    new_batch = 'batch-' + uuid4().hex
    new_task = ImportTaskCreate('task-' + uuid4().hex, new_batch, user,
        'doc-' + uuid4().hex, 'file.txt', '.txt', 3, 'imports/file.txt')
    task_id, batch_id = attempt.task.task_id, attempt.task.batch_id
    before = _gated_mutation_rows(db, user)
    repository = PostgresImportTaskRepository(db)
    controls = {
        'inherited_request_cancel': lambda: repository.request_cancel(
            user, batch_id, task_id, now='T2'),
        'inherited_retry_task': lambda: repository.retry_task(
            user, task_id, now='T2'),
        'inherited_retry_failed_in_batch': lambda:
            repository.retry_failed_in_batch(user, batch_id, now='T2'),
        'inherited_create_batch': lambda: repository.create_batch(
            user, [new_task], now='T2'),
        'coordinator_acquire': lambda:
            PostgresUserMutationCoordinator(db).acquire(user, 'other-worker'),
    }
    if mutation in controls:
        with pytest.raises(PublicationEvidenceError, match='Unresolved'):
            controls[mutation]()
    else:
        with db.transaction() as cursor:
            cursor.execute('begin')
            store = ImportStore(cursor, postgres=True)
            methods = {
                'insert_batch': lambda: store.insert_batch(new_batch,user,'T2'),
                'insert_tasks': lambda: store.insert_tasks([new_task],'T2'),
                'cancel_queued': lambda: store.cancel_queued(
                    user,batch_id,task_id,'T2'),
                'request_running_cancel': lambda: store.request_running_cancel(
                    user,batch_id,task_id,'T2'),
                'retry_task': lambda: store.retry_task(user,task_id,'T2'),
                'retry_batch': lambda: store.retry_batch(user,batch_id,'T2'),
                'touch_batch': lambda: store.touch_batch(user,batch_id,'T2'),
                'direct_touch': lambda: PostgresImportLeaseRepository._touch(
                    cursor,cursor.execute('select * from import_tasks where id=%s',
                                          (task_id,)).fetchone()),
                'inherited_create_batch_in_transaction': lambda:
                    repository.create_batch_in_transaction(cursor,user,[new_task],now='T2'),
                'escaped_caller_owned': lambda:
                    ImportPersistence(db,postgres=True).caller_owned(cursor,user),
            }
            with pytest.raises(PublicationEvidenceError, match='Unresolved'):
                methods[mutation]()
    assert _gated_mutation_rows(db, user) == before


@pytest.mark.parametrize('method', ['progress', 'finish'])
def test_task9_direct_lease_helper_rejects_cross_row_before_any_dml(
        memory_publication, method):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    before = _gated_mutation_rows(db, user)
    leases = PostgresImportLeaseRepository(db)
    with db.transaction() as cursor:
        cursor.execute('begin')
        original = leases._live(cursor, attempt)
        other_row = dict(original)
        other_row['batch_id'] = 'cross-row-' + uuid4().hex
        with pytest.raises(ImportLeaseLost, match='Original import'):
            if method == 'progress':
                leases._progress(cursor, attempt, other_row, 'parsing', 1)
            else:
                leases._finish(cursor, attempt, other_row, 'failed', 'failed')
    assert _gated_mutation_rows(db, user) == before


@pytest.mark.parametrize('shared_database', ['20261002_14'], indirect=True)
@pytest.mark.parametrize('break_seed', [False,True])
def test_populated_14_to_15_preserves_exact_private_payload(
        shared_database, memory_publication, break_seed):
    service, db, store, _, _, rag_scope, _, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    plan = service._plan_intent(rag_scope, attempt,
        [_point(rag_scope, attempt.task.document_id, 'legacy')],
        event_vector=[1., 0., 0., 0.], event_profile=profile)
    encoded = encode_intent(plan.intent)
    key = (user, attempt.task.task_id, attempt.lease_version)
    with db.transaction() as cursor:
        cursor.execute('''insert into import_publication_evidence
            (user_id,task_id,task_lease_version,document_id,worker_id,
             task_lease_token,user_lease_token,user_lease_version,
             schema_version,intent_format,intent_payload,canonical_bytes,intent_hash)
            values(%s,%s,%s,%s,%s,%s,%s,%s,1,'canonical-json-zlib-1',%s,%s,%s)''',
            (*key,attempt.task.document_id,attempt.worker_id,attempt.lease_token,
             attempt.user_lease.lease_token,attempt.user_lease.lease_version,
             encoded.payload,encoded.canonical_bytes,encoded.digest))
        cursor.execute('''insert into user_publication_gates
            (user_id,task_id,task_lease_version) values(%s,%s,%s)''', key)
        for kind in ('rag','episode'):
            scope = plan.intent['scopes'][kind]
            cursor.execute('''insert into generation_reservations
                (generation_id,user_id,task_id,task_lease_version,
                 vector_kind,namespace,index_key,base_revision,owner,
                 user_lease_token,user_lease_version)
                values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (scope['candidate_id'],*key,kind,scope['namespace'],
                 scope['index_key'],scope['head']['revision'],attempt.worker_id,
                 attempt.user_lease.lease_token,attempt.user_lease.lease_version))
        old = cursor.execute('''select intent_payload,canonical_bytes,intent_hash,
            document_slot,rag_sealed_slot,episode_sealed_slot,terminal_slot,
            phase,phase_version from import_publication_evidence
            where user_id=%s and task_id=%s and task_lease_version=%s''', key).fetchone()
    assert old is not None

    if break_seed:
        with db.transaction() as cursor:
            cursor.execute('''update import_tasks set
                user_lease_version=user_lease_version+1 where id=%s''',(key[1],))
        with pytest.raises(DBAPIError, match='Recovery queue backfill mismatch'):
            command.upgrade(Config('alembic.ini'), '20261002_15')
        with db.transaction() as cursor:
            assert cursor.execute('select version_num from alembic_version').fetchone()['version_num'] == '20261002_14'
            assert cursor.execute("select to_regclass('import_publication_recovery_queue') as name").fetchone()['name'] is None
            unchanged = cursor.execute('''select intent_payload,canonical_bytes,intent_hash,
                document_slot,rag_sealed_slot,episode_sealed_slot,terminal_slot,
                phase,phase_version from import_publication_evidence
                where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
            assert unchanged == old
        return

    command.upgrade(Config('alembic.ini'), '20261002_15')

    with db.transaction() as cursor:
        child = cursor.execute('''select * from import_publication_private_payloads
            where user_id=%s and task_id=%s and task_lease_version=%s''', key).fetchone()
        queue = cursor.execute('''select state,due_at from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''', key).fetchone()
        header = cursor.execute('''select * from import_publication_evidence
            where user_id=%s and task_id=%s and task_lease_version=%s''', key).fetchone()
    assert child is not None and queue is not None and header is not None
    for name in ('intent_payload', 'canonical_bytes', 'document_slot',
                 'rag_sealed_slot', 'episode_sealed_slot', 'terminal_slot'):
        assert child[name] == old[name]
        assert name not in header
    assert child['payload_phase_version'] == header['phase_version'] == old['phase_version']
    assert header['intent_hash'] == old['intent_hash']
    assert queue['state'] == 'pending' and queue['due_at'] is not None

    frozen = PostgresImportPublicationEvidenceRepository(db).read_exact(*key)
    assert frozen is not None and frozen.phase == 'intent'
    assert frozen.intent == plan.intent


@pytest.mark.parametrize('shared_database', ['20261002_14'], indirect=True)
@pytest.mark.parametrize('fault', [
    'compression', 'hash', 'canonical_size', 'missing_reservation',
    'wrong_reservation', 'resolved_gate',
])
def test_legacy_corruption_aborts_migration_without_losing_original_bytes(
        shared_database, memory_publication, fault):
    service, db, store, _, _, rag_scope, _, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    plan = service._plan_intent(rag_scope, attempt,
        [_point(rag_scope, attempt.task.document_id, 'legacy-corrupt')],
        event_vector=[1.,0.,0.,0.], event_profile=profile)
    encoded = encode_intent(plan.intent)
    key = (user, attempt.task.task_id, attempt.lease_version)
    payload = b'not-zlib' if fault == 'compression' else encoded.payload
    digest = '0' * 64 if fault == 'hash' else encoded.digest
    canonical_size = encoded.canonical_bytes + (1 if fault == 'canonical_size' else 0)
    with db.transaction() as cursor:
        cursor.execute('''insert into import_publication_evidence
            (user_id,task_id,task_lease_version,document_id,worker_id,
             task_lease_token,user_lease_token,user_lease_version,
             schema_version,intent_format,intent_payload,canonical_bytes,intent_hash)
            values(%s,%s,%s,%s,%s,%s,%s,%s,1,'canonical-json-zlib-1',%s,%s,%s)''',
            (*key,attempt.task.document_id,attempt.worker_id,attempt.lease_token,
             attempt.user_lease.lease_token,attempt.user_lease.lease_version,
             payload,canonical_size,digest))
        cursor.execute('''insert into user_publication_gates
            (user_id,task_id,task_lease_version) values(%s,%s,%s)''', key)
        if fault == 'resolved_gate':
            cursor.execute('''update user_publication_gates set status='resolved',
                resolved_at=clock_timestamp() where user_id=%s''',(user,))
        for kind in ('rag','episode'):
            if fault == 'missing_reservation' and kind == 'episode':
                continue
            scope = plan.intent['scopes'][kind]
            cursor.execute('''insert into generation_reservations
                (generation_id,user_id,task_id,task_lease_version,
                 vector_kind,namespace,index_key,base_revision,owner,
                 user_lease_token,user_lease_version)
                values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (scope['candidate_id'],*key,kind,
                 scope['namespace'] + '-wrong' if fault == 'wrong_reservation'
                     and kind == 'episode' else scope['namespace'],
                 scope['index_key'],scope['head']['revision'],attempt.worker_id,
                 attempt.user_lease.lease_token,attempt.user_lease.lease_version))
        before = cursor.execute('''select intent_payload,canonical_bytes,intent_hash
            from import_publication_evidence where user_id=%s and task_id=%s
            and task_lease_version=%s''',key).fetchone()
    with pytest.raises((RuntimeError, PublicationEvidenceError, DBAPIError)):
        command.upgrade(Config('alembic.ini'), '20261002_15')
    with db.transaction() as cursor:
        assert cursor.execute('select version_num from alembic_version').fetchone()['version_num'] == '20261002_14'
        assert cursor.execute("select to_regclass('import_publication_private_payloads') as name").fetchone()['name'] is None
        after = cursor.execute('''select intent_payload,canonical_bytes,intent_hash
            from import_publication_evidence where user_id=%s and task_id=%s
            and task_lease_version=%s''',key).fetchone()
        assert after == before


def _legacy_complete_slots(intent, digest):
    attempt = intent['attempt']
    source = intent['source']
    document = intent['document']
    unsigned = dict(user_id=attempt['user_id'], document_id=attempt['document_id'],
        bucket=source['bucket'],key=document['key'],version_id='legacy-version',
        sha256=source['sha256'],size_bytes=source['size_bytes'],
        record_hash=sha256(_canonical(document['record'])).hexdigest())
    final_hash = sha256(b'1:' + digest.encode('ascii') + b':' +
                        _canonical(unsigned)).hexdigest()
    document_slot = _canonical(dict(unsigned,final_expected_hash=final_hash))
    sealed_slots = {}
    receipts = {}
    timestamp = '2026-10-04T00:00:00.000000+00:00'
    for kind in ('rag','episode'):
        scope = intent['scopes'][kind]
        snapshot = intent['snapshots']['next_' + ('history' if kind == 'rag' else 'memory')]
        sealed = dict(generation_id=scope['candidate_id'],vector_kind=kind,
            user_id=attempt['user_id'],task_id=attempt['task_id'],
            task_lease_token=attempt['task_lease_token'],
            task_lease_version=attempt['task_lease_version'],
            worker_id=attempt['worker_id'],
            user_lease_token=attempt['user_lease_token'],
            user_lease_version=attempt['user_lease_version'],
            namespace=scope['namespace'],index_key=scope['index_key'],
            base_revision=scope['head']['revision'],
            index_revision=scope['index_revision'],
            snapshot_version=snapshot['version'],expected_count=0,
            content_digest='0'*64,final_expected_hash=final_hash)
        sealed_slots[kind] = _canonical(sealed)
        receipts[kind] = [scope['candidate_id'],attempt['user_id'],kind,
            scope['namespace'],scope['index_key'],scope['head']['revision'],
            scope['index_revision'],scope['head']['revision']+1,
            snapshot['version'],0,'0'*64,attempt['worker_id'],
            attempt['user_lease_token'],attempt['user_lease_version'],
            attempt['task_id'],attempt['task_lease_token'],
            attempt['task_lease_version'],timestamp,timestamp,timestamp]
    terminal_slot = _canonical(dict(final_expected_hash=final_hash,
        rag_receipt=receipts['rag'],episode_receipt=receipts['episode']))
    return document_slot,sealed_slots['rag'],sealed_slots['episode'],terminal_slot


@pytest.mark.parametrize('shared_database', ['20261002_14'], indirect=True)
@pytest.mark.parametrize('phase,corruption', [
    ('document_verified',None),('partial_rag',None),('pair_sealed',None),
    ('terminal_committed',None),('proved_succeeded',None),('abandoned',None),
    ('document_verified','document_slot'),('partial_rag','rag_sealed_slot'),
    ('pair_sealed','episode_sealed_slot'),('terminal_committed','terminal_slot'),
    ('proved_succeeded','unresolved_gate'),('abandoned','reserved_episode'),
])
def test_populated_legacy_phase_and_disposition_migrate_byte_exact(
        shared_database, memory_publication, phase, corruption):
    service, db, store, _, _, rag_scope, _, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    plan = service._plan_intent(rag_scope,attempt,
        [_point(rag_scope,attempt.task.document_id,'legacy-' + phase)],
        event_vector=[1.,0.,0.,0.],event_profile=profile)
    encoded = encode_intent(plan.intent)
    slots = _legacy_complete_slots(plan.intent,encoded.digest)
    key = (user,attempt.task.task_id,attempt.lease_version)
    with db.transaction() as cursor:
        cursor.execute('''insert into import_publication_evidence
            (user_id,task_id,task_lease_version,document_id,worker_id,
             task_lease_token,user_lease_token,user_lease_version,
             schema_version,intent_format,intent_payload,canonical_bytes,intent_hash)
            values(%s,%s,%s,%s,%s,%s,%s,%s,1,'canonical-json-zlib-1',%s,%s,%s)''',
            (*key,attempt.task.document_id,attempt.worker_id,attempt.lease_token,
             attempt.user_lease.lease_token,attempt.user_lease.lease_version,
             encoded.payload,encoded.canonical_bytes,encoded.digest))
        cursor.execute('''insert into user_publication_gates
            (user_id,task_id,task_lease_version) values(%s,%s,%s)''',key)
        for kind in ('rag','episode'):
            scope = plan.intent['scopes'][kind]
            cursor.execute('''insert into generation_reservations
                (generation_id,user_id,task_id,task_lease_version,vector_kind,
                 namespace,index_key,base_revision,owner,user_lease_token,
                 user_lease_version) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (scope['candidate_id'],*key,kind,scope['namespace'],
                 scope['index_key'],scope['head']['revision'],attempt.worker_id,
                 attempt.user_lease.lease_token,attempt.user_lease.lease_version))
        if phase == 'abandoned':
            cursor.execute('''update import_publication_evidence
                set phase='abandoned',phase_version=2 where user_id=%s and task_id=%s
                and task_lease_version=%s''',key)
            cursor.execute('''update generation_reservations set state='revoked',
                revoked_at=clock_timestamp() where user_id=%s and task_id=%s
                and task_lease_version=%s''' +
                (" and vector_kind='rag'" if corruption == 'reserved_episode' else ''),
                key)
        else:
            cursor.execute('''update import_publication_evidence
                set phase='document_verified',phase_version=2,document_slot=%s
                where user_id=%s and task_id=%s and task_lease_version=%s''',
                (b'{}' if corruption == 'document_slot' else slots[0],*key))
            if phase in ('partial_rag','pair_sealed','terminal_committed','proved_succeeded'):
                cursor.execute('''update import_publication_evidence
                    set phase_version=3,rag_sealed_slot=%s where user_id=%s
                    and task_id=%s and task_lease_version=%s''',
                    (b'{}' if corruption == 'rag_sealed_slot' else slots[1],*key))
            if phase in ('pair_sealed','terminal_committed','proved_succeeded'):
                cursor.execute('''update import_publication_evidence
                    set phase='pair_sealed',phase_version=4,episode_sealed_slot=%s
                    where user_id=%s and task_id=%s and task_lease_version=%s''',
                    (b'{}' if corruption == 'episode_sealed_slot' else slots[2],*key))
            if phase in ('terminal_committed','proved_succeeded'):
                cursor.execute('''update import_publication_evidence
                    set phase='terminal_committed',phase_version=5,terminal_slot=%s
                    where user_id=%s and task_id=%s and task_lease_version=%s''',
                    (b'{}' if corruption == 'terminal_slot' else slots[3],*key))
                cursor.execute('''update import_tasks set status='succeeded',
                    stage='succeeded',progress=100,finished_at=clock_timestamp()
                    where id=%s and user_id=%s''',(key[1],user))
                cursor.execute('''update import_task_attempts set
                    ended_at=clock_timestamp(),end_reason='succeeded',
                    last_stage='succeeded' where task_id=%s and lease_version=%s''',
                    key[1:])
            if phase == 'proved_succeeded':
                cursor.execute('''update import_publication_evidence
                    set phase='proved_succeeded',phase_version=6
                    where user_id=%s and task_id=%s and task_lease_version=%s''',key)
        if phase in ('proved_succeeded','abandoned') and corruption != 'unresolved_gate':
            cursor.execute('''update user_publication_gates set status='resolved',
                resolved_at=clock_timestamp() where user_id=%s and task_id=%s
                and task_lease_version=%s''',key)
        old = cursor.execute('''select intent_payload,canonical_bytes,document_slot,
            rag_sealed_slot,episode_sealed_slot,terminal_slot,phase,phase_version
            from import_publication_evidence where user_id=%s and task_id=%s
            and task_lease_version=%s''',key).fetchone()
    if corruption is None:
        _validate_stored_slots(plan.intent,encoded.digest,old['phase'],
            old['phase_version'],None,tuple(old[name] for name in
            ('document_slot','rag_sealed_slot','episode_sealed_slot','terminal_slot')))
    else:
        with pytest.raises((RuntimeError,PublicationEvidenceError,DBAPIError)):
            command.upgrade(Config('alembic.ini'),'20261002_15')
        with db.transaction() as cursor:
            assert cursor.execute('select version_num from alembic_version').fetchone()['version_num'] == '20261002_14'
            assert cursor.execute("select to_regclass('import_publication_private_payloads') as name").fetchone()['name'] is None
            current = cursor.execute('''select intent_payload,canonical_bytes,
                document_slot,rag_sealed_slot,episode_sealed_slot,terminal_slot,
                phase,phase_version from import_publication_evidence
                where user_id=%s and task_id=%s and task_lease_version=%s''',
                key).fetchone()
            assert current == old
        return
    command.upgrade(Config('alembic.ini'),'20261002_15')
    with db.transaction() as cursor:
        child = cursor.execute('''select * from import_publication_private_payloads
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
        queue = cursor.execute('''select state from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
        task_status = cursor.execute('select status from import_tasks where id=%s',
                                     (key[1],)).fetchone()['status']
    for name in ('intent_payload','canonical_bytes','document_slot',
                 'rag_sealed_slot','episode_sealed_slot','terminal_slot'):
        assert child[name] == old[name]
    assert child['payload_phase_version'] == old['phase_version']
    assert (queue is not None) == (phase not in ('proved_succeeded','abandoned'))
    if phase in ('terminal_committed','proved_succeeded'):
        assert task_status == 'succeeded'


def test_fixed_completion_succeeds_under_15(memory_publication):
    service, db, _, user, attempt, live = _issue(memory_publication)
    with pytest.raises(PublicationEvidenceError):
        with db.transaction() as cursor:
            cursor.execute('begin')
            row = service.pair.imports._live(cursor,attempt)
            service.pair.imports._progress(cursor,attempt,row,'committing',
                                           row['progress'])
    result = service._execute_live_publication(live)
    assert result.pair.import_task.status == 'succeeded'
    frozen = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert frozen is not None and frozen.phase == 'terminal_committed'
    assert frozen.terminal_slot is not None


def _claim_due_exact(db, user, attempt, *, lease_seconds=60):
    key = (user, attempt.task.task_id, attempt.lease_version)
    with db.transaction() as cursor:
        cursor.execute('''update import_tasks set lease_expires_at=
            clock_timestamp()-interval '1 second' where id=%s''', (key[1],))
        cursor.execute('''update user_mutation_leases set lease_expires_at=
            clock_timestamp()-interval '1 second' where user_id=%s''', (user,))
        cursor.execute('''update import_publication_recovery_queue set
            due_at=clock_timestamp(),reason_code='unknown',
            queue_version=queue_version+1 where user_id=%s and task_id=%s
            and task_lease_version=%s''', key)
    claim = PostgresImportPublicationRecoveryRepository(db).claim_next(
        'task8',lease_seconds=lease_seconds)
    assert claim is not None
    return key, claim


def test_task8_proof_first_revokes_both_absent_generations(memory_publication):
    service, db, _, user, attempt, _ = _issue(memory_publication)
    key, claim = _claim_due_exact(db, user, attempt)
    assert ImportPublicationProofService(db).prove_exact(*key) is None
    assert PostgresImportPublicationRecoveryRepository(db).prove_or_hold(claim) == 'abandoned'
    with db.transaction() as cursor:
        rows = cursor.execute('''select vector_kind,state from generation_reservations
            where user_id=%s and task_id=%s and task_lease_version=%s
            order by vector_kind''', key).fetchall()
        header = cursor.execute('''select phase from import_publication_evidence
            where user_id=%s and task_id=%s and task_lease_version=%s''', key).fetchone()
        gate = cursor.execute('''select status from user_publication_gates
            where user_id=%s''', (user,)).fetchone()
    assert [(r['vector_kind'], r['state']) for r in rows] == [
        ('episode', 'revoked'), ('rag', 'revoked')]
    assert header['phase'] == 'abandoned' and gate['status'] == 'resolved'


def test_task8_lost_document_put_response_recovers_without_replay(
        memory_publication,monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    original_put = store.put_immutable
    put_results = []

    def lost_response(*args,**kwargs):
        put_results.append(original_put(*args,**kwargs))
        raise ConnectionError('injected lost S3 put response')

    monkeypatch.setattr(store,'put_immutable',lost_response)
    with pytest.raises(DurablePublicationUnknown):
        service._execute_live_publication(live)
    key = (user,attempt.task.task_id,attempt.lease_version)
    frozen = PostgresImportPublicationEvidenceRepository(db).read_exact(*key)
    assert len(put_results) == 1 and frozen.phase == 'intent'
    assert frozen.document_slot is None
    _, claim = _claim_due_exact(db,user,attempt)
    assert PostgresImportPublicationRecoveryRepository(db).prove_or_hold(claim) == 'abandoned'
    assert len(put_results) == 1
    with db.transaction() as cursor:
        reservations = cursor.execute('''select state from generation_reservations
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            key).fetchall()
    assert [row['state'] for row in reservations] == ['revoked','revoked']


def test_task8_committed_response_loss_acknowledges_without_replay(memory_publication):
    service, db, _, user, attempt, live = _issue(memory_publication)
    completed = service._execute_live_publication(live)
    assert completed.pair.import_task.status == 'succeeded'
    key = (user,attempt.task.task_id,attempt.lease_version)
    with db.transaction() as cursor:
        cursor.execute('''update import_publication_recovery_queue set
            due_at=clock_timestamp(),reason_code='unknown',
            queue_version=queue_version+1 where user_id=%s and task_id=%s
            and task_lease_version=%s''', key)
    proof = ImportPublicationProofService(db).prove_exact(*key)
    assert proof is not None
    claim = PostgresImportPublicationRecoveryRepository(db).claim_next('task8')
    assert claim is not None
    assert PostgresImportPublicationRecoveryRepository(db).prove_or_hold(claim) == 'proved_succeeded'
    with db.transaction() as cursor:
        phase = cursor.execute('''select phase,observation_reason from
            import_publication_evidence where user_id=%s and task_id=%s
            and task_lease_version=%s''', key).fetchone()
        task = cursor.execute('select status from import_tasks where id=%s',
                              (key[1],)).fetchone()
        gate = cursor.execute('''select status from user_publication_gates
            where user_id=%s''', (user,)).fetchone()
    assert phase['phase'] == 'proved_succeeded' and phase['observation_reason'] is None
    assert task['status'] == 'succeeded' and gate['status'] == 'resolved'


def test_task8_caller_rollback_restores_every_success_ack_write(memory_publication):
    service, db, _, user, attempt, live = _issue(memory_publication)
    service._execute_live_publication(live)
    key = (user,attempt.task.task_id,attempt.lease_version)
    with db.transaction() as cursor:
        cursor.execute('''update import_publication_recovery_queue set
            due_at=clock_timestamp(),reason_code='unknown',
            queue_version=queue_version+1 where user_id=%s and task_id=%s
            and task_lease_version=%s''',key)
    repository = PostgresImportPublicationRecoveryRepository(db)
    claim = repository.claim_next('task8')
    proof = ImportPublicationProofService(db).prove_exact(*key)
    assert claim is not None and proof is not None

    class RollbackProbe(RuntimeError):
        pass

    with pytest.raises(RollbackProbe):
        with db.transaction() as cursor:
            cursor.execute('begin')
            repository.ack_proved_in_transaction(cursor,claim,proof)
            assert cursor.execute('''select phase from import_publication_evidence
                where user_id=%s and task_id=%s and task_lease_version=%s''',
                key).fetchone()['phase'] == 'proved_succeeded'
            raise RollbackProbe()
    with db.transaction() as cursor:
        header = cursor.execute('''select phase from import_publication_evidence
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            key).fetchone()
        gate = cursor.execute('''select status from user_publication_gates
            where user_id=%s''',(user,)).fetchone()
        queue = cursor.execute('''select state from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            key).fetchone()
        lease = cursor.execute('''select expires_at from import_publication_recovery_leases
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            key).fetchone()
    assert header['phase'] == 'terminal_committed'
    assert gate['status'] == 'unresolved' and queue['state'] == 'claimed'
    assert lease['expires_at'] == claim.recovery_expires_at
    assert repository.prove_or_hold(claim) == 'proved_succeeded'


def test_task8_rejects_tampered_detached_success_envelope(memory_publication):
    service, db, _, user, attempt, live = _issue(memory_publication)
    service._execute_live_publication(live)
    key = (user,attempt.task.task_id,attempt.lease_version)
    with db.transaction() as cursor:
        cursor.execute('''update import_publication_recovery_queue set
            due_at=clock_timestamp(),reason_code='unknown',
            queue_version=queue_version+1 where user_id=%s and task_id=%s
            and task_lease_version=%s''', key)
    claim = PostgresImportPublicationRecoveryRepository(db).claim_next('task8')
    proof = ImportPublicationProofService(db).prove_exact(*key)
    assert claim is not None and proof is not None
    forged = replace(proof, proof=replace(proof.proof,
        final_expected_hash='0' * 64))
    with pytest.raises(RuntimeError, match='Terminal receipt envelope changed'):
        PostgresImportPublicationRecoveryRepository(db).ack_success(claim,forged)
    assert PostgresImportPublicationEvidenceRepository(db).read_exact(*key).phase == 'terminal_committed'
    assert PostgresImportPublicationRecoveryRepository(db).prove_or_hold(claim) == 'proved_succeeded'


def test_task8_revokes_present_and_absent_generation_together(
        memory_publication,monkeypatch):
    service, db, _, user, attempt, live = _issue(memory_publication)
    issue = service._require_live_issue(live)
    def stop_after_rag_stage(scope,owner,head,corpus,*,snapshot_version,
                             generation_id,live):
        issue.rag_authority.stage(scope,owner,expected_revision=head.revision,
                                  generation_id=generation_id,live=live)
        raise RuntimeError('stop after rag stage')
    monkeypatch.setattr(issue.rag_service,'_prepare_sealed',stop_after_rag_stage)
    with pytest.raises(DurablePublicationUnknown):
        service._execute_live_publication(live)
    key, claim = _claim_due_exact(db,user,attempt)
    assert PostgresImportPublicationRecoveryRepository(db).prove_or_hold(claim) == 'abandoned'
    with db.transaction() as cursor:
        reservations = cursor.execute('''select vector_kind,state from generation_reservations
            where user_id=%s and task_id=%s and task_lease_version=%s
            order by vector_kind''',key).fetchall()
        generation = cursor.execute('''select state from vector_generations
            where generation_id=%s''',(issue.plan.candidate_ids[0],)).fetchone()
    assert [(r['vector_kind'],r['state']) for r in reservations] == [
        ('episode','revoked'),('rag','revoked')]
    assert generation['state'] == 'abandoned'


def test_task8_invalid_second_generation_rolls_back_first_id_and_closure(
        memory_publication,monkeypatch):
    service, db, _, user, attempt, live = _issue(memory_publication)
    issue = service._require_live_issue(live)
    def stop_after_episode_stage(scope,owner,head,corpus,*,snapshot_version,
                                 generation_id,live):
        issue.episode_authority.stage(scope,owner,expected_revision=head.revision,
                                      generation_id=generation_id,live=live)
        raise RuntimeError('stop after episode stage')
    monkeypatch.setattr(issue.episode_service,'_prepare_sealed',
                        stop_after_episode_stage)
    with pytest.raises(DurablePublicationUnknown):
        service._execute_live_publication(live)
    with db.transaction() as cursor:
        cursor.execute('''update vector_generations set state='abandoned',
            abandoned_at=clock_timestamp() where generation_id=%s''',
            (issue.plan.candidate_ids[1],))
    key, claim = _claim_due_exact(db,user,attempt)
    assert PostgresImportPublicationRecoveryRepository(db).prove_or_hold(claim) == 'manual_hold'
    with db.transaction() as cursor:
        reservations = cursor.execute('''select vector_kind,state from generation_reservations
            where user_id=%s and task_id=%s and task_lease_version=%s
            order by vector_kind''', key).fetchall()
        rag = cursor.execute('''select state from vector_generations where generation_id=%s''',
            (issue.plan.candidate_ids[0],)).fetchone()
        task = cursor.execute('select status from import_tasks where id=%s',
                              (key[1],)).fetchone()
        audit = cursor.execute('''select ended_at from import_task_attempts
            where task_id=%s and lease_version=%s''',key[1:]).fetchone()
        gate = cursor.execute('''select status from user_publication_gates
            where user_id=%s''',(user,)).fetchone()
    assert [(r['vector_kind'],r['state']) for r in reservations] == [
        ('episode','reserved'),('rag','reserved')]
    assert rag['state'] == 'sealed'
    assert task['status'] == 'running' and audit['ended_at'] is None
    assert gate['status'] == 'unresolved'


@pytest.mark.parametrize('published_state', ['published','retired'])
def test_task8_published_candidate_holds_before_audit_expiry(
        memory_publication,monkeypatch,published_state):
    service, db, _, user, attempt, live = _issue(memory_publication)
    issue = service._require_live_issue(live)
    monkeypatch.setattr(issue.imports,'try_begin_committing',
        lambda *args,**kwargs: (_ for _ in ()).throw(
            RuntimeError('stop after pair seal')))
    with pytest.raises(DurablePublicationUnknown):
        service._execute_live_publication(live)
    key = (user,attempt.task.task_id,attempt.lease_version)
    candidate = issue.plan.candidate_ids[0]
    with db.transaction() as cursor:
        exact = cursor.execute('''select * from vector_generations
            where generation_id=%s''',(candidate,)).fetchone()
        head = cursor.execute('''select * from vector_heads where tenant_id=%s
            and vector_kind=%s and namespace=%s and index_key=%s for update''',
            (exact['tenant_id'],exact['vector_kind'],exact['namespace'],
             exact['index_key'])).fetchone()
        assert exact['state'] == 'sealed' and head is not None
        cursor.execute('''update vector_generations set state='retired'
            where generation_id=%s''',(head['last_generation_id'],))
        cursor.execute('''update vector_generations set state='published',
            publication_revision=%s,publication_snapshot_version=1,
            published_at=clock_timestamp() where generation_id=%s''',
            (head['revision']+1,candidate))
        cursor.execute('''update vector_heads set revision=%s,
            generation_id=%s,last_generation_id=%s,snapshot_version=1
            where tenant_id=%s and vector_kind=%s and namespace=%s
            and index_key=%s''',
            (head['revision']+1,candidate,candidate,exact['tenant_id'],
             exact['vector_kind'],exact['namespace'],exact['index_key']))
    if published_state == 'retired':
        successor = uuid4()
        with db.transaction() as cursor:
            cursor.execute('''insert into vector_generations
                (generation_id,tenant_id,vector_kind,namespace,index_key,
                 base_revision,index_revision,owner,user_lease_token,
                 user_lease_version,task_id,task_lease_token,
                 task_lease_version,state)
                select %s,tenant_id,vector_kind,namespace,index_key,%s,
                       index_revision,owner,user_lease_token,user_lease_version,
                       task_id,task_lease_token,task_lease_version,'staging'
                from vector_generations where generation_id=%s''',
                (successor,head['revision']+1,candidate))
            cursor.execute('''update vector_generations set state='sealed',
                expected_count=%s,content_digest=%s,
                sealed_at=clock_timestamp() where generation_id=%s''',
                (exact['expected_count'],exact['content_digest'],successor))
            cursor.execute('''update vector_generations set state='retired'
                where generation_id=%s''',(candidate,))
            cursor.execute('''update vector_generations set state='published',
                publication_revision=%s,publication_snapshot_version=2,
                published_at=clock_timestamp() where generation_id=%s''',
                (head['revision']+2,successor))
            cursor.execute('''update vector_heads set revision=%s,
                generation_id=%s,last_generation_id=%s,snapshot_version=2
                where tenant_id=%s and vector_kind=%s and namespace=%s
                and index_key=%s''',
                (head['revision']+2,successor,successor,exact['tenant_id'],
                 exact['vector_kind'],exact['namespace'],exact['index_key']))
    key, claim = _claim_due_exact(db,user,attempt)
    assert ImportPublicationProofService(db).prove_exact(*key) is None
    with db.transaction() as cursor:
        original = tuple(cursor.execute(query,params).fetchall() for query,params in (
            ('select * from import_tasks where id=%s',(key[1],)),
            ('''select * from import_task_attempts where task_id=%s
                and lease_version=%s''',key[1:]),
            ('''select * from generation_reservations where user_id=%s
                and task_id=%s and task_lease_version=%s
                order by vector_kind''',key),
            ('''select * from vector_generations where generation_id=%s''',
             (candidate,)),
            ('''select * from user_publication_gates where user_id=%s''',
             (user,)),
        ))
    assert PostgresImportPublicationRecoveryRepository(db).prove_or_hold(claim) == 'manual_hold'
    with db.transaction() as cursor:
        current = tuple(cursor.execute(query,params).fetchall() for query,params in (
            ('select * from import_tasks where id=%s',(key[1],)),
            ('''select * from import_task_attempts where task_id=%s
                and lease_version=%s''',key[1:]),
            ('''select * from generation_reservations where user_id=%s
                and task_id=%s and task_lease_version=%s
                order by vector_kind''',key),
            ('''select * from vector_generations where generation_id=%s''',
             (candidate,)),
            ('''select * from user_publication_gates where user_id=%s''',
             (user,)),
        ))
        header = cursor.execute('''select phase,observation_reason from
            import_publication_evidence where user_id=%s and task_id=%s
            and task_lease_version=%s''',key).fetchone()
        queue = cursor.execute('''select state from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            key).fetchone()
    assert current == original
    assert header['phase'] == 'pair_sealed'
    assert header['observation_reason'] == queue['state'] == 'manual_hold'


def test_task8_caller_rollback_restores_every_abandonment_write(memory_publication):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key, claim = _claim_due_exact(db,user,attempt)
    repository = PostgresImportPublicationRecoveryRepository(db)
    class RollbackProbe(RuntimeError):
        pass
    with pytest.raises(RollbackProbe):
        with db.transaction() as cursor:
            cursor.execute('begin')
            repository.abandon_exact_in_transaction(cursor,claim)
            assert cursor.execute('''select count(*) as n from generation_reservations
                where user_id=%s and task_id=%s and task_lease_version=%s
                and state='revoked' ''',key).fetchone()['n'] == 2
            raise RollbackProbe()
    with db.transaction() as cursor:
        reservations = cursor.execute('''select state from generation_reservations
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchall()
        task = cursor.execute('select status from import_tasks where id=%s',
                              (key[1],)).fetchone()
        queue = cursor.execute('''select state from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
    assert [r['state'] for r in reservations] == ['reserved','reserved']
    assert task['status'] == 'running' and queue['state'] == 'claimed'
    assert repository.prove_or_hold(claim) == 'abandoned'


@pytest.mark.parametrize('live_side', ['task','user'])
def test_task8_d1_one_live_ordinary_lease_defers_after_capture(
        memory_publication,live_side):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key, claim = _claim_due_exact(db,user,attempt)
    with db.transaction() as cursor:
        if live_side == 'task':
            cursor.execute('''update import_tasks set lease_expires_at=
                clock_timestamp()+interval '90 seconds' where id=%s''',(key[1],))
        else:
            cursor.execute('''update user_mutation_leases set lease_expires_at=
                clock_timestamp()+interval '90 seconds' where user_id=%s''',(user,))
    assert PostgresImportPublicationRecoveryRepository(db).prove_or_hold(claim) == 'retry_later'
    with db.transaction() as cursor:
        queue = cursor.execute('''select state,due_at,transient_count from
            import_publication_recovery_queue where user_id=%s and task_id=%s
            and task_lease_version=%s''',key).fetchone()
        task = cursor.execute('select status,lease_expires_at from import_tasks where id=%s',
                              (key[1],)).fetchone()
        ordinary = cursor.execute('''select lease_expires_at from user_mutation_leases
            where user_id=%s''',(user,)).fetchone()
        reservation_count = cursor.execute('''select count(*) as n from generation_reservations
            where user_id=%s and task_id=%s and task_lease_version=%s
            and state='reserved' ''',key).fetchone()['n']
    assert queue['state'] == 'pending' and queue['transient_count'] == 0
    assert queue['due_at'] > max(task['lease_expires_at'],ordinary['lease_expires_at'])
    assert task['status'] == 'running' and reservation_count == 2


def test_task8_six_persisted_transients_then_seventh_manual_hold(
        memory_publication,monkeypatch):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key, claim = _claim_due_exact(db,user,attempt)
    def unavailable(*args):
        raise PublicationProofUnknown(AttemptKey(*key),'evidence')
    monkeypatch.setattr(ImportPublicationProofService,'prove_exact',unavailable)
    repository = PostgresImportPublicationRecoveryRepository(db)
    for count in range(1,8):
        result = repository.prove_or_hold(claim)
        assert result == ('manual_hold' if count == 7 else 'retry_later')
        with db.transaction() as cursor:
            queue = cursor.execute('''select state,transient_count,due_at,
                reason_code from import_publication_recovery_queue
                where user_id=%s and task_id=%s and task_lease_version=%s''',
                key).fetchone()
        assert queue['transient_count'] == count
        if count == 7:
            assert queue['state'] == 'manual_hold' and queue['due_at'] is None
            assert queue['reason_code'] == 'manual_hold'
            break
        assert queue['state'] == 'pending' and queue['reason_code'] == 'transient'
        with db.transaction() as cursor:
            cursor.execute('''update import_publication_recovery_queue
                set due_at=clock_timestamp(),queue_version=queue_version+1
                where user_id=%s and task_id=%s and task_lease_version=%s
                and state='pending' ''',key)
        claim = repository.claim_next('task8')
        assert claim is not None


def test_task8_transient_without_safe_database_cas_preserves_capture(
        memory_publication,monkeypatch):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key, claim = _claim_due_exact(db,user,attempt)
    def unavailable(*args):
        raise PublicationProofUnknown(AttemptKey(*key),'evidence')
    monkeypatch.setattr(ImportPublicationProofService,'prove_exact',unavailable)
    class BrokenDatabase:
        def transaction(self):
            raise psycopg.OperationalError('disposable unavailable')
    with pytest.raises(RecoveryUnavailable):
        PostgresImportPublicationRecoveryRepository(BrokenDatabase()).prove_or_hold(claim)
    with db.transaction() as cursor:
        queue = cursor.execute('''select state,claim_token,queue_version,
            transient_count from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            key).fetchone()
    assert queue['state'] == 'claimed' and queue['claim_token'] == claim.queue_claim_token
    assert queue['queue_version'] == claim.queue_version and queue['transient_count'] == 0


def test_task8_stale_recovery_lease_cannot_write_result(memory_publication):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key, claim = _claim_due_exact(db,user,attempt)
    with db.transaction() as cursor:
        cursor.execute('''update import_publication_recovery_leases
            set expires_at=clock_timestamp()-interval '1 second'
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            key)
    with pytest.raises(RecoveryLeaseLost):
        PostgresImportPublicationRecoveryRepository(db).prove_or_hold(claim)
    with db.transaction() as cursor:
        reservations = cursor.execute('''select state from generation_reservations
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchall()
        queue = cursor.execute('''select state from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
    assert [r['state'] for r in reservations] == ['reserved','reserved']
    assert queue['state'] == 'claimed'


def test_task8_revoked_ids_remain_forbidden_after_physical_cleanup_and_new_owner(
        memory_publication,monkeypatch):
    service, db, _, user, attempt, live = _issue(memory_publication)
    issue = service._require_live_issue(live)
    def stop_after_rag_stage(scope,owner,head,corpus,*,snapshot_version,
                             generation_id,live):
        issue.rag_authority.stage(scope,owner,expected_revision=head.revision,
                                  generation_id=generation_id,live=live)
        raise RuntimeError('stop after rag stage')
    monkeypatch.setattr(issue.rag_service,'_prepare_sealed',stop_after_rag_stage)
    with pytest.raises(DurablePublicationUnknown):
        service._execute_live_publication(live)
    key, claim = _claim_due_exact(db,user,attempt)
    assert PostgresImportPublicationRecoveryRepository(db).prove_or_hold(claim) == 'abandoned'
    assert service.pair.rag.cleanup_abandoned(issue.plan.rag_scope,
        issue.plan.candidate_ids[0]) >= 0
    new_attempt = PostgresImportLeaseRepository(db).claim_next('new-owner')
    assert new_attempt is not None and new_attempt.lease_version > attempt.lease_version
    for scope,authority,generation_id in (
            (issue.plan.rag_scope,issue.rag_authority,issue.plan.candidate_ids[0]),
            (service.episode_scope,issue.episode_authority,issue.plan.candidate_ids[1])):
        expected = authority.read_head(scope).revision
        with pytest.raises(ImportLeaseLost):
            authority.stage(scope,attempt,expected_revision=expected,
                            generation_id=generation_id)
        with pytest.raises(VectorAuthorityError, match='permanently reserved'):
            authority.stage(scope,new_attempt,expected_revision=expected,
                            generation_id=generation_id)


@pytest.mark.parametrize('stage_commits', [False,True])
def test_task8_old_stage_user_lock_barrier_rechecks_after_commit_or_rollback(
        memory_publication,monkeypatch,stage_commits):
    service, db, _, user, attempt, live = _issue(memory_publication)
    issue = service._require_live_issue(live)
    authority = issue.rag_authority
    key = (user,attempt.task.task_id,attempt.lease_version)
    locked = Event()
    release = Event()
    original_owner = authority._owner
    called = []
    def owner_barrier(cursor,owner,scope):
        result = original_owner(cursor,owner,scope)
        if not called:
            called.append(True)
            locked.set()
            assert release.wait(12)
        return result
    monkeypatch.setattr(authority,'_owner',owner_barrier)
    def stop_after_stage(scope,owner,head,corpus,*,snapshot_version,
                         generation_id,live):
        with db.transaction() as cursor:
            seconds = 8 if stage_commits else 1
            cursor.execute('''update import_tasks set lease_expires_at=
                clock_timestamp()+%s * interval '1 second' where id=%s''',
                (seconds,key[1]))
            cursor.execute('''update user_mutation_leases set lease_expires_at=
                clock_timestamp()+%s * interval '1 second' where user_id=%s''',
                (seconds,user))
            cursor.execute('''update import_publication_recovery_queue set
                due_at=clock_timestamp(),reason_code='unknown',
                queue_version=queue_version+1 where user_id=%s and task_id=%s
                and task_lease_version=%s''',key)
        authority.stage(scope,owner,expected_revision=head.revision,
                        generation_id=generation_id,live=live)
        raise RuntimeError('stop after stage')
    monkeypatch.setattr(issue.rag_service,'_prepare_sealed',stop_after_stage)
    def old_stage():
        with pytest.raises((DurablePublicationUnknown,ImportLeaseLost)):
            service._execute_live_publication(live)
    with ThreadPoolExecutor(max_workers=2) as pool:
        stage_future = pool.submit(old_stage)
        assert locked.wait(15)
        if not stage_commits:
            time.sleep(1.2)
        recovery_future = pool.submit(
            PostgresImportPublicationRecoveryRepository(db).claim_next,'task8')
        deadline = time.monotonic()+8
        while time.monotonic()<deadline:
            with db.transaction() as cursor:
                capture = cursor.execute('''select state,claim_token from
                    import_publication_recovery_queue where user_id=%s
                    and task_id=%s and task_lease_version=%s''',key).fetchone()
            if capture['state']=='claimed':
                break
            time.sleep(.05)
        else:
            pytest.fail('Queue capture did not commit while old stage held user lock')
        assert not recovery_future.done()
        release.set()
        stage_future.result(timeout=15)
        claim = recovery_future.result(timeout=15)
    with db.transaction() as cursor:
        generation = cursor.execute('''select state from vector_generations
            where generation_id=%s''',(issue.plan.candidate_ids[0],)).fetchone()
    if stage_commits:
        assert generation is not None and generation['state']=='staging'
        assert claim is None
        _, claim = _claim_due_exact(db,user,attempt)
    else:
        assert generation is None and claim is not None
    assert ImportPublicationProofService(db).prove_exact(*key) is None
    assert PostgresImportPublicationRecoveryRepository(db).prove_or_hold(claim) == 'abandoned'
    with db.transaction() as cursor:
        reservations = cursor.execute('''select vector_kind,state from
            generation_reservations where user_id=%s and task_id=%s
            and task_lease_version=%s order by vector_kind''',key).fetchall()
        task = cursor.execute('''select status from import_tasks where id=%s''',
                              (key[1],)).fetchone()
        audit = cursor.execute('''select end_reason from import_task_attempts
            where task_id=%s and lease_version=%s''',key[1:]).fetchone()
        gate = cursor.execute('''select status from user_publication_gates
            where user_id=%s''',(user,)).fetchone()
        queue = cursor.execute('''select state from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            key).fetchone()
        after = cursor.execute('''select state from vector_generations
            where generation_id=%s''',(issue.plan.candidate_ids[0],)).fetchone()
        head = cursor.execute('''select last_generation_id from vector_heads
            where tenant_id=%s and vector_kind=%s and namespace=%s
            and index_key=%s''',issue.plan.rag_scope.key).fetchone()
    assert [(r['vector_kind'],r['state']) for r in reservations] == [
        ('episode','revoked'),('rag','revoked')]
    assert task['status']=='retry_wait' and audit['end_reason']=='lease_expired'
    assert gate['status']=='resolved' and queue['state']=='resolved'
    assert (None if after is None else after['state']) == (
        'abandoned' if stage_commits else None)
    assert head['last_generation_id'] != issue.plan.candidate_ids[0]
    new_attempt = PostgresImportLeaseRepository(db).claim_next('new-owner')
    assert new_attempt is not None and new_attempt.lease_version > attempt.lease_version
    for scope, candidate_authority, generation_id in (
            (issue.plan.rag_scope,issue.rag_authority,issue.plan.candidate_ids[0]),
            (service.episode_scope,issue.episode_authority,
             issue.plan.candidate_ids[1])):
        expected = candidate_authority.read_head(scope).revision
        with pytest.raises(ImportLeaseLost):
            candidate_authority.stage(scope,attempt,
                expected_revision=expected,generation_id=generation_id)
        with pytest.raises(VectorAuthorityError,match='permanently reserved'):
            candidate_authority.stage(scope,new_attempt,
                expected_revision=expected,generation_id=generation_id)
        with pytest.raises(VectorAuthorityError):
            with db.transaction() as cursor:
                candidate_authority._publish(cursor,scope,new_attempt,
                    generation_id,expected_revision=expected,
                    expected_index_revision=1,snapshot_version=1)


def test_task8_absent_uuid_revocation_commits_before_old_and_new_stage(
        memory_publication,monkeypatch):
    service, db, _, user, attempt, live = _issue(memory_publication)
    other = memory_publication[-1]
    issue = service._require_live_issue(live)
    key = (user,attempt.task.task_id,attempt.lease_version)
    stage_ready = Event()
    release_stage = Event()
    def old_stage_after_barrier(scope,owner,head,corpus,*,snapshot_version,
                                generation_id,live):
        stage_ready.set()
        assert release_stage.wait(15)
        issue.rag_authority.stage(scope,owner,expected_revision=head.revision,
                                  generation_id=generation_id,live=live)
        raise RuntimeError('old stage unexpectedly committed')
    monkeypatch.setattr(issue.rag_service,'_prepare_sealed',old_stage_after_barrier)
    def old_worker():
        with pytest.raises((DurablePublicationUnknown,ImportLeaseLost,
                            PublicationEvidenceError)):
            service._execute_live_publication(live)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(old_worker)
        try:
            assert stage_ready.wait(15)
            with db.transaction() as cursor:
                absent = cursor.execute('''select count(*) as n from vector_generations
                    where generation_id in (%s,%s)''',issue.plan.candidate_ids).fetchone()
            assert absent['n'] == 0
            other_lease = PostgresUserMutationCoordinator(db).acquire(
                other,'unrelated-progress',lease_seconds=10)
            assert other_lease is not None
            assert PostgresUserMutationCoordinator(db).release(other_lease)
            _, claim = _claim_due_exact(db,user,attempt)
            assert PostgresImportPublicationRecoveryRepository(db).prove_or_hold(claim) == 'abandoned'
            with db.transaction() as cursor:
                states = cursor.execute('''select vector_kind,state from generation_reservations
                    where user_id=%s and task_id=%s and task_lease_version=%s
                    order by vector_kind''',key).fetchall()
            assert [(r['vector_kind'],r['state']) for r in states] == [
                ('episode','revoked'),('rag','revoked')]
        finally:
            release_stage.set()
        future.result(timeout=15)
    new_attempt = PostgresImportLeaseRepository(db).claim_next('new-owner')
    assert new_attempt is not None
    for scope,authority,generation_id in (
            (issue.plan.rag_scope,issue.rag_authority,issue.plan.candidate_ids[0]),
            (service.episode_scope,issue.episode_authority,issue.plan.candidate_ids[1])):
        with pytest.raises(VectorAuthorityError, match='permanently reserved'):
            authority.stage(scope,new_attempt,
                expected_revision=authority.read_head(scope).revision,
                generation_id=generation_id)


def test_task8_fault_after_gate_write_rolls_back_closure_both_ids_and_metadata(
        memory_publication,monkeypatch):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key, claim = _claim_due_exact(db,user,attempt)
    assert ImportPublicationProofService(db).prove_exact(*key) is None
    repository = PostgresImportPublicationRecoveryRepository(db)
    original = repository._resolve_gate
    def fault_after_gate(cursor, exact_claim):
        original(cursor,exact_claim)
        raise RuntimeError('injected after gate write')
    monkeypatch.setattr(repository,'_resolve_gate',fault_after_gate)
    with pytest.raises(RuntimeError, match='injected after gate write'):
        repository.abandon_exact(claim)
    with db.transaction() as cursor:
        task = cursor.execute('select status from import_tasks where id=%s',
                              (key[1],)).fetchone()
        audit = cursor.execute('''select ended_at from import_task_attempts
            where task_id=%s and lease_version=%s''',key[1:]).fetchone()
        states = cursor.execute('''select state from generation_reservations
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchall()
        header = cursor.execute('''select phase from import_publication_evidence
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
        gate = cursor.execute('''select status from user_publication_gates
            where user_id=%s''',(user,)).fetchone()
        queue = cursor.execute('''select state from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
    assert task['status'] == 'running' and audit['ended_at'] is None
    assert [r['state'] for r in states] == ['reserved','reserved']
    assert header['phase'] == 'intent' and gate['status'] == 'unresolved'
    assert queue['state'] == 'claimed'
    monkeypatch.setattr(repository,'_resolve_gate',original)
    assert repository.prove_or_hold(claim) == 'abandoned'


def test_task8_direct_writers_reject_forged_capture_and_premature_resolution(
        memory_publication):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key, claim = _claim_due_exact(db,user,attempt)
    repository = PostgresImportPublicationRecoveryRepository(db)
    with db.transaction() as cursor:
        frozen = PostgresImportPublicationEvidenceRepository(
            db)._read_exact_in_cursor(cursor,AttemptKey(*key))

    def state():
        with db.transaction() as cursor:
            return tuple(cursor.execute(query,key).fetchall() for query in (
                '''select * from import_publication_evidence where user_id=%s
                   and task_id=%s and task_lease_version=%s''',
                '''select * from import_publication_private_payloads where user_id=%s
                   and task_id=%s and task_lease_version=%s''',
                '''select * from user_publication_gates where user_id=%s
                   and task_id=%s and task_lease_version=%s''',
                '''select * from import_publication_recovery_leases where user_id=%s
                   and task_id=%s and task_lease_version=%s''',
                '''select * from import_publication_recovery_queue where user_id=%s
                   and task_id=%s and task_lease_version=%s''',
                '''select * from generation_reservations where user_id=%s
                   and task_id=%s and task_lease_version=%s order by vector_kind''',
            ))

    before = state()
    forged = replace(claim,recovery_token=uuid4())
    for exact_claim, operation in (
            (forged,lambda cursor,c: repository._close_capture(
                cursor,c,'manual_hold','manual_hold')),
            (forged,lambda cursor,c: repository._resolve_gate(cursor,c)),
            (forged,lambda cursor,c: repository._transition_metadata(
                cursor,c,frozen,'abandoned')),
            (claim,lambda cursor,c: repository._close_capture(
                cursor,c,'abandoned')),
            (claim,lambda cursor,c: repository._resolve_gate(cursor,c)),
            (claim,lambda cursor,c: repository._transition_metadata(
                cursor,c,frozen,'abandoned'))):
        with pytest.raises(RuntimeError):
            with db.transaction() as cursor:
                operation(cursor,exact_claim)
        assert state() == before


def test_task8_terminal_without_complete_proof_stays_succeeded_and_held(
        memory_publication,monkeypatch):
    service, db, _, user, attempt, live = _issue(memory_publication)
    service._execute_live_publication(live)
    key = (user,attempt.task.task_id,attempt.lease_version)
    with db.transaction() as cursor:
        cursor.execute('''update import_publication_recovery_queue set
            due_at=clock_timestamp(),reason_code='unknown',
            queue_version=queue_version+1 where user_id=%s and task_id=%s
            and task_lease_version=%s''',key)
    claim = PostgresImportPublicationRecoveryRepository(db).claim_next('task8')
    assert claim is not None
    monkeypatch.setattr(ImportPublicationProofService,'prove_exact',
                        lambda *args: None)
    assert PostgresImportPublicationRecoveryRepository(db).prove_or_hold(claim) == 'manual_hold'
    with db.transaction() as cursor:
        task = cursor.execute('select status from import_tasks where id=%s',
                              (key[1],)).fetchone()
        header = cursor.execute('''select phase,observation_reason from
            import_publication_evidence where user_id=%s and task_id=%s
            and task_lease_version=%s''',key).fetchone()
        queue = cursor.execute('''select state from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
        reservations = cursor.execute('''select state from generation_reservations
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchall()
    assert task['status'] == 'succeeded'
    assert header['phase'] == 'terminal_committed'
    assert header['observation_reason'] == 'manual_hold'
    assert queue['state'] == 'manual_hold'
    assert [r['state'] for r in reservations] == ['reserved','reserved']


def test_task8_expiry_during_user_lock_wait_loses_before_any_write(
        memory_publication):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key, claim = _claim_due_exact(db,user,attempt,lease_seconds=1)
    repository = PostgresImportPublicationRecoveryRepository(db)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.transaction() as cursor:
            cursor.execute('select id from users where id=%s for update',(user,))
            waiting = pool.submit(repository.prove_or_hold,claim)
            time.sleep(1.2)
            assert not waiting.done()
        # The worker remains blocked until the transaction releases the user.
        with pytest.raises(RecoveryLeaseLost):
            waiting.result(timeout=15)
    with db.transaction() as cursor:
        states = cursor.execute('''select state from generation_reservations
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchall()
        queue = cursor.execute('''select state from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
    assert [r['state'] for r in states] == ['reserved','reserved']
    assert queue['state'] == 'claimed'


@pytest.mark.parametrize('crash_at', ['document_verified','pair_sealed',
                                      'terminal_rollback'])
def test_task8_preterminal_crash_rows_prove_first_then_revoke(
        memory_publication,monkeypatch,crash_at):
    service, db, _, user, attempt, live = _issue(memory_publication)
    issue = service._require_live_issue(live)
    if crash_at == 'document_verified':
        monkeypatch.setattr(issue.rag_service,'_prepare_sealed',
                            lambda *args,**kwargs: (_ for _ in ()).throw(
                                RuntimeError('after document result')))
    elif crash_at == 'pair_sealed':
        monkeypatch.setattr(issue.imports,'try_begin_committing',
                            lambda *args,**kwargs: (_ for _ in ()).throw(
                                RuntimeError('after both seals')))
    else:
        monkeypatch.setattr(service,'_terminal',
                            lambda *args,**kwargs: (_ for _ in ()).throw(
                                RuntimeError('terminal transaction rollback')))
    with pytest.raises(DurablePublicationUnknown):
        service._execute_live_publication(live)
    key = (user,attempt.task.task_id,attempt.lease_version)
    frozen = PostgresImportPublicationEvidenceRepository(db).read_exact(*key)
    assert frozen.phase == ('document_verified' if crash_at == 'document_verified'
                            else 'pair_sealed')
    assert ImportPublicationProofService(db).prove_exact(*key) is None
    _, claim = _claim_due_exact(db,user,attempt)
    assert PostgresImportPublicationRecoveryRepository(db).prove_or_hold(claim) == 'abandoned'
    with db.transaction() as cursor:
        reservations = cursor.execute('''select state from generation_reservations
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchall()
        gate = cursor.execute('''select status from user_publication_gates
            where user_id=%s''',(user,)).fetchone()
    assert [r['state'] for r in reservations] == ['revoked','revoked']
    assert gate['status'] == 'resolved'


@pytest.mark.parametrize('candidate_state', ['staging','sealed'])
def test_gated_direct_abandon_and_publication_do_not_enter_mutation(
        memory_publication, monkeypatch, candidate_state):
    service, db, _, user, attempt, live = _issue(memory_publication)
    issue = service._require_live_issue(live)
    authority = issue.rag_authority
    scope = issue.plan.rag_scope
    generation_id = issue.plan.candidate_ids[0]
    prepare = issue.rag_service._prepare_sealed
    def stop_after_candidate(scope, owner, head, corpus, *, snapshot_version,
                             generation_id, live):
        if candidate_state == 'staging':
            authority.stage(scope, owner, expected_revision=head.revision,
                            generation_id=generation_id, live=live)
        else:
            prepare(scope, owner, head, corpus,
                    snapshot_version=snapshot_version,
                    generation_id=generation_id, live=live)
        raise RuntimeError('stop after candidate')
    monkeypatch.setattr(issue.rag_service, '_prepare_sealed', stop_after_candidate)
    with pytest.raises(DurablePublicationUnknown):
        service._execute_live_publication(live)
    key = (user,attempt.task.task_id,attempt.lease_version)
    before = PostgresImportPublicationEvidenceRepository(db).read_exact(*key)
    with db.transaction() as cursor:
        state = cursor.execute('select state from vector_generations where generation_id=%s',
                               (generation_id,)).fetchone()['state']
        reservations = cursor.execute('''select vector_kind,state from generation_reservations
            where user_id=%s and task_id=%s and task_lease_version=%s
            order by vector_kind''',key).fetchall()
    assert state == candidate_state
    entered = []
    with pytest.raises(PublicationEvidenceError):
        authority.abandon(scope, attempt, generation_id)
    with pytest.raises(PublicationEvidenceError):
        with authority.coordinator.publication(attempt.user_lease):
            entered.append(True)
    assert not entered
    assert PostgresImportPublicationEvidenceRepository(db).read_exact(*key) == before
    with db.transaction() as cursor:
        assert cursor.execute('select state from vector_generations where generation_id=%s',
                              (generation_id,)).fetchone()['state'] == candidate_state
        assert cursor.execute('''select vector_kind,state from generation_reservations
            where user_id=%s and task_id=%s and task_lease_version=%s
            order by vector_kind''',key).fetchall() == reservations


def test_expired_evidence_closes_only_original_tuple_and_recovery_claims(
        memory_publication):
    service, db, _, user, attempt, _ = _issue(memory_publication)
    key = (user,attempt.task.task_id,attempt.lease_version)
    with db.transaction() as cursor:
        queue = cursor.execute('''select * from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
        assert queue is not None and queue['state']=='pending'
        cursor.execute('''update import_tasks set lease_expires_at=
            clock_timestamp()-interval '1 second' where id=%s''',(key[1],))
        cursor.execute('''update user_mutation_leases set lease_expires_at=
            clock_timestamp()-interval '1 second' where user_id=%s''',(user,))
    assert PostgresImportLeaseRepository(db).recover_expired()==1
    with db.transaction() as cursor:
        task = cursor.execute('select * from import_tasks where id=%s',(key[1],)).fetchone()
        audit = cursor.execute('''select * from import_task_attempts
            where task_id=%s and lease_version=%s''',key[1:]).fetchone()
        gate = cursor.execute('''select status from user_publication_gates
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
        reservations = cursor.execute('''select state from generation_reservations
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchall()
    assert task['status']=='retry_wait' and task['next_attempt_at'] is None
    assert task['error_code']=='needs_reconciliation'
    assert audit['end_reason']=='lease_expired' and audit['ended_at'] is not None
    assert gate['status']=='unresolved' and [r['state'] for r in reservations]==['reserved','reserved']
    assert PostgresImportLeaseRepository(db).claim_next('ordinary') is None
    # The original seed retains its scheduled expiry under D2 even after this
    # synthetic clock shortening; an exact Unknown may make proof due now.
    with db.transaction() as cursor:
        cursor.execute('''update import_publication_recovery_queue
            set due_at=clock_timestamp(),reason_code='unknown',
            queue_version=queue_version+1
            where user_id=%s and task_id=%s and task_lease_version=%s''',key)
    claim = PostgresImportPublicationRecoveryRepository(db).claim_next('recovery')
    assert claim is not None and (claim.user_id,claim.task_id,
        claim.task_lease_version)==key
    renewed = PostgresImportPublicationRecoveryRepository(db).renew(claim)
    assert renewed.queue_claim_token==claim.queue_claim_token
    assert renewed.recovery_token==claim.recovery_token
    assert renewed.queue_expires_at>claim.queue_expires_at
    assert renewed.recovery_expires_at>claim.recovery_expires_at


def test_due_live_attempt_defers_without_recovery_grant_or_transient_count(
        memory_publication):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key = (user,attempt.task.task_id,attempt.lease_version)
    with db.transaction() as cursor:
        cursor.execute('''update import_publication_recovery_queue
            set due_at=clock_timestamp(),reason_code='unknown',
            queue_version=queue_version+1
            where user_id=%s and task_id=%s and task_lease_version=%s''',key)
    repo = PostgresImportPublicationRecoveryRepository(db)
    assert repo.claim_next('recovery') is None
    with db.transaction() as cursor:
        queue = cursor.execute('''select * from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
        task = cursor.execute('select * from import_tasks where id=%s',(key[1],)).fetchone()
        ordinary = cursor.execute('''select * from user_mutation_leases
            where user_id=%s''',(user,)).fetchone()
        grant = cursor.execute('''select 1 from import_publication_recovery_leases
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
        gate = cursor.execute('''select status from user_publication_gates
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
    assert queue['state']=='pending' and queue['reason_code']=='ordinary_live'
    assert queue['transient_count']==0 and queue['claim_token'] is None
    assert queue['due_at']>max(task['lease_expires_at'],ordinary['lease_expires_at'])
    assert task['status']=='running' and gate['status']=='unresolved'
    assert grant is None


@pytest.mark.parametrize('live_ordinary', ['task','user'])
def test_d1_post_wait_one_sided_ordinary_expiry_defers_without_count(
        memory_publication,live_ordinary):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key = (user,attempt.task.task_id,attempt.lease_version)
    with db.transaction() as cursor:
        cursor.execute('''update import_publication_recovery_queue
            set due_at=clock_timestamp(),reason_code='unknown',
            queue_version=queue_version+1
            where user_id=%s and task_id=%s and task_lease_version=%s''',key)
        cursor.execute("update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second' where id=%s",(key[1],))
        cursor.execute("update user_mutation_leases set lease_expires_at=clock_timestamp()-interval '1 second' where user_id=%s",(user,))
    repo = PostgresImportPublicationRecoveryRepository(db)
    with ThreadPoolExecutor(max_workers=1) as workers:
        with db.transaction() as cursor:
            cursor.execute('select id from users where id=%s for update',(user,))
            if live_ordinary == 'task':
                cursor.execute("update import_tasks set lease_expires_at=clock_timestamp()+interval '30 seconds' where id=%s",(key[1],))
            else:
                cursor.execute("update user_mutation_leases set lease_expires_at=clock_timestamp()+interval '30 seconds' where user_id=%s",(user,))
            claiming = workers.submit(repo.claim_next,'recover')
            deadline = time.monotonic()+15
            while time.monotonic()<deadline:
                captured = cursor.execute('''select state from import_publication_recovery_queue
                    where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
                if captured['state']=='claimed':
                    break
                assert not claiming.done(), claiming.exception() if claiming.done() else None
                time.sleep(.05)
            else:
                pytest.fail('Queue capture did not commit before user authority wait')
        assert claiming.result(timeout=15) is None
    with db.transaction() as cursor:
        queue = cursor.execute('''select * from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
        task = cursor.execute('select lease_expires_at from import_tasks where id=%s',
                              (key[1],)).fetchone()
        lease = cursor.execute('select lease_expires_at from user_mutation_leases where user_id=%s',
                               (user,)).fetchone()
        grant = cursor.execute('''select 1 from import_publication_recovery_leases
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
    assert queue['state']=='pending' and queue['reason_code']=='ordinary_live'
    assert queue['transient_count']==0 and queue['claim_token'] is None
    assert queue['due_at'] > max(task['lease_expires_at'],lease['lease_expires_at'])
    assert grant is None


@pytest.mark.parametrize('disposition', ['pending','claimed','manual_hold','resolved'])
def test_d2_seed_preserves_existing_queue_disposition(memory_publication,disposition):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key = AttemptKey(user,attempt.task.task_id,attempt.lease_version)
    args = (key.user_id,key.task_id,key.task_lease_version)
    if disposition != 'pending':
        with db.transaction() as cursor:
            cursor.execute('''update import_publication_recovery_queue
                set due_at=clock_timestamp(),reason_code='unknown',
                queue_version=queue_version+1
                where user_id=%s and task_id=%s and task_lease_version=%s''',args)
            cursor.execute("update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second' where id=%s",(key.task_id,))
            cursor.execute("update user_mutation_leases set lease_expires_at=clock_timestamp()-interval '1 second' where user_id=%s",(user,))
        claim = PostgresImportPublicationRecoveryRepository(db).claim_next(
            'recover',lease_seconds=1 if disposition in ('manual_hold','resolved') else 60)
        assert claim is not None
        if disposition in ('manual_hold','resolved'):
            time.sleep(1.2)
            with db.transaction() as cursor:
                cursor.execute('''update import_publication_recovery_queue
                    set state=%s,due_at=null,queue_version=queue_version+1,
                    claim_token=null,claim_expires_at=null,reason_code=%s
                    where user_id=%s and task_id=%s and task_lease_version=%s''',
                    (disposition,'manual_hold' if disposition == 'manual_hold'
                     else 'proved_succeeded',*args))
    with db.transaction() as cursor:
        before = cursor.execute('''select * from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',args).fetchone()
        task_expiry = cursor.execute('select lease_expires_at from import_tasks where id=%s',
                                     (key.task_id,)).fetchone()['lease_expires_at']
        user_expiry = cursor.execute('select lease_expires_at from user_mutation_leases where user_id=%s',
                                     (user,)).fetchone()['lease_expires_at']
        if disposition == 'resolved':
            with pytest.raises(RecoveryLeaseLost):
                seed_missing_queue_in_transaction(cursor,key,task_expiry,user_expiry)
        else:
            seeded = seed_missing_queue_in_transaction(cursor,key,task_expiry,user_expiry)
            assert seeded == before
    with db.transaction() as cursor:
        assert cursor.execute('''select * from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',args).fetchone() == before


@pytest.mark.parametrize('disposition', ['claimed', 'manual_hold', 'resolved'])
def test_schedule_unknown_requires_existing_exact_queue_and_preserves_nonpending(
        memory_publication, disposition):
    service, db, _, user, attempt, live = _issue(memory_publication)
    key = AttemptKey(user, attempt.task.task_id, attempt.lease_version)
    args = (key.user_id, key.task_id, key.task_lease_version)
    recovery = PostgresImportPublicationRecoveryRepository(db)
    with db.transaction() as cursor:
        before_count = cursor.execute('''select count(*) as n from
            import_publication_recovery_queue''').fetchone()['n']
    with pytest.raises(RecoveryUnavailable):
        recovery.schedule_unknown(AttemptKey(user, key.task_id,
                                            key.task_lease_version + 1))
    with db.transaction() as cursor:
        assert cursor.execute('''select count(*) as n from
            import_publication_recovery_queue''').fetchone()['n'] == before_count
    if disposition == 'resolved':
        assert service._execute_live_publication(live).pair.import_task.status == 'succeeded'
    _, claim = _claim_due_exact(db, user, attempt)
    assert claim is not None
    if disposition == 'manual_hold':
        assert recovery._manual_hold(claim) == 'manual_hold'
    elif disposition == 'resolved':
        assert recovery.prove_or_hold(claim) == 'proved_succeeded'
    before_rows = _gated_mutation_rows(db, user)
    with db.transaction() as cursor:
        before = cursor.execute('''select * from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''', args).fetchone()
    assert before['state'] == disposition
    assert recovery.schedule_unknown(key) == disposition
    with db.transaction() as cursor:
        assert cursor.execute('''select * from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''', args).fetchone() == before
    assert _gated_mutation_rows(db, user) == before_rows


def test_schedule_unknown_uses_fresh_clock_after_user_lock_wait(
        memory_publication):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key = AttemptKey(user, attempt.task.task_id, attempt.lease_version)
    args = (key.user_id, key.task_id, key.task_lease_version)
    recovery = PostgresImportPublicationRecoveryRepository(db)
    with db.transaction() as cursor:
        cursor.execute('''update import_tasks set lease_expires_at=
            clock_timestamp()-interval '1 second' where id=%s''', (key.task_id,))
        cursor.execute('''update user_mutation_leases set lease_expires_at=
            clock_timestamp()-interval '1 second' where user_id=%s''', (user,))
        cursor.execute('''update import_publication_recovery_queue set
            due_at=clock_timestamp()-interval '1 second',queue_version=queue_version+1
            where user_id=%s and task_id=%s and task_lease_version=%s''', args)
    with ThreadPoolExecutor(max_workers=1) as workers:
        with db.transaction() as cursor:
            cursor.execute('select id from users where id=%s for update', (user,))
            scheduled = workers.submit(recovery.schedule_unknown, key)
            time.sleep(.2)
            assert not scheduled.done()
            released_after = cursor.execute('select clock_timestamp() as now').fetchone()['now']
        assert scheduled.result(timeout=20) == 'pending'
    with db.transaction() as cursor:
        queue = cursor.execute('''select state,due_at,reason_code from
            import_publication_recovery_queue where user_id=%s and task_id=%s
            and task_lease_version=%s''', args).fetchone()
    assert queue['state'] == 'pending' and queue['reason_code'] == 'unknown'
    assert queue['due_at'] >= released_after


def test_recovery_renewal_is_all_or_nothing_and_expired_capture_takes_over(
        memory_publication):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key = (user,attempt.task.task_id,attempt.lease_version)
    with db.transaction() as cursor:
        cursor.execute('''update import_tasks set lease_expires_at=
            clock_timestamp()-interval '1 second' where id=%s''',(key[1],))
        cursor.execute('''update user_mutation_leases set lease_expires_at=
            clock_timestamp()-interval '1 second' where user_id=%s''',(user,))
        cursor.execute('''update import_publication_recovery_queue
            set due_at=clock_timestamp(),reason_code='unknown',
            queue_version=queue_version+1
            where user_id=%s and task_id=%s and task_lease_version=%s''',key)
    repo = PostgresImportPublicationRecoveryRepository(db)
    first = repo.claim_next('first',lease_seconds=1)
    assert first is not None
    with pytest.raises(RecoveryLeaseLost):
        repo.renew(replace(first,queue_claim_token=attempt.lease_token))
    with db.transaction() as cursor:
        assert cursor.execute('''select claim_expires_at from
            import_publication_recovery_queue where user_id=%s and task_id=%s
            and task_lease_version=%s''',key).fetchone()['claim_expires_at']==first.queue_expires_at
        assert cursor.execute('''select expires_at from
            import_publication_recovery_leases where user_id=%s and task_id=%s
            and task_lease_version=%s''',key).fetchone()['expires_at']==first.recovery_expires_at
    time.sleep(1.2)
    with pytest.raises(RecoveryLeaseLost):
        repo.renew(first)
    second = repo.claim_next('second')
    assert second is not None
    assert second.queue_claim_token!=first.queue_claim_token
    assert second.recovery_token!=first.recovery_token
    assert second.queue_version>first.queue_version
    assert second.recovery_version>first.recovery_version
    with pytest.raises(RecoveryLeaseLost):
        repo.renew(first)


def test_terminal_release_is_original_one_use_and_closed_after_completion(
        memory_publication, monkeypatch):
    service, db, _, user, attempt, live = _issue(memory_publication)
    original_finish = PostgresImportLeaseRepository._finish
    seen = []

    def checked_finish(self, cursor, current, row, status, reason, **kwargs):
        binding = kwargs['terminal_release']
        original_issue = live_module._RELEASE_BINDINGS[binding]
        before = cursor.execute('''select status,stage,progress from import_tasks
            where id=%s''',(current.task.task_id,)).fetchone()
        with pytest.raises(ImportMemoryPublicationError,
                           match='Original terminal import receiver required'):
            original_finish(PostgresImportLeaseRepository(db),
                cursor,current,row,status,reason,**kwargs)
        assert cursor.execute('''select status,stage,progress from import_tasks
            where id=%s''',(current.task.task_id,)).fetchone() == before
        assert not original_issue.finish_done
        assert live_module._ADMISSIONS[
            live_module._RELEASE_BINDINGS[binding].admission].active is False
        for invalid in (live_module._TerminalReleaseBinding(), copy(binding),
                        type('Derived', (live_module._TerminalReleaseBinding,), {})()):
            with pytest.raises(ImportMemoryPublicationError):
                live_module._require_terminal_release_binding(
                    invalid,cursor,current,operation='finish')
        with cursor.connection.cursor() as other_cursor:
            with pytest.raises(ImportMemoryPublicationError):
                live_module._require_terminal_release_binding(
                    binding,other_cursor,current,operation='finish')
        issue = live_module._RELEASE_BINDINGS[binding]
        admission_issue = live_module._ADMISSIONS[issue.admission]
        live_module._ADMISSIONS[issue.admission] = copy(admission_issue)
        try:
            with pytest.raises(ImportMemoryPublicationError,
                               match='provenance changed'):
                live_module._require_terminal_release_binding(
                    binding,cursor,current,operation='finish')
        finally:
            live_module._ADMISSIONS[issue.admission] = admission_issue
        assert not issue.finish_done
        original_txid = issue.transaction_id
        issue.transaction_id += 1
        try:
            with pytest.raises(ImportMemoryPublicationError):
                live_module._require_terminal_release_binding(
                    binding,cursor,current,operation='finish')
        finally:
            issue.transaction_id = original_txid
        original_service = issue.service
        issue.service = object()
        try:
            with pytest.raises(ImportMemoryPublicationError):
                live_module._require_terminal_release_binding(
                    binding,cursor,current,operation='finish')
        finally:
            issue.service = original_service
        with pytest.raises(ImportMemoryPublicationError):
            live_module._require_terminal_release_binding(
                binding,cursor,replace(current),operation='finish')
        original_receiver = service.pair.imports
        service.pair.imports = PostgresImportLeaseRepository(db)
        try:
            with pytest.raises(ImportMemoryPublicationError):
                live_module._require_terminal_release_binding(
                    binding,cursor,current,operation='finish')
        finally:
            service.pair.imports = original_receiver
        original_coordinator = original_receiver.coordinator
        original_receiver.coordinator = type(original_coordinator)(db)
        try:
            with pytest.raises(ImportMemoryPublicationError):
                live_module._require_terminal_release_binding(
                    binding,cursor,current,operation='finish')
        finally:
            original_receiver.coordinator = original_coordinator
        with pytest.raises(ImportMemoryPublicationError):
            live_module._require_terminal_release_binding(
                binding,cursor,current,operation='release')
        seen.append(binding)
        return original_finish(self,cursor,current,row,status,reason,**kwargs)

    original_release = PostgresUserMutationCoordinator.release_in_transaction

    def checked_release(self, cursor, handle, *, terminal_release=None):
        if terminal_release is not None:
            binding = live_module._RELEASE_BINDINGS[terminal_release]
            before = cursor.execute('''select lease_expires_at from
                user_mutation_leases where user_id=%s''',(handle.user_id,)).fetchone()
            with pytest.raises(ImportMemoryPublicationError,
                               match='Original terminal coordinator required'):
                original_release(PostgresUserMutationCoordinator(db),cursor,handle,
                                 terminal_release=terminal_release)
            assert cursor.execute('''select lease_expires_at from
                user_mutation_leases where user_id=%s''',(handle.user_id,)).fetchone() == before
            assert not binding.release_done
        return original_release(self,cursor,handle,terminal_release=terminal_release)

    monkeypatch.setattr(PostgresImportLeaseRepository,'_finish',checked_finish)
    monkeypatch.setattr(PostgresUserMutationCoordinator,'release_in_transaction',
                        checked_release)
    result = service._execute_live_publication(live)
    assert result.pair.import_task.status=='succeeded' and len(seen)==1
    with db.transaction() as cursor:
        with pytest.raises(ImportMemoryPublicationError):
            live_module._require_terminal_release_binding(
                seen[0],cursor,attempt,operation='finish')


@pytest.mark.parametrize('changed', ['started_at', 'batch_id'])
def test_terminal_finish_refuses_caller_bookkeeping_row_before_consumption(
        memory_publication, monkeypatch, changed):
    _, db, _, _, _, _, _, _, user, _ = memory_publication
    other_batch = str(uuid4())
    with db.transaction() as cursor:
        cursor.execute('''insert into import_batches(id,user_id,created_at,updated_at)
            values(%s,%s,'before','before')''',(other_batch,user))
    service, _, _, _, attempt, live = _issue(memory_publication)
    original_finish = PostgresImportLeaseRepository._finish
    tested = []

    def checked_finish(self, cursor, current, row, status, reason, **kwargs):
        binding = kwargs['terminal_release']
        issue = live_module._RELEASE_BINDINGS[binding]
        task_id = current.task.task_id
        original_batch = row['batch_id']

        def durable_rows():
            task = cursor.execute('select * from import_tasks where id=%s',
                                  (task_id,)).fetchone()
            audit = cursor.execute('''select * from import_task_attempts
                where task_id=%s and lease_version=%s''',
                (task_id,current.lease_version)).fetchone()
            batches = cursor.execute('''select * from import_batches
                where id in (%s,%s) order by id''',
                (original_batch,other_batch)).fetchall()
            return task,audit,batches

        before = durable_rows()
        altered = dict(row)
        if changed == 'started_at':
            altered['started_at'] = cursor.execute('''select
                (clock_timestamp()+interval '1 day') as changed''').fetchone()['changed'].isoformat()
        else:
            altered['batch_id'] = other_batch
        with pytest.raises(ImportMemoryPublicationError,
                           match='Original terminal task row required'):
            original_finish(self,cursor,current,altered,status,reason,**kwargs)
        assert durable_rows() == before
        assert not issue.finish_done
        tested.append(True)
        return original_finish(self,cursor,current,row,status,reason,**kwargs)

    monkeypatch.setattr(PostgresImportLeaseRepository,'_finish',checked_finish)
    result = service._execute_live_publication(live)
    assert result.pair.import_task.status == 'succeeded'
    assert tested == [True]


def test_live_issuer_refuses_coordinator_replacement_before_admission(
        memory_publication, monkeypatch):
    service, db, _, user, attempt, live = _issue(memory_publication)
    original_admission = live_module._create_terminal_admission
    original_coordinator = service.pair.imports.coordinator
    reached = []

    def replaced_before_admission(work, cursor, evidence):
        service.pair.imports.coordinator = PostgresUserMutationCoordinator(db)
        try:
            with pytest.raises(ImportMemoryPublicationError,
                               match='Live publication issuance differs'):
                original_admission(work,cursor,evidence)
            reached.append(True)
        finally:
            service.pair.imports.coordinator = original_coordinator
        raise RuntimeError('admission intentionally stopped after rejection')

    monkeypatch.setattr(live_module,'_create_terminal_admission',
                        replaced_before_admission)
    with pytest.raises(DurablePublicationUnknown) as unknown:
        service._execute_live_publication(live)
    assert unknown.value.phase == 'terminal_commit' and reached == [True]
    frozen = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user,attempt.task.task_id,attempt.lease_version)
    assert frozen.phase == 'pair_sealed' and frozen.terminal_slot is None


@pytest.mark.parametrize('failure_point',
    ['after_terminal','task_success','audit_success','batch_touch',
     'user_release','task_expiry'])
def test_fixed_terminal_failure_rolls_back_evidence_domain_and_lease(
        memory_publication, monkeypatch, failure_point):
    service, db, _, user, attempt, live = _issue(memory_publication)
    terminal_entries = []
    original_terminal = service._run_fixed_terminal
    def count_terminal(cursor, admission, terminal):
        terminal_entries.append(terminal)
        return original_terminal(cursor, admission, terminal)
    monkeypatch.setattr(service, '_run_fixed_terminal', count_terminal)
    if failure_point == 'after_terminal':
        def refuse_finish(*args, **kwargs):
            raise RuntimeError('injected post-terminal failure')
        monkeypatch.setattr(PostgresImportLeaseRepository,'_finish',refuse_finish)
    else:
        table, predicate = {
            'task_success': ('import_tasks',
                "new.status='succeeded' and old.status<>'succeeded'"),
            'audit_success': ('import_task_attempts',
                "new.end_reason='succeeded' and old.end_reason is distinct from 'succeeded'"),
            'batch_touch': ('import_batches', 'true'),
            'user_release': ('user_mutation_leases',
                'new.lease_expires_at<old.lease_expires_at'),
            'task_expiry': ('import_tasks',
                "new.status='succeeded' and new.lease_expires_at<old.lease_expires_at"),
        }[failure_point]
        body = ('''if exists(select 1 from import_tasks where batch_id=new.id
            and status='succeeded') then
            raise exception 'injected fixed terminal failure'; end if;
            return new;''' if failure_point == 'batch_touch' else
            "raise exception 'injected fixed terminal failure';")
        with db.transaction() as cursor:
            cursor.execute(f'''create function refuse_fixed_terminal_15()
                returns trigger language plpgsql as $$ begin
                {body} end $$''')
            cursor.execute(f'''create trigger refuse_fixed_terminal_15
                after update on {table} for each row when ({predicate})
                execute function refuse_fixed_terminal_15()''')
    with pytest.raises(DurablePublicationUnknown):
        service._execute_live_publication(live)
    key = (user,attempt.task.task_id,attempt.lease_version)
    frozen = PostgresImportPublicationEvidenceRepository(db).read_exact(*key)
    assert frozen is not None and frozen.phase=='pair_sealed'
    assert frozen.terminal_slot is None
    with db.transaction() as cursor:
        task = cursor.execute('select * from import_tasks where id=%s',(key[1],)).fetchone()
        audit = cursor.execute('''select * from import_task_attempts
            where task_id=%s and lease_version=%s''',key[1:]).fetchone()
        gate = cursor.execute('''select status from user_publication_gates
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
        lease = cursor.execute('''select lease_expires_at from user_mutation_leases
            where user_id=%s''',(user,)).fetchone()
        states = cursor.execute('''select state from vector_generations
            where task_id=%s order by vector_kind''',(key[1],)).fetchall()
    assert task['status']=='running' and task['stage']=='committing'
    assert audit['ended_at'] is None and audit['end_reason'] is None
    assert gate['status']=='unresolved'
    assert task['lease_expires_at']>db.server_now()
    assert lease['lease_expires_at']>db.server_now()
    assert [row['state'] for row in states]==['sealed','sealed']
    assert len(terminal_entries) == 1
    work = service._terminal_work_for_live[live]
    terminal = service._terminal_issues[work]
    with pytest.raises(ImportMemoryPublicationError, match='already attempted'):
        service.pair.imports.complete(attempt, terminal_work=work)
    with db.transaction() as cursor:
        with pytest.raises(ImportMemoryPublicationError, match='already attempted'):
            live_module._create_terminal_admission(work, cursor, frozen)
    with pytest.raises(ImportMemoryPublicationError, match='already issued'):
        service._issue_fixed_terminal(live, terminal.prepared,
            terminal.rag_sealed, terminal.episode_sealed, terminal.expected)
    assert len(terminal_entries) == 1


@pytest.mark.parametrize('dependency',['source','audit'])
def test_intent_serializes_with_direct_dependency_delete(
        memory_publication, dependency):
    service, db, store, _, _, rag_scope, _, profile, user, _ = memory_publication
    attempt = _task(db,store,user)
    table, where, args = (
        ('import_objects','task_id=%s and user_id=%s',
         (attempt.task.task_id,user)) if dependency=='source' else
        ('import_task_attempts','task_id=%s and lease_version=%s',
         (attempt.task.task_id,attempt.lease_version)))
    with db.transaction() as cursor:
        cursor.execute('''create function pause_intent_dependency_15()
            returns trigger language plpgsql as $$ begin
            perform pg_advisory_xact_lock(90210, 15);
            return new; end $$''')
        cursor.execute('''create trigger pause_intent_dependency_15
            after insert on import_publication_evidence for each row
            execute function pause_intent_dependency_15()''')
    def delete_dependency():
        with db.transaction() as cursor:
            cursor.execute(f'delete from {table} where {where}',args)
    with ThreadPoolExecutor(max_workers=2) as workers:
        with db.transaction() as cursor:
            cursor.execute('select pg_advisory_xact_lock(90210,15)')
            issuing = workers.submit(service._issue_live_publication,
                rag_scope,attempt,
                [_point(rag_scope,attempt.task.document_id,'racing')],
                event_vector=[1.,0.,0.,0.],event_profile=profile)
            deadline = time.monotonic()+15
            while time.monotonic()<deadline:
                waiting = cursor.execute('''select pid from pg_locks
                    where locktype='advisory' and mode='ExclusiveLock'
                    and classid=90210 and objid=15 and not granted
                    limit 1''').fetchone()
                if waiting:
                    assert waiting['pid']!=cursor.connection.info.backend_pid
                    break
                assert not issuing.done(), issuing.exception() if issuing.done() else None
                time.sleep(.05)
            else:
                pytest.fail('Intent did not reach SQL advisory barrier')
            deleting = workers.submit(delete_dependency)
            time.sleep(.2)
            assert not deleting.done(), deleting.exception() if deleting.done() else None
        live = issuing.result(timeout=15)
        with pytest.raises(psycopg.errors.RaiseException):
            deleting.result(timeout=15)
    assert live is not None
    with db.transaction() as cursor:
        assert cursor.execute(f'select 1 from {table} where {where}',args).fetchone()
        assert cursor.execute('''select 1 from user_publication_gates
            where user_id=%s and task_id=%s and task_lease_version=%s
            and status='unresolved' ''',
            (user,attempt.task.task_id,attempt.lease_version)).fetchone()


def _retained_publication_dependencies(cursor, key, batch_id):
    retained = {
        'import_objects': ('task_id=%s', (key[1],), 'task_id'),
        'import_task_attempts': ('task_id=%s', (key[1],),
                                 'task_id,lease_version'),
        'import_tasks': ('id=%s', (key[1],), 'id'),
        'import_batches': ('id=%s', (batch_id,), 'id'),
        'users': ('id=%s', (key[0],), 'id'),
        'import_publication_evidence': ('task_id=%s', (key[1],),
                                        'user_id,task_id,task_lease_version'),
        'import_publication_private_payloads': ('task_id=%s', (key[1],),
                                                 'user_id,task_id,task_lease_version'),
        'user_publication_gates': ('task_id=%s', (key[1],),
                                   'user_id,task_id,task_lease_version'),
        'import_publication_recovery_queue': ('task_id=%s', (key[1],),
                                              'user_id,task_id,task_lease_version'),
        'generation_reservations': ('task_id=%s', (key[1],), 'generation_id'),
    }
    return {table: cursor.execute(
        f'select * from {table} where {where} order by {order}', values).fetchall()
        for table, (where, values, order) in retained.items()}


@pytest.mark.parametrize('mutation', ['delete', 'update'])
def test_old_repeatable_read_snapshot_cannot_mutate_newly_gated_source(
        memory_publication, mutation):
    service, db, store, _, _, rag_scope, _, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    key = (user, attempt.task.task_id, attempt.lease_version)

    # Connection A establishes its old MVCC snapshot without taking the user
    # lock. Connection B then commits the intent and gate before A's raw SQL.
    with ThreadPoolExecutor(max_workers=1) as workers:
        with db.transaction() as old:
            old.execute('set transaction isolation level repeatable read')
            old_pid = old.connection.info.backend_pid
            assert old.execute('select 1 from import_objects where task_id=%s',
                               (key[1],)).fetchone()
            def reserve_new_gate():
                with db.transaction() as current:
                    new_pid = current.connection.info.backend_pid
                live = service._issue_live_publication(rag_scope, attempt,
                    [_point(rag_scope, attempt.task.document_id, 'old-snapshot')],
                    event_vector=[1., 0., 0., 0.], event_profile=profile)
                return new_pid, live
            new_pid, live = workers.submit(reserve_new_gate).result(timeout=30)
            assert new_pid != old_pid and live is not None
            with db.transaction() as current:
                before = _retained_publication_dependencies(
                    current, key, attempt.task.batch_id)
            assert all(before.values())
            if mutation == 'delete':
                sql = 'delete from import_objects where task_id=%s and user_id=%s'
            else:
                sql = '''update import_objects set size_bytes=size_bytes+1
                    where task_id=%s and user_id=%s'''
            with pytest.raises(psycopg.errors.RaiseException,
                               match='READ COMMITTED') as blocked:
                old.execute(sql, (key[1], user))
            assert blocked.value.sqlstate == 'P0001'
    with db.transaction() as cursor:
        assert _retained_publication_dependencies(
            cursor, key, attempt.task.batch_id) == before


@pytest.mark.parametrize('dependency', ['audit', 'audit_update', 'task', 'batch', 'user'])
def test_old_repeatable_read_snapshot_cannot_delete_newly_gated_parent(
        memory_publication, dependency):
    service, db, store, _, _, rag_scope, _, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    key = (user, attempt.task.task_id, attempt.lease_version)
    delete = {
        'audit': ('delete from import_task_attempts where task_id=%s '
                  'and lease_version=%s', key[1:]),
        'audit_update': ('''update import_task_attempts set
            ended_at=clock_timestamp(),end_reason='failed' where task_id=%s
            and lease_version=%s''', key[1:]),
        'task': ('delete from import_tasks where id=%s and user_id=%s',
                 (key[1], user)),
        'batch': ('delete from import_batches where id=%s and user_id=%s',
                  (attempt.task.batch_id, user)),
        'user': ('delete from users where id=%s', (user,)),
    }[dependency]
    with ThreadPoolExecutor(max_workers=1) as workers:
        with db.transaction() as old:
            old.execute('set transaction isolation level repeatable read')
            old_pid = old.connection.info.backend_pid
            assert old.execute('select 1 from import_task_attempts '
                               'where task_id=%s and lease_version=%s',
                               key[1:]).fetchone()
            def issue_current():
                with db.transaction() as current:
                    new_pid = current.connection.info.backend_pid
                live = service._issue_live_publication(rag_scope, attempt,
                    [_point(rag_scope, attempt.task.document_id,
                            'old-parent-snapshot')], event_vector=[1., 0., 0., 0.],
                    event_profile=profile)
                return new_pid, live
            new_pid, live = workers.submit(issue_current).result(timeout=30)
            assert new_pid != old_pid and live is not None
            with db.transaction() as current:
                before = _retained_publication_dependencies(
                    current, key, attempt.task.batch_id)
            assert all(before.values())
            with pytest.raises((psycopg.errors.RaiseException,
                                psycopg.errors.ForeignKeyViolation,
                                psycopg.errors.SerializationFailure)) as blocked:
                old.execute(*delete)
            if dependency in ('audit', 'audit_update'):
                assert blocked.value.sqlstate == 'P0001'
                assert 'READ COMMITTED' in str(blocked.value)
            elif dependency in ('task', 'batch') and blocked.value.sqlstate == 'P0001':
                assert 'READ COMMITTED' in str(blocked.value)
            else:
                assert blocked.value.sqlstate in ('23503', '40001')
    with db.transaction() as cursor:
        assert _retained_publication_dependencies(
            cursor, key, attempt.task.batch_id) == before


@pytest.mark.parametrize('dependency',['source','audit'])
def test_raw_dependency_delete_wins_before_intent_and_issuance_fails(
        memory_publication, dependency):
    service, db, store, _, _, rag_scope, _, profile, user, _ = memory_publication
    attempt = _task(db,store,user)
    key = (user,attempt.task.task_id,attempt.lease_version)
    table, where, args = (
        ('import_objects','task_id=%s and user_id=%s',(key[1],user))
        if dependency == 'source' else
        ('import_task_attempts','task_id=%s and lease_version=%s',key[1:]))
    with ThreadPoolExecutor(max_workers=1) as workers:
        with db.transaction() as cursor:
            cursor.execute(f'delete from {table} where {where}',args)
            issuing = workers.submit(service._issue_live_publication,
                rag_scope,attempt,
                [_point(rag_scope,attempt.task.document_id,'racing-delete')],
                event_vector=[1.,0.,0.,0.],event_profile=profile)
            time.sleep(.2)
            assert not issuing.done(), issuing.exception() if issuing.done() else None
        with pytest.raises((PublicationEvidenceError, ImportMemoryPublicationError,
                            ImportLeaseLost)):
            issuing.result(timeout=15)
    with db.transaction() as cursor:
        assert cursor.execute('''select 1 from import_publication_evidence
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone() is None
        assert cursor.execute('''select 1 from user_publication_gates
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone() is None


@pytest.mark.parametrize('kind', ['queue', 'recovery'])
def test_unmatched_permanent_token_issuance_rolls_back(memory_publication, kind):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key = (user,attempt.task.task_id,attempt.lease_version)
    with pytest.raises(psycopg.errors.RaiseException, match='issuance lacks exact'):
        with db.transaction() as cursor:
            cursor.execute('''insert into import_publication_recovery_token_issuance
                (token,kind,user_id,task_id,task_lease_version,issued_version)
                values(%s,%s,%s,%s,%s,%s)''',
                (uuid4(),kind,*key,2 if kind == 'queue' else 1))
    with db.transaction() as cursor:
        assert cursor.execute('''select count(*) as n from
            import_publication_recovery_token_issuance where user_id=%s
            and task_id=%s and task_lease_version=%s''',key).fetchone()['n'] == 0


@pytest.mark.parametrize('case', ['queue_version','recovery_version',
                                   'cross_kind_token','missing_queue_key'])
def test_issuance_requires_exact_current_grant_and_never_reuses_token(
        memory_publication, case):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key = (user,attempt.task.task_id,attempt.lease_version)
    with db.transaction() as cursor:
        cursor.execute('''update import_publication_recovery_queue set
            due_at=clock_timestamp(),reason_code='unknown',queue_version=queue_version+1
            where user_id=%s and task_id=%s and task_lease_version=%s''',key)
        cursor.execute("update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second' where id=%s",(key[1],))
        cursor.execute("update user_mutation_leases set lease_expires_at=clock_timestamp()-interval '1 second' where user_id=%s",(user,))
    repo = PostgresImportPublicationRecoveryRepository(db)
    claim = repo.claim_next('recover')
    assert claim is not None
    with db.transaction() as cursor:
        initial = cursor.execute('''select count(*) as n from
            import_publication_recovery_token_issuance where user_id=%s
            and task_id=%s and task_lease_version=%s''',key).fetchone()['n']
    assert initial == 2
    renewed = repo.renew(claim)
    assert renewed.recovery_token == claim.recovery_token
    with db.transaction() as cursor:
        assert cursor.execute('''select count(*) as n from
            import_publication_recovery_token_issuance where user_id=%s
            and task_id=%s and task_lease_version=%s''',key).fetchone()['n'] == initial
    values = {
        'queue_version': (uuid4(),'queue',*key,claim.queue_version+1),
        'recovery_version': (uuid4(),'recovery',*key,claim.recovery_version+1),
        'cross_kind_token': (claim.recovery_token,'queue',*key,claim.queue_version+1),
        'missing_queue_key': (uuid4(),'queue',user,str(uuid4()),key[2],2),
    }[case]
    expected = (psycopg.errors.UniqueViolation if case == 'cross_kind_token'
                else psycopg.errors.ForeignKeyViolation if case == 'missing_queue_key'
                else psycopg.errors.RaiseException)
    with pytest.raises(expected):
        with db.transaction() as cursor:
            cursor.execute('''insert into import_publication_recovery_token_issuance
                (token,kind,user_id,task_id,task_lease_version,issued_version)
                values(%s,%s,%s,%s,%s,%s)''',values)
    with db.transaction() as cursor:
        assert cursor.execute('''select count(*) as n from
            import_publication_recovery_token_issuance where user_id=%s
            and task_id=%s and task_lease_version=%s''',key).fetchone()['n'] == initial


def test_queue_only_issuance_commit_does_not_wait_on_header_lock(
        memory_publication):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key = (user,attempt.task.task_id,attempt.lease_version)
    header_locked = Event()
    queue_locked = Event()
    def header_then_queue():
        with db.transaction() as cursor:
            cursor.execute('''select 1 from import_publication_evidence
                where user_id=%s and task_id=%s and task_lease_version=%s
                for update''',key)
            header_locked.set()
            assert queue_locked.wait(10)
            cursor.execute('''select 1 from import_publication_recovery_queue
                where user_id=%s and task_id=%s and task_lease_version=%s
                for update''',key)
    with ThreadPoolExecutor(max_workers=1) as workers:
        waiting = workers.submit(header_then_queue)
        assert header_locked.wait(10)
        with db.transaction() as cursor:
            token = uuid4()
            captured = cursor.execute('''update import_publication_recovery_queue
                set state='claimed',due_at=null,queue_version=queue_version+1,
                claim_token=%s,claim_expires_at=clock_timestamp()+interval '30 seconds',
                last_claimed_at=clock_timestamp()
                where user_id=%s and task_id=%s and task_lease_version=%s
                returning queue_version''',(token,*key)).fetchone()
            assert captured is not None
            queue_locked.set()
            time.sleep(.1)
            assert not waiting.done()
            cursor.execute('''insert into import_publication_recovery_token_issuance
                (token,kind,user_id,task_id,task_lease_version,issued_version)
                values(%s,'queue',%s,%s,%s,%s)''',
                (token,*key,captured['queue_version']))
        waiting.result(timeout=10)


@pytest.mark.parametrize('fault', ['child_only','header_only','missing_child',
                                   'append_during_abandon'])
def test_deferred_private_header_pair_rejects_incomplete_history(
        memory_publication, fault):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key = (user,attempt.task.task_id,attempt.lease_version)
    with pytest.raises(psycopg.errors.RaiseException):
        with db.transaction() as cursor:
            if fault == 'child_only':
                cursor.execute('''update import_publication_private_payloads
                    set payload_phase_version=payload_phase_version+1
                    where user_id=%s and task_id=%s and task_lease_version=%s''',key)
            elif fault == 'header_only':
                cursor.execute('''update import_publication_evidence
                    set phase_version=phase_version+1,observation_reason='unknown'
                    where user_id=%s and task_id=%s and task_lease_version=%s''',key)
            elif fault == 'missing_child':
                cursor.execute('alter table import_publication_private_payloads disable trigger import_publication_private_guard_15')
                cursor.execute('''delete from import_publication_private_payloads
                    where user_id=%s and task_id=%s and task_lease_version=%s''',key)
                cursor.execute('''update import_publication_evidence
                    set phase_version=phase_version+1,observation_reason='unknown'
                    where user_id=%s and task_id=%s and task_lease_version=%s''',key)
            else:
                cursor.execute('''update import_publication_private_payloads
                    set payload_phase_version=2,document_slot=%s
                    where user_id=%s and task_id=%s and task_lease_version=%s''',
                    (b'{}',*key))
                cursor.execute('''update import_publication_evidence
                    set phase='abandoned',phase_version=2
                    where user_id=%s and task_id=%s and task_lease_version=%s''',key)
            cursor.execute('set constraints all immediate')
    with db.transaction() as cursor:
        row = cursor.execute('''select h.phase,h.phase_version,p.payload_phase_version,
            p.document_slot from import_publication_evidence h join
            import_publication_private_payloads p using(user_id,task_id,task_lease_version)
            where h.user_id=%s and h.task_id=%s and h.task_lease_version=%s''',key).fetchone()
    assert (row['phase'],row['phase_version'],row['payload_phase_version'],
            row['document_slot']) == ('intent',1,1,None)


def test_abandon_history_named_constraint_rejects_unpaired_child_early(
        memory_publication):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key = (user,attempt.task.task_id,attempt.lease_version)
    with pytest.raises(psycopg.errors.RaiseException,
                       match='Child event lacks paired header version'):
        with db.transaction() as cursor:
            cursor.execute('''update import_publication_private_payloads
                set payload_phase_version=payload_phase_version+1
                where user_id=%s and task_id=%s and task_lease_version=%s''',key)
            header = cursor.execute('''select phase_version from
                import_publication_evidence where user_id=%s and task_id=%s
                and task_lease_version=%s''',key).fetchone()
            assert header['phase_version'] == 1
            cursor.execute('set constraints import_publication_abandon_history_15 immediate')
    with db.transaction() as cursor:
        child = cursor.execute('''select payload_phase_version from
            import_publication_private_payloads where user_id=%s and task_id=%s
            and task_lease_version=%s''',key).fetchone()
    assert child['payload_phase_version'] == 1


def test_earlier_document_append_allows_later_abandon_history_check(
        memory_publication):
    service, db, _, user, attempt, live = _issue(memory_publication)
    issue = service._require_live_issue(live)
    prepared = service.documents._write_planned_document(
        issue.plan.document,attempt,live=live)
    service._bind_verified_document(live,prepared.verified)
    repository = PostgresImportPublicationEvidenceRepository(db)
    key = (user,attempt.task.task_id,attempt.lease_version)
    class RollBackProbe(RuntimeError):
        pass
    with pytest.raises(RollBackProbe):
        with db.transaction() as cursor:
            frozen = repository.append_document_in_transaction(cursor,live,
                prepared.verified,expected_phase='intent',expected_version=1)
            assert frozen.phase == 'document_verified' and frozen.phase_version == 2
            cursor.execute('''update import_publication_private_payloads
                set payload_phase_version=payload_phase_version+1
                where user_id=%s and task_id=%s and task_lease_version=%s''',key)
            cursor.execute('''update import_publication_evidence
                set phase='abandoned',phase_version=phase_version+1
                where user_id=%s and task_id=%s and task_lease_version=%s''',key)
            cursor.execute('set constraints import_publication_abandon_history_15 immediate')
            final = cursor.execute('''select h.phase,h.phase_version,
                p.payload_phase_version,p.document_slot from
                import_publication_evidence h join
                import_publication_private_payloads p
                using(user_id,task_id,task_lease_version)
                where h.user_id=%s and h.task_id=%s and h.task_lease_version=%s''',
                key).fetchone()
            assert final['phase'] == 'abandoned'
            assert final['phase_version'] == final['payload_phase_version'] == 3
            assert final['document_slot'] == frozen.document_slot
            raise RollBackProbe()
    original = repository.read_exact(*key)
    assert original.phase == 'intent' and original.phase_version == 1
    assert original.document_slot is None


def test_raw_child_writer_does_not_deadlock_header_first_document_append(
        memory_publication):
    service, db, _, user, attempt, live = _issue(memory_publication)
    issue = service._require_live_issue(live)
    prepared = service.documents._write_planned_document(
        issue.plan.document,attempt,live=live)
    service._bind_verified_document(live,prepared.verified)
    repository = PostgresImportPublicationEvidenceRepository(db)
    key = (user,attempt.task.task_id,attempt.lease_version)
    def append_document():
        with db.transaction() as cursor:
            return repository.append_document_in_transaction(cursor,live,
                prepared.verified,expected_phase='intent',expected_version=1)
    with ThreadPoolExecutor(max_workers=1) as workers:
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction() as cursor:
                cursor.execute('''update import_publication_private_payloads
                    set payload_phase_version=payload_phase_version+1
                    where user_id=%s and task_id=%s and task_lease_version=%s''',key)
                writing = workers.submit(append_document)
                time.sleep(.2)
                assert not writing.done(), writing.exception() if writing.done() else None
                cursor.execute('set constraints all immediate')
        frozen = writing.result(timeout=15)
    assert frozen.phase == 'document_verified'
    assert repository.read_exact(*key) == frozen


def test_first_schedule_seed_waits_for_user_but_claimant_sees_no_partial_row(
        memory_publication):
    service, db, store, _, _, rag_scope, _, profile, user, _ = memory_publication
    attempt = _task(db,store,user)
    with ThreadPoolExecutor(max_workers=2) as workers:
        with db.transaction() as cursor:
            cursor.execute('select id from users where id=%s for update',(user,))
            issuing = workers.submit(service._issue_live_publication,
                rag_scope,attempt,
                [_point(rag_scope,attempt.task.document_id,'first-schedule')],
                event_vector=[1.,0.,0.,0.],event_profile=profile)
            assert PostgresImportPublicationRecoveryRepository(db).claim_next('scanner') is None
            assert cursor.execute('''select 1 from import_publication_recovery_schedule
                where user_id=%s''',(user,)).fetchone() is None
            assert not issuing.done(), issuing.exception() if issuing.done() else None
        live = issuing.result(timeout=15)
    assert live is not None
    with db.transaction() as cursor:
        assert cursor.execute('''select count(*) as n from import_publication_recovery_schedule
            where user_id=%s''',(user,)).fetchone()['n'] == 1


@pytest.mark.parametrize('failure_at', ['before_capture','after_capture','unknown_capture_commit'])
def test_claim_database_outage_keeps_only_actual_committed_capture(
        memory_publication, monkeypatch, failure_at):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key = (user,attempt.task.task_id,attempt.lease_version)
    with db.transaction() as cursor:
        cursor.execute('''update import_publication_recovery_queue
            set due_at=clock_timestamp(),reason_code='unknown',
            queue_version=queue_version+1
            where user_id=%s and task_id=%s and task_lease_version=%s''',key)
        cursor.execute("update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second' where id=%s",(key[1],))
        cursor.execute("update user_mutation_leases set lease_expires_at=clock_timestamp()-interval '1 second' where user_id=%s",(user,))
    original = db.transaction
    entered = 0
    @contextmanager
    def interrupted():
        nonlocal entered
        entered += 1
        if ((failure_at == 'before_capture' and entered == 1)
                or (failure_at == 'after_capture' and entered == 2)):
            raise psycopg.OperationalError('injected database outage')
        with original() as cursor:
            yield cursor
        if failure_at == 'unknown_capture_commit' and entered == 1:
            raise psycopg.OperationalError('injected lost commit response')
    monkeypatch.setattr(db, 'transaction', interrupted)
    repo = PostgresImportPublicationRecoveryRepository(db)
    with pytest.raises(RecoveryUnavailable) as raised:
        repo.claim_next('recover')
    assert isinstance(raised.value.__cause__, psycopg.OperationalError)
    monkeypatch.setattr(db, 'transaction', original)
    with db.transaction() as cursor:
        queue = cursor.execute('''select state,claim_token from
            import_publication_recovery_queue where user_id=%s and task_id=%s
            and task_lease_version=%s''',key).fetchone()
        grant = cursor.execute('''select token from import_publication_recovery_leases
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
    assert queue['state'] == ('pending' if failure_at == 'before_capture' else 'claimed')
    assert (queue['claim_token'] is None) == (failure_at == 'before_capture')
    assert grant is None


def test_dual_renewal_outage_after_first_write_rolls_back_both(
        memory_publication, monkeypatch):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key = (user,attempt.task.task_id,attempt.lease_version)
    with db.transaction() as cursor:
        cursor.execute('''update import_publication_recovery_queue set
            due_at=clock_timestamp(),reason_code='unknown',queue_version=queue_version+1
            where user_id=%s and task_id=%s and task_lease_version=%s''',key)
        cursor.execute("update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second' where id=%s",(key[1],))
        cursor.execute("update user_mutation_leases set lease_expires_at=clock_timestamp()-interval '1 second' where user_id=%s",(user,))
    repo = PostgresImportPublicationRecoveryRepository(db)
    claim = repo.claim_next('recover')
    assert claim is not None
    original = db.transaction
    class FailedAfterLeaseWrite:
        def __init__(self, cursor):
            self.cursor = cursor
            self.connection = cursor.connection
        def execute(self, statement, parameters=None):
            result = self.cursor.execute(statement,parameters)
            if 'update import_publication_recovery_leases' in statement:
                raise psycopg.OperationalError('injected outage after lease renewal')
            return result
    @contextmanager
    def interrupted():
        with original() as cursor:
            yield FailedAfterLeaseWrite(cursor)
    monkeypatch.setattr(db,'transaction',interrupted)
    with pytest.raises(RecoveryUnavailable) as raised:
        repo.renew(claim)
    assert isinstance(raised.value.__cause__,psycopg.OperationalError)
    monkeypatch.setattr(db,'transaction',original)
    with db.transaction() as cursor:
        queue = cursor.execute('''select claim_expires_at from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
        lease = cursor.execute('''select expires_at from import_publication_recovery_leases
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
    assert queue['claim_expires_at'] == claim.queue_expires_at
    assert lease['expires_at'] == claim.recovery_expires_at


@pytest.mark.parametrize('survivor', ['queue','recovery'])
def test_renew_rejects_either_one_sided_expiry(memory_publication,survivor):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key = (user,attempt.task.task_id,attempt.lease_version)
    with db.transaction() as cursor:
        cursor.execute('''update import_publication_recovery_queue set
            due_at=clock_timestamp(),reason_code='unknown',queue_version=queue_version+1
            where user_id=%s and task_id=%s and task_lease_version=%s''',key)
        cursor.execute("update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second' where id=%s",(key[1],))
        cursor.execute("update user_mutation_leases set lease_expires_at=clock_timestamp()-interval '1 second' where user_id=%s",(user,))
    repo = PostgresImportPublicationRecoveryRepository(db)
    claim = repo.claim_next('recover',lease_seconds=1)
    assert claim is not None
    with db.transaction() as cursor:
        if survivor == 'queue':
            cursor.execute('''update import_publication_recovery_queue
                set claim_expires_at=clock_timestamp()+interval '30 seconds'
                where user_id=%s and task_id=%s and task_lease_version=%s''',key)
        else:
            cursor.execute('''update import_publication_recovery_leases
                set heartbeat_at=clock_timestamp(),
                    expires_at=clock_timestamp()+interval '30 seconds'
                where user_id=%s and task_id=%s and task_lease_version=%s''',key)
    time.sleep(1.2)
    with pytest.raises(RecoveryLeaseLost):
        repo.renew(claim)


@pytest.mark.parametrize('grant_fault',
                         ['missing','closed','expired','wrong_capture'])
def test_raw_queue_pure_renew_requires_matching_live_recovery_grant(
        memory_publication,grant_fault):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key = (user,attempt.task.task_id,attempt.lease_version)
    with db.transaction() as cursor:
        cursor.execute('''update import_publication_recovery_queue set
            due_at=clock_timestamp(),reason_code='unknown',queue_version=queue_version+1
            where user_id=%s and task_id=%s and task_lease_version=%s''',key)
        cursor.execute("update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second' where id=%s",(key[1],))
        cursor.execute("update user_mutation_leases set lease_expires_at=clock_timestamp()-interval '1 second' where user_id=%s",(user,))
    if grant_fault == 'missing':
        with db.transaction() as cursor:
            pending = cursor.execute('''select * from
                import_publication_recovery_queue where user_id=%s and task_id=%s
                and task_lease_version=%s for update''',key).fetchone()
            token = uuid4()
            capture = cursor.execute('''update import_publication_recovery_queue
                set state='claimed',due_at=null,queue_version=queue_version+1,
                    claim_token=%s,claim_expires_at=clock_timestamp()+interval '30 seconds',
                    last_claimed_at=clock_timestamp()
                where user_id=%s and task_id=%s and task_lease_version=%s
                and queue_version=%s returning *''',
                (token,*key,pending['queue_version'])).fetchone()
            assert capture is not None
            cursor.execute('''insert into import_publication_recovery_token_issuance
                (token,kind,user_id,task_id,task_lease_version,issued_version)
                values(%s,'queue',%s,%s,%s,%s)''',
                (token,*key,capture['queue_version']))
    else:
        claim = PostgresImportPublicationRecoveryRepository(db).claim_next(
            'recover',lease_seconds=1 if grant_fault in ('expired','wrong_capture') else 30)
        assert claim is not None
        if grant_fault == 'closed':
            with db.transaction() as cursor:
                closed = cursor.execute('''update import_publication_recovery_leases
                    set expires_at=least(expires_at,clock_timestamp())
                    where user_id=%s and task_id=%s and task_lease_version=%s
                    and token=%s and version=%s and expires_at=%s returning *''',
                    (*key,claim.recovery_token,claim.recovery_version,
                     claim.recovery_expires_at)).fetchone()
                assert closed is not None
        elif grant_fault == 'expired':
            with db.transaction() as cursor:
                cursor.execute('''update import_publication_recovery_queue
                    set claim_expires_at=clock_timestamp()+interval '30 seconds'
                    where user_id=%s and task_id=%s and task_lease_version=%s''',key)
            time.sleep(1.2)
        else:
            with db.transaction() as cursor:
                extended = cursor.execute('''update import_publication_recovery_leases
                    set heartbeat_at=clock_timestamp(),
                        expires_at=expires_at+interval '30 seconds'
                    where user_id=%s and task_id=%s and task_lease_version=%s
                    and token=%s and version=%s returning *''',
                    (*key,claim.recovery_token,claim.recovery_version)).fetchone()
                assert extended is not None
            time.sleep(1.2)
            with db.transaction() as cursor:
                previous = cursor.execute('''select * from
                    import_publication_recovery_queue where user_id=%s and task_id=%s
                    and task_lease_version=%s for update''',key).fetchone()
                token = uuid4()
                replacement = cursor.execute('''update import_publication_recovery_queue
                    set state='claimed',queue_version=queue_version+1,
                        claim_token=%s,claim_expires_at=clock_timestamp()+interval '30 seconds',
                        last_claimed_at=clock_timestamp()
                    where user_id=%s and task_id=%s and task_lease_version=%s
                    and queue_version=%s returning *''',
                    (token,*key,previous['queue_version'])).fetchone()
                assert replacement is not None
                cursor.execute('''insert into import_publication_recovery_token_issuance
                    (token,kind,user_id,task_id,task_lease_version,issued_version)
                    values(%s,'queue',%s,%s,%s,%s)''',
                    (token,*key,replacement['queue_version']))

    with db.transaction() as cursor:
        baseline_queue = cursor.execute('''select * from
            import_publication_recovery_queue where user_id=%s and task_id=%s
            and task_lease_version=%s''',key).fetchone()
        baseline_lease = cursor.execute('''select * from
            import_publication_recovery_leases where user_id=%s and task_id=%s
            and task_lease_version=%s''',key).fetchone()
        baseline_issuance = cursor.execute('''select count(*) as n from
            import_publication_recovery_token_issuance
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()['n']
        assert baseline_queue['state'] == 'claimed'
        assert baseline_queue['claim_expires_at'] > cursor.execute(
            'select clock_timestamp() as now').fetchone()['now']
        if grant_fault == 'missing':
            assert baseline_lease is None
        elif grant_fault == 'wrong_capture':
            assert baseline_lease['expires_at'] > cursor.execute(
                'select clock_timestamp() as now').fetchone()['now']
            assert baseline_lease['queue_claim_token'] != baseline_queue['claim_token']
        else:
            assert baseline_lease['expires_at'] <= cursor.execute(
                'select clock_timestamp() as now').fetchone()['now']

    with pytest.raises(psycopg.errors.RaiseException,
                       match='Recovery queue pure renewal differs'):
        with db.transaction() as cursor:
            cursor.execute('''update import_publication_recovery_queue
                set claim_expires_at=claim_expires_at+interval '1 second'
                where user_id=%s and task_id=%s and task_lease_version=%s''',key)
    with db.transaction() as cursor:
        assert cursor.execute('''select * from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            key).fetchone() == baseline_queue
        assert cursor.execute('''select * from import_publication_recovery_leases
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            key).fetchone() == baseline_lease
        assert cursor.execute('''select count(*) as n from
            import_publication_recovery_token_issuance
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            key).fetchone()['n'] == baseline_issuance


@pytest.mark.parametrize('fault,message',[
    ('future_shorter','Recovery lease update is not renewal or closure'),
    ('live_noop','Recovery lease update is not renewal or closure'),
    ('heartbeat','Recovery lease closure changed heartbeat'),
    ('owner','Recovery lease renewal scope differs'),
    ('capture','Recovery lease renewal scope differs'),
    ('token','Recovery lease takeover requires expiry and fresh version'),
    ('version','Recovery lease takeover requires expiry and fresh version'),
])
def test_recovery_lease_closure_rejects_nonclosure_or_identity_change(
        memory_publication,fault,message):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key = (user,attempt.task.task_id,attempt.lease_version)
    with db.transaction() as cursor:
        cursor.execute('''update import_publication_recovery_queue set
            due_at=clock_timestamp(),reason_code='unknown',queue_version=queue_version+1
            where user_id=%s and task_id=%s and task_lease_version=%s''',key)
        cursor.execute("update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second' where id=%s",(key[1],))
        cursor.execute("update user_mutation_leases set lease_expires_at=clock_timestamp()-interval '1 second' where user_id=%s",(user,))
    claim = PostgresImportPublicationRecoveryRepository(db).claim_next('recover',
                                                                      lease_seconds=30)
    assert claim is not None
    with db.transaction() as cursor:
        before = cursor.execute('''select * from import_publication_recovery_leases
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
        issuance = cursor.execute('''select count(*) as n from
            import_publication_recovery_token_issuance where user_id=%s
            and task_id=%s and task_lease_version=%s''',key).fetchone()['n']
    updates = {
        'future_shorter': "expires_at=expires_at-interval '1 second'",
        'live_noop': 'expires_at=expires_at',
        'heartbeat': 'expires_at=clock_timestamp(),heartbeat_at=clock_timestamp()',
        'owner': "expires_at=clock_timestamp(),owner='foreign'",
        'capture': 'expires_at=clock_timestamp(),queue_claim_token=gen_random_uuid()',
        'token': 'expires_at=clock_timestamp(),token=gen_random_uuid()',
        'version': 'expires_at=clock_timestamp(),version=version+1',
    }
    with pytest.raises(psycopg.errors.RaiseException,match=message):
        with db.transaction() as cursor:
            cursor.execute(f'''update import_publication_recovery_leases
                set {updates[fault]} where user_id=%s and task_id=%s
                and task_lease_version=%s''',key)
    with db.transaction() as cursor:
        assert cursor.execute('''select * from import_publication_recovery_leases
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone() == before
        assert cursor.execute('''select count(*) as n from
            import_publication_recovery_token_issuance where user_id=%s
            and task_id=%s and task_lease_version=%s''',key).fetchone()['n'] == issuance


@pytest.mark.parametrize('expired_old',[False,True])
def test_recovery_lease_closure_normalizes_to_fresh_guard_clock(
        memory_publication,expired_old):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key = (user,attempt.task.task_id,attempt.lease_version)
    with db.transaction() as cursor:
        cursor.execute('''update import_publication_recovery_queue set
            due_at=clock_timestamp(),reason_code='unknown',queue_version=queue_version+1
            where user_id=%s and task_id=%s and task_lease_version=%s''',key)
        cursor.execute("update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second' where id=%s",(key[1],))
        cursor.execute("update user_mutation_leases set lease_expires_at=clock_timestamp()-interval '1 second' where user_id=%s",(user,))
    claim = PostgresImportPublicationRecoveryRepository(db).claim_next('recover',
        lease_seconds=1 if expired_old else 30)
    assert claim is not None
    if expired_old:
        time.sleep(1.2)
    with db.transaction() as cursor:
        before = cursor.execute('''select * from import_publication_recovery_leases
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
        issuance = cursor.execute('''select count(*) as n from
            import_publication_recovery_token_issuance where user_id=%s
            and task_id=%s and task_lease_version=%s''',key).fetchone()['n']
        sampled_before = cursor.execute('select clock_timestamp() as now').fetchone()['now']
        requested = sampled_before - timedelta(seconds=1)
        closed = cursor.execute('''update import_publication_recovery_leases
            set expires_at=%s where user_id=%s and task_id=%s
            and task_lease_version=%s and owner=%s and token=%s and version=%s
            and queue_claim_token=%s and queue_version=%s and expires_at=%s
            returning *''',(requested,*key,claim.worker_id,claim.recovery_token,
            claim.recovery_version,claim.queue_claim_token,claim.queue_version,
            claim.recovery_expires_at)).fetchone()
        sampled_after = cursor.execute('select clock_timestamp() as now').fetchone()['now']
        assert closed is not None
        if expired_old:
            assert closed['expires_at'] == before['expires_at']
        else:
            assert sampled_before <= closed['expires_at'] <= sampled_after
            assert requested != closed['expires_at'] < before['expires_at']
        for column in ('user_id','task_id','task_lease_version','owner','token',
                       'version','queue_claim_token','queue_version','heartbeat_at'):
            assert closed[column] == before[column]
        assert cursor.execute('''select count(*) as n from
            import_publication_recovery_token_issuance where user_id=%s
            and task_id=%s and task_lease_version=%s''',key).fetchone()['n'] == issuance


def test_stale_recovery_lease_closure_cas_cannot_close_replacement(
        memory_publication):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key = (user,attempt.task.task_id,attempt.lease_version)
    with db.transaction() as cursor:
        cursor.execute('''update import_publication_recovery_queue set
            due_at=clock_timestamp(),reason_code='unknown',queue_version=queue_version+1
            where user_id=%s and task_id=%s and task_lease_version=%s''',key)
        cursor.execute("update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second' where id=%s",(key[1],))
        cursor.execute("update user_mutation_leases set lease_expires_at=clock_timestamp()-interval '1 second' where user_id=%s",(user,))
    repo = PostgresImportPublicationRecoveryRepository(db)
    first = repo.claim_next('first',lease_seconds=1)
    assert first is not None
    time.sleep(1.2)
    second = repo.claim_next('second',lease_seconds=30)
    assert second is not None and second.recovery_token != first.recovery_token
    with db.transaction() as cursor:
        before = cursor.execute('''select * from import_publication_recovery_leases
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
        stale = cursor.execute('''update import_publication_recovery_leases
            set expires_at=least(expires_at,clock_timestamp())
            where user_id=%s and task_id=%s and task_lease_version=%s
            and owner=%s and token=%s and version=%s
            and queue_claim_token=%s and queue_version=%s and expires_at=%s
            returning *''',(*key,first.worker_id,first.recovery_token,
                first.recovery_version,first.queue_claim_token,first.queue_version,
                first.recovery_expires_at)).fetchone()
        after = cursor.execute('''select * from import_publication_recovery_leases
            where user_id=%s and task_id=%s and task_lease_version=%s''',key).fetchone()
    assert stale is None and after == before


def test_queue_capture_commits_before_user_wait_and_expired_capture_loses(
        memory_publication):
    _, db, _, user, attempt, _ = _issue(memory_publication)
    key = (user,attempt.task.task_id,attempt.lease_version)
    with db.transaction() as cursor:
        cursor.execute('''update import_publication_recovery_queue set
            due_at=clock_timestamp(),reason_code='unknown',queue_version=queue_version+1
            where user_id=%s and task_id=%s and task_lease_version=%s''',key)
        cursor.execute("update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second' where id=%s",(key[1],))
        cursor.execute("update user_mutation_leases set lease_expires_at=clock_timestamp()-interval '1 second' where user_id=%s",(user,))
    repo = PostgresImportPublicationRecoveryRepository(db)
    with ThreadPoolExecutor(max_workers=2) as workers:
        with db.transaction() as cursor:
            cursor.execute('select id from users where id=%s for update',(user,))
            waiting = workers.submit(repo.claim_next,'blocked',lease_seconds=1)
            deadline = time.monotonic()+15
            while time.monotonic()<deadline:
                captured = cursor.execute('''select state,claim_token,queue_version
                    from import_publication_recovery_queue where user_id=%s
                    and task_id=%s and task_lease_version=%s''',key).fetchone()
                if captured['state']=='claimed':
                    break
                assert not waiting.done(), waiting.exception() if waiting.done() else None
                time.sleep(.05)
            else:
                pytest.fail('Queue-only capture did not commit before user wait')
            time.sleep(1.2)
            replacement_waiting = workers.submit(repo.claim_next,'replacement',
                                                lease_seconds=30)
            deadline = time.monotonic()+15
            while time.monotonic()<deadline:
                replacement = cursor.execute('''select state,claim_token,
                    queue_version from import_publication_recovery_queue
                    where user_id=%s and task_id=%s and task_lease_version=%s''',
                    key).fetchone()
                if (replacement['state']=='claimed'
                        and replacement['claim_token'] != captured['claim_token']):
                    break
                assert not replacement_waiting.done(), (
                    replacement_waiting.exception() if replacement_waiting.done()
                    else None)
                time.sleep(.05)
            else:
                pytest.fail('Replacement capture did not commit during user wait')
            assert replacement['queue_version'] > captured['queue_version']
            assert not waiting.done() and not replacement_waiting.done()
        with pytest.raises(RecoveryLeaseLost, match='expired during authority wait'):
            waiting.result(timeout=15)
        winner = replacement_waiting.result(timeout=15)
    assert winner is not None
    assert winner.queue_claim_token == replacement['claim_token']
    assert winner.queue_version == replacement['queue_version']
    with db.transaction() as cursor:
        final = cursor.execute('''select claim_token,queue_version from
            import_publication_recovery_queue where user_id=%s and task_id=%s
            and task_lease_version=%s''',key).fetchone()
    assert final['claim_token'] == winner.queue_claim_token
    assert final['queue_version'] == winner.queue_version


def test_recovery_claim_rotates_between_due_users_after_first_capture_expires(
        memory_publication):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, other = memory_publication
    other_rag = VectorScope(other,'rag','pdf_' + other,rag_scope.identity)
    other_episode = VectorScope(other,'episode','episodes',episode_scope.identity)
    coordinator = PostgresUserMutationCoordinator(db)
    bootstrap = coordinator.acquire(other,'bootstrap-other',lease_seconds=120)
    assert bootstrap is not None
    def history(cursor):
        snapshots.compare_and_swap_in_transaction(cursor,other,'history',
            dict(EMPTY_HISTORY),expected_version=0)
    rag.publish_complete(other_rag,bootstrap,rag.authority.read_head(other_rag),[],
        domain_publish=history,snapshot_version=1)
    assert coordinator.release(bootstrap)
    publish_episode_baseline(db,service.pair.episode,other_episode,[])
    other_service = ImportMemoryPublicationService(db,store,rag,service.pair.episode,
        trusted_episode_scope=other_episode)
    for owner, publishing, scope in ((user,service,rag_scope),
                                     (other,other_service,other_rag)):
        attempt = _task(db,store,owner)
        live = publishing._issue_live_publication(scope,attempt,
            [_point(scope,attempt.task.document_id,'fair-' + owner)],
            event_vector=[1.,0.,0.,0.],event_profile=profile)
        assert live is not None
        key = (owner,attempt.task.task_id,attempt.lease_version)
        with db.transaction() as cursor:
            cursor.execute('''update import_publication_recovery_queue set
                due_at=clock_timestamp(),reason_code='unknown',
                queue_version=queue_version+1 where user_id=%s and task_id=%s
                and task_lease_version=%s''',key)
            cursor.execute("update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second' where id=%s",(key[1],))
            cursor.execute("update user_mutation_leases set lease_expires_at=clock_timestamp()-interval '1 second' where user_id=%s",(owner,))
    repo = PostgresImportPublicationRecoveryRepository(db)
    first = repo.claim_next('first',lease_seconds=1)
    assert first is not None
    time.sleep(1.2)
    second = repo.claim_next('second',lease_seconds=10)
    assert second is not None and second.user_id != first.user_id
    assert {first.user_id,second.user_id} == {user,other}
