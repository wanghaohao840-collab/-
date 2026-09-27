"""Inspect archived per-user authorities without extraction or content disclosure.

Absent authorities are recorded, not invented. This inventory does not migrate
or reconcile them: later migration must account for every recorded authority.
"""
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
from pathlib import PurePosixPath
import sqlite3


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _invalid_constant(_value):
    raise ValueError('Snapshot contains non-finite JSON')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Snapshot contains duplicate JSON keys')
        result[key] = value
    return result


def _json(content):
    return json.loads(content, parse_constant=_invalid_constant, object_pairs_hook=_unique_object)


@contextmanager
def _database(content):
    conn = sqlite3.connect(':memory:')
    try:
        conn.deserialize(content)
        conn.execute('pragma query_only=on')
        if (conn.execute('pragma integrity_check').fetchall() != [('ok',)]
                or conn.execute('pragma foreign_key_check').fetchall()):
            raise ValueError('Archived structured database integrity failed')
        yield conn
    finally:
        conn.close()


def _episodes(content, owner):
    with _database(content) as conn:
        tables = {row[0] for row in conn.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")}
        if tables != {'documents'}:
            raise ValueError('Episode database has unsupported tables')
        columns = [row[1] for row in conn.execute('pragma table_info(documents)')]
        if columns != ['id', 'content', 'metadata', 'created_at']:
            raise ValueError('Episode database has unsupported columns')
        rows = conn.execute('select id,content,metadata,created_at from documents order by id').fetchall()
        for row in rows:
            metadata = _json(row[2])
            if not isinstance(metadata, dict) or metadata.get('user_id') != owner:
                raise ValueError('Episode record owner mismatch')
        return {'rows': len(rows), 'rows_sha256': _digest(rows)}


def inventory_authorities(database_bytes: bytes, user_files: dict[str, bytes]) -> dict:
    """Input paths are safe normalized archive paths, validated by inventory_pair."""
    with _database(database_bytes) as conn:
        users = {row[0]: {'history': None, 'memory': None, 'episodes': None}
                 for row in conn.execute('select id from users order by id')}
        tag_groups = {}
        if conn.execute("select 1 from sqlite_master where type='table' and name='note_tags'").fetchone():
            for owner, note_id, tag in conn.execute('select user_id,note_id,normalized_tag from note_tags order by rowid'):
                if owner not in users:
                    raise ValueError('Note tag owner is absent')
                tag_groups.setdefault((owner, note_id), []).append(tag)

    for name, content in sorted(user_files.items()):
        parts = PurePosixPath(name).parts
        if len(parts) < 4 or parts[:2] != ('app', 'users'):
            raise ValueError('Unsupported user authority path')
        owner = parts[2]
        if owner not in users:
            raise ValueError('User file owner is absent')
        relative = '/'.join(parts[3:])
        if parts[3] in {'documents', 'reports'}:
            continue
        if relative.endswith(('-wal', '-journal', '-shm')):
            raise ValueError('Structured authority has a journal sibling')
        evidence = {'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)}
        if relative == 'history.json':
            value = _json(content)
            fields = ('documents', 'questions', 'notes', 'sessions')
            if not isinstance(value, dict) or any(not isinstance(value.get(key), list) for key in fields):
                raise ValueError('History snapshot schema is invalid')
            evidence.update(counts={key: len(value[key]) for key in fields}, payload_sha256=_digest(value))
            users[owner]['history'] = evidence
        elif relative == 'memory/memories.json':
            value = _json(content)
            if (not isinstance(value, dict) or value.get('user_id') != owner
                    or not isinstance(value.get('memories'), list)):
                raise ValueError('Memory snapshot schema or owner is invalid')
            for item in value['memories']:
                if (not isinstance(item, dict) or not isinstance(item.get('metadata'), dict)
                        or item['metadata'].get('user_id') != owner or not isinstance(item.get('memory_type'), str)):
                    raise ValueError('Memory item schema or owner is invalid')
            evidence.update(types=dict(sorted(Counter(item['memory_type'] for item in value['memories']).items())),
                            payload_sha256=_digest(value))
            users[owner]['memory'] = evidence
        elif relative == f'memory/memory_{owner}.db':
            evidence.update(_episodes(content, owner))
            users[owner]['episodes'] = evidence
        else:
            raise ValueError('User directory contains an unsupported structured authority')
    return {'users': users, 'note_tag_order': [
        {'user_id': owner, 'note_id': note_id, 'normalized_tags': tags}
        for (owner, note_id), tags in sorted(tag_groups.items())
    ]}
