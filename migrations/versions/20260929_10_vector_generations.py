"""Durable, scoped vector generation authority (no runtime wiring)."""
from alembic import op

revision = '20260929_10'
down_revision = '20260928_09'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''create table vector_indexes (
        tenant_id text not null references users(id),
        vector_kind text not null check(vector_kind in ('rag','episode')),
        namespace text not null check(length(namespace)>0),
        index_key text not null check(index_key ~ '^[a-f0-9]{64}$'),
        identity jsonb not null check(jsonb_typeof(identity)='object'),
        index_revision bigint not null default 1 check(index_revision>0),
        primary key(tenant_id,vector_kind,namespace,index_key),
        unique(tenant_id,vector_kind,namespace,index_key,index_revision)
    )''')
    op.execute('''create table vector_generations (
        generation_id uuid primary key,
        tenant_id text not null,
        vector_kind text not null,
        namespace text not null,
        index_key text not null,
        base_revision bigint,
        index_revision bigint not null check(index_revision>0),
        owner text not null,
        user_lease_token uuid not null,
        user_lease_version bigint not null check(user_lease_version>0),
        task_id text,
        task_lease_token uuid,
        task_lease_version bigint,
        state text not null check(state in ('staging','sealed','published','retired','abandoned')),
        expected_count bigint,
        content_digest text,
        created_at timestamptz not null default clock_timestamp(),
        sealed_at timestamptz,
        published_at timestamptz,
        abandoned_at timestamptz,
        foreign key(tenant_id,vector_kind,namespace,index_key)
          references vector_indexes(tenant_id,vector_kind,namespace,index_key),
        foreign key(tenant_id,vector_kind,namespace,index_key,index_revision)
          references vector_indexes(tenant_id,vector_kind,namespace,index_key,index_revision),
        foreign key(task_id,tenant_id) references import_tasks(id,user_id),
        unique(generation_id,tenant_id,vector_kind,namespace,index_key),
        check(base_revision is null or base_revision>0),
        check((task_id is null)=(task_lease_token is null)),
        check((task_id is null)=(task_lease_version is null)),
        check(task_lease_version is null or task_lease_version>0),
        check((expected_count is null and content_digest is null) or
              (expected_count>=0 and content_digest ~ '^[a-f0-9]{64}$')),
        check((state='staging' and sealed_at is null and expected_count is null)
           or (state='abandoned')
           or (state in ('sealed','published','retired') and sealed_at is not null and expected_count is not null))
    )''')
    op.execute('''create table vector_heads (
        tenant_id text not null,
        vector_kind text not null,
        namespace text not null,
        index_key text not null,
        revision bigint not null check(revision>0),
        generation_id uuid,
        last_generation_id uuid not null,
        index_revision bigint not null check(index_revision>0),
        snapshot_version bigint,
        updated_at timestamptz not null default clock_timestamp(),
        primary key(tenant_id,vector_kind,namespace,index_key),
        foreign key(tenant_id,vector_kind,namespace,index_key)
          references vector_indexes(tenant_id,vector_kind,namespace,index_key),
        foreign key(tenant_id,vector_kind,namespace,index_key,index_revision)
          references vector_indexes(tenant_id,vector_kind,namespace,index_key,index_revision),
        foreign key(generation_id,tenant_id,vector_kind,namespace,index_key)
          references vector_generations(generation_id,tenant_id,vector_kind,namespace,index_key),
        foreign key(last_generation_id,tenant_id,vector_kind,namespace,index_key)
          references vector_generations(generation_id,tenant_id,vector_kind,namespace,index_key),
        check(snapshot_version is null or snapshot_version>=0)
    )''')
    op.execute('''create unique index vector_one_published_generation
        on vector_generations(tenant_id,vector_kind,namespace,index_key)
        where state='published' ''')
    op.execute('''create function vector_index_guard() returns trigger language plpgsql as $$
      begin
        raise exception 'registered vector identity is immutable';
      end $$''')
    op.execute('''create trigger vector_index_guard before update or delete
        on vector_indexes for each row execute function vector_index_guard()''')
    op.execute('''create function vector_generation_guard() returns trigger language plpgsql as $$
      begin
        if tg_op='DELETE' then
          raise exception 'vector generation history is retained';
        end if;
        if (old.generation_id,old.tenant_id,old.vector_kind,old.namespace,old.index_key,
            old.base_revision,old.index_revision,old.owner,old.user_lease_token,
            old.user_lease_version,old.task_id,old.task_lease_token,old.task_lease_version,
            old.created_at) is distinct from
           (new.generation_id,new.tenant_id,new.vector_kind,new.namespace,new.index_key,
            new.base_revision,new.index_revision,new.owner,new.user_lease_token,
            new.user_lease_version,new.task_id,new.task_lease_token,new.task_lease_version,
            new.created_at) then
          raise exception 'vector generation identity is immutable';
        end if;
        if old.state<>'staging' and
           (old.expected_count,old.content_digest,old.sealed_at) is distinct from
           (new.expected_count,new.content_digest,new.sealed_at) then
          raise exception 'sealed vector manifest is immutable';
        end if;
        if not ((old.state='staging' and new.state in ('sealed','abandoned'))
             or (old.state='sealed' and new.state in ('published','abandoned'))
             or (old.state='published' and new.state='retired')) then
          raise exception 'illegal vector generation transition';
        end if;
        return new;
      end $$''')
    op.execute('''create trigger vector_generation_guard before update or delete
        on vector_generations for each row execute function vector_generation_guard()''')
    op.execute('''create function vector_head_guard() returns trigger language plpgsql as $$
      begin
        if tg_op='DELETE' then
          raise exception 'vector head history cannot be deleted';
        end if;
        if (old.tenant_id,old.vector_kind,old.namespace,old.index_key) is distinct from
           (new.tenant_id,new.vector_kind,new.namespace,new.index_key)
           or new.revision<>old.revision+1 then
          raise exception 'vector head revision must advance by one within its scope';
        end if;
        return new;
      end $$''')
    op.execute('''create trigger vector_head_guard before update or delete
        on vector_heads for each row execute function vector_head_guard()''')
    op.execute('''create function vector_head_published_guard() returns trigger language plpgsql as $$
      begin
        if not exists (
          select 1 from vector_generations g where g.generation_id=new.last_generation_id
          and g.tenant_id=new.tenant_id and g.vector_kind=new.vector_kind
          and g.namespace=new.namespace and g.index_key=new.index_key
          and g.state='published'
          and ((g.expected_count=0 and new.generation_id is null)
            or (g.expected_count>0 and new.generation_id=g.generation_id))) then
          raise exception 'vector head must reference a published generation';
        end if;
        return null;
      end $$''')
    op.execute('''create constraint trigger vector_head_published_guard
        after insert or update on vector_heads deferrable initially deferred
        for each row execute function vector_head_published_guard()''')


def downgrade():
    raise RuntimeError('Vector generation history requires verified compatible recovery')
