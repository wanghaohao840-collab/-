from dataclasses import replace
from hashlib import sha256
from threading import RLock
from types import SimpleNamespace

import pytest

from app.database import connect
from app.learning_service import LearningService
from app.learning_models import LearningMigrationRequired, LearningNotFound
from app.session import InvalidSessionError
from app.storage import UserStorage
from tests.test_learning_plan_repository import setup, command, NOW


@pytest.fixture
def services(setup):
    path, repo = setup
    storage = UserStorage(path.parent)
    current = SimpleNamespace(user_id='owner', token='token', runtime=SimpleNamespace(lock=RLock()))
    class Registry:
        def get_session(self, token):
            if token != 'token':
                raise InvalidSessionError()
            return current
    docs = [SimpleNamespace(document_id='doc', name='server name', status='ready')]
    library = SimpleNamespace(list_documents=lambda token: tuple(docs))
    return LearningService(repo, Registry(), library, storage, now=lambda: NOW), current, storage, docs


def test_session_identity_and_document_authority(services):
    service, current, _, docs = services
    supplied = SimpleNamespace(token='token', user_id='other')
    result = service.create_plan(supplied, command())
    assert result.plan.document_name == 'server name'
    assert service.get_plan(current, result.plan.id) == result.plan
    with pytest.raises(InvalidSessionError):
        service.list_plans(SimpleNamespace(token='expired'))
    docs.clear()
    assert not service.list_plans(current).items
    with pytest.raises(LearningNotFound):
        service.get_plan(current, result.plan.id)
    with pytest.raises(LearningNotFound):
        service.create_plan(current, command())


def test_legacy_gate_preserves_file_and_matching_marker(services, setup):
    service, current, storage, _ = services
    path = storage.user_paths('owner').root / 'learning.json'
    path.parent.mkdir(parents=True)
    raw = b'{"version":1,"plans":[],"cards":[],"exercises":[],"review_logs":[]}'
    path.write_bytes(raw)
    with pytest.raises(LearningMigrationRequired):
        service.create_plan(current, command())
    assert path.read_bytes() == raw
    with connect(setup[0]) as db:
        db.execute("insert into learning_migrations values ('owner',?,1,1,'Asia/Shanghai',0,0,'normalized','now')", (sha256(raw).hexdigest(),))
    assert service.create_plan(current, command()).plan.task_count == 1
    path.write_bytes(raw + b' ')
    with pytest.raises(LearningMigrationRequired):
        service.list_plans(current)


def test_unready_and_cross_user_resource(services, setup):
    service, current, _, docs = services
    other = setup[1].create_plan('other', command(), document_name='other', now=NOW).plan
    with pytest.raises(LearningNotFound):
        service.get_plan(current, other.id)
    docs[0].status = 'pending'
    with pytest.raises(LearningNotFound):
        service.create_plan(current, command())


def test_bootstrap_wires_shared_repository(tmp_path):
    from app.bootstrap import ApplicationServices
    app = ApplicationServices.create(tmp_path)
    assert app.learning_service.repository is app.learning_repository
    assert app.qa_deletion_repository.learning_repository is app.learning_repository


def test_oversize_legacy_blocks_without_reading(services):
    service, current, storage, _ = services
    path = storage.user_paths('owner').root / 'learning.json'
    path.parent.mkdir(parents=True)
    with path.open('wb') as stream:
        stream.truncate(16 * 1024 * 1024 + 1)
    with pytest.raises(LearningMigrationRequired):
        service.today(current, bucket='today')


def test_reparse_source_blocks(services, monkeypatch):
    from pathlib import Path
    service, current, storage, _ = services
    path = storage.user_paths('owner').root / 'learning.json'
    path.parent.mkdir(parents=True)
    path.write_text('{}')
    original = Path.lstat
    def replaced(target):
        info = original(target)
        if target == path:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=1024)
        return info
    monkeypatch.setattr(Path, 'lstat', replaced)
    with pytest.raises(LearningMigrationRequired):
        service.list_plans(current)
