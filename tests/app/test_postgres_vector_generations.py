from dataclasses import replace

import pytest

from app.postgres_vector_generations import VectorScope, _check_revision
from hello_agents.memory.rag.embedding_profile import EmbeddingProfile
from hello_agents.memory.rag.index_identity import IndexIdentity


def identity(model='SimpleEmbedding'):
    return IndexIdentity('qdrant', 'docs', EmbeddingProfile('simple', '', model, 'v1', 4))


def test_scope_key_covers_full_profile_kind_namespace_and_tenant():
    base = VectorScope('tenant-a', 'rag', 'documents', identity())
    assert base.index_key != replace(base, identity=identity('OtherModel')).index_key
    assert len({base.key, replace(base, tenant_id='tenant-b').key,
                replace(base, namespace='episodes').key,
                replace(base, vector_kind='episode').key}) == 4


def test_scope_and_expected_revision_reject_ambiguous_inputs():
    for kind in ('', 'other'):
        with pytest.raises(ValueError):
            VectorScope('tenant', kind, 'ns', identity())
    with pytest.raises(ValueError):
        VectorScope('tenant', 'rag', '', identity())
    with pytest.raises(ValueError):
        VectorScope('tenant', 'rag', 'ns', IndexIdentity('json', 'docs', identity().profile))
    for revision in (0, -1, True, 1.5, '1'):
        with pytest.raises(ValueError):
            _check_revision(revision)
