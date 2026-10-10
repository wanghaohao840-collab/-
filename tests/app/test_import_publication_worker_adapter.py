"""The private Worker seam never replays an uncertain durable attempt."""

from copy import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.import_memory_publication import (
    DurablePublicationUnknown, ImportMemoryPublicationError,
    _LivePublication,
)
from app.import_publication_evidence import AttemptKey, PublicationEvidenceError
from app.import_publication_recovery import PostgresImportPublicationRecoveryRepository
from app.import_publication_worker_adapter import ImportPublicationWorkerAdapter
from app.import_publication_proof import ImportPublicationProofService
from tests.integration.test_import_document_publication import publication
from tests.integration.test_import_memory_publication import memory_publication
from tests.integration.test_import_publication_proof import _issue
from tests.integration.test_postgres_auth_sessions import shared_database
from tests.integration.test_s3_object_store import store


def _adapter(service, db, *, enabled=True):
    return ImportPublicationWorkerAdapter(service,
        PostgresImportPublicationRecoveryRepository(db), enabled=enabled)


def test_adapter_is_disabled_without_private_opt_in(memory_publication):
    service, db, _, user, attempt, live = _issue(memory_publication)
    adapter = _adapter(service, db, enabled=False)
    with pytest.raises(RuntimeError, match='disabled'):
        adapter.run_once(live)
    with db.transaction() as cursor:
        assert cursor.execute('''select phase from import_publication_evidence
            where task_id=%s''', (attempt.task.task_id,)).fetchone()['phase'] == 'intent'
        assert cursor.execute('''select status from import_tasks
            where id=%s''', (attempt.task.task_id,)).fetchone()['status'] == 'running'


def test_adapter_refuses_forged_and_copied_live_before_execution(
        memory_publication, monkeypatch):
    service, db, _, _, _, live = _issue(memory_publication)
    adapter = _adapter(service, db)
    called = []
    monkeypatch.setattr(service, '_execute_live_publication', called.append)
    for invalid in (_LivePublication(), copy(live), object()):
        with pytest.raises((ImportMemoryPublicationError, TypeError)):
            adapter.run_once(invalid)
    assert called == []


@pytest.mark.parametrize('phase', ['document_put', 'bad_phase', []])
def test_adapter_unknown_schedules_exact_queue_without_replay(
        memory_publication, monkeypatch, phase):
    service, db, _, user, attempt, live = _issue(memory_publication)
    key = AttemptKey(user, attempt.task.task_id, attempt.lease_version)
    calls = []

    def uncertain(original):
        calls.append(original)
        raise DurablePublicationUnknown(key, phase)

    monkeypatch.setattr(service, '_execute_live_publication', uncertain)
    outcome = _adapter(service, db).run_once(live)
    assert calls == [live]
    assert (outcome.attempt_key, outcome.status, outcome.task_id) == (
        key, 'needs_reconciliation', key.task_id)
    assert outcome.reconciliation_reason == (
        'durable_unknown' if phase == 'document_put' else 'recovery_unavailable')
    assert set(vars(outcome)) == {
        'attempt_key', 'status', 'task_id', 'reconciliation_reason'}
    with db.transaction() as cursor:
        queue = cursor.execute('''select state,queue_version,reason_code,due_at
            from import_publication_recovery_queue where user_id=%s
            and task_id=%s and task_lease_version=%s''',
            (key.user_id, key.task_id, key.task_lease_version)).fetchone()
        task = cursor.execute('select status,lease_expires_at from import_tasks '
                              'where id=%s', (key.task_id,)).fetchone()
    assert queue['state'] == 'pending'
    assert queue['queue_version'] == (2 if phase == 'document_put' else 1)
    assert queue['reason_code'] == ('unknown' if phase == 'document_put' else 'seed')
    assert queue['due_at'] >= task['lease_expires_at']
    assert task['status'] == 'running'


