"""Immutable witnesses pairing a RAG publication with complete History documents."""
from alembic import op

revision = '20260930_13'
down_revision = '20260929_12'
branch_labels = None
depends_on = None


def upgrade():
    # Deliberately empty on upgrade: old publications need explicit proof before
    # an inline bootstrap can create their first witness.
    op.execute('''create table history_document_witnesses (
        tenant_id text not null,
        vector_kind text not null default 'rag' check(vector_kind='rag'),
        namespace text not null,
        index_key text not null,
        head_revision bigint not null check(head_revision>0),
        last_generation_id uuid not null,
        index_revision bigint not null check(index_revision>0),
        publication_snapshot_version bigint not null check(publication_snapshot_version>0),
        document_count bigint not null check(document_count>=0),
        documents_sha256 text not null check(documents_sha256 ~ '^[0-9a-f]{64}$'),
        created_at timestamptz not null default clock_timestamp(),
        primary key(tenant_id,vector_kind,namespace,index_key,head_revision),
        foreign key(last_generation_id,tenant_id,vector_kind,namespace,index_key)
          references vector_generations(generation_id,tenant_id,vector_kind,namespace,index_key)
    )''')
    op.execute('''create function history_document_witness_guard()
      returns trigger language plpgsql as $$
      begin
        if tg_op <> 'INSERT' then
          raise exception 'History document witness is immutable';
        end if;
        if not exists (
          select 1 from vector_generations g
          where g.generation_id=new.last_generation_id
            and g.tenant_id=new.tenant_id and g.vector_kind=new.vector_kind
            and g.namespace=new.namespace and g.index_key=new.index_key
            and g.state in ('published','retired')
            and g.publication_revision=new.head_revision
            and g.index_revision=new.index_revision
            and g.publication_snapshot_version is not null
            and g.publication_snapshot_version=new.publication_snapshot_version
        ) then
          raise exception 'History document witness has no matching publication receipt';
        end if;
        return new;
      end $$''')
    op.execute('''create trigger history_document_witness_guard
      before insert or update or delete on history_document_witnesses
      for each row execute function history_document_witness_guard()''')


def downgrade():
    raise RuntimeError('History document witnesses require verified compatible recovery')
