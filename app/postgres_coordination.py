"""PostgreSQL ownership for long user mutations and short DB publication.

External work must run outside publication(). Unfenced direct repositories are
not protected until runtime wiring is complete. This fence does not replace
task/attempt checks or immutable external vector generation publication.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from psycopg.pq import TransactionStatus

from app.postgres import PostgresDatabase


class MutationLeaseLost(RuntimeError):
    """Ownership expired, changed, or the user is no longer active."""


@dataclass(frozen=True)
class UserMutationLease:
    user_id: str
    owner: str
    lease_token: UUID
    lease_version: int
    expires_at: datetime


def _duration(seconds):
    if type(seconds) is not int or not 1 <= seconds <= 86400:
        raise ValueError('lease_seconds must be an integer between 1 and 86400')


def _identity(value):
    return isinstance(value, str) and bool(value.strip())


def _handle(row):
    return UserMutationLease(row['user_id'], row['owner'], row['lease_token'],
                             row['lease_version'], row['lease_expires_at'])


class PostgresUserMutationCoordinator:
    def __init__(self, database: PostgresDatabase):
        self.database = database

    @staticmethod
    def _isolation(cursor):
        if cursor.execute('show transaction_isolation').fetchone()['transaction_isolation'] != 'read committed':
            raise ValueError('User mutation leases require READ COMMITTED')
        if cursor.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError('User mutation leases require an active transaction')

    @staticmethod
    def _lock_user(cursor, user_id, *, skip=False):
        row = cursor.execute('select id from users where id=%s for update'
                             + (' skip locked' if skip else ''), (user_id,)).fetchone()
        if row is None:
            # A separate plain read distinguishes an existing locked row from
            # an unknown user without waiting on the other user's mutation.
            if skip and cursor.execute('select id from users where id=%s', (user_id,)).fetchone():
                return False
            raise ValueError('User does not exist')
        # Fresh READ COMMITTED statement after any wait, never lock-statement
        # status or transaction-start time.
        row = cursor.execute('select status from users where id=%s', (user_id,)).fetchone()
        if row is None or row['status'] != 'active':
            raise ValueError('User is not active')
        return True

    def acquire(self, user_id, owner, lease_seconds=60):
        with self.database.transaction() as cursor:
            cursor.execute('begin')
            return self.acquire_in_transaction(cursor, user_id, owner, lease_seconds)

    def acquire_in_transaction(self, cursor, user_id, owner, lease_seconds=60):
        """Acquire within an active caller transaction; lock user before tasks.

        The returned handle is provisional until the caller commits. The caller
        must roll back on any failure; this method never commits or closes it.
        """
        _duration(lease_seconds)
        if not _identity(user_id) or not _identity(owner):
            raise ValueError('User and owner must be nonempty strings')
        if cursor.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError('Active caller-owned transaction required')
        self._isolation(cursor)
        if not self._lock_user(cursor, user_id, skip=True):
            return None
        cursor.execute('select user_id from user_mutation_leases where user_id=%s for update', (user_id,))
        row = cursor.execute(
            'select *, lease_expires_at > clock_timestamp() as live from user_mutation_leases where user_id=%s',
            (user_id,),
        ).fetchone()
        if row is not None and row['live']:
            return None
        return _handle(cursor.execute(
            'with t as materialized (select clock_timestamp() as now) '
            'insert into user_mutation_leases(user_id,owner,lease_token,lease_version,heartbeat_at,lease_expires_at) '
            "select %s,%s,%s,%s,t.now,t.now + %s * interval '1 second' from t "
            'on conflict(user_id) do update set owner=excluded.owner,lease_token=excluded.lease_token, '
            'lease_version=excluded.lease_version,heartbeat_at=excluded.heartbeat_at,lease_expires_at=excluded.lease_expires_at '
            'returning *',
            (user_id, owner, uuid4(), row['lease_version'] + 1 if row else 1, lease_seconds),
        ).fetchone())

    def _require_live(self, cursor, handle):
        if (not isinstance(handle, UserMutationLease) or not _identity(handle.user_id)
                or not _identity(handle.owner) or not isinstance(handle.lease_token, UUID)
                or type(handle.lease_version) is not int or handle.lease_version < 1):
            raise MutationLeaseLost('Invalid lease handle')
        self._isolation(cursor)
        try:
            self._lock_user(cursor, handle.user_id)
        except ValueError as exc:
            raise MutationLeaseLost(str(exc)) from exc
        cursor.execute('select user_id from user_mutation_leases where user_id=%s for update', (handle.user_id,))
        row = cursor.execute(
            'select * from user_mutation_leases where user_id=%s and owner=%s and lease_token=%s '
            'and lease_version=%s and lease_expires_at > clock_timestamp()',
            (handle.user_id, handle.owner, handle.lease_token, handle.lease_version),
        ).fetchone()
        if row is None:
            raise MutationLeaseLost('User mutation lease is no longer live')
        return row

    def heartbeat(self, handle, lease_seconds=60):
        _duration(lease_seconds)
        with self.database.transaction() as cursor:
            self._require_live(cursor, handle)
            row = cursor.execute(
                'with t as materialized (select clock_timestamp() as now) '
                'update user_mutation_leases set heartbeat_at=t.now, '
                "lease_expires_at=t.now + %s * interval '1 second' from t "
                'where user_id=%s and lease_expires_at > t.now returning user_mutation_leases.*',
                (lease_seconds, handle.user_id),
            ).fetchone()
            if row is None:
                raise MutationLeaseLost('User mutation lease expired before renewal')
            return _handle(row)

    def release(self, handle):
        try:
            with self.database.transaction() as cursor:
                self._require_live(cursor, handle)
                return cursor.execute(
                    'with t as materialized (select clock_timestamp() as now) '
                    'update user_mutation_leases set lease_expires_at=t.now from t '
                    'where user_id=%s and lease_expires_at > t.now returning user_id',
                    (handle.user_id,),
                ).fetchone() is not None
        except MutationLeaseLost:
            return False

    @contextmanager
    def publication(self, handle):
        """Short deterministic DB-only work; caller must not commit the cursor.

        The final fresh-clock check occurs immediately before the transaction
        helper commits. Exceptions or expiry roll back every domain change.
        """
        with self.database.transaction() as cursor:
            self._require_live(cursor, handle)
            yield cursor
            self._require_live(cursor, handle)
