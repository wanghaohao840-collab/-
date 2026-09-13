from uuid import uuid4

import httpx
import pytest

from deploy.learning_coordinator import CoordinatorError, execute_maintenance
from deploy.learning_maintenance import begin_journal, read_journal, JournalError
from deploy.maintenance_app import create_server_application


@pytest.mark.parametrize('mode,operation', [('2', ''), ('0', str(uuid4())), ('1', 'invalid')])
def test_invalid_maintenance_configuration_rejected(monkeypatch, mode, operation):
    monkeypatch.setenv('ZHIYAN_MAINTENANCE_MODE', mode)
    monkeypatch.setenv('ZHIYAN_MAINTENANCE_OPERATION_ID', operation)
    with pytest.raises(RuntimeError):
        create_server_application()


def test_maintenance_is_latched_and_blocks_business(monkeypatch):
    import asyncio
    operation = str(uuid4())
    monkeypatch.setenv('ZHIYAN_MAINTENANCE_MODE', '1')
    monkeypatch.setenv('ZHIYAN_MAINTENANCE_OPERATION_ID', operation)
    app = create_server_application()
    monkeypatch.setenv('ZHIYAN_MAINTENANCE_MODE', '0')
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            assert (await client.get('/maintenancez')).json()['operation_id'] == operation
            for path in ['/healthz', '/api/v1/notes', '/legacy/', '/']:
                response = await client.get(path)
                assert response.status_code == 503
                assert response.headers['cache-control'] == 'no-store'
            assert (await client.head('/maintenancez')).content == b''
    asyncio.run(check())


class Backend:
    def __init__(self, root, fail=False):
        self.calls = []
        self.fail = fail
        self.record = dict(app_archive=str(root/'app.tar.gz'), app_sha256='a'*64,
                           qdrant_archive=str(root/'qdrant.tar.gz'), qdrant_sha256='b'*64)
    def verify_context(self, *args): return True
    def stop(self): self.calls.append('stop')
    def verify_stopped(self): return True
    def backup(self): self.calls.append('backup'); return self.record
    def verify_backup(self, record): return record == self.record
    def apply(self):
        self.calls.append('apply')
        if self.fail: raise RuntimeError('private error')
    def restore(self, *args): self.calls.append('restore')
    def verify_data(self, *args): return True
    def start_maintenance(self, *args): self.calls.append('maintenance')
    def verify_maintenance(self, *args): return True
    def release(self, *args): self.calls.append('release')
    def verify_released(self, *args): return 'release' in self.calls
    def clear_marker(self): self.calls.append('clear')


def begin(root):
    operation = str(uuid4())
    baseline = dict(configuration_sha256='c'*64, app_image='sha256:'+'d'*64,
                    qdrant_image='sha256:'+'e'*64, running_services=['app', 'qdrant'])
    begin_journal(root, operation, baseline)
    return operation


def test_terminal_recovery_never_reapplies_or_restores_data(tmp_path):
    operation = begin(tmp_path)
    backend = Backend(tmp_path)
    assert execute_maintenance(tmp_path, operation, backend).phase == 'complete'
    assert backend.calls == ['stop', 'backup', 'apply', 'maintenance', 'release', 'clear']
    backend.calls.clear()
    assert execute_maintenance(tmp_path, operation, backend, recover=True).phase == 'complete'
    assert backend.calls == ['release', 'clear']


def test_apply_failure_keeps_marker_and_requires_explicit_recovery(tmp_path):
    operation = begin(tmp_path)
    backend = Backend(tmp_path, fail=True)
    with pytest.raises(CoordinatorError, match='MAINTENANCE_REQUIRES_RECOVERY'):
        execute_maintenance(tmp_path, operation, backend)
    assert 'clear' not in backend.calls and 'release' not in backend.calls
    assert read_journal(tmp_path, operation).phase == 'applying'
    assert execute_maintenance(tmp_path, operation, backend, recover=True).phase == 'recovered'
    assert backend.calls.count('restore') == 1


def test_corrupt_journal_is_rejected_before_backend_actions(tmp_path):
    operation = begin(tmp_path)
    (tmp_path/'learning-maintenance'/operation/'0001.json').write_text('{')
    with pytest.raises(JournalError): read_journal(tmp_path, operation)
    backend = Backend(tmp_path)
    with pytest.raises(CoordinatorError): execute_maintenance(tmp_path, operation, backend)
    assert backend.calls == []
