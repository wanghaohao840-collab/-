"""Tenant-scoped episodic document rows.

Revision ID: 20260927_06
Revises: 20260927_05
"""
from alembic import op

revision = '20260927_06'
down_revision = '20260927_05'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('''create table memory_documents (
        user_id text not null references users(id) on delete cascade,
        document_id text not null,
        content text not null,
        metadata text not null,
        created_at text,
        primary key(user_id,document_id)
    )''')


def downgrade() -> None:
    raise RuntimeError('Memory documents require a verified compatible data recovery procedure')
