from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from threading import Event
from uuid import uuid4

import psycopg

import pytest

from tests.integration.test_postgres_auth_sessions import shared_database
from app.qa_repository import PostgresQaRepository
from app.qa_persistence import QaStore
from app.qa_models import QaDocumentCandidate, QaConflictError, QaValidationError, QaSourceDraft


@pytest.fixture
def repositories(shared_database):
    open_pool, _ = shared_database
    first, second = open_pool(), open_pool()
    with first.transaction() as cur:
        for user in ('alice', 'bob'):
            cur.execute('insert into users values (%s,%s,%s,%s,%s,%s,%s)',
                        (user, user, user, 'hash', 'active', 'now', 'now'))
    return first, PostgresQaRepository(first), PostgresQaRepository(second), open_pool


def documents(user='alice'):
    return [QaDocumentCandidate('doc-2', 'Second 文档', user),
            QaDocumentCandidate('doc-1', 'First', user)]


def test_shared_turn_replay_completion_and_restart(repositories):
    db, first, second, open_pool = repositories
    conversation = first.create_conversation('alice', documents())
    assert second.get_conversation('alice', conversation.id) == conversation
    def create(repo):
        return repo.create_pending_turn('alice', conversation.id, 'question', 'joint', 'request')
    with ThreadPoolExecutor(2) as pool:
        turns = list(pool.map(create, (first, second)))
    assert turns[0].assistant_message == turns[1].assistant_message
    assert sorted(t.duplicate for t in turns) == [False, True]
    with pytest.raises(QaConflictError, match='QA_CONVERSATION_BUSY'):
        first.create_pending_turn('alice', conversation.id, 'different', 'joint', 'other')
    message = turns[0].assistant_message
    source = QaSourceDraft('citation-1', 'doc-2', 'Second 文档', 7, 'section', 'excerpt', 'ref', True, 'rag')
    def complete(repo):
        return repo.complete_turn('alice', message.id, message.version, 'answer', [source], 'available', None)
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(complete, (first, second))) == [False, True]
    result = second.get_message('alice', message.id)
    assert len(result.sources) == 1
    assert result.sources[0].page_number == 7
    db.close()
    assert PostgresQaRepository(open_pool()).get_message('alice', message.id) == result


def pending(repo, conversation_id, request='request', **kwargs):
    return repo.create_pending_turn('alice', conversation_id, 'question', 'joint', request, **kwargs)


def finish(repo, message, sources=(), **kwargs):
    return repo.complete_turn('alice', message.id, message.version, 'answer', sources, 'available' if sources else 'none', None, **kwargs)


def test_request_identity_busy_and_validation(repositories):
    _, first, second, _ = repositories
    c = first.create_conversation('alice', documents())
    def create(pair):
        repo, key = pair
        try:
            return pending(repo, c.id, key)
        except QaConflictError as exc:
            return exc.code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(create, ((first, 'one'), (second, 'two'))))
    assert results.count('QA_CONVERSATION_BUSY') == 1
    turn = next(t for t in results if t != 'QA_CONVERSATION_BUSY')
    # Preserve existing identity semantics: normalized valid changed payload replays original.
    replay = second.create_pending_turn('alice', c.id, 'changed question', 'compare', turn.user_message.client_request_id)
    assert replay.duplicate and replay.user_message == turn.user_message
    assert replay.assistant_message == turn.assistant_message
    with pytest.raises(QaValidationError) as error:
        pending(first, c.id, '')
    assert error.value.code == 'QA_CLIENT_REQUEST_REQUIRED'
    with pytest.raises(QaValidationError):
        first.create_conversation('alice', documents('bob'))
    with pytest.raises(QaValidationError) as error:
        first.create_conversation('missing', documents('missing'))
    assert error.value.code == 'QA_USER_NOT_FOUND'


