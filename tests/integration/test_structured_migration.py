import hashlib
import io
import json
import os
import sqlite3
import tarfile

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app.note_repository import PostgresNoteRepository
from app.postgres import PostgresDatabase
from app.postgres_memory_documents import PostgresMemoryDocumentStore
from app.postgres_snapshots import PostgresSnapshotRepository
from deploy.migrate_relational_isolated import migrate_relational
from deploy.migrate_structured_isolated import migrate_structured, StructuredMigrationError
from deploy.migrate_structured_isolated import main
from tests.integration.test_relational_migration import target, source


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add(archive, name, content):
    entry = tarfile.TarInfo(name)
    entry.size = len(content)
    archive.addfile(entry, io.BytesIO(content))


@pytest.fixture
def prepared(source, target, tmp_path):
    schema, url = target
    with sqlite3.connect(source) as conn:
        conn.execute("update notes set body_markdown='中文 café alpha_beta' where id='n1'")
        conn.executemany('insert into note_tags values(?,?,?,?)',
                         [('u1', 'n1', 'z', 'Z'), ('u1', 'n1', 'a', 'A')])
    with sqlite3.connect(':memory:') as conn:
        conn.execute('create table documents(id text primary key,content text,metadata text,created_at text)')
        raw = '{ "user_id": "u1", "nested": [3,1] }'
        conn.execute('insert into documents values(?,?,?,?)', ('m', 'episode', raw, '2026-01-01 01:02:03'))
        episode_bytes = conn.serialize()
    app = tmp_path / 'pair.tar.gz'
    with tarfile.open(app, 'w:gz') as archive:
        add(archive, 'app/app.db', source.read_bytes())
        add(archive, 'app/users/u1/history.json', json.dumps({'documents': [{'document_id': 'd', 'user_id': 'u1'}], 'questions': [], 'notes': [], 'sessions': []}).encode())
        add(archive, 'app/users/u1/memory/memories.json', json.dumps({'user_id': 'u1', 'memories': [{'id': 'm', 'memory_type': 'episodic', 'metadata': {'user_id': 'u1'}}]}).encode())
        add(archive, 'app/users/u1/memory/memory_u1.db', episode_bytes)
    qdrant = tmp_path / 'pair.qdrant.tar.gz'
    with tarfile.open(qdrant, 'w:gz') as archive:
        add(archive, 'collections/test/config.json', b'{}')
    (tmp_path / 'pair.tar.gz.meta').write_text(json.dumps({
        'archive': app.name, 'sha256': digest(app), 'qdrant_archive': qdrant.name, 'qdrant_sha256': digest(qdrant)
    }), encoding='utf-8')
    migrate_relational(source, expected_sha256=digest(source), database_url=url, target_schema=schema, mode='apply')
    command.upgrade(Config('alembic.ini'), '20260927_06')
    return app, schema, url


def run(prepared, mode):
    archive, schema, url = prepared
    return migrate_structured(archive, database_url=url, target_schema=schema, mode=mode)


def test_all_scoped_authorities_copy_and_replay_without_source_changes(prepared):
    archive, schema, url = prepared
    before = digest(archive)
    assert run(prepared, 'dry-run')['status'] == 'ready'
    assert run(prepared, 'apply')['status'] == 'applied'
    assert run(prepared, 'apply')['status'] == 'unchanged'
    assert run(prepared, 'verify')['status'] == 'equal'
    assert digest(archive) == before
    db = PostgresDatabase(make_conninfo(url, options=f'-csearch_path={schema}'))
    db.open()
    try:
        assert PostgresSnapshotRepository(db).read('u1', 'history').data['documents'][0]['document_id'] == 'd'
        assert PostgresSnapshotRepository(db).read('u1', 'memory').version == 1
        assert PostgresMemoryDocumentStore(db, 'u1').get_document('m')['metadata'] == '{ "user_id": "u1", "nested": [3,1] }'
        note = PostgresNoteRepository(db).list_page('u1', query='中文 cafe alpha_b').items[0]
        assert note.id == 'n1' and note.tags == ('Z', 'A')
    finally:
        db.close()


def test_different_baseline_is_not_overwritten(prepared):
    _archive, schema, url = prepared
    with psycopg.connect(make_conninfo(url, options=f'-csearch_path={schema}')) as conn:
        conn.execute("update notes set body_markdown='new target write' where id='n1'")
    with pytest.raises(StructuredMigrationError, match='baseline'):
        run(prepared, 'apply')
    with psycopg.connect(make_conninfo(url, options=f'-csearch_path={schema}')) as conn:
        assert conn.execute('select count(*) from user_snapshots').fetchone() == (0,)
        assert conn.execute("select body_markdown from notes where id='n1'").fetchone() == ('new target write',)


def test_target_changes_are_rejected_after_publication(prepared):
    _archive, schema, url = prepared
    run(prepared, 'apply')
    with psycopg.connect(make_conninfo(url, options=f'-csearch_path={schema}')) as conn:
        conn.execute("update memory_documents set content='new write'")
    for mode in ('apply', 'verify'):
        with pytest.raises(StructuredMigrationError, match='authority'):
            run(prepared, mode)


