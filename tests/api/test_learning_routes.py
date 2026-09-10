from types import SimpleNamespace
from uuid import uuid4
import sqlite3

import pytest
from fastapi.testclient import TestClient

from api.app import create_api_app
from app.bootstrap import ApplicationServices
from app.learning_models import LearningMigrationRequired


@pytest.fixture
def host(tmp_path, monkeypatch):
    services = ApplicationServices.create(tmp_path / 'data')
    monkeypatch.setattr(services.document_library, 'list_documents', lambda _: (
        SimpleNamespace(document_id='doc', name='资料', status='ready'),))
    with TestClient(create_api_app(services), raise_server_exceptions=False) as client:
        yield services, client


def login(client, name='alice'):
    response = client.post('/api/v1/auth/register', json={'username':name,'password':'correct horse battery'})
    assert response.status_code == 200
    return {'X-CSRF-Token':response.json()['csrf_token']}


def body():
    return dict(request_id=str(uuid4()),document_id='doc',title='学习',days=2,daily_minutes=30,timezone='Asia/Shanghai')


def checked(response, status):
    assert response.status_code == status, response.text
    assert response.headers['cache-control'] == 'no-store'
    return response.json()


def test_real_api_closed_loop(host):
    _, client = host
    checked(client.get('/api/v1/learning/plans'),401)
    headers = login(client)
    cmd = body()
    checked(client.post('/api/v1/learning/plans',json=cmd),403)
    created = checked(client.post('/api/v1/learning/plans',json=cmd,headers=headers),201)
    plan = created['plan']
    assert checked(client.post('/api/v1/learning/plans',json=cmd,headers=headers),201)['replayed']
    assert checked(client.get('/api/v1/learning/plans'),200)['items'][0]['id'] == plan['id']
    checked(client.get('/api/v1/learning/plans/'+plan['id']),200)
    tasks = checked(client.get('/api/v1/learning/plans/'+plan['id']+'/tasks?limit=1'),200)
    assert tasks['next_cursor']
    task = tasks['items'][0]
    update = dict(request_id=str(uuid4()),expected_version=task['version'],completed=True)
    updated = checked(client.patch('/api/v1/learning/tasks/'+task['id'],json=update,headers=headers),200)
    assert updated['task']['completed']
    assert checked(client.get('/api/v1/learning/today?bucket=completed'),200)['items']
    checked(client.patch('/api/v1/learning/tasks/'+task['id'],json=update|{'request_id':str(uuid4())},headers=headers),409)
    checked(client.patch('/api/v1/learning/tasks/'+task['id'],json=update|{'request_id':str(uuid4()),'expected_version':2,'completed':False},headers=headers),200)
    login(client,'bob')
    checked(client.get('/api/v1/learning/plans/'+plan['id']),404)


@pytest.mark.parametrize('changes',[{'days':True},{'daily_minutes':True},{'title':' '},{'user_id':'other'},{'request_id':'bad'}])
def test_strict_validation(host, changes):
    _, client = host
    headers = login(client)
    result = checked(client.post('/api/v1/learning/plans',json=body()|changes,headers=headers),422)
    assert result['error']['code'] == 'LEARNING_VALIDATION_ERROR'


@pytest.mark.parametrize('error,status,code',[(LearningMigrationRequired,409,'LEARNING_MIGRATION_REQUIRED'),
                                             (sqlite3.OperationalError,503,'LEARNING_UNAVAILABLE'),
                                             (RuntimeError,500,'internal_error')])
def test_safe_errors(host, monkeypatch, error, status, code):
    services, client = host
    login(client)
    def fail(*args, **kwargs):
        raise error('C:/private sk-secret')
    monkeypatch.setattr(services.learning_service,'list_plans',fail)
    result = checked(client.get('/api/v1/learning/plans'),status)
    assert result['error']['code'] == code
    assert 'private' not in str(result) and 'sk-secret' not in str(result)


def test_openapi_and_query_errors(host):
    _, client = host
    login(client)
    checked(client.get('/api/v1/learning/today?bucket=bad'),422)
    checked(client.get('/api/v1/learning/plans?limit=51'),422)
    checked(client.get('/api/v1/learning/unknown'),404)
    paths = client.get('/openapi.json').json()['paths']
    assert '/api/v1/learning/plans/{plan_id}/tasks' in paths
