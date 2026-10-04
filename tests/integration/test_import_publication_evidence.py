"""Bounded evidence, permanent reservations and direct user gate on disposable PG."""
from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
import base64
import json
import random
import threading
import time
from uuid import uuid4, uuid5
import zlib

import pytest
import psycopg

from app.history import EMPTY_HISTORY
from app.import_publication_evidence import (
    CANONICAL_LIMIT, COMPRESSED_LIMIT, LOGICAL_ROW_LIMIT, AttemptKey,
    PostgresImportPublicationEvidenceRepository, PublicationEvidenceError,
    PublicationEvidenceUnknown, _validate_stored_slots,
    decode_intent, encode_intent,
)
from app.import_memory_publication import ImportMemoryPublicationService
from app.postgres_import_leases import ImportLeaseLost
from app.object_store import artifact_key
from app.postgres_memory_documents import PostgresMemoryDocumentStore
from app.postgres_snapshots import PostgresSnapshotRepository
from app.postgres_coordination import PostgresUserMutationCoordinator
from app.postgres_history_document_witnesses import PostgresHistoryDocumentWitnessRepository
from app.postgres_vector_generations import VectorScope
from hello_agents.memory.rag.embedding_profile import EmbeddingProfile
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.rag.prepare import PROJECT_POINT_NAMESPACE_UUID
from tests.integration.test_import_document_publication import _point, _task, publication
from tests.integration.test_import_memory_publication import memory_publication
from tests.integration.test_published_episode_reads import _publish as publish_episode_baseline
from tests.integration.test_postgres_auth_sessions import shared_database
from tests.integration.test_s3_object_store import store


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def _sample_intent():
    user, task_id, document_id = (str(uuid4()) for _ in range(3))
    task_token, user_token = str(uuid4()), str(uuid4())
    source_hash = sha256(b'synthetic source').hexdigest()
    bucket = 'disposable-evidence-test'
    source_key = artifact_key(user, 'imports', task_id, '.txt', source_hash)
    document_key = artifact_key(user, 'documents', document_id, '.txt', source_hash)
    path = f'object://{bucket}/{document_key}'
    timestamp = '2026-10-02T00:00:00.000000+00:00'
    event_metadata = {'user_id': user, 'import_task_id': task_id,
                      'document_id': document_id, 'document_name': 'synthetic.txt',
                      'document_path': path, 'file_suffix': '.txt',
                      'session_id': 'import'}
    item = {'id': 'import-' + str(uuid5(PROJECT_POINT_NAMESPACE_UUID,
                                      f'{user}:{task_id}')),
            'content': '用户导入了文档：synthetic.txt',
            'memory_type': 'episodic', 'importance': 0.8, 'timestamp': timestamp,
            'metadata': dict(event_metadata)}
    event_metadata.update({'memory_id': item['id'], 'episode_id': item['id'],
                           'timestamp': timestamp, 'memory_type': 'episodic',
                           'importance': 0.8, 'content': item['content']})
    history = deepcopy(EMPTY_HISTORY)
    record = {'user_id': user, 'document_id': document_id,
              'document_name': 'synthetic.txt', 'file_suffix': '.txt',
              'document_path': path, 'loaded_at': timestamp,
              'import_task_id': task_id}
    next_history = deepcopy(history)
    next_history['documents'].append(record)
    memory = {'user_id': user, 'memories': []}
    next_memory = {'user_id': user, 'memories': [item]}
    def scope(kind, namespace):
        identity = IndexIdentity('qdrant', 'test_' + uuid4().hex,
                                 EmbeddingProfile('simple', '', kind, 'v1', 4))
        index = identity.to_dict()
        index_key = sha256(_canonical(index)).hexdigest()
        old_id = str(uuid4())
        receipt = [old_id, user, kind, namespace, index_key, None, 1, 1, 1,
                   0, sha256(b'empty').hexdigest(), 'bootstrap', user_token, 1,
                   None, None, None, timestamp, timestamp, timestamp]
        return {'tenant_id': user, 'vector_kind': kind, 'namespace': namespace,
                'index_key': index_key, 'identity': index, 'index_revision': 1,
                'head': {'state': 'empty', 'revision': 1, 'generation_id': None,
                         'last_generation_id': old_id, 'index_revision': 1,
                         'snapshot_version': 1},
                'old_receipt': receipt, 'candidate_id': str(uuid4())}
    return {'schema_version': 1,
            'attempt': {'user_id': user, 'task_id': task_id, 'task_lease_version': 1,
                        'worker_id': 'worker', 'task_lease_token': task_token,
                        'user_lease_token': user_token, 'user_lease_version': 1,
                        'document_id': document_id},
            'task': {'task_id': task_id, 'batch_id': str(uuid4()), 'user_id': user,
                     'document_id': document_id, 'original_name': 'synthetic.txt',
                     'file_suffix': '.txt', 'size_bytes': len(b'synthetic source'),
                     'created_at': timestamp},
            'source': {'bucket': bucket, 'key': source_key, 'version_id': 'v1',
                       'sha256': source_hash, 'size_bytes': len(b'synthetic source')},
            'scopes': {'rag': scope('rag', f'pdf_{user}'),
                       'episode': scope('episode', 'episodes')},
            'snapshots': {'old_history': {'version': 1, 'data': history},
                          'next_history': {'version': 2, 'data': next_history},
                          'old_memory': {'version': 1, 'data': memory},
                          'next_memory': {'version': 2, 'data': next_memory}},
            'memory_rows': [],
            'event': {'event_id': item['id'], 'timestamp': timestamp,
                      'item': item, 'metadata': event_metadata},
            'document': {'key': document_key, 'record': record,
                         'count': 1,
                         'digest': sha256(_canonical(next_history['documents'])).hexdigest(),
                         'baseline_row_digest': sha256(_canonical([])).hexdigest(),
                         'fixed_refs': [], 'old_witness': None}}


