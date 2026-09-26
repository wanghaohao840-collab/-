"""Initial PostgreSQL copy of the existing relational business schema.

Revision ID: 20260926_01
Revises:
"""

from __future__ import annotations

import re
from pathlib import Path

from alembic import context, op
from sqlalchemy import text


revision = "20260926_01"
down_revision = None
branch_labels = None
depends_on = None

_SNAPSHOT = Path(__file__).with_suffix(".sql")


def _statements() -> list[str]:
    return [statement.strip() for statement in _SNAPSHOT.read_text(encoding="utf-8").split(";") if statement.strip()]


def upgrade() -> None:
    statements = _statements()
    if not context.is_offline_mode():
        connection = op.get_bind()
        existing = set(connection.execute(text("""
            select tablename from pg_catalog.pg_tables
            where schemaname = current_schema()
        """)).scalars())
        expected = {
            match.group(1)
            for statement in statements
            if (match := re.match(r"create table if not exists (\w+)", statement, re.IGNORECASE))
        }
        collisions = sorted(existing & expected)
        if collisions:
            raise RuntimeError(
                "Business tables already exist in the target schema: "
                + ", ".join(collisions)
                + ". Verify and migrate the existing data explicitly; do not stamp this revision."
            )
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade would drop business data. Restore the paired database backup "
        "with the matching application image instead."
    )