def test_one_transaction_rolls_back_all_authorities(prepared):
    _archive, schema, url = prepared
    with psycopg.connect(make_conninfo(url, options=f'-csearch_path={schema}')) as conn:
        conn.execute('''create function reject_episode() returns trigger language plpgsql as $$
            begin raise exception 'injected episode failure'; end; $$''')
        conn.execute('create trigger reject_episode before insert on memory_documents for each row execute function reject_episode()')
    with pytest.raises(psycopg.errors.RaiseException, match='injected episode failure'):
        run(prepared, 'apply')
    with psycopg.connect(make_conninfo(url, options=f'-csearch_path={schema}')) as conn:
        for table in ('user_snapshots', 'memory_documents', 'note_search_tokens'):
            assert conn.execute(sql.SQL('select count(*) from {}').format(sql.Identifier(table))).fetchone() == (0,)
        assert conn.execute('select position from note_tags').fetchall() == [(None,), (None,)]


def test_cli_cannot_overwrite_frozen_backup_or_sidecar(tmp_path, monkeypatch):
    archive = tmp_path / 'pair.tar.gz'
    archive.write_bytes(b'frozen archive')
    before = digest(archive)
    monkeypatch.setenv('CUTOVER_TEST_DATABASE_URL', 'unused-protected-before-connect')
    for evidence in (archive, tmp_path / 'pair.tar.gz.meta', tmp_path / 'new-evidence.json'):
        monkeypatch.setattr('sys.argv', ['migration', str(archive), '--target-schema', 'cutover_test',
                                      '--mode', 'apply', '--evidence', str(evidence)])
        with pytest.raises(ValueError, match='outside source_root'):
            main()
    assert digest(archive) == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ['pair.tar.gz']


def test_cli_replaces_hardlink_evidence_without_truncating_backup(tmp_path, monkeypatch):
    backup = tmp_path / 'backup'
    backup.mkdir()
    archive = backup / 'pair.tar.gz'
    archive.write_bytes(b'frozen backup bytes')
    output = tmp_path / 'evidence.json'
    os.link(archive, output)
    monkeypatch.setenv('CUTOVER_TEST_DATABASE_URL', 'unused-test')
    monkeypatch.setattr('deploy.migrate_structured_isolated.migrate_structured', lambda *_args, **_kwargs: {'status': 'equal'})
    monkeypatch.setattr('sys.argv', ['migration', str(archive), '--target-schema', 'cutover_test',
                                  '--mode', 'verify', '--evidence', str(output)])
    assert main() == 0
    assert archive.read_bytes() == b'frozen backup bytes'
    assert json.loads(output.read_text()) == {'status': 'equal'}
    assert not os.path.samefile(archive, output)


def test_initial_partial_authority_is_rejected_and_preserved(prepared):
    _archive, schema, url = prepared
    with psycopg.connect(make_conninfo(url, options=f'-csearch_path={schema}')) as conn:
        conn.execute('''insert into user_snapshots(user_id,kind,version,payload,updated_at)
            values('u1','memory',1,%s::jsonb,clock_timestamp())''',
            (json.dumps({'user_id': 'u1', 'memories': [{'id': 'm', 'memory_type': 'episodic', 'metadata': {'user_id': 'u1'}}]}),))
    for mode in ('dry-run', 'apply', 'verify'):
        with pytest.raises(StructuredMigrationError, match='authority'):
            run(prepared, mode)
    with psycopg.connect(make_conninfo(url, options=f'-csearch_path={schema}')) as conn:
        assert conn.execute('select kind,version from user_snapshots').fetchall() == [('memory', 1)]
        assert conn.execute('select count(*) from memory_documents').fetchone() == (0,)
        assert conn.execute('select position from note_tags').fetchall() == [(None,), (None,)]


def test_cli_failed_evidence_replace_preserves_backup_and_cleans_owned_temp(tmp_path, monkeypatch):
    backup = tmp_path / 'backup'
    backup.mkdir()
    archive = backup / 'pair.tar.gz'
    archive.write_bytes(b'frozen backup bytes')
    output = tmp_path / 'evidence.json'
    os.link(archive, output)
    unrelated = tmp_path / '.evidence.json.unrelated.tmp'
    unrelated.write_bytes(b'leave this file alone')
    monkeypatch.setenv('CUTOVER_TEST_DATABASE_URL', 'unused-test')
    monkeypatch.setattr('deploy.migrate_structured_isolated.migrate_structured', lambda *_args, **_kwargs: {'status': 'equal'})
    monkeypatch.setattr('sys.argv', ['migration', str(archive), '--target-schema', 'cutover_test',
                                  '--mode', 'verify', '--evidence', str(output)])
    def reject_replace(source, destination):
        assert source.parent == output.parent
        assert source != output and destination == output
        assert json.loads(source.read_text()) == {'status': 'equal'}
        raise OSError('injected replace failure')
    monkeypatch.setattr('deploy.migrate_structured_isolated.os.replace', reject_replace)
    with pytest.raises(OSError, match='injected replace failure'):
        main()
    assert archive.read_bytes() == output.read_bytes() == b'frozen backup bytes'
    assert os.path.samefile(archive, output)
    assert unrelated.read_bytes() == b'leave this file alone'
    assert sorted(path.name for path in tmp_path.iterdir()) == [unrelated.name, 'backup', 'evidence.json']
