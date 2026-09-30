import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tarfile
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg.conninfo import make_conninfo

from app.postgres import PostgresDatabase
from app.postgres_document_objects import PostgresDocumentObjectRepository
from deploy.migrate_document_refs_isolated import migrate_document_refs
from deploy.migrate_document_refs_isolated import _desired
from deploy.migrate_files_isolated import migrate_files
from deploy.migrate_relational_isolated import migrate_relational
from deploy.migrate_structured_isolated import migrate_structured
from deploy.migrate_structured_isolated import _source
from tests.integration.test_relational_migration import source, target
from tests.integration.test_s3_object_store import store


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add(archive, name, content):
    entry = tarfile.TarInfo(name)
    entry.size = len(content)
    archive.addfile(entry, io.BytesIO(content))


@pytest.fixture
def prepared(source, target, store, tmp_path):
    schema, url = target
    owner, doc_a, doc_b, stale = (str(uuid4()) for _ in range(4))
    files = {doc_a: b'first document', doc_b: b'second document'}
    archived_files = {**files, stale: b'old deleted document'}
    root = tmp_path / 'source-files'
    folder = root / 'users' / owner / 'documents'
    folder.mkdir(parents=True)
    with sqlite3.connect(source) as conn:
        conn.execute('insert into users values(?,?,?,?,?,?,?)',
                     (owner, owner, owner, 'hash', 'active', 't', 't'))
    def record(document_id, name):
        return {'document_id': document_id, 'user_id': owner, 'document_name': name,
                'document_path': str(folder / f'{document_id}.pdf'), 'file_suffix': '.pdf'}
    history = {'documents': [
        record(doc_a, 'old'),
        record(doc_a, 'latest'),
        record(doc_b, 'second'),
    ], 'questions': [], 'notes': [], 'sessions': []}
    app = tmp_path / 'pair.tar.gz'
    with tarfile.open(app, 'w:gz') as archive:
        add(archive, 'app/app.db', source.read_bytes())
        add(archive, f'app/users/{owner}/history.json', json.dumps(history).encode())
        for document_id, content in archived_files.items():
            (folder / f'{document_id}.pdf').write_bytes(content)
            add(archive, f'app/users/{owner}/documents/{document_id}.pdf', content)
    qdrant = tmp_path / 'pair.qdrant.tar.gz'
    with tarfile.open(qdrant, 'w:gz') as archive:
        add(archive, 'collections/test/config.json', b'{}')
    Path(str(app) + '.meta').write_text(json.dumps({
        'archive': app.name, 'sha256': digest(app),
        'qdrant_archive': qdrant.name, 'qdrant_sha256': digest(qdrant),
    }), encoding='utf-8')
    manifest = tmp_path / 'files.json'
    migrate_files(root, store, 'apply', manifest_path=manifest)
    migrate_relational(source, expected_sha256=digest(source), database_url=url,
                       target_schema=schema, mode='apply')
    command.upgrade(Config('alembic.ini'), '20260927_06')
    migrate_structured(app, database_url=url, target_schema=schema, mode='apply')
    command.upgrade(Config('alembic.ini'), '20260929_12')
    return app, manifest, schema, url, store, owner, files, stale


def run(prepared, mode):
    app, manifest, schema, url, store, *_ = prepared
    return migrate_document_refs(app, manifest, store, database_url=url,
                                 target_schema=schema, mode=mode)


def test_latest_visible_records_publish_exact_pinned_bytes(prepared):
    app, _manifest, schema, url, store, owner, files, _stale = prepared
    before = digest(app)
    dry = run(prepared, 'dry-run')
    assert dry['status'] == 'ready'
    assert dry['extra_archived_document_files'] == [
        {'user_id': owner, 'document_id': prepared[-1], 'reason': 'absent_from_visible_history'}]
    assert run(prepared, 'apply')['status'] == 'applied'
    assert run(prepared, 'apply')['status'] == 'unchanged'
    assert run(prepared, 'verify')['status'] == 'equal'
    db = PostgresDatabase(make_conninfo(url, options=f'-csearch_path={schema}'))
    db.open()
    try:
        repo = PostgresDocumentObjectRepository(db, store)
        for document_id, content in files.items():
            assert repo.read_document_bytes(owner, document_id) == content
    finally:
        db.close()
    assert digest(app) == before


