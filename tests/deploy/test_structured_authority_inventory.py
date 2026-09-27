import json
import sqlite3

import pytest

from deploy.structured_authority_inventory import inventory_authorities


def database():
    with sqlite3.connect(':memory:') as conn:
        conn.executescript('''create table users(id text primary key);
            insert into users values('u');
            create table note_tags(user_id text,note_id text,normalized_tag text,display_tag text);
            insert into note_tags values('u','n','z','Z'),('u','n','a','A');''')
        return conn.serialize()


def files():
    with sqlite3.connect(':memory:') as conn:
        conn.execute('create table documents(id text primary key, content text,metadata text,created_at text)')
        conn.execute('insert into documents values(?,?,?,?)',
                     ('m', 'private-body', json.dumps({'user_id': 'u'}), '2026-01-01'))
        episode = conn.serialize()
    return {
        'app/users/u/history.json': json.dumps({'documents': [{'document_id': 'd'}], 'questions': [], 'notes': [], 'sessions': []}).encode(),
        'app/users/u/memory/memories.json': json.dumps({'user_id': 'u', 'memories': [{'id': 'm', 'memory_type': 'episodic', 'metadata': {'user_id': 'u'}}]}).encode(),
        'app/users/u/memory/memory_u.db': episode,
    }


def test_inventory_preserves_tag_order_and_covers_each_authority_without_content():
    result = inventory_authorities(database(), files())
    assert result['users']['u']['history']['counts']['documents'] == 1
    assert result['users']['u']['memory']['types'] == {'episodic': 1}
    assert result['users']['u']['episodes']['rows'] == 1
    assert result['note_tag_order'] == [{'user_id': 'u', 'note_id': 'n', 'normalized_tags': ['z', 'a']}]
    assert 'private-body' not in json.dumps(result)


@pytest.mark.parametrize('bad', [
    {'user_id': 'other', 'memories': []},
    {'user_id': 'u', 'memories': [{'metadata': {'user_id': 'other'}}]},
    {'user_id': 'u', 'memories': [], 'extra': float('nan')},
])
def test_inventory_rejects_invalid_memory(bad):
    source = files()
    source['app/users/u/memory/memories.json'] = json.dumps(bad).encode()
    with pytest.raises(ValueError):
        inventory_authorities(database(), source)


def test_inventory_refuses_orphan_owner_and_live_episode_journal():
    source = files()
    source['app/users/missing/history.json'] = source['app/users/u/history.json']
    with pytest.raises(ValueError, match='owner'):
        inventory_authorities(database(), source)
    source = files()
    source['app/users/u/memory/memory_u.db-wal'] = b''
    with pytest.raises(ValueError, match='journal'):
        inventory_authorities(database(), source)


def test_inventory_reports_missing_authorities_without_synthesizing_them():
    result = inventory_authorities(database(), {})
    assert result['users']['u'] == {'history': None, 'memory': None, 'episodes': None}


def test_inventory_refuses_unknown_structured_file():
    source = files()
    source['app/users/u/memory/extra.db'] = source['app/users/u/memory/memory_u.db']
    with pytest.raises(ValueError, match='unsupported'):
        inventory_authorities(database(), source)
