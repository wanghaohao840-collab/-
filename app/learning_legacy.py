"""Pure legacy validation. No filesystem reads, runtime wiring or database writes."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class LegacyValidationError(ValueError):
    """Static safe error code; never include source values."""


@dataclass(frozen=True)
class ValidatedLegacy:
    source_sha256: str
    normalized_sha256: str
    timezone: str
    normalized_json: bytes
    plan_count: int
    task_count: int


ROOT = {'version', 'plans', 'cards', 'exercises', 'review_logs'}
PLAN = {'id', 'document_id', 'document_name', 'title', 'target_date', 'daily_minutes',
        'status', 'tasks', 'created_at', 'updated_at'}
TASK = {'id', 'due_date', 'phase', 'title', 'duration_minutes', 'completed', 'completed_at'}


def _require(condition):
    if not condition:
        raise ValueError()


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _constant(_value):
    raise ValueError()


def _keys(value, expected):
    _require(type(value) is dict and set(value) == expected)


def _text(value):
    _require(type(value) is str and bool(value.strip()))
    return value


def _identifier(value, seen):
    _text(value)
    _require(str(UUID(value)) == value and value not in seen)
    seen.add(value)


def _minutes(value):
    _require(type(value) is int and 5 <= value <= 480)


def _day(value):
    _text(value)
    parsed = date.fromisoformat(value)
    _require(parsed.isoformat() == value)
    return parsed


def _timestamp(value):
    _text(value)
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    _require(parsed.tzinfo is not None and parsed.utcoffset() is not None)
    return parsed.astimezone(timezone.utc)


def _normalize(data, zone):
    _keys(data, ROOT)
    _require(type(data['version']) is int and data['version'] == 1)
    for name in ROOT - {'version'}:
        _require(type(data[name]) is list)
    if any(data[name] for name in ('cards', 'exercises', 'review_logs')):
        raise LegacyValidationError('UNSUPPORTED_MATERIALS')
    plans, plan_ids, task_ids = [], set(), set()
    for source in data['plans']:
        _keys(source, PLAN)
        _identifier(source['id'], plan_ids)
        for name in ('document_id', 'document_name', 'title'):
            _text(source[name])
        _require(1 <= len(source['title'].strip()) <= 100)
        _minutes(source['daily_minutes'])
        target = _day(source['target_date'])
        created, updated = _timestamp(source['created_at']), _timestamp(source['updated_at'])
        _require(updated >= created)
        _require(type(source['tasks']) is list and 1 <= len(source['tasks']) <= 365)
        tasks, dates = [], []
        for item in source['tasks']:
            _keys(item, TASK)
            _identifier(item['id'], task_ids)
            _text(item['title'])
            _require(item['phase'] in ('reading', 'cards', 'exercises', 'review'))
            _minutes(item['duration_minutes'])
            dates.append(_day(item['due_date']))
            _require(type(item['completed']) is bool)
            completed_at = None
            if item['completed']:
                completed = _timestamp(item['completed_at'])
                _require(completed <= updated)
                completed_at = completed.isoformat()
            else:
                _require(item['completed_at'] is None)
            tasks.append({**item, 'completed_at': completed_at, 'version': 1})
        dates.sort()
        _require(dates[-1] == target)
        _require(dates == [dates[0] + timedelta(days=i) for i in range(len(dates))])
        expected_status = 'completed' if all(task['completed'] for task in tasks) else 'active'
        _require(source['status'] == expected_status)
        plans.append({**source, 'tasks': tasks, 'timezone': zone,
                      'start_date': dates[0].isoformat(), 'version': 1,
                      'created_at': created.isoformat(), 'updated_at': updated.isoformat()})
    return {'version': 1, 'plans': plans}


def validate_legacy_payload(payload: bytes, *, timezone_name: str) -> ValidatedLegacy:
    try:
        _text(timezone_name)
        ZoneInfo(timezone_name)
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        raise LegacyValidationError('INVALID_TIMEZONE') from None
    if type(payload) is not bytes:
        raise LegacyValidationError('INVALID_LEGACY_DATA')
    if len(payload) > 16 * 1024 * 1024:
        raise LegacyValidationError('SOURCE_TOO_LARGE')
    try:
        data = json.loads(payload.decode('utf-8'), object_pairs_hook=_object, parse_constant=_constant)
        normalized = _normalize(data, timezone_name)
        encoded = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except LegacyValidationError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise LegacyValidationError('INVALID_LEGACY_DATA') from None
    return ValidatedLegacy(sha256(payload).hexdigest(), sha256(encoded).hexdigest(), timezone_name,
                           encoded, len(normalized['plans']), sum(len(p['tasks']) for p in normalized['plans']))
