"""Opt-in frozen-pair migration against disposable PG and separate Qdrant."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

import boto3
from alembic import command
from alembic.config import Config
from botocore.config import Config as BotoConfig
import psycopg
from psycopg import sql
import pytest
from sqlalchemy.engine import make_url

from app.object_store import S3ObjectStore
from app.postgres_coordination import PostgresUserMutationCoordinator
from app.postgres_vector_generations import PostgresVectorGenerationAuthority, VectorHead
from app.vector_generation_service import VectorGenerationService, VectorPublicationUnknown
from hello_agents.memory.storage.generation_vector_store import CandidateGenerationWriter
from hello_agents.memory.storage.vector_store import QdrantVectorStore
from deploy.inventory_paired_backup import inventory_pair
from deploy.migrate_relational_isolated import migrate_relational
from deploy.migrate_structured_isolated import migrate_structured
from deploy.migrate_report_refs_isolated import migrate_report_refs
from deploy.migrate_document_refs_isolated import migrate_document_refs
from deploy.migrate_vectors_isolated import (
    _controller_lock, _endpoint_key, migrate_frozen_vectors, read_frozen_source,
)


def _create_owned_target(source_url, target_url, target, identity):
    if _endpoint_key(source_url) == _endpoint_key(target_url):
        raise ValueError('Test source and target Qdrant endpoints are identical')
    if target.client.collection_exists(identity.physical_collection):
        raise ValueError('Test target collection already exists; refuse ownership')
    target.ensure_collection(identity.physical_collection,
                             identity.profile.dimension, identity.profile.distance)


def test_preexisting_target_collection_is_never_changed_or_deleted():
    class Client:
        writes = []

        def collection_exists(self, _):
            return True

        def delete_collection(self, name):
            self.writes.append(name)

    class Target:
        client = Client()

        def ensure_collection(self, *args):
            self.client.writes.append(args)

    target = Target()
    with pytest.raises(ValueError, match='already exists'):
        _create_owned_target('http://localhost:51528', 'http://127.0.0.1:63730',
                             target, _test_identity())
    assert target.client.writes == []


def test_loopback_alias_to_source_is_rejected_before_target_inspection():
    class Client:
        def collection_exists(self, _):
            raise AssertionError('Source collection must not be inspected as target')

    class Target:
        client = Client()

    with pytest.raises(ValueError, match='identical'):
        _create_owned_target('http://localhost:51528', 'http://127.0.0.1:51528',
                             Target(), _test_identity())


def _test_identity():
    from hello_agents.memory.rag.embedding_profile import EmbeddingProfile
    from hello_agents.memory.rag.index_identity import IndexIdentity
    return IndexIdentity('qdrant', 'example',
                         EmbeddingProfile('simple', '', 'deterministic', 'v1', 4))


def test_one_controller_lock_rejects_a_second_session():
    base_url = os.environ.get('POSTGRES_TEST_URL')
    if not base_url:
        pytest.skip('POSTGRES_TEST_URL required')
    schema = 'cutover_lock_test_' + uuid4().hex[:12]
    with _controller_lock(base_url, schema):
        with pytest.raises(ValueError, match='Another vector migration controller'):
            with _controller_lock(base_url, schema):
                pass
    with _controller_lock(base_url, schema):
        pass


@pytest.mark.parametrize('fault', [None, 'head_after_preflight',
                                   'baseline_after_stage',
                                   'manifest_after_upload', 'archive_after_upload',
                                   'unknown_upload', 'unknown_abandonment',
                                   'publish_response_lost', 'release_response_lost'])
def test_frozen_pair_publishes_once_and_rejects_changed_target(monkeypatch, tmp_path, fault):
    names = ('PAIRED_VECTOR_APP_ARCHIVE', 'PAIRED_VECTOR_INVENTORY',
             'PAIRED_VECTOR_INVENTORY_SHA256', 'PAIRED_VECTOR_SOURCE_URL',
             'PAIRED_VECTOR_TARGET_URL', 'PAIRED_VECTOR_FILE_MANIFEST',
             'POSTGRES_TEST_URL', 'S3_TEST_ENDPOINT', 'S3_TEST_ACCESS_KEY',
             'S3_TEST_SECRET_KEY')
    if any(not os.environ.get(name) for name in names):
        pytest.skip('Frozen-pair, PG, separate Qdrant and S3 test values required')
    archive = Path(os.environ['PAIRED_VECTOR_APP_ARCHIVE'])
    inventory_path = Path(os.environ['PAIRED_VECTOR_INVENTORY'])
    manifest_path = Path(os.environ['PAIRED_VECTOR_FILE_MANIFEST'])
    source_url = os.environ['PAIRED_VECTOR_SOURCE_URL']
    target_url = os.environ['PAIRED_VECTOR_TARGET_URL']
    base_url = os.environ['POSTGRES_TEST_URL']
    schema = 'cutover_vector_test_' + uuid4().hex[:12]
    original = read_frozen_source(archive, inventory_path,
                                  os.environ['PAIRED_VECTOR_INVENTORY_SHA256'],
                                  source_url)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    s3 = boto3.client('s3', endpoint_url=os.environ['S3_TEST_ENDPOINT'],
                      region_name='us-east-1',
                      aws_access_key_id=os.environ['S3_TEST_ACCESS_KEY'],
                      aws_secret_access_key=os.environ['S3_TEST_SECRET_KEY'],
                      config=BotoConfig(s3={'addressing_style': 'path'}))
    store = S3ObjectStore(s3, manifest['target']['bucket'])
    target = QdrantVectorStore(url=target_url, retry_delays=())
    _create_owned_target(source_url, target_url, target, original.identity)
    created_collection = True
    with psycopg.connect(base_url, autocommit=True) as admin:
        admin.execute(sql.SQL('create schema {}').format(sql.Identifier(schema)))
    database_url = make_url(base_url).set(drivername='postgresql+psycopg',
                                      query={'options': f'-csearch_path={schema}'})
    database_url = database_url.render_as_string(hide_password=False)
    sqlite_path = tmp_path / 'source.db'
    from tarfile import open as open_tar
    with open_tar(archive, 'r:gz') as frozen:
        sqlite_path.write_bytes(frozen.extractfile('./app/app.db').read())
    previous = os.environ.get('DATABASE_URL')
    monkeypatch.setenv('DATABASE_URL', database_url)
    try:
        command.upgrade(Config('alembic.ini'), '20260926_01')
        pair = inventory_pair(archive)
        migrate_relational(sqlite_path, expected_sha256=pair['sqlite_sha256'],
                           database_url=base_url, target_schema=schema, mode='apply')
        command.upgrade(Config('alembic.ini'), '20260927_06')
        migrate_structured(archive, database_url=base_url,
                           target_schema=schema, mode='apply')
        migrate_report_refs(archive, manifest_path, store, database_url=base_url,
                            target_schema=schema, mode='apply')
        command.upgrade(Config('alembic.ini'), '20260929_12')
        migrate_document_refs(archive, manifest_path, store, database_url=base_url,
                              target_schema=schema, mode='apply', require_report_refs=True)
        migration_manifest = manifest_path
        migration_archive = archive
        if fault == 'manifest_after_upload':
            migration_manifest = tmp_path / 'manifest-copy.json'
            shutil.copyfile(manifest_path, migration_manifest)
        if fault == 'archive_after_upload':
            migration_archive = tmp_path / archive.name
            shutil.copyfile(archive, migration_archive)
            shutil.copyfile(Path(str(archive) + '.meta'),
                            Path(str(migration_archive) + '.meta'))
            paired = json.loads(Path(str(archive) + '.meta').read_text(encoding='utf-8'))
            shutil.copyfile(archive.parent / paired['qdrant_archive'],
                            tmp_path / paired['qdrant_archive'])
        args = (migration_archive, inventory_path, os.environ['PAIRED_VECTOR_INVENTORY_SHA256'],
                migration_manifest, store, source_url, target)
        kwargs = {'database_url': base_url, 'target_schema': schema}
        assert migrate_frozen_vectors(*args, mode='dry-run', **kwargs)['status'] == 'ready'
        if fault == 'head_after_preflight':
            monkeypatch.setattr(PostgresVectorGenerationAuthority, 'read_head',
                                lambda *_: VectorHead('published', 1, uuid4(), 1, 1))
            with pytest.raises(ValueError, match='authority changed after preflight'):
                migrate_frozen_vectors(*args, mode='apply', **kwargs)
            with psycopg.connect(base_url) as connection:
                connection.execute(sql.SQL('set search_path to {}').format(sql.Identifier(schema)))
                assert connection.execute('select count(*) from vector_generations').fetchone() == (0,)
                assert connection.execute('select count(*) from vector_heads').fetchone() == (0,)
                assert connection.execute('select count(*) from user_mutation_leases').fetchone() == (0,)
            assert target.count(original.identity.physical_collection) == 0
            return
        if fault == 'baseline_after_stage':
            with psycopg.connect(base_url) as connection:
                connection.execute(sql.SQL('set search_path to {}').format(sql.Identifier(schema)))
                username_before = connection.execute('select username from users where id=%s',
                                                     (original.owner,)).fetchone()
            original_publish = VectorGenerationService.publish_complete

            def changed_baseline(service, scope, owner, expected_head, corpus, *,
                                 domain_publish=None, snapshot_version=None):
                def corrupted(cursor):
                    cursor.execute("update users set username='changed' where id=%s",
                                   (original.owner,))
                    domain_publish(cursor)
                return original_publish(service, scope, owner, expected_head, corpus,
                                        domain_publish=corrupted,
                                        snapshot_version=snapshot_version)

            monkeypatch.setattr(VectorGenerationService, 'publish_complete', changed_baseline)
            with pytest.raises(ValueError, match='baseline'):
                migrate_frozen_vectors(*args, mode='apply', **kwargs)
            with psycopg.connect(base_url) as connection:
                connection.execute(sql.SQL('set search_path to {}').format(sql.Identifier(schema)))
                assert connection.execute('select count(*) from vector_heads').fetchone() == (0,)
                assert connection.execute("select state from vector_generations").fetchall() == [('sealed',)]
                assert connection.execute('select username from users where id=%s',
                                          (original.owner,)).fetchone() == username_before
            return
        if fault in {'manifest_after_upload', 'archive_after_upload'}:
            original_verify = CandidateGenerationWriter.verify

            def changed_input(writer):
                result = original_verify(writer)
                changed_path = (migration_manifest if fault == 'manifest_after_upload'
                                else migration_archive)
                changed_path.write_bytes(b'changed during upload')
                return result

            monkeypatch.setattr(CandidateGenerationWriter, 'verify', changed_input)
            with pytest.raises(ValueError, match='Frozen migration inputs changed'):
                migrate_frozen_vectors(*args, mode='apply', **kwargs)
            with psycopg.connect(base_url) as connection:
                connection.execute(sql.SQL('set search_path to {}').format(sql.Identifier(schema)))
                assert connection.execute('select count(*) from vector_heads').fetchone() == (0,)
                assert connection.execute('select state from vector_generations').fetchall() == [('sealed',)]
            return
        if fault in {'unknown_upload', 'unknown_abandonment'}:
            original_upload = CandidateGenerationWriter.upload

            def unknown_after_write(writer, points):
                original_upload(writer, points)
                raise RuntimeError('simulated lost Qdrant response')

            monkeypatch.setattr(CandidateGenerationWriter, 'upload', unknown_after_write)
            if fault == 'unknown_abandonment':
                monkeypatch.setattr(PostgresVectorGenerationAuthority, 'abandon',
                                    lambda *_: (_ for _ in ()).throw(
                                        RuntimeError('simulated lost abandonment response')))
            error_type = VectorPublicationUnknown if fault == 'unknown_abandonment' else Exception
            error_match = 'unknown abandonment' if fault == 'unknown_abandonment' else 'abandoned'
            with pytest.raises(error_type, match=error_match):
                migrate_frozen_vectors(*args, mode='apply', **kwargs)
            with psycopg.connect(base_url) as connection:
                connection.execute(sql.SQL('set search_path to {}').format(sql.Identifier(schema)))
                assert connection.execute('select count(*) from vector_heads').fetchone() == (0,)
                expected_state = 'staging' if fault == 'unknown_abandonment' else 'abandoned'
                assert connection.execute('select state from vector_generations').fetchall() == [(expected_state,)]
            with pytest.raises(ValueError, match='existing vector or lease authority'):
                migrate_frozen_vectors(*args, mode='apply', **kwargs)
            return
        if fault == 'publish_response_lost':
            original_publish_user = PostgresVectorGenerationAuthority.publish_user

            def lost_pg_response(authority, *args, **kwargs):
                original_publish_user(authority, *args, **kwargs)
                raise RuntimeError('simulated lost PG response after commit')

            monkeypatch.setattr(PostgresVectorGenerationAuthority, 'publish_user',
                                lost_pg_response)
        if fault == 'release_response_lost':
            original_release = PostgresUserMutationCoordinator.release

            def lost_release(coordinator, lease):
                assert original_release(coordinator, lease)
                return False

            monkeypatch.setattr(PostgresUserMutationCoordinator, 'release', lost_release)
            with pytest.raises(ValueError, match='lease release was not confirmed'):
                migrate_frozen_vectors(*args, mode='apply', **kwargs)
            assert migrate_frozen_vectors(*args, mode='verify', **kwargs)['status'] == 'equal'
            return
        applied = migrate_frozen_vectors(*args, mode='apply', **kwargs)
        assert applied['status'] == 'applied'
        assert applied['point_count'] == 5 and applied['document_count'] == 2
        assert migrate_frozen_vectors(*args, mode='apply', **kwargs)['status'] == 'unchanged'
        assert migrate_frozen_vectors(*args, mode='verify', **kwargs)['status'] == 'equal'
        assert read_frozen_source(archive, inventory_path,
                                  os.environ['PAIRED_VECTOR_INVENTORY_SHA256'],
                                  source_url).source_points_sha256 == original.source_points_sha256
        with psycopg.connect(base_url) as connection:
            connection.execute(sql.SQL('set search_path to {}').format(sql.Identifier(schema)))
            connection.execute("update users set username='changed' where id=%s", (original.owner,))
        with pytest.raises(ValueError, match='baseline'):
            migrate_frozen_vectors(*args, mode='verify', **kwargs)
    finally:
        if previous is None:
            monkeypatch.delenv('DATABASE_URL', raising=False)
        else:
            monkeypatch.setenv('DATABASE_URL', previous)
        if created_collection:
            target.client.delete_collection(original.identity.physical_collection)
        with psycopg.connect(base_url, autocommit=True) as admin:
            admin.execute(sql.SQL('drop schema {} cascade').format(sql.Identifier(schema)))
