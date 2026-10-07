"""Private, bounded PostgreSQL claim authority for unknown import publications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import wraps
from typing import Literal
from uuid import UUID, uuid4

import psycopg

from app.import_publication_evidence import (
    AttemptKey, PostgresImportPublicationEvidenceRepository,
    PublicationEvidenceError, _read_slot, _validate_stored_slots,
)
from app.import_publication_proof import (
    DetachedImportMemoryPublication, ImportPublicationProofService,
    PublicationProofEnvelope, PublicationProofUnknown, _InvalidProof,
)
from app.import_memory_publication import ImportMemoryPublicationError
from app.postgres_history_document_witnesses import HistoryDocumentWitnessError
from app.postgres_coordination import PostgresUserMutationCoordinator, _duration, _identity


class RecoveryLeaseLost(RuntimeError):
    """The exact recovery capture has expired or changed."""


class RecoveryUnavailable(RuntimeError):
    """The recovery database cannot provide authoritative state."""


class _RecoveryHold(RuntimeError):
    """The capture is valid but its publication cannot be resolved safely."""


RecoveryOutcome = Literal['proved_succeeded', 'abandoned', 'manual_hold', 'retry_later']


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

    def _capture_rows(self, cursor, key):
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
                or gate is None or queue is None or task_row['lease_version']!=version):
            raise RecoveryLeaseLost('Exact recovery authority changed')
        return ordinary, task_row, audit, header, gate, recovery, queue

    def _authority_rows(self, cursor, key):
        rows = self._capture_rows(cursor, key)
        if (rows[4]['status'] != 'unresolved'
                or rows[3]['phase'] in ('proved_succeeded','abandoned')):
            raise RecoveryLeaseLost('Exact unresolved recovery authority changed')
        return rows

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

    def _live_capture(self, cursor, claim):
        if (type(claim) is not RecoveryClaim or not _identity(claim.worker_id)
                or type(claim.queue_claim_token) is not UUID
                or type(claim.recovery_token) is not UUID
                or type(claim.queue_version) is not int or claim.queue_version < 1
                or type(claim.recovery_version) is not int or claim.recovery_version < 1):
            raise RecoveryLeaseLost('Exact recovery claim required')
        key = AttemptKey(claim.user_id,claim.task_id,claim.task_lease_version)
        rows = self._capture_rows(cursor,key)
        ordinary, task, audit, header, gate, lease, queue = rows
        now = cursor.execute('select clock_timestamp() as now').fetchone()['now']
        if (lease is None or queue['state']!='claimed'
                or queue['claim_token']!=claim.queue_claim_token
                or queue['queue_version']!=claim.queue_version
                or queue['claim_expires_at']!=claim.queue_expires_at
                or lease['owner']!=claim.worker_id
                or lease['token']!=claim.recovery_token
                or lease['version']!=claim.recovery_version
                or lease['queue_claim_token']!=claim.queue_claim_token
                or lease['queue_version']!=claim.queue_version
                or lease['expires_at']!=claim.recovery_expires_at
                or lease['expires_at']<=now or queue['claim_expires_at']<=now):
            raise RecoveryLeaseLost('Recovery result lost either authority')
        for kind, token, version in (
                ('queue',claim.queue_claim_token,claim.queue_version),
                ('recovery',claim.recovery_token,claim.recovery_version)):
            if cursor.execute('''select 1 from import_publication_recovery_token_issuance
                where token=%s and kind=%s and user_id=%s and task_id=%s
                and task_lease_version=%s and issued_version=%s''',
                (token,kind,key.user_id,key.task_id,key.task_lease_version,
                 version)).fetchone() is None:
                raise RecoveryLeaseLost('Permanent recovery token differs')
        return key, rows, now

    def _claimed_rows(self, cursor, claim):
        key, rows, now = self._live_capture(cursor, claim)
        if (rows[4]['status'] != 'unresolved'
                or rows[4]['resolved_at'] is not None
                or rows[3]['phase'] in ('proved_succeeded','abandoned')):
            raise RecoveryLeaseLost('Exact unresolved recovery authority changed')
        return key, rows, now

    @staticmethod
    def _original_tuple(rows, evidence):
        ordinary, task, audit, header, _, _, _ = rows
        attempt = evidence.intent['attempt']
        expected = (attempt['worker_id'],UUID(attempt['task_lease_token']),
                    attempt['task_lease_version'],UUID(attempt['user_lease_token']),
                    attempt['user_lease_version'])
        if ((task['claimed_by'],task['lease_token'],task['lease_version'],
             task['user_lease_token'],task['user_lease_version']) != expected
                or audit['user_id'] != attempt['user_id']
                or (audit['worker_id'],audit['lease_token'],audit['lease_version'],
                    audit['user_lease_token'],audit['user_lease_version']) != expected
                or (ordinary['owner'],ordinary['lease_token'],
                    ordinary['lease_version']) !=
                    (expected[0],expected[3],expected[4])
                or (header['worker_id'],header['task_lease_token'],
                    header['user_lease_token'],header['user_lease_version']) !=
                    (expected[0],expected[1],expected[3],expected[4])
                or header['intent_hash'] != evidence.intent_hash):
            raise _RecoveryHold('Original attempt tuple differs')

    def _frozen(self, cursor, key, rows):
        try:
            frozen = PostgresImportPublicationEvidenceRepository(
                self.database)._read_exact_in_cursor(cursor,key)
        except PublicationEvidenceError as error:
            raise _RecoveryHold('Stored proof is invalid') from error
        if (frozen is None or frozen.phase != rows[3]['phase']
                or frozen.phase_version != rows[3]['phase_version']
                or frozen.intent_hash != rows[3]['intent_hash']):
            raise _RecoveryHold('Frozen proof changed')
        return frozen

    @staticmethod
    def _still_live(cursor, claim):
        now = cursor.execute('select clock_timestamp() as now').fetchone()['now']
        if (claim.queue_expires_at <= now or claim.recovery_expires_at <= now):
            raise RecoveryLeaseLost('Recovery authority expired after row wait')
        return now

    def _close_capture(self, cursor, claim, reason, state='resolved', due=None,
                       transient_count=None):
        _, rows, _ = self._live_capture(cursor, claim)
        if state == 'resolved' and (
                rows[4]['status'] != 'resolved'
                or rows[3]['phase'] not in ('proved_succeeded', 'abandoned')):
            raise RecoveryLeaseLost('Publication resolution has not committed')
        key = (claim.user_id,claim.task_id,claim.task_lease_version)
        lease = cursor.execute('''update import_publication_recovery_leases
            set expires_at=least(expires_at,clock_timestamp())
            where user_id=%s and task_id=%s and task_lease_version=%s
            and owner=%s and token=%s and version=%s
            and queue_claim_token=%s and queue_version=%s
            and expires_at=%s returning token''',
            (*key,claim.worker_id,claim.recovery_token,claim.recovery_version,
             claim.queue_claim_token,claim.queue_version,
             claim.recovery_expires_at)).fetchone()
        if lease is None:
            raise RecoveryLeaseLost('Recovery lease close CAS lost')
        count_sql = '' if transient_count is None else ',transient_count=%s'
        params = (state,due,reason)
        if transient_count is not None:
            params += (transient_count,)
        queue = cursor.execute(f'''update import_publication_recovery_queue
            set state=%s,due_at=%s,reason_code=%s{count_sql},
            claim_token=null,claim_expires_at=null,queue_version=queue_version+1
            where user_id=%s and task_id=%s and task_lease_version=%s
            and state='claimed' and claim_token=%s and queue_version=%s
            and claim_expires_at=%s returning queue_version''',
            (*params,*key,claim.queue_claim_token,claim.queue_version,
             claim.queue_expires_at)).fetchone()
        if queue is None:
            raise RecoveryLeaseLost('Recovery queue close CAS lost')

    def _resolve_gate(self, cursor, claim):
        _, rows, _ = self._live_capture(cursor, claim)
        if (rows[4]['status'] != 'unresolved'
                or rows[3]['phase'] not in ('proved_succeeded', 'abandoned')):
            raise RecoveryLeaseLost('Publication transition has not committed')
        gate = cursor.execute('''update user_publication_gates
            set status='resolved',resolved_at=clock_timestamp()
            where user_id=%s and task_id=%s and task_lease_version=%s
            and status='unresolved' and resolved_at is null
            returning task_id''',
            (claim.user_id,claim.task_id,claim.task_lease_version)).fetchone()
        if gate is None:
            raise _RecoveryHold('Exact publication gate changed')

    def _transition_metadata(self, cursor, claim, evidence, next_phase,
                             reason=None):
        """Write mirrored metadata only while the exact recovery claim is held."""
        key, rows, _ = self._claimed_rows(cursor, claim)
        if (self._frozen(cursor, key, rows) != evidence
                or next_phase not in ('proved_succeeded', 'abandoned', evidence.phase)
                or reason not in (None, 'manual_hold')):
            raise _RecoveryHold('Recovery metadata transition differs')
        if next_phase == 'proved_succeeded':
            terminal = _read_slot(evidence.terminal_slot)
            if (evidence.phase != 'terminal_committed' or terminal is None
                    or rows[1]['status'] != 'succeeded'
                    or rows[2]['end_reason'] != 'succeeded'
                    or rows[2]['ended_at'] is None):
                raise _RecoveryHold('Success transition lacks terminal authority')
            try:
                pair = ImportPublicationProofService._pair(cursor,evidence,terminal)
                ImportPublicationProofService._domain(cursor,evidence,terminal,pair)
            except (PublicationEvidenceError, ImportMemoryPublicationError,
                    HistoryDocumentWitnessError, _InvalidProof, ValueError,
                    KeyError, TypeError) as error:
                raise _RecoveryHold('Success transition lacks exact witnesses') from error
        elif next_phase == 'abandoned':
            self._original_tuple(rows,evidence)
            if (evidence.phase not in ('intent','document_verified','pair_sealed')
                    or evidence.terminal_slot is not None
                    or rows[1]['status'] != 'retry_wait'
                    or rows[1]['next_attempt_at'] is not None
                    or rows[2]['end_reason'] != 'lease_expired'):
                raise _RecoveryHold('Abandonment transition lacks ordinary closure')
            for kind in ('rag','episode'):
                generation_id = UUID(evidence.intent['scopes'][kind]['candidate_id'])
                reservation = cursor.execute('''select state,revoked_at from
                    generation_reservations where generation_id=%s for update''',
                    (generation_id,)).fetchone()
                generation = cursor.execute('''select state from vector_generations
                    where generation_id=%s for update''',(generation_id,)).fetchone()
                if (reservation is None or reservation['state'] != 'revoked'
                        or reservation['revoked_at'] is None
                        or generation is not None and generation['state'] != 'abandoned'):
                    raise _RecoveryHold('Abandonment transition lacks two revoked IDs')
        _validate_stored_slots(evidence.intent, evidence.intent_hash,
            next_phase, evidence.phase_version + 1, reason,
            (evidence.document_slot, evidence.rag_sealed_slot,
             evidence.episode_sealed_slot, evidence.terminal_slot))
        self._still_live(cursor, claim)
        child = cursor.execute('''update import_publication_private_payloads
            set payload_phase_version=payload_phase_version+1
            where user_id=%s and task_id=%s and task_lease_version=%s
            and payload_phase_version=%s returning payload_phase_version''',
            (key.user_id, key.task_id, key.task_lease_version,
             evidence.phase_version)).fetchone()
        if child is None:
            raise PublicationEvidenceError('Private recovery metadata CAS lost')
        header = cursor.execute('''update import_publication_evidence
            set phase=%s,phase_version=phase_version+1,observation_reason=%s
            where user_id=%s and task_id=%s and task_lease_version=%s
            and phase=%s and phase_version=%s and intent_hash=%s
            returning phase_version''',
            (next_phase, reason, key.user_id, key.task_id, key.task_lease_version,
             evidence.phase, evidence.phase_version, evidence.intent_hash)).fetchone()
        if header is None or header['phase_version'] != child['payload_phase_version']:
            raise PublicationEvidenceError('Recovery header metadata CAS lost')
        self._still_live(cursor, claim)

    @_report_unavailable
    def ack_success(self, claim: RecoveryClaim,
                    proof: DetachedImportMemoryPublication) -> Literal['proved_succeeded']:
        with self.database.transaction() as cursor:
            self.ack_proved_in_transaction(cursor,claim,proof)
        return 'proved_succeeded'

    def ack_proved_in_transaction(self, cursor, claim: RecoveryClaim,
                                  proof: DetachedImportMemoryPublication) -> None:
        key, rows, _ = self._claimed_rows(cursor,claim)
        frozen = self._frozen(cursor,key,rows)
        if (type(proof) is not DetachedImportMemoryPublication
                or type(proof.proof) is not PublicationProofEnvelope
                or proof.proof.attempt_key != key
                or frozen.phase != 'terminal_committed'
                or proof.proof.phase != frozen.phase
                or proof.proof.phase_version != frozen.phase_version
                or proof.proof.intent_hash != frozen.intent_hash
                or rows[1]['status'] != 'succeeded'
                or rows[2]['end_reason'] != 'succeeded'
                or rows[2]['ended_at'] is None):
            raise _RecoveryHold('Terminal success proof changed')
        document = _read_slot(frozen.document_slot)
        terminal = _read_slot(frozen.terminal_slot)
        if (document is None or terminal is None
                or proof.proof.final_expected_hash != document['final_expected_hash']
                or tuple(terminal['rag_receipt']) != proof.proof.rag_receipt
                or tuple(terminal['episode_receipt']) != proof.proof.episode_receipt):
            raise _RecoveryHold('Terminal receipt envelope changed')
        try:
            pair = ImportPublicationProofService._pair(cursor,frozen,terminal)
            ref, witness = ImportPublicationProofService._domain(
                cursor,frozen,terminal,pair)
        except (PublicationEvidenceError, ImportMemoryPublicationError,
                HistoryDocumentWitnessError, _InvalidProof, ValueError,
                KeyError, TypeError) as error:
            raise _RecoveryHold('Terminal publication witness differs') from error
        if (pair != proof.pair or ref != proof.document_ref
                or witness != proof.witness
                or proof.event_id != frozen.intent['event']['event_id']
                or proof.history_version != frozen.intent['snapshots']['next_history']['version']
                or proof.memory_version != frozen.intent['snapshots']['next_memory']['version']):
            raise _RecoveryHold('Detached proof is stale or forged')
        self._still_live(cursor,claim)
        self._transition_metadata(cursor,claim,frozen,'proved_succeeded')
        self._resolve_gate(cursor,claim)
        self._close_capture(cursor,claim,'proved_succeeded')

    @_report_unavailable
    def abandon_exact(self, claim: RecoveryClaim) -> Literal['abandoned']:
        with self.database.transaction() as cursor:
            self.abandon_exact_in_transaction(cursor,claim)
        return 'abandoned'

    def abandon_exact_in_transaction(self, cursor, claim: RecoveryClaim) -> None:
        from app.postgres_vector_generations import (
            VectorAuthorityError, validate_import_pair_for_recovery_in_transaction,
        )
        key, rows, now = self._claimed_rows(cursor,claim)
        frozen = self._frozen(cursor,key,rows)
        self._original_tuple(rows,frozen)
        ordinary, task, audit, _, _, _, _ = rows
        if (frozen.phase not in ('intent','document_verified','pair_sealed')
                or frozen.terminal_slot is not None
                or task['status'] not in ('running','retry_wait')
                or audit['end_reason']=='succeeded'
                or task['status']=='succeeded'):
            raise _RecoveryHold('Publication is terminal or ordinary tuple differs')
        if task['lease_expires_at']>now or ordinary['lease_expires_at']>now:
            raise _RecoveryHold('Original ordinary lease remains live')
        try:
            locked = validate_import_pair_for_recovery_in_transaction(
                cursor,frozen.intent)
            self._still_live(cursor,claim)
        except VectorAuthorityError as error:
            raise _RecoveryHold('Exact abandonment predicates differ') from error
        try:
            close_expired_evidence_attempt_in_transaction(cursor,key,task)
        except RecoveryLeaseLost as error:
            raise _RecoveryHold('Exact abandonment predicates differ') from error
        try:
            for generation_id, present in locked:
                changed = cursor.execute('''update generation_reservations
                    set state='revoked',revoked_at=clock_timestamp()
                    where generation_id=%s and state='reserved' and revoked_at is null
                    returning generation_id''', (generation_id,)).fetchone()
                if changed is None:
                    raise VectorAuthorityError('Generation revocation CAS lost')
                if present:
                    changed = cursor.execute('''update vector_generations
                        set state='abandoned',abandoned_at=clock_timestamp()
                        where generation_id=%s and state in ('staging','sealed')
                        returning generation_id''', (generation_id,)).fetchone()
                    if changed is None:
                        raise VectorAuthorityError('Generation abandonment CAS lost')
            self._still_live(cursor,claim)
        except VectorAuthorityError as error:
            raise _RecoveryHold('Exact abandonment predicates differ') from error
        self._transition_metadata(cursor,claim,frozen,'abandoned')
        self._resolve_gate(cursor,claim)
        self._close_capture(cursor,claim,'abandoned')
        due = cursor.execute('''update import_tasks set next_attempt_at=clock_timestamp(),
            error_code=null,error_summary=null,updated_at=clock_timestamp()
            where id=%s and user_id=%s and status='retry_wait'
            and lease_version=%s and next_attempt_at is null
            and error_code='needs_reconciliation' returning id''',
            (key.task_id,key.user_id,key.task_lease_version)).fetchone()
        if due is None:
            raise _RecoveryHold('Held task scheduling CAS lost')
        self._still_live(cursor,claim)

    @_report_unavailable
    def prove_or_hold(self, claim: RecoveryClaim) -> RecoveryOutcome:
        if type(claim) is not RecoveryClaim:
            raise RecoveryLeaseLost('Exact recovery claim required')
        try:
            proof = ImportPublicationProofService(self.database).prove_exact(
                claim.user_id,claim.task_id,claim.task_lease_version)
        except PublicationProofUnknown:
            return self._record_transient(claim)
        try:
            if proof is not None:
                return self.ack_success(claim,proof)
            with self.database.transaction() as cursor:
                key, rows, now = self._claimed_rows(cursor,claim)
                frozen = self._frozen(cursor,key,rows)
                self._original_tuple(rows,frozen)
                if frozen.phase not in ('intent','document_verified','pair_sealed'):
                    raise _RecoveryHold('Terminal attempt lacks complete proof')
                ordinary, task = rows[:2]
                if task['lease_expires_at']>now or ordinary['lease_expires_at']>now:
                    self._close_capture(cursor,claim,'ordinary_live','pending',
                        max(task['lease_expires_at'],ordinary['lease_expires_at'])
                        + timedelta(seconds=1))
                    return 'retry_later'
            return self.abandon_exact(claim)
        except _RecoveryHold:
            return self._manual_hold(claim)

    @_report_unavailable
    def _manual_hold(self, claim: RecoveryClaim) -> Literal['manual_hold']:
        with self.database.transaction() as cursor:
            key, rows, _ = self._claimed_rows(cursor,claim)
            try:
                frozen = self._frozen(cursor,key,rows)
                self._still_live(cursor,claim)
                self._transition_metadata(cursor,claim,frozen,frozen.phase,'manual_hold')
            except _RecoveryHold:
                self._still_live(cursor,claim)
                header = rows[3]
                child = cursor.execute('''update import_publication_private_payloads
                    set payload_phase_version=payload_phase_version+1
                    where user_id=%s and task_id=%s and task_lease_version=%s
                    and payload_phase_version=%s returning payload_phase_version''',
                    (key.user_id,key.task_id,key.task_lease_version,
                     header['phase_version'])).fetchone()
                if child is not None:
                    changed = cursor.execute('''update import_publication_evidence
                        set phase_version=phase_version+1,observation_reason='manual_hold'
                        where user_id=%s and task_id=%s and task_lease_version=%s
                        and phase_version=%s returning phase_version''',
                        (key.user_id,key.task_id,key.task_lease_version,
                         header['phase_version'])).fetchone()
                    if changed is None:
                        raise RecoveryUnavailable('Evidence hold CAS lost')
            self._close_capture(cursor,claim,'manual_hold','manual_hold')
        return 'manual_hold'

    @_report_unavailable
    def _record_transient(self, claim: RecoveryClaim) -> RecoveryOutcome:
        with self.database.transaction() as cursor:
            _, rows, now = self._claimed_rows(cursor,claim)
            count = rows[6]['transient_count'] + 1
            if count >= 7:
                self._close_capture(cursor,claim,'manual_hold','manual_hold',
                                    transient_count=7)
                return 'manual_hold'
            due = now + timedelta(seconds=min(5 * 2 ** (count - 1), 300))
            self._close_capture(cursor,claim,'transient','pending',due,
                                transient_count=count)
        return 'retry_later'
