"""Permanent candidate identities and private, bounded import publication proof."""

from alembic import op

revision = '20261002_14'
down_revision = '20260930_13'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''create table import_publication_evidence (
        user_id text not null references users(id),
        task_id text not null,
        task_lease_version bigint not null check(task_lease_version > 0),
        document_id text not null,
        worker_id text not null,
        task_lease_token uuid not null,
        user_lease_token uuid not null,
        user_lease_version bigint not null check(user_lease_version > 0),
        schema_version integer not null check(schema_version = 1),
        intent_format text not null check(intent_format = 'canonical-json-zlib-1'),
        intent_payload bytea not null check(octet_length(intent_payload) <= 8388608),
        canonical_bytes bigint not null check(canonical_bytes between 1 and 67108864),
        intent_hash text not null check(intent_hash ~ '^[0-9a-f]{64}$'),
        phase text not null default 'intent' check(phase in
            ('intent','document_verified','pair_sealed','terminal_committed',
             'proved_succeeded','abandoned')),
        phase_version bigint not null default 1 check(phase_version > 0),
        observation_reason text check(observation_reason in ('unknown','manual_hold')),
        document_slot bytea check(octet_length(document_slot) <= 262144),
        rag_sealed_slot bytea check(octet_length(rag_sealed_slot) <= 262144),
        episode_sealed_slot bytea check(octet_length(episode_sealed_slot) <= 262144),
        terminal_slot bytea check(octet_length(terminal_slot) <= 262144),
        created_at timestamptz not null default clock_timestamp(),
        updated_at timestamptz not null default clock_timestamp(),
        primary key(user_id,task_id,task_lease_version),
        foreign key(task_id,user_id) references import_tasks(id,user_id),
        check(octet_length(intent_payload)
            + coalesce(octet_length(document_slot),0)
            + coalesce(octet_length(rag_sealed_slot),0)
            + coalesce(octet_length(episode_sealed_slot),0)
            + coalesce(octet_length(terminal_slot),0) <= 9437184)
    )''')
    op.execute('''create function import_publication_evidence_guard()
      returns trigger language plpgsql as $$
      begin
        if tg_op = 'DELETE' then
          raise exception 'Import publication proof cannot be deleted';
        end if;
        if tg_op = 'INSERT' and (new.phase <> 'intent' or new.phase_version <> 1
           or new.observation_reason is not null or new.document_slot is not null
           or new.rag_sealed_slot is not null or new.episode_sealed_slot is not null
           or new.terminal_slot is not null) then
          raise exception 'Import publication intent must start empty';
        end if;
        if tg_op = 'UPDATE' then
          if (new.user_id,new.task_id,new.task_lease_version,new.document_id,
              new.worker_id,new.task_lease_token,new.user_lease_token,
              new.user_lease_version,new.schema_version,new.intent_format,
              new.intent_payload,new.canonical_bytes,new.intent_hash,new.created_at)
             is distinct from
             (old.user_id,old.task_id,old.task_lease_version,old.document_id,
              old.worker_id,old.task_lease_token,old.user_lease_token,
              old.user_lease_version,old.schema_version,old.intent_format,
              old.intent_payload,old.canonical_bytes,old.intent_hash,old.created_at) then
            raise exception 'Import publication intent is immutable';
          end if;
          if new.phase_version <> old.phase_version + 1 then
            raise exception 'Import publication phase CAS required';
          end if;
          if (old.document_slot is not null and new.document_slot is distinct from old.document_slot)
             or (old.rag_sealed_slot is not null and new.rag_sealed_slot is distinct from old.rag_sealed_slot)
             or (old.episode_sealed_slot is not null and new.episode_sealed_slot is distinct from old.episode_sealed_slot)
             or (old.terminal_slot is not null and new.terminal_slot is distinct from old.terminal_slot) then
            raise exception 'Import publication slots are write once';
          end if;
          if new.phase <> old.phase and not (
             (old.phase='intent' and new.phase in ('document_verified','abandoned'))
             or (old.phase='document_verified' and new.phase in ('pair_sealed','abandoned'))
             or (old.phase='pair_sealed' and new.phase in ('terminal_committed','abandoned'))
             or (old.phase='terminal_committed' and new.phase='proved_succeeded')) then
            raise exception 'Illegal import publication phase transition';
          end if;
          if old.phase in ('proved_succeeded','abandoned') then
            raise exception 'Terminal import publication proof is immutable';
          end if;
          if (new.phase in ('document_verified','pair_sealed','terminal_committed','proved_succeeded')
              and new.document_slot is null)
             or (new.phase in ('pair_sealed','terminal_committed','proved_succeeded')
                 and (new.rag_sealed_slot is null or new.episode_sealed_slot is null))
             or (new.phase in ('terminal_committed','proved_succeeded') and new.terminal_slot is null) then
            raise exception 'Import publication phase lacks proof slots';
          end if;
          if (new.phase='intent' and (new.document_slot is not null
               or new.rag_sealed_slot is not null or new.episode_sealed_slot is not null
               or new.terminal_slot is not null))
             or (new.phase='document_verified' and new.terminal_slot is not null)
             or (new.phase='document_verified' and new.episode_sealed_slot is not null)
             or (new.phase='pair_sealed' and new.terminal_slot is not null) then
            raise exception 'Import publication proof slot is premature';
          end if;
          if new.phase='abandoned' and (new.document_slot,new.rag_sealed_slot,
              new.episode_sealed_slot,new.terminal_slot) is distinct from
              (old.document_slot,old.rag_sealed_slot,old.episode_sealed_slot,
               old.terminal_slot) then
            raise exception 'Abandon cannot append publication proof';
          end if;
          new.updated_at := clock_timestamp();
        end if;
        return new;
      end $$''')
    op.execute('''create trigger import_publication_evidence_guard before insert or update or delete
      on import_publication_evidence for each row
      execute function import_publication_evidence_guard()''')
    op.execute('''create table user_publication_gates (
        user_id text not null references users(id),
        task_id text not null,
        task_lease_version bigint not null check(task_lease_version > 0),
        status text not null default 'unresolved' check(status in ('unresolved','resolved')),
        created_at timestamptz not null default clock_timestamp(),
        resolved_at timestamptz,
        primary key(user_id,task_id,task_lease_version),
        foreign key(user_id,task_id,task_lease_version)
          references import_publication_evidence(user_id,task_id,task_lease_version),
        check((status='unresolved' and resolved_at is null)
           or (status='resolved' and resolved_at is not null))
    )''')
    op.execute("create unique index one_unresolved_publication_gate on user_publication_gates(user_id) where status='unresolved'")
    op.execute('''create function user_publication_gate_guard()
      returns trigger language plpgsql as $$
      begin
        if tg_op='DELETE' then raise exception 'Publication gate cannot be deleted'; end if;
        if tg_op='INSERT' and (new.status <> 'unresolved' or new.resolved_at is not null) then
          raise exception 'Publication gate must start unresolved';
        end if;
        if tg_op='UPDATE' and (
             (new.user_id,new.task_id,new.task_lease_version,new.created_at)
              is distinct from (old.user_id,old.task_id,old.task_lease_version,old.created_at)
             or old.status <> 'unresolved' or new.status <> 'resolved'
             or new.resolved_at is null) then
          raise exception 'Publication gate may only resolve once';
        end if;
        return new;
      end $$''')
    op.execute('''create trigger user_publication_gate_guard before insert or update or delete
      on user_publication_gates for each row execute function user_publication_gate_guard()''')
    op.execute('''create table generation_reservations (
        generation_id uuid primary key,
        user_id text not null,
        task_id text not null,
        task_lease_version bigint not null,
        vector_kind text not null check(vector_kind in ('rag','episode')),
        namespace text not null,
        index_key text not null check(index_key ~ '^[0-9a-f]{64}$'),
        base_revision bigint check(base_revision > 0),
        owner text not null,
        user_lease_token uuid not null,
        user_lease_version bigint not null check(user_lease_version > 0),
        state text not null default 'reserved' check(state in ('reserved','revoked')),
        created_at timestamptz not null default clock_timestamp(),
        revoked_at timestamptz,
        foreign key(user_id,task_id,task_lease_version)
          references import_publication_evidence(user_id,task_id,task_lease_version),
        unique(user_id,task_id,task_lease_version,vector_kind),
        check((state='reserved' and revoked_at is null)
           or (state='revoked' and revoked_at is not null))
    )''')
    op.execute('''create function generation_reservation_guard()
      returns trigger language plpgsql as $$
      begin
        if tg_op='DELETE' then raise exception 'Generation reservation is permanent'; end if;
        if tg_op='INSERT' and (new.state <> 'reserved' or new.revoked_at is not null) then
          raise exception 'Generation reservation must start reserved';
        end if;
        if tg_op='UPDATE' and (
             (new.generation_id,new.user_id,new.task_id,new.task_lease_version,
              new.vector_kind,new.namespace,new.index_key,new.base_revision,
              new.owner,new.user_lease_token,new.user_lease_version,new.created_at)
              is distinct from
             (old.generation_id,old.user_id,old.task_id,old.task_lease_version,
              old.vector_kind,old.namespace,old.index_key,old.base_revision,
              old.owner,old.user_lease_token,old.user_lease_version,old.created_at)
             or old.state <> 'reserved' or new.state <> 'revoked'
             or new.revoked_at is null) then
          raise exception 'Generation reservation may only revoke once';
        end if;
        return new;
      end $$''')
    op.execute('''create trigger generation_reservation_guard before insert or update or delete
      on generation_reservations for each row execute function generation_reservation_guard()''')


def downgrade():
    raise RuntimeError('Import publication evidence requires paired database backup and verified recovery')
