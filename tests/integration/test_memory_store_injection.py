import json

import pytest

from app.postgres_memory_documents import PostgresMemoryDocumentStore
from hello_agents.memory.base import MemoryConfig
from hello_agents.memory.storage.vector_store import InMemoryVectorStore
from hello_agents.tools.builtin.memory_tool import MemoryTool
from tests.integration.test_postgres_memory_documents import documents, shared_database


class Embedder:
    def encode(self, text):
        return [1.0, 0.0]


@pytest.fixture
def backends(monkeypatch, tmp_path):
    import hello_agents.memory.types.episodic as module
    vector = InMemoryVectorStore(collection_name='episodes')
    monkeypatch.setattr(module, 'create_embedding_model_with_fallback', lambda: Embedder())
    monkeypatch.setattr(module.QdrantConnectionManager, 'get_instance', lambda **kwargs: vector)
    def forbidden(*args, **kwargs):
        pytest.fail('injected memory must not construct SQLite')
    monkeypatch.setattr(module, 'SQLiteDocumentStore', forbidden)
    config = MemoryConfig(database_path=str(tmp_path / 'forbidden.db'),
                          qdrant_collection='episodes', qdrant_vector_size=2)
    return config, vector


def test_public_memory_tool_uses_shared_document_store(documents, backends):
    first, second, (user, other) = documents
    config, _ = backends
    store = PostgresMemoryDocumentStore(first, user)
    peer = PostgresMemoryDocumentStore(second, user)
    tool = MemoryTool(user_id=user, memory_config=config, memory_types=['episodic'],
                      episodic_document_store=store)
    try:
        identifier = tool.memory_manager.add_memory('共享事件', 'episodic', memory_id='episode-1')
        row = peer.get_document(identifier)
        assert row['content'] == '共享事件'
        assert json.loads(row['metadata'])['user_id'] == user
        assert PostgresMemoryDocumentStore(second, other).get_document(identifier) is None
        assert tool.memory_manager.remove_memory(identifier, memory_type='episodic')
        assert peer.get_document(identifier) is None
    finally:
        tool.close()
    assert first.ping()
    with pytest.raises(RuntimeError, match='closed'):
        store.get_document('episode-1')


@pytest.mark.parametrize('failure', ['document', 'vector'])
def test_injected_persistence_failure_does_not_publish_resident_state(backends, monkeypatch, failure):
    config, vector = backends
    class Store:
        def add_document(self, **kwargs):
            if failure == 'document':
                raise RuntimeError('document unavailable')
        def close(self):
            pass
    class Snapshots:
        saves = 0
        def restore_to_manager(self, manager):
            pass
        def save_from_manager(self, manager):
            self.saves += 1
    snapshots = Snapshots()
    if failure == 'vector':
        def fail(*args, **kwargs):
            raise RuntimeError('vector unavailable')
        monkeypatch.setattr(vector, 'upsert', fail)
    tool = MemoryTool(user_id='owner', memory_config=config, memory_types=['episodic'],
                      memory_repository=snapshots, episodic_document_store=Store())
    try:
        with pytest.raises(RuntimeError, match=failure + ' unavailable'):
            tool.memory_manager.add_memory('event', 'episodic', memory_id='episode-1')
        module = tool.memory_manager.memory_types['episodic']
        assert module._episodes == {}
        assert module.sessions == {}
        assert snapshots.saves == 0
        assert not vector.vectors
    finally:
        tool.close()
