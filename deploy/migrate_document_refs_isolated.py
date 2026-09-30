"""Publish frozen History document references into an isolated revision012 copy."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import UUID

import psycopg
from psycopg import sql

from app.object_store import ObjectRef, artifact_key
from app.postgres_document_objects import PostgresDocumentObjectRepository
from deploy.inventory_paired_backup import _sha256
from deploy.migrate_files_isolated import _load_manifest, _target_identity
from deploy.migrate_relational_isolated import TABLES, _fingerprint
from deploy.migrate_report_refs_isolated import _read as _read_report_refs, _references as _report_references
from deploy.migrate_structured_isolated import _comparison, _read_auxiliary, _source, KNOWN


REVISION = '20260929_12'
EMPTY_TABLES = ('user_mutation_leases', 'import_objects', 'import_task_attempts',
                'import_user_schedule', 'vector_indexes', 'vector_generations', 'vector_heads')
TABLES_012 = KNOWN | set(EMPTY_TABLES) | {'document_objects'}


def _canonical_uuid(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, TypeError):
        return False


def _archived_path_for_record(owner, document_id, record):
    raw_path, suffix = record.get('document_path'), record.get('file_suffix')
    if not isinstance(raw_path, str) or not isinstance(suffix, str):
        raise ValueError('History document path or suffix is absent')
    path = PureWindowsPath(raw_path) if '\\' in raw_path else PurePosixPath(raw_path)
    expected = ('users', owner, 'documents', document_id + suffix)
    if (not path.is_absolute() or '..' in path.parts or tuple(path.parts[-4:]) != expected
            or path.suffix != suffix):
        raise ValueError('History document path, owner or suffix differs from archive identity')
    return 'app/' + '/'.join(expected)


def _desired(inventory, columns, baseline, structured, manifest_path, store):
    files, by_identity, by_path = [], {}, {}
    for item in inventory['files']:
        path = PurePosixPath(item['path'])
        parts = path.parts
        if len(parts) < 4 or parts[:2] != ('app', 'users'):
            raise ValueError('Invalid archived user file path')
        if parts[3] not in {'documents', 'reports'}:
            continue
        if len(parts) != 5 or not _canonical_uuid(parts[2]):
            raise ValueError('Invalid archived artifact owner or path')
        suffix = path.suffix
        artifact_id = path.name[:-len(suffix)] if suffix else ''
        key = artifact_key(parts[2], parts[3], artifact_id, suffix, item['sha256'])
        entry = {'user_id': parts[2], 'kind': parts[3], 'artifact_id': artifact_id,
                 'key': key, 'size_bytes': item['bytes'], 'sha256': item['sha256'],
                 'suffix': suffix}
        files.append(entry)
        identity = (parts[2], parts[3], artifact_id)
        if identity in by_identity:
            raise ValueError('Ambiguous archived artifact identity')
        by_identity[identity] = entry
        by_path[item['path']] = entry
    files.sort(key=lambda item: (item['user_id'], item['kind'], item['artifact_id']))
    manifest = _load_manifest(Path(manifest_path), files, _target_identity(store))
    if set(manifest['completed']) != {item['key'] for item in files}:
        raise ValueError('File migration manifest is incomplete')

    users = {row[0]: row for row in baseline['users']}
    # The archived relational baseline is checked by the caller; the snapshot
    # owner must still exist and be active before a reference is published.
    snapshots = {(owner, kind): json.loads(payload)
                 for owner, kind, _version, payload in structured['user_snapshots']}
    fences = set()
    for row in baseline['qa_deletion_fences']:
        record = dict(zip(columns['qa_deletion_fences'], row))
        if (record['target_type'] == 'document' and
                (record['status'] in {'queued', 'running'} or
                 (record['status'] == 'failed' and record['attempt_count'] < 3))):
            fences.add((record['user_id'], record['target_id']))
    desired = []
    visible_ids = set()
    for (owner, kind), payload in snapshots.items():
        if kind != 'history':
            continue
        if owner not in users or dict(zip(columns['users'], users[owner]))['status'] != 'active':
            raise ValueError('History owner is absent or inactive')
        documents = payload.get('documents')
        if not isinstance(documents, list):
            raise ValueError('History documents are invalid')
        latest = {}
        for record in documents:
            if not isinstance(record, dict) or record.get('user_id', owner) != owner:
                raise ValueError('History document owner is invalid')
            document_id = record.get('document_id')
            if not _canonical_uuid(document_id):
                raise ValueError('History document ID is invalid')
            latest[document_id] = record
        for document_id, record in latest.items():
            if (owner, document_id) in fences:
                continue
            visible_ids.add((owner, document_id))
            archived_path = _archived_path_for_record(owner, document_id, record)
            item = by_path.get(archived_path)
            if item is None:
                raise ValueError('Visible History document path has no matching archived file')
            ref = ObjectRef(**manifest['completed'][item['key']])
            store.read_verified(owner, ref)
            desired.append((owner, document_id, store.bucket, ref.key, ref.version_id,
                            ref.sha256, ref.size_bytes,
                            PostgresDocumentObjectRepository._record_hash(record)))
    # Archived bytes can outlive a History record (for example after deletion).
    # Record their disposition explicitly; no reference is published for them.
    extra_files = [
        {'user_id': item['user_id'], 'document_id': item['artifact_id'],
         'reason': ('deletion_fenced' if (item['user_id'], item['artifact_id']) in fences
                    else 'absent_from_visible_history')}
        for item in files if item['kind'] == 'documents'
        and (item['user_id'], item['artifact_id']) not in visible_ids
    ]
    return sorted(desired), extra_files


def _read(cursor):
    return cursor.execute('''select user_id,document_id,bucket,object_key,version_id,
        sha256,size_bytes,history_record_sha256 from document_objects''').fetchall()


def migrate_document_refs(archive_path, manifest_path, store, *, database_url, target_schema, mode):
    if mode not in {'dry-run', 'apply', 'verify'}:
        raise ValueError('Invalid migration mode')
    if (not target_schema.startswith('cutover_') or len(target_schema) <= 8
            or not target_schema.replace('_', '').isalnum()):
        raise ValueError('Target must be an explicit isolated cutover_ schema')
    archive_path = Path(archive_path).resolve()
    inventory, columns, baseline, structured = _source(archive_path)
    manifest_sha256 = _sha256(Path(manifest_path))
    desired, extra_files = _desired(inventory, columns, baseline, structured, manifest_path, store)
    desired_reports = _report_references(inventory, columns, baseline, manifest_path, store)
    expected_hash = _fingerprint(desired)
    if _sha256(archive_path) != inventory['source_sha256'] or _sha256(archive_path.parent / inventory['qdrant_archive']) != inventory['qdrant_sha256']:
        raise ValueError('Frozen source changed before publication')
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute('select tablename from pg_catalog.pg_tables where schemaname=%s', (target_schema,))
            if {row[0] for row in cursor.fetchall()} != TABLES_012:
                raise ValueError('Target schema table baseline mismatch')
            cursor.execute(sql.SQL('set local search_path to {}').format(sql.Identifier(target_schema)))
            cursor.execute(sql.SQL('lock table {} in access exclusive mode').format(
                sql.SQL(',').join(sql.Identifier(target_schema, table) for table in sorted(TABLES_012))))
            if cursor.execute('select version_num from alembic_version').fetchall() != [(REVISION,)]:
                raise ValueError('Target must be explicitly migrated to revision012')
            if cursor.execute('select 1 from auth_sessions limit 1').fetchone():
                raise ValueError('Target has live session state')
            for table in EMPTY_TABLES:
                if cursor.execute(sql.SQL('select 1 from {} limit 1').format(sql.Identifier(table))).fetchone():
                    raise ValueError('Target has post-baseline authority: ' + table)
            if cursor.execute('''select 1 from import_tasks where lease_version <> 0
                or claimed_by is not null or lease_token is not null
                or heartbeat_at is not null or lease_expires_at is not null
                or user_lease_token is not null or user_lease_version is not null
                limit 1''').fetchone():
                raise ValueError('Target has post-baseline authority: import_tasks lease state')
            for table in TABLES:
                query = sql.SQL('select {} from {}').format(
                    sql.SQL(',').join(map(sql.Identifier, columns[table])), sql.Identifier(table))
                if _fingerprint(cursor.execute(query).fetchall()) != _fingerprint(baseline[table]):
                    raise ValueError('Target relational baseline differs: ' + table)
            _, differences = _comparison(structured, _read_auxiliary(cursor))
            if differences:
                raise ValueError('Target structured authority differs: ' + ','.join(differences))
            if _fingerprint(_read_report_refs(cursor)) != _fingerprint(desired_reports):
                raise ValueError('Target report references differ from verified source')
            actual = _read(cursor)
            equal = _fingerprint(actual) == expected_hash
            if not equal and (actual or mode == 'verify'):
                raise ValueError('Target document references differ from verified source')
            status = 'equal' if mode == 'verify' else ('unchanged' if equal else 'ready')
            if mode == 'apply' and not equal:
                cursor.executemany('''insert into document_objects
                    (user_id,document_id,bucket,object_key,version_id,sha256,size_bytes,history_record_sha256)
                    values(%s,%s,%s,%s,%s,%s,%s,%s)''', desired)
                actual = _read(cursor)
                if _fingerprint(actual) != expected_hash:
                    raise ValueError('Post-copy document reference verification failed')
                status = 'applied'
            if _sha256(archive_path) != inventory['source_sha256'] or _sha256(archive_path.parent / inventory['qdrant_archive']) != inventory['qdrant_sha256']:
                raise ValueError('Frozen source changed during publication')
            if _sha256(Path(manifest_path)) != manifest_sha256:
                raise ValueError('File migration manifest changed during publication')
    return {'status': status, 'mode': mode, 'source_sha256': inventory['source_sha256'],
            'qdrant_sha256': inventory['qdrant_sha256'], 'target_schema': target_schema,
            'source_count': len(desired), 'target_count': len(actual),
            'extra_archived_document_files': extra_files,
            'source_references_sha256': expected_hash, 'target_references_sha256': _fingerprint(actual)}
