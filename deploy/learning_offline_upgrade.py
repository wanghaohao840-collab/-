"""Offline, no-legacy-source upgrade; run inside the approved candidate image.

The host must hold the operations lock and stop both services. Evidence lives
outside the application data tree. No legacy import or recovery is implicit.
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3


def snapshot(root):
    root = Path(root)
    for parent, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = Path(parent) / name
            if path.is_symlink() or name == 'learning.json':
                raise ValueError('LEGACY_OR_LINK_REQUIRES_SEPARATE_MIGRATION')
    db = root / 'app.db'
    if not db.is_file():
        raise ValueError('EXISTING_DATABASE_REQUIRED')
    with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as conn:
        if conn.execute('pragma integrity_check').fetchall() != [('ok',)]:
            raise ValueError('DATABASE_INTEGRITY_FAILED')
        if conn.execute('pragma foreign_key_check').fetchone() is not None:
            raise ValueError('DATABASE_FOREIGN_KEYS_FAILED')
        tables = [r[0] for r in conn.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")]
        result = {}
        for name in tables:
            quoted = '"' + name.replace('"', '""') + '"'
            rows = conn.execute('select * from ' + quoted).fetchall()
            digest = sha256()
            for row in sorted(repr(row) for row in rows):
                digest.update(row.encode('utf-8') + b'\n')
            result[name] = dict(rows=len(rows), sha256=digest.hexdigest())
        return result


def upgrade(root, evidence, *, verify=False):
    root, evidence = Path(root).absolute(), Path(evidence)
    current = snapshot(root)
    if verify:
        original = json.loads(evidence.read_text(encoding='utf-8'))
    else:
        if any(name.startswith('learning_') for name in current):
            raise ValueError('OLD_DATABASE_WITHOUT_LEARNING_TABLES_REQUIRED')
        original = current
        with evidence.open('x', encoding='utf-8') as stream:
            json.dump(original, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        from app.database import initialize_database
        initialize_database(root / 'app.db')
        current = snapshot(root)
    if any(current.get(name) != record for name, record in original.items()):
        raise ValueError('EXISTING_ROWS_CHANGED')
    learning = {name: record for name, record in current.items() if name.startswith('learning_')}
    expected = {'learning_plans', 'learning_plan_documents', 'learning_tasks',
                'learning_requests', 'learning_task_events', 'learning_migrations'}
    if set(learning) != expected or any(record['rows'] for record in learning.values()):
        raise ValueError('EMPTY_LEARNING_SCHEMA_NOT_VERIFIED')
    print('OFFLINE_UPGRADE_VERIFIED', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['upgrade', 'verify'])
    parser.add_argument('--root', required=True)
    parser.add_argument('--evidence', required=True)
    args = parser.parse_args()
    upgrade(args.root, args.evidence, verify=args.action == 'verify')
