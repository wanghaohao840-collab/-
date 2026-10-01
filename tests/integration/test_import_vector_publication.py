"""Fixed RAG/episode publication against disposable PG and Qdrant state."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from contextlib import contextmanager
import threading
from uuid import uuid4

import pytest

from app.import_vector_publication import (
    ImportPairRejected, ImportPairUnknown, ImportVectorPairPublicationService,
    PairScopePlan,
)
from app.postgres_import_leases import PostgresImportLeaseRepository
from app.postgres_vector_generations import (
    PostgresVectorGenerationAuthority, VectorAuthorityError, VectorScope,
)
from app.vector_generation_service import VectorGenerationService
from hello_agents.memory.rag.embedding_profile import EmbeddingProfile
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.storage.vector_store import QdrantVectorStore, VectorPoint
from tests.integration.test_vector_generation_service import import_attempt, setup
from tests.integration.test_postgres_auth_sessions import shared_database


def plans(setup, request):
    rag, _, db, _, raw, rag_scope, users = setup
    identity = IndexIdentity('qdrant', 'episode_' + uuid4().hex,
                             EmbeddingProfile('simple', '', 'deterministic', 'v1', 3))
    raw.ensure_collection(identity.physical_collection, 3)
    request.addfinalizer(lambda: raw.client.delete_collection(identity.physical_collection))
    episode_scope = VectorScope(users[0], 'episode', 'memory', identity)
    episode = VectorGenerationService(PostgresVectorGenerationAuthority(db), raw)
    return (rag, episode, rag_scope, episode_scope,
            PairScopePlan(rag, rag_scope, rag.authority.read_head(rag_scope),
                          [VectorPoint('rag', [1., 0., 0., 0.], {'marker': 'rag'})], 1),
            PairScopePlan(episode, episode_scope, episode.authority.read_head(episode_scope),
                          [VectorPoint('episode', [0., 1., 0.], {'marker': 'episode'})], 1))


def test_pair_publishes_two_heads_with_one_completion(setup, monkeypatch, request):
    rag, episode, rag_scope, episode_scope, rag_plan, episode_plan = plans(setup, request)
    db = setup[2]
    attempt = import_attempt(db, setup[6][0])
    service = ImportVectorPairPublicationService(rag, episode)
    imports = rag.authority.imports
    original_begin, original_complete = imports.try_begin_committing, imports.complete
    calls = []

    def begin(owner):
        calls.append('begin')
        return original_begin(owner)

    def complete(owner, callback):
        calls.append('complete')
        return original_complete(owner, callback)

    monkeypatch.setattr(imports, 'try_begin_committing', begin)
    monkeypatch.setattr(imports, 'complete', complete)
    result = service.publish_prepared_pair(rag_plan, episode_plan, attempt)
    assert calls == ['begin', 'complete']
    assert result.import_task.status == 'succeeded'
    assert result.rag.head == rag.authority.read_head(rag_scope)
    assert result.episode.head == episode.authority.read_head(episode_scope)
    assert service.reconcile_pair(result._context) == result
    assert rag.read_view(rag_scope).count(rag_scope.identity.physical_collection) == 1
    assert episode.read_view(episode_scope).count(episode_scope.identity.physical_collection) == 1


def test_second_head_or_callback_failure_rolls_back_both(setup, monkeypatch, request):
    rag, episode, rag_scope, episode_scope, rag_plan, episode_plan = plans(setup, request)
    db = setup[2]
    attempt = import_attempt(db, setup[6][0])
    original = episode.authority._publish
    failure = RuntimeError('second head failed')

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(episode.authority, '_publish', fail)
    with pytest.raises(RuntimeError) as captured:
        ImportVectorPairPublicationService(rag, episode).publish_prepared_pair(
            rag_plan, episode_plan, attempt)
    assert captured.value is failure
    assert rag.authority.read_head(rag_scope).state == 'missing'
    assert episode.authority.read_head(episode_scope).state == 'missing'
    with db.transaction() as cursor:
        task = cursor.execute('select status,stage from import_tasks where id=%s',
                              (attempt.task.task_id,)).fetchone()
    assert task == {'status': 'running', 'stage': 'committing'}
    monkeypatch.setattr(episode.authority, '_publish', original)


def test_lost_completion_response_reconciles_both_receipts(setup, monkeypatch, request):
    rag, episode, rag_scope, episode_scope, rag_plan, episode_plan = plans(setup, request)
    attempt = import_attempt(setup[2], setup[6][0])
    service = ImportVectorPairPublicationService(rag, episode)
    original = rag.authority.imports.complete

    def lost(owner, callback):
        original(owner, callback)
        raise ConnectionError('response lost')

    monkeypatch.setattr(rag.authority.imports, 'complete', lost)
    result = service.publish_prepared_pair(rag_plan, episode_plan, attempt)
    assert result.import_task.status == 'succeeded'
    assert result.rag.head == rag.authority.read_head(rag_scope)
    assert result.episode.head == episode.authority.read_head(episode_scope)


def test_first_stage_lost_retains_both_ids_without_retry(setup, monkeypatch, request):
    rag, episode, rag_scope, episode_scope, rag_plan, episode_plan = plans(setup, request)
    attempt = import_attempt(setup[2], setup[6][0])
    service = ImportVectorPairPublicationService(rag, episode)
    calls = []

    def lost(*args, **kwargs):
        calls.append(kwargs['generation_id'])
        raise ConnectionError('stage response lost')

    monkeypatch.setattr(rag.authority, 'stage', lost)
    with pytest.raises(ImportPairUnknown) as captured:
        service.publish_prepared_pair(rag_plan, episode_plan, attempt)
    error = captured.value
    assert len(calls) == 1 and error.generation_ids[0] == calls[0]
    assert error.generation_ids[0] != error.generation_ids[1]
    assert error.candidate_states[error.generation_ids[0]] == 'unconfirmed'
    assert error.candidate_states[error.generation_ids[1]] == 'not_invoked'
    assert rag.authority.read_head(rag_scope).state == 'missing'
    assert episode.authority.read_head(episode_scope).state == 'missing'


def test_second_upload_failure_abandons_both_and_keeps_first_proof(setup, monkeypatch, request):
    rag, episode, rag_scope, episode_scope, rag_plan, episode_plan = plans(setup, request)
    attempt = import_attempt(setup[2], setup[6][0])
    service = ImportVectorPairPublicationService(rag, episode)
    raw = setup[4]
    original = raw.client.upsert

    def fail_episode(*args, **kwargs):
        if kwargs['collection_name'] == episode_scope.identity.physical_collection:
            raise TimeoutError('episode write unknown')
        return original(*args, **kwargs)

    monkeypatch.setattr(raw.client, 'upsert', fail_episode)
    with pytest.raises(ImportPairRejected) as captured:
        service.publish_prepared_pair(rag_plan, episode_plan, attempt)
    context = captured.value.context
    assert context.rag.sealed is not None
    assert context.rag.sealed.expected_count == 1
    assert context.episode.sealed is None
    for scope, generation_id in zip(context.scopes, context.generation_ids):
        assert rag.authority.reconcile(scope, generation_id)['state'] == 'abandoned'
    assert rag.authority.read_head(rag_scope).state == 'missing'
    assert episode.authority.read_head(episode_scope).state == 'missing'


def test_pair_freezes_nested_points_before_first_io(setup, monkeypatch, request):
    rag, episode, rag_scope, episode_scope, rag_plan, episode_plan = plans(setup, request)
    attempt = import_attempt(setup[2], setup[6][0])
    payload = {'nested': {'value': 'original'}}
    vector = [1., 0., 0., 0.]
    rag_plan = replace(rag_plan, complete_corpus=[VectorPoint('rag', vector, payload)])
    original = rag.authority.read_head
    mutated = []

    def mutate_after_freeze(scope):
        if not mutated:
            payload['nested']['value'] = 'changed'
            vector[0] = 0.
            mutated.append(True)
        return original(scope)

    monkeypatch.setattr(rag.authority, 'read_head', mutate_after_freeze)
    result = ImportVectorPairPublicationService(rag, episode).publish_prepared_pair(
        rag_plan, episode_plan, attempt)
    assert result.import_task.status == 'succeeded'
    point = rag.read_view(rag_scope).scroll(rag_scope.identity.physical_collection,
                                           with_vectors=True)[0]
    assert point.vector == [1., 0., 0., 0.]
    assert point.payload['nested'] == {'value': 'original'}


def test_pair_rejects_cross_user_and_cross_database_before_io(setup, monkeypatch, request):
    rag, episode, rag_scope, episode_scope, rag_plan, episode_plan = plans(setup, request)
    attempt = import_attempt(setup[2], setup[6][0])
    other_user = replace(episode_plan, scope=replace(episode_scope,
                                                     tenant_id=setup[6][1]))
    with pytest.raises(ValueError, match='Wrong tenant'):
        ImportVectorPairPublicationService(rag, episode).publish_prepared_pair(
            rag_plan, other_user, attempt)
    assert rag.authority.read_head(rag_scope).state == 'missing'
    other_service = VectorGenerationService(PostgresVectorGenerationAuthority(setup[3]), setup[4])
    with pytest.raises(ValueError, match='same database'):
        ImportVectorPairPublicationService(rag, other_service)


def test_begin_false_abandons_both_and_never_completes(setup, monkeypatch, request):
    rag, episode, rag_scope, episode_scope, rag_plan, episode_plan = plans(setup, request)
    attempt = import_attempt(setup[2], setup[6][0])
    service = ImportVectorPairPublicationService(rag, episode)
    calls = []

    def no_begin(owner):
        calls.append('begin')
        return False

    def forbidden(*args, **kwargs):
        calls.append('complete')
        raise AssertionError('completion forbidden')

    monkeypatch.setattr(service.imports, 'try_begin_committing', no_begin)
    monkeypatch.setattr(service.imports, 'complete', forbidden)
    with pytest.raises(ImportPairRejected) as captured:
        service.publish_prepared_pair(rag_plan, episode_plan, attempt)
    assert calls == ['begin']
    for scope, generation_id in zip(captured.value.context.scopes,
                                    captured.value.generation_ids):
        assert rag.authority.reconcile(scope, generation_id)['state'] == 'abandoned'


def test_pair_domain_callback_rollback_is_original_exception(setup, request):
    rag, episode, rag_scope, episode_scope, rag_plan, episode_plan = plans(setup, request)
    db = setup[2]
    attempt = import_attempt(db, setup[6][0])
    failure = RuntimeError('domain rejected')

    def domain(cursor):
        cursor.execute("update users set updated_at='rolled-back' where id=%s",
                       (attempt.task.user_id,))
        raise failure

    with pytest.raises(RuntimeError) as captured:
        ImportVectorPairPublicationService(rag, episode).publish_prepared_pair(
            rag_plan, episode_plan, attempt, _domain_work=domain)
    assert captured.value is failure
    assert rag.authority.read_head(rag_scope).state == 'missing'
    assert episode.authority.read_head(episode_scope).state == 'missing'
    with db.transaction() as cursor:
        task = cursor.execute('select status,stage from import_tasks where id=%s',
                              (attempt.task.task_id,)).fetchone()
        user = cursor.execute('select updated_at from users where id=%s',
                              (attempt.task.user_id,)).fetchone()
    assert task == {'status': 'running', 'stage': 'committing'}
    assert user['updated_at'] != 'rolled-back'


def test_ambiguous_first_seal_wrong_tuple_is_unknown(setup, monkeypatch, request):
    rag, episode, rag_scope, episode_scope, rag_plan, episode_plan = plans(setup, request)
    attempt = import_attempt(setup[2], setup[6][0])
    service = ImportVectorPairPublicationService(rag, episode)
    original_seal = rag.authority.seal
    original_receipt = rag.authority.publication_receipt

    def sealed_then_lost(*args, **kwargs):
        original_seal(*args, **kwargs)
        raise ConnectionError('seal response lost')

    def wrong_tuple(*args, **kwargs):
        return original_receipt(*args, **kwargs) | {'task_lease_version': -1}

    monkeypatch.setattr(rag.authority, 'seal', sealed_then_lost)
    monkeypatch.setattr(rag.authority, 'publication_receipt', wrong_tuple)
    with pytest.raises(ImportPairUnknown) as captured:
        service.publish_prepared_pair(rag_plan, episode_plan, attempt)
    context = captured.value.context
    assert context.rag.sealed is None and context.episode.sealed is None
    assert len(set(context.generation_ids)) == 2
    assert captured.value.candidate_states[context.episode.generation_id] == 'not_invoked'
    assert rag.authority.read_head(rag_scope).state == 'missing'


def test_pair_reconciliation_accepts_retired_historical_receipts(setup, request):
    rag, episode, rag_scope, episode_scope, rag_plan, episode_plan = plans(setup, request)
    db = setup[2]
    attempt = import_attempt(db, setup[6][0])
    service = ImportVectorPairPublicationService(rag, episode)
    first = service.publish_prepared_pair(rag_plan, episode_plan, attempt)
    from tests.integration.test_vector_generation_service import lease, point
    next_lease = lease(db, setup[6][0], 'next-owner')
    rag.publish_complete(rag_scope, next_lease, first.rag.head, [point('next')])
    episode.publish_complete(episode_scope, next_lease, first.episode.head,
                             [VectorPoint('next', [0., 1., 0.], {'marker': 'next'})])
    assert rag.authority.reconcile(rag_scope, first.rag.generation_id)['state'] == 'retired'
    assert episode.authority.reconcile(episode_scope, first.episode.generation_id)['state'] == 'retired'
    assert service.reconcile_pair(first._context) == first


def test_definitive_second_stage_refusal_keeps_first_seal_and_abandons_it(
        setup, monkeypatch, request):
    rag, episode, rag_scope, episode_scope, rag_plan, episode_plan = plans(setup, request)
    attempt = import_attempt(setup[2], setup[6][0])
    service = ImportVectorPairPublicationService(rag, episode)

    def reject(*args, **kwargs):
        raise VectorAuthorityError('stage CAS refused')

    monkeypatch.setattr(episode.authority, 'stage', reject)
    with pytest.raises(ImportPairRejected) as captured:
        service.publish_prepared_pair(rag_plan, episode_plan, attempt)
    context = captured.value.context
    assert context.rag.sealed is not None
    assert context.episode.sealed is None
    assert rag.authority.reconcile(rag_scope, context.rag.generation_id)['state'] == 'abandoned'
    assert rag.authority.reconcile(episode_scope, context.episode.generation_id) is None
    assert rag.authority.read_head(rag_scope).state == 'missing'


def test_stage_commits_then_loses_response_retains_id_and_abandons(setup, monkeypatch, request):
    rag, episode, rag_scope, _, rag_plan, episode_plan = plans(setup, request)
    attempt = import_attempt(setup[2], setup[6][0])
    original = rag.authority.stage
    staged = []

    def stage_then_lost(*args, **kwargs):
        staged.append(kwargs['generation_id'])
        original(*args, **kwargs)
        raise ConnectionError('response lost after stage commit')

    monkeypatch.setattr(rag.authority, 'stage', stage_then_lost)
    with pytest.raises(ImportPairRejected) as captured:
        ImportVectorPairPublicationService(rag, episode).publish_prepared_pair(
            rag_plan, episode_plan, attempt)
    assert staged == [captured.value.generation_ids[0]]
    assert rag.authority.reconcile(rag_scope, staged[0])['state'] == 'abandoned'


def test_partial_abandonment_failure_retains_both_candidate_states(
        setup, monkeypatch, request):
    rag, episode, rag_scope, episode_scope, rag_plan, episode_plan = plans(setup, request)
    attempt = import_attempt(setup[2], setup[6][0])
    original_upload = episode.raw.client.upsert
    original_abandon = rag.authority.abandon

    def failed_episode(*args, **kwargs):
        if kwargs['collection_name'] == episode_scope.identity.physical_collection:
            raise TimeoutError('episode upload failed')
        return original_upload(*args, **kwargs)

    def failed_rag_abandon(scope, *args, **kwargs):
        if scope.key == rag_scope.key:
            raise ConnectionError('abandon response unavailable')
        return original_abandon(scope, *args, **kwargs)

    monkeypatch.setattr(episode.raw.client, 'upsert', failed_episode)
    monkeypatch.setattr(rag.authority, 'abandon', failed_rag_abandon)
    with pytest.raises(ImportPairUnknown) as captured:
        ImportVectorPairPublicationService(rag, episode).publish_prepared_pair(
            rag_plan, episode_plan, attempt)
    context = captured.value.context
    assert context.rag.sealed is not None
    assert captured.value.candidate_states[context.rag.generation_id] == 'unconfirmed'
    assert captured.value.candidate_states[context.episode.generation_id] == 'abandoned'
    assert rag.authority.read_head(rag_scope).state == 'missing'


def test_descriptor_tamper_cannot_commit_heads(setup, monkeypatch, request):
    rag, episode, rag_scope, episode_scope, rag_plan, episode_plan = plans(setup, request)
    attempt = import_attempt(setup[2], setup[6][0])
    original = rag._prepare_sealed

    def altered(*args, **kwargs):
        return replace(original(*args, **kwargs), content_digest='x' * 64)

    monkeypatch.setattr(rag, '_prepare_sealed', altered)
    with pytest.raises(ImportPairRejected) as captured:
        ImportVectorPairPublicationService(rag, episode).publish_prepared_pair(
            rag_plan, episode_plan, attempt)
    assert captured.value.context.rag.sealed is not None
    assert rag.authority.read_head(rag_scope).state == 'missing'
    assert episode.authority.read_head(episode_scope).state == 'missing'


def test_unresolved_completion_before_callback_is_unknown(
        setup, monkeypatch, request):
    rag, episode, rag_scope, episode_scope, rag_plan, episode_plan = plans(setup, request)
    attempt = import_attempt(setup[2], setup[6][0])
    service = ImportVectorPairPublicationService(rag, episode)
    calls = []

    def lost_before_decision(owner, callback):
        calls.append('complete')
        raise ConnectionError('commit decision not observed')

    monkeypatch.setattr(service.imports, 'complete', lost_before_decision)
    with pytest.raises(ImportPairUnknown) as captured:
        service.publish_prepared_pair(rag_plan, episode_plan, attempt)
    assert calls == ['complete']
    assert set(captured.value.candidate_states.values()) == {'sealed'}
    assert rag.authority.read_head(rag_scope).state == 'missing'
    assert episode.authority.read_head(episode_scope).state == 'missing'


def test_transaction_exit_replaces_callback_error_with_unknown(
        setup, monkeypatch, request):
    rag, episode, rag_scope, episode_scope, rag_plan, episode_plan = plans(setup, request)
    attempt = import_attempt(setup[2], setup[6][0])
    service = ImportVectorPairPublicationService(rag, episode)
    transaction = service.database.transaction

    @contextmanager
    def replaced_transaction():
        try:
            with transaction() as cursor:
                yield cursor
        except RuntimeError as error:
            # Simulates a rollback/close failure replacing the callback error.
            raise ConnectionError('transaction exit obscured rollback') from error

    monkeypatch.setattr(service.database, 'transaction', replaced_transaction)

    def fail_domain(cursor):
        raise RuntimeError('original callback failure')

    with pytest.raises(ImportPairUnknown) as captured:
        service.publish_prepared_pair(rag_plan, episode_plan, attempt,
                                      _domain_work=fail_domain)
    assert captured.value.phase == 'completion'
    assert set(captured.value.candidate_states.values()) == {'sealed'}
    assert rag.authority.read_head(rag_scope).state == 'missing'
    assert episode.authority.read_head(episode_scope).state == 'missing'


def test_valid_shape_wrong_descriptor_digest_is_rejected_inside_completion(
        setup, monkeypatch, request):
    rag, episode, rag_scope, episode_scope, rag_plan, episode_plan = plans(setup, request)
    attempt = import_attempt(setup[2], setup[6][0])
    original = rag._prepare_sealed

    def altered(*args, **kwargs):
        return replace(original(*args, **kwargs), content_digest='f' * 64)

    monkeypatch.setattr(rag, '_prepare_sealed', altered)
    with pytest.raises(VectorAuthorityError, match='Candidate base/index revision changed'):
        ImportVectorPairPublicationService(rag, episode).publish_prepared_pair(
            rag_plan, episode_plan, attempt)
    assert rag.authority.read_head(rag_scope).state == 'missing'
    assert episode.authority.read_head(episode_scope).state == 'missing'


def test_coherent_reader_rejects_forged_receipt_and_shares_readonly_snapshot(
        setup, monkeypatch, request):
    rag, episode, _, _, rag_plan, episode_plan = plans(setup, request)
    attempt = import_attempt(setup[2], setup[6][0])
    service = ImportVectorPairPublicationService(rag, episode)
    result = service.publish_prepared_pair(rag_plan, episode_plan, attempt)
    context = result._context
    with service.database.transaction() as cursor:
        cursor.execute('set transaction isolation level repeatable read read only')
        rows = service._read_pair_evidence_in_cursor(cursor, context)
        domain = cursor.execute('select id from users where id=%s',
                                (context.attempt.task.user_id,)).fetchone()
        assert domain['id'] == context.attempt.task.user_id
        assert service._validate_pair_evidence(context, rows) == result
    for field, wrong in (
            ('owner', 'forged'), ('base_revision', 999), ('index_revision', 999),
            ('publication_revision', 999), ('publication_snapshot_version', 999),
            ('expected_count', 999), ('content_digest', '0' * 64),
            ('task_status', 'running'), ('attempt_end_reason', 'failed'),
            ('identity', {'forged': True}), ('audit_task_token', 'forged')):
        altered = (dict(rows[0]) | {field: wrong}, rows[1])
        assert service._validate_pair_evidence(context, altered) is None, field
    assert service._validate_pair_evidence(context, (None, rows[1])) is None
    sealed = context.rag.sealed
    assert sealed is not None
    for changed in (
            replace(sealed, scope=replace(sealed.scope, namespace='forged')),
            replace(sealed, generation_id=uuid4()),
            replace(sealed, owner_key=sealed.owner_key[:3] + ('forged',) + sealed.owner_key[4:]),
            replace(sealed, expected_head=replace(sealed.expected_head, revision=1)),
            replace(sealed, expected_index_revision=True),
            replace(sealed, snapshot_version=99),
            replace(sealed, expected_count=999),
            replace(sealed, content_digest='not-a-digest')):
        forged = replace(context, rag=replace(context.rag, sealed=changed))
        assert not service._valid_descriptor(forged, forged.rag)


@pytest.mark.parametrize('delayed_kind', ('rag', 'episode'))
def test_reclaimed_pair_fences_late_old_upsert_for_each_scope(
        setup, monkeypatch, request, delayed_kind):
    rag, episode, rag_scope, episode_scope, rag_plan, episode_plan = plans(setup, request)
    db, raw = setup[2], setup[4]
    old_attempt = import_attempt(db, setup[6][0], 'old-worker')
    service = ImportVectorPairPublicationService(rag, episode)
    selected_scope = rag_scope if delayed_kind == 'rag' else episode_scope
    entered, release = threading.Event(), threading.Event()
    original_upsert = raw.client.upsert

    def delayed_upsert(*args, **kwargs):
        if (threading.current_thread().name.startswith('old-pair-upload')
                and kwargs['collection_name'] == selected_scope.identity.physical_collection):
            entered.set()
            assert release.wait(20), 'Delayed old upload was not released'
        return original_upsert(*args, **kwargs)

    monkeypatch.setattr(raw.client, 'upsert', delayed_upsert)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='old-pair-upload') as pool:
        old = pool.submit(service.publish_prepared_pair, rag_plan, episode_plan, old_attempt)
        try:
            assert entered.wait(20), 'Old pair did not reach the selected Qdrant write'
            with db.transaction() as cursor:
                rows = cursor.execute('''select generation_id,vector_kind,state from
                    vector_generations where task_id=%s order by vector_kind''',
                                      (old_attempt.task.task_id,)).fetchall()
            assert len(rows) == (1 if delayed_kind == 'rag' else 2)
            assert {row['vector_kind']: row['state'] for row in rows} == (
                {'rag': 'staging'} if delayed_kind == 'rag'
                else {'rag': 'sealed', 'episode': 'staging'})
            old_ids = {row['vector_kind']: row['generation_id'] for row in rows}
            for kind, scope in (('rag', rag_scope), ('episode', episode_scope)):
                if kind not in old_ids:
                    continue
                authority = rag.authority if kind == 'rag' else episode.authority
                owner_service = rag if kind == 'rag' else episode
                authority.abandon(scope, old_attempt, old_ids[kind])
                assert owner_service.cleanup_abandoned(scope, old_ids[kind]) >= 0
                assert authority.reconcile(scope, old_ids[kind])['state'] == 'abandoned'
            with db.transaction() as cursor:
                cursor.execute("""update import_tasks set
                    lease_expires_at=clock_timestamp()-interval '1 second' where id=%s""",
                               (old_attempt.task.task_id,))
            imports = PostgresImportLeaseRepository(db)
            assert imports.recover_expired() == 1
            new_attempt = imports.claim_next('new-worker')
            assert new_attempt is not None
            assert new_attempt.task.task_id == old_attempt.task.task_id
            assert new_attempt.lease_version == old_attempt.lease_version + 1
            new_rag = replace(rag_plan, expected_head=rag.authority.read_head(rag_scope),
                              complete_corpus=[VectorPoint('rag', [1., 0., 0., 0.],
                                                           {'marker': 'current-rag'})])
            new_episode = replace(
                episode_plan, expected_head=episode.authority.read_head(episode_scope),
                complete_corpus=[VectorPoint('episode', [0., 1., 0.],
                                             {'marker': 'current-episode'})])
            current = service.publish_prepared_pair(new_rag, new_episode, new_attempt)
            assert current.import_task.status == 'succeeded'
        finally:
            release.set()
        with pytest.raises((ImportPairRejected, ImportPairUnknown)) as captured:
            old.result(timeout=20)
    assert len(captured.value.generation_ids) == 2
    assert captured.value.generation_ids[0] != captured.value.generation_ids[1]
    assert current.rag.head == rag.authority.read_head(rag_scope)
    assert current.episode.head == episode.authority.read_head(episode_scope)
    for scope, owner_service, marker in (
            (rag_scope, rag, 'current-rag'),
            (episode_scope, episode, 'current-episode')):
        collection = scope.identity.physical_collection
        view = owner_service.read_view(scope)
        assert view.count(collection) == 1
        assert view.scroll(collection)[0].payload['marker'] == marker
        assert view.search(collection, [1., 0., 0., 0.] if scope.vector_kind == 'rag'
                           else [0., 1., 0.])[0].payload['marker'] == marker
    assert raw.count(selected_scope.identity.physical_collection,
                     {'_gv_generation': str(old_ids[delayed_kind])}) == 1


def test_old_empty_receipts_retire_for_new_empty_and_nonempty_heads(setup, request):
    rag, episode, rag_scope, episode_scope, rag_plan, episode_plan = plans(setup, request)
    service = ImportVectorPairPublicationService(rag, episode)
    first = service.publish_prepared_pair(
        replace(rag_plan, complete_corpus=[]),
        replace(episode_plan, complete_corpus=[]),
        import_attempt(setup[2], setup[6][0]))
    assert first.rag.head.state == first.episode.head.state == 'empty'
    assert first.rag.head.generation_id is first.episode.head.generation_id is None
    second = service.publish_prepared_pair(
        replace(rag_plan, expected_head=first.rag.head, snapshot_version=2),
        replace(episode_plan, expected_head=first.episode.head,
                complete_corpus=[], snapshot_version=2),
        import_attempt(setup[2], setup[6][0]))
    assert second.rag.head.state == 'published'
    assert second.episode.head.state == 'empty'
    assert second.episode.head.generation_id is None
    assert rag.authority.reconcile(rag_scope, first.rag.generation_id)['state'] == 'retired'
    assert episode.authority.reconcile(episode_scope, first.episode.generation_id)['state'] == 'retired'
    assert service.reconcile_pair(first._context) == first
    assert service.reconcile_pair(second._context) == second
