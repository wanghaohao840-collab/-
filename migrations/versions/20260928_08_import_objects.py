"""Pinned source objects for staged imports.

Revision ID: 20260928_08
Revises: 20260928_07
"""
from alembic import op

revision = "20260928_08"
down_revision = "20260928_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("create unique index ix_import_task_owner on import_tasks(id,user_id)")
    op.execute("""create table import_objects (
        task_id text primary key,
        user_id text not null,
        bucket text not null check (length(bucket) > 0),
        object_key text not null,
        version_id text not null check (length(version_id) > 0 and version_id <> 'null'),
        sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
        size_bytes bigint not null check (size_bytes >= 0),
        foreign key(task_id,user_id) references import_tasks(id,user_id) on delete cascade
    )""")


def downgrade() -> None:
    raise RuntimeError("Import references require a verified compatible data recovery procedure")
