"""Offline History/Memory/episode/Notes-derived migration into revision006.

Source archives must remain frozen and target applications must remain stopped.
This covers structured user authorities, not object references or RAG manifests.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import sys
import tarfile

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from app.note_tokenizer import tokenize_fields
from deploy.inventory_paired_backup import inventory_pair, _safe_name, _sha256
from deploy.migrate_relational_isolated import TABLES, FTS_TABLES, _fingerprint
from deploy.migrate_files_isolated import _ensure_manifest_outside_source
from deploy.structured_authority_inventory import _database, _json


REVISION = '20260927_06'
AUXILIARY = ('user_snapshots', 'memory_documents', 'note_search_tokens')
KNOWN = set(TABLES) | set(AUXILIARY) | {'alembic_version', 'auth_sessions', 'report_objects'}


class StructuredMigrationError(ValueError):
    pass


def _payload(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _source(archive_path):
    inventory = inventory_pair(archive_path)
    if set(inventory['tables']) != set(TABLES) | FTS_TABLES:
        raise StructuredMigrationError('Source business table baseline mismatch')
    contents = {}
    with tarfile.open(archive_path, 'r:gz') as archive:
        for member in archive:
            name = _safe_name(member)
            parts = PurePosixPath(name).parts
            if member.isfile() and (name == 'app/app.db' or
                    (len(parts) >= 4 and parts[:2] == ('app', 'users') and parts[3] not in {'documents', 'reports'})):
                with archive.extractfile(member) as stream:
                    contents[name] = stream.read()
    if (_sha256(archive_path) != inventory['source_sha256'] or
            _sha256(archive_path.parent / inventory['qdrant_archive']) != inventory['qdrant_sha256']):
        raise StructuredMigrationError('Source archive changed during inspection')

    desired = {key: [] for key in (*AUXILIARY, 'note_tag_positions')}
    for owner, authorities in inventory['structured_authorities']['users'].items():
        for kind, relative in (('history', 'history.json'), ('memory', 'memory/memories.json')):
            if authorities[kind] is not None:
                desired['user_snapshots'].append((owner, kind, 1, _payload(_json(contents[f'app/users/{owner}/{relative}']))))
        if authorities['episodes'] is not None:
            with _database(contents[f'app/users/{owner}/memory/memory_{owner}.db']) as conn:
                for identifier, content, metadata, created in conn.execute('select id,content,metadata,created_at from documents'):
                    if not isinstance(identifier, str) or not identifier or not isinstance(content, str) or not isinstance(metadata, str):
                        raise StructuredMigrationError('Episode row is incompatible with target')
                    desired['memory_documents'].append((owner, identifier, content, metadata, created))

    columns, baseline = {}, {}
    with _database(contents['app/app.db']) as conn:
        for table in TABLES:
            columns[table] = tuple(row[1] for row in conn.execute(f'pragma table_info("{table}")'))
            baseline[table] = conn.execute(f'select * from "{table}"').fetchall()
        tags = {}
        for owner, note, normalized in conn.execute('select user_id,note_id,normalized_tag from note_tags order by rowid'):
            group = tags.setdefault((owner, note), [])
            desired['note_tag_positions'].append((owner, note, normalized, len(group)))
            group.append(normalized)
        for owner, note, body, concept in conn.execute('select user_id,id,body_markdown,concept from notes where deleted_at is null'):
            desired['note_search_tokens'].extend((owner, note, field, position, token)
                for field, position, token in tokenize_fields(body, concept or '', ' '.join(tags.get((owner, note), []))))
    return inventory, columns, baseline, desired


def _read_auxiliary(cursor):
    snapshots = cursor.execute('select user_id,kind,version,payload from user_snapshots').fetchall()
    return {
        'user_snapshots': [(user, kind, version, _payload(data)) for user, kind, version, data in snapshots],
        'memory_documents': cursor.execute('select user_id,document_id,content,metadata,created_at from memory_documents').fetchall(),
        'note_search_tokens': cursor.execute('select user_id,note_id,field,position,token from note_search_tokens').fetchall(),
        'note_tag_positions': cursor.execute('select user_id,note_id,normalized_tag,position from note_tags').fetchall(),
    }


def _comparison(desired, actual):
    result = {}
    for table in desired:
        result[table] = {'source_count': len(desired[table]), 'target_count': len(actual[table]),
                         'source_sha256': _fingerprint(desired[table]), 'target_sha256': _fingerprint(actual[table])}
    return result, [table for table, values in result.items() if values['source_sha256'] != values['target_sha256']]


def migrate_structured(archive_path: Path, *, database_url: str, target_schema: str, mode: str) -> dict:
    if mode not in {'dry-run', 'apply', 'verify'}:
        raise StructuredMigrationError('Invalid migration mode')
    if (not target_schema.startswith('cutover_') or len(target_schema) <= 8
            or not target_schema.replace('_', '').isalnum()):
        raise StructuredMigrationError('Target must be an explicit isolated cutover_ schema')
    inventory, columns, baseline, desired = _source(Path(archive_path).resolve())
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute('select tablename from pg_catalog.pg_tables where schemaname=%s', (target_schema,))
            if {row[0] for row in cursor.fetchall()} != KNOWN:
                raise StructuredMigrationError('Target schema table baseline mismatch')
            cursor.execute(sql.SQL('set local search_path to {}').format(sql.Identifier(target_schema)))
            cursor.execute(sql.SQL('lock table {} in access exclusive mode').format(
                sql.SQL(',').join(sql.Identifier(target_schema, table) for table in sorted(KNOWN))))
            if cursor.execute('select version_num from alembic_version').fetchall() != [(REVISION,)]:
                raise StructuredMigrationError('Target must be explicitly migrated to revision006')
            if cursor.execute('select 1 from auth_sessions limit 1').fetchone():
                raise StructuredMigrationError('Target has live session state; offline target required')
            for table in TABLES:
                query = sql.SQL('select {} from {}').format(
                    sql.SQL(',').join(map(sql.Identifier, columns[table])), sql.Identifier(table))
                if _fingerprint(cursor.execute(query).fetchall()) != _fingerprint(baseline[table]):
                    raise StructuredMigrationError('Target relational baseline differs: ' + table)
            actual = _read_auxiliary(cursor)
            tables, differences = _comparison(desired, actual)
            empty = all(not actual[table] for table in AUXILIARY) and all(row[3] is None for row in actual['note_tag_positions'])
            if differences and (not empty or mode == 'verify'):
                raise StructuredMigrationError('Target structured authority differs: ' + ','.join(differences))
            status = 'equal' if mode == 'verify' else ('unchanged' if not differences else 'ready')
            if mode == 'apply' and differences:
                cursor.executemany('insert into user_snapshots(user_id,kind,version,payload,updated_at) values(%s,%s,%s,%s,clock_timestamp())',
                                   [(owner, kind, version, Jsonb(json.loads(payload))) for owner, kind, version, payload in desired['user_snapshots']])
                cursor.executemany('insert into memory_documents(user_id,document_id,content,metadata,created_at) values(%s,%s,%s,%s,%s)', desired['memory_documents'])
                cursor.executemany('update note_tags set position=%s where user_id=%s and note_id=%s and normalized_tag=%s',
                                   [(position, owner, note, tag) for owner, note, tag, position in desired['note_tag_positions']])
                cursor.executemany('insert into note_search_tokens(user_id,note_id,field,position,token) values(%s,%s,%s,%s,%s)', desired['note_search_tokens'])
                tables, differences = _comparison(desired, _read_auxiliary(cursor))
                if differences:
                    raise StructuredMigrationError('Post-copy structured authority verification failed')
                status = 'applied'
    return {'status': status, 'mode': mode, 'source_sha256': inventory['source_sha256'],
            'qdrant_sha256': inventory['qdrant_sha256'], 'target_schema': target_schema,
            'tables': tables, 'discrepancies': differences}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--target-schema', required=True)
    parser.add_argument('--mode', choices=('dry-run', 'apply', 'verify'), required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    database_url = os.getenv('CUTOVER_TEST_DATABASE_URL')
    if not database_url:
        parser.error('CUTOVER_TEST_DATABASE_URL is required')
    # Protect the frozen archive directory, including metadata/pair and aliases.
    # Reuse the migration manifest's lexical/resolved path containment guard.
    _ensure_manifest_outside_source(args.archive.resolve().parent, args.evidence)
    try:
        result = migrate_structured(args.archive, database_url=database_url, target_schema=args.target_schema, mode=args.mode)
        code = 0
    except (ValueError, sqlite3.Error, psycopg.Error) as exc:
        # Driver messages can contain failing source rows. Report only the type.
        result = {'status': 'failed', 'mode': args.mode, 'error_type': type(exc).__name__}
        code = 1
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('Structured authority migration: ' + result['status'])
    return code


if __name__ == '__main__':
    raise SystemExit(main())
