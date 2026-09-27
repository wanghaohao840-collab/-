"""Derived Notes lexical positions and explicit tag order.

Revision ID: 20260927_04
Revises: 20260927_03

Existing tags require a source-rowid order rebuild; NULL is intentionally not
an invented order. Existing notes need a token rebuild before distributed
bootstrap may open. Source migration and bootstrap are outside this revision.
"""
from alembic import op

revision = '20260927_04'
down_revision = '20260927_03'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('alter table note_tags add column position integer check (position >= 0)')
    op.execute('create unique index ix_note_tags_position on note_tags(user_id,note_id,position)')
    op.execute('''create table note_search_tokens (
        user_id text not null,
        note_id text not null,
        field text not null check (field in ('body_markdown','concept','tags_text')),
        position integer not null check (position >= 0),
        token text collate "C" not null,
        primary key (user_id,note_id,field,position),
        foreign key (note_id,user_id) references notes(id,user_id) on delete cascade
    )''')
    # Valid note tokens can exceed PostgreSQL's B-tree index tuple size limit.
    # The primary key supports our user/note-correlated positional lookups;
    # keep unbounded token text out of indexes without truncating search terms.


def downgrade() -> None:
    raise RuntimeError('Notes downgrade requires a verified compatible data recovery procedure')
