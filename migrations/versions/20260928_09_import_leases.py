"""Native import attempt ownership and retained audit, without Worker wiring."""
from alembic import op

revision = '20260928_09'
down_revision = '20260928_08'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''alter table import_tasks
        add column claimed_by text,
        add column lease_token uuid,
        add column lease_version bigint not null default 0 check(lease_version >= 0),
        add column heartbeat_at timestamptz,
        add column lease_expires_at timestamptz,
        add column user_lease_token uuid,
        add column user_lease_version bigint check(user_lease_version > 0)''')
    op.execute('''create table import_task_attempts (
        task_id text not null, user_id text not null,
        lease_version bigint not null check(lease_version > 0),
        worker_id text not null check(length(trim(worker_id)) > 0),
        lease_token uuid not null,
        user_lease_token uuid not null,
        user_lease_version bigint not null check(user_lease_version > 0),
        started_at timestamptz not null, heartbeat_at timestamptz not null,
        ended_at timestamptz, end_reason text, error_code text,
        error_summary text check(length(error_summary) <= 500), last_stage text not null,
        primary key(task_id,lease_version),
        foreign key(task_id,user_id) references import_tasks(id,user_id) on delete cascade,
        check((ended_at is null) = (end_reason is null))
    )''')
    op.execute('''create table import_user_schedule (
        user_id text primary key references users(id) on delete cascade,
        last_claimed_at timestamptz not null
    )''')


def downgrade():
    raise RuntimeError('Import attempt history requires a verified compatible data recovery procedure')
