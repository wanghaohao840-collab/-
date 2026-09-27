from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from tests.integration.test_postgres_auth_sessions import shared_database
from app.note_repository import PostgresNoteRepository
from app.note_models import NoteIdempotencyConflict, NoteVersionConflict


@pytest.fixture
def repositories(shared_database):
    open_pool, _ = shared_database
    first, second = open_pool(), open_pool()
    with first.transaction() as cur:
        for user in ('alice', 'bob'):
            cur.execute('insert into users values (%s,%s,%s,%s,%s,%s,%s)',
                        (user, user, user, 'hash', 'active', 'now', 'now'))
    return first, PostgresNoteRepository(first), PostgresNoteRepository(second), open_pool


def test_concurrent_replay_version_and_tombstone(repositories):
    db, first, second, open_pool = repositories
    def create(repo):
        return repo.create('alice', '中文 café alpha_beta', None, ('second', 'first'), 'request')
    with ThreadPoolExecutor(2) as pool:
        notes = list(pool.map(create, (first, second)))
    assert notes[0] == notes[1]
    note = notes[0]
    assert note.tags == ('second', 'first')
    with pytest.raises(NoteIdempotencyConflict):
        first.create('alice', 'different', None, (), 'request')
    def update(repo):
        try:
            return repo.update('alice', note.id, expected_version=1,
                               body_markdown='updated', concept=None, tags=()).version
        except NoteVersionConflict:
            return 'conflict'
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(update, (first, second)))
    assert 2 in results and 'conflict' in results
    deleted = first.soft_delete('alice', note.id, expected_version=2)
    assert create(second) == deleted
    assert second.get('alice', note.id) is None
    assert PostgresNoteRepository(open_pool()).get_including_deleted('alice', note.id) == deleted
    with db.transaction() as cur:
        assert cur.execute('select count(*) as n from notes').fetchone()['n'] == 1
        assert cur.execute('select count(*) as n from note_projection_tasks').fetchone()['n'] == 3

from dataclasses import replace
from threading import Event
import psycopg

from app.database import connect, initialize_database
from app.note_models import (NewNoteSource, NoteNotFoundError, NoteSourceNotFoundError,
                             NoteSourceDeletingError, NoteValidationError)
from app.note_repository import NoteRepository, _request_digest
from app.note_persistence import NoteStore


def document_source(document_id='00000000-0000-0000-0000-000000000001'):
    return NewNoteSource('document_chunk', None, None, None, document_id,
                         {'chunk_id': 'chunk', 'chunk_index': 0, 'content_sha256': 'a' * 64},
                         'source title', 'source excerpt')


def test_search_sqlite_parity_and_tag_intersection(repositories, tmp_path):
    _, pg, _, _ = repositories
    path = tmp_path / 'local.db'
    initialize_database(path)
    with connect(path) as conn:
        for user in ('alice', 'bob'):
            conn.execute('insert into users values (?,?,?,?,?,?,?)',
                         (user, user, user, 'hash', 'active', 'now', 'now'))
    local = NoteRepository(path)
    corpus = [
        ('中文学习 笔记 café CAFÉ alpha_beta', 'Concept phrase', ('Tag B', 'Tag A')),
        ('中文 学习 cafe alpha beta_suffix', None, ('Tag A',)),
        ('prefixalpha betamax résumé naïve coöperate', '中文学习', ('Tag B',)),
        ('alpha something beta', None, ()),
        ('alpha', 'beta', ()),
        ('beta alpha', None, ()),
        ('😀!!! _ ___', None, ()),
        ('Straße STRASSE abc123 pre-token', 'fieldtwo', ('thirdfield',)),
    ]
    for repo in (local, pg):
        for i, (body, concept, tags) in enumerate(corpus):
            repo.create('alice', body, concept, tags, f'request-{i}', note_id=f'n{i}', now='2026-09-27')
        repo.create('bob', '中文学习 café alpha_beta', None, ('Tag B', 'Tag A'), 'foreign')
    queries = ('中文', '中文学', '学', '学习', '中文 学', 'CAFÉ', 'cafe', 'café',
               'rés', 'naive', 'cooperate', 'alpha_beta', 'alpha_b', 'alph_beta',
               'alpha beta', 'prefix', 'beta_alpha', '__', 'alpha __', '!!!',
               '😀', '', None, 'alpha.b', 'alpha-beta', 'Tag_A', 'tag b',
               'body concept', 'concept phrase', 'alpha_beta_suffix', 'straße', 'STRASSE',
               'third fieldtwo', 'abc123', "'; DROP TABLE notes; --", '中文 CAFÉ')
    for query in queries:
        for tags in ((), ('tag a',), ('tag a', 'tag b'), ('tag a', 'tag a')):
            expected = [n.id for n in local.list_page('alice', query=query, tags=tags).items]
            actual = [n.id for n in pg.list_page('alice', query=query, tags=tags).items]
            assert actual == expected, (query, tags, expected, actual)
    # Adjacency and final-token-only prefix have explicit positive/negative expectations.
    assert {n.id for n in pg.list_page('alice', query='alpha_b').items} == {'n0', 'n1'}
    assert not pg.list_page('alice', query='alph_beta').items
    assert not pg.list_page('alice', query='alpha __').items
    assert pg.count('alice') == len(corpus)
    assert pg.count('bob') == 1