def test_adapter_normal_return_requires_fresh_exact_proof(memory_publication):
    service, db, _, user, attempt, live = _issue(memory_publication)
    adapter = _adapter(service, db)
    outcome = adapter.run_once(live)
    key = AttemptKey(user, attempt.task.task_id, attempt.lease_version)
    assert outcome == type(outcome)(key, 'committed_success', key.task_id, 'none')
    assert set(vars(outcome)) == {
        'attempt_key', 'status', 'task_id', 'reconciliation_reason'}
    repeated = adapter.run_once(live)
    assert repeated == type(outcome)(key, 'needs_reconciliation', key.task_id,
                                      'recovery_unavailable')
    with pytest.raises(ImportMemoryPublicationError, match='already attempted'):
        service._execute_live_publication(live)


@pytest.mark.parametrize('phase', ['bad_phase', [], None])
def test_adapter_rejects_malformed_unknown_without_queue_change(
        memory_publication, monkeypatch, phase):
    service, db, _, user, attempt, live = _issue(memory_publication)
    key = AttemptKey(user, attempt.task.task_id, attempt.lease_version)
    wrong = AttemptKey(user, attempt.task.task_id, attempt.lease_version + 1)
    with db.transaction() as cursor:
        before = cursor.execute('''select * from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            (key.user_id, key.task_id, key.task_lease_version)).fetchone()
    calls = []
    def malformed(original):
        calls.append(original)
        raise DurablePublicationUnknown(wrong if phase is None else key,
                                        'document_put' if phase is None else phase)
    monkeypatch.setattr(service, '_execute_live_publication', malformed)
    adapter = _adapter(service, db)
    first = adapter.run_once(live)
    second = adapter.run_once(live)
    assert calls == [live]
    assert first.status == second.status == 'needs_reconciliation'
    assert first.reconciliation_reason == second.reconciliation_reason == 'recovery_unavailable'
    with db.transaction() as cursor:
        assert cursor.execute('''select * from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            (key.user_id, key.task_id, key.task_lease_version)).fetchone() == before


def test_adapter_concurrent_calls_use_original_live_only_once(
        memory_publication, monkeypatch):
    service, db, _, user, attempt, live = _issue(memory_publication)
    key = AttemptKey(user, attempt.task.task_id, attempt.lease_version)
    entered, release = Event(), Event()
    calls = []
    def uncertain(original):
        calls.append(original)
        entered.set()
        assert release.wait(10)
        raise DurablePublicationUnknown(key, 'document_put')
    monkeypatch.setattr(service, '_execute_live_publication', uncertain)
    adapter = _adapter(service, db)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(adapter.run_once, live)
        assert entered.wait(10)
        second = workers.submit(adapter.run_once, live)
        assert second.result(timeout=10).reconciliation_reason == 'recovery_unavailable'
        release.set()
        assert first.result(timeout=20).status == 'needs_reconciliation'
    assert calls == [live]


def test_adapter_success_requires_separate_fresh_proof_read(
        memory_publication, monkeypatch):
    service, db, _, user, attempt, live = _issue(memory_publication)
    original = ImportPublicationProofService.prove_exact
    reads = []
    def fail_second_read(proof_service, *args):
        reads.append(args)
        if len(reads) == 2:
            raise RuntimeError('fresh proof unavailable')
        return original(proof_service, *args)
    monkeypatch.setattr(ImportPublicationProofService, 'prove_exact', fail_second_read)
    result = _adapter(service, db).run_once(live)
    assert reads == [(user, attempt.task.task_id, attempt.lease_version)] * 2
    assert result.status == 'needs_reconciliation'
    assert result.reconciliation_reason == 'recovery_unavailable'


