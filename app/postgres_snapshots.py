"""Versioned structured authority; callers authorize the user before access.

User-row locking serializes snapshot mutation with other domain writes using
the same convention. It does not fence external work: a Worker must validate
its live attempt in the caller-owned publication transaction as well.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
import json
from typing import Any, Callable

from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from app.history import EMPTY_HISTORY, HistoryRepository
from app.postgres import PostgresDatabase
from app.import_publication_evidence import require_no_gate_in_transaction


class SnapshotConflict(ValueError):
    """The caller's snapshot version is no longer current."""


@dataclass(frozen=True)
class VersionedSnapshot:
    user_id: str
    kind: str
    version: int
    data: dict[str, Any] = field(repr=False)
    updated_at: datetime


def _kind(kind: str) -> None:
    if kind not in {'history', 'memory'}:
        raise ValueError('Unsupported snapshot kind')


def _validate(user_id: str, kind: str, data: Any) -> dict[str, Any]:
    _kind(kind)
    if not isinstance(data, dict):
        raise ValueError('Snapshot must be an object')
    # Freeze a JSON-only copy before awaiting database locks. Disallow NaN and
    # implicit conversion of tuple/datetime/custom objects to different values.
    try:
        copied = json.loads(json.dumps(data, allow_nan=False, ensure_ascii=False))
    except (ValueError, TypeError, OverflowError):
        raise ValueError('Snapshot must contain finite JSON data') from None
    if copied != data:
        raise ValueError('Snapshot must contain JSON-native values')
    if kind == 'history':
        try:
            HistoryRepository.validate_schema(copied)
        except RuntimeError:
            raise ValueError('History snapshot schema is invalid') from None
    else:
        if copied.get('user_id') != user_id or not isinstance(copied.get('memories'), list):
            raise ValueError('Memory snapshot schema or ownership is invalid')
        for memory in copied['memories']:
            if (not isinstance(memory, dict) or not isinstance(memory.get('metadata'), dict)
                    or memory['metadata'].get('user_id') != user_id):
                raise ValueError('Memory item ownership is invalid')
    return copied


def _snapshot(row) -> VersionedSnapshot | None:
    if row is None:
        return None
    data = _validate(row['user_id'], row['kind'], row['payload'])
    return VersionedSnapshot(row['user_id'], row['kind'], row['version'], data, row['updated_at'])


class PostgresSnapshotRepository:
    def __init__(self, database: PostgresDatabase):
        self.database = database

    def read(self, user_id: str, kind: str) -> VersionedSnapshot | None:
        _kind(kind)
        with self.database.transaction() as cursor:
            return self._read(cursor, user_id, kind)

    @staticmethod
    def _read(cursor, user_id, kind):
        return _snapshot(cursor.execute(
            'select * from user_snapshots where user_id=%s and kind=%s',
            (user_id, kind),
        ).fetchone())

    @staticmethod
    def _lock_user(cursor, user_id):
        # Non-autocommit connections begin the caller's transaction on this
        # first SELECT. Autocommit requires an explicit surrounding transaction.
        if (cursor.connection.autocommit
                and cursor.connection.info.transaction_status != TransactionStatus.INTRANS):
            raise ValueError('Caller-owned transaction required')
        if cursor.execute('select id from users where id=%s for update', (user_id,)).fetchone() is None:
            raise ValueError('Snapshot owner does not exist')

    def compare_and_swap(self, user_id: str, kind: str, data: dict, *, expected_version: int) -> VersionedSnapshot:
        with self.database.transaction() as cursor:
            return self.compare_and_swap_in_transaction(cursor, user_id, kind, data,
                                                        expected_version=expected_version)

    def compare_and_swap_in_transaction(self, cursor, user_id: str, kind: str,
                                        data: dict, *, expected_version: int) -> VersionedSnapshot:
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError('Snapshot version must be a nonnegative integer')
        payload = _validate(user_id, kind, data)
        # The surrounding publication transaction must lock this user before
        # any other domain rows, then check its Worker attempt if applicable.
        self._lock_user(cursor, user_id)
        require_no_gate_in_transaction(cursor, user_id)
        if expected_version == 0:
            row = cursor.execute(
                'insert into user_snapshots(user_id,kind,version,payload,updated_at) '
                'values(%s,%s,1,%s,clock_timestamp()) on conflict(user_id,kind) do nothing returning *',
                (user_id, kind, Jsonb(payload)),
            ).fetchone()
        else:
            row = cursor.execute(
                'update user_snapshots set payload=%s,version=version+1,updated_at=clock_timestamp() '
                'where user_id=%s and kind=%s and version=%s returning *',
                (Jsonb(payload), user_id, kind, expected_version),
            ).fetchone()
        if row is None:
            raise SnapshotConflict('Snapshot version changed')
        return _snapshot(row)

    def update(self, user_id: str, kind: str, mutation: Callable[[dict], None]) -> VersionedSnapshot:
        """Apply a short in-memory mutation; callback must perform no external I/O."""
        _kind(kind)
        with self.database.transaction() as cursor:
            self._lock_user(cursor, user_id)
            require_no_gate_in_transaction(cursor, user_id)
            previous = self._read(cursor, user_id, kind)
            data = previous.data if previous else (
                deepcopy(EMPTY_HISTORY) if kind == 'history' else {'user_id': user_id, 'memories': []}
            )
            mutation(data)
            return self.compare_and_swap_in_transaction(cursor, user_id, kind, data,
                                                        expected_version=previous.version if previous else 0)