def test_tenant_denial_all_read_and_write_paths(repositories):
    _, repo, second, _ = repositories
    c = repo.create_conversation('alice', documents())
    turn = pending(repo, c.id)
    m = turn.assistant_message
    assert second.get_conversation('bob', c.id) is None
    assert second.get_message('bob', m.id) is None
    assert not second.list_conversations('bob').items
    assert not second.list_messages('bob', c.id).items
    assert not second.list_recent_messages('bob', c.id).items
    assert second.count_completed_turns('bob') == 0
    assert not second.list_completed_turns_for_report('bob')
    assert not second.list_recent_completed_turns('bob')
    assert not second.list_completed_activity_dates('bob', since='')
    for action in (
        lambda: second.create_pending_turn('bob', c.id, 'q', 'joint', 'foreign'),
        lambda: second.create_pending_retry('bob', c.id, m.id, 'retry'),
    ):
        with pytest.raises(QaValidationError) as error:
            action()
        assert error.value.code == 'QA_CONVERSATION_NOT_FOUND'
    assert not second.complete_turn('bob', m.id, m.version, 'hijack', (), 'none', None)
    assert not second.fail_turn('bob', m.id, m.version, 'error', None)
    assert not second.cancel_turn('bob', m.id, m.version)
    assert not second.hard_delete_conversation('bob', c.id)
    assert not second.update_rolling_summary('bob', c.id, 2, 0, 'foreign', turn.user_message.id)
    assert finish(repo, m)
    assert second.count_completed_turns('bob') == 0
    assert not second.list_completed_turns_for_report('bob')


def test_sources_exact_scope_rollback_and_nonunique_errors_propagate(repositories):
    db, repo, second, _ = repositories
    c = repo.create_conversation('alice', documents())
    m = pending(repo, c.id).assistant_message
    outside = QaSourceDraft('citation', 'outside', 'outside')
    with pytest.raises(QaValidationError) as error:
        finish(repo, m, [outside])
    assert error.value.code == 'QA_SOURCE_OUT_OF_SCOPE'
    assert second.get_message('alice', m.id) == m
    # A schema constraint violation must never be converted to a busy/idempotency error.
    invalid = QaSourceDraft('citation', 'doc-1', None)
    with pytest.raises(psycopg.errors.NotNullViolation):
        finish(repo, m, [invalid])
    assert second.get_message('alice', m.id) == m
    drafts = [QaSourceDraft('c2', 'doc-2', 'Original 文档', 9, '§ two', 'excerpt2', 'chunk://a', True, 'rag'),
              QaSourceDraft('c1', 'doc-1', 'First', None, None, 'excerpt1', 'chunk://b', False, 'document')]
    assert finish(repo, m, drafts)
    actual = second.get_message('alice', m.id)
    for index, (source, draft) in enumerate(zip(actual.sources, drafts)):
        expected = asdict(draft)
        assert {key: getattr(source, key) for key in expected} == expected
        assert source.position == index and source.assistant_message_id == m.id
        assert source.user_id == 'alice' and source.conversation_id == c.id
    assert not finish(second, m, drafts)
    assert second.get_message('alice', m.id) == actual


