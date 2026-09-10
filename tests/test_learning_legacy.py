import json
from copy import deepcopy
from uuid import uuid4

import pytest

from app.learning_legacy import LegacyValidationError, validate_legacy_payload


def source():
    return dict(version=1, cards=[], exercises=[], review_logs=[], plans=[dict(
        id=str(uuid4()), document_id='doc-1', document_name='资料.md', title='旧计划',
        target_date='2026-09-07', daily_minutes=30, status='active',
        created_at='2026-09-07T08:00:00+08:00', updated_at='2026-09-07T08:00:00+08:00',
        tasks=[dict(id=str(uuid4()), due_date='2026-09-07', phase='review', title='复习',
                    duration_minutes=30, completed=False, completed_at=None)])])


def validate(data):
    return validate_legacy_payload(json.dumps(data).encode(), timezone_name='Asia/Shanghai')


def test_preserves_ids_and_times_without_mutation():
    data = source()
    original = deepcopy(data)
    result = validate(data)
    plan = json.loads(result.normalized_json)['plans'][0]
    assert data == original
    assert result.plan_count == result.task_count == 1
    assert plan['id'] == data['plans'][0]['id']
    assert plan['tasks'][0]['id'] == data['plans'][0]['tasks'][0]['id']
    assert plan['created_at'] == '2026-09-07T00:00:00+00:00'
    assert plan['start_date'] == '2026-09-07'
    assert plan['timezone'] == 'Asia/Shanghai'
    assert plan['version'] == plan['tasks'][0]['version'] == 1
    assert 'events' not in plan


def test_semantically_equal_times_normalize_same_but_source_hash_differs():
    data = source()
    first = validate(data)
    data['plans'][0]['created_at'] = '2026-09-07T00:00:00Z'
    second = validate(data)
    assert first.normalized_sha256 == second.normalized_sha256
    assert first.source_sha256 != second.source_sha256


def test_empty_source():
    data = source()
    data['plans'] = []
    assert validate(data).plan_count == 0


@pytest.mark.parametrize('collection', ['cards', 'exercises', 'review_logs'])
def test_unsupported_material_never_silently_dropped(collection):
    data = source()
    data[collection] = [{}]
    with pytest.raises(LegacyValidationError, match='^UNSUPPORTED_MATERIALS$'):
        validate(data)


@pytest.mark.parametrize('field,value', [
    ('daily_minutes', True), ('daily_minutes', 4), ('daily_minutes', 481),
    ('title', ''), ('title', 'x'*101), ('id', 'bad'), ('created_at', '2026-09-07'),
    ('updated_at', '2026-09-06T00:00:00Z'), ('status', 'completed'),
    ('target_date', '2026-09-08'), ('tasks', []), ('unknown', 'private-content'),
])
def test_invalid_plan(field, value):
    data = source()
    data['plans'][0][field] = value
    with pytest.raises(LegacyValidationError, match='^INVALID_LEGACY_DATA$'):
        validate(data)


@pytest.mark.parametrize('field,value', [
    ('completed', 1), ('completed', True), ('completed_at', '2026-09-07T00:00:00Z'),
    ('duration_minutes', False), ('phase', 'invalid'), ('id', 'bad'),
    ('due_date', '2026-09-06'), ('unknown', 'secret'),
])
def test_invalid_task(field, value):
    data = source()
    data['plans'][0]['tasks'][0][field] = value
    with pytest.raises(LegacyValidationError):
        validate(data)


def test_completed_history_preserved():
    data = source()
    plan = data['plans'][0]
    plan['status'] = 'completed'
    plan['tasks'][0].update(completed=True, completed_at=plan['updated_at'])
    task = json.loads(validate(data).normalized_json)['plans'][0]['tasks'][0]
    assert task['completed_at'] == '2026-09-07T00:00:00+00:00'


@pytest.mark.parametrize('kind', ['plan', 'task', 'gap', 'date'])
def test_duplicate_or_inconsistent_relationships(kind):
    data = source()
    plan = data['plans'][0]
    if kind == 'plan':
        data['plans'].append(deepcopy(plan))
    else:
        task = deepcopy(plan['tasks'][0])
        if kind != 'task':
            task['id'] = str(uuid4())
        if kind == 'gap':
            task['due_date'] = plan['target_date'] = '2026-09-09'
        plan['tasks'].append(task)
    with pytest.raises(LegacyValidationError):
        validate(data)


@pytest.mark.parametrize('payload', [b'{}', b'\xff', b'{"version":1,"version":1}', b'NaN', b'['*2000, b' '*(16*1024*1024+1)], ids=['empty-object', 'encoding', 'duplicate-key', 'nonfinite', 'deep-nesting', 'oversize'])
def test_bad_json_is_safe(payload):
    with pytest.raises(LegacyValidationError, match='^(INVALID_LEGACY_DATA|SOURCE_TOO_LARGE)$'):
        validate_legacy_payload(payload, timezone_name='Asia/Shanghai')


def test_timezone_is_explicit_and_valid():
    with pytest.raises(LegacyValidationError, match='^INVALID_TIMEZONE$'):
        validate_legacy_payload(json.dumps(source()).encode(), timezone_name='Not/AZone')