def test_long_incompressible_token_create_search_update_parity(repositories, tmp_path):
    from random import Random
    from string import ascii_lowercase

    _, pg, _, _ = repositories
    path = tmp_path / 'long-token.db'
    initialize_database(path)
    with connect(path) as conn:
        conn.execute('insert into users values (?,?,?,?,?,?,?)',
                     ('alice', 'alice', 'alice', 'hash', 'active', 'now', 'now'))
    local = NoteRepository(path)
    random = Random(20260927)
    original = ''.join(random.choices(ascii_lowercase, k=6000))
    replacement = ''.join(random.choices(ascii_lowercase, k=6000))
    queries = (original, original[:3000], replacement, replacement[:3000])

    for repo in (local, pg):
        note = repo.create('alice', original, None, (), 'long', note_id='long-note')
        assert note.body_markdown == original
    for query in queries:
        expected = ['long-note'] if query in (original, original[:3000]) else []
        assert [n.id for n in local.list_page('alice', query=query).items] == expected
        assert [n.id for n in pg.list_page('alice', query=query).items] == expected

    for repo in (local, pg):
        note = repo.update('alice', 'long-note', expected_version=1,
                           body_markdown=replacement, concept=None, tags=())
        assert note.body_markdown == replacement and note.version == 2
    for query in queries:
        expected = ['long-note'] if query in (replacement, replacement[:3000]) else []
        assert [n.id for n in local.list_page('alice', query=query).items] == expected
        assert [n.id for n in pg.list_page('alice', query=query).items] == expected


def test_scope_pagination_activity_update_clear_and_retry(repositories):
    db, repo, second, _ = repositories
    notes = [repo.create('alice', 'searchable', None, ('z', 'a'), f'r{i}',
                         note_id=f'n{i}', now=f'2026-09-2{i}',
                         sources=(document_source(),) if i % 2 else ()) for i in range(5)]
    other = repo.create('bob', 'searchable', None, (), 'other')
    assert second.get('bob', notes[0].id) is None
    assert second.get_including_deleted('bob', notes[0].id) is None
    assert [n.id for n in second.list_page('bob', query='search').items] == [other.id]
    with pytest.raises(NoteNotFoundError):
        second.update('bob', notes[0].id, expected_version=1, body_markdown='attack', concept=None, tags=())
    with pytest.raises(NoteNotFoundError):
        second.soft_delete('bob', notes[0].id, expected_version=1)
    cursor, seen = None, []
    while True:
        page = repo.list_page('alice', limit=2, cursor=cursor)
        seen.extend(n.id for n in page.items)
        cursor = page.next_cursor
        if not cursor:
            break
    assert seen == ['n4', 'n3', 'n2', 'n1', 'n0']
    assert [n.id for n in repo.list_page('alice', source_kind='document_chunk').items] == ['n3', 'n1']
    assert repo.list_activity_dates('alice', since='2026-09-23') == ('2026-09-23', '2026-09-24')
    updated = repo.update('alice', notes[0].id, expected_version=1,
                          body_markdown='changed token', concept='concept', tags=('new',))
    assert [n.id for n in repo.list_page('alice', query='changed').items] == [updated.id]
    assert updated.id not in {n.id for n in repo.list_page('alice', query='searchable').items}
    with db.transaction() as cur:
        cur.execute("update note_projection_tasks set status='failed', attempt_count=3, last_error_code='test'")
        cur.execute("update notes set projection_state='failed'")
    assert repo.retry_failed_projections('alice', now='retry') == 5
    assert repo.retry_failed_projections('alice') == 0
    with db.transaction() as cur:
        rows = cur.execute("select status,attempt_count from note_projection_tasks where note_id='n0' order by note_version").fetchall()
        assert [r['status'] for r in rows] == ['failed', 'queued']
        assert rows[1]['attempt_count'] == 0
    assert repo.clear_all('alice') == 5
    assert repo.clear_all('alice') == 0
    assert not repo.list_page('alice', query='changed').items
    assert repo.count('alice') == 0 and repo.count('bob') == 1
    assert repo.get('bob', other.id).projection_state == 'failed'
    for limit in (0, 51, True):
        with pytest.raises(NoteValidationError):
            repo.list_page('alice', limit=limit)