def test_retry_identity_concurrency_and_caller_owned_rollback(repositories):
    db, repo, second, _ = repositories
    c = repo.create_conversation('alice', documents())
    before = second.get_conversation('alice', c.id)
    class Rollback(Exception):
        pass
    with pytest.raises(Rollback):
        with db.transaction() as cursor:
            cursor.execute('begin')
            repo.create_pending_turn_in_transaction(cursor, 'alice', c.id, 'question', 'joint', 'aborted')
            raise Rollback()
    assert not second.list_messages('alice', c.id).items
    assert second.get_conversation('alice', c.id) == before
    with db.transaction() as cursor:
        cursor.execute('begin')
        turn = repo.create_pending_turn_in_transaction(cursor, 'alice', c.id, 'question', 'joint', 'request')
    assert repo.fail_turn('alice', turn.assistant_message.id, turn.assistant_message.version, 'engine_error', 'trace')
    failed = second.get_message('alice', turn.assistant_message.id)
    before = second.get_conversation('alice', c.id)
    with pytest.raises(Rollback):
        with db.transaction() as cursor:
            cursor.execute('begin')
            repo.create_pending_retry_in_transaction(cursor, 'alice', c.id, failed.id, 'aborted-retry')
            raise Rollback()
    assert len(second.list_messages('alice', c.id).items) == 2
    assert second.get_conversation('alice', c.id) == before
    def retry(pair):
        repository, request = pair
        return repository.create_pending_retry('alice', c.id, failed.id, request)
    with ThreadPoolExecutor(2) as pool:
        retries = list(pool.map(retry, ((repo, 'retry-1'), (second, 'retry-2'))))
    assert retries[0].assistant_message == retries[1].assistant_message
    assert sorted(t.duplicate for t in retries) == [False, True]
    assert retries[0].user_message == turn.user_message
    assert finish(repo, retries[0].assistant_message)
    assert repo.create_pending_retry('alice', c.id, failed.id, 'retry-3').duplicate
    assert second.create_pending_retry('alice', c.id, failed.id, 'aborted-retry').assistant_message.status == 'completed'
    # The canonical retry request cannot be redirected to a different failed message.
    next_turn = pending(repo, c.id, 'next')
    assert repo.fail_turn('alice', next_turn.assistant_message.id, next_turn.assistant_message.version, 'error', None)
    with db.transaction() as cursor:
        key = cursor.execute('select client_request_id from qa_retry_requests where failed_assistant_message_id=%s', (failed.id,)).fetchone()['client_request_id']
    with pytest.raises(QaValidationError) as error:
        second.create_pending_retry('alice', c.id, next_turn.assistant_message.id, key)
    assert error.value.code == 'QA_CLIENT_REQUEST_REUSED'


def test_summary_reports_pagination_and_terminal_versions(repositories):
    _, repo, second, _ = repositories
    c = repo.create_conversation('alice', documents(), now='2026-09-01T00:00:00Z')
    for i in range(4):
        m = pending(repo, c.id, f'r{i}', now=f'2026-09-0{i+2}T00:00:00Z').assistant_message
        assert finish(repo, m, now=f'2026-09-0{i+2}T00:01:00Z')
    page = second.list_messages('alice', c.id, limit=3)
    next_page = second.list_messages('alice', c.id, cursor=page.next_cursor, limit=3)
    assert not set(x.id for x in page.items) & set(x.id for x in next_page.items)
    recent = second.list_recent_messages('alice', c.id, limit=3)
    older = second.list_recent_messages('alice', c.id, cursor=recent.next_cursor, limit=3)
    assert recent.items[-1].id == m.id
    assert not set(x.id for x in recent.items) & set(x.id for x in older.items)
    assert second.count_completed_turns('alice') == 4
    turns = second.list_completed_turns_for_report('alice')
    assert len(turns) == 4 and turns[0].document_ids == ('doc-2', 'doc-1')
    assert turns[0].document_names == ('Second 文档', 'First')
    assert second.list_recent_completed_turns('alice', limit=2) == tuple(reversed(turns[-2:]))
    assert second.list_completed_activity_dates('alice', since='2026-09-04') == ('2026-09-04T00:01:00Z', '2026-09-05T00:01:00Z')
    current = repo.get_conversation('alice', c.id).conversation
    def summary(repository):
        return repository.update_rolling_summary('alice', c.id, current.version, 0, 'summary', m.id)
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(summary, (repo, second))) == [False, True]
    assert not summary(repo)
    updated = second.get_conversation('alice', c.id).conversation
    assert updated.summary_version == 1 and updated.version == current.version + 1
    assert updated.summary_through_message_id == m.id and updated.rolling_summary == 'summary'
    for i in range(3):
        repo.create_conversation('alice', documents(), now='2026-09-07T00:00:00Z')
    p = second.list_conversations('alice', limit=2)
    q = second.list_conversations('alice', cursor=p.next_cursor, limit=2)
    assert len(p.items + q.items) == 4
    assert not set(x.id for x in p.items) & set(x.id for x in q.items)
    turn = pending(repo, c.id, 'cancel')
    assert repo.cancel_turn('alice', turn.assistant_message.id, turn.assistant_message.version)
    assert not repo.fail_turn('alice', turn.assistant_message.id, turn.assistant_message.version, 'error', None)
    assert second.get_message('alice', turn.assistant_message.id).status == 'cancelled'
    assert second.count_completed_turns('alice') == 4


