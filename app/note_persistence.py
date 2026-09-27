"""Transaction-bound Notes SQL operations.

Fixed statements use explicit dialect bind markers. Business validation, version
transitions, source guards and DTO assembly remain in note_repository.
PostgreSQL search authority is positional rows; SQLite :memory: is used only
as a disposable unicode61 lexical tokenizer, never a durable index or store.
"""
from contextlib import closing, contextmanager

from app.database import connect, transaction
from app.note_models import NoteNotFoundError
from app.note_tokenizer import tokenize_fields, query_groups


class NoteStore:
    def __init__(self, cursor, *, postgres=False):
        self.cursor = cursor
        self.postgres = postgres
        self.bind = '%s' if postgres else '?'

    def delete_tags(self, values):
        b = self.bind
        return self.cursor.execute(f'delete from note_tags where user_id={b} and note_id={b}', values)

    def insert_tags(self, values):
        b = self.bind
        if self.postgres:
            return self.cursor.executemany('''insert into note_tags
                (user_id,note_id,normalized_tag,display_tag,position)
                values (%s,%s,%s,%s,%s)''',
                [(*row, position) for position, row in enumerate(values)])
        return self.cursor.executemany(f'insert into note_tags (user_id,note_id,normalized_tag,display_tag) values ({b},{b},{b},{b})', values)

    def insert_source(self, values):
        b = self.bind
        return self.cursor.execute(f"""
            insert into note_sources (
                id,user_id,note_id,source_kind,qa_thread_id,qa_message_id,
                citation_id,document_id,locator_json,title_snapshot,
                excerpt_snapshot,created_at
            ) values ({b},{b},{b},{b},{b},{b},{b},{b},{b},{b},{b},{b})
            """, values)

    def delete_search(self, values):
        b = self.bind
        if self.postgres:
            return self.cursor.execute('delete from note_search_tokens where note_id=%s and user_id=%s', values)
        return self.cursor.execute(f'delete from notes_fts where note_id={b} and user_id={b}', values)

    def insert_search(self, values):
        b = self.bind
        if self.postgres:
            note_id, user_id, body, concept, tags = values
            tokens = tokenize_fields(body, concept, tags)
            self.cursor.executemany('''insert into note_search_tokens
                (user_id,note_id,field,position,token) values (%s,%s,%s,%s,%s)''',
                [(user_id, note_id, field, position, token) for field, position, token in tokens])
            return
        return self.cursor.execute(f'insert into notes_fts (note_id,user_id,body_markdown,concept,tags_text) values ({b},{b},{b},{b},{b})', values)

    def enqueue(self, values):
        b = self.bind
        if self.postgres:
            return self.cursor.execute('''insert into note_projection_tasks
                (id,user_id,note_id,note_version,operation,status,attempt_count,available_at,created_at)
                values (%s,%s,%s,%s,%s,'queued',0,%s,%s)
                on conflict (user_id,note_id,note_version,operation) do nothing''', values)
        return self.cursor.execute(f"""
            insert or ignore into note_projection_tasks (
                id,user_id,note_id,note_version,operation,status,attempt_count,
                available_at,created_at
            ) values ({b},{b},{b},{b},{b},'queued',0,{b},{b})
            """, values)

    def scrub_document_sources(self, values):
        b = self.bind
        return self.cursor.execute(f"""update note_document_sources set document_id=null,chunk_id=null,chunk_index=null,
               content_sha256=null,locator_json=null,title_snapshot=null,excerpt_snapshot=null,
               source_deleted_at={b} where user_id={b} and document_id={b} and source_deleted_at is null""", values)

    def scrub_qa_sources(self, values, field):
        b = self.bind
        if field not in ('document_id', 'qa_thread_id'):
            raise ValueError('invalid source scope')
        return self.cursor.execute(f"""
            update note_sources set qa_thread_id=null,qa_message_id=null,citation_id=null,
                document_id=null,locator_json=null,title_snapshot=null,excerpt_snapshot=null,
                source_deleted_at={b}
            where user_id={b} and note_id={b} and {field}={b} and source_deleted_at is null
            """, values)

    def bump_version(self, values):
        b = self.bind
        return self.cursor.execute(f"update notes set version={b},updated_at={b},projection_state='pending' where id={b} and user_id={b}", values)

    def insert_note(self, values):
        b = self.bind
        return self.cursor.execute(f"""
                insert into notes (
                    id,user_id,body_markdown,concept,version,projection_state,
                    client_request_id,request_digest,created_at,updated_at
                ) values ({b},{b},{b},{b},1,'pending',{b},{b},{b},{b})
                """, values)

    def update_note(self, values):
        b = self.bind
        return self.cursor.execute(f"""
                update notes set body_markdown={b}, concept={b}, version={b},
                    projection_state='pending', updated_at={b}
                where id={b} and user_id={b} and deleted_at is null and version={b}
                """, values)

    def soft_delete(self, values):
        b = self.bind
        return self.cursor.execute(f"""
                update notes set deleted_at={b}, updated_at={b}, version={b}, projection_state='pending'
                where id={b} and user_id={b} and deleted_at is null and version={b}
                """, values)

    def insert_document_source(self, values):
        b = self.bind
        return self.cursor.execute(f"""insert into note_document_sources (
                    id,user_id,note_id,document_id,chunk_id,chunk_index,content_sha256,
                    locator_json,title_snapshot,excerpt_snapshot,created_at
                ) values ({b},{b},{b},{b},{b},{b},{b},{b},{b},{b},{b})""", values)

    def scrub_candidates(self, values, field):
        b = self.bind
        if field not in ('document_id', 'qa_thread_id'):
            raise ValueError('invalid source scope')
        return self.cursor.execute(f"""
        select distinct note_id from note_sources
        where user_id={b} and {field}={b} and source_deleted_at is null
        """, values)

    def request(self, values):
        b = self.bind
        return self.cursor.execute(f'select id, request_digest from notes where user_id={b} and client_request_id={b}', values)

    def mark_deleted(self, values):
        b = self.bind
        return self.cursor.execute(f"update notes set deleted_at={b},updated_at={b},version={b},projection_state='pending' where id={b} and user_id={b}", values)

    def retry_projections(self, values):
        b = self.bind
        return self.cursor.executemany(f"update note_projection_tasks set status='queued', attempt_count=0, available_at={b}, lease_owner=null, lease_expires_at=null, last_error_code=null, finished_at=null where id={b}", values)

    def mark_pending(self, values):
        b = self.bind
        return self.cursor.executemany(f"update notes set projection_state='pending' where id={b} and user_id={b}", values)

    def note(self, values, condition):
        b = self.bind
        if condition not in ('', 'and deleted_at is null'):
            raise ValueError('invalid note condition')
        return self.cursor.execute(f'select * from notes where id={b} and user_id={b} {condition}', values)

    def tags(self, values):
        b = self.bind
        order = 'position,normalized_tag' if self.postgres else 'rowid'
        return self.cursor.execute(f'select display_tag from note_tags where user_id={b} and note_id={b} order by {order}', values)

    def sources(self, values):
        b = self.bind
        return self.cursor.execute(f'select * from note_sources where user_id={b} and note_id={b} order by created_at,id', values)

    def completed_message(self, values):
        b = self.bind
        return self.cursor.execute(f"""
            select id, conversation_id from qa_messages
            where id={b} and conversation_id={b} and user_id={b}
              and role='assistant' and status='completed'
            """, values)

    def active_fence(self, values):
        b = self.bind
        return self.cursor.execute(f"""
            select 1 from qa_deletion_fences
            where user_id={b}
              and (
                status in ('queued','running')
                or (status='failed' and attempt_count < 3)
              )
              and (
                (target_type='conversation' and target_id={b})
                or (target_type='document' and target_id={b} and exists (
                    select 1 from qa_conversation_documents
                    where user_id={b} and conversation_id={b} and document_id={b}
                ))
              )
            limit 1
            """, values)

    def live_version(self, values):
        b = self.bind
        return self.cursor.execute(f'select version from notes where id={b} and user_id={b} and deleted_at is null', values)

    def activity_dates(self, values):
        b = self.bind
        return self.cursor.execute(f"""
                select created_at from notes
                where user_id = {b} and deleted_at is null and created_at >= {b}
                order by created_at
                """, values)

    def live_exists(self, values):
        b = self.bind
        return self.cursor.execute(f'select 1 from notes where id={b} and user_id={b} and deleted_at is null', values)

    def exists(self, values):
        b = self.bind
        return self.cursor.execute(f'select 1 from notes where id={b} and user_id={b}', values)

    def version(self, values):
        b = self.bind
        return self.cursor.execute(f'select version, deleted_at from notes where id={b} and user_id={b}', values)

    def live_notes(self, values):
        b = self.bind
        return self.cursor.execute(f'select id, version from notes where user_id={b} and deleted_at is null', values)

    def failed_projections(self, values):
        b = self.bind
        return self.cursor.execute(f"""
                select t.id, t.note_id from note_projection_tasks t
                join notes n on n.id=t.note_id and n.user_id=t.user_id
                where t.user_id={b} and t.status='failed' and t.note_version=n.version
                """, values)

    def document_sources(self, values):
        b = self.bind
        return self.cursor.execute(f"""select id,user_id,note_id,'document_chunk' as source_kind,
               null as qa_thread_id,null as qa_message_id,null as citation_id,
               document_id,locator_json,title_snapshot,excerpt_snapshot,source_deleted_at,created_at
               from note_document_sources where user_id={b} and note_id={b}""", values)

    def document_fence(self, values):
        b = self.bind
        return self.cursor.execute(f"""select status from qa_deletion_fences
                   where user_id={b} and target_type='document' and target_id={b} limit 1""", values)

    def citation(self, values):
        b = self.bind
        return self.cursor.execute(f"""
                select citation_id, document_id from qa_message_sources
                where assistant_message_id={b} and conversation_id={b} and user_id={b}
                  and citation_id={b}
                """, values)

    def document_scrub_candidates(self, values):
        b = self.bind
        return self.cursor.execute(f'select distinct note_id from note_document_sources where user_id={b} and document_id={b} and source_deleted_at is null', values)

    def count(self, values):
        b = self.bind
        return self.cursor.execute(f'select count(*) as n from notes where user_id={b} and deleted_at is null', values)

    def page(self, user_id, *, limit, match_query, tags, source_kind, last):
        b = self.bind
        params = [user_id]
        clauses = [f'n.user_id={b}', 'n.deleted_at is null']
        joins = ''
        if match_query:
            if self.postgres:
                groups = query_groups(match_query)
                if not groups or any(not group for group in groups):
                    return []
                for group in groups:
                    # A phrase must occur in one field at adjacent offsets.
                    # Equality for every token except the last prefix token.
                    terms = ['p.user_id=n.user_id', 'p.note_id=n.id']
                    for offset, token in enumerate(group):
                        alias = 'p' if offset == 0 else f'p{offset}'
                        if offset == len(group) - 1:
                            predicate = f'left({alias}.token,length({b}))={b}'
                            params.extend((token, token))
                        else:
                            predicate = f'{alias}.token={b}'
                            params.append(token)
                        if offset:
                            terms.append(f'''exists (select 1 from note_search_tokens {alias}
                                where {alias}.user_id=p.user_id and {alias}.note_id=p.note_id
                                and {alias}.field=p.field and {alias}.position=p.position+{offset}
                                and {predicate})''')
                        else:
                            terms.append(predicate)
                    clauses.append('exists (select 1 from note_search_tokens p where ' + ' and '.join(terms) + ')')
            else:
                joins = 'join notes_fts f on f.note_id=n.id and f.user_id=n.user_id'
                clauses.append(f'notes_fts match {b}')
                params.append(match_query)
        for tag in dict.fromkeys(tags):
            clauses.append(f'''exists (select 1 from note_tags t where
                t.user_id=n.user_id and t.note_id=n.id and t.normalized_tag={b})''')
            params.append(tag)
        if source_kind == 'document_chunk':
            clauses.append('''exists (select 1 from note_document_sources s where
                s.user_id=n.user_id and s.note_id=n.id)''')
        elif source_kind is not None:
            clauses.append(f'''exists (select 1 from note_sources s where
                s.user_id=n.user_id and s.note_id=n.id and s.source_kind={b})''')
            params.append(source_kind)
        if last:
            timestamp, identifier = last
            clauses.append(f'(n.updated_at < {b} or (n.updated_at={b} and n.id < {b}))')
            params.extend((timestamp, timestamp, identifier))
        params.append(limit + 1)
        return self.cursor.execute(f'''select n.id from notes n {joins}
            where {' and '.join(clauses)} order by n.updated_at desc,n.id desc limit {b}''', params).fetchall()