def seed_qa(db):
    with db.transaction() as cur:
        cur.execute("""insert into qa_conversations
            (id,user_id,title,origin,created_at,updated_at,last_message_at)
            values ('thread','alice','title','product','now','now','now')""")
        cur.execute("insert into qa_conversation_documents values ('thread','alice','00000000-0000-0000-0000-000000000001','title',0)")
        cur.execute("""insert into qa_messages
            (id,conversation_id,user_id,turn_id,role,status,created_at,updated_at)
            values ('message','thread','alice','turn','assistant','completed','now','now')""")
        cur.execute("""insert into qa_message_sources
            (id,assistant_message_id,conversation_id,user_id,position,citation_id,
             document_id,document_name,excerpt,reference,source_type)
            values ('source','message','thread','alice',0,'citation','00000000-0000-0000-0000-000000000001','title','excerpt','ref','document')""")
    return NewNoteSource('qa_citation', 'thread', 'message', 'citation', '00000000-0000-0000-0000-000000000001',
                         {'page': 4}, 'title', 'excerpt')


def add_fence(db, *, user='alice', kind='document', target='00000000-0000-0000-0000-000000000001', status='queued'):
    with db.transaction() as cur:
        cur.execute('select id from users where id=%s for update', (user,))
        cur.execute('''insert into qa_deletion_fences
            (id,user_id,target_type,target_id,status,stage,created_at,updated_at)
            values (%s,%s,%s,%s,%s,%s,'now','now')''', (str(uuid4()), user, kind, target, status, 'fenced'))


def test_guard_ownership_completed_messages_citations_and_fences(repositories):
    db, repo, second, _ = repositories
    source = seed_qa(db)
    for invalid in (replace(source, citation_id='missing', locator=dict(source.locator)), replace(source, qa_thread_id='missing', locator=dict(source.locator)),
                    replace(source, qa_message_id='missing', locator=dict(source.locator))):
        with pytest.raises(NoteSourceNotFoundError):
            repo.create('alice', 'body', None, (), str(uuid4()), sources=(invalid,), guard_sources=True)
    with pytest.raises(NoteSourceNotFoundError):
        repo.create('bob', 'body', None, (), 'foreign', sources=(source,), guard_sources=True)
    with db.transaction() as cur:
        cur.execute("update qa_messages set status='pending'")
    with pytest.raises(NoteSourceNotFoundError):
        repo.create('alice', 'body', None, (), 'pending', sources=(source,), guard_sources=True)
    with db.transaction() as cur:
        cur.execute("update qa_messages set status='completed'")
    note = repo.create('alice', 'body', None, (), 'valid', sources=(source,), guard_sources=True)
    assert repo.list_page('alice', source_kind='qa_citation').items == (note,)
    add_fence(db, user='bob')
    assert repo.create('alice', 'body', None, (), 'other-fence', sources=(source,), guard_sources=True)
    add_fence(db)
    with pytest.raises(NoteSourceDeletingError):
        second.create('alice', 'body', None, (), 'fenced', sources=(source,), guard_sources=True)
    with pytest.raises(NoteSourceDeletingError):
        second.create('alice', 'body', None, (), 'direct-fenced', sources=(document_source(),), guard_sources=True)
    with db.transaction() as cur:
        cur.execute("update qa_deletion_fences set status='completed' where user_id='alice'")
    with pytest.raises(NoteSourceNotFoundError):
        second.create('alice', 'body', None, (), 'completed', sources=(document_source(),), guard_sources=True)
    # Idempotent replay is intentionally possible after source deletion/fencing.
    assert second.create('alice', 'body', None, (), 'valid', sources=(source,), guard_sources=True) == note
    digest = _request_digest('body', None, (), (source,))
    assert second.get_by_client_request_id('alice', 'valid', digest) == note
    assert second.get_by_client_request_id('bob', 'valid', digest) is None
    with pytest.raises(NoteIdempotencyConflict):
        second.get_by_client_request_id('alice', 'valid', 'different')


