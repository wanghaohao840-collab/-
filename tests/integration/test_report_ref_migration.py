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
from botocore.exceptions import ClientError
from psycopg.conninfo import make_conninfo

from app.postgres import PostgresDatabase
from app.object_store import ObjectIntegrityError
from app.postgres_reports import PostgresReportService
from deploy.migrate_files_isolated import migrate_files
from deploy.migrate_relational_isolated import migrate_relational
from deploy.migrate_report_refs_isolated import migrate_report_refs
from tests.integration.test_relational_migration import target, source
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
    owner = str(uuid4())
    files = {str(uuid4()): '# 中文报告一\n'.encode(), str(uuid4()): b'# second\r\n'}
    root = tmp_path / 'source-files'
    folder = root / 'users' / owner / 'reports'
    folder.mkdir(parents=True)
    with sqlite3.connect(source) as conn:
        conn.execute('insert into users values(?,?,?,?,?,?,?)', (owner, owner, owner, 'hash', 'active', 't', 't'))
        for report_id, content in files.items():
            (folder / f'{report_id}.md').write_bytes(content)
            conn.execute('insert into report_records values(?,?,?,?,?)', (report_id, owner, 'title', f'reports/{report_id}.md', '2026-09-28'))
    app = tmp_path / 'pair.tar.gz'
    with tarfile.open(app, 'w:gz') as archive:
        add(archive, 'app/app.db', source.read_bytes())
        for report_id, content in files.items():
            add(archive, f'app/users/{owner}/reports/{report_id}.md', content)
    qdrant = tmp_path / 'pair.qdrant.tar.gz'
    with tarfile.open(qdrant, 'w:gz') as archive:
        add(archive, 'collections/test/config.json', b'{}')
    Path(str(app) + '.meta').write_text(json.dumps({'archive': app.name, 'sha256': digest(app),
        'qdrant_archive': qdrant.name, 'qdrant_sha256': digest(qdrant)}), encoding='utf-8')
    manifest = tmp_path / 'files.json'
    migrate_files(root, store, 'apply', manifest_path=manifest)
    migrate_relational(source, expected_sha256=digest(source), database_url=url, target_schema=schema, mode='apply')
    command.upgrade(Config('alembic.ini'), '20260927_06')
    return app, manifest, schema, url, store, owner, files


def run(prepared, mode):
    app, manifest, schema, url, store, *_ = prepared
    return migrate_report_refs(app, manifest, store, database_url=url, target_schema=schema, mode=mode)


def test_migrated_reports_read_exact_pinned_bytes(prepared):
    app, _manifest, schema, url, store, owner, files = prepared
    before = digest(app)
    assert run(prepared, 'dry-run')['status'] == 'ready'
    assert run(prepared, 'apply')['status'] == 'applied'
    assert run(prepared, 'apply')['status'] == 'unchanged'
    assert run(prepared, 'verify')['status'] == 'equal'
    db = PostgresDatabase(make_conninfo(url, options=f'-csearch_path={schema}'))
    db.open()
    try:
        service = PostgresReportService(db, store)
        assert {r.id for r in service.list_reports(owner)} == set(files)
        for report_id, content in files.items():
            assert service.read_report_bytes(owner, report_id) == content
    finally:
        db.close()
    assert digest(app) == before


@pytest.mark.parametrize('change', ['bucket', 'endpoint', 'version', 'hash', 'incomplete'])
def test_manifest_identity_and_incomplete_versions_fail_before_publication(prepared, change):
    _app, path, schema, url, _store, _owner, _files = prepared
    data = json.loads(path.read_text())
    if change in {'bucket', 'endpoint'}:
        data['target'][change] = 'different'
    elif change == 'version':
        next(iter(data['completed'].values()))['version_id'] = 'missing-version'
    elif change == 'hash':
        data['files'][0]['sha256'] = '0' * 64
    else:
        data['completed'].pop(next(iter(data['completed'])))
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises((ValueError, ClientError)):
        run(prepared, 'apply')
    with psycopg.connect(make_conninfo(url, options=f'-csearch_path={schema}')) as conn:
        assert conn.execute('select count(*) from report_objects').fetchone() == (0,)


def test_partial_refs_rejected_and_publication_failure_rolls_back(prepared):
    _app, _path, schema, url, _store, _owner, files = prepared
    with psycopg.connect(make_conninfo(url, options=f'-csearch_path={schema}')) as conn:
        conn.execute('''create function reject_reference() returns trigger language plpgsql as $$
            begin if NEW.report_id = (select max(id) from report_records) then
            raise exception 'injected second reference failure'; end if; return NEW; end; $$''')
        conn.execute('create trigger reject_reference before insert on report_objects for each row execute function reject_reference()')
    with pytest.raises(psycopg.errors.RaiseException, match='injected second reference'):
        run(prepared, 'apply')
    with psycopg.connect(make_conninfo(url, options=f'-csearch_path={schema}')) as conn:
        assert conn.execute('select count(*) from report_objects').fetchone() == (0,)
        conn.execute('drop trigger reject_reference on report_objects')
    run(prepared, 'apply')
    with psycopg.connect(make_conninfo(url, options=f'-csearch_path={schema}')) as conn:
        conn.execute('delete from report_objects where report_id=%s', (min(files),))
    with pytest.raises(ValueError, match='reference'):
        run(prepared, 'apply')
    with psycopg.connect(make_conninfo(url, options=f'-csearch_path={schema}')) as conn:
        assert conn.execute('select count(*) from report_objects').fetchone() == (1,)


def test_changed_target_metadata_and_conflicting_reference_are_preserved(prepared):
    _app, _path, schema, url, _store, _owner, _files = prepared
    with psycopg.connect(make_conninfo(url, options=f'-csearch_path={schema}')) as conn:
        conn.execute("update report_records set title='changed'")
    for mode in ('dry-run', 'apply', 'verify'):
        with pytest.raises(ValueError, match='relational baseline'):
            run(prepared, mode)
    with psycopg.connect(make_conninfo(url, options=f'-csearch_path={schema}')) as conn:
        assert conn.execute('select count(*) from report_objects').fetchone() == (0,)
        assert conn.execute('select distinct title from report_records').fetchall() == [('changed',)]
        conn.execute("update report_records set title='title'")
    run(prepared, 'apply')
    with psycopg.connect(make_conninfo(url, options=f'-csearch_path={schema}')) as conn:
        conn.execute("update report_objects set version_id='conflicting'")
    for mode in ('dry-run', 'apply', 'verify'):
        with pytest.raises(ValueError, match='references'):
            run(prepared, mode)
    with psycopg.connect(make_conninfo(url, options=f'-csearch_path={schema}')) as conn:
        assert conn.execute('select distinct version_id from report_objects').fetchall() == [('conflicting',)]


def test_corrupt_pinned_object_stops_all_reference_publication(prepared):
    _app, path, schema, url, store, _owner, _files = prepared
    data = json.loads(path.read_text())
    key = next(iter(data['completed']))
    bad = store.client.put_object(Bucket=store.bucket, Key=key, Body=b'corrupt bytes')
    data['completed'][key]['version_id'] = bad['VersionId']
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ObjectIntegrityError, match='bytes do not match'):
        run(prepared, 'apply')
    with psycopg.connect(make_conninfo(url, options=f'-csearch_path={schema}')) as conn:
        assert conn.execute('select count(*) from report_objects').fetchone() == (0,)
