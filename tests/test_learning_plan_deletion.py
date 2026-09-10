import pytest

from app.database import connect
from app.qa_deletion import QaDeletionRepository
from tests.test_learning_plan_repository import setup, command, NOW


def fence(path, kind):
    with connect(path) as conn:
        conn.execute('''insert into qa_deletion_fences
            (id,user_id,target_type,target_id,status,stage,lease_owner,lease_expires_at,created_at,updated_at)
            values ('f','owner',?,'doc','running','fenced','worker','2026-09-09T00:00:00Z','now','now')''', (kind,))


@pytest.mark.parametrize('kind,expected', [('document',0),('conversation',1)])
def test_target_scoped_cleanup(setup, kind, expected):
    path, repo = setup
    for user in ('owner','other'):
        repo.create_plan(user, command(), document_name='资料', now=NOW)
    fence(path, kind)
    deletion = QaDeletionRepository(path, learning_repository=repo)
    assert deletion.remove_qa_rows('f','worker',now='2026-09-07T00:00:00Z')
    with connect(path) as conn:
        assert conn.execute("select count(*) from learning_plans where user_id='owner'").fetchone()[0] == expected
        assert conn.execute("select count(*) from learning_plans where user_id='other'").fetchone()[0] == 1
        assert conn.execute("select count(*) from learning_requests where user_id='owner'").fetchone()[0] == 1
        assert conn.execute("select stage from qa_deletion_fences where id='f'").fetchone()[0] == 'qa_rows_removed'


def test_failure_rolls_back_then_retry(setup):
    path, repo = setup
    repo.create_plan('owner',command(),document_name='资料',now=NOW)
    fence(path,'document')
    class FailingNotes:
        def scrub_sources_in_transaction(self, *args, **kwargs):
            raise RuntimeError('injected after learning deletion')
    deletion = QaDeletionRepository(path, FailingNotes(), learning_repository=repo)
    with pytest.raises(RuntimeError):
        deletion.remove_qa_rows('f','worker',now='2026-09-07T00:00:00Z')
    with connect(path) as conn:
        assert conn.execute('select count(*) from learning_plans').fetchone()[0] == 1
        assert conn.execute("select stage from qa_deletion_fences where id='f'").fetchone()[0] == 'fenced'
    deletion.note_repository = None
    assert deletion.remove_qa_rows('f','worker',now='2026-09-07T00:00:00Z')