class SQLiteNotePersistence:
    def __init__(self, db_path):
        self.db_path = db_path

    @contextmanager
    def read(self):
        with closing(connect(self.db_path)) as conn:
            conn.execute('begin')
            yield NoteStore(conn)

    @contextmanager
    def write(self, user_id):
        with transaction(self.db_path) as conn:
            conn.execute('begin immediate')
            yield NoteStore(conn)

    def caller_owned(self, conn, user_id):
        if isinstance(conn, NoteStore):
            return conn
        if not conn.in_transaction:
            raise ValueError('caller-owned transaction required')
        return NoteStore(conn)


class PostgresNotePersistence:
    def __init__(self, database):
        self.database = database

    @contextmanager
    def read(self):
        with self.database.transaction() as cursor:
            cursor.execute('set transaction isolation level repeatable read, read only')
            yield NoteStore(cursor, postgres=True)

    @contextmanager
    def write(self, user_id):
        with self.database.transaction() as cursor:
            self._lock_user(cursor, user_id)
            yield NoteStore(cursor, postgres=True)

    @staticmethod
    def _lock_user(cursor, user_id):
        # Shared lock order: user first, then replay/version/fence/domain state.
        if cursor.execute('select id from users where id=%s for update', (user_id,)).fetchone() is None:
            raise NoteNotFoundError(user_id)

    def caller_owned(self, cursor, user_id):
        if isinstance(cursor, NoteStore):
            return cursor
        from psycopg.pq import TransactionStatus
        if cursor.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError('caller-owned transaction required')
        self._lock_user(cursor, user_id)
        return NoteStore(cursor, postgres=True)
