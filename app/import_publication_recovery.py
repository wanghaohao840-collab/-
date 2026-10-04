"""Private, bounded PostgreSQL claim authority for unknown import publications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import wraps
from uuid import UUID, uuid4

import psycopg

from app.import_publication_evidence import AttemptKey
from app.postgres_coordination import PostgresUserMutationCoordinator, _duration, _identity


class RecoveryLeaseLost(RuntimeError):
    """The exact recovery capture has expired or changed."""


class RecoveryUnavailable(RuntimeError):
    """The recovery database cannot provide authoritative state."""


def _report_unavailable(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except (psycopg.OperationalError, psycopg.InterfaceError, OSError) as error:
            raise RecoveryUnavailable('Recovery database is unavailable') from error
        except psycopg.Error as error:
            if error.sqlstate and (error.sqlstate.startswith('08')
                                   or error.sqlstate in ('57P01','57P02','57P03')):
                raise RecoveryUnavailable('Recovery database is unavailable') from error
            raise
    return wrapped


@dataclass(frozen=True)
class RecoveryClaim:
    user_id: str
    task_id: str
    task_lease_version: int
    worker_id: str
    queue_claim_token: UUID
    queue_version: int
    queue_expires_at: datetime
    recovery_token: UUID
    recovery_version: int
    recovery_expires_at: datetime


def _exact_key(key):
    if (type(key) is not AttemptKey or not _identity(key.user_id)
            or not _identity(key.task_id) or type(key.task_lease_version) is not int
            or key.task_lease_version < 1):
        raise RecoveryLeaseLost('Exact recovery attempt key required')
    return key.user_id, key.task_id, key.task_lease_version


def seed_missing_queue_in_transaction(cursor, key, task_expiry, user_expiry):
    """D2: seed only a missing row; preserve every existing disposition."""
    user, task, version = _exact_key(key)
    existing = cursor.execute('''select * from import_publication_recovery_queue
        where user_id=%s and task_id=%s and task_lease_version=%s for update''',
        (user,task,version)).fetchone()
    if existing is not None:
        if existing['state']=='resolved':
            raise RecoveryLeaseLost('Resolved queue contradicts unresolved gate')
        return existing
    return cursor.execute('''insert into import_publication_recovery_queue
        (user_id,task_id,task_lease_version,due_at,state,queue_version,reason_code)
        values(%s,%s,%s,greatest(%s,%s),'pending',1,'seed')
        on conflict(user_id,task_id,task_lease_version) do nothing returning *''',
        (user,task,version,task_expiry,user_expiry)).fetchone()


def close_expired_evidence_attempt_in_transaction(cursor, key, frozen_tuple):
    """Close only the original expired ordinary tuple; leave proof and gate intact."""
    from app.postgres_import_leases import PostgresImportLeaseRepository

    user, task_id, version = _exact_key(key)
    if (frozen_tuple['id'],frozen_tuple['user_id'],frozen_tuple['lease_version']) != (
            task_id,user,version):
        raise RecoveryLeaseLost('Original task tuple differs')
    task = cursor.execute('''select * from import_tasks where id=%s and user_id=%s
        for update''',(task_id,user)).fetchone()
    audit = cursor.execute('''select * from import_task_attempts
        where task_id=%s and lease_version=%s for update''',
        (task_id,version)).fetchone()
    header = cursor.execute('''select * from import_publication_evidence
        where user_id=%s and task_id=%s and task_lease_version=%s for update''',
        (user,task_id,version)).fetchone()
    gate = cursor.execute('''select * from user_publication_gates
        where user_id=%s and task_id=%s and task_lease_version=%s for update''',
        (user,task_id,version)).fetchone()
    lease = cursor.execute('''select * from user_mutation_leases
        where user_id=%s for update''',(user,)).fetchone()
    payload = cursor.execute('''select terminal_slot,payload_phase_version from
        import_publication_private_payloads where user_id=%s and task_id=%s
        and task_lease_version=%s''',(user,task_id,version)).fetchone()
    if (task is None or audit is None or header is None or gate is None
            or lease is None or payload is None or gate['status']!='unresolved'
            or header['phase'] not in ('intent','document_verified','pair_sealed')
            or payload['terminal_slot'] is not None
            or payload['payload_phase_version']!=header['phase_version']
            or (task['claimed_by'],task['lease_token'],task['lease_version'],
                task['user_lease_token'],task['user_lease_version']) !=
               (audit['worker_id'],audit['lease_token'],audit['lease_version'],
                audit['user_lease_token'],audit['user_lease_version'])
            or (lease['owner'],lease['lease_token'],lease['lease_version']) !=
               (audit['worker_id'],audit['user_lease_token'],audit['user_lease_version'])
            or (header['worker_id'],header['task_lease_token'],header['user_lease_token'],
                header['user_lease_version']) !=
               (audit['worker_id'],audit['lease_token'],audit['user_lease_token'],
                audit['user_lease_version'])):
        raise RecoveryLeaseLost('Original publication closure tuple differs')
    now = cursor.execute('select clock_timestamp() as now').fetchone()['now']
    if task['lease_expires_at']>now or lease['lease_expires_at']>now:
        raise RecoveryLeaseLost('Original ordinary lease remains live')
    if task['status']=='retry_wait' and task['next_attempt_at'] is None:
        if (audit['end_reason']=='lease_expired' and audit['ended_at'] is not None
                and task['error_code']=='needs_reconciliation'):
            return False
        raise RecoveryLeaseLost('Prior closure shape differs')
    if task['status']!='running' or audit['ended_at'] is not None:
        raise RecoveryLeaseLost('Original task cannot be closed')
    cursor.execute('''update import_task_attempts set ended_at=%s,
        end_reason='lease_expired' where task_id=%s and lease_version=%s
        and ended_at is null returning task_id''',(now,task_id,version)).fetchone()
    held = cursor.execute('''update import_tasks set status='retry_wait',
        next_attempt_at=null,finished_at=null,updated_at=%s,
        error_code='needs_reconciliation',
        error_summary='Import outcome requires reconciliation'
        where id=%s and user_id=%s and status='running'
        and lease_version=%s returning *''',
        (now,task_id,user,version)).fetchone()
    if held is None:
        raise RecoveryLeaseLost('Original task closure CAS lost')
    cursor.execute('''update user_mutation_leases set
        lease_expires_at=least(lease_expires_at,%s) where user_id=%s
        and owner=%s and lease_token=%s and lease_version=%s''',
        (now,user,audit['worker_id'],audit['user_lease_token'],
         audit['user_lease_version']))
    cursor.execute('''update import_tasks set
        lease_expires_at=least(lease_expires_at,%s) where id=%s and user_id=%s
        and lease_version=%s and lease_token=%s''',
        (now,task_id,user,version,audit['lease_token']))
    PostgresImportLeaseRepository._touch(cursor,held)
    return True


class PostgresImportPublicationRecoveryRepository:
    def __init__(self, database):
        self.database = database
        self.coordinator = PostgresUserMutationCoordinator(database)

    def _authority_rows(self, cursor, key):
        user, task, version = _exact_key(key)
        self.coordinator._isolation(cursor)
        if cursor.execute('select id from users where id=%s for update',
                          (user,)).fetchone() is None:
            raise RecoveryLeaseLost('Recovery user is missing')
        ordinary = cursor.execute('''select * from user_mutation_leases
            where user_id=%s for update''',(user,)).fetchone()
        task_row = cursor.execute('''select * from import_tasks where id=%s
            and user_id=%s for update''',(task,user)).fetchone()
        audit = cursor.execute('''select * from import_task_attempts
            where task_id=%s and lease_version=%s for update''',
            (task,version)).fetchone()
        header = cursor.execute('''select * from import_publication_evidence
            where user_id=%s and task_id=%s and task_lease_version=%s
            for update''',(user,task,version)).fetchone()
        gate = cursor.execute('''select * from user_publication_gates
            where user_id=%s and task_id=%s and task_lease_version=%s
            for update''',(user,task,version)).fetchone()
        recovery = cursor.execute('''select * from import_publication_recovery_leases
            where user_id=%s and task_id=%s and task_lease_version=%s
            for update''',(user,task,version)).fetchone()
        queue = cursor.execute('''select * from import_publication_recovery_queue
            where user_id=%s and task_id=%s and task_lease_version=%s
            for update''',(user,task,version)).fetchone()
        if (ordinary is None or task_row is None or audit is None or header is None
                or gate is None or queue is None or gate['status']!='unresolved'
                or header['phase'] in ('proved_succeeded','abandoned')
                or task_row['lease_version']!=version):
            raise RecoveryLeaseLost('Exact unresolved recovery authority changed')
        return ordinary, task_row, audit, header, gate, recovery, queue

    @_report_unavailable
    def claim_next(self, worker_id: str, lease_seconds: int = 60) -> RecoveryClaim | None:
        _duration(lease_seconds)
        if not _identity(worker_id):
            raise ValueError('Recovery worker ID must be nonempty')
        for _ in range(3):
            with self.database.transaction() as cursor:
                self.coordinator._isolation(cursor)
                candidate = cursor.execute('''with ranked as (
                    select q.user_id,q.task_id,q.task_lease_version,
                      row_number() over(partition by q.user_id
                        order by q.queued_at,q.task_id,q.task_lease_version) as position,
                      s.last_claimed_at
                    from import_publication_recovery_queue q
                    join import_publication_recovery_schedule s on s.user_id=q.user_id
                    where (q.state='pending' and q.due_at<=clock_timestamp())
                      or (q.state='claimed' and q.claim_expires_at<=clock_timestamp()))
                    select q.* from ranked r join import_publication_recovery_queue q
                      on q.user_id=r.user_id and q.task_id=r.task_id
                      and q.task_lease_version=r.task_lease_version
                    where r.position=1 and
                      ((q.state='pending' and q.due_at<=clock_timestamp())
                        or (q.state='claimed' and q.claim_expires_at<=clock_timestamp()))
                      and exists(select 1 from user_publication_gates g where
                        g.user_id=q.user_id and g.task_id=q.task_id
                        and g.task_lease_version=q.task_lease_version
                        and g.status='unresolved')
                      and not exists(select 1 from import_publication_recovery_leases l
                        where l.user_id=q.user_id and l.task_id=q.task_id
                        and l.task_lease_version=q.task_lease_version
                        and l.expires_at>clock_timestamp())
                    order by r.last_claimed_at,r.user_id
                    for update of q skip locked limit 1''').fetchone()
                if candidate is None:
                    return None
                now = cursor.execute('select clock_timestamp() as now').fetchone()['now']
                if (candidate['state']=='pending' and candidate['due_at']>now
                    or candidate['state']=='claimed' and candidate['claim_expires_at']>now):
                    continue
                token = uuid4()
                capture = cursor.execute('''update import_publication_recovery_queue
                    set state='claimed',due_at=null,queue_version=queue_version+1,
                      claim_token=%s,claim_expires_at=%s,last_claimed_at=%s
                    where user_id=%s and task_id=%s and task_lease_version=%s
                      and queue_version=%s returning *''',
                    (token,now+timedelta(seconds=lease_seconds),now,
                     candidate['user_id'],candidate['task_id'],candidate['task_lease_version'],
                     candidate['queue_version'])).fetchone()
                if capture is None:
                    continue
                cursor.execute('''insert into import_publication_recovery_token_issuance
                    (token,kind,user_id,task_id,task_lease_version,issued_version)
                    values(%s,'queue',%s,%s,%s,%s)''',
                    (token,capture['user_id'],capture['task_id'],
                     capture['task_lease_version'],capture['queue_version']))
                changed = cursor.execute('''update import_publication_recovery_schedule
                    set last_claimed_at=%s where user_id=%s returning user_id''',
                    (now,capture['user_id'])).fetchone()
                if changed is None:
                    raise RecoveryLeaseLost('Recovery schedule is missing')
            key = AttemptKey(capture['user_id'],capture['task_id'],
                             capture['task_lease_version'])
            with self.database.transaction() as cursor:
                ordinary, task_row, audit, header, _, old, final = self._authority_rows(cursor,key)
                now = cursor.execute('select clock_timestamp() as now').fetchone()['now']
                if (final['state']!='claimed' or final['claim_token']!=token
                    or final['queue_version']!=capture['queue_version']
                    or final['claim_expires_at']<=now):
                    raise RecoveryLeaseLost('Queue capture expired during authority wait')
                if old is not None and old['expires_at']>now:
                    raise RecoveryLeaseLost('Another recovery lease remains live')
                original = (
                    header['phase'] in ('intent','document_verified','pair_sealed')
                    and (task_row['claimed_by'],task_row['lease_token'],
                         task_row['lease_version'],task_row['user_lease_token'],
                         task_row['user_lease_version']) ==
                        (audit['worker_id'],audit['lease_token'],
                         audit['lease_version'],audit['user_lease_token'],
                         audit['user_lease_version'])
                    and (ordinary['owner'],ordinary['lease_token'],
                         ordinary['lease_version']) ==
                        (audit['worker_id'],audit['user_lease_token'],
                         audit['user_lease_version'])
                    and (header['worker_id'],header['task_lease_token'],
                         header['user_lease_token'],header['user_lease_version']) ==
                        (audit['worker_id'],audit['lease_token'],
                         audit['user_lease_token'],audit['user_lease_version'])
                )
                if (original and (task_row['lease_expires_at']>now
                                  or ordinary['lease_expires_at']>now)):
                    deferred = cursor.execute('''update import_publication_recovery_queue
                        set state='pending',due_at=%s,queue_version=queue_version+1,
                        claim_token=null,claim_expires_at=null,
                        reason_code='ordinary_live',updated_at=%s
                        where user_id=%s and task_id=%s and task_lease_version=%s
                        and state='claimed' and claim_token=%s and queue_version=%s
                        returning task_id''',
                        (max(task_row['lease_expires_at'],
                             ordinary['lease_expires_at'])+timedelta(seconds=1),
                         now,key.user_id,key.task_id,key.task_lease_version,
                         token,final['queue_version'])).fetchone()
                    if deferred is None:
                        raise RecoveryLeaseLost('Original lease deferral CAS lost')
                    return None
                recovery_token = uuid4()
                recovery_version = 1 if old is None else old['version']+1
                if old is None:
                    lease = cursor.execute('''insert into import_publication_recovery_leases
                        (user_id,task_id,task_lease_version,owner,token,version,
                         heartbeat_at,expires_at,queue_claim_token,queue_version)
                        values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *''',
                        (*key.__dict__.values(),worker_id,recovery_token,
                         recovery_version,now,final['claim_expires_at'],token,
                         final['queue_version'])).fetchone()
                else:
                    lease = cursor.execute('''update import_publication_recovery_leases
                        set owner=%s,token=%s,version=version+1,heartbeat_at=%s,
                        expires_at=%s,queue_claim_token=%s,queue_version=%s
                        where user_id=%s and task_id=%s and task_lease_version=%s
                        and version=%s and expires_at<=%s returning *''',
                        (worker_id,recovery_token,now,final['claim_expires_at'],
                         token,final['queue_version'],*key.__dict__.values(),
                         old['version'],now)).fetchone()
                if lease is None:
                    raise RecoveryLeaseLost('Recovery grant CAS lost')
                cursor.execute('''insert into import_publication_recovery_token_issuance
                    (token,kind,user_id,task_id,task_lease_version,issued_version)
                    values(%s,'recovery',%s,%s,%s,%s)''',
                    (recovery_token,*key.__dict__.values(),recovery_version))
                return RecoveryClaim(key.user_id,key.task_id,key.task_lease_version,
                    worker_id,token,final['queue_version'],final['claim_expires_at'],
                    recovery_token,recovery_version,lease['expires_at'])
        return None

    @_report_unavailable
    def renew(self, claim: RecoveryClaim, lease_seconds: int = 60) -> RecoveryClaim:
        _duration(lease_seconds)
        if type(claim) is not RecoveryClaim:
            raise RecoveryLeaseLost('Exact recovery claim required')
        key = AttemptKey(claim.user_id,claim.task_id,claim.task_lease_version)
        with self.database.transaction() as cursor:
            _, _, _, _, _, lease, queue = self._authority_rows(cursor,key)
            now = cursor.execute('select clock_timestamp() as now').fetchone()['now']
            if (lease is None or queue['state']!='claimed'
                or queue['claim_token']!=claim.queue_claim_token
                or queue['queue_version']!=claim.queue_version
                or lease['owner']!=claim.worker_id
                or lease['token']!=claim.recovery_token
                or lease['version']!=claim.recovery_version
                or lease['queue_claim_token']!=claim.queue_claim_token
                or lease['queue_version']!=claim.queue_version
                or lease['expires_at']<=now or queue['claim_expires_at']<=now):
                raise RecoveryLeaseLost('Recovery renewal lost either authority')
            for kind, token, version in (
                    ('queue',claim.queue_claim_token,claim.queue_version),
                    ('recovery',claim.recovery_token,claim.recovery_version)):
                if cursor.execute('''select 1 from import_publication_recovery_token_issuance
                    where token=%s and kind=%s and user_id=%s and task_id=%s
                    and task_lease_version=%s and issued_version=%s''',
                    (token,kind,*key.__dict__.values(),version)).fetchone() is None:
                    raise RecoveryLeaseLost('Recovery token issuance changed')
            old_queue_expiry = queue['claim_expires_at']
            old_lease_expiry = lease['expires_at']
            new_queue_expiry = max(old_queue_expiry,now+timedelta(seconds=lease_seconds)) + timedelta(microseconds=1)
            new_lease_expiry = max(old_lease_expiry,now+timedelta(seconds=lease_seconds)) + timedelta(microseconds=1)
            l = cursor.execute('''update import_publication_recovery_leases
                set heartbeat_at=%s,expires_at=%s where user_id=%s and task_id=%s
                and task_lease_version=%s and token=%s and version=%s
                and expires_at=%s returning *''',
                (now,new_lease_expiry,*key.__dict__.values(),claim.recovery_token,
                 claim.recovery_version,old_lease_expiry)).fetchone()
            q = cursor.execute('''update import_publication_recovery_queue
                set claim_expires_at=%s where user_id=%s and task_id=%s
                and task_lease_version=%s and claim_token=%s and queue_version=%s
                and claim_expires_at=%s returning *''',
                (new_queue_expiry,*key.__dict__.values(),claim.queue_claim_token,
                 claim.queue_version,old_queue_expiry)).fetchone()
            if l is None or q is None:
                raise RecoveryLeaseLost('Recovery dual renewal CAS lost')
            return RecoveryClaim(claim.user_id,claim.task_id,claim.task_lease_version,
                claim.worker_id,claim.queue_claim_token,claim.queue_version,
                q['claim_expires_at'],claim.recovery_token,claim.recovery_version,
                l['expires_at'])
