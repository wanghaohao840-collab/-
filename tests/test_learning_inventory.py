import hashlib
from datetime import datetime, timezone
import json
from uuid import uuid4

import pytest

from app.database import connect, initialize_database
from app.document_library import DocumentLibraryService
from app.storage import UserStorage
from app.learning_inventory import DocumentInventoryError, read_ready_documents
import app.learning_inventory as inventory


@pytest.fixture
def setup(tmp_path):
    initialize_database(tmp_path / 'app.db')
    owner, other = str(uuid4()), str(uuid4())
    with connect(tmp_path / 'app.db') as db:
        for user in (owner, other):
            db.execute('insert into users values (?,?,?,?,?,?,?)', (user,user,user,'hash','active','now','now'))
    history = tmp_path / 'users' / owner / 'history.json'
    history.parent.mkdir(parents=True)
    record = dict(document_id='doc', document_name='资料.md', file_suffix='.md',
                  document_path=str(history.parent / 'documents' / 'doc.md'), loaded_at='2026-09-07T00:00:00Z')
    return tmp_path, owner, other, history, record


def write(setup, documents=None):
    root, owner, other, history, record = setup
    payload = json.dumps(dict(documents=[record] if documents is None else documents,
                              questions=[], notes=[], sessions=[]), ensure_ascii=False).encode()
    history.write_bytes(payload)
    return payload


def test_receipt_uses_shared_projection_without_creating_document_directories(setup):
    root, owner, _, history, record = setup
    payload = write(setup)
    before = (root / 'app.db').read_bytes()
    receipt = read_ready_documents(root, user_id=owner)
    service = DocumentLibraryService(None, UserStorage(root), None)
    projected = service.project_history_documents(owner, [record])
    assert receipt.ready_document_ids == frozenset(item.document_id for item in projected) == {'doc'}
    assert receipt.user_id == owner and receipt.source_sha256 == hashlib.sha256(payload).hexdigest()
    assert projected[0].size_bytes is None  # Preserve existing missing-source semantics.
    assert not (history.parent / 'documents').exists()
    assert (root / 'app.db').read_bytes() == before
    assert history.read_bytes() == payload


@pytest.mark.parametrize('status', ['queued', 'running', 'failed', 'completed'])
def test_all_owner_document_fences_excluded_other_user_fences_ignored(setup, status):
    root, owner, other, _, record = setup
    write(setup, [record, record | {'document_id': 'other-doc'}])
    with connect(root / 'app.db') as db:
        for user, target in ((owner, 'doc'), (other, 'other-doc')):
            db.execute('''insert into qa_deletion_fences
                (id,user_id,target_type,target_id,status,stage,created_at,updated_at)
                values (?,?,'document',?,?,'fenced','now','now')''', (str(uuid4()), user, target, status))
    assert read_ready_documents(root, user_id=owner).ready_document_ids == {'other-doc'}


@pytest.mark.parametrize('change', [dict(document_name='../bad'), dict(file_suffix='.pdf'),
                                  dict(document_path='relative/doc.md'), dict(document_name='')])
def test_latest_invalid_record_suppresses_earlier_valid_record(setup, change):
    write(setup, [setup[4], setup[4] | change])
    assert read_ready_documents(setup[0], user_id=setup[1]).ready_document_ids == set()


def test_foreign_path_never_becomes_owned(setup):
    root, owner, other, _, record = setup
    write(setup, [record | {'document_path': str(root / 'users' / other / 'documents' / 'doc.md')}])
    assert read_ready_documents(root, user_id=owner).ready_document_ids == set()


def test_explicit_foreign_owner_rejected(setup):
    write(setup, [setup[4] | {'user_id': setup[2]}])
    with pytest.raises(DocumentInventoryError, match='HISTORY_OWNER'):
        read_ready_documents(setup[0], user_id=setup[1])


@pytest.mark.parametrize('payload', [b'?', b'[]', b'{"documents":[],"documents":[]}',
                                    b'{"documents":{},"notes":[]}', b'{"documents":[1]}',
                                    b'{"documents":[],"notes":[NaN]}',
                                    b'{"documents":[],"notes":[1e999]}', b'{}', b'\xff'])
