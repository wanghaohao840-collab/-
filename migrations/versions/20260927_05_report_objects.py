"""Pinned report object references.

Revision ID: 20260927_05
Revises: 20260927_04
"""
from alembic import op

revision = '20260927_05'
down_revision = '20260927_04'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('create unique index ix_report_owner on report_records(id,user_id)')
    op.execute('''create table report_objects (
        report_id text primary key,
        user_id text not null,
        bucket text not null check (length(bucket) > 0),
        object_key text not null,
        version_id text not null check (length(version_id) > 0 and version_id <> 'null'),
        sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
        size_bytes bigint not null check (size_bytes >= 0),
        foreign key(report_id,user_id) references report_records(id,user_id) on delete cascade
    )''')


def downgrade() -> None:
    raise RuntimeError('Report references require a verified compatible data recovery procedure')
