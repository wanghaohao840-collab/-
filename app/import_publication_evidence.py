"""Private, bounded intent proof and permanent candidate reservation authority.

The read value is scalar evidence only. It cannot issue a document capability or
authorize a vector mutation. Live publication is deliberately unavailable here.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import re
from uuid import UUID, uuid5
import zlib

import psycopg

from app.postgres_import_leases import ImportAttempt, PostgresImportLeaseRepository
from app.object_store import artifact_key
from hello_agents.memory.rag.index_identity import IndexIdentity, IndexIdentityError
from hello_agents.memory.rag.prepare import PROJECT_POINT_NAMESPACE_UUID


CANONICAL_LIMIT = 64 * 1024 * 1024
COMPRESSED_LIMIT = 8 * 1024 * 1024
LOGICAL_ROW_LIMIT = 9 * 1024 * 1024
SLOT_LIMIT = 256 * 1024
INTENT_FORMAT = 'canonical-json-zlib-1'
SCHEMA_VERSION = 1
_HASH = re.compile(r'[0-9a-f]{64}\Z')
RECEIPT_FIELDS = (
    'generation_id', 'tenant_id', 'vector_kind', 'namespace', 'index_key',
    'base_revision', 'index_revision', 'publication_revision',
    'publication_snapshot_version', 'expected_count', 'content_digest',
    'owner', 'user_lease_token', 'user_lease_version', 'task_id',
    'task_lease_token', 'task_lease_version', 'created_at', 'sealed_at',
    'published_at',
)
_RECEIPT_UUID = {'generation_id', 'user_lease_token', 'task_lease_token'}
_RECEIPT_TIME = {'created_at', 'sealed_at', 'published_at'}
_RECEIPT_INT = {'base_revision', 'index_revision', 'publication_revision',
                'publication_snapshot_version', 'expected_count',
                'user_lease_version', 'task_lease_version'}


class PublicationEvidenceError(ValueError):
    """A requested intent or durable proof is invalid or no longer current."""


class PublicationEvidenceUnknown(RuntimeError):
    """A commit response was lost; callers must stop before external I/O."""


def _uuid(value: object) -> str:
    if not isinstance(value, str):
        raise PublicationEvidenceError('Canonical UUID text required')
    try:
        if str(UUID(value)) == value:
            return value
    except (ValueError, TypeError, AttributeError):
        pass
    raise PublicationEvidenceError('Canonical UUID text required')


def _time(value: object) -> str:
    if not isinstance(value, str):
        raise PublicationEvidenceError('Offset-aware timestamp required')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.utcoffset() is None:
            raise ValueError('naive')
        return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds')
    except (ValueError, TypeError, AttributeError) as error:
        raise PublicationEvidenceError('Offset-aware timestamp required') from error


def _int(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise PublicationEvidenceError('Typed nonnegative integer required')
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise PublicationEvidenceError('Nonempty text required')
    return value


def _digest(value: object) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise PublicationEvidenceError('SHA-256 digest required')
    return value


def _keys(value: object, exact: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != exact:
        raise PublicationEvidenceError('Intent schema differs')
    return value


def _json_native(value: object, *, depth: int = 0) -> None:
    if depth > 100:
        raise PublicationEvidenceError('JSON nesting exceeds bound')
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise PublicationEvidenceError('Nonfinite JSON number')
        return
    if type(value) is list:
        for item in value:
            _json_native(item, depth=depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise PublicationEvidenceError('JSON keys must be text')
            _json_native(item, depth=depth + 1)
        return
    raise PublicationEvidenceError('JSON-native values required')


def _receipt(values: object, kind: str, user: str) -> list:
    if not isinstance(values, list) or len(values) != len(RECEIPT_FIELDS):
        raise PublicationEvidenceError('Complete ordered receipt required')
    result = []
    for key, value in zip(RECEIPT_FIELDS, values):
        if value is None:
            if key in ('generation_id', 'tenant_id', 'vector_kind', 'namespace',
                       'index_key', 'created_at', 'sealed_at', 'published_at'):
                raise PublicationEvidenceError('Receipt field is missing')
        elif key in _RECEIPT_UUID:
            _uuid(value)
        elif key in _RECEIPT_TIME:
            if _time(value) != value:
                raise PublicationEvidenceError('Receipt timestamp is noncanonical')
        elif key in _RECEIPT_INT:
            _int(value)
        else:
            _text(value)
        result.append(value)
    if values[1] != user or values[2] != kind:
        raise PublicationEvidenceError('Receipt tenant or kind differs')
    _digest(values[4])
    if values[10] is not None:
        _digest(values[10])
    return result


def _scope(value: object, kind: str, user: str) -> dict:
    value = _keys(value, {'tenant_id', 'vector_kind', 'namespace', 'index_key',
                          'identity', 'index_revision', 'head', 'old_receipt',
                          'candidate_id'})
    if value['tenant_id'] != user or value['vector_kind'] != kind:
        raise PublicationEvidenceError('Scope tenant or kind differs')
    _text(value['namespace'])
    _digest(value['index_key'])
    try:
        identity = IndexIdentity.from_dict(value['identity'])
    except (IndexIdentityError, TypeError, ValueError) as error:
        raise PublicationEvidenceError('Full valid index identity required') from error
    if (identity.backend != 'qdrant'
            or sha256(_canonical(value['identity'])).hexdigest() != value['index_key']
            or (kind == 'rag' and value['namespace'] != f'pdf_{user}')
            or (kind == 'episode' and value['namespace'] != 'episodes')):
        raise PublicationEvidenceError('Index identity or namespace differs')
    _int(value['index_revision'], minimum=1)
    _uuid(value['candidate_id'])
    head = _keys(value['head'], {'state', 'revision', 'generation_id', 'last_generation_id',
                                 'index_revision', 'snapshot_version'})
    if head['state'] not in ('empty', 'published'):
        raise PublicationEvidenceError('Published baseline head required')
    _int(head['revision'], minimum=1)
    _int(head['index_revision'], minimum=1)
    _int(head['snapshot_version'], minimum=1)
    if head['index_revision'] != value['index_revision']:
        raise PublicationEvidenceError('Index revision differs')
    if head['state'] == 'published':
        _uuid(head['generation_id'])
    elif head['generation_id'] is not None:
        raise PublicationEvidenceError('Empty head has generation')
    _uuid(head['last_generation_id'])
    _receipt(value['old_receipt'], kind, user)
    if value['old_receipt'][0] != head['last_generation_id']:
        raise PublicationEvidenceError('Receipt and head generation differ')
    if (value['old_receipt'][3] != value['namespace']
            or value['old_receipt'][4] != value['index_key']
            or value['old_receipt'][6] != value['index_revision']
            or value['old_receipt'][7] != head['revision']
            or value['old_receipt'][8] != head['snapshot_version']):
        raise PublicationEvidenceError('Full receipt and head differ')
    return value


def validate_intent(intent: object) -> dict:
    intent = _keys(intent, {'schema_version', 'attempt', 'task', 'source',
                            'scopes', 'snapshots', 'memory_rows', 'event',
                            'document'})
    if type(intent['schema_version']) is not int or intent['schema_version'] != SCHEMA_VERSION:
        raise PublicationEvidenceError('Unsupported intent schema version')
    attempt = _keys(intent['attempt'], {'user_id', 'task_id', 'task_lease_version',
        'worker_id', 'task_lease_token', 'user_lease_token', 'user_lease_version',
        'document_id'})
    user = _text(attempt['user_id'])
    _text(attempt['task_id'])
    _int(attempt['task_lease_version'], minimum=1)
    _text(attempt['worker_id'])
    _uuid(attempt['task_lease_token'])
    _uuid(attempt['user_lease_token'])
    _int(attempt['user_lease_version'], minimum=1)
    _uuid(attempt['document_id'])
    task = _keys(intent['task'], {'task_id', 'batch_id', 'user_id', 'document_id',
        'original_name', 'file_suffix', 'size_bytes', 'created_at'})
    if any(task[k] != attempt[k] for k in ('task_id', 'user_id', 'document_id')):
        raise PublicationEvidenceError('Task identity differs')
    _text(task['batch_id'])
    _text(task['original_name'])
    _text(task['file_suffix'])
    _int(task['size_bytes'])
    _time(task['created_at'])
    source = _keys(intent['source'], {'bucket', 'key', 'version_id', 'sha256', 'size_bytes'})
    for key in ('bucket', 'key', 'version_id'):
        _text(source[key])
    _digest(source['sha256'])
    _int(source['size_bytes'])
    if source['size_bytes'] != task['size_bytes']:
        raise PublicationEvidenceError('Source size differs')
    if source['key'] != artifact_key(user, 'imports', task['task_id'],
                                     task['file_suffix'], source['sha256']):
        raise PublicationEvidenceError('Accepted source key differs')
    scopes = _keys(intent['scopes'], {'rag', 'episode'})
    rag = _scope(scopes['rag'], 'rag', user)
    episode = _scope(scopes['episode'], 'episode', user)
    if rag['candidate_id'] == episode['candidate_id']:
        raise PublicationEvidenceError('Candidate identities must differ')
    snapshots = _keys(intent['snapshots'], {'old_history', 'next_history',
                                            'old_memory', 'next_memory'})
    for name, snapshot in snapshots.items():
        _keys(snapshot, {'version', 'data'})
        _int(snapshot['version'], minimum=1)
        if not isinstance(snapshot['data'], dict):
            raise PublicationEvidenceError('Full snapshot required')
        _json_native(snapshot['data'])
        if name.endswith('memory') and snapshot['data'].get('user_id') != user:
            raise PublicationEvidenceError('Memory snapshot owner differs')
    if (snapshots['next_history']['version'] != snapshots['old_history']['version'] + 1
            or snapshots['next_memory']['version'] != snapshots['old_memory']['version'] + 1
            or rag['head']['snapshot_version'] != snapshots['old_history']['version']
            or episode['head']['snapshot_version'] != snapshots['old_memory']['version']):
        raise PublicationEvidenceError('Snapshot versions differ')
    rows = intent['memory_rows']
    if not isinstance(rows, list):
        raise PublicationEvidenceError('Complete raw Memory rows required')
    seen = set()
    for row in rows:
        if (not isinstance(row, list) or len(row) != 4
                or not isinstance(row[0], str) or not row[0]
                or not isinstance(row[1], str) or not isinstance(row[2], str)
                or (row[3] is not None and not isinstance(row[3], str))
                or row[0] in seen):
            raise PublicationEvidenceError('Memory row shape differs')
        seen.add(row[0])
    event = _keys(intent['event'], {'event_id', 'timestamp', 'item', 'metadata'})
    _text(event['event_id'])
    _time(event['timestamp'])
    for key in ('item', 'metadata'):
        if not isinstance(event[key], dict):
            raise PublicationEvidenceError('Complete event data required')
        _json_native(event[key])
    document = _keys(intent['document'], {'key', 'record', 'count', 'digest',
                                          'baseline_row_digest', 'fixed_refs',
                                          'old_witness'})
    _text(document['key'])
    _int(document['count'])
    _digest(document['digest'])
    _digest(document['baseline_row_digest'])
    if not isinstance(document['fixed_refs'], list):
        raise PublicationEvidenceError('Full retained references required')
    for ref in document['fixed_refs']:
        if not isinstance(ref, list) or len(ref) != 7:
            raise PublicationEvidenceError('Retained reference shape differs')
        for value in ref[:5]:
            _text(value)
        _int(ref[5])
        _digest(ref[4])
        _digest(ref[6])
    witness = document['old_witness']
    if witness is not None:
        _keys(witness, {'head_revision', 'last_generation_id', 'index_revision',
                        'publication_snapshot_version', 'document_count',
                        'documents_sha256'})
        for name in ('head_revision', 'index_revision',
                     'publication_snapshot_version'):
            _int(witness[name], minimum=1)
        _int(witness['document_count'])
        _uuid(witness['last_generation_id'])
        _digest(witness['documents_sha256'])
        if (witness['head_revision'] != rag['head']['revision']
                or witness['last_generation_id'] != rag['head']['last_generation_id']):
            raise PublicationEvidenceError('Old witness differs from RAG head')
    if not isinstance(document['record'], dict):
        raise PublicationEvidenceError('Complete document record required')
    _json_native(document['record'])
    if (document['record'].get('user_id') != user
            or document['record'].get('document_id') != attempt['document_id']
            or document['record'].get('document_path') !=
            f"object://{source['bucket']}/{document['key']}"
            or event['metadata'].get('document_path') != document['record']['document_path']):
        raise PublicationEvidenceError('Document path or identity differs')
    if document['key'] != artifact_key(user, 'documents', attempt['document_id'],
                                       task['file_suffix'], source['sha256']):
        raise PublicationEvidenceError('Deterministic document key differs')
    expected_record = {'user_id': user, 'document_id': task['document_id'],
        'document_name': task['original_name'], 'file_suffix': task['file_suffix'],
        'document_path': f"object://{source['bucket']}/{document['key']}",
        'loaded_at': task['created_at'], 'import_task_id': task['task_id']}
    if _canonical(document['record']) != _canonical(expected_record):
        raise PublicationEvidenceError('Document record differs from task')
    if event['event_id'] != 'import-' + str(uuid5(
            PROJECT_POINT_NAMESPACE_UUID, f"{user}:{task['task_id']}")):
        raise PublicationEvidenceError('Deterministic event identity differs')
    item_metadata = {'user_id': user, 'import_task_id': task['task_id'],
        'document_id': task['document_id'], 'document_name': task['original_name'],
        'document_path': document['record']['document_path'],
        'file_suffix': task['file_suffix'], 'session_id': 'import'}
    expected_item = {'id': event['event_id'],
        'content': f"用户导入了文档：{task['original_name']}",
        'memory_type': 'episodic', 'importance': 0.8,
        'timestamp': event['timestamp'], 'metadata': item_metadata}
    expected_metadata = dict(item_metadata) | {
        'memory_id': event['event_id'], 'episode_id': event['event_id'],
        'timestamp': event['timestamp'], 'memory_type': 'episodic',
        'importance': 0.8, 'content': expected_item['content']}
    if (_canonical(event['item']) != _canonical(expected_item)
            or _canonical(event['metadata']) != _canonical(expected_metadata)):
        raise PublicationEvidenceError('Event projection differs')
    if (sha256(_canonical(rows)).hexdigest() != document['baseline_row_digest']
            or document['record'].get('import_task_id') != attempt['task_id']):
        raise PublicationEvidenceError('Baseline rows or document task differ')
    old_history = snapshots['old_history']['data']
    next_history = snapshots['next_history']['data']
    old_memory = snapshots['old_memory']['data']
    next_memory = snapshots['next_memory']['data']
    if (not isinstance(old_history.get('documents'), list)
            or not isinstance(next_history.get('documents'), list)
            or _canonical(next_history) != _canonical(dict(old_history,
                                    documents=[*old_history['documents'], document['record']]))
            or not isinstance(old_memory.get('memories'), list)
            or not isinstance(next_memory.get('memories'), list)
            or _canonical(next_memory) != _canonical(dict(old_memory,
                                   memories=[*old_memory['memories'], event['item']]))
            or event['item'].get('metadata', {}).get('document_path') !=
               document['record']['document_path']):
        raise PublicationEvidenceError('Complete snapshot append differs')
    document_bytes = _canonical(next_history['documents'])
    if (document['count'] != len(next_history['documents'])
            or document['digest'] != sha256(document_bytes).hexdigest()):
        raise PublicationEvidenceError('History document digest differs')
    _json_native(intent)
    return intent


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def _bounded_canonical(value: object) -> bytes:
    """Stop serializing as soon as the canonical byte ceiling is crossed."""
    output = bytearray()
    encoder = json.JSONEncoder(sort_keys=True, separators=(',', ':'),
                               ensure_ascii=False, allow_nan=False)
    for fragment in encoder.iterencode(value):
        chunk = fragment.encode('utf-8')
        if len(output) + len(chunk) > CANONICAL_LIMIT:
            raise PublicationEvidenceError('Canonical intent exceeds bound')
        output.extend(chunk)
    return bytes(output)


def _minimum_text_bytes(value: object, spent: list[int]) -> None:
    """Reject giant caller values before making the private deep copy."""
    if spent[0] > CANONICAL_LIMIT:
        return
    if type(value) is str:
        spent[0] += len(value)  # UTF-8 and JSON escaping can only be larger.
    elif type(value) is list:
        for item in value:
            _minimum_text_bytes(item, spent)
    elif type(value) is dict:
        for key, item in value.items():
            _minimum_text_bytes(key, spent)
            _minimum_text_bytes(item, spent)


@dataclass(frozen=True)
class EncodedIntent:
    payload: bytes = field(repr=False)
    canonical_bytes: int
    digest: str
    format: str = INTENT_FORMAT


def encode_intent(intent: dict) -> EncodedIntent:
    spent = [0]
    try:
        _minimum_text_bytes(intent, spent)
    except RecursionError as error:
        raise PublicationEvidenceError('Intent nesting exceeds bound') from error
    if spent[0] > CANONICAL_LIMIT:
        raise PublicationEvidenceError('Canonical intent exceeds bound')
    copied = deepcopy(intent)
    validate_intent(copied)
    raw = _bounded_canonical(copied)
    encoded = zlib.compress(raw, level=6)
    if len(encoded) > COMPRESSED_LIMIT or len(encoded) > LOGICAL_ROW_LIMIT:
        raise PublicationEvidenceError('Compressed intent exceeds bound')
    return EncodedIntent(encoded, len(raw), sha256(raw).hexdigest())


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PublicationEvidenceError('Duplicate JSON key')
        result[key] = value
    return result


def _bad_number(value: str):
    raise PublicationEvidenceError('Nonfinite JSON number')


def decode_intent(encoded: bytes, *, canonical_bytes: int, digest: str) -> dict:
    if (type(encoded) is not bytes or len(encoded) > COMPRESSED_LIMIT
            or type(canonical_bytes) is not int or not 0 < canonical_bytes <= CANONICAL_LIMIT):
        raise PublicationEvidenceError('Encoded intent length differs')
    _digest(digest)
    decompressor = zlib.decompressobj()
    output = bytearray()
    try:
        for start in range(0, len(encoded), 65536):
            chunk = encoded[start:start + 65536]
            while chunk:
                remaining = canonical_bytes - len(output)
                part = decompressor.decompress(chunk, remaining + 1)
                output.extend(part)
                if len(output) > canonical_bytes:
                    raise PublicationEvidenceError('Decoded intent exceeds bound')
                chunk = decompressor.unconsumed_tail
                if decompressor.eof:
                    if chunk or decompressor.unused_data or start + 65536 < len(encoded):
                        raise PublicationEvidenceError('Trailing compressed bytes')
                    break
        output.extend(decompressor.flush(canonical_bytes - len(output) + 1))
    except zlib.error as error:
        raise PublicationEvidenceError('Malformed compressed intent') from error
    if not decompressor.eof or decompressor.unused_data or len(output) != canonical_bytes:
        raise PublicationEvidenceError('Truncated or oversized intent')
    raw = bytes(output)
    if sha256(raw).hexdigest() != digest:
        raise PublicationEvidenceError('Intent hash differs')
    try:
        decoded = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs,
                             parse_constant=_bad_number)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise PublicationEvidenceError('Intent JSON is malformed') from error
    validate_intent(decoded)
    if _canonical(decoded) != raw:
        raise PublicationEvidenceError('Intent JSON is noncanonical')
    return decoded


@dataclass(frozen=True)
class AttemptKey:
    user_id: str
    task_id: str
    task_lease_version: int


@dataclass(frozen=True)
class FrozenEvidence:
    key: AttemptKey
    intent: dict = field(repr=False)
    phase: str
    phase_version: int
    observation_reason: str | None
    intent_hash: str
    document_slot: bytes | None = field(repr=False)
    rag_sealed_slot: bytes | None = field(repr=False)
    episode_sealed_slot: bytes | None = field(repr=False)
    terminal_slot: bytes | None = field(repr=False)


def _read_slot(raw: bytes | None) -> dict | None:
    if raw is None:
        return None
    if len(raw) > SLOT_LIMIT:
        raise PublicationEvidenceError('Evidence slot exceeds bound')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs,
                           parse_constant=_bad_number)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise PublicationEvidenceError('Evidence slot JSON is malformed') from error
    _json_native(value)
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise PublicationEvidenceError('Evidence slot is noncanonical')
    return value


def _validate_stored_slots(intent: dict, intent_hash: str, phase: str,
                           phase_version: int, reason: str | None,
                           slots: tuple[bytes | None, ...]) -> None:
    if (phase not in ('intent', 'document_verified', 'pair_sealed',
                      'terminal_committed', 'proved_succeeded', 'abandoned')
            or type(phase_version) is not int or phase_version < 1
            or reason not in (None, 'unknown', 'manual_hold')):
        raise PublicationEvidenceError('Stored evidence phase differs')
    document, rag, episode, terminal = map(_read_slot, slots)
    if ((phase == 'intent' and any(slot is not None for slot in slots))
            or (phase == 'document_verified' and
                (document is None or episode is not None or terminal is not None))
            or (phase == 'pair_sealed' and
                (document is None or rag is None or episode is None or terminal is not None))
            or (phase in ('terminal_committed', 'proved_succeeded') and
                any(slot is None for slot in slots))
            or (phase == 'abandoned' and terminal is not None)
            or (document is None and (rag is not None or episode is not None))
            or (episode is not None and rag is None)):
        raise PublicationEvidenceError('Stored evidence slots differ from phase')
    if document is None:
        return
    _keys(document, {'user_id', 'document_id', 'bucket', 'key', 'version_id',
                     'sha256', 'size_bytes', 'record_hash', 'final_expected_hash'})
    source = intent['source']
    expected = intent['document']
    attempt = intent['attempt']
    if (document['user_id'] != attempt['user_id']
            or document['document_id'] != attempt['document_id']
            or document['bucket'] != source['bucket']
            or document['key'] != expected['key']
            or document['sha256'] != source['sha256']
            or document['size_bytes'] != source['size_bytes']):
        raise PublicationEvidenceError('Document result differs from frozen intent')
    _text(document['version_id'])
    _int(document['size_bytes'])
    _digest(document['record_hash'])
    if document['record_hash'] != sha256(_canonical(expected['record'])).hexdigest():
        raise PublicationEvidenceError('Document record hash differs')
    _digest(document['final_expected_hash'])
    unsigned = {key: value for key, value in document.items()
                if key != 'final_expected_hash'}
    final_hash = sha256(b'1:' + intent_hash.encode('ascii') + b':' +
                        _canonical(unsigned)).hexdigest()
    if document['final_expected_hash'] != final_hash:
        raise PublicationEvidenceError('Final expected hash differs')
    for kind, sealed in (('rag', rag), ('episode', episode)):
        if sealed is None:
            continue
        _keys(sealed, {'generation_id', 'vector_kind', 'user_id', 'task_id',
                       'task_lease_token', 'task_lease_version', 'worker_id', 'user_lease_token',
                       'user_lease_version', 'namespace', 'index_key',
                       'base_revision', 'index_revision', 'snapshot_version',
                       'expected_count', 'content_digest', 'final_expected_hash'})
        scope = intent['scopes'][kind]
        next_snapshot = intent['snapshots']['next_' + ('history' if kind == 'rag' else 'memory')]
        if (sealed['generation_id'] != scope['candidate_id']
                or sealed['vector_kind'] != kind
                or sealed['user_id'] != attempt['user_id']
                or sealed['task_id'] != attempt['task_id']
                or sealed['task_lease_token'] != attempt['task_lease_token']
                or sealed['task_lease_version'] != attempt['task_lease_version']
                or sealed['worker_id'] != attempt['worker_id']
                or sealed['user_lease_token'] != attempt['user_lease_token']
                or sealed['user_lease_version'] != attempt['user_lease_version']
                or sealed['namespace'] != scope['namespace']
                or sealed['index_key'] != scope['index_key']
                or sealed['base_revision'] != scope['head']['revision']
                or sealed['index_revision'] != scope['index_revision']
                or sealed['snapshot_version'] != next_snapshot['version']
                or sealed['final_expected_hash'] != final_hash):
            raise PublicationEvidenceError('Sealed candidate differs from frozen intent')
        _uuid(sealed['generation_id'])
        _uuid(sealed['task_lease_token'])
        _uuid(sealed['user_lease_token'])
        for name in ('task_lease_version', 'user_lease_version', 'base_revision',
                     'index_revision', 'snapshot_version'):
            _int(sealed[name], minimum=1)
        if _int(sealed['expected_count']) > 100000:
            raise PublicationEvidenceError('Sealed point count exceeds vector bound')
        _digest(sealed['content_digest'])
    if terminal is not None:
        _keys(terminal, {'final_expected_hash', 'rag_receipt', 'episode_receipt'})
        if terminal['final_expected_hash'] != final_hash:
            raise PublicationEvidenceError('Terminal final hash differs')
        for kind in ('rag', 'episode'):
            scope = intent['scopes'][kind]
            receipt = _receipt(terminal[kind + '_receipt'], kind, attempt['user_id'])
            sealed = rag if kind == 'rag' else episode
            next_snapshot = intent['snapshots']['next_' + ('history' if kind == 'rag' else 'memory')]
            if (receipt[0] != scope['candidate_id']
                    or receipt[3] != scope['namespace']
                    or receipt[4] != scope['index_key']
                    or receipt[5] != scope['head']['revision']
                    or receipt[6] != scope['index_revision']
                    or receipt[7] != scope['head']['revision'] + 1
                    or receipt[8] != next_snapshot['version']
                    or receipt[9] != sealed['expected_count']
                    or receipt[10] != sealed['content_digest']
                    or receipt[11] != attempt['worker_id']
                    or receipt[12] != attempt['user_lease_token']
                    or receipt[13] != attempt['user_lease_version']
                    or receipt[14] != attempt['task_id']
                    or receipt[15] != attempt['task_lease_token']
                    or receipt[16] != attempt['task_lease_version']):
                raise PublicationEvidenceError('Terminal receipt differs from sealed intent')


def require_no_gate_in_transaction(cursor, user_id: str) -> None:
    """Call after locking the user row; missing authority tables fail closed."""
    if cursor.execute('''select 1 from user_publication_gates
        where user_id=%s and status='unresolved' limit 1''', (user_id,)).fetchone():
        raise PublicationEvidenceError('Unresolved import publication gates this user')


class PostgresImportPublicationEvidenceRepository:
    def __init__(self, database):
        self.database = database
        self.imports = PostgresImportLeaseRepository(database)

    def reserve_intent(self, attempt: ImportAttempt, intent: dict) -> AttemptKey:
        encoded = encode_intent(intent)
        frozen = decode_intent(encoded.payload, canonical_bytes=encoded.canonical_bytes,
                               digest=encoded.digest)
        handle = frozen['attempt']
        if (not isinstance(attempt, ImportAttempt)
                or (handle['user_id'], handle['task_id'], handle['task_lease_version'],
                    handle['worker_id'], handle['task_lease_token'],
                    handle['user_lease_token'], handle['user_lease_version'],
                    handle['document_id']) !=
                   (attempt.task.user_id, attempt.task.task_id, attempt.lease_version,
                    attempt.worker_id, str(attempt.lease_token),
                     str(attempt.user_lease.lease_token), attempt.user_lease.lease_version,
                     attempt.task.document_id)):
            raise PublicationEvidenceError('Intent and live attempt differ')
        if (attempt.bucket != frozen['source']['bucket']
                or (attempt.source.key, attempt.source.version_id,
                    attempt.source.sha256, attempt.source.size_bytes) !=
                   (frozen['source']['key'], frozen['source']['version_id'],
                    frozen['source']['sha256'], frozen['source']['size_bytes'])):
            raise PublicationEvidenceError('Intent and accepted source pin differ')
        key = AttemptKey(handle['user_id'], handle['task_id'],
                         handle['task_lease_version'])
        try:
            with self.database.transaction() as cursor:
                cursor.execute('begin')
                self._verify_current(cursor, attempt, frozen)
                require_no_gate_in_transaction(cursor, key.user_id)
                cursor.execute('''insert into import_publication_evidence
                    (user_id,task_id,task_lease_version,document_id,worker_id,
                     task_lease_token,user_lease_token,user_lease_version,
                     schema_version,intent_format,intent_payload,canonical_bytes,intent_hash)
                    values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (*key.__dict__.values(), handle['document_id'], handle['worker_id'],
                     handle['task_lease_token'], handle['user_lease_token'],
                     handle['user_lease_version'], SCHEMA_VERSION, INTENT_FORMAT,
                     encoded.payload, encoded.canonical_bytes, encoded.digest))
                cursor.execute('''insert into user_publication_gates
                    (user_id,task_id,task_lease_version) values(%s,%s,%s)''',
                    (key.user_id, key.task_id, key.task_lease_version))
                for kind in ('rag', 'episode'):
                    scope = frozen['scopes'][kind]
                    if cursor.execute('select 1 from vector_generations where generation_id=%s',
                                      (scope['candidate_id'],)).fetchone():
                        raise PublicationEvidenceError('Candidate UUID already exists in vector authority')
                    cursor.execute('''insert into generation_reservations
                        (generation_id,user_id,task_id,task_lease_version,
                         vector_kind,namespace,index_key,base_revision,owner,
                         user_lease_token,user_lease_version)
                        values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                        (scope['candidate_id'], key.user_id, key.task_id,
                         key.task_lease_version, kind, scope['namespace'],
                         scope['index_key'], scope['head']['revision'],
                         handle['worker_id'], handle['user_lease_token'],
                         handle['user_lease_version']))
                self.imports._live(cursor, attempt)
        except psycopg.IntegrityError as error:
            raise PublicationEvidenceError('Intent identity or candidate is already reserved') from error
        except psycopg.OperationalError as error:
            raise PublicationEvidenceUnknown('Intent commit outcome is unknown') from error
        return key

    def _verify_current(self, cursor, attempt: ImportAttempt, intent: dict) -> None:
        from app.import_repository import _task_from_row
        row = self.imports._live(cursor, attempt)
        task = _task_from_row(row)
        if any(getattr(task, key) != intent['task'][key] for key in (
                'task_id', 'batch_id', 'user_id', 'document_id',
                'original_name', 'file_suffix', 'size_bytes', 'created_at')):
            raise PublicationEvidenceError('Task changed before reservation')
        source = cursor.execute('''select bucket,object_key,version_id,sha256,size_bytes
            from import_objects where task_id=%s and user_id=%s''',
            (task.task_id, task.user_id)).fetchone()
        if (source is None or {k: source[sql] for k, sql in
                (('bucket','bucket'),('key','object_key'),('version_id','version_id'),
                 ('sha256','sha256'),('size_bytes','size_bytes'))} != intent['source']):
            raise PublicationEvidenceError('Pinned source changed before reservation')
        for kind in ('rag', 'episode'):
            scope = intent['scopes'][kind]
            params = (task.user_id, kind, scope['namespace'], scope['index_key'])
            index = cursor.execute('''select identity,index_revision from vector_indexes
                where tenant_id=%s and vector_kind=%s and namespace=%s and index_key=%s''',
                params).fetchone()
            head = cursor.execute('''select revision,generation_id,last_generation_id,index_revision,
                snapshot_version from vector_heads where tenant_id=%s and vector_kind=%s
                and namespace=%s and index_key=%s''', params).fetchone()
            if (index is None or index['identity'] != scope['identity']
                    or index['index_revision'] != scope['index_revision']
                    or head is None or
                    {'state': 'published' if head['generation_id'] else 'empty',
                     'revision': head['revision'],
                     'generation_id': str(head['generation_id']) if head['generation_id'] else None,
                     'last_generation_id': str(head['last_generation_id']),
                     'index_revision': head['index_revision'],
                     'snapshot_version': head['snapshot_version']} != scope['head']):
                raise PublicationEvidenceError('Vector base changed before reservation')
            old = cursor.execute('''select state,to_jsonb(g) as receipt from vector_generations g
                where generation_id=%s and tenant_id=%s and vector_kind=%s
                  and namespace=%s and index_key=%s''',
                (head['last_generation_id'], *params)).fetchone()
            if old is None or old['state'] != 'published':
                raise PublicationEvidenceError('Old full receipt missing')
            current = _receipt_from_sql(old['receipt'])
            if current != scope['old_receipt']:
                raise PublicationEvidenceError('Old full receipt changed')
        for kind in ('history', 'memory'):
            old = intent['snapshots']['old_' + kind]
            row = cursor.execute('''select version,payload from user_snapshots
                where user_id=%s and kind=%s''', (task.user_id, kind)).fetchone()
            if (row is None or row['version'] != old['version']
                    or _canonical(row['payload']) != _canonical(old['data'])):
                raise PublicationEvidenceError('Baseline snapshot changed')
        witness = cursor.execute('''select head_revision,last_generation_id,
            index_revision,publication_snapshot_version,document_count,documents_sha256
            from history_document_witnesses where tenant_id=%s and vector_kind='rag'
            and namespace=%s and index_key=%s and head_revision=%s''',
            (task.user_id, intent['scopes']['rag']['namespace'],
             intent['scopes']['rag']['index_key'],
             intent['scopes']['rag']['head']['revision'])).fetchone()
        observed_witness = None if witness is None else {
            name: str(witness[name]) if name == 'last_generation_id' else witness[name]
            for name in ('head_revision','last_generation_id','index_revision',
                         'publication_snapshot_version','document_count','documents_sha256')}
        if observed_witness != intent['document']['old_witness']:
            raise PublicationEvidenceError('Old History witness changed')
        fixed = intent['document']['fixed_refs']
        if fixed:
            refs = cursor.execute('''select document_id,bucket,object_key,version_id,
                sha256,size_bytes,history_record_sha256 from document_objects
                where user_id=%s and document_id=any(%s)''',
                (task.user_id, [item[0] for item in fixed])).fetchall()
            if sorted([list(ref.values()) for ref in refs]) != sorted(fixed):
                raise PublicationEvidenceError('Retained document references changed')
        rows = cursor.execute('''select document_id,content,metadata,created_at
            from memory_documents where user_id=%s order by document_id''',
            (task.user_id,)).fetchall()
        if [[r['document_id'], r['content'], r['metadata'], r['created_at']]
                for r in rows] != intent['memory_rows']:
            raise PublicationEvidenceError('Raw Memory rows changed')
        self.imports._live(cursor, attempt)

    def read_exact(self, user_id: str, task_id: str,
                   task_lease_version: int) -> FrozenEvidence | None:
        _text(user_id)
        _text(task_id)
        _int(task_lease_version, minimum=1)
        with self.database.transaction() as cursor:
            row = cursor.execute('''select * from import_publication_evidence
                where user_id=%s and task_id=%s and task_lease_version=%s''',
                (user_id, task_id, task_lease_version)).fetchone()
        if row is None:
            return None
        if row['schema_version'] != SCHEMA_VERSION or row['intent_format'] != INTENT_FORMAT:
            raise PublicationEvidenceError('Unsupported stored evidence format')
        intent = decode_intent(bytes(row['intent_payload']),
            canonical_bytes=row['canonical_bytes'], digest=row['intent_hash'])
        attempt = intent['attempt']
        if (attempt['user_id'], attempt['task_id'], attempt['task_lease_version'],
                attempt['document_id'], attempt['worker_id'], attempt['task_lease_token'],
                attempt['user_lease_token'], attempt['user_lease_version']) != (
                row['user_id'], row['task_id'], row['task_lease_version'],
                row['document_id'], row['worker_id'], str(row['task_lease_token']),
                str(row['user_lease_token']), row['user_lease_version']):
            raise PublicationEvidenceError('Stored evidence identity differs')
        slots = tuple(bytes(row[name]) if row[name] is not None else None for name in
                      ('document_slot','rag_sealed_slot','episode_sealed_slot','terminal_slot'))
        if (len(row['intent_payload']) + sum(len(value or b'') for value in slots)
                > LOGICAL_ROW_LIMIT or any(value is not None and len(value) > SLOT_LIMIT
                                           for value in slots)):
            raise PublicationEvidenceError('Stored evidence row exceeds bound')
        _validate_stored_slots(intent, row['intent_hash'], row['phase'],
                               row['phase_version'], row['observation_reason'], slots)
        return FrozenEvidence(AttemptKey(user_id, task_id, task_lease_version),
            deepcopy(intent), row['phase'], row['phase_version'], row['observation_reason'],
            row['intent_hash'], *slots)


def _receipt_from_sql(row: dict) -> list:
    result = []
    for field in RECEIPT_FIELDS:
        if field not in row:
            raise PublicationEvidenceError('Stored receipt field missing')
        value = row[field]
        if value is not None:
            if field in _RECEIPT_UUID:
                value = _uuid(str(value))
            elif field in _RECEIPT_TIME:
                value = (value if isinstance(value, datetime)
                         else datetime.fromisoformat(value.replace('Z', '+00:00')))
                value = value.astimezone(timezone.utc).isoformat(timespec='microseconds')
        result.append(value)
    return result
