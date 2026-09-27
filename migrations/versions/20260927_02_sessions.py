"""Shared authentication sessions.

Revision ID: 20260927_02
Revises: 20260926_01
"""

from alembic import op
from sqlalchemy import text

revision = "20260927_02"
down_revision = "20260926_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        create table auth_sessions (
            token_hash text primary key,
            user_id text not null references users(id) on delete cascade,
            csrf_token text not null,
            created_at timestamptz not null,
            last_accessed_at timestamptz not null,
            expires_at timestamptz not null
        )
    """)
    op.execute("create index ix_auth_sessions_expires_at on auth_sessions(expires_at)")


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(text("select exists(select 1 from auth_sessions)")).scalar():
        raise RuntimeError("Downgrade would drop live session data")
    op.execute("drop table auth_sessions")