def _complete_slots(intent, intent_hash):
    attempt, source, document = intent['attempt'], intent['source'], intent['document']
    result = {'user_id': attempt['user_id'], 'document_id': attempt['document_id'],
              'bucket': source['bucket'], 'key': document['key'],
              'version_id': 'verified-disposable-version', 'sha256': source['sha256'],
              'size_bytes': source['size_bytes'],
              'record_hash': sha256(_canonical(document['record'])).hexdigest()}
    final_hash = sha256(b'1:' + intent_hash.encode('ascii') + b':' +
                        _canonical(result)).hexdigest()
    result['final_expected_hash'] = final_hash
    sealed = {}
    receipts = {}
    for kind in ('rag', 'episode'):
        scope = intent['scopes'][kind]
        next_name = 'next_history' if kind == 'rag' else 'next_memory'
        sealed[kind] = {
            'generation_id': scope['candidate_id'], 'vector_kind': kind,
            'user_id': attempt['user_id'], 'task_id': attempt['task_id'],
            'task_lease_token': attempt['task_lease_token'],
            'task_lease_version': attempt['task_lease_version'],
            'worker_id': attempt['worker_id'],
            'user_lease_token': attempt['user_lease_token'],
            'user_lease_version': attempt['user_lease_version'],
            'namespace': scope['namespace'], 'index_key': scope['index_key'],
            'base_revision': scope['head']['revision'],
            'index_revision': scope['index_revision'],
            'snapshot_version': intent['snapshots'][next_name]['version'],
            'expected_count': 1, 'content_digest': sha256(kind.encode()).hexdigest(),
            'final_expected_hash': final_hash}
        receipt = list(scope['old_receipt'])
        receipt[0] = scope['candidate_id']
        receipt[5] = scope['head']['revision']
        receipt[7] = scope['head']['revision'] + 1
        receipt[8] = intent['snapshots'][next_name]['version']
        receipt[9] = sealed[kind]['expected_count']
        receipt[10] = sealed[kind]['content_digest']
        receipt[11:17] = [attempt['worker_id'], attempt['user_lease_token'],
                          attempt['user_lease_version'], attempt['task_id'],
                          attempt['task_lease_token'], attempt['task_lease_version']]
        receipts[kind + '_receipt'] = receipt
    return (_canonical(result), _canonical(sealed['rag']),
            _canonical(sealed['episode']),
            _canonical({'final_expected_hash': final_hash, **receipts}))


def _advance_private_evidence(cursor, selector, *, phase=None, slot=None,
                              value=None, reason=None):
    """Test-only legal child-first, header-second CAS for raw fixture setup."""
    row = cursor.execute('''select phase,phase_version from import_publication_evidence
        where user_id=%s and task_id=%s and task_lease_version=%s for update''',
        selector).fetchone()
    assert row is not None
    if slot is None:
        child = cursor.execute('''update import_publication_private_payloads
            set payload_phase_version=payload_phase_version+1
            where user_id=%s and task_id=%s and task_lease_version=%s
            and payload_phase_version=%s returning payload_phase_version''',
            (*selector,row['phase_version'])).fetchone()
    else:
        assert slot in ('document_slot','rag_sealed_slot','episode_sealed_slot','terminal_slot')
        child = cursor.execute(f'''update import_publication_private_payloads
            set {slot}=%s,payload_phase_version=payload_phase_version+1
            where user_id=%s and task_id=%s and task_lease_version=%s
            and payload_phase_version=%s and {slot} is null
            returning payload_phase_version''',
            (value,*selector,row['phase_version'])).fetchone()
    assert child is not None
    new_phase = row['phase'] if phase is None else phase
    header = cursor.execute('''update import_publication_evidence
        set phase=%s,phase_version=phase_version+1,observation_reason=%s
        where user_id=%s and task_id=%s and task_lease_version=%s
        and phase_version=%s returning phase_version''',
        (new_phase,reason,*selector,row['phase_version'])).fetchone()
    assert header is not None
    return header


def test_exact_evidence_decodes_inside_callers_read_only_snapshot(memory_publication):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    plan = service._plan_intent(rag_scope, attempt,
        [_point(rag_scope, attempt.task.document_id, 'new')],
        event_vector=[1., 0., 0., 0.], event_profile=profile)
    key = PostgresImportPublicationEvidenceRepository(db).reserve_intent(attempt, plan.intent)
    repository = PostgresImportPublicationEvidenceRepository(db)
    with db.transaction() as cursor:
        cursor.execute('set transaction isolation level repeatable read read only')
        frozen = repository._read_exact_in_cursor(cursor, key)
        assert frozen.key == key
        assert frozen.intent == plan.intent
        assert frozen.phase == 'intent'


def test_codec_round_trip_and_bounds_for_complete_typed_intent():
    intent = _sample_intent()
    encoded = encode_intent(intent)
    assert decode_intent(encoded.payload, canonical_bytes=encoded.canonical_bytes,
                         digest=encoded.digest) == intent
    assert 0 < encoded.canonical_bytes < CANONICAL_LIMIT
    assert len(encoded.payload) < COMPRESSED_LIMIT
    assert len(encoded.payload) + 4 * 262144 < LOGICAL_ROW_LIMIT