def test_caller_owned_rollback_scrub_and_atomic_projection(repositories):
    db, repo, second, _ = repositories
    qa = seed_qa(db)
    note = repo.create('alice', 'my body survives', 'concept', ('tag',), 'original',
                       sources=(qa, document_source()), guard_sources=True)
    with pytest.raises(RuntimeError, match='rollback'):
        with db.transaction() as cur:
            cur.execute("select id from users where id='alice' for update")
            created = repo.create_in_transaction(cur, 'alice', 'transient', None, (), 'rolled-back')
            assert repo.scrub_sources_in_transaction(cur, user_id='alice', document_id='00000000-0000-0000-0000-000000000001', deleted_at='later') == 1
            assert cur.execute('select count(*) as n from notes').fetchone()['n'] == 2
            raise RuntimeError('rollback')
    assert second.get('alice', created.id) is None
    assert second.get('alice', note.id) == note
    with db.transaction() as cur:
        assert cur.execute('select count(*) as n from note_projection_tasks').fetchone()['n'] == 1
        cur.execute("select id from users where id='bob' for update")
        assert repo.scrub_sources_in_transaction(cur, user_id='bob', document_id='00000000-0000-0000-0000-000000000001', deleted_at='later') == 0
    with db.transaction() as cur:
        cur.execute("select id from users where id='alice' for update")
        assert repo.scrub_sources_in_transaction(cur, user_id='alice', document_id='00000000-0000-0000-0000-000000000001', deleted_at='later') == 1
    changed = second.get('alice', note.id)
    assert changed.body_markdown == note.body_markdown and changed.tags == note.tags
    assert changed.version == 2 and all(s.deleted for s in changed.sources)
    assert all(s.document_id is None and s.excerpt_snapshot is None for s in changed.sources)
    with db.transaction() as cur:
        cur.execute("select id from users where id='alice' for update")
        assert repo.scrub_sources_in_transaction(cur, user_id='alice', document_id='00000000-0000-0000-0000-000000000001', deleted_at='again') == 0
        assert cur.execute('select count(*) as n from note_projection_tasks').fetchone()['n'] == 2
    with db.connection() as conn:
        with conn.cursor() as cur:
            with pytest.raises(ValueError, match='caller-owned'):
                repo.create_in_transaction(cur, 'alice', 'body', None, (), 'invalid')
            with pytest.raises(ValueError, match='caller-owned'):
                repo.scrub_sources_in_transaction(cur, user_id='alice', thread_id='thread', deleted_at='now')
    # Unrelated primary-key violations must not become idempotency conflicts.
    with pytest.raises(psycopg.errors.UniqueViolation):
        repo.create('alice', 'different', None, (), 'new-request', note_id=note.id)
    assert repo.count('alice') == 1


def test_frozen_page_read_during_concurrent_update(repositories, monkeypatch):
    _, repo, writer, _ = repositories
    note = repo.create('alice', 'old text', None, ('old',), 'frozen')
    selected, mutated = Event(), Event()
    original_page = NoteStore.page
    def blocked_page(store, *args, **kwargs):
        rows = original_page(store, *args, **kwargs)
        selected.set()
        assert mutated.wait(5)
        return rows
    monkeypatch.setattr(NoteStore, 'page', blocked_page)
    with ThreadPoolExecutor(1) as pool:
        result = pool.submit(repo.list_page, 'alice')
        assert selected.wait(5)
        try:
            writer.update('alice', note.id, expected_version=1, body_markdown='new text', concept='new', tags=('new',))
        finally:
            mutated.set()
        assert result.result().items == (note,)
    assert repo.get('alice', note.id).version == 2