@pytest.mark.parametrize('target_type', ['document', 'conversation'])
@pytest.mark.parametrize('status,attempts,active', [('queued', 0, True), ('running', 1, True), ('failed', 2, True), ('failed', 3, False), ('completed', 1, False)])
def test_deletion_fence_visibility_and_mutation(repositories, target_type, status, attempts, active):
    db, repo, second, _ = repositories
    c = repo.create_conversation('alice', documents())
    first = pending(repo, c.id).assistant_message
    assert finish(repo, first)
    waiting = pending(repo, c.id, 'pending').assistant_message
    with db.transaction() as cursor:
        cursor.execute('select id from users where id=%s for update', ('alice',))
        cursor.execute('''insert into qa_deletion_fences
            (id,user_id,target_type,target_id,status,stage,attempt_count,created_at,updated_at)
            values (%s,'alice',%s,%s,%s,'fenced',%s,'now','now')''',
            (str(uuid4()), target_type, 'doc-2' if target_type == 'document' else c.id, status, attempts))
    assert (second.get_conversation('alice', c.id) is None) == active
    assert (second.get_message('alice', first.id) is None) == active
    assert (not second.list_messages('alice', c.id).items) == active
    assert (not second.list_recent_messages('alice', c.id).items) == active
    assert (not second.list_conversations('alice').items) == active
    assert second.count_completed_turns('alice') == (0 if active else 1)
    assert (not second.list_completed_turns_for_report('alice')) == active
    assert (not second.list_recent_completed_turns('alice')) == active
    assert (not second.list_completed_activity_dates('alice', since='')) == active
    assert finish(second, waiting) == (not active)
    if active:
        assert not second.fail_turn('alice', waiting.id, waiting.version, 'error', None)
        assert not second.cancel_turn('alice', waiting.id, waiting.version)
        assert not second.update_rolling_summary('alice', c.id, 4, 0, 'summary', first.id)
        with pytest.raises(QaValidationError):
            pending(second, c.id, 'new')
        with pytest.raises(QaValidationError):
            second.create_pending_retry('alice', c.id, first.id, 'retry')
        if target_type == 'document':
            with pytest.raises(QaValidationError) as error:
                second.create_conversation('alice', documents())
            assert error.value.code == 'QA_DOCUMENT_DELETING'


@pytest.mark.parametrize('method', ['list_conversations', 'get_conversation', 'list_messages', 'get_message', 'list_completed_turns_for_report'])
def test_multiquery_dto_snapshot_survives_concurrent_delete(repositories, monkeypatch, method):
    _, repo, writer, _ = repositories
    c = repo.create_conversation('alice', documents())
    m = pending(repo, c.id).assistant_message
    assert finish(repo, m, [QaSourceDraft('citation', 'doc-2', 'Second 文档')])
    operation = 'sources' if method in ('list_messages', 'get_message') else 'report_documents' if method == 'list_completed_turns_for_report' else 'documents'
    reached, deleted = Event(), Event()
    original = getattr(QaStore, operation)
    def pause(store, params):
        reached.set()
        assert deleted.wait(10)
        return original(store, params)
    monkeypatch.setattr(QaStore, operation, pause)
    args = ('alice', c.id) if method in ('get_conversation', 'list_messages') else ('alice', m.id) if method == 'get_message' else ('alice',)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(getattr(repo, method), *args)
        try:
            assert reached.wait(10)
            assert writer.hard_delete_conversation('alice', c.id)
        finally:
            deleted.set()
        result = future.result(timeout=10)
    if method in ('list_conversations', 'get_conversation'):
        aggregate = result.items[0] if method == 'list_conversations' else result
        assert len(aggregate.documents) == 2
    elif method == 'list_completed_turns_for_report':
        assert result[0].document_ids == ('doc-2', 'doc-1')
    else:
        message = result.items[-1] if method == 'list_messages' else result
        assert len(message.sources) == 1
    assert writer.get_conversation('alice', c.id) is None
    # Isolation is transaction-local; no connection-pool session default mutation.
    with repo._persistence.database.transaction() as cursor:
        assert cursor.execute('show transaction_isolation').fetchone()['transaction_isolation'] == 'read committed'