@pytest.mark.parametrize('change', [
    lambda i: i['attempt'].__setitem__('task_lease_version', True),
    lambda i: i['attempt'].__setitem__('task_lease_token', 'NOT-UUID'),
    lambda i: i['event'].__setitem__('timestamp', 'naive'),
    lambda i: i['scopes']['rag']['identity']['profile'].__setitem__('dimension', True),
    lambda i: i['scopes']['rag'].__setitem__('candidate_id', i['scopes']['episode']['candidate_id']),
    lambda i: i['event']['item'].__setitem__('importance', float('nan')),
])
def test_codec_rejects_invalid_typed_identity(change):
    intent = _sample_intent()
    change(intent)
    with pytest.raises(PublicationEvidenceError):
        encode_intent(intent)


def test_codec_rejects_hash_length_duplicate_trailing_truncated_and_bomb():
    encoded = encode_intent(_sample_intent())
    with pytest.raises(PublicationEvidenceError):
        decode_intent(encoded.payload, canonical_bytes=encoded.canonical_bytes + 1,
                      digest=encoded.digest)
    with pytest.raises(PublicationEvidenceError):
        decode_intent(encoded.payload, canonical_bytes=encoded.canonical_bytes,
                      digest='0' * 64)
    for payload in (encoded.payload + b'extra', encoded.payload[:-2],
                    zlib.compress(b'A' * (encoded.canonical_bytes + 1))):
        with pytest.raises(PublicationEvidenceError):
            decode_intent(payload, canonical_bytes=encoded.canonical_bytes,
                          digest=encoded.digest)
    raw = _canonical(_sample_intent())
    duplicate = raw.replace(b'"schema_version":1',
                            b'"schema_version":1,"schema_version":1', 1)
    assert duplicate != raw
    with pytest.raises(PublicationEvidenceError):
        decode_intent(zlib.compress(duplicate), canonical_bytes=len(duplicate),
                      digest=sha256(duplicate).hexdigest())


def test_codec_rejects_profile_cross_validation_and_oversize_declaration():
    intent = _sample_intent()
    intent['scopes']['rag']['identity']['profile']['model'] = 'changed-model'
    with pytest.raises(PublicationEvidenceError):
        encode_intent(intent)
    encoded = encode_intent(_sample_intent())
    with pytest.raises(PublicationEvidenceError):
        decode_intent(encoded.payload, canonical_bytes=CANONICAL_LIMIT + 1,
                      digest=encoded.digest)


@pytest.mark.parametrize('change', [
    lambda i: i['snapshots']['next_history']['data'].__setitem__('extra', True),
    lambda i: i['snapshots']['next_memory']['data'].__setitem__('extra', True),
    lambda i: i['event']['item'].__setitem__('memory_type', 'semantic'),
    lambda i: i['event']['item']['metadata'].__setitem__('source', 'other'),
    lambda i: i['event']['metadata'].__setitem__('content', 'other'),
    lambda i: i['event']['metadata'].__setitem__('document_id', str(uuid4())),
])
def test_codec_refuses_changed_snapshot_or_event_proof(change):
    intent = _sample_intent()
    change(intent)
    with pytest.raises(PublicationEvidenceError):
        encode_intent(intent)


@pytest.mark.parametrize('field,value', [
    ('document_name', 'forged.txt'),
    ('loaded_at', '2025-01-01T00:00:00+00:00'),
    ('unplanned', True),
])
def test_codec_refuses_coherently_changed_document_record(field, value):
    intent = _sample_intent()
    intent['document']['record'][field] = value
    documents = intent['snapshots']['next_history']['data']['documents']
    documents[-1][field] = value
    intent['document']['digest'] = sha256(_canonical(documents)).hexdigest()
    with pytest.raises(PublicationEvidenceError):
        encode_intent(intent)


def test_codec_refuses_consistent_but_unplanned_event_identity():
    intent = _sample_intent()
    forged = 'import-' + str(uuid4())
    intent['event']['event_id'] = forged
    intent['event']['item']['id'] = forged
    intent['event']['metadata']['memory_id'] = forged
    intent['event']['metadata']['episode_id'] = forged
    intent['snapshots']['next_memory']['data']['memories'][-1]['id'] = forged
    with pytest.raises(PublicationEvidenceError):
        encode_intent(intent)


def test_codec_refuses_complete_oversize_canonical_and_incompressible_intents():
    intent = _sample_intent()
    large = {'id': 'prior', 'content': 'x' * (33 * 1024 * 1024),
             'metadata': {'user_id': intent['attempt']['user_id']}}
    intent['snapshots']['old_memory']['data']['memories'].append(large)
    intent['snapshots']['next_memory']['data']['memories'].insert(0, deepcopy(large))
    with pytest.raises(PublicationEvidenceError, match='Canonical intent exceeds bound'):
        encode_intent(intent)
    intent = _sample_intent()
    text = base64.b64encode(random.Random(20261002).randbytes(5 * 1024 * 1024)).decode('ascii')
    prior = {'id': 'prior', 'content': text,
             'metadata': {'user_id': intent['attempt']['user_id']}}
    intent['snapshots']['old_memory']['data']['memories'].append(prior)
    intent['snapshots']['next_memory']['data']['memories'].insert(0, deepcopy(prior))
    with pytest.raises(PublicationEvidenceError, match='Compressed intent exceeds bound'):
        encode_intent(intent)


