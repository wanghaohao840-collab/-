"""Detached proof of a reserved import on isolated PostgreSQL/S3/Qdrant."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import copy
from dataclasses import replace
import json
import math
from threading import Event
import time
from uuid import uuid4

import pytest
import psycopg
import app.import_memory_publication as live_module
from psycopg.types.json import Jsonb
from uuid import UUID

from app.import_memory_publication import (
    DurablePublicationUnknown, ImportMemoryPublicationError, _LivePublication,
    _ADMISSIONS,
    _require_terminal_admission,
)
from app.postgres_import_leases import InvalidImportTransition
from app.import_publication_evidence import (
    PostgresImportPublicationEvidenceRepository, PublicationEvidenceError,
)
from app.import_publication_proof import (
    ImportPublicationProofService, PublicationProofUnknown,
)
from app.postgres_vector_generations import (
    PostgresVectorGenerationAuthority, VectorAuthorityError,
)
from app.postgres_document_objects import (
    DocumentPublicationError, PostgresDocumentObjectRepository, VerifiedDocumentRef,
)
from app.postgres_history_document_witnesses import PostgresHistoryDocumentWitnessRepository
from app.postgres_import_leases import ImportLeaseLost, PostgresImportLeaseRepository
from app.postgres_memory_documents import PostgresMemoryDocumentStore
from app.object_store import ObjectRef, ObjectWrite
from tests.integration.test_import_document_publication import _point, _task, publication
from tests.integration.test_import_memory_publication import memory_publication
from tests.integration.test_import_publication_evidence import _advance_private_evidence
from tests.integration.test_postgres_auth_sessions import shared_database
from tests.integration.test_s3_object_store import store


def test_live_issue_reserves_before_io_and_ordinary_completion_refuses_callback(
        memory_publication, monkeypatch):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    monkeypatch.setattr(store, 'put_immutable', lambda *args, **kwargs:
                        pytest.fail('Issuance performed external write'))
    live = service._issue_live_publication(rag_scope, attempt,
        [_point(rag_scope, attempt.task.document_id, 'new')],
        event_vector=[1., 0., 0., 0.], event_profile=profile)
    frozen = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert frozen is not None and frozen.phase == 'intent'
    with pytest.raises(PublicationEvidenceError):
        service.pair.imports.try_begin_committing(attempt)
    with db.transaction() as cursor:
        cursor.execute('begin')
        row = service.pair.imports._live(cursor, attempt)
        with pytest.raises(PublicationEvidenceError):
            service.pair.imports._progress(cursor, attempt, row, 'committing', row['progress'])
    invoked = []
    with pytest.raises(InvalidImportTransition):
        service.pair.imports.complete(attempt, lambda cursor: invoked.append(True))
    assert invoked == []
    assert live is not None


def _issue(memory_publication):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    live = service._issue_live_publication(rag_scope, attempt,
        [_point(rag_scope, attempt.task.document_id, 'new')],
        event_vector=[1., 0., 0., 0.], event_profile=profile)
    return service, db, store, user, attempt, live


def test_live_publication_commits_and_detached_reader_proves(memory_publication):
    service, db, store, user, attempt, live = _issue(memory_publication)
    result = service._execute_live_publication(live)
    detached = ImportPublicationProofService(db).prove_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert result == detached
    assert detached is not None
    assert detached.proof.phase == 'terminal_committed'
    assert detached.pair.import_task.status == 'succeeded'
    assert len(detached.proof.rag_receipt) == len(detached.proof.episode_receipt) == 20
    assert not any(hasattr(detached, name) for name in
                   ('_context', '_expected', 'token', 'live', 'admission'))


def test_unissued_handle_refuses_before_document_io(memory_publication, monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    monkeypatch.setattr(store, 'put_immutable', lambda *args, **kwargs:
                        pytest.fail('Unissued handle reached S3'))
    with pytest.raises(ImportMemoryPublicationError):
        service._execute_live_publication(_LivePublication())
    saved = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert saved.phase == 'intent' and saved.document_slot is None


def test_terminal_failure_rolls_back_pair_domain_and_success(memory_publication, monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    original = service.documents.snapshots.compare_and_swap_in_transaction
    def fail_memory(cursor, owner, kind, data, **options):
        if kind == 'memory':
            raise RuntimeError('injected terminal failure')
        return original(cursor, owner, kind, data, **options)
    monkeypatch.setattr(service.documents.snapshots,
                        'compare_and_swap_in_transaction', fail_memory)
    with pytest.raises(DurablePublicationUnknown) as unknown:
        service._execute_live_publication(live)
    assert unknown.value.phase == 'terminal_commit'
    saved = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert saved.phase == 'pair_sealed' and saved.terminal_slot is None
    assert ImportPublicationProofService(db).prove_exact(
        user, attempt.task.task_id, attempt.lease_version) is None
    with db.transaction() as cursor:
        rows = cursor.execute('''select state from vector_generations
            where generation_id=any(%s)''', ([UUID(saved.intent['scopes'][kind]['candidate_id'])
            for kind in ('rag', 'episode')],)).fetchall()
        task = cursor.execute('select status from import_tasks where id=%s',
                              (attempt.task.task_id,)).fetchone()
        pinned = cursor.execute('''select 1 from document_objects where user_id=%s
            and document_id=%s''', (user, attempt.task.document_id)).fetchone()
    assert sorted(row['state'] for row in rows) == ['sealed', 'sealed']
    assert task['status'] == 'running' and pinned is None


def test_same_version_snapshot_tamper_refuses_but_later_retained_snapshot_proves(
        memory_publication):
    service, db, store, user, attempt, live = _issue(memory_publication)
    service._execute_live_publication(live)
    proof = ImportPublicationProofService(db)
    with db.transaction() as cursor:
        cursor.execute('begin')
        row = cursor.execute('''select payload from user_snapshots where user_id=%s
            and kind=%s''', (user, 'memory')).fetchone()
        payload = row['payload']
        payload['unrelated_later_field'] = 'added outside publication'
        cursor.execute('''update user_snapshots set payload=%s where user_id=%s
            and kind=%s''', (Jsonb(payload), user, 'memory'))
    assert proof.prove_exact(user, attempt.task.task_id, attempt.lease_version) is None
    # Test-only later snapshot fixture; the packet's later publication check is separate.
    with db.transaction() as cursor:
        cursor.execute('begin')
        cursor.execute('''update user_snapshots set version=version+1 where user_id=%s
            and kind=%s''', (user, 'memory'))
    assert proof.prove_exact(user, attempt.task.task_id,
                             attempt.lease_version) is not None


def test_lost_document_put_response_never_replays_the_original_live_handle(
        memory_publication, monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    original = store.put_immutable
    calls = []

    def lost_response(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        raise ConnectionError('injected lost S3 put response after real put')

    monkeypatch.setattr(store, 'put_immutable', lost_response)
    with pytest.raises(DurablePublicationUnknown) as unknown:
        service._execute_live_publication(live)
    assert unknown.value.phase == 'document_put' and len(calls) == 1
    with pytest.raises((DurablePublicationUnknown, ImportMemoryPublicationError)):
        service._execute_live_publication(live)
    assert len(calls) == 1
    saved = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert saved.phase == 'intent' and saved.document_slot is None


def test_committed_terminal_with_lost_response_proves_from_fresh_reader(
        memory_publication, monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    original = service.pair.imports.complete

    def lost_response(*args, **kwargs):
        original(*args, **kwargs)
        raise ConnectionError('injected lost PostgreSQL response after real commit')

    monkeypatch.setattr(service.pair.imports, 'complete', lost_response)
    result = service._execute_live_publication(live)
    fresh = ImportPublicationProofService(db).prove_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert result == fresh and fresh.proof.phase == 'terminal_committed'
    with db.transaction() as cursor:
        gate = cursor.execute('''select status from user_publication_gates
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            (user, attempt.task.task_id, attempt.lease_version)).fetchone()
    assert gate['status'] == 'unresolved'


@pytest.mark.parametrize('interrupt_at', ['batch', 'page'])
def test_candidate_authority_loss_stops_next_underlying_qdrant_operation(
        memory_publication, monkeypatch, interrupt_at):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    # 129 points force two 100-point upserts and two 128-point verification pages.
    points = [_point(rag_scope, attempt.task.document_id, f'part-{i}')
              for i in range(129)]
    live = service._issue_live_publication(rag_scope, attempt, points,
        event_vector=[1., 0., 0., 0.], event_profile=profile)
    client = rag.raw.client
    operation = 'upsert' if interrupt_at == 'batch' else 'scroll'
    original = getattr(client, operation)
    calls = []

    def expire_after_first(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        if len(calls) == 1:
            with db.transaction() as cursor:
                cursor.execute('''update user_mutation_leases
                    set lease_expires_at=clock_timestamp()-interval '1 second'
                    where user_id=%s''', (user,))
        return result

    monkeypatch.setattr(client, operation, expire_after_first)
    with pytest.raises(Exception):
        service._execute_live_publication(live)
    assert len(calls) == 1
    saved = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert saved.phase == 'document_verified' and saved.rag_sealed_slot is None
    assert ImportPublicationProofService(db).prove_exact(
        user, attempt.task.task_id, attempt.lease_version) is None


def test_concurrent_entry_cannot_reuse_one_live_handle(memory_publication, monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    entered, release = Event(), Event()
    original = store.put_immutable
    puts = []

    def waiting_put(*args, **kwargs):
        puts.append(True)
        entered.set()
        assert release.wait(30)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, 'put_immutable', waiting_put)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(service._execute_live_publication, live)
        assert entered.wait(30)
        with pytest.raises(ImportMemoryPublicationError):
            service._execute_live_publication(live)
        release.set()
        assert first.result(timeout=60) is not None
    assert puts == [True]


def test_lost_seal_response_never_abandons_or_replays(memory_publication, monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    authority = service.pair.rag.authority
    original = authority.seal
    calls = []

    def lost_response(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(True)
        raise ConnectionError('injected lost seal response after real seal')

    monkeypatch.setattr(authority, 'seal', lost_response)
    with pytest.raises(DurablePublicationUnknown):
        service._execute_live_publication(live)
    assert calls == [True]
    saved = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert saved.phase == 'document_verified' and saved.rag_sealed_slot is None
    with db.transaction() as cursor:
        generation = cursor.execute('''select state from vector_generations
            where generation_id=%s''',
            (UUID(saved.intent['scopes']['rag']['candidate_id']),)).fetchone()
    assert generation['state'] == 'sealed'
    with pytest.raises(ImportMemoryPublicationError):
        service._execute_live_publication(live)
    assert calls == [True]


@pytest.mark.parametrize('vector_kind', ['rag', 'episode'])
def test_revoked_sealed_reservation_fences_terminal_before_publication(
        memory_publication, monkeypatch, vector_kind):
    service, db, store, user, attempt, live = _issue(memory_publication)
    imports = service.pair.imports
    original = imports.try_begin_committing

    def revoke_before_terminal(*args, **kwargs):
        with db.transaction() as cursor:
            cursor.execute('''update generation_reservations
                set state='revoked',revoked_at=clock_timestamp()
                where generation_id=(select generation_id from generation_reservations
                    where user_id=%s and task_id=%s and vector_kind=%s)''',
                (user, attempt.task.task_id, vector_kind))
        return original(*args, **kwargs)

    monkeypatch.setattr(imports, 'try_begin_committing', revoke_before_terminal)
    with pytest.raises(DurablePublicationUnknown):
        service._execute_live_publication(live)
    saved = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert saved.phase == 'pair_sealed' and saved.terminal_slot is None
    with db.transaction() as cursor:
        states = cursor.execute('''select state from vector_generations
            where generation_id=any(%s)''', ([UUID(saved.intent['scopes'][kind]['candidate_id'])
            for kind in ('rag', 'episode')],)).fetchall()
        task = cursor.execute('select status from import_tasks where id=%s',
            (attempt.task.task_id,)).fetchone()
    assert sorted(row['state'] for row in states) == ['sealed', 'sealed']
    assert task['status'] == 'running'


def test_blocking_sealed_reservation_revocation_wins_before_terminal(
        memory_publication, monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    imports = service.pair.imports
    original = imports.try_begin_committing
    locked = Event()
    beginning = Event()
    release = Event()
    workers = ThreadPoolExecutor(max_workers=2)
    candidate = service._live_issues[live].plan.candidate_ids[0]

    def revoke_while_locked():
        with db.transaction() as cursor:
            cursor.execute('begin')
            row = cursor.execute('''select state from generation_reservations
                where generation_id=%s for update''', (candidate,)).fetchone()
            assert row['state'] == 'reserved'
            locked.set()
            assert release.wait(30)
            cursor.execute('''update generation_reservations
                set state='revoked',revoked_at=clock_timestamp()
                where generation_id=%s''', (candidate,))

    def contended_begin(*args, **kwargs):
        revocation = workers.submit(revoke_while_locked)
        assert locked.wait(30)
        beginning.set()
        try:
            return original(*args, **kwargs)
        finally:
            revocation.result(timeout=60)

    monkeypatch.setattr(imports, 'try_begin_committing', contended_begin)
    publication = workers.submit(service._execute_live_publication, live)
    try:
        assert beginning.wait(60)
        time.sleep(0.1)
        assert not publication.done()
    finally:
        release.set()
    with pytest.raises(DurablePublicationUnknown):
        publication.result(timeout=90)
    workers.shutdown(wait=True)
    saved = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert saved.phase == 'pair_sealed' and saved.terminal_slot is None
    with db.transaction() as cursor:
        head = cursor.execute('''select status,stage from import_tasks where id=%s''',
                              (attempt.task.task_id,)).fetchone()
    assert head['status'] == 'running' and head['stage'] != 'committing'


def test_intent_observation_version_is_used_for_document_append(memory_publication):
    service, db, store, user, attempt, live = _issue(memory_publication)
    with db.transaction() as cursor:
        changed = _advance_private_evidence(cursor,
            (user,attempt.task.task_id,attempt.lease_version),reason='unknown')
    assert changed['phase_version'] == 2
    result = service._execute_live_publication(live)
    assert result.proof.phase == 'terminal_committed'
    assert result.proof.phase_version >= 6


def test_changed_original_authority_refuses_before_document_put(
        memory_publication, monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    monkeypatch.setattr(store, 'put_immutable', lambda *args, **kwargs:
                        pytest.fail('changed authority reached S3'))
    service.pair.rag.authority = PostgresVectorGenerationAuthority(db)
    with pytest.raises(ImportMemoryPublicationError):
        service._execute_live_publication(live)


def test_terminal_admission_cannot_cross_transaction_on_same_cursor(
        memory_publication, monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    original_admission = live_module._create_terminal_admission
    reached = []

    def changed_transaction(work, cursor, evidence):
        admission = original_admission(work,cursor,evidence)
        original_id = _ADMISSIONS[admission].transaction_id
        cursor.execute('commit')
        cursor.execute('begin')
        assert cursor.execute('select txid_current() as id').fetchone()['id'] != original_id
        with pytest.raises(ImportMemoryPublicationError,
                           match='transaction changed'):
            _require_terminal_admission(admission, cursor, 'task_source', attempt)
        reached.append(True)
        raise RuntimeError('admission intentionally stopped after transaction test')

    monkeypatch.setattr(live_module,'_create_terminal_admission',
                        changed_transaction)
    with pytest.raises(DurablePublicationUnknown) as unknown:
        service._execute_live_publication(live)
    assert unknown.value.phase == 'terminal_commit' and reached == [True]
    saved = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user,attempt.task.task_id,attempt.lease_version)
    assert saved.phase == 'pair_sealed' and saved.terminal_slot is None


def test_changed_source_after_seals_refuses_before_committing_stage(
        memory_publication, monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    imports = service.pair.imports
    original = imports.try_begin_committing

    def change_pin_before_stage(*args, **kwargs):
        with db.transaction() as cursor:
            cursor.execute('''update import_objects set version_id=%s
                where task_id=%s and user_id=%s''',
                ('changed-version', attempt.task.task_id, user))
        return original(*args, **kwargs)

    monkeypatch.setattr(imports, 'try_begin_committing', change_pin_before_stage)
    with pytest.raises(DurablePublicationUnknown):
        service._execute_live_publication(live)
    with db.transaction() as cursor:
        task = cursor.execute('''select status,stage from import_tasks
            where id=%s''', (attempt.task.task_id,)).fetchone()
        audit = cursor.execute('''select last_stage,ended_at from import_task_attempts
            where task_id=%s and lease_version=%s''',
            (attempt.task.task_id, attempt.lease_version)).fetchone()
    assert task['status'] == 'running' and task['stage'] != 'committing'
    assert audit['last_stage'] != 'committing' and audit['ended_at'] is None


def test_simulated_acknowledgement_allows_later_real_publication_and_retained_proof(
        memory_publication):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    live = service._issue_live_publication(rag_scope, attempt,
        [_point(rag_scope, attempt.task.document_id, 'first')],
        event_vector=[1., 0., 0., 0.], event_profile=profile)
    first = service._execute_live_publication(live)
    proof = ImportPublicationProofService(db)
    assert proof.prove_exact(user, attempt.task.task_id, attempt.lease_version) == first
    # Test-only schema-valid simulation of future Packet 3 acknowledgement.
    # This keyed CAS is not an ordinary recovery API or recovery acceptance.
    with db.transaction() as cursor:
        cursor.execute('begin')
        evidence = [_advance_private_evidence(cursor,
            (user,attempt.task.task_id,attempt.lease_version),
            phase='proved_succeeded')]
        gate = cursor.execute('''update user_publication_gates
            set status='resolved',resolved_at=clock_timestamp()
            where user_id=%s and task_id=%s and task_lease_version=%s
              and status='unresolved' returning status''',
            (user, attempt.task.task_id, attempt.lease_version)).fetchall()
        assert len(evidence) == len(gate) == 1
        assert evidence[0]['phase_version'] == first.proof.phase_version + 1
    second_attempt = _task(db, store, user, content=b'later accepted bytes')
    later = service.publish(rag_scope, second_attempt,
        [_point(rag_scope, second_attempt.task.document_id, 'later')],
        event_vector=[0., 1., 0., 0.], event_profile=profile)
    assert later.pair.import_task.status == 'succeeded'
    retained = ImportPublicationProofService(db).prove_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert retained is not None and retained.proof.phase == 'proved_succeeded'
    assert retained.proof.phase_version == first.proof.phase_version + 1
    with db.transaction() as cursor:
        states = cursor.execute('''select state from vector_generations
            where generation_id=any(%s)''',
            ([UUID(first.proof.rag_receipt[0]),
              UUID(first.proof.episode_receipt[0])],)).fetchall()
    assert sorted(row['state'] for row in states) == ['retired', 'retired']


@pytest.mark.parametrize('target', ['task', 'source', 'memory_row'])
def test_detached_proof_rejects_retained_identity_tamper(memory_publication, target):
    service, db, store, user, attempt, live = _issue(memory_publication)
    result = service._execute_live_publication(live)
    if target == 'source':
        with pytest.raises(psycopg.errors.RaiseException,
                           match='Pinned import source has unresolved publication evidence'):
            with db.transaction() as cursor:
                cursor.execute('''update import_objects set version_id=%s
                    where task_id=%s and user_id=%s''',
                    ('changed-version', attempt.task.task_id, user))
        assert ImportPublicationProofService(db).prove_exact(
            user, attempt.task.task_id, attempt.lease_version) is not None
        return
    with db.transaction() as cursor:
        if target == 'task':
            cursor.execute('''update import_tasks set original_name=%s where id=%s''',
                           ('changed.txt', attempt.task.task_id))
        else:
            cursor.execute('''update memory_documents set content=%s
                where user_id=%s and document_id=%s''',
                ('changed event', user, result.event_id))
    assert ImportPublicationProofService(db).prove_exact(
        user, attempt.task.task_id, attempt.lease_version) is None


@pytest.mark.parametrize('failure_phase', ['evidence', 'terminal_committed'])
def test_detached_proof_read_failure_is_unknown(memory_publication, monkeypatch,
                                                failure_phase):
    service, db, store, user, attempt, live = _issue(memory_publication)
    service._execute_live_publication(live)
    proof = ImportPublicationProofService(db)
    if failure_phase == 'evidence':
        def fail_evidence(*args, **kwargs):
            raise RuntimeError('injected decoder failure')
        monkeypatch.setattr(proof.evidence, '_read_exact_in_cursor', fail_evidence)
    else:
        def fail_later_select(*args, **kwargs):
            raise psycopg.OperationalError('injected later SELECT failure')
        monkeypatch.setattr(proof, '_pair', fail_later_select)
    with pytest.raises(PublicationProofUnknown) as unknown:
        proof.prove_exact(user, attempt.task.task_id, attempt.lease_version)
    assert unknown.value.phase == failure_phase
    assert unknown.value.attempt_key.task_id == attempt.task.task_id


@pytest.mark.parametrize('write', [
    'rag_vector', 'episode_vector', 'old_witness', 'history_snapshot',
    'document_object', 'new_witness', 'memory_snapshot', 'memory_row',
    'terminal_slot', 'task_finish',
])
def test_each_terminal_write_failure_rolls_back_all_publication_state(
        memory_publication, monkeypatch, write):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    live = service._issue_live_publication(rag_scope, attempt,
        [_point(rag_scope, attempt.task.document_id, 'new')],
        event_vector=[1., 0., 0., 0.], event_profile=profile)
    hit = []

    def fail_after(original, predicate=lambda args, kwargs: True):
        def wrapped(*args, **kwargs):
            result = original(*args, **kwargs)
            if predicate(args, kwargs):
                hit.append(True)
                raise RuntimeError('injected failure after terminal write ' + write)
            return result
        return wrapped

    if write in ('rag_vector', 'episode_vector'):
        original = PostgresVectorGenerationAuthority._publish
        kind = write.split('_')[0]
        monkeypatch.setattr(PostgresVectorGenerationAuthority, '_publish',
            fail_after(original, lambda args, kwargs: args[2].vector_kind == kind))
    elif write in ('old_witness', 'new_witness'):
        original = PostgresHistoryDocumentWitnessRepository.insert
        ordinal = 1 if write == 'old_witness' else 2
        calls = []
        def selected(*args, **kwargs):
            calls.append(True)
            result = original(*args, **kwargs)
            if len(calls) == ordinal:
                hit.append(True)
                raise RuntimeError('injected failure after witness insert')
            return result
        monkeypatch.setattr(PostgresHistoryDocumentWitnessRepository,
                            'insert', staticmethod(selected))
    elif write in ('history_snapshot', 'memory_snapshot'):
        original = service.documents.snapshots.compare_and_swap_in_transaction
        kind = write.split('_')[0]
        monkeypatch.setattr(service.documents.snapshots,
            'compare_and_swap_in_transaction',
            fail_after(original, lambda args, kwargs: args[2] == kind))
    elif write == 'document_object':
        original = PostgresDocumentObjectRepository.publish_in_transaction
        monkeypatch.setattr(PostgresDocumentObjectRepository,
                            'publish_in_transaction', fail_after(original))
    elif write == 'memory_row':
        original = PostgresMemoryDocumentStore.add_document_in_transaction
        monkeypatch.setattr(PostgresMemoryDocumentStore,
                            'add_document_in_transaction', fail_after(original))
    elif write == 'terminal_slot':
        original = PostgresImportPublicationEvidenceRepository.append_terminal_in_transaction
        monkeypatch.setattr(PostgresImportPublicationEvidenceRepository,
                            'append_terminal_in_transaction', fail_after(original))
    else:
        original = PostgresImportLeaseRepository._finish
        monkeypatch.setattr(PostgresImportLeaseRepository, '_finish', fail_after(original))

    with pytest.raises(DurablePublicationUnknown):
        service._execute_live_publication(live)
    assert hit == [True]
    saved = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert saved.phase == 'pair_sealed' and saved.terminal_slot is None
    assert ImportPublicationProofService(db).prove_exact(
        user, attempt.task.task_id, attempt.lease_version) is None
    with db.transaction() as cursor:
        generations = cursor.execute('''select state from vector_generations
            where generation_id=any(%s)''',
            ([UUID(saved.intent['scopes'][kind]['candidate_id'])
              for kind in ('rag', 'episode')],)).fetchall()
        task = cursor.execute('''select status from import_tasks where id=%s''',
            (attempt.task.task_id,)).fetchone()
        audit = cursor.execute('''select ended_at from import_task_attempts
            where task_id=%s and lease_version=%s''',
            (attempt.task.task_id, attempt.lease_version)).fetchone()
        objects = cursor.execute('''select count(*) as n from document_objects
            where user_id=%s and document_id=%s''',
            (user, attempt.task.document_id)).fetchone()
        witnesses = cursor.execute('''select count(*) as n from history_document_witnesses
            where tenant_id=%s and namespace=%s and index_key=%s
            and head_revision=%s''',
            (user, rag_scope.namespace, rag_scope.index_key,
             saved.intent['scopes']['rag']['head']['revision'] + 1)).fetchone()
    assert sorted(row['state'] for row in generations) == ['sealed', 'sealed']
    assert task['status'] == 'running' and audit['ended_at'] is None
    assert objects['n'] == witnesses['n'] == 0
    assert snapshots.read(user, 'history').version == 1
    assert snapshots.read(user, 'memory').version == 1


@pytest.mark.parametrize('operation', [
    'collection_exists', 'get_collection', 'count', 'revoked_reservation',
    'retry_collection_exists',
])
def test_candidate_constructor_count_retry_and_revocation_recheck_before_io(
        memory_publication, monkeypatch, operation):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    live = service._issue_live_publication(rag_scope, attempt,
        [_point(rag_scope, attempt.task.document_id, 'new')],
        event_vector=[1., 0., 0., 0.], event_profile=profile)
    client = rag.raw.client
    name = ('collection_exists' if operation in
            ('revoked_reservation', 'retry_collection_exists') else operation)
    original = getattr(client, name)
    calls = []
    if operation == 'retry_collection_exists':
        rag.raw.retry_delays = (0,)

    def first_read(*args, **kwargs):
        calls.append(True)
        if operation != 'retry_collection_exists':
            result = original(*args, **kwargs)
        with db.transaction() as cursor:
            if operation == 'revoked_reservation':
                cursor.execute('''update generation_reservations
                    set state='revoked',revoked_at=clock_timestamp()
                    where generation_id=%s''',
                    (service._live_issues[live].plan.candidate_ids[0],))
            else:
                cursor.execute('''update user_mutation_leases
                    set lease_expires_at=clock_timestamp()-interval '1 second'
                    where user_id=%s''', (user,))
        if operation == 'retry_collection_exists':
            raise ConnectionError('injected retry after first underlying read')
        return result

    monkeypatch.setattr(client, name, first_read)
    with pytest.raises(Exception):
        service._execute_live_publication(live)
    assert calls == [True]
    saved = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert saved.phase == 'document_verified' and saved.rag_sealed_slot is None
    assert ImportPublicationProofService(db).prove_exact(
        user, attempt.task.task_id, attempt.lease_version) is None


def test_changed_terminal_admission_refuses_before_first_vector_write(
        memory_publication, monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    original_admission = live_module._create_terminal_admission
    original_publish = PostgresVectorGenerationAuthority._publish
    publications = []

    def wrong_transaction(*args, **kwargs):
        admission = original_admission(*args, **kwargs)
        live_module._ADMISSIONS[admission].transaction_id += 1
        return admission

    def count_publish(*args, **kwargs):
        publications.append(True)
        return original_publish(*args, **kwargs)

    monkeypatch.setattr(live_module, '_create_terminal_admission', wrong_transaction)
    monkeypatch.setattr(PostgresVectorGenerationAuthority, '_publish', count_publish)
    with pytest.raises(DurablePublicationUnknown):
        service._execute_live_publication(live)
    assert publications == []
    saved = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert saved.phase == 'pair_sealed' and saved.terminal_slot is None


def test_foreign_verified_document_issuer_refuses_continuation(
        memory_publication, monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    foreign = PostgresDocumentObjectRepository(db, store)
    monkeypatch.setattr(service.documents.documents, 'verify_for_publication',
                        foreign.verify_for_publication)
    with pytest.raises(ImportMemoryPublicationError):
        service._execute_live_publication(live)
    saved = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert saved.phase == 'intent' and saved.document_slot is None


def test_changed_s3_version_response_cannot_be_document_evidence(
        memory_publication, monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    original = store.put_immutable
    calls = []

    def changed_version(*args, **kwargs):
        write = original(*args, **kwargs)
        calls.append(True)
        ref = write.ref
        return ObjectWrite(ObjectRef(ref.key, ref.sha256, ref.size_bytes,
                                     'forged-version-id'), write.created)

    monkeypatch.setattr(store, 'put_immutable', changed_version)
    with pytest.raises(DurablePublicationUnknown):
        service._execute_live_publication(live)
    assert calls == [True]
    saved = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert saved.phase == 'intent' and saved.document_slot is None


def test_each_evidence_append_rejects_a_duplicate_second_write(
        memory_publication, monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    calls = []
    for method in ('append_document_in_transaction',
                   'append_sealed_in_transaction',
                   'append_terminal_in_transaction'):
        original = getattr(PostgresImportPublicationEvidenceRepository, method)
        def repeat(*args, original=original, method=method, **kwargs):
            result = original(*args, **kwargs)
            with pytest.raises(PublicationEvidenceError):
                original(*args, **kwargs)
            calls.append(method)
            return result
        monkeypatch.setattr(PostgresImportPublicationEvidenceRepository, method,
                            repeat)
    result = service._execute_live_publication(live)
    assert result.proof.phase == 'terminal_committed'
    assert calls == ['append_document_in_transaction',
                     'append_sealed_in_transaction',
                     'append_sealed_in_transaction',
                     'append_terminal_in_transaction']


@pytest.mark.parametrize('mutation', [
    'snapshot_kind', 'snapshot_version', 'snapshot_value',
    'event_content', 'event_metadata', 'document_token',
    'old_witness', 'new_witness',
])
def test_terminal_admission_refuses_changed_domain_arguments(
        memory_publication, monkeypatch, mutation):
    service, db, store, user, attempt, live = _issue(memory_publication)
    hit = []
    if mutation.startswith('snapshot_'):
        original = service.documents.snapshots.compare_and_swap_in_transaction
        def changed(cursor, subject, kind, data, *, expected_version, **kwargs):
            if kind == 'history':
                hit.append(True)
                if mutation == 'snapshot_kind':
                    kind = 'memory'
                elif mutation == 'snapshot_version':
                    expected_version += 1
                else:
                    data = dict(data, unauthorized=True)
            return original(cursor, subject, kind, data,
                            expected_version=expected_version, **kwargs)
        monkeypatch.setattr(service.documents.snapshots,
                            'compare_and_swap_in_transaction', changed)
    elif mutation.startswith('event_'):
        original = PostgresMemoryDocumentStore.add_document_in_transaction
        def changed(self, cursor, event_id, content, metadata, **kwargs):
            hit.append(True)
            if mutation == 'event_content':
                content += ' changed'
            else:
                metadata = '{"user_id":"wrong"}'
            return original(self, cursor, event_id, content, metadata, **kwargs)
        monkeypatch.setattr(PostgresMemoryDocumentStore,
                            'add_document_in_transaction', changed)
    elif mutation == 'document_token':
        original = PostgresDocumentObjectRepository.publish_in_transaction
        def changed(self, cursor, verified, **kwargs):
            hit.append(True)
            copied = VerifiedDocumentRef(verified.user_id, verified.document_id,
                                         verified.bucket, verified.ref)
            return original(self, cursor, copied, **kwargs)
        monkeypatch.setattr(PostgresDocumentObjectRepository,
                            'publish_in_transaction', changed)
    else:
        original = PostgresHistoryDocumentWitnessRepository.insert
        ordinal = 1 if mutation == 'old_witness' else 2
        calls = []
        def changed(cursor, scope, pairing, evidence, **kwargs):
            calls.append(True)
            if len(calls) == ordinal:
                hit.append(True)
                evidence = replace(evidence, digest='0' * 64)
            return original(cursor, scope, pairing, evidence, **kwargs)
        monkeypatch.setattr(PostgresHistoryDocumentWitnessRepository,
                            'insert', staticmethod(changed))
    with pytest.raises(DurablePublicationUnknown):
        service._execute_live_publication(live)
    assert hit == [True]
    saved = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert saved.phase == 'pair_sealed' and saved.terminal_slot is None


@pytest.mark.parametrize('tamper', [
    'receipt_timestamp', 'missing_witness', 'audit', 'tenant', 'deletion_fence',
])
def test_detached_proof_refuses_immutable_receipt_domain_and_fence_tamper(
        memory_publication, tamper):
    service, db, store, user, attempt, live = _issue(memory_publication)
    published = service._execute_live_publication(live)
    if tamper == 'audit':
        with pytest.raises(psycopg.errors.RaiseException,
                           match='Unresolved publication audit end is immutable'):
            with db.transaction() as cursor:
                cursor.execute('''update import_task_attempts set end_reason='failed'
                    where task_id=%s and lease_version=%s''',
                    (attempt.task.task_id, attempt.lease_version))
        assert ImportPublicationProofService(db).prove_exact(
            user, attempt.task.task_id, attempt.lease_version) is not None
        return
    if tamper == 'receipt_timestamp':
        # Test-only corruption inside this disposable schema. PostgreSQL's
        # deferred receipt trigger requires separate disable/write/enable
        # transactions; the fixture restores it even if the write fails.
        with db.transaction() as cursor:
            cursor.execute('alter table vector_generations disable trigger vector_generation_guard')
        try:
            with db.transaction() as cursor:
                cursor.execute('''update vector_generations
                    set published_at=published_at+interval '1 microsecond'
                    where generation_id=%s''',
                    (UUID(published.proof.rag_receipt[0]),))
        finally:
            with db.transaction() as cursor:
                cursor.execute('alter table vector_generations enable trigger vector_generation_guard')
    else:
        with db.transaction() as cursor:
            cursor.execute('begin')
            if tamper == 'missing_witness':
                cursor.execute('''alter table history_document_witnesses
                    disable trigger history_document_witness_guard''')
                cursor.execute('''delete from history_document_witnesses
                    where tenant_id=%s and vector_kind='rag' and namespace=%s
                      and index_key=%s and head_revision=%s''',
                    (user, service._live_issues[live].plan.rag_scope.namespace,
                     service._live_issues[live].plan.rag_scope.index_key,
                     published.proof.rag_receipt[7]))
                cursor.execute('''alter table history_document_witnesses
                    enable trigger history_document_witness_guard''')
            elif tamper == 'tenant':
                cursor.execute('''update memory_documents set metadata=%s
                    where user_id=%s and document_id=%s''',
                    ('{"user_id":"foreign","import_task_id":"wrong"}',
                     user, published.event_id))
            else:
                cursor.execute('''insert into qa_deletion_fences
                    (id,user_id,target_type,target_id,status,stage,created_at,updated_at)
                    values(%s,%s,'document',%s,'queued','fenced',%s,%s)''',
                    (str(uuid4()), user, attempt.task.document_id,
                     '2026-10-03T00:00:00Z', '2026-10-03T00:00:00Z'))
    assert ImportPublicationProofService(db).prove_exact(
        user, attempt.task.task_id, attempt.lease_version) is None


def test_detached_proof_uses_one_repeatable_read_read_only_transaction(
        memory_publication, monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    service._execute_live_publication(live)
    proof = ImportPublicationProofService(db)
    original = db.transaction
    scopes = []

    @contextmanager
    def tracked_transaction():
        with original() as cursor:
            scopes.append(None)
            yield cursor
            row = cursor.execute('''select current_setting('transaction_isolation')
                as isolation,current_setting('transaction_read_only') as read_only''').fetchone()
            scopes[-1] = (row['isolation'], row['read_only'])

    monkeypatch.setattr(db, 'transaction', tracked_transaction)
    assert proof.prove_exact(user, attempt.task.task_id,
                             attempt.lease_version) is not None
    assert scopes == [('repeatable read', 'on')]


def test_changed_fixed_callback_refuses_before_callback_invocation(
        memory_publication, monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    original_issue = service._issue_fixed_terminal
    invoked = []

    def change_callback(*args, **kwargs):
        work = original_issue(*args, **kwargs)
        monkeypatch.setattr(live_module._FixedTerminalWork, 'run',
                            lambda *call: invoked.append(True))
        return work

    monkeypatch.setattr(service, '_issue_fixed_terminal', change_callback)
    with pytest.raises(DurablePublicationUnknown):
        service._execute_live_publication(live)
    assert invoked == []
    saved = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert saved.phase == 'pair_sealed' and saved.terminal_slot is None


@pytest.mark.parametrize('changed', ['database', 'document_database', 'equal_subclass'])
def test_original_live_issuer_identity_refuses_before_s3(
        memory_publication, monkeypatch, changed):
    service, db, store, user, attempt, live = _issue(memory_publication)
    monkeypatch.setattr(store, 'put_immutable', lambda *args, **kwargs:
                        pytest.fail('changed issuer reached S3'))
    if changed == 'database':
        service.database = object()
        candidate = live
    elif changed == 'document_database':
        service.documents.documents.database = object()
        candidate = live
    else:
        class EqualSubclass(_LivePublication):
            def __hash__(self):
                return hash(live)
            def __eq__(self, other):
                return other is live
        candidate = EqualSubclass()
    with pytest.raises(ImportMemoryPublicationError):
        service._execute_live_publication(candidate)


def test_equal_subclass_of_sealed_descriptor_cannot_claim_original_issuance(
        memory_publication, monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    original = service.pair.rag._prepare_sealed

    def spoofed_seal(*args, **kwargs):
        issued = original(*args, **kwargs)
        class EqualSeal(type(issued)):
            def __hash__(self):
                return hash(issued)
            def __eq__(self, other):
                return other is issued
        return EqualSeal(**issued.__dict__)

    monkeypatch.setattr(service.pair.rag, '_prepare_sealed', spoofed_seal)
    with pytest.raises(DurablePublicationUnknown):
        service._execute_live_publication(live)
    saved = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert saved.phase == 'document_verified' and saved.rag_sealed_slot is None


def test_equal_subclass_of_verified_document_cannot_claim_original_token(
        memory_publication):
    service, db, store, user, attempt, live = _issue(memory_publication)
    planned = service._live_issues[live].plan.document
    prepared = service.documents._write_planned_document(planned, attempt, live=live)
    original = prepared.verified

    class EqualVerified(VerifiedDocumentRef):
        def __hash__(self):
            return hash(original)

        def __eq__(self, other):
            return other is original

    forged = EqualVerified(original.user_id, original.document_id,
                           original.bucket, original.ref)
    assert service.documents.documents._verified.get(forged) is not None
    with pytest.raises(ImportMemoryPublicationError):
        service._bind_verified_document(live, forged)
    assert live not in service._document_continuations
    with db.transaction() as cursor:
        cursor.execute('begin')
        with pytest.raises(DocumentPublicationError):
            service.documents.documents.publish_in_transaction(cursor, forged)
    saved = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert saved.phase == 'intent' and saved.document_slot is None


def test_closed_or_foreign_cursor_terminal_admission_refuses(
        memory_publication, monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    original_admission = live_module._create_terminal_admission
    reached = []

    def foreign_and_closed(work, cursor, evidence):
        admission = original_admission(work,cursor,evidence)
        with db.transaction() as other_cursor:
            other_cursor.execute('begin')
            with pytest.raises(ImportMemoryPublicationError,
                               match='Active transaction-bound terminal admission required'):
                _require_terminal_admission(admission, other_cursor,
                                            'task_source', attempt)
        live_module._close_terminal_admission(admission)
        with pytest.raises(ImportMemoryPublicationError,
                           match='Active transaction-bound terminal admission required'):
            _require_terminal_admission(admission, cursor,
                                        'task_source', attempt)
        reached.append(True)
        raise RuntimeError('admission intentionally stopped after cursor tests')

    monkeypatch.setattr(live_module,'_create_terminal_admission',
                        foreign_and_closed)
    with pytest.raises(DurablePublicationUnknown) as unknown:
        service._execute_live_publication(live)
    assert unknown.value.phase == 'terminal_commit' and reached == [True]
    saved = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user,attempt.task.task_id,attempt.lease_version)
    assert saved.phase == 'pair_sealed' and saved.terminal_slot is None


def test_shallow_copied_service_cannot_use_original_live_handle(memory_publication,
                                                                 monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    monkeypatch.setattr(store, 'put_immutable', lambda *args, **kwargs:
                        pytest.fail('copied service reached S3'))
    with pytest.raises(ImportMemoryPublicationError):
        copy(service)._execute_live_publication(live)
    assert PostgresImportPublicationEvidenceRepository(db).read_exact(
        user, attempt.task.task_id, attempt.lease_version).phase == 'intent'


def test_detached_proof_rejects_format_only_memory_metadata_tamper(
        memory_publication):
    service, db, store, user, attempt, live = _issue(memory_publication)
    result = service._execute_live_publication(live)
    with db.transaction() as cursor:
        cursor.execute('begin')
        row = cursor.execute('''select metadata from memory_documents
            where user_id=%s and document_id=%s''',
            (user, result.event_id)).fetchone()
        changed = json.dumps(json.loads(row['metadata']), indent=2)
        assert changed != row['metadata']
        cursor.execute('''update memory_documents set metadata=%s
            where user_id=%s and document_id=%s''',
            (changed, user, result.event_id))
    assert ImportPublicationProofService(db).prove_exact(
        user, attempt.task.task_id, attempt.lease_version) is None


@pytest.mark.parametrize('target', ['malformed_metadata', 'wrong_snapshot_owner'])
def test_detached_proof_rejects_malformed_retained_data(memory_publication, target):
    service, db, store, user, attempt, live = _issue(memory_publication)
    result = service._execute_live_publication(live)
    with db.transaction() as cursor:
        if target == 'malformed_metadata':
            cursor.execute('''update memory_documents set metadata=%s
                where user_id=%s and document_id=%s''',
                ('{', user, result.event_id))
        else:
            row = cursor.execute('''select payload from user_snapshots
                where user_id=%s and kind='memory' ''', (user,)).fetchone()
            changed = dict(row['payload'])
            changed['user_id'] = str(uuid4())
            cursor.execute('''update user_snapshots set payload=%s
                where user_id=%s and kind='memory' ''',
                (Jsonb(changed), user))
    assert ImportPublicationProofService(db).prove_exact(
        user, attempt.task.task_id, attempt.lease_version) is None


def test_detached_proof_builtin_read_error_is_unknown(memory_publication,
                                                      monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    service._execute_live_publication(live)
    proof = ImportPublicationProofService(db)
    def failed_read(*args, **kwargs):
        raise ValueError('injected decoded-row read failure')
    monkeypatch.setattr(proof, '_pair', failed_read)
    with pytest.raises(PublicationProofUnknown):
        proof.prove_exact(user, attempt.task.task_id, attempt.lease_version)


def test_observation_during_candidate_preparation_uses_fresh_seal_version(
        memory_publication, monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    original = service.pair.rag._prepare_sealed
    def observed(*args, **kwargs):
        sealed = original(*args, **kwargs)
        with db.transaction() as cursor:
            cursor.execute('begin')
            changed = _advance_private_evidence(cursor,
                (user,attempt.task.task_id,attempt.lease_version),reason='unknown')
        assert changed is not None
        return sealed
    monkeypatch.setattr(service.pair.rag, '_prepare_sealed', observed)
    result = service._execute_live_publication(live)
    assert result.proof.phase == 'terminal_committed'


@pytest.mark.parametrize('bad_vector', [[math.nan, 0., 0., 0.], [1., 0.]])
def test_invalid_rag_vector_refuses_before_reservation_and_s3(
        memory_publication, monkeypatch, bad_vector):
    service, db, store, rag, snapshots, scope, episode, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    point = _point(scope, attempt.task.document_id, 'invalid')
    point = replace(point, vector=bad_vector)
    monkeypatch.setattr(store, 'put_immutable', lambda *args, **kwargs:
                        pytest.fail('invalid vector reached S3'))
    with pytest.raises((ImportMemoryPublicationError, ValueError)):
        service._issue_live_publication(scope, attempt, [point],
            event_vector=[1., 0., 0., 0.], event_profile=profile)
    with db.transaction() as cursor:
        assert cursor.execute('select count(*) as n from user_publication_gates').fetchone()['n'] == 0
        assert cursor.execute('select count(*) as n from generation_reservations').fetchone()['n'] == 0
        assert cursor.execute('select count(*) as n from import_publication_evidence').fetchone()['n'] == 0


@pytest.mark.parametrize('invalid', ['long_id', 'control_id', 'reserved',
                                     'non_json', 'non_finite_payload',
                                     'nested_tuple', 'nested_numeric_key',
                                     'nested_cycle'])
def test_invalid_rag_candidate_refuses_before_reservation_and_s3(
        memory_publication, monkeypatch, invalid):
    service, db, store, rag, snapshots, scope, episode, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    point = _point(scope, attempt.task.document_id, 'invalid')
    if invalid == 'long_id':
        point = replace(point, id='x' * 513)
    elif invalid == 'control_id':
        point = replace(point, id='bad\x00id')
    else:
        payload = dict(point.payload)
        if invalid == 'nested_cycle':
            nested = []
            nested.append(nested)
            payload['extra'] = nested
        else:
            payload['extra' if invalid != 'reserved' else '_gv_any'] = {
                'reserved': 'x', 'non_json': object(),
                'non_finite_payload': math.nan,
                'nested_tuple': {'items': (1, 2)},
                'nested_numeric_key': {'item': {1: 'x'}},
            }[invalid]
        point = replace(point, payload=payload)
    monkeypatch.setattr(store, 'put_immutable', lambda *args, **kwargs:
                        pytest.fail('invalid candidate reached S3'))
    with pytest.raises((ImportMemoryPublicationError, ValueError)):
        service._issue_live_publication(scope, attempt, [point],
            event_vector=[1., 0., 0., 0.], event_profile=profile)
    with db.transaction() as cursor:
        assert cursor.execute('select count(*) as n from user_publication_gates').fetchone()['n'] == 0
        assert cursor.execute('select count(*) as n from generation_reservations').fetchone()['n'] == 0
        assert cursor.execute('select count(*) as n from import_publication_evidence').fetchone()['n'] == 0


def test_oversized_rag_corpus_refuses_before_reservation_and_s3(
        memory_publication, monkeypatch):
    service, db, store, rag, snapshots, scope, episode, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    original = service.documents._plan_document
    def oversized(*args, **kwargs):
        planned = original(*args, **kwargs)
        return replace(planned, points=(planned.points[0],) * 100001)
    monkeypatch.setattr(service.documents, '_plan_document', oversized)
    monkeypatch.setattr(store, 'put_immutable', lambda *args, **kwargs:
                        pytest.fail('oversized corpus reached S3'))
    with pytest.raises(ImportMemoryPublicationError, match='bound'):
        service._issue_live_publication(scope, attempt,
            [_point(scope, attempt.task.document_id, 'new')],
            event_vector=[1., 0., 0., 0.], event_profile=profile)
    with db.transaction() as cursor:
        assert cursor.execute('select count(*) as n from user_publication_gates').fetchone()['n'] == 0
        assert cursor.execute('select count(*) as n from generation_reservations').fetchone()['n'] == 0
        assert cursor.execute('select count(*) as n from import_publication_evidence').fetchone()['n'] == 0


def test_revocation_after_last_upload_blocks_verification_constructor_reads(
        memory_publication, monkeypatch):
    service, db, store, user, attempt, live = _issue(memory_publication)
    client = service.pair.rag.raw.client
    original_exists = client.collection_exists
    original_upsert = client.upsert
    reads = []
    def observed_exists(*args, **kwargs):
        reads.append(True)
        return original_exists(*args, **kwargs)
    def revoked_after_upsert(*args, **kwargs):
        result = original_upsert(*args, **kwargs)
        with db.transaction() as cursor:
            cursor.execute('''update generation_reservations
                set state='revoked',revoked_at=clock_timestamp()
                where generation_id=%s''',
                (service._live_issues[live].plan.candidate_ids[0],))
        return result
    monkeypatch.setattr(client, 'collection_exists', observed_exists)
    monkeypatch.setattr(client, 'upsert', revoked_after_upsert)
    with pytest.raises(Exception):
        service._execute_live_publication(live)
    assert reads == [True]
    saved = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert saved.phase == 'document_verified' and saved.rag_sealed_slot is None


@pytest.mark.parametrize('lost_at', ['stage', 'upload'])
def test_applied_candidate_response_loss_never_abandons_or_replays(
        memory_publication, monkeypatch, lost_at):
    service, db, store, user, attempt, live = _issue(memory_publication)
    authority = service.pair.rag.authority
    client = service.pair.rag.raw.client
    calls = []
    if lost_at == 'stage':
        original = authority.stage
        def lost_stage(*args, **kwargs):
            result = original(*args, **kwargs)
            calls.append(True)
            raise ConnectionError('lost stage response after commit')
        monkeypatch.setattr(authority, 'stage', lost_stage)
    else:
        original = client.upsert
        def lost_upload(*args, **kwargs):
            result = original(*args, **kwargs)
            calls.append(True)
            raise ConnectionError('lost upload response after application')
        monkeypatch.setattr(client, 'upsert', lost_upload)
    abandon = []
    monkeypatch.setattr(service.pair.rag, '_abandon_or_quarantine',
                        lambda *args, **kwargs: abandon.append(True))
    with pytest.raises(DurablePublicationUnknown):
        service._execute_live_publication(live)
    assert calls == [True] and abandon == []
    with pytest.raises(ImportMemoryPublicationError):
        service._execute_live_publication(live)
    assert calls == [True]
    saved = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert saved.phase == 'document_verified' and saved.rag_sealed_slot is None
    with db.transaction() as cursor:
        row = cursor.execute('''select state from vector_generations
            where generation_id=%s''',
            (service._live_issues[live].plan.candidate_ids[0],)).fetchone()
    assert row['state'] == 'staging'


@pytest.mark.parametrize('refusal', ['expired_lease', 'authority'])
def test_known_stage_refusal_keeps_original_error_before_qdrant(
        memory_publication, monkeypatch, refusal):
    service, db, store, user, attempt, live = _issue(memory_publication)
    authority = service.pair.rag.authority
    original = authority.stage
    def refuse(*args, **kwargs):
        if refusal == 'expired_lease':
            with db.transaction() as cursor:
                cursor.execute('''update user_mutation_leases
                    set lease_expires_at=clock_timestamp() - interval '1 second'
                    where user_id=%s''', (user,))
            return original(*args, **kwargs)
        raise VectorAuthorityError('known stage refusal')
    monkeypatch.setattr(authority, 'stage', refuse)
    for operation in ('collection_exists', 'get_collection', 'count', 'scroll', 'upsert'):
        monkeypatch.setattr(service.pair.rag.raw.client, operation,
                            lambda *args, **kwargs: pytest.fail('stage refusal reached Qdrant'))
    with pytest.raises(ImportLeaseLost if refusal == 'expired_lease'
                       else VectorAuthorityError):
        service._execute_live_publication(live)
    saved = PostgresImportPublicationEvidenceRepository(db).read_exact(
        user, attempt.task.task_id, attempt.lease_version)
    assert saved.phase == 'document_verified' and saved.rag_sealed_slot is None
