"""Native DB import authority. Deliberately not connected to a runnable Worker.

Publication callbacks must be short, deterministic DB-only work on the supplied
cursor, without committing. External artifacts need their own generation fence.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.import_models import ImportTaskRecord
from app.import_repository import InvalidImportTransition, _task_from_row
from app.object_store import ObjectRef
from app.postgres_coordination import (
    MutationLeaseLost, PostgresUserMutationCoordinator, UserMutationLease,
    _duration, _identity,
)
from hello_agents.memory.rag.errors import sanitize_error_message


class ImportLeaseLost(MutationLeaseLost):
    """The complete task and user ownership tuple is no longer live."""


@dataclass(frozen=True)
class ImportAttempt:
    task: ImportTaskRecord
    worker_id: str
    lease_token: UUID
    lease_version: int
    user_lease: UserMutationLease
    source: ObjectRef
    bucket: str
    expires_at: datetime


def _now(cursor):
    return cursor.execute('select clock_timestamp() as now').fetchone()['now']


def _text(value):
    return value.astimezone(timezone.utc).isoformat()


# Cast existing timestamp text; lexical ordering does not preserve offsets.
_DUE = """t.status in ('queued','retry_wait')
    and (t.next_attempt_at is null or t.next_attempt_at::timestamptz <= clock_timestamp())
    and exists(select 1 from import_objects o where o.task_id=t.id and o.user_id=t.user_id)"""


class PostgresImportLeaseRepository:
    def __init__(self, database):
        self.database = database
        self.coordinator = PostgresUserMutationCoordinator(database)

    def _attempt(self, cursor, row, lease):
        source = cursor.execute('select * from import_objects where task_id=%s and user_id=%s',
                                (row['id'], row['user_id'])).fetchone()
        if source is None:
            raise ImportLeaseLost('Import source reference is missing')
        return ImportAttempt(_task_from_row(row), row['claimed_by'], row['lease_token'],
            row['lease_version'], lease, ObjectRef(source['object_key'], source['sha256'],
            source['size_bytes'], source['version_id']), source['bucket'], row['lease_expires_at'])

    @staticmethod
    def _touch(cursor, row):
        cursor.execute('update import_batches set updated_at=%s where id=%s and user_id=%s',
                       (_text(_now(cursor)), row['batch_id'], row['user_id']))

    def claim_next(self, worker_id, lease_seconds=60):
        _duration(lease_seconds)
        if not _identity(worker_id):
            raise ValueError('worker_id must be a nonempty string')
        with self.database.transaction() as cursor:
            candidates = cursor.execute(f'''select u.id from users u
                left join import_user_schedule s on s.user_id=u.id
                where u.status='active' and exists(select 1 from import_tasks t
                    where t.user_id=u.id and {_DUE})
                and not exists(select 1 from import_tasks r where r.user_id=u.id and r.status='running')
                order by s.last_claimed_at nulls first,u.id''').fetchall()
        # Separate short transactions do not retain busy users' locks. No LIMIT:
        # a long prefix of busy mutation owners cannot starve an eligible user.
        for candidate in candidates:
            with self.database.transaction() as cursor:
                locked = cursor.execute('select id from users where id=%s for update skip locked',
                                        (candidate['id'],)).fetchone()
                if locked is None:
                    continue
                if cursor.execute('select status from users where id=%s', (candidate['id'],)).fetchone()['status'] != 'active':
                    continue
                if cursor.execute("select id from import_tasks where user_id=%s and status='running'", (candidate['id'],)).fetchone():
                    continue
                # Select before acquisition, then lock task only after user lease.
                task = cursor.execute(f'select t.id from import_tasks t where t.user_id=%s and {_DUE} order by t.created_at::timestamptz,t.id limit 1',
                                      (candidate['id'],)).fetchone()
                if task is None:
                    continue
                lease = self.coordinator.acquire_in_transaction(cursor, candidate['id'], worker_id, lease_seconds)
                if lease is None:
                    continue
                cursor.execute('select id from import_tasks where id=%s for update', (task['id'],))
                row = cursor.execute(f'select t.* from import_tasks t where t.id=%s and {_DUE}', (task['id'],)).fetchone()
                if row is None:
                    # Unexpected unfenced writer: roll back acquisition as well.
                    raise ImportLeaseLost('Import eligibility changed during claim')
                now = _now(cursor)
                row = cursor.execute('''update import_tasks set status='running',stage='queued',progress=0,
                    claimed_by=%s,lease_token=%s,lease_version=lease_version+1,
                    user_lease_token=%s,user_lease_version=%s,heartbeat_at=%s,
                    lease_expires_at=%s + %s * interval '1 second',
                    total_attempt_count=total_attempt_count+1,next_attempt_at=null,
                    error_code=null,error_summary=null,finished_at=null,started_at=%s,updated_at=%s
                    where id=%s returning *''', (worker_id,uuid4(),lease.lease_token,lease.lease_version,
                    now,now,lease_seconds,_text(now),_text(now),row['id'])).fetchone()
                cursor.execute('''insert into import_task_attempts(task_id,user_id,lease_version,worker_id,
                    lease_token,user_lease_token,user_lease_version,started_at,heartbeat_at,last_stage)
                    values(%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued')''',
                    (row['id'],row['user_id'],row['lease_version'],worker_id,row['lease_token'],
                     lease.lease_token,lease.lease_version,now,now))
                cursor.execute('''insert into import_user_schedule values(%s,%s)
                    on conflict(user_id) do update set last_claimed_at=excluded.last_claimed_at''', (row['user_id'],now))
                self._touch(cursor,row)
                attempt = self._attempt(cursor,row,lease)
                self._live(cursor,attempt)
                return attempt
        return None

    def _live(self, cursor, attempt, *, status='running', ended=False):
        if (not isinstance(attempt, ImportAttempt) or not isinstance(attempt.task, ImportTaskRecord)
                or not _identity(attempt.task.task_id) or not _identity(attempt.task.user_id)
                or not _identity(attempt.worker_id) or not isinstance(attempt.lease_token, UUID)
                or type(attempt.lease_version) is not int or attempt.lease_version < 1
                or not isinstance(attempt.user_lease, UserMutationLease)
                or attempt.user_lease.user_id != attempt.task.user_id
                or attempt.user_lease.owner != attempt.worker_id):
            raise ImportLeaseLost('Invalid import attempt handle')
        try:
            self.coordinator.require_live_in_transaction(cursor,attempt.user_lease)
        except MutationLeaseLost as exc:
            raise ImportLeaseLost(str(exc)) from exc
        cursor.execute('select id from import_tasks where id=%s and user_id=%s for update',
                       (attempt.task.task_id,attempt.task.user_id))
        cursor.execute('select task_id from import_task_attempts where task_id=%s and lease_version=%s for update',
                       (attempt.task.task_id,attempt.lease_version))
        # Recheck both authorities after ALL potentially blocking row locks.
        try:
            self.coordinator.require_live_in_transaction(cursor,attempt.user_lease)
        except MutationLeaseLost as exc:
            raise ImportLeaseLost(str(exc)) from exc
        row = cursor.execute('''with authority_clock as materialized(select clock_timestamp() as now)
            select t.* from import_tasks t join import_task_attempts a
            on a.task_id=t.id and a.user_id=t.user_id and a.lease_version=t.lease_version
            join user_mutation_leases u on u.user_id=t.user_id
            cross join authority_clock c
            where t.id=%s and t.user_id=%s and t.claimed_by=%s and t.lease_token=%s
            and t.lease_version=%s and t.user_lease_token=%s and t.user_lease_version=%s
            and t.status=%s and t.lease_expires_at>c.now
            and u.owner=t.claimed_by and u.lease_token=t.user_lease_token
            and u.lease_version=t.user_lease_version and u.lease_expires_at>c.now
            and a.worker_id=t.claimed_by and a.lease_token=t.lease_token
            and a.user_lease_token=t.user_lease_token and a.user_lease_version=t.user_lease_version
            and (a.ended_at is not null)=%s''',
            (attempt.task.task_id,attempt.task.user_id,attempt.worker_id,attempt.lease_token,
             attempt.lease_version,attempt.user_lease.lease_token,attempt.user_lease.lease_version,status,ended)).fetchone()
        if row is None:
            raise ImportLeaseLost('Import attempt is no longer live')
        return row

    def heartbeat(self, attempt, lease_seconds=60):
        _duration(lease_seconds)
        with self.database.transaction() as cursor:
            cursor.execute('begin')
            self._live(cursor,attempt)
            try:
                lease = self.coordinator.heartbeat_in_transaction(cursor,attempt.user_lease,lease_seconds)
            except MutationLeaseLost as exc:
                raise ImportLeaseLost(str(exc)) from exc
            row = cursor.execute('''with t as materialized(select clock_timestamp() as now)
                update import_tasks set heartbeat_at=t.now,lease_expires_at=t.now + %s * interval '1 second'
                from t where id=%s and lease_expires_at>t.now returning import_tasks.*''',
                (lease_seconds,attempt.task.task_id)).fetchone()
            if row is None:
                raise ImportLeaseLost('Task expired before heartbeat')
            cursor.execute('update import_task_attempts set heartbeat_at=%s where task_id=%s and lease_version=%s',
                           (row['heartbeat_at'],row['id'],row['lease_version']))
            self._live(cursor,attempt)
            return self._attempt(cursor,row,lease)

    def update_progress(self, attempt, stage, progress):
        if stage not in ('staged','parsing','chunking','embedding','persisting','committing') or type(progress) is not int or not 0 <= progress <= 100:
            raise ValueError('Expected a processing stage and integer progress between 0 and 100')
        with self.database.transaction() as cursor:
            cursor.execute('begin')
            row = self._live(cursor,attempt)
            if (row['stage']=='committing') != (stage=='committing'):
                raise InvalidImportTransition('Use try_begin_committing to enter committing; cannot leave it')
            return self._progress(cursor,attempt,row,stage,progress)

    def _progress(self,cursor,attempt,row,stage,progress):
        cursor.execute('update import_tasks set stage=%s,progress=%s,updated_at=%s where id=%s',
                       (stage,progress,_text(_now(cursor)),row['id']))
        cursor.execute('update import_task_attempts set last_stage=%s where task_id=%s and lease_version=%s',
                       (stage,row['id'],row['lease_version']))
        self._touch(cursor,row)
        return _task_from_row(self._live(cursor,attempt))

    def try_begin_committing(self,attempt,*,live: object | None = None):
        with self.database.transaction() as cursor:
            cursor.execute('begin')
            row = self._live(cursor,attempt)
            if live is None:
                from app.import_publication_evidence import require_no_gate_in_transaction
                require_no_gate_in_transaction(cursor, attempt.task.user_id)
            else:
                from app.import_memory_publication import _require_live_issuer
                from app.import_publication_evidence import PostgresImportPublicationEvidenceRepository
                service, issue = _require_live_issuer(live)
                if (self is not issue.imports or attempt is not issue.original_attempt
                        or issue.plan.attempt != attempt):
                    raise ImportLeaseLost('Original durable attempt issuer required')
                repository = PostgresImportPublicationEvidenceRepository(self.database)
                current = repository._read_exact_in_cursor(cursor, issue.key)
                if current is None:
                    raise ImportLeaseLost('Durable publication evidence is missing')
                _, _, evidence = repository._live_evidence(cursor, live,
                    expected_phase='pair_sealed',
                    expected_version=current.phase_version)
                service._require_pair_sealed(live, evidence)
                repository.require_sealed_reservations_in_transaction(cursor,
                    live, evidence)
                repository._verify_current(cursor, issue.plan.attempt,
                    evidence.intent)
            if row['cancel_requested_at'] is not None or row['stage']=='committing':
                return False
            self._progress(cursor,attempt,row,'committing',row['progress'])
            return True

    def complete(self,attempt,publish=None,*,terminal_work: object | None = None):
        with self.database.transaction() as cursor:
            cursor.execute('begin')
            row = self._live(cursor,attempt)
            if row['stage']!='committing':
                raise InvalidImportTransition('Completion requires committing')
            if terminal_work is None:
                from app.import_publication_evidence import require_no_gate_in_transaction
                require_no_gate_in_transaction(cursor, attempt.task.user_id)
                if publish is None:
                    raise TypeError('Publication callback required')
                publish(cursor)
            else:
                from app.import_memory_publication import (
                    _close_terminal_admission, _create_terminal_admission,
                    _require_terminal_work,
                )
                from app.import_publication_evidence import PostgresImportPublicationEvidenceRepository
                service, work_issue = _require_terminal_work(terminal_work)
                live = work_issue.live
                issue = service._require_live_issue(live)
                if (publish is not None or self is not issue.imports
                        or attempt is not issue.original_attempt
                        or issue.plan.attempt != attempt):
                    raise ImportLeaseLost('Only original fixed terminal work is admitted')
                repository = PostgresImportPublicationEvidenceRepository(self.database)
                current = repository._read_exact_in_cursor(cursor, issue.key)
                if current is None:
                    raise ImportLeaseLost('Durable publication evidence is missing')
                _, _, evidence = repository._live_evidence(cursor, live,
                    expected_phase='pair_sealed',
                    expected_version=current.phase_version)
                service._require_pair_sealed(live, evidence)
                repository.require_sealed_reservations_in_transaction(cursor,
                    live, evidence)
                repository._verify_current(cursor, issue.plan.attempt,
                    evidence.intent)
                admission = _create_terminal_admission(terminal_work, cursor, evidence)
                try:
                    work_issue.callback(cursor, admission)
                finally:
                    _close_terminal_admission(admission)
            row = self._live(cursor,attempt)
            if row['stage'] != 'committing':
                raise ImportLeaseLost('Committing stage changed during publication')
            return self._finish(cursor,attempt,row,'succeeded','succeeded',progress=100)

    def _finish(self,cursor,attempt,row,status,reason,*,progress=None,error_code=None,error_summary=None,delay=None,unstarted=False):
        now = _now(cursor)
        retry = status=='retry_wait'
        requeue = status=='queued'
        stage = 'queued' if retry or requeue else status
        deadline = cursor.execute("select clock_timestamp() + %s * interval '1 second' as deadline", (delay,)).fetchone()['deadline'] if retry else None
        cursor.execute('''update import_tasks set status=%s,stage=%s,progress=%s,error_code=%s,error_summary=%s,
            next_attempt_at=%s,finished_at=%s,updated_at=%s,started_at=%s,
            auto_retry_count=auto_retry_count+%s,total_attempt_count=total_attempt_count-%s where id=%s''',
            (status,stage,row['progress'] if progress is None else progress,error_code,error_summary,
             _text(deadline) if deadline else None,None if retry or requeue else _text(now),_text(now),
             None if requeue else row['started_at'],int(retry),int(unstarted),row['id']))
        cursor.execute('''update import_task_attempts set ended_at=%s,end_reason=%s,error_code=%s,
            error_summary=%s,last_stage=%s where task_id=%s and lease_version=%s''',
            (now,reason,error_code,error_summary,stage,row['id'],row['lease_version']))
        self._touch(cursor,row)
        result = self._live(cursor,attempt,status=status,ended=True)
        try:
            self.coordinator.release_in_transaction(cursor,attempt.user_lease)
        except MutationLeaseLost as exc:
            raise ImportLeaseLost(str(exc)) from exc
        # Keep tokens/version as durable evidence, but expire task authority too.
        closed = cursor.execute("""with t as materialized(select clock_timestamp() as now)
            update import_tasks j set lease_expires_at=t.now from t
            where j.id=%s and j.user_id=%s and j.claimed_by=%s and j.lease_token=%s
            and j.lease_version=%s and j.user_lease_token=%s and j.user_lease_version=%s
            and j.status=%s and j.lease_expires_at>t.now returning j.id""",
            (row['id'],row['user_id'],attempt.worker_id,attempt.lease_token,attempt.lease_version,
             attempt.user_lease.lease_token,attempt.user_lease.lease_version,status)).fetchone()
        if closed is None:
            raise ImportLeaseLost('Task expired or changed during user lease release')
        return _task_from_row(result)

    def fail(self,attempt,error_code,error_summary,retry_delay_seconds=None):
        if retry_delay_seconds is not None and (type(retry_delay_seconds) is not int or not 0 <= retry_delay_seconds <= 86400):
            raise ValueError('retry_delay_seconds must be an integer between 0 and 86400')
        summary = sanitize_error_message(error_summary)[:500]
        with self.database.transaction() as cursor:
            cursor.execute('begin')
            row = self._live(cursor,attempt)
            retry = retry_delay_seconds is not None and row['auto_retry_count']<row['max_auto_retries']
            return self._finish(cursor,attempt,row,'retry_wait' if retry else 'failed',
                'retry_wait' if retry else 'failed',error_code=error_code,error_summary=summary,delay=retry_delay_seconds)

    def cancel(self,attempt):
        with self.database.transaction() as cursor:
            cursor.execute('begin')
            row = self._live(cursor,attempt)
            if row['cancel_requested_at'] is None or row['stage']=='committing':
                raise InvalidImportTransition('Cancellation requires pending request before committing')
            return self._finish(cursor,attempt,row,'cancelled','cancelled')

    def release_unstarted(self,attempt):
        with self.database.transaction() as cursor:
            cursor.execute('begin')
            row = self._live(cursor,attempt)
            if row['stage']!='queued' or row['progress']!=0:
                raise InvalidImportTransition('Attempt already started')
            return self._finish(cursor,attempt,row,'queued','unstarted',progress=0,unstarted=True)

    def recover_expired(self,limit=100):
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError('limit must be an integer between 1 and 10000')
        with self.database.transaction() as cursor:
            self.coordinator._isolation(cursor)
            candidates = cursor.execute("select id,user_id from import_tasks where status='running' and lease_version>0 order by user_id,id").fetchall()
        recovered = 0
        for candidate in candidates:
            if recovered>=limit:
                break
            with self.database.transaction() as cursor:
                # A pool may return a different connection than the scan used.
                # Reject autocommit/isolation drift before taking any locks.
                self.coordinator._isolation(cursor)
                if cursor.execute('select id from users where id=%s for update skip locked',(candidate['user_id'],)).fetchone() is None:
                    continue
                if cursor.execute('select status from users where id=%s',(candidate['user_id'],)).fetchone()['status']!='active':
                    continue
                cursor.execute('select user_id from user_mutation_leases where user_id=%s for update',(candidate['user_id'],))
                cursor.execute('select id from import_tasks where id=%s for update',(candidate['id'],))
                row = cursor.execute("select * from import_tasks where id=%s and status='running' and lease_version>0",(candidate['id'],)).fetchone()
                if row is None:
                    continue
                audit = cursor.execute('''select * from import_task_attempts where task_id=%s and user_id=%s
                    and lease_version=%s and lease_token=%s and worker_id=%s and user_lease_token=%s
                    and user_lease_version=%s and ended_at is null for update''',
                    (row['id'],row['user_id'],row['lease_version'],row['lease_token'],row['claimed_by'],row['user_lease_token'],row['user_lease_version'])).fetchone()
                if audit is None:
                    continue  # Legacy/malformed ownership is never guessed.
                live = cursor.execute('''select 1 from import_tasks t join user_mutation_leases u on u.user_id=t.user_id
                    where t.id=%s and t.lease_expires_at>clock_timestamp() and u.lease_expires_at>clock_timestamp()
                    and u.owner=t.claimed_by and u.lease_token=t.user_lease_token and u.lease_version=t.user_lease_version''',(row['id'],)).fetchone()
                if live:
                    continue
                now = _now(cursor)
                cursor.execute('''update import_task_attempts set ended_at=%s,end_reason='lease_expired'
                    where task_id=%s and lease_version=%s''',(now,row['id'],row['lease_version']))
                cursor.execute('''update import_tasks set status='queued',stage='queued',progress=0,
                    started_at=null,finished_at=null,next_attempt_at=null,updated_at=%s,
                    error_code='process_interrupted',error_summary='Import processing was interrupted',
                    lease_expires_at=%s where id=%s''',(_text(now),now,row['id']))
                cursor.execute('''update user_mutation_leases set lease_expires_at=%s where user_id=%s
                    and owner=%s and lease_token=%s and lease_version=%s''',
                    (now,row['user_id'],row['claimed_by'],row['user_lease_token'],row['user_lease_version']))
                self._touch(cursor,row)
                recovered += 1
        return recovered