def test_stored_slot_numeric_types_reject_bool_equal_to_one():
    intent = _sample_intent()
    intent['source']['size_bytes'] = 1
    intent['task']['size_bytes'] = 1
    encoded = encode_intent(intent)
    slots = list(_complete_slots(intent, encoded.digest))
    document = json.loads(slots[0])
    document['size_bytes'] = True
    unsigned = {key: value for key, value in document.items()
                if key != 'final_expected_hash'}
    document['final_expected_hash'] = sha256(b'1:' + encoded.digest.encode('ascii')
        + b':' + _canonical(unsigned)).hexdigest()
    slots[0] = _canonical(document)
    with pytest.raises(PublicationEvidenceError):
        _validate_stored_slots(intent, encoded.digest, 'document_verified', 2,
                               None, tuple(slots[:1] + [None, None, None]))
    slots = list(_complete_slots(intent, encoded.digest))
    sealed = json.loads(slots[1])
    sealed['base_revision'] = True
    slots[1] = _canonical(sealed)
    with pytest.raises(PublicationEvidenceError):
        _validate_stored_slots(intent, encoded.digest, 'document_verified', 3,
                               None, (slots[0], slots[1], None, None))


def _live_plan(memory_publication):
    service, db, store, _, _, rag_scope, _, profile, user, other = memory_publication
    attempt = _task(db, store, user)
    plan = service._plan_intent(rag_scope, attempt,
        [_point(rag_scope, attempt.task.document_id, 'new')],
        event_vector=[1., 0., 0., 0.], event_profile=profile)
    return service, db, store, rag_scope, user, other, attempt, plan


def test_ordinary_witness_lock_serializes_with_intent_reservation(
        memory_publication, monkeypatch):
    service, db, store, scope, user, other, attempt, plan = _live_plan(memory_publication)
    pairing = plan.document.old_pairing
    assert not pairing.has_witness
    acquired = threading.Event()
    reservation_attempted = threading.Event()
    release = threading.Event()
    original_lock = PostgresUserMutationCoordinator._lock_user

    def held_lock(cursor, user_id, *, skip=False):
        if (threading.current_thread().name.startswith('ordinary-witness')
                and acquired.is_set()):
            reservation_attempted.set()
        result = original_lock(cursor, user_id, skip=skip)
        if (threading.current_thread().name.startswith('ordinary-witness')
                and not acquired.is_set()):
            acquired.set()
            assert release.wait(30)
        return result

    monkeypatch.setattr(PostgresUserMutationCoordinator, '_lock_user',
                        staticmethod(held_lock))
    def insert_witness():
        with db.transaction() as cursor:
            cursor.execute('begin')
            PostgresHistoryDocumentWitnessRepository.insert(
                cursor, scope, pairing, plan.document.old_evidence)

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix='ordinary-witness') as pool:
        witness = pool.submit(insert_witness)
        assert acquired.wait(30)
        reserve = pool.submit(PostgresImportPublicationEvidenceRepository(db).reserve_intent,
                              attempt, plan.intent)
        try:
            assert reservation_attempted.wait(30)
            assert not reserve.done()
        finally:
            release.set()
        witness.result(timeout=60)
        with pytest.raises(Exception):
            reserve.result(timeout=60)
    with db.transaction() as cursor:
        assert cursor.execute('select count(*) as n from user_publication_gates').fetchone()['n'] == 0


def test_read_only_plan_then_reservation_is_separate_and_gates_direct_writers(memory_publication,
                                                                               monkeypatch):
    service, db, store, rag_scope, user, other, attempt, plan = _live_plan(memory_publication)
    repository = PostgresImportPublicationEvidenceRepository(db)
    with db.transaction() as cursor:
        assert cursor.execute('select count(*) as n from import_publication_evidence').fetchone()['n'] == 0
        assert cursor.execute('select count(*) as n from user_publication_gates').fetchone()['n'] == 0
        schema = cursor.execute('select current_schema() as s').fetchone()['s']
    assert plan.document.record['document_path'] == (
        f"object://{store.bucket}/{plan.document.object_key}")
    assert plan.intent['snapshots']['next_history']['data']['documents'][-1] == plan.document.record
    assert plan.intent['event']['metadata']['document_path'] == plan.document.record['document_path']
    assert plan.intent['scopes']['rag']['candidate_id'] != plan.intent['scopes']['episode']['candidate_id']
    key = repository.reserve_intent(attempt, plan.intent)
    assert key == AttemptKey(user, attempt.task.task_id, attempt.lease_version)
    saved = repository.read_exact(user, attempt.task.task_id, attempt.lease_version)
    assert saved is not None and saved.intent == plan.intent and saved.phase == 'intent'
    saved.intent['event']['item']['content'] = 'caller edit'
    assert repository.read_exact(user, attempt.task.task_id, attempt.lease_version).intent == plan.intent
    called = False
    def mutation(_data):
        nonlocal called
        called = True
    with pytest.raises(PublicationEvidenceError):
        PostgresSnapshotRepository(db).update(user, 'history', mutation)
    assert not called
    with pytest.raises(PublicationEvidenceError):
        PostgresSnapshotRepository(db).compare_and_swap(
            user, 'history', deepcopy(plan.document.next_history),
            expected_version=plan.document.history.version)
    with pytest.raises(PublicationEvidenceError):
        PostgresMemoryDocumentStore(db, user).add_document('x', 'x',
            json.dumps({'user_id': user}))
    with pytest.raises(PublicationEvidenceError):
        PostgresMemoryDocumentStore(db, user).delete_document('x')
    def unexpected_put(*_args, **_kwargs):
        raise AssertionError('An unresolved gate reached S3 publication')
    monkeypatch.setattr(store, 'put_immutable', unexpected_put)
    new_points = [_point(rag_scope, attempt.task.document_id, 'new')]
    with pytest.raises(PublicationEvidenceError):
        service.documents.publish(rag_scope, attempt, new_points)
    with pytest.raises(PublicationEvidenceError):
        service.publish(rag_scope, attempt, new_points,
            event_vector=[1., 0., 0., 0.],
            event_profile=service.episode_scope.identity.profile)
    assert PostgresSnapshotRepository(db).update(other, 'history',
        lambda data: None).version >= 1
    with db.transaction() as cursor:
        assert cursor.execute('select count(*) as n from generation_reservations').fetchone()['n'] == 2
    # Test report records only disposable identifiers.
    pytest.report_header = schema


