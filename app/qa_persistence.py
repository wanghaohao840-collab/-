"""Fixed QA domain statements and backend transaction boundaries.

No SQL translation or emulated SQLite connection; both backends use the same
explicit operations, while validation and DTO assembly live in QaRepository.
"""
from contextlib import closing, contextmanager

from app.database import connect
from app.qa_models import QaValidationError


def _not_fenced_clause(conversation_alias: str) -> str:
    return f"""
    not exists (
        select 1 from qa_deletion_fences deletion_fence
        where deletion_fence.user_id = {conversation_alias}.user_id
          and (
              deletion_fence.status in ('queued', 'running')
              or (
                  deletion_fence.status = 'failed'
                  and deletion_fence.attempt_count < 3
              )
          )
          and (
              (
                  deletion_fence.target_type = 'conversation'
                  and deletion_fence.target_id = {conversation_alias}.id
              )
              or (
                  deletion_fence.target_type = 'document'
                  and exists (
                      select 1 from qa_conversation_documents fenced_document
                      where fenced_document.conversation_id = {conversation_alias}.id
                        and fenced_document.user_id = {conversation_alias}.user_id
                        and fenced_document.document_id = deletion_fence.target_id
                  )
              )
          )
    )
    """


class QaStore:
    def __init__(self, cursor, *, postgres=False, locked_user_id=None):
        self.cursor = cursor
        self.postgres = postgres
        self.bind = '%s' if postgres else '?'
        self.locked_user_id = locked_user_id

    def selected_documents_fenced(self, params, document_count):
        b = self.bind
        return self.cursor.execute(f"""
            select 1 from qa_deletion_fences
            where user_id = {b} and target_type = 'document'
              and (
                  status in ('queued', 'running')
                  or (status = 'failed' and attempt_count < 3)
              )
              and target_id in ({','.join([b] * document_count)})
            limit 1
            """, params)

    def insert_conversation(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            insert into qa_conversations (
                id, user_id, title, origin, created_at, updated_at,
                last_message_at
            ) values ({b}, {b}, '新对话', {b}, {b}, {b}, {b})
            """, params)

    def insert_documents(self, params):
        b = self.bind
        return self.cursor.executemany(f"""
            insert into qa_conversation_documents (
                conversation_id, user_id, document_id, document_name, position
            ) values ({b}, {b}, {b}, {b}, {b})
            """, params)

    def conversation_page(self, params, has_cursor):
        b = self.bind
        cursor_clause = (f'and (last_message_at < {b} or (last_message_at = {b} and id < {b}))'
                         if has_cursor else '')
        return self.cursor.execute(f"""
            select * from qa_conversations
            where user_id = {b}
              and {_not_fenced_clause('qa_conversations')}
              {cursor_clause}
            order by last_message_at desc, id desc
            limit {b}
            """, params)

    def message_page(self, params, has_cursor):
        b = self.bind
        cursor_clause = (f'and (created_at > {b} or (created_at = {b} and id > {b}))'
                         if has_cursor else '')
        return self.cursor.execute(f"""
            select * from qa_messages
            where user_id = {b} and conversation_id = {b}
              and exists (
                  select 1 from qa_conversations
                  where qa_conversations.id = qa_messages.conversation_id
                    and qa_conversations.user_id = qa_messages.user_id
                    and {_not_fenced_clause('qa_conversations')}
              )
              {cursor_clause}
            order by created_at, id
            limit {b}
            """, params)

    def recent_message_page(self, params, has_cursor):
        b = self.bind
        cursor_clause = (f'and (created_at < {b} or (created_at = {b} and id < {b}))'
                         if has_cursor else '')
        return self.cursor.execute(f"""
            select * from qa_messages
            where user_id = {b} and conversation_id = {b}
              and exists (
                  select 1 from qa_conversations
                  where qa_conversations.id = qa_messages.conversation_id
                    and qa_conversations.user_id = qa_messages.user_id
                    and {_not_fenced_clause('qa_conversations')}
              )
              {cursor_clause}
            order by created_at desc, id desc
            limit {b}
            """, params)

    def visible_message(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select * from qa_messages where id = {b} and user_id = {b}
              and exists (
                  select 1 from qa_conversations
                  where qa_conversations.id = qa_messages.conversation_id
                    and qa_conversations.user_id = qa_messages.user_id
                    and {_not_fenced_clause('qa_conversations')}
              )
            """, params)

    def pending_for_completion(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select conversation_id from qa_messages
            where id = {b} and user_id = {b} and role = 'assistant'
              and status = 'pending' and version = {b}
              and exists (
                  select 1 from qa_conversations
                  where qa_conversations.id = qa_messages.conversation_id
                    and qa_conversations.user_id = qa_messages.user_id
                    and {_not_fenced_clause('qa_conversations')}
              )
            """, params)

    def complete_message(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            update qa_messages
            set status = 'completed', content = {b}, source_state = {b},
                memory_id = {b}, memory_sync_status = {b}, safe_error_code = null,
                trace_id = null, version = version + 1, updated_at = {b},
                completed_at = {b}
            where id = {b} and user_id = {b} and role = 'assistant'
              and status = 'pending' and version = {b}
            """, params)

    def insert_sources(self, params):
        b = self.bind
        return self.cursor.executemany(f"""
            insert into qa_message_sources (
                id, assistant_message_id, conversation_id, user_id,
                position, citation_id, document_id, document_name,
                page_number, section, excerpt, reference, truncated,
                source_type
            ) values ({b}, {b}, {b}, {b}, {b}, {b}, {b}, {b}, {b}, {b}, {b}, {b}, {b}, {b})
            """, params)

    def delete_conversation(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            delete from qa_conversations where id = {b} and user_id = {b}
            """, params)

    def report_turns(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select user_message.content as question,
                   assistant_message.content as answer,
                   user_message.mode as mode,
                   user_message.created_at as asked_at,
                   user_message.conversation_id as conversation_id
            from qa_messages user_message
            join qa_messages assistant_message
              on assistant_message.user_id = user_message.user_id
             and assistant_message.conversation_id = user_message.conversation_id
             and assistant_message.turn_id = user_message.turn_id
             and assistant_message.role = 'assistant'
             and assistant_message.status = 'completed'
            join qa_conversations
              on qa_conversations.id = user_message.conversation_id
             and qa_conversations.user_id = user_message.user_id
            where user_message.user_id = {b}
              and user_message.role = 'user'
              and user_message.status = 'completed'
              and {_not_fenced_clause('qa_conversations')}
            order by user_message.created_at, user_message.id
            """, params)

    def report_documents(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select document_id, document_name
            from qa_conversation_documents
            where user_id = {b} and conversation_id = {b}
            order by position
            """, params)

    def recent_report_turns(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select user_message.content as question,
                   assistant_message.content as answer,
                   user_message.mode as mode,
                   user_message.created_at as asked_at,
                   user_message.conversation_id as conversation_id
            from qa_messages user_message
            join qa_messages assistant_message
              on assistant_message.user_id = user_message.user_id
             and assistant_message.conversation_id = user_message.conversation_id
             and assistant_message.turn_id = user_message.turn_id
             and assistant_message.role = 'assistant'
             and assistant_message.status = 'completed'
            join qa_conversations
              on qa_conversations.id = user_message.conversation_id
             and qa_conversations.user_id = user_message.user_id
            where user_message.user_id = {b} and user_message.role = 'user'
              and user_message.status = 'completed'
              and {_not_fenced_clause('qa_conversations')}
            order by user_message.created_at desc, user_message.id desc
            limit {b}
            """, params)

    def completed_count(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select count(*) as count from qa_messages assistant_message
            join qa_conversations
              on qa_conversations.id = assistant_message.conversation_id
             and qa_conversations.user_id = assistant_message.user_id
            where assistant_message.user_id = {b}
              and assistant_message.role = 'assistant'
              and assistant_message.status = 'completed'
              and {_not_fenced_clause('qa_conversations')}
            """, params)

    def activity_dates(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select assistant_message.completed_at as occurred_at
            from qa_messages assistant_message
            join qa_conversations
              on qa_conversations.id = assistant_message.conversation_id
             and qa_conversations.user_id = assistant_message.user_id
            where assistant_message.user_id = {b}
              and assistant_message.role = 'assistant'
              and assistant_message.status = 'completed'
              and assistant_message.completed_at >= {b}
              and {_not_fenced_clause('qa_conversations')}
            order by assistant_message.completed_at
            """, params)

    def update_summary(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            update qa_conversations
            set rolling_summary = {b}, summary_through_message_id = {b},
                summary_version = summary_version + 1,
                version = version + 1, updated_at = {b}
            where id = {b} and user_id = {b} and version = {b}
              and summary_version = {b}
              and {_not_fenced_clause('qa_conversations')}
              and exists (
                  select 1 from qa_messages
                  where id = {b} and conversation_id = qa_conversations.id
                    and user_id = qa_conversations.user_id
                    and status = 'completed'
              )
            """, params)

    def visible_conversation(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select * from qa_conversations where id = {b} and user_id = {b}
              and {_not_fenced_clause('qa_conversations')}
            """, params)

    def ordered_document_ids(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select document_id from qa_conversation_documents
            where conversation_id = {b} and user_id = {b} order by position
            """, params)

    def request_message(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select * from qa_messages
            where user_id = {b} and conversation_id = {b} and role = 'user'
              and client_request_id = {b}
            """, params)

    def turn_assistant(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select * from qa_messages
            where user_id = {b} and conversation_id = {b}
              and turn_id = {b} and role = 'assistant'
            """, params)

    def failed_assistant_exists(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select 1 from qa_messages
            where id = {b} and user_id = {b} and conversation_id = {b}
              and role = 'assistant' and status = 'failed'
            """, params)

    def pending_exists(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select 1 from qa_messages
            where user_id = {b} and conversation_id = {b}
              and role = 'assistant' and status = 'pending'
            """, params)

    def insert_user_message(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            insert into qa_messages (
                id, conversation_id, user_id, turn_id, role, status, mode,
                content, source_state, client_request_id, memory_sync_status,
                created_at, updated_at, completed_at
            ) values (
                {b}, {b}, {b}, {b}, 'user', 'completed', {b}, {b}, 'none', {b},
                'not_required', {b}, {b}, {b}
            )
            """, params)

    def insert_assistant_message(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            insert into qa_messages (
                id, conversation_id, user_id, turn_id, role, status, mode,
                content, source_state, retry_of_message_id,
                memory_sync_status, created_at, updated_at
            ) values (
                {b}, {b}, {b}, {b}, 'assistant', 'pending', {b}, '', 'none', {b},
                'not_required', {b}, {b}
            )
            """, params)

    def prior_message_count(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select count(*) as count from qa_messages
            where conversation_id = {b} and user_id = {b} and id != {b} and id != {b}
            """, params)

    def update_conversation_title(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            update qa_conversations
            set title = coalesce({b}, title), last_message_at = {b}, updated_at = {b},
                version = version + 1
            where id = {b} and user_id = {b}
            """, params)

    def message(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select * from qa_messages where id = {b} and user_id = {b}
            """, params)

    def retry_request(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select * from qa_retry_requests
            where user_id = {b} and conversation_id = {b} and client_request_id = {b}
            """, params)

    def retry_for_target(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select * from qa_retry_requests
            where user_id = {b} and conversation_id = {b}
              and failed_assistant_message_id = {b}
            """, params)

    def failed_assistant(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select * from qa_messages
            where id = {b} and user_id = {b} and conversation_id = {b}
              and role = 'assistant' and status = 'failed'
            """, params)

    def paired_user(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select * from qa_messages
            where user_id = {b} and conversation_id = {b} and turn_id = {b}
              and role = 'user' and status = 'completed'
            order by created_at, id limit 1
            """, params)

    def legacy_retry(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select * from qa_messages
            where user_id = {b} and conversation_id = {b}
              and retry_of_message_id = {b} and role = 'assistant'
            order by created_at, id limit 1
            """, params)

    def insert_retry_request(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            insert into qa_retry_requests (
                user_id, conversation_id, failed_assistant_message_id,
                assistant_message_id, client_request_id, created_at
            ) values ({b}, {b}, {b}, {b}, {b}, {b})
            """, params)

    def touch_conversation(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            update qa_conversations
            set last_message_at = {b}, updated_at = {b}, version = version + 1
            where id = {b} and user_id = {b}
            """, params)

    def retry_target_turn(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select turn_id from qa_messages
            where id = {b} and user_id = {b} and conversation_id = {b}
            """, params)

    def conversation_message(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select * from qa_messages
            where id = {b} and user_id = {b} and conversation_id = {b}
            """, params)

    def finish_message(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            update qa_messages
            set status = {b}, safe_error_code = {b}, trace_id = {b},
                memory_sync_status = 'not_required', version = version + 1,
                updated_at = {b}, completed_at = {b}
            where id = {b} and user_id = {b} and role = 'assistant'
              and status = 'pending' and version = {b}
            """, params)

    def document_ids(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select document_id from qa_conversation_documents
            where user_id = {b} and conversation_id = {b}
            """, params)

    def documents(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select * from qa_conversation_documents
            where conversation_id = {b} and user_id = {b} order by position
            """, params)

    def sources(self, params):
        b = self.bind
        return self.cursor.execute(f"""
            select * from qa_message_sources
            where assistant_message_id = {b} and user_id = {b} order by position
            """, params)


