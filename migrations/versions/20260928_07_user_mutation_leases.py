"""Persistent user mutation lease generations.

Revision ID: 20260928_07
Revises: 20260927_06
"""
from alembic import op

revision = '20260928_07'
down_revision = '20260927_06'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('''create table user_mutation_leases (
        user_id text primary key references users(id) on delete cascade,
        owner text not null check (length(btrim(owner)) > 0),
        lease_token uuid not null,
        lease_version bigint not null check (lease_version > 0),
        heartbeat_at timestamptz not null,
        lease_expires_at timestamptz not null
    )''')


def downgrade() -> None:
    raise RuntimeError('User mutation leases require a verified compatible data recovery procedure')