def test_gate_committed_during_witness_read_refuses_document_put(memory_publication,
                                                                 monkeypatch):
    service, db, store, _, user, _, attempt, plan = _live_plan(memory_publication)
    repository = PostgresImportPublicationEvidenceRepository(db)
    original = service.documents.witnesses.read_current
    def reserve_while_reading(scope, *, cursor=None):
        result = original(scope, cursor=cursor)
        if cursor is None:
            repository.reserve_intent(attempt, plan.intent)
        return result
    monkeypatch.setattr(service.documents.witnesses, 'read_current',
                        reserve_while_reading)
    puts = []
    monkeypatch.setattr(store, 'put_immutable', lambda *_args, **_kwargs: puts.append(1))
    with pytest.raises(PublicationEvidenceError):
        service.documents._write_planned_document(plan.document, attempt)
    assert puts == []
    assert repository.read_exact(user, attempt.task.task_id, attempt.lease_version)


def test_oversize_planner_refuses_before_document_put(memory_publication, monkeypatch):
    service, db, store, _, _, rag_scope, _, profile, user, _ = memory_publication
    attempt = _task(db, store, user)
    def unexpected_put(*_args, **_kwargs):
        raise AssertionError('Oversize plan reached S3 publication')
    monkeypatch.setattr(store, 'put_immutable', unexpected_put)
    monkeypatch.setattr('app.import_publication_evidence.CANONICAL_LIMIT', 1)
    with pytest.raises(PublicationEvidenceError, match='Canonical intent exceeds bound'):
        service._plan_intent(rag_scope, attempt,
            [_point(rag_scope, attempt.task.document_id, 'new')],
            event_vector=[1., 0., 0., 0.], event_profile=profile)
    with db.transaction() as cursor:
        for table in ('import_publication_evidence', 'user_publication_gates',
                      'generation_reservations'):
            assert cursor.execute(f'select count(*) as n from {table}').fetchone()['n'] == 0


def test_drift_refuses_reservation_without_partial_rows(memory_publication):
    _, db, _, _, user, _, attempt, plan = _live_plan(memory_publication)
    repository = PostgresImportPublicationEvidenceRepository(db)
    with db.transaction() as cursor:
        cursor.execute('''update user_snapshots set version=version+1
            where user_id=%s and kind='memory' ''', (user,))
    with pytest.raises(PublicationEvidenceError):
        repository.reserve_intent(attempt, plan.intent)
    with db.transaction() as cursor:
        for table in ('import_publication_evidence', 'user_publication_gates',
                      'generation_reservations'):
            assert cursor.execute(f'select count(*) as n from {table}').fetchone()['n'] == 0


def test_ambiguous_reservation_commit_is_unknown_without_external_write(memory_publication,
                                                                          monkeypatch):
    _, db, store, _, user, _, attempt, plan = _live_plan(memory_publication)
    repository = PostgresImportPublicationEvidenceRepository(db)
    original = db.transaction
    @contextmanager
    def committed_but_response_lost():
        with original() as cursor:
            yield cursor
        raise psycopg.OperationalError('synthetic commit response loss')
    def unexpected_put(*_args, **_kwargs):
        raise AssertionError('Unknown intent must not reach S3')
    monkeypatch.setattr(store, 'put_immutable', unexpected_put)
    monkeypatch.setattr(db, 'transaction', committed_but_response_lost)
    with pytest.raises(PublicationEvidenceUnknown):
        repository.reserve_intent(attempt, plan.intent)
    monkeypatch.setattr(db, 'transaction', original)
    saved = repository.read_exact(user, attempt.task.task_id, attempt.lease_version)
    assert saved is not None and saved.intent == plan.intent
    with db.transaction() as cursor:
        assert cursor.execute('''select count(*) as n from user_publication_gates
            where user_id=%s and status='unresolved' ''', (user,)).fetchone()['n'] == 1
        assert cursor.execute('''select count(*) as n from generation_reservations
            where user_id=%s''', (user,)).fetchone()['n'] == 2


