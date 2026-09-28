"""Publish verified historical report references on a stopped isolated copy.

The paired archive and completed file manifest must remain frozen. This helper
does not upload objects, change source files, or enable a distributed runtime.
"""
from pathlib import Path, PurePosixPath

import psycopg
from psycopg import sql

from app.object_store import ObjectRef, artifact_key
from deploy.migrate_files_isolated import _load_manifest, _target_identity
from deploy.migrate_relational_isolated import TABLES, _fingerprint
from deploy.migrate_structured_isolated import KNOWN, REVISION, _source


def _references(inventory, columns, baseline, manifest_path, store):
    files, by_path = [], {}
    for item in inventory['files']:
        path = PurePosixPath(item['path'])
        parts = path.parts
        if len(parts) < 4 or parts[3] not in {'documents', 'reports'}:
            continue
        if len(parts) != 5 or parts[:2] != ('app', 'users'):
            raise ValueError('Unsupported archived artifact path')
        owner, kind = parts[2:4]
        entry = {
            'user_id': owner, 'kind': kind, 'artifact_id': path.stem,
            'key': artifact_key(owner, kind, path.stem, path.suffix, item['sha256']),
            'size_bytes': item['bytes'], 'sha256': item['sha256'], 'suffix': path.suffix,
        }
        files.append(entry)
        by_path[item['path']] = entry
    files.sort(key=lambda item: (item['user_id'], item['kind'], item['artifact_id']))
    manifest = _load_manifest(Path(manifest_path), files, _target_identity(store))
    if set(manifest['completed']) != {item['key'] for item in files}:
        raise ValueError('File migration manifest is incomplete')

    desired = []
    for values in baseline['report_records']:
        row = dict(zip(columns['report_records'], values))
        item = by_path.get(f"app/users/{row['user_id']}/{row['relative_path']}")
        if item is None or item['kind'] != 'reports' or item['artifact_id'] != row['id']:
            raise ValueError('Archived report metadata has no matching report file')
        ref = ObjectRef(**manifest['completed'][item['key']])
        # Network verification is completed before opening the publication transaction.
        store.read_verified(row['user_id'], ref)
        desired.append((row['id'], row['user_id'], store.bucket, ref.key,
                        ref.version_id, ref.sha256, ref.size_bytes))
    return sorted(desired)


def _read(cursor):
    return cursor.execute('''select report_id,user_id,bucket,object_key,
        version_id,sha256,size_bytes from report_objects''').fetchall()


def migrate_report_refs(archive_path, manifest_path, store, *, database_url, target_schema, mode):
    if mode not in {'dry-run', 'apply', 'verify'}:
        raise ValueError('Invalid migration mode')
    if (not target_schema.startswith('cutover_') or len(target_schema) <= 8
            or not target_schema.replace('_', '').isalnum()):
        raise ValueError('Target must be an explicit isolated cutover_ schema')
    inventory, columns, baseline, _ = _source(Path(archive_path).resolve())
    desired = _references(inventory, columns, baseline, manifest_path, store)
    expected_hash = _fingerprint(desired)
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute('select tablename from pg_catalog.pg_tables where schemaname=%s', (target_schema,))
            if {row[0] for row in cursor.fetchall()} != KNOWN:
                raise ValueError('Target schema table baseline mismatch')
            cursor.execute(sql.SQL('set local search_path to {}').format(sql.Identifier(target_schema)))
            cursor.execute(sql.SQL('lock table {} in access exclusive mode').format(
                sql.SQL(',').join(sql.Identifier(target_schema, table) for table in sorted(KNOWN))))
            if cursor.execute('select version_num from alembic_version').fetchall() != [(REVISION,)]:
                raise ValueError('Target must be explicitly migrated to revision006')
            if cursor.execute('select 1 from auth_sessions limit 1').fetchone():
                raise ValueError('Target has live session state; offline target required')
            for table in TABLES:
                query = sql.SQL('select {} from {}').format(
                    sql.SQL(',').join(map(sql.Identifier, columns[table])), sql.Identifier(table))
                if _fingerprint(cursor.execute(query).fetchall()) != _fingerprint(baseline[table]):
                    raise ValueError('Target relational baseline differs: ' + table)
            actual = _read(cursor)
            equal = _fingerprint(actual) == expected_hash
            if not equal and (actual or mode == 'verify'):
                raise ValueError('Target report references differ from verified source')
            status = 'equal' if mode == 'verify' else ('unchanged' if equal else 'ready')
            if mode == 'apply' and not equal:
                cursor.executemany('''insert into report_objects
                    (report_id,user_id,bucket,object_key,version_id,sha256,size_bytes)
                    values(%s,%s,%s,%s,%s,%s,%s)''', desired)
                actual = _read(cursor)
                if _fingerprint(actual) != expected_hash:
                    raise ValueError('Post-copy report reference verification failed')
                status = 'applied'
    return {'status': status, 'mode': mode, 'source_sha256': inventory['source_sha256'],
            'qdrant_sha256': inventory['qdrant_sha256'], 'target_schema': target_schema,
            'source_count': len(desired), 'target_count': len(actual),
            'source_references_sha256': expected_hash, 'target_references_sha256': _fingerprint(actual)}
