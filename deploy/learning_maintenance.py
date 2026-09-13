"""Append-only checkpoints, not a production migration command.

The trusted coordinator must hold the operations lock, enforce directory ACLs
and verify actual backup/image/service facts. Checksums detect corruption, not
hostile rewrites. Partial writes are intentionally retained and fail closed.
"""
from dataclasses import dataclass
from hashlib import sha256
from itertools import islice
import json
import os
from pathlib import Path
import re
import stat
from uuid import UUID

LIMIT = 16384
PHASES = ('before_stop', 'stopped', 'backed_up', 'applying', 'applied', 'verified', 'complete')
FIELDS = {'version', 'operation_id', 'sequence', 'phase', 'baseline', 'backup', 'previous_sha256'}


class JournalError(ValueError):
    """Static error without runtime paths or payloads."""


@dataclass(frozen=True)
class JournalState:
    sequence: int
    phase: str
    baseline_json: str
    backup_json: str | None
    record_sha256: str


def _require(value):
    if not value:
        raise JournalError('INVALID_JOURNAL')


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _baseline(value):
    _require(type(value) is dict and set(value) == {'configuration_sha256', 'app_image', 'qdrant_image', 'running_services'})
    _require(_hash(value['configuration_sha256']))
    for key in ('app_image', 'qdrant_image'):
        _require(type(value[key]) is str and value[key].startswith('sha256:') and _hash(value[key][7:]))
    services = value['running_services']
    _require(type(services) is list and all(type(item) is str and item in ('app', 'qdrant') for item in services))
    _require(len(services) == len(set(services)))


def _backup(value):
    _require(type(value) is dict and set(value) == {'app_archive', 'app_sha256', 'qdrant_archive', 'qdrant_sha256'})
    for key in ('app_archive', 'qdrant_archive'):
        _require(type(value[key]) is str and '\x00' not in value[key])
        path = Path(value[key])
        _require(path.is_absolute() and '..' not in path.parts)
    _require(_hash(value['app_sha256']) and _hash(value['qdrant_sha256']))


def _safe(path, directory=True):
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        _require(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 1024)
        if component != path or directory:
            _require(stat.S_ISDIR(info.st_mode))
        else:
            _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= LIMIT)


def _location(state_root, operation_id):
    _require(type(operation_id) is str and str(UUID(operation_id)) == operation_id)
    root = Path(state_root).absolute()
    _require('..' not in root.parts)
    _safe(root)
    return root / 'learning-maintenance' / operation_id


def _transition(previous, current):
    phase = previous['phase']
    next_phase = current['phase']
    allowed = set()
    if phase in PHASES[:-1]:
        allowed = {PHASES[PHASES.index(phase)+1], 'recovering'}
    elif phase == 'recovering':
        allowed = {'recovered'}
    _require(next_phase in allowed)
    _require(current['baseline'] == previous['baseline'])
    if next_phase == 'backed_up':
        _require(previous['backup'] is None)
        _backup(current['backup'])
    else:
        _require(current['backup'] == previous['backup'])


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _read(directory, operation_id):
    _safe(directory)
    with os.scandir(directory) as scan:
        names = sorted(entry.name for entry in islice(scan, 17))
    _require(0 < len(names) <= 16 and names == [f'{i:04d}.json' for i in range(len(names))])
    previous, digest = None, None
    for sequence, name in enumerate(names):
        path = directory / name
        _safe(path, directory=False)
        with path.open('rb') as stream:
            raw = stream.read(LIMIT+1)
        _require(len(raw) <= LIMIT)
        current = json.loads(raw.decode('utf-8'), object_pairs_hook=_object)
        _require(type(current) is dict and set(current) == FIELDS)
        _require(type(current['version']) is int and current['version'] == 1)
        _require(type(current['sequence']) is int and current['sequence'] == sequence)
        _require(current['operation_id'] == operation_id and current['previous_sha256'] == digest)
        _baseline(current['baseline'])
        if previous is None:
            _require(current['phase'] == 'before_stop' and current['backup'] is None)
        else:
            _transition(previous, current)
        # Canonical bytes are also the format contract; no ambiguous encodings.
        _require(raw == _json(current).encode('utf-8'))
        digest = sha256(raw).hexdigest()
        previous = current
    return previous, digest


def _state(record, digest):
    return JournalState(record['sequence'], record['phase'], _json(record['baseline']),
                        None if record['backup'] is None else _json(record['backup']), digest)


def _write(directory, record):
    _safe(directory)
    raw = _json(record).encode('utf-8')
    _require(len(raw) <= LIMIT)
    with (directory / f"{record['sequence']:04d}.json").open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return _state(record, sha256(raw).hexdigest())


def begin_journal(state_root, operation_id, baseline):
    try:
        _baseline(baseline)
        directory = _location(state_root, operation_id)
        directory.parent.mkdir(exist_ok=True)
        _safe(directory.parent)
        directory.mkdir()  # Never reuse or repair an existing operation.
        return _write(directory, dict(version=1, operation_id=operation_id, sequence=0,
                                     phase='before_stop', baseline=baseline, backup=None, previous_sha256=None))
    except (OSError, ValueError, TypeError, RecursionError):
        raise JournalError('JOURNAL_BEGIN_FAILED') from None


def read_journal(state_root, operation_id):
    try:
        record, digest = _read(_location(state_root, operation_id), operation_id)
        return _state(record, digest)
    except (OSError, ValueError, TypeError, RecursionError):
        raise JournalError('JOURNAL_READ_FAILED') from None


def advance_journal(state_root, operation_id, expected_sequence, phase, *, backup=None):
    try:
        directory = _location(state_root, operation_id)
        previous, digest = _read(directory, operation_id)
        _require(type(expected_sequence) is int and expected_sequence == previous['sequence'])
        _require(type(phase) is str)
        _require(backup is None or phase == 'backed_up')
        record = previous | dict(sequence=expected_sequence+1, phase=phase, previous_sha256=digest,
                                 backup=backup if phase == 'backed_up' else previous['backup'])
        _transition(previous, record)
        return _write(directory, record)
    except (OSError, ValueError, TypeError, RecursionError):
        raise JournalError('JOURNAL_ADVANCE_FAILED') from None
