"""Versioned per-user History and Memory authority.

Revision ID: 20260927_03
Revises: 20260927_02
"""
from alembic import op

revision = '20260927_03'
down_revision = '20260927_02'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('''create table user_snapshots (
        user_id text not null references users(id) on delete cascade,
        kind text not null check (kind in ('history', 'memory')),
        version bigint not null check (version > 0),
        payload jsonb not null check (jsonb_typeof(payload) = 'object'),
        updated_at timestamptz not null,
        primary key (user_id, kind)
    )''')


def downgrade() -> None:
    raise RuntimeError('Snapshot downgrade requires a verified compatible data recovery procedure')