def test_gate_survives_lease_expiry_and_two_connection_wait(memory_publication,
                                                            monkeypatch):
    _, db, _, _, user, other, attempt, plan = _live_plan(memory_publication)
    repository = PostgresImportPublicationEvidenceRepository(db)
    repository.reserve_intent(attempt, plan.intent)
    entered = threading.Event()
    release = threading.Event()
    attempting_lock = threading.Event()
    backend_pid = []
    outcome = []
    original_lock = PostgresSnapshotRepository._lock_user
    def observed_lock(cursor, owner):
        backend_pid.append(cursor.execute('select pg_backend_pid() as pid').fetchone()['pid'])
        attempting_lock.set()
        return original_lock(cursor, owner)
    monkeypatch.setattr(PostgresSnapshotRepository, '_lock_user',
                        staticmethod(observed_lock))
    def hold_user():
        with db.transaction() as cursor:
            cursor.execute('select id from users where id=%s for update', (user,))
            entered.set()
            assert release.wait(30)
    thread = threading.Thread(target=hold_user)
    thread.start()
    assert entered.wait(10)
    def mutate():
        try:
            PostgresSnapshotRepository(db).update(user, 'history', lambda data: None)
        except Exception as error:
            outcome.append(error)
    waiting = threading.Thread(target=mutate)
    waiting.start()
    assert attempting_lock.wait(10)
    observed_wait = False
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with db.transaction() as cursor:
            state = cursor.execute('''select wait_event_type from pg_stat_activity
                where pid=%s''', (backend_pid[0],)).fetchone()
        if state and state['wait_event_type'] == 'Lock':
            observed_wait = True
            break
        time.sleep(0.05)
    assert observed_wait and waiting.is_alive()
    assert PostgresSnapshotRepository(db).update(other, 'history',
        lambda data: None).version >= 1
    assert waiting.is_alive()
    with db.transaction() as cursor:
        cursor.execute('''update import_tasks set lease_expires_at=clock_timestamp()
            where id=%s''', (attempt.task.task_id,))
    release.set()
    thread.join(10)
    waiting.join(10)
    assert not thread.is_alive() and not waiting.is_alive()
    assert len(outcome) == 1 and isinstance(outcome[0], PublicationEvidenceError)
    with db.transaction() as cursor:
        gate = cursor.execute('''select status from user_publication_gates
            where user_id=%s''', (user,)).fetchone()
    assert gate['status'] == 'unresolved'


def test_reservation_rechecks_lease_after_unique_index_wait(memory_publication):
    _, db, store, _, user, other, attempt, plan = _live_plan(memory_publication)
    blocker_attempt = _task(db, store, other)
    repository = PostgresImportPublicationEvidenceRepository(db)
    encoded = encode_intent(plan.intent)
    candidate = plan.intent['scopes']['rag']['candidate_id']
    held = threading.Event()
    release = threading.Event()
    errors = []
    def hold_uncommitted_candidate():
        try:
            with db.transaction() as cursor:
                # This synthetic foreign-tenant row exists only to hold the UUID
                # unique-index insertion; the whole transaction rolls back.
                cursor.execute('''insert into import_publication_evidence
                    (user_id,task_id,task_lease_version,document_id,worker_id,
                     task_lease_token,user_lease_token,user_lease_version,
                         schema_version,intent_format,intent_hash)
                        values(%s,%s,%s,%s,%s,%s,%s,%s,1,'canonical-json-zlib-1',%s)''',
                    (other, blocker_attempt.task.task_id, blocker_attempt.lease_version,
                     blocker_attempt.task.document_id, blocker_attempt.worker_id,
                     blocker_attempt.lease_token, blocker_attempt.user_lease.lease_token,
                         blocker_attempt.user_lease.lease_version, encoded.digest))
                cursor.execute('''insert into generation_reservations
                    (generation_id,user_id,task_id,task_lease_version,vector_kind,
                     namespace,index_key,base_revision,owner,user_lease_token,
                     user_lease_version) values(%s,%s,%s,%s,'rag',%s,%s,%s,%s,%s,%s)''',
                    (candidate, other, blocker_attempt.task.task_id,
                     blocker_attempt.lease_version,
                     plan.intent['scopes']['rag']['namespace'],
                     plan.intent['scopes']['rag']['index_key'],
                     plan.intent['scopes']['rag']['head']['revision'],
                     blocker_attempt.worker_id, blocker_attempt.user_lease.lease_token,
                     blocker_attempt.user_lease.lease_version))
                held.set()
                assert release.wait(20)
                raise RuntimeError('roll back synthetic unique-index blocker')
        except RuntimeError as error:
            assert 'synthetic unique-index blocker' in str(error)
        except BaseException as error:
            errors.append(error)

    blocker = threading.Thread(target=hold_uncommitted_candidate)
    blocker.start()
    try:
        assert held.wait(10), errors
        with db.transaction() as cursor:
            cursor.execute('''update import_tasks set lease_expires_at=
                clock_timestamp()+interval '4 seconds' where id=%s''',
                (attempt.task.task_id,))
            cursor.execute('''update user_mutation_leases set lease_expires_at=
                clock_timestamp()+interval '4 seconds' where user_id=%s''', (user,))
        result = []
        def reserve():
            try:
                result.append(repository.reserve_intent(attempt, plan.intent))
            except BaseException as error:
                result.append(error)
        waiter = threading.Thread(target=reserve)
        waiter.start()
        deadline = time.monotonic() + 5
        observed_wait = False
        while time.monotonic() < deadline:
            with db.transaction() as cursor:
                rows = cursor.execute('''select wait_event_type,query
                    from pg_stat_activity where wait_event_type='Lock' ''').fetchall()
            if any('generation_reservations' in row['query'] for row in rows):
                observed_wait = True
                break
            time.sleep(0.05)
        assert observed_wait and waiter.is_alive(), result
        time.sleep(4.1)
        release.set()
        waiter.join(15)
        assert not waiter.is_alive()
        assert len(result) == 1 and isinstance(result[0], ImportLeaseLost)
        with db.transaction() as cursor:
            for table in ('import_publication_evidence', 'user_publication_gates',
                          'generation_reservations'):
                assert cursor.execute(f'''select count(*) as n from {table}
                    where user_id=%s''', (user,)).fetchone()['n'] == 0
    finally:
        release.set()
        blocker.join(15)
        assert not blocker.is_alive() and not errors