@pytest.mark.parametrize('method', ['claim_next_memory_sync', 'heartbeat_memory_sync', 'complete_memory_sync', 'fail_memory_sync', 'recover_interrupted_questions'])
def test_worker_methods_fail_closed_without_database(method):
    repo = PostgresQaRepository(object())
    with pytest.raises(NotImplementedError, match='durable lease integration'):
        getattr(repo, method)()


def test_caller_owned_requires_transaction_holds_user_lock_and_does_not_commit(repositories):
    db, repo, second, _ = repositories
    c = repo.create_conversation('alice', documents())
    with db.connection() as connection:
        with connection.cursor() as cursor:
            with pytest.raises(ValueError, match='caller-owned'):
                repo.create_pending_turn_in_transaction(cursor, 'alice', c.id, 'question', 'joint', 'idle')
            with pytest.raises(ValueError, match='caller-owned'):
                repo.create_pending_retry_in_transaction(cursor, 'alice', c.id, 'missing', 'idle')
    with db.transaction() as cursor:
        cursor.execute('begin')
        turn = repo.create_pending_turn_in_transaction(cursor, 'alice', c.id, 'question', 'joint', 'committed')
        assert not second.list_messages('alice', c.id).items
        with pytest.raises(psycopg.errors.LockNotAvailable):
            with second._persistence.database.transaction() as other:
                other.execute("select id from users where id='alice' for update nowait")
        assert cursor.execute('select count(*) as n from qa_messages').fetchone()['n'] == 2
        again = repo.create_pending_turn_in_transaction(cursor, 'alice', c.id, 'question', 'joint', 'committed')
        assert again.duplicate and again.assistant_message == turn.assistant_message
    assert second.get_message('alice', turn.assistant_message.id) == turn.assistant_message
    with pytest.raises(QaValidationError) as error:
        with db.transaction() as cursor:
            cursor.execute('begin')
            repo.create_pending_turn_in_transaction(cursor, 'missing', c.id, 'question', 'joint', 'missing')
    assert error.value.code == 'QA_USER_NOT_FOUND'


def test_legacy_retry_adoption_and_delete_cascade(repositories):
    db, repo, second, _ = repositories
    c = repo.create_conversation('alice', documents())
    turn = pending(repo, c.id)
    failed = turn.assistant_message
    assert repo.fail_turn('alice', failed.id, failed.version, 'error', None)
    with db.transaction() as cursor:
        cursor.execute("select id from users where id='alice' for update")
        cursor.execute('''insert into qa_messages
            (id,conversation_id,user_id,turn_id,role,status,mode,retry_of_message_id,created_at,updated_at)
            values ('legacy',%s,'alice',%s,'assistant','pending','joint',%s,'now','now')''',
            (c.id, failed.turn_id, failed.id))
    adopted = repo.create_pending_retry('alice', c.id, failed.id, 'adopted')
    assert adopted.duplicate and adopted.assistant_message.id == 'legacy'
    assert adopted.user_message == turn.user_message
    assert second.create_pending_retry('alice', c.id, failed.id, 'another').assistant_message == adopted.assistant_message
    assert finish(repo, adopted.assistant_message, [QaSourceDraft('c', 'doc-2', 'Second')])
    assert repo.hard_delete_conversation('alice', c.id)
    assert not second.hard_delete_conversation('alice', c.id)
    with db.transaction() as cursor:
        for table in ('qa_messages', 'qa_retry_requests', 'qa_message_sources', 'qa_conversation_documents'):
            assert cursor.execute(f'select count(*) as n from {table}').fetchone()['n'] == 0
