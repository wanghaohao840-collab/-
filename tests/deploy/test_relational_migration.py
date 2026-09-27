"""Offline source guards must reject newly appearing SQLite journal files."""
import hashlib
from pathlib import Path

import pytest

from app.database import initialize_database
from deploy import migrate_relational_isolated as migration


def test_journal_created_during_inspection_rejected_before_postgres(tmp_path, monkeypatch):
    source = tmp_path / 'app.db'
    initialize_database(source)
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    real_digest = migration._digest_file
    calls = 0

    def digest_then_start_writer(path):
        nonlocal calls
        calls += 1
        result = real_digest(path)
        if calls == 2:
            Path(str(path) + '-wal').write_bytes(b'writer-started')
        return result

    monkeypatch.setattr(migration, '_digest_file', digest_then_start_writer)
    with pytest.raises(migration.MigrationError, match='journal siblings'):
        migration.migrate_relational(source, expected_sha256=expected,
                                     database_url='must-not-be-contacted',
                                     target_schema='cutover_test', mode='apply')
