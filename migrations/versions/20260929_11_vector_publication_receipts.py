"""Retain immutable transaction-bound vector publication receipts."""
from alembic import op

revision = '20260929_11'
down_revision = '20260929_10'
branch_labels = None
depends_on = None


def upgrade():
    # The earlier schema did not retain historical snapshot versions. Guessing
    # receipts for already retired rows would silently misreport old commits.
    op.execute('''do $$ begin
        if exists(select 1 from vector_generations
                  where state in ('published','retired')) then
          raise exception 'existing vector publications require an explicit receipt migration';
        end if;
    end $$''')
    op.execute('''alter table vector_generations
        add column publication_revision bigint,
        add column publication_snapshot_version bigint,
        add constraint vector_publication_receipt_state check (
          (state in ('staging','sealed','abandoned')
            and publication_revision is null
            and publication_snapshot_version is null
            and published_at is null)
          or (state in ('published','retired')
            and publication_revision=coalesce(base_revision,0)+1
            and published_at is not null
            and (publication_snapshot_version is null
              or publication_snapshot_version>=0)))''')
    op.execute('''create or replace function vector_generation_guard()
      returns trigger language plpgsql as $$
      begin
        if tg_op='DELETE' then
          raise exception 'vector generation history is retained';
        end if;
        if tg_op='INSERT' then
          if new.state in ('published','retired') then
            raise exception 'vector publication requires a sealed transition';
          end if;
          return new;
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
        if old.state in ('published','retired') and
           (old.publication_revision,old.publication_snapshot_version,old.published_at)
           is distinct from
           (new.publication_revision,new.publication_snapshot_version,new.published_at) then
          raise exception 'vector publication receipt is immutable';
        end if;
        if not ((old.state='staging' and new.state in ('sealed','abandoned'))
             or (old.state='sealed' and new.state in ('published','abandoned'))
             or (old.state='published' and new.state='retired')) then
          raise exception 'illegal vector generation transition';
        end if;
        return new;
      end $$''')
    op.execute('''drop trigger vector_generation_guard on vector_generations''')
    op.execute('''create trigger vector_generation_guard before insert or update or delete
        on vector_generations for each row execute function vector_generation_guard()''')


def downgrade():
    raise RuntimeError('Historical vector publication receipts require verified compatible recovery')