def test_malformed_history_fails_closed(setup, payload):
    setup[3].write_bytes(payload)
    with pytest.raises(DocumentInventoryError, match='HISTORY_INVALID'):
        read_ready_documents(setup[0], user_id=setup[1])


def test_missing_history_and_other_user_do_not_create_paths(setup):
    root, owner, other, _, _ = setup
    for user in (owner, other):
        with pytest.raises(DocumentInventoryError, match='HISTORY_UNAVAILABLE'):
            read_ready_documents(root, user_id=user)
    assert not (root / 'users' / other).exists()


def test_missing_registry_not_created(tmp_path):
    with pytest.raises(DocumentInventoryError):
        read_ready_documents(tmp_path, user_id=str(uuid4()))
    assert list(tmp_path.iterdir()) == []


def test_inactive_account_rejected(setup):
    write(setup)
    with connect(setup[0] / 'app.db') as db:
        db.execute("update users set status='disabled' where id=?", (setup[1],))
    with pytest.raises(DocumentInventoryError, match='HISTORY_UNAVAILABLE'):
        read_ready_documents(setup[0], user_id=setup[1])


def test_changed_history_rejected(setup, monkeypatch):
    write(setup)
    original = inventory.read_history_source
    calls = 0
    def changed(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        return result if calls == 1 else result + b' '
    monkeypatch.setattr(inventory, 'read_history_source', changed)
    with pytest.raises(DocumentInventoryError, match='HISTORY_CHANGED'):
        read_ready_documents(setup[0], user_id=setup[1])


@pytest.mark.parametrize('kind', ['directory', 'large'])
def test_unsafe_or_large_history_rejected(setup, kind):
    if kind == 'directory':
        setup[3].mkdir()
    else:
        setup[3].write_bytes(b' ' * (16*1024*1024+1))
    with pytest.raises(DocumentInventoryError, match='HISTORY_UNAVAILABLE'):
        read_ready_documents(setup[0], user_id=setup[1])


def test_inventory_consumed_by_importer_with_transactional_fence_recheck(setup):
    from app.learning_migration import MigrationImportError, import_legacy_plans
    from app.learning_repository import LearningRepository

    root, owner, _, _, _ = setup
    write(setup)
    receipt = read_ready_documents(root, user_id=owner)
    now = datetime(2026, 9, 7, tzinfo=timezone.utc)
    plan_id = str(uuid4())
    payload = json.dumps(dict(version=1, cards=[], exercises=[], review_logs=[], plans=[dict(
        id=plan_id, document_id='doc', document_name='资料.md', title='学习',
        target_date='2026-09-07', daily_minutes=30, status='active',
        created_at=now.isoformat(), updated_at=now.isoformat(), tasks=[dict(
            id=str(uuid4()), due_date='2026-09-07', phase='review', title='复习',
            duration_minutes=30, completed=False, completed_at=None)])])).encode()
    args = dict(user_id=owner, payload=payload, timezone_name='Asia/Shanghai',
                ready_document_ids=receipt.ready_document_ids, now=now)
    # A receipt is not an enduring authorization: later fences still win.
    with connect(root / 'app.db') as db:
        db.execute('''insert into qa_deletion_fences
            (id,user_id,target_type,target_id,status,stage,created_at,updated_at)
            values ('test-fence',?,'document','doc','completed','fenced','now','now')''', (owner,))
    with pytest.raises(MigrationImportError, match='DOCUMENT_UNAVAILABLE'):
        import_legacy_plans(root / 'app.db', **args)
    with connect(root / 'app.db') as db:
        assert db.execute('select count(*) from learning_plans').fetchone()[0] == 0
        db.execute("delete from qa_deletion_fences where id='test-fence'")
    result = import_legacy_plans(root / 'app.db', **args)
    assert result.plan_count == result.task_count == 1 and not result.replayed
    assert LearningRepository(root / 'app.db').get_plan(owner, plan_id).title == '学习'
