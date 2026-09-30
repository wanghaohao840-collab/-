"""Frozen legacy vectors keep their application IDs and source metadata."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hello_agents.memory.rag.embedding_profile import EmbeddingProfile
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.storage.vector_store import QdrantVectorStore
from app.postgres_vector_generations import VectorHead

import deploy.migrate_vectors_isolated as migration
from deploy.migrate_vectors_isolated import _source_point, _verify_published, read_frozen_source


def identity():
    return IndexIdentity('qdrant', 'example',
                         EmbeddingProfile('simple', '', 'deterministic', 'v1', 4))


def point(owner, document_id, registered):
    logical = str(uuid4())
    return {
        'id': logical,
        'vector': [1.0, 0.0, 0.0, 0.0],
        'payload': {
            '_vector_store_id': logical,
            'rag_namespace': f'pdf_{owner}',
            'document_id': document_id,
            'chunk_index': 0,
            'content': 'source text',
            'metadata': {'user_id': owner,
                         'embedding_fingerprint': registered.profile.fingerprint,
                         'page_number': 3},
        },
    }


def test_empty_published_head_verifies_without_physical_points():
    head = VectorHead('empty', 1, None, 1, 7)
    collection = identity().physical_collection
    view = SimpleNamespace(scroll=lambda name, with_vectors: [] if name == collection else None)
    raw = SimpleNamespace(count=lambda name: 0 if name == collection else None)
    service = SimpleNamespace(authority=SimpleNamespace(read_head=lambda _: head),
                              read_view=lambda _: view, raw=raw)
    frozen = SimpleNamespace(points=())
    assert _verify_published(service, SimpleNamespace(identity=identity()), frozen, 7) == head


def test_source_point_preserves_logical_id_and_citation_payload():
    owner, document_id, registered = str(uuid4()), str(uuid4()), identity()
    source = point(owner, document_id, registered)
    converted = _source_point(source, owner, {document_id}, registered)
    assert converted.id == source['payload']['_vector_store_id']
    assert converted.vector == source['vector']
    assert converted.payload == {k: v for k, v in source['payload'].items()
                                 if k != '_vector_store_id'}


@pytest.mark.parametrize('mutate', [
    lambda p: p.update(id=str(uuid4())),
    lambda p: p['payload'].update(rag_namespace='pdf_wrong'),
    lambda p: p['payload']['metadata'].update(user_id=str(uuid4())),
    lambda p: p['payload']['metadata'].update(embedding_fingerprint='wrong'),
    lambda p: p['payload'].update(_gv_generation='forged'),
    lambda p: p['vector'].append(0.0),
])
def test_source_point_rejects_identity_scope_and_vector_mismatch(mutate):
    owner, document_id, registered = str(uuid4()), str(uuid4()), identity()
    source = point(owner, document_id, registered)
    mutate(source)
    with pytest.raises(ValueError):
        _source_point(source, owner, {document_id}, registered)


def test_real_frozen_pair_scan_matches_point_and_document_inventory():
    archive = os.environ.get('PAIRED_VECTOR_APP_ARCHIVE')
    inventory = os.environ.get('PAIRED_VECTOR_INVENTORY')
    digest = os.environ.get('PAIRED_VECTOR_INVENTORY_SHA256')
    source_url = os.environ.get('PAIRED_VECTOR_SOURCE_URL')
    if not all((archive, inventory, digest, source_url)):
        pytest.skip('Explicit frozen-pair paths, inventory digest and source URL required')
    frozen = read_frozen_source(Path(archive), Path(inventory), digest, source_url)
    assert len(frozen.points) == 5
    assert {point.payload['document_id'] for point in frozen.points} == frozen.document_ids
    assert len(frozen.document_ids) == 2
    assert len({point.id for point in frozen.points}) == 5
    assert frozen.identity.profile.dimension == 1024


class _Response:
    def __init__(self, value):
        self.value = value

    def raise_for_status(self):
        pass

    def json(self):
        return {'result': self.value}


@pytest.mark.parametrize('changed', ['app', 'qdrant', 'inventory', 'page'])
def test_frozen_input_or_pagination_change_during_scan_fails_closed(monkeypatch, tmp_path, changed):
    registered = identity()
    name = registered.physical_collection
    app = tmp_path / 'pair.tar.gz'
    qdrant = tmp_path / 'pair.qdrant.tar.gz'
    app.write_bytes(b'app before scan')
    qdrant.write_bytes(b'qdrant before scan')
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    saved = {'status': 'passed', 'source_qdrant_archive_sha256': sha(qdrant),
             'source_documents': 0, 'matched_documents': 0,
             'collections': [{'collection': name, 'point_count': 0,
                              'config': {'size': 4, 'distance': 'Cosine'},
                              'points_sha256': migration._digest([]), 'documents': []}]}
    inventory = tmp_path / 'inventory.json'
    inventory.write_text(json.dumps(saved), encoding='utf-8')
    initial_app, initial_qdrant = sha(app), sha(qdrant)
    monkeypatch.setattr(migration, '_source', lambda _:
                        ({'source_sha256': initial_app,
                          'qdrant_sha256': initial_qdrant,
                          'qdrant_archive': qdrant.name,
                          'qdrant_collections': [name]}, None, None, None))
    monkeypatch.setattr(migration, '_active_identity', lambda _: registered)
    monkeypatch.setattr(migration, '_visible_documents', lambda *_: ('owner', set()))

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, path):
            if path == '/collections':
                if changed != 'page':
                    {'app': app, 'qdrant': qdrant,
                     'inventory': inventory}[changed].write_bytes(b'changed')
                return _Response({'collections': [{'name': name}]})
            return _Response({'config': {'params': {'vectors':
                              {'size': 4, 'distance': 'Cosine'}}}})

        def post(self, path, json):
            if path.endswith('/count'):
                return _Response({'count': 0})
            return _Response({'points': [],
                              'next_page_offset': 'repeat' if changed == 'page' else None})

    monkeypatch.setattr(migration.httpx, 'Client', Client)
    with pytest.raises(ValueError, match='changed during scan|cursor repeats'):
        read_frozen_source(app, inventory, sha(inventory), 'http://source')