def test_source_pin_and_candidate_reuse_refuse_without_partial_rows(memory_publication):
    _, db, _, _, user, _, attempt, plan = _live_plan(memory_publication)
    repository = PostgresImportPublicationEvidenceRepository(db)
    wrong_source = replace(attempt, bucket='another-disposable-bucket')
    with pytest.raises(PublicationEvidenceError):
        repository.reserve_intent(wrong_source, plan.intent)
    with db.transaction() as cursor:
        cursor.execute('''update import_objects set version_id=%s
            where task_id=%s and user_id=%s''', ('changed-version', attempt.task.task_id, user))
    with pytest.raises(PublicationEvidenceError):
        repository.reserve_intent(attempt, plan.intent)
    with db.transaction() as cursor:
        for table in ('import_publication_evidence', 'user_publication_gates',
                      'generation_reservations'):
            assert cursor.execute(f'select count(*) as n from {table}').fetchone()['n'] == 0


def test_read_exact_fails_closed_on_phase_slot_shape_and_sql_immutability(memory_publication):
    _, db, _, _, user, other, attempt, plan = _live_plan(memory_publication)
    repository = PostgresImportPublicationEvidenceRepository(db)
    key = repository.reserve_intent(attempt, plan.intent)
    assert repository.read_exact(other, key.task_id, key.task_lease_version) is None
    assert repository.read_exact(user, key.task_id, key.task_lease_version + 1) is None
    selector = (key.user_id, key.task_id, key.task_lease_version)
    with pytest.raises(psycopg.errors.RaiseException, match='intent must start empty'):
        with db.transaction() as cursor:
            cursor.execute('''insert into import_publication_evidence
                (user_id,task_id,task_lease_version,document_id,worker_id,
                 task_lease_token,user_lease_token,user_lease_version,
                 schema_version,intent_format,intent_hash,phase)
                select user_id,task_id,task_lease_version+1,document_id,worker_id,
                 task_lease_token,user_lease_token,user_lease_version,
                 schema_version,intent_format,intent_hash,'abandoned'
                 from import_publication_evidence
                where user_id=%s and task_id=%s and task_lease_version=%s''', selector)
    with pytest.raises(psycopg.errors.RaiseException, match='intent is immutable'):
        with db.transaction() as cursor:
            cursor.execute('''update import_publication_evidence set intent_hash=%s,
                phase_version=phase_version+1 where user_id=%s and task_id=%s
                and task_lease_version=%s''', ('0' * 64, *selector))
    with pytest.raises(psycopg.errors.RaiseException, match='Illegal import publication phase'):
        with db.transaction() as cursor:
            cursor.execute('''update import_publication_evidence set phase='pair_sealed',
                phase_version=phase_version+1 where user_id=%s and task_id=%s
                and task_lease_version=%s''', selector)
    with db.transaction() as cursor:
        _advance_private_evidence(cursor,selector,phase='document_verified',
                                  slot='document_slot',value=b'{}')
    with pytest.raises(PublicationEvidenceError):
        repository.read_exact(*selector)
    with pytest.raises(psycopg.errors.RaiseException, match='slots are write once'):
        with db.transaction() as cursor:
            cursor.execute('''update import_publication_private_payloads
                set document_slot=%s,payload_phase_version=payload_phase_version+1
                where user_id=%s and task_id=%s and task_lease_version=%s''',
                (b'{"changed":true}', *selector))


def test_read_exact_rejects_stored_hash_corruption(memory_publication):
    _, db, _, _, user, _, attempt, plan = _live_plan(memory_publication)
    repository = PostgresImportPublicationEvidenceRepository(db)
    key = repository.reserve_intent(attempt, plan.intent)
    with db.transaction() as cursor:
        cursor.execute('''alter table import_publication_evidence
            disable trigger import_publication_evidence_guard''')
        cursor.execute('''update import_publication_evidence set intent_hash=%s
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            ('0' * 64, key.user_id, key.task_id, key.task_lease_version))
        cursor.execute('set constraints all immediate')
        cursor.execute('''alter table import_publication_evidence
            enable trigger import_publication_evidence_guard''')
    with pytest.raises(PublicationEvidenceError, match='Intent hash differs'):
        repository.read_exact(key.user_id, key.task_id, key.task_lease_version)


def test_sql_reservation_is_permanent_after_revoke(memory_publication):
    _, db, _, _, user, _, attempt, plan = _live_plan(memory_publication)
    repository = PostgresImportPublicationEvidenceRepository(db)
    key = repository.reserve_intent(attempt, plan.intent)
    candidate = plan.intent['scopes']['rag']['candidate_id']
    with db.transaction() as cursor:
        cursor.execute('''update generation_reservations
            set state='revoked',revoked_at=clock_timestamp()
            where generation_id=%s''', (candidate,))
        cursor.execute('''update user_publication_gates
            set status='resolved',resolved_at=clock_timestamp()
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            (key.user_id, key.task_id, key.task_lease_version))
    with pytest.raises(psycopg.errors.RaiseException, match='permanent'):
        with db.transaction() as cursor:
            cursor.execute('delete from generation_reservations where generation_id=%s',
                           (candidate,))
    with pytest.raises(psycopg.errors.RaiseException, match='only revoke once'):
        with db.transaction() as cursor:
            cursor.execute('''update generation_reservations set state='reserved',
                revoked_at=null where generation_id=%s''', (candidate,))
    with pytest.raises(psycopg.errors.UniqueViolation):
        with db.transaction() as cursor:
            cursor.execute('''insert into generation_reservations
                (generation_id,user_id,task_id,task_lease_version,vector_kind,
                 namespace,index_key,base_revision,owner,user_lease_token,user_lease_version)
                select generation_id,user_id,task_id,task_lease_version,vector_kind,
                 namespace,index_key,base_revision,owner,user_lease_token,user_lease_version
                from generation_reservations where generation_id=%s''', (candidate,))
    with pytest.raises(psycopg.errors.RaiseException, match='must start unresolved'):
        with db.transaction() as cursor:
            cursor.execute('''insert into user_publication_gates
                (user_id,task_id,task_lease_version,status,resolved_at)
                values(%s,%s,%s,'resolved',clock_timestamp())''',
                (key.user_id, key.task_id, key.task_lease_version))
    with pytest.raises(psycopg.errors.RaiseException, match='must start reserved'):
        with db.transaction() as cursor:
            cursor.execute('''insert into generation_reservations
                (generation_id,user_id,task_id,task_lease_version,vector_kind,
                 namespace,index_key,base_revision,owner,user_lease_token,
                 user_lease_version,state,revoked_at)
                select generation_id,user_id,task_id,task_lease_version,vector_kind,
                 namespace,index_key,base_revision,owner,user_lease_token,
                 user_lease_version,'revoked',clock_timestamp()
                from generation_reservations where generation_id=%s''', (candidate,))
    assert repository.read_exact(*tuple(key.__dict__.values())).phase == 'intent'