class SQLiteQaPersistence:
    def __init__(self, db_path):
        self.db_path = db_path

    @contextmanager
    def read(self):
        with closing(connect(self.db_path)) as conn:
            conn.execute('begin')
            yield QaStore(conn)

    @contextmanager
    def write(self, user_id):
        with closing(connect(self.db_path)) as conn:
            try:
                conn.execute('begin immediate')
                yield QaStore(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def caller_owned(self, conn, user_id):
        if isinstance(conn, QaStore):
            return conn
        if not conn.in_transaction:
            raise ValueError('caller-owned transaction required')
        return QaStore(conn)


class PostgresQaPersistence:
    def __init__(self, database):
        self.database = database

    @contextmanager
    def read(self):
        with self.database.transaction() as cursor:
            cursor.execute('set transaction isolation level repeatable read, read only')
            yield QaStore(cursor, postgres=True)

    @contextmanager
    def write(self, user_id):
        with self.database.transaction() as cursor:
            self._lock_user(cursor, user_id)
            yield QaStore(cursor, postgres=True, locked_user_id=user_id)

    @staticmethod
    def _lock_user(cursor, user_id):
        # All writers, including future queue and deletion adapters, lock users first.
        if cursor.execute('select id from users where id=%s for update', (user_id,)).fetchone() is None:
            raise QaValidationError('QA_USER_NOT_FOUND', 'user not found')

    def caller_owned(self, cursor, user_id):
        if isinstance(cursor, QaStore):
            if not cursor.postgres or cursor.locked_user_id != user_id:
                raise ValueError('caller-owned QA transaction must hold this user lock')
            return cursor
        from psycopg.pq import TransactionStatus
        if cursor.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError('caller-owned transaction required')
        self._lock_user(cursor, user_id)
        return QaStore(cursor, postgres=True, locked_user_id=user_id)
