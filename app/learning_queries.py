"""Bounded keyset reads. Cursor data is a locator, never authorization."""
import base64
import binascii
from contextlib import closing
from datetime import date, datetime, timezone
import json
from zoneinfo import ZoneInfo

from app.database import connect
from app.learning_models import Page, LearningValidationError


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('aware timestamp required')
    return value.astimezone(timezone.utc)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate cursor key')
        result[key] = value
    return result


def _state(cursor, scope, date_key, now):
    if cursor is None:
        return {**scope, 'as_of': _utc(now).isoformat() if scope['kind'] == 'today' else None, 'last': None}
    if not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048:
        raise ValueError('invalid cursor')
    raw = base64.b64decode(cursor.encode('ascii'), altchars=b'-_', validate=True)
    state = json.loads(raw, object_pairs_hook=_unique_object)
    if not isinstance(state, dict) or set(state) != set(scope) | {'as_of', 'last'}:
        raise ValueError('invalid cursor fields')
    if type(state['v']) is not int or any(state[key] != value for key, value in scope.items()):
        raise ValueError('cursor scope mismatch')
    last = state['last']
    if not isinstance(last, list) or len(last) != 2 or any(not isinstance(x, str) or not x for x in last):
        raise ValueError('invalid cursor position')
    if date_key:
        if date.fromisoformat(last[0]).isoformat() != last[0]:
            raise ValueError('invalid date key')
    else:
        _utc(datetime.fromisoformat(last[0].replace('Z', '+00:00')))
    if scope['kind'] == 'today':
        if not isinstance(state['as_of'], str):
            raise ValueError('invalid as_of')
        instant = _utc(datetime.fromisoformat(state['as_of']))
        if not 0 <= (_utc(now) - instant).total_seconds() <= 300:
            raise ValueError('expired cursor')
    elif state['as_of'] is not None:
        raise ValueError('unexpected as_of')
    return state


def read_page(repo, user_id, *, kind, plan_id=None, bucket=None, now=None, cursor=None, limit=20):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise LearningValidationError('invalid limit')
    if kind == 'today' and bucket not in ('today', 'overdue', 'completed'):
        raise LearningValidationError('invalid bucket')
    is_plan = kind == 'plans'
    descending = is_plan or bucket == 'completed'
    key = 'created_at' if is_plan else ('completed_at' if bucket == 'completed' else 'due_date')
    scope = dict(v=1, user_id=user_id, kind=kind, plan_id=plan_id, bucket=bucket)
    try:
        state = _state(cursor, scope, key == 'due_date', now)
    except (ValueError, TypeError, UnicodeError, binascii.Error, RecursionError):
        raise LearningValidationError('invalid or expired cursor') from None
    alias = 'p' if is_plan else 't'
    clauses = [f'{alias}.user_id=?', '''not exists (select 1 from qa_deletion_fences f
               where f.user_id=d.user_id and f.target_type='document' and f.target_id=d.document_id)''']
    params = [user_id]
    if kind == 'tasks':
        clauses.append('t.plan_id=?')
        params.append(plan_id)
    if kind == 'today':
        if bucket == 'completed':
            clauses.append('t.completed=1')
        else:
            clauses.extend(['t.completed=0', 't.due_date ' + ('=' if bucket == 'today' else '<') + ' learning_day(p.timezone)'])
    if state['last']:
        clauses.append(f'({alias}.{key},{alias}.id) ' + ('<' if descending else '>') + ' (?,?)')
        params.extend(state['last'])
    params.append(limit + 1)
    joins = 'learning_plans p join learning_plan_documents d on d.user_id=p.user_id and d.plan_id=p.id'
    if not is_plan:
        joins += ' join learning_tasks t on t.user_id=p.user_id and t.plan_id=p.id'
    order = 'desc' if descending else 'asc'
    sql = f'select {alias}.id,{alias}.{key} sort_key from {joins} where ' + ' and '.join(clauses)
    sql += f' order by {alias}.{key} {order},{alias}.id {order} limit ?'
    with closing(connect(repo.db_path)) as conn:
        conn.execute('begin')
        if kind == 'tasks':
            repo._plan(conn, user_id, plan_id)
        if kind == 'today':
            instant = datetime.fromisoformat(state['as_of'])
            conn.create_function('learning_day', 1, lambda zone: instant.astimezone(ZoneInfo(zone)).date().isoformat(), deterministic=True)
        rows = conn.execute(sql, params).fetchall()
        selected = rows[:limit]
        reader = repo._plan if is_plan else repo._task
        items = tuple(reader(conn, user_id, row['id']) for row in selected)
        next_cursor = None
        if len(rows) > limit:
            state['last'] = [selected[-1]['sort_key'], selected[-1]['id']]
            next_cursor = base64.urlsafe_b64encode(json.dumps(state, separators=(',', ':')).encode()).decode()
        return Page(items, next_cursor)
