"""Immutable, tenant-scoped references to retained document object versions."""
from alembic import op

revision = '20260929_12'
down_revision = '20260929_11'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''create table document_objects (
        user_id text not null references users(id),
        document_id text not null,
        bucket text not null check(length(bucket)>0),
        object_key text not null,
        version_id text not null check(length(version_id)>0 and version_id<>'null'),
        sha256 text not null check(sha256 ~ '^[0-9a-f]{64}$'),
        size_bytes bigint not null check(size_bytes>=0),
        history_record_sha256 text not null check(history_record_sha256 ~ '^[0-9a-f]{64}$'),
        primary key(user_id,document_id)
    )''')
    op.execute('''create function document_object_immutable() returns trigger language plpgsql as $$
        begin raise exception 'published document object reference is immutable'; end $$''')
    op.execute('''create trigger document_object_immutable before update or delete
        on document_objects for each row execute function document_object_immutable()''')


def downgrade():
    raise RuntimeError('Document object references require verified compatible recovery')