def test_adapter_unknown_schedule_failure_stays_held_without_proof_or_replay(
        memory_publication, monkeypatch):
    service, db, _, user, attempt, live = _issue(memory_publication)
    key = AttemptKey(user, attempt.task.task_id, attempt.lease_version)
    adapter = _adapter(service, db)
    calls = []
    proof_calls = []
    with db.transaction() as cursor:
        before = cursor.execute('''select * from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            (key.user_id, key.task_id, key.task_lease_version)).fetchone()

    def uncertain(original):
        calls.append(original)
        raise DurablePublicationUnknown(key, 'document_put')

    def scheduling_unavailable(_):
        raise RuntimeError('queue unavailable')

    def unexpected_proof(*args):
        proof_calls.append(args)
        raise AssertionError('proof must follow successful scheduling')

    monkeypatch.setattr(service, '_execute_live_publication', uncertain)
    monkeypatch.setattr(adapter.recovery, 'schedule_unknown', scheduling_unavailable)
    monkeypatch.setattr(ImportPublicationProofService, 'prove_exact', unexpected_proof)
    result = adapter.run_once(live)
    assert calls == [live] and proof_calls == []
    assert result.status == 'needs_reconciliation'
    assert result.reconciliation_reason == 'recovery_unavailable'
    with db.transaction() as cursor:
        assert cursor.execute('''select * from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            (key.user_id, key.task_id, key.task_lease_version)).fetchone() == before


def test_adapter_unknown_after_committed_result_still_needs_reconciliation(
        memory_publication, monkeypatch):
    service, db, _, user, attempt, live = _issue(memory_publication)
    key = AttemptKey(user, attempt.task.task_id, attempt.lease_version)
    execute = service._execute_live_publication
    calls = []

    def commit_then_lose_response(original):
        calls.append(original)
        assert execute(original).pair.import_task.status == 'succeeded'
        raise DurablePublicationUnknown(key, 'terminal_proof')

    monkeypatch.setattr(service, '_execute_live_publication', commit_then_lose_response)
    result = _adapter(service, db).run_once(live)
    assert calls == [live]
    assert ImportPublicationProofService(db).prove_exact(
        key.user_id, key.task_id, key.task_lease_version) is not None
    assert result.status == 'needs_reconciliation'
    assert result.reconciliation_reason == 'durable_unknown'
    with db.transaction() as cursor:
        queue = cursor.execute('''select state,reason_code from
            import_publication_recovery_queue where user_id=%s and task_id=%s
            and task_lease_version=%s''',
            (key.user_id, key.task_id, key.task_lease_version)).fetchone()
    assert queue['state'] == 'pending' and queue['reason_code'] == 'unknown'


def test_adapter_generic_evidence_failure_is_held_without_queue_change(
        memory_publication, monkeypatch):
    service, db, _, user, attempt, live = _issue(memory_publication)
    key = AttemptKey(user, attempt.task.task_id, attempt.lease_version)
    with db.transaction() as cursor:
        before = cursor.execute('''select * from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            (key.user_id, key.task_id, key.task_lease_version)).fetchone()
    calls = []

    def evidence_failure(original):
        calls.append(original)
        raise PublicationEvidenceError('evidence unavailable')

    monkeypatch.setattr(service, '_execute_live_publication', evidence_failure)
    result = _adapter(service, db).run_once(live)
    assert calls == [live]
    assert result.status == 'needs_reconciliation'
    assert result.reconciliation_reason == 'recovery_unavailable'
    with db.transaction() as cursor:
        assert cursor.execute('''select * from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            (key.user_id, key.task_id, key.task_lease_version)).fetchone() == before


def test_adapter_rejects_database_change_and_cross_issuer_before_execution(
        memory_publication, monkeypatch):
    service, db, _, _, _, live = _issue(memory_publication)
    with pytest.raises(TypeError, match='share the original database'):
        ImportPublicationWorkerAdapter(
            service, PostgresImportPublicationRecoveryRepository(object()),
            enabled=True)

    calls = []
    other_service = copy(service)
    monkeypatch.setattr(other_service, '_execute_live_publication', calls.append)
    with pytest.raises(ImportMemoryPublicationError, match='issuance'):
        _adapter(other_service, db).run_once(live)

    adapter = _adapter(service, db)
    adapter.recovery = PostgresImportPublicationRecoveryRepository(object())
    monkeypatch.setattr(service, '_execute_live_publication', calls.append)
    with pytest.raises(TypeError, match='database differ'):
        adapter.run_once(live)
    assert calls == []