def test_revoked_candidate_cannot_be_reused_by_other_tenant(memory_publication):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, other = memory_publication
    attempt = _task(db, store, user)
    plan = service._plan_intent(rag_scope, attempt,
        [_point(rag_scope, attempt.task.document_id, 'first')],
        event_vector=[1., 0., 0., 0.], event_profile=profile)
    repository = PostgresImportPublicationEvidenceRepository(db)
    first = repository.reserve_intent(attempt, plan.intent)
    candidate = plan.intent['scopes']['rag']['candidate_id']
    with db.transaction() as cursor:
        cursor.execute('''update generation_reservations
            set state='revoked',revoked_at=clock_timestamp()
            where generation_id=%s''', (candidate,))
        cursor.execute('''update user_publication_gates
            set status='resolved',resolved_at=clock_timestamp()
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            (first.user_id, first.task_id, first.task_lease_version))

    other_rag = VectorScope(other, 'rag', f'pdf_{other}', rag_scope.identity)
    coordinator = PostgresUserMutationCoordinator(db)
    lease = coordinator.acquire(other, 'bootstrap-other', lease_seconds=120)
    assert lease is not None
    try:
        rag.publish_complete(other_rag, lease, rag.authority.read_head(other_rag), [],
            domain_publish=lambda cursor: snapshots.compare_and_swap_in_transaction(
                cursor, other, 'history', deepcopy(EMPTY_HISTORY), expected_version=0),
            snapshot_version=1)
    finally:
        coordinator.release(lease)
    other_episode = VectorScope(other, 'episode', 'episodes', episode_scope.identity)
    publish_episode_baseline(db, service.pair.episode, other_episode, [])
    other_service = ImportMemoryPublicationService(db, store, rag, service.pair.episode,
                                                    trusted_episode_scope=other_episode)
    other_attempt = _task(db, store, other)
    other_plan = other_service._plan_intent(other_rag, other_attempt,
        [_point(other_rag, other_attempt.task.document_id, 'second')],
        event_vector=[1., 0., 0., 0.], event_profile=profile)
    other_plan.intent['scopes']['rag']['candidate_id'] = candidate
    with pytest.raises(PublicationEvidenceError, match='already reserved'):
        repository.reserve_intent(other_attempt, other_plan.intent)
    with db.transaction() as cursor:
        for table in ('import_publication_evidence', 'user_publication_gates',
                      'generation_reservations'):
            assert cursor.execute(f'''select count(*) as n from {table}
                where user_id=%s''', (other,)).fetchone()['n'] == 0
        retained = cursor.execute('''select state from generation_reservations
            where generation_id=%s''', (candidate,)).fetchone()
    assert retained['state'] == 'revoked'


def test_read_exact_accepts_only_typed_ordered_complete_future_slots(memory_publication):
    _, db, _, _, _, _, attempt, plan = _live_plan(memory_publication)
    repository = PostgresImportPublicationEvidenceRepository(db)
    key = repository.reserve_intent(attempt, plan.intent)
    selector = (key.user_id, key.task_id, key.task_lease_version)
    saved = repository.read_exact(*selector)
    document, rag, episode, terminal = _complete_slots(saved.intent, saved.intent_hash)
    assert sum(map(len, (document, rag, episode, terminal))) < 4 * 262144
    with db.transaction() as cursor:
        _advance_private_evidence(cursor,selector,phase='document_verified',
                                  slot='document_slot',value=document)
        _advance_private_evidence(cursor,selector,slot='rag_sealed_slot',value=rag)
    assert repository.read_exact(*selector).rag_sealed_slot == rag
    with pytest.raises(psycopg.errors.RaiseException, match='phase mismatch'):
        with db.transaction() as cursor:
            cursor.execute('''update import_publication_evidence
                set phase='pair_sealed',phase_version=phase_version+1
                where user_id=%s and task_id=%s and task_lease_version=%s''', selector)
    with db.transaction() as cursor:
        _advance_private_evidence(cursor,selector,phase='pair_sealed',
                                  slot='episode_sealed_slot',value=episode)
        _advance_private_evidence(cursor,selector,phase='terminal_committed',
                                  slot='terminal_slot',value=terminal)
    assert repository.read_exact(*selector).terminal_slot == terminal
    with pytest.raises(psycopg.errors.RaiseException, match='Illegal import publication phase'):
        with db.transaction() as cursor:
            cursor.execute('''update import_publication_evidence
                set phase='abandoned',phase_version=phase_version+1
                where user_id=%s and task_id=%s and task_lease_version=%s''', selector)
