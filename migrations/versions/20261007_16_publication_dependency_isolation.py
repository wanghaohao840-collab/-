"""Reject dependency writes from stale transaction snapshots."""

from alembic import op


revision = '20261007_16'
down_revision = '20261002_15'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''create function publication_dependency_isolation_guard_16()
      returns trigger language plpgsql as $$
      begin
        if current_setting('transaction_isolation') <> 'read committed' then
          raise exception 'Publication dependency mutation requires READ COMMITTED';
        end if;
        if tg_op = 'DELETE' then return old; end if;
        return new;
      end $$''')
    op.execute('''create trigger aa_import_object_isolation_guard_16
      before update or delete on import_objects for each row
      execute function publication_dependency_isolation_guard_16()''')
    op.execute('''create trigger aa_import_attempt_isolation_guard_16
      before update or delete on import_task_attempts for each row
      execute function publication_dependency_isolation_guard_16()''')


def downgrade():
    raise RuntimeError('Publication source isolation guard cannot be removed while recovery is supported')
