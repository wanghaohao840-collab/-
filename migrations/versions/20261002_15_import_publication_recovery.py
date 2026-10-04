"""Separate permanent publication identity from private proof and recovery metadata."""

from alembic import op

from app.import_publication_evidence import decode_intent, _validate_stored_slots

revision = '20261002_15'
down_revision = '20261002_14'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''create table import_publication_private_payloads (
        user_id text not null, task_id text not null,
        task_lease_version bigint not null check(task_lease_version > 0),
        intent_payload bytea not null check(octet_length(intent_payload) <= 8388608),
        canonical_bytes bigint not null check(canonical_bytes between 1 and 67108864),
        document_slot bytea check(octet_length(document_slot) <= 262144),
        rag_sealed_slot bytea check(octet_length(rag_sealed_slot) <= 262144),
        episode_sealed_slot bytea check(octet_length(episode_sealed_slot) <= 262144),
        terminal_slot bytea check(octet_length(terminal_slot) <= 262144),
        payload_phase_version bigint not null check(payload_phase_version > 0),
        primary key(user_id,task_id,task_lease_version),
        foreign key(user_id,task_id,task_lease_version) references
          import_publication_evidence(user_id,task_id,task_lease_version)
          on delete no action deferrable initially deferred,
        check(octet_length(intent_payload)
          + coalesce(octet_length(document_slot),0)
          + coalesce(octet_length(rag_sealed_slot),0)
          + coalesce(octet_length(episode_sealed_slot),0)
          + coalesce(octet_length(terminal_slot),0) <= 9437184)
    )''')
    op.execute('''insert into import_publication_private_payloads
        (user_id,task_id,task_lease_version,intent_payload,canonical_bytes,
         document_slot,rag_sealed_slot,episode_sealed_slot,terminal_slot,payload_phase_version)
        select user_id,task_id,task_lease_version,intent_payload,canonical_bytes,
         document_slot,rag_sealed_slot,episode_sealed_slot,terminal_slot,phase_version
        from import_publication_evidence''')
    op.execute('''do $$ begin
        if exists(select 1 from import_publication_evidence e full join
            import_publication_private_payloads p using(user_id,task_id,task_lease_version)
            where e.user_id is null or p.user_id is null or
              (e.intent_payload,e.canonical_bytes,e.document_slot,e.rag_sealed_slot,
               e.episode_sealed_slot,e.terminal_slot,e.phase_version) is distinct from
              (p.intent_payload,p.canonical_bytes,p.document_slot,p.rag_sealed_slot,
               p.episode_sealed_slot,p.terminal_slot,p.payload_phase_version)) then
            raise exception 'Private evidence backfill mismatch';
        end if;
        if exists(select 1 from import_publication_evidence e where
          e.phase in ('intent','document_verified','pair_sealed','terminal_committed')
          and not exists(select 1 from user_publication_gates g where
            g.user_id=e.user_id and g.task_id=e.task_id and
            g.task_lease_version=e.task_lease_version and g.status='unresolved')) then
            raise exception 'Unresolved evidence lacks gate';
        end if;
    end $$''')
    _validate_legacy_evidence()

    op.execute('''create table import_publication_recovery_queue (
        user_id text not null, task_id text not null,
        task_lease_version bigint not null check(task_lease_version > 0),
        queued_at timestamptz not null default clock_timestamp(),
        due_at timestamptz,
        state text not null check(state in ('pending','claimed','manual_hold','resolved')),
        queue_version bigint not null check(queue_version > 0),
        claim_token uuid, claim_expires_at timestamptz,last_claimed_at timestamptz,
        reason_code text not null check(reason_code in
          ('seed','unknown','ordinary_live','transient','manual_hold','proved_succeeded','abandoned')),
        transient_count integer not null default 0 check(transient_count between 0 and 7),
        updated_at timestamptz not null default clock_timestamp(),
        primary key(user_id,task_id,task_lease_version),
        foreign key(user_id,task_id,task_lease_version) references
          import_publication_evidence(user_id,task_id,task_lease_version),
        check((state='pending' and due_at is not null and claim_token is null
                 and claim_expires_at is null)
          or (state='claimed' and due_at is null and claim_token is not null
                 and claim_expires_at is not null and last_claimed_at is not null)
          or (state in ('manual_hold','resolved') and due_at is null
                 and claim_token is null and claim_expires_at is null))
    )''')
    op.execute('''create index import_publication_recovery_due_15 on
        import_publication_recovery_queue(due_at,queued_at,user_id,task_id)
        where state='pending' ''')
    op.execute('''create index import_publication_recovery_expired_claim_15 on
        import_publication_recovery_queue(claim_expires_at,queued_at,user_id,task_id)
        where state='claimed' ''')
    op.execute('''create table import_publication_recovery_leases (
        user_id text not null,task_id text not null,
        task_lease_version bigint not null check(task_lease_version > 0),
        owner text not null check(length(btrim(owner)) > 0),
        token uuid not null unique,version bigint not null check(version > 0),
        heartbeat_at timestamptz not null,expires_at timestamptz not null,
        queue_claim_token uuid not null,queue_version bigint not null check(queue_version > 0),
        primary key(user_id,task_id,task_lease_version),
        foreign key(user_id,task_id,task_lease_version) references
          import_publication_evidence(user_id,task_id,task_lease_version)
    )''')
    op.execute('''create table import_publication_recovery_token_issuance (
        token uuid primary key,kind text not null check(kind in ('queue','recovery')),
        user_id text not null,task_id text not null,
        task_lease_version bigint not null check(task_lease_version > 0),
        issued_version bigint not null check(issued_version > 0),
        issued_at timestamptz not null default clock_timestamp(),
        foreign key(user_id,task_id,task_lease_version) references
          import_publication_recovery_queue(user_id,task_id,task_lease_version)
          on delete no action,
        unique(kind,user_id,task_id,task_lease_version,issued_version)
    )''')
    op.execute('''create table import_publication_recovery_schedule (
        user_id text primary key references users(id),
        last_claimed_at timestamptz not null
    )''')
    op.execute('''insert into import_publication_recovery_schedule(user_id,last_claimed_at)
        select distinct e.user_id,'epoch'::timestamptz from import_publication_evidence e
        join user_publication_gates g using(user_id,task_id,task_lease_version)
        where g.status='unresolved' ''')
    op.execute('''insert into import_publication_recovery_queue
        (user_id,task_id,task_lease_version,due_at,state,queue_version,reason_code)
        select e.user_id,e.task_id,e.task_lease_version,
          greatest(t.lease_expires_at,u.lease_expires_at),'pending',1,'seed'
        from import_publication_evidence e
        join user_publication_gates g using(user_id,task_id,task_lease_version)
        join import_tasks t on t.id=e.task_id and t.user_id=e.user_id
        join user_mutation_leases u on u.user_id=e.user_id
        where g.status='unresolved'
          and t.lease_version=e.task_lease_version
          and t.lease_token=e.task_lease_token
          and t.user_lease_token=e.user_lease_token
          and t.user_lease_version=e.user_lease_version
          and u.lease_token=e.user_lease_token
          and u.lease_version=e.user_lease_version''')
    op.execute('''do $$ begin
        if exists(select 1 from import_publication_evidence e
          join user_publication_gates g using(user_id,task_id,task_lease_version)
          where g.status='unresolved' and not exists(select 1 from
            import_publication_recovery_queue q where q.user_id=e.user_id
            and q.task_id=e.task_id and q.task_lease_version=e.task_lease_version)) then
          raise exception 'Recovery queue backfill mismatch';
        end if;
    end $$''')

    _install_payload_guards()
    _install_queue_guards()
    _install_dependency_guards()
    op.execute('''alter table import_publication_evidence
        drop column intent_payload,drop column canonical_bytes,
        drop column document_slot,drop column rag_sealed_slot,
        drop column episode_sealed_slot,drop column terminal_slot''')


def downgrade():
    raise RuntimeError('Publication evidence identity and recovery tokens cannot be discarded')


def _validate_legacy_evidence():
    """Reject corrupt _14 authority before the private header columns disappear."""
    connection = op.get_bind()
    rows = connection.exec_driver_sql('''select e.*, p.payload_phase_version,
        g.status as gate_status, g.resolved_at as gate_resolved_at
        from import_publication_evidence e join import_publication_private_payloads p
          using(user_id,task_id,task_lease_version)
        left join user_publication_gates g using(user_id,task_id,task_lease_version)
        order by e.user_id,e.task_id,e.task_lease_version''').mappings()
    for row in rows:
        intent = decode_intent(bytes(row['intent_payload']),
            canonical_bytes=row['canonical_bytes'], digest=row['intent_hash'])
        attempt = intent['attempt']
        if (row['schema_version'] != 1
                or row['intent_format'] != 'canonical-json-zlib-1'
                or row['phase_version'] != row['payload_phase_version']
                or (attempt['user_id'],attempt['task_id'],attempt['task_lease_version'],
                    attempt['document_id'],attempt['worker_id'],attempt['task_lease_token'],
                    attempt['user_lease_token'],attempt['user_lease_version']) !=
                   (row['user_id'],row['task_id'],row['task_lease_version'],
                    row['document_id'],row['worker_id'],str(row['task_lease_token']),
                    str(row['user_lease_token']),row['user_lease_version'])):
            raise RuntimeError('Legacy publication header differs from intent')
        slots = tuple(bytes(row[name]) if row[name] is not None else None for name in
            ('document_slot','rag_sealed_slot','episode_sealed_slot','terminal_slot'))
        _validate_stored_slots(intent,row['intent_hash'],row['phase'],
            row['phase_version'],row['observation_reason'],slots)
        resolved = row['phase'] in ('proved_succeeded','abandoned')
        if (row['gate_status'] != ('resolved' if resolved else 'unresolved')
                or (row['gate_resolved_at'] is not None) != resolved):
            raise RuntimeError('Legacy publication gate disposition differs')
        reservations = connection.exec_driver_sql('''select * from generation_reservations
            where user_id=%s and task_id=%s and task_lease_version=%s''',
            (row['user_id'],row['task_id'],row['task_lease_version'])).mappings().all()
        if len(reservations) != 2 or {r['vector_kind'] for r in reservations} != {'rag','episode'}:
            raise RuntimeError('Legacy publication reservations are incomplete')
        for reservation in reservations:
            scope = intent['scopes'][reservation['vector_kind']]
            revoked = row['phase'] == 'abandoned'
            if ((str(reservation['generation_id']),reservation['namespace'],
                    reservation['index_key'],reservation['base_revision'],
                    reservation['owner'],str(reservation['user_lease_token']),
                    reservation['user_lease_version'],reservation['state'],
                    reservation['revoked_at'] is not None) !=
                   (scope['candidate_id'],scope['namespace'],scope['index_key'],
                    scope['head']['revision'],attempt['worker_id'],
                    attempt['user_lease_token'],attempt['user_lease_version'],
                    'revoked' if revoked else 'reserved',revoked)):
                raise RuntimeError('Legacy publication reservation differs')


def _install_payload_guards():
    op.execute('drop trigger import_publication_evidence_guard on import_publication_evidence')
    op.execute('''create or replace function import_publication_evidence_guard()
      returns trigger language plpgsql as $$ begin
        if tg_op='DELETE' then
          raise exception 'Import publication proof cannot be deleted';
        end if;
        if tg_op='INSERT' then
          if new.phase <> 'intent' or new.phase_version <> 1
             or new.observation_reason is not null then
            raise exception 'Import publication intent must start empty';
          end if;
        else
          if (new.user_id,new.task_id,new.task_lease_version,new.document_id,
              new.worker_id,new.task_lease_token,new.user_lease_token,
              new.user_lease_version,new.schema_version,new.intent_format,
              new.intent_hash,new.created_at) is distinct from
             (old.user_id,old.task_id,old.task_lease_version,old.document_id,
              old.worker_id,old.task_lease_token,old.user_lease_token,
              old.user_lease_version,old.schema_version,old.intent_format,
              old.intent_hash,old.created_at) then
            raise exception 'Import publication intent is immutable';
          end if;
          if new.phase_version <> old.phase_version+1 then
            raise exception 'Import publication phase CAS required';
          end if;
          if old.phase in ('proved_succeeded','abandoned') then
            raise exception 'Terminal import publication proof is immutable';
          end if;
          if new.phase <> old.phase and not (
             (old.phase='intent' and new.phase in ('document_verified','abandoned'))
             or (old.phase='document_verified' and new.phase in ('pair_sealed','abandoned'))
             or (old.phase='pair_sealed' and new.phase in ('terminal_committed','abandoned'))
             or (old.phase='terminal_committed' and new.phase='proved_succeeded')) then
            raise exception 'Illegal import publication phase transition';
          end if;
          new.updated_at := clock_timestamp();
        end if;
        return new;
      end $$''')
    op.execute('''create trigger import_publication_evidence_guard
      before insert or update or delete on import_publication_evidence
      for each row execute function import_publication_evidence_guard()''')
    op.execute('''create function import_publication_private_guard_15()
      returns trigger language plpgsql as $$ begin
        if tg_op='DELETE' then
          raise exception 'Private publication proof cleanup is disabled';
        end if;
        if tg_op='INSERT' then
          if new.payload_phase_version <> 1 or new.document_slot is not null
             or new.rag_sealed_slot is not null or new.episode_sealed_slot is not null
             or new.terminal_slot is not null then
            raise exception 'Private intent must start empty';
          end if;
        else
          if (new.user_id,new.task_id,new.task_lease_version,new.intent_payload,
              new.canonical_bytes) is distinct from
             (old.user_id,old.task_id,old.task_lease_version,old.intent_payload,
              old.canonical_bytes) then
            raise exception 'Private intent is immutable';
          end if;
          if new.payload_phase_version <> old.payload_phase_version+1 then
            raise exception 'Private phase CAS required';
          end if;
          if (old.document_slot is not null and new.document_slot is distinct from old.document_slot)
             or (old.rag_sealed_slot is not null and new.rag_sealed_slot is distinct from old.rag_sealed_slot)
             or (old.episode_sealed_slot is not null and new.episode_sealed_slot is distinct from old.episode_sealed_slot)
             or (old.terminal_slot is not null and new.terminal_slot is distinct from old.terminal_slot) then
            raise exception 'Private proof slots are write once';
          end if;
        end if;
        return new;
      end $$''')
    op.execute('''create trigger import_publication_private_guard_15
      before insert or update or delete on import_publication_private_payloads
      for each row execute function import_publication_private_guard_15()''')
    op.execute('''create function import_publication_complete_15(
      target_user text,target_task text,target_version bigint)
      returns void language plpgsql as $$
      declare h import_publication_evidence%rowtype;
              p import_publication_private_payloads%rowtype;
      begin
        select * into h from import_publication_evidence where user_id=target_user
          and task_id=target_task and task_lease_version=target_version;
        select * into p from import_publication_private_payloads where user_id=target_user
          and task_id=target_task and task_lease_version=target_version;
        if h.user_id is null or p.user_id is null or h.phase_version<>p.payload_phase_version then
          raise exception 'Publication header/private phase mismatch';
        end if;
        if (h.phase='intent' and (p.document_slot is not null
             or p.rag_sealed_slot is not null or p.episode_sealed_slot is not null
             or p.terminal_slot is not null))
          or (h.phase='document_verified' and (p.document_slot is null
             or p.episode_sealed_slot is not null or p.terminal_slot is not null))
          or (h.phase='pair_sealed' and (p.document_slot is null
             or p.rag_sealed_slot is null or p.episode_sealed_slot is null
             or p.terminal_slot is not null))
          or (h.phase in ('terminal_committed','proved_succeeded') and
             (p.document_slot is null or p.rag_sealed_slot is null
              or p.episode_sealed_slot is null or p.terminal_slot is null))
          or (h.phase='abandoned' and (p.terminal_slot is not null
             or (p.document_slot is null and
                 (p.rag_sealed_slot is not null or p.episode_sealed_slot is not null))
             or (p.episode_sealed_slot is not null and p.rag_sealed_slot is null))) then
          raise exception 'Publication phase/private slot shape mismatch';
        end if;
      end $$''')
    op.execute('''create function import_publication_complete_event_15()
      returns trigger language plpgsql as $$ begin
        perform import_publication_complete_15(new.user_id,new.task_id,new.task_lease_version);
        return null;
      end $$''')
    op.execute('''create constraint trigger import_publication_header_complete_15
      after insert or update on import_publication_evidence
      deferrable initially deferred for each row
      execute function import_publication_complete_event_15()''')
    op.execute('''create constraint trigger import_publication_payload_complete_15
      after insert or update on import_publication_private_payloads
      deferrable initially deferred for each row
      execute function import_publication_complete_event_15()''')
    op.execute('''create function import_publication_abandon_history_guard_15()
      returns trigger language plpgsql as $$
      declare final_phase text;final_version bigint;
      begin
        select phase,phase_version into final_phase,final_version
          from import_publication_evidence where user_id=new.user_id
          and task_id=new.task_id and task_lease_version=new.task_lease_version;
        if not found or final_version<new.payload_phase_version then
          raise exception 'Child event lacks paired header version';
        end if;
        if final_phase='abandoned' and final_version=new.payload_phase_version
           and (new.document_slot,new.rag_sealed_slot,new.episode_sealed_slot,new.terminal_slot)
               is distinct from
               (old.document_slot,old.rag_sealed_slot,old.episode_sealed_slot,old.terminal_slot) then
          raise exception 'Abandon cannot append publication proof';
        end if;
        return null;
      end $$''')
    op.execute('''create constraint trigger import_publication_abandon_history_15
      after update on import_publication_private_payloads
      deferrable initially deferred for each row
      execute function import_publication_abandon_history_guard_15()''')
    op.execute('''do $$ declare r record; begin
      for r in select user_id,task_id,task_lease_version from import_publication_evidence loop
        perform import_publication_complete_15(r.user_id,r.task_id,r.task_lease_version);
      end loop;
    end $$''')


def _install_queue_guards():
    op.execute('''create function import_publication_recovery_queue_guard_15()
      returns trigger language plpgsql as $$
      declare now_at timestamptz;
      begin
        if tg_op='DELETE' then
          raise exception 'Recovery queue identity is permanent';
        end if;
        if tg_op='INSERT' then
          if new.state<>'pending' or new.queue_version<>1
             or new.claim_token is not null then
            raise exception 'Recovery queue must start pending';
          end if;
          return new;
        end if;
        if (new.user_id,new.task_id,new.task_lease_version,new.queued_at)
           is distinct from
           (old.user_id,old.task_id,old.task_lease_version,old.queued_at) then
          raise exception 'Recovery queue identity is immutable';
        end if;
        if old.state in ('manual_hold','resolved') then
          raise exception 'Recovery queue disposition is permanent';
        end if;
        now_at := clock_timestamp();
        if old.state='claimed' and new.state='claimed'
           and new.queue_version=old.queue_version
           and new.claim_token=old.claim_token then
          if old.claim_expires_at<=now_at or new.claim_expires_at<=old.claim_expires_at
             or (new.last_claimed_at,new.reason_code,new.transient_count,new.due_at)
                is distinct from
                (old.last_claimed_at,old.reason_code,old.transient_count,old.due_at)
             or not exists(select 1 from import_publication_recovery_leases l
                where l.user_id=old.user_id and l.task_id=old.task_id
                and l.task_lease_version=old.task_lease_version
                and l.queue_claim_token=old.claim_token
                and l.queue_version=old.queue_version
                and l.expires_at>now_at) then
            raise exception 'Recovery queue pure renewal differs';
          end if;
        else
          if new.queue_version<>old.queue_version+1 then
            raise exception 'Recovery queue CAS required';
          end if;
          if not ((old.state='pending' and new.state in ('pending','claimed'))
             or (old.state='claimed' and new.state in
                 ('pending','claimed','manual_hold','resolved'))) then
            raise exception 'Illegal recovery queue transition';
          end if;
          if new.state='claimed' then
            if new.claim_token is null or new.claim_token is not distinct from old.claim_token
               or (old.state='claimed' and old.claim_expires_at>now_at) then
              raise exception 'Fresh expired recovery claim required';
            end if;
          elsif new.claim_token is not null or new.claim_expires_at is not null then
            raise exception 'Closed recovery claim must clear token';
          end if;
        end if;
        new.updated_at := now_at;
        return new;
      end $$''')
    op.execute('''create trigger import_publication_recovery_queue_guard_15
      before insert or update or delete on import_publication_recovery_queue
      for each row execute function import_publication_recovery_queue_guard_15()''')
    op.execute('''create function import_publication_recovery_lease_guard_15()
      returns trigger language plpgsql as $$
      declare now_at timestamptz;
      begin
        if tg_op='DELETE' then
          raise exception 'Recovery lease identity cannot be deleted';
        end if;
        if tg_op='INSERT' then
          if new.version<>1 then raise exception 'First recovery lease version must be one'; end if;
          return new;
        end if;
        if (new.user_id,new.task_id,new.task_lease_version)
           is distinct from (old.user_id,old.task_id,old.task_lease_version) then
          raise exception 'Recovery lease identity is immutable';
        end if;
        now_at := clock_timestamp();
        if new.version=old.version and new.token=old.token then
          if (new.owner,new.queue_claim_token,new.queue_version)
              is distinct from (old.owner,old.queue_claim_token,old.queue_version) then
            raise exception 'Recovery lease renewal scope differs';
          end if;
          if new.expires_at>old.expires_at then
            if old.expires_at<=now_at or new.heartbeat_at<old.heartbeat_at then
              raise exception 'Recovery lease renewal expired';
            end if;
          elsif new.expires_at<=old.expires_at and new.expires_at<=now_at then
            if new.heartbeat_at is distinct from old.heartbeat_at then
              raise exception 'Recovery lease closure changed heartbeat';
            end if;
            new.expires_at:=least(old.expires_at,now_at);
          else
            raise exception 'Recovery lease update is not renewal or closure';
          end if;
        else
          if new.version<>old.version+1 or new.token=old.token
             or old.expires_at>now_at then
            raise exception 'Recovery lease takeover requires expiry and fresh version';
          end if;
        end if;
        return new;
      end $$''')
    op.execute('''create trigger import_publication_recovery_lease_guard_15
      before insert or update or delete on import_publication_recovery_leases
      for each row execute function import_publication_recovery_lease_guard_15()''')
    op.execute('''create function import_publication_recovery_issuance_guard_15()
      returns trigger language plpgsql as $$ begin
        if tg_op<>'INSERT' then
          raise exception 'Recovery token issuance is permanent';
        end if;
        return new;
      end $$''')
    op.execute('''create trigger import_publication_recovery_issuance_guard_15
      before insert or update or delete on import_publication_recovery_token_issuance
      for each row execute function import_publication_recovery_issuance_guard_15()''')
    op.execute('''create function import_publication_recovery_issuance_final_15()
      returns trigger language plpgsql as $$ begin
        if new.kind='queue' then
          if not exists(select 1 from import_publication_recovery_queue q
            where q.user_id=new.user_id and q.task_id=new.task_id
              and q.task_lease_version=new.task_lease_version
              and q.state='claimed' and q.queue_version=new.issued_version
              and q.claim_token=new.token) then
            raise exception 'Queue issuance lacks exact final capture';
          end if;
        elsif not exists(select 1 from import_publication_recovery_leases l
          join import_publication_recovery_queue q
            on q.user_id=l.user_id and q.task_id=l.task_id
            and q.task_lease_version=l.task_lease_version
          where l.user_id=new.user_id and l.task_id=new.task_id
            and l.task_lease_version=new.task_lease_version
            and l.version=new.issued_version and l.token=new.token
            and q.state='claimed' and q.claim_token=l.queue_claim_token
            and q.queue_version=l.queue_version) then
          raise exception 'Recovery issuance lacks exact final grant';
        end if;
        return null;
      end $$''')
    op.execute('''create constraint trigger import_publication_recovery_issuance_final_15
      after insert on import_publication_recovery_token_issuance
      deferrable initially deferred for each row
      execute function import_publication_recovery_issuance_final_15()''')
    op.execute('''create function import_publication_recovery_current_token_15()
      returns trigger language plpgsql as $$ begin
        if new.state='claimed' and not exists(select 1 from
          import_publication_recovery_token_issuance i where i.token=new.claim_token
          and i.kind='queue' and i.user_id=new.user_id and i.task_id=new.task_id
          and i.task_lease_version=new.task_lease_version
          and i.issued_version=new.queue_version) then
          raise exception 'Current queue claim lacks permanent issuance';
        end if;
        return null;
      end $$''')
    op.execute('''create constraint trigger import_publication_recovery_current_token_15
      after insert or update on import_publication_recovery_queue
      deferrable initially deferred for each row
      execute function import_publication_recovery_current_token_15()''')
    op.execute('''create function import_publication_recovery_lease_token_15()
      returns trigger language plpgsql as $$ begin
        if not exists(select 1 from import_publication_recovery_token_issuance i
          where i.token=new.token and i.kind='recovery' and i.user_id=new.user_id
          and i.task_id=new.task_id and i.task_lease_version=new.task_lease_version
          and i.issued_version=new.version) then
          raise exception 'Recovery lease lacks permanent token issuance';
        end if;
        return null;
      end $$''')
    op.execute('''create constraint trigger import_publication_recovery_lease_token_15
      after insert or update on import_publication_recovery_leases
      deferrable initially deferred for each row
      execute function import_publication_recovery_lease_token_15()''')
    op.execute('''create function import_publication_recovery_schedule_guard_15()
      returns trigger language plpgsql as $$ begin
        if tg_op='DELETE' or (tg_op='UPDATE' and new.user_id<>old.user_id) then
          raise exception 'Recovery schedule identity is permanent';
        end if;
        return new;
      end $$''')
    op.execute('''create trigger import_publication_recovery_schedule_guard_15
      before update or delete on import_publication_recovery_schedule
      for each row execute function import_publication_recovery_schedule_guard_15()''')


def _install_dependency_guards():
    op.execute('''create function import_object_dependency_guard_15()
      returns trigger language plpgsql as $$
      declare held boolean;
      begin
        select exists(select 1 from import_publication_evidence e
          join user_publication_gates g using(user_id,task_id,task_lease_version)
          where e.user_id=old.user_id and e.task_id=old.task_id
          and g.status='unresolved') into held;
        if held and (tg_op='DELETE' or
          (new.task_id,new.user_id,new.bucket,new.object_key,new.version_id,
           new.sha256,new.size_bytes) is distinct from
          (old.task_id,old.user_id,old.bucket,old.object_key,old.version_id,
           old.sha256,old.size_bytes)) then
          raise exception 'Pinned import source has unresolved publication evidence';
        end if;
        if tg_op='DELETE' then return old; end if;
        return new;
      end $$''')
    op.execute('''create trigger import_object_dependency_guard_15
      before update or delete on import_objects for each row
      execute function import_object_dependency_guard_15()''')
    op.execute('''create function import_attempt_dependency_guard_15()
      returns trigger language plpgsql as $$
      declare held boolean;allowed boolean;
      begin
        select exists(select 1 from import_publication_evidence e
          join user_publication_gates g using(user_id,task_id,task_lease_version)
          where e.user_id=old.user_id and e.task_id=old.task_id
          and e.task_lease_version=old.lease_version and g.status='unresolved') into held;
        if tg_op='DELETE' then
          if held then raise exception 'Unresolved publication audit cannot be deleted'; end if;
          return old;
        end if;
        if (new.task_id,new.user_id,new.lease_version,new.worker_id,new.lease_token,
            new.user_lease_token,new.user_lease_version,new.started_at) is distinct from
           (old.task_id,old.user_id,old.lease_version,old.worker_id,old.lease_token,
            old.user_lease_token,old.user_lease_version,old.started_at) then
          raise exception 'Import audit identity is immutable';
        end if;
        if (new.ended_at is null) <> (new.end_reason is null) then
          raise exception 'Import audit end fields must agree';
        end if;
        if not held then return new; end if;
        if old.ended_at is not null then
          if (new.ended_at,new.end_reason) is distinct from
             (old.ended_at,old.end_reason) then
            raise exception 'Unresolved publication audit end is immutable';
          end if;
          return new;
        end if;
        if new.ended_at is null then return new; end if;
        if new.end_reason='succeeded' then
          select count(*)=1 into allowed from import_tasks t
            join user_mutation_leases u on u.user_id=t.user_id
            join import_publication_evidence e on e.user_id=t.user_id
              and e.task_id=t.id and e.task_lease_version=t.lease_version
            join import_publication_private_payloads p on p.user_id=e.user_id
              and p.task_id=e.task_id and p.task_lease_version=e.task_lease_version
            join user_publication_gates g on g.user_id=e.user_id
              and g.task_id=e.task_id and g.task_lease_version=e.task_lease_version
            where t.id=old.task_id and t.user_id=old.user_id
              and t.lease_version=old.lease_version
              and t.claimed_by=old.worker_id and t.lease_token=old.lease_token
              and t.user_lease_token=old.user_lease_token
              and t.user_lease_version=old.user_lease_version
              and u.owner=old.worker_id and u.lease_token=old.user_lease_token
              and u.lease_version=old.user_lease_version
              and e.worker_id=old.worker_id and e.task_lease_token=old.lease_token
              and e.user_lease_token=old.user_lease_token
              and e.user_lease_version=old.user_lease_version
              and g.status='unresolved' and t.status='succeeded'
              and t.stage='succeeded' and t.progress=100
              and e.phase='terminal_committed' and p.terminal_slot is not null
              and p.payload_phase_version=e.phase_version;
          if not allowed then raise exception 'Audit success lacks exact terminal publication'; end if;
        elsif new.end_reason='lease_expired' then
          select count(*)=1 into allowed from import_tasks t
            join user_mutation_leases u on u.user_id=t.user_id
            join import_publication_evidence e on e.user_id=t.user_id
              and e.task_id=t.id and e.task_lease_version=t.lease_version
            join import_publication_private_payloads p on p.user_id=e.user_id
              and p.task_id=e.task_id and p.task_lease_version=e.task_lease_version
            join user_publication_gates g on g.user_id=e.user_id
              and g.task_id=e.task_id and g.task_lease_version=e.task_lease_version
            where t.id=old.task_id and t.user_id=old.user_id
              and t.lease_version=old.lease_version
              and t.claimed_by=old.worker_id and t.lease_token=old.lease_token
              and t.user_lease_token=old.user_lease_token
              and t.user_lease_version=old.user_lease_version
              and u.owner=old.worker_id and u.lease_token=old.user_lease_token
              and u.lease_version=old.user_lease_version
              and e.worker_id=old.worker_id and e.task_lease_token=old.lease_token
              and e.user_lease_token=old.user_lease_token
              and e.user_lease_version=old.user_lease_version
              and g.status='unresolved' and t.status='running'
              and e.phase in ('intent','document_verified','pair_sealed')
              and p.terminal_slot is null
              and p.payload_phase_version=e.phase_version
              and t.lease_expires_at<=clock_timestamp()
              and u.lease_expires_at<=clock_timestamp()
              and not exists(select 1 from generation_reservations r
                join vector_generations v on v.generation_id=r.generation_id
                where r.user_id=e.user_id and r.task_id=e.task_id
                and r.task_lease_version=e.task_lease_version
                and v.tenant_id=r.user_id and v.vector_kind=r.vector_kind
                and v.namespace=r.namespace and v.index_key=r.index_key
                and v.owner=r.owner and v.user_lease_token=r.user_lease_token
                and v.user_lease_version=r.user_lease_version
                and v.task_id=r.task_id and v.task_lease_version=r.task_lease_version
                and (v.state in ('published','retired')
                  or v.publication_revision is not null
                  or v.publication_snapshot_version is not null
                  or v.published_at is not null));
          if not allowed then raise exception 'Audit expiry lacks exact expired preterminal tuple'; end if;
          if (new.error_code,new.error_summary,new.last_stage) is distinct from
             (old.error_code,old.error_summary,old.last_stage) then
            raise exception 'Expiry closure changed audit result';
          end if;
        else
          raise exception 'Unresolved publication audit end is not allowed';
        end if;
        return new;
      end $$''')
    op.execute('''create trigger import_attempt_dependency_guard_15
      before update or delete on import_task_attempts for each row
      execute function import_attempt_dependency_guard_15()''')
