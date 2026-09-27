from uuid import uuid4

import pytest
import psycopg

from app.object_store import S3ObjectStore
from app.postgres_reports import PostgresReportService, ReportPublicationError
from tests.integration.test_postgres_auth_sessions import shared_database
from tests.integration.test_s3_object_store import store


@pytest.fixture
def reports(shared_database, store):
    open_pool, _ = shared_database
    first, second = open_pool(), open_pool()
    users = [str(uuid4()), str(uuid4())]
    with first.transaction() as cursor:
        for user in users:
            cursor.execute('insert into users values(%s,%s,%s,%s,%s,%s,%s)',
                           (user, user, user, 'unused', 'active', 'now', 'now'))
    yield [PostgresReportService(first, store), PostgresReportService(second, store)], users, first, store


def test_report_persistence_tenant_scope_and_pinned_version(reports):
    services, (user, other), first, store = reports
    content = '# 报告\r\n\n保留 UTF-8、换行和来源 [1]。\n'
    report = services[0].create_markdown_snapshot(user, '学习报告', content)
    assert report.relative_path == f'reports/{report.id}.md'
    assert services[1].list_reports(user) == [report]
    assert services[1].list_reports(other) == []
    with pytest.raises(FileNotFoundError):
        services[1].read_report(other, report.id)
    with first.transaction() as cursor:
        row = cursor.execute('select * from report_objects where report_id=%s', (report.id,)).fetchone()
    store.client.put_object(Bucket=store.bucket, Key=row['object_key'], Body=b'unpublished')
    store.client.delete_object(Bucket=store.bucket, Key=row['object_key'])
    first.close()
    assert services[1].read_report(user, report.id) == content
    assert services[1].read_report_bytes(user, report.id) == content.encode('utf-8')


def test_failed_publication_leaves_no_visible_report_but_retains_staged_object(reports):
    services, (user, _), first, store = reports
    with first.transaction() as cursor:
        cursor.execute('''create function reject_report_object() returns trigger language plpgsql as $$
            begin raise exception 'injected publication failure'; end; $$''')
        cursor.execute('create trigger reject_report before insert on report_objects for each row execute function reject_report_object()')
    with pytest.raises(psycopg.errors.RaiseException, match='injected publication failure'):
        services[0].create_markdown_snapshot(user, 'title', 'staged content')
    assert services[1].list_reports(user) == []
    with first.transaction() as cursor:
        assert cursor.execute('select count(*) as n from report_objects').fetchone()['n'] == 0
    versions = store.client.list_object_versions(Bucket=store.bucket)['Versions']
    assert len(versions) == 1
    response = store.client.get_object(Bucket=store.bucket, Key=versions[0]['Key'], VersionId=versions[0]['VersionId'])
    try:
        assert response['Body'].read() == b'staged content'
    finally:
        response['Body'].close()


def test_incomplete_migration_and_bucket_change_fail_closed(reports):
    services, (user, other), first, store = reports
    report = services[0].create_markdown_snapshot(user, 'title', 'content')
    wrong_bucket = PostgresReportService(first, S3ObjectStore(store.client, 'wrong-bucket'))
    with pytest.raises(ReportPublicationError, match='bucket'):
        wrong_bucket.read_report(user, report.id)
    missing_id = str(uuid4())
    with first.transaction() as cursor:
        cursor.execute('insert into report_records values(%s,%s,%s,%s,%s)',
                       (missing_id, other, 'legacy', 'reports/legacy.md', '2026-01-01'))
    with pytest.raises(ReportPublicationError, match='reference'):
        services[0].list_reports(other)
    with pytest.raises(ReportPublicationError, match='reference'):
        services[0].read_report(other, missing_id)


def test_missing_user_cannot_stage_report(reports):
    services, _users, _first, store = reports
    with pytest.raises(FileNotFoundError):
        services[0].create_markdown_snapshot(str(uuid4()), 'title', 'content')
    assert store.client.list_object_versions(Bucket=store.bucket).get('Versions', []) == []


def test_reference_foreign_key_rejects_cross_user_binding(reports):
    services, (user, other), first, _store = reports
    report = services[0].create_markdown_snapshot(user, 'title', 'content')
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with first.transaction() as cursor:
            cursor.execute('update report_objects set user_id=%s where report_id=%s', (other, report.id))
    assert services[1].read_report(user, report.id) == 'content'


def test_upload_failure_does_not_publish_metadata(reports, monkeypatch):
    services, (user, _other), _first, store = reports

    def fail(*_args):
        raise TimeoutError('injected storage timeout')

    monkeypatch.setattr(store, 'put_immutable', fail)
    with pytest.raises(TimeoutError, match='injected storage timeout'):
        services[0].create_markdown_snapshot(user, 'title', 'content')
    assert services[1].list_reports(user) == []