def test_changed_frozen_source_and_target_authority_fail_closed(prepared):
    app, _manifest, schema, url, _store, owner, *_ = prepared
    metadata = Path(str(app) + '.meta')
    original = metadata.read_bytes()
    try:
        parsed = json.loads(original)
        parsed['sha256'] = '0' * 64
        metadata.write_text(json.dumps(parsed))
        with pytest.raises(ValueError):
            run(prepared, 'apply')
    finally:
        metadata.write_bytes(original)
    with psycopg.connect(make_conninfo(url, options=f'-csearch_path={schema}')) as conn:
        conn.execute('''insert into user_mutation_leases
            (user_id,owner,lease_token,lease_version,heartbeat_at,lease_expires_at)
            values(%s,%s,%s,1,now(),now())''', (owner, 'test', str(uuid4())))
    with pytest.raises(ValueError, match='post-baseline authority'):
        run(prepared, 'apply')


@pytest.mark.parametrize('change', ['path', 'suffix', 'owner'])
def test_history_path_suffix_owner_must_match_archive(prepared, change):
    app, manifest, _schema, _url, store, owner, *_ = prepared
    inventory, columns, baseline, structured = _source(app)
    snapshots = structured['user_snapshots']
    index = next(i for i, row in enumerate(snapshots) if row[1] == 'history')
    row = snapshots[index]
    payload = json.loads(row[3])
    record = payload['documents'][-1]
    if change == 'path':
        record['document_path'] = str(Path(record['document_path']).with_suffix('.txt'))
    elif change == 'suffix':
        record['file_suffix'] = '.txt'
    else:
        record['document_path'] = record['document_path'].replace(owner, str(uuid4()))
    snapshots[index] = (*row[:3], json.dumps(payload))
    with pytest.raises(ValueError, match='path|suffix|owner'):
        _desired(inventory, columns, baseline, structured, manifest, store)


@pytest.mark.parametrize('change', ['bucket', 'version', 'incomplete'])
def test_manifest_or_version_mismatch_rejects_publication(prepared, change):
    _app, path, schema, url, _store, *_ = prepared
    data = json.loads(path.read_text())
    if change == 'bucket':
        data['target']['bucket'] = 'wrong'
    elif change == 'version':
        next(iter(data['completed'].values()))['version_id'] = 'missing'
    else:
        data['completed'].pop(next(iter(data['completed'])))
    path.write_text(json.dumps(data))
    with pytest.raises(Exception):
        run(prepared, 'apply')
    with psycopg.connect(make_conninfo(url, options=f'-csearch_path={schema}')) as conn:
        assert conn.execute('select count(*) from document_objects').fetchone() == (0,)


def test_partial_existing_refs_and_changed_structured_source_fail_closed(prepared):
    app, _manifest, schema, url, _store, *_ = prepared
    run(prepared, 'apply')
    with psycopg.connect(make_conninfo(url, options=f'-csearch_path={schema}')) as conn:
        conn.execute('alter table document_objects disable trigger document_object_immutable')
        conn.execute('delete from document_objects where document_id=(select min(document_id) from document_objects)')
        conn.execute('alter table document_objects enable trigger document_object_immutable')
    with pytest.raises(ValueError, match='references'):
        run(prepared, 'apply')
    with psycopg.connect(make_conninfo(url, options=f'-csearch_path={schema}')) as conn:
        conn.execute("update user_snapshots set payload=jsonb_set(payload, '{documents,0,name}', '\"tampered\"')")
    with pytest.raises(ValueError, match='structured authority'):
        run(prepared, 'verify')
    assert digest(app) == json.loads(Path(str(app) + '.meta').read_text())['sha256']


def test_wrong_revision_or_live_session_is_rejected(prepared):
    _app, _manifest, schema, url, _store, *_ = prepared
    with psycopg.connect(make_conninfo(url, options=f'-csearch_path={schema}')) as conn:
        conn.execute("update alembic_version set version_num='20260929_11'")
    with pytest.raises(ValueError, match='revision012'):
        run(prepared, 'apply')
