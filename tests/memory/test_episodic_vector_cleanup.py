from hello_agents.memory.base import Episode
from hello_agents.memory.storage.vector_store import InMemoryVectorStore
from hello_agents.memory.types.episodic import EpisodicMemory


class RecordingDocumentStore:
    def __init__(self, rows=None, fail_on_delete=None):
        self.deleted_ids = []
        self.rows = dict(rows or {})
        self.fail_on_delete = fail_on_delete

    def get_document(self, doc_id):
        row = self.rows.get(doc_id)
        return dict(row) if row is not None else None

    def add_document(self, doc_id, content, metadata="{}"):
        self.rows[doc_id] = {
            "id": doc_id,
            "content": content,
            "metadata": metadata,
        }

    def delete_document(self, doc_id):
        if doc_id == self.fail_on_delete:
            raise RuntimeError("document delete failed")
        self.deleted_ids.append(doc_id)
        self.rows.pop(doc_id, None)


class FailingVectorStore(InMemoryVectorStore):
    def __init__(self, collection_name="episodic"):
        super().__init__(collection_name=collection_name)
        self.delete_calls = []

    def delete_by_filter(self, collection_name, filters):
        self.delete_calls.append((collection_name, dict(filters)))
        raise RuntimeError("vector delete failed")


def _row(episode):
    return {
        "id": episode.episode_id,
        "content": episode.content,
        "metadata": "{}",
    }


def _memory_with_episodes(store, doc_store):
    memory = EpisodicMemory.__new__(EpisodicMemory)
    memory.vector_store = store
    memory.vector_collection = "episodic"
    memory.doc_store = doc_store
    low = Episode(
        episode_id="low",
        session_id="session",
        timestamp="2026-06-30T00:00:00",
        content="low",
        context={"importance": 0.1},
    )
    high = Episode(
        episode_id="high",
        session_id="session",
        timestamp="2026-06-30T00:00:00",
        content="high",
        context={"importance": 0.9},
    )
    memory._episodes = {low.episode_id: low, high.episode_id: high}
    memory.sessions = {"session": [low.episode_id, high.episode_id]}
    return memory, low, high


def test_episodic_forget_and_clear_remove_vector_entries():
    store = InMemoryVectorStore(collection_name="episodic")
    memory, low, high = _memory_with_episodes(store, RecordingDocumentStore())
    memory.doc_store.rows = {
        low.episode_id: _row(low),
        high.episode_id: _row(high),
    }
    store.add_vectors(
        vectors=[[1.0], [2.0], [3.0]],
        metadata=[
            {"memory_type": "episodic"},
            {"memory_type": "episodic"},
            {"memory_type": "semantic"},
        ],
        ids=[low.episode_id, high.episode_id, "semantic"],
    )

    assert memory.forget(threshold=0.5) == 1
    assert set(store.vectors) == {high.episode_id, "semantic"}
    assert memory.doc_store.deleted_ids == [low.episode_id]
    assert set(memory.doc_store.rows) == {high.episode_id}

    memory.clear()
    assert set(store.vectors) == {"semantic"}
    assert memory.doc_store.deleted_ids == [low.episode_id, high.episode_id]
    assert memory.doc_store.rows == {}


def test_forget_restores_sqlite_and_memory_state_when_vector_delete_fails():
    store = FailingVectorStore()
    memory, low, high = _memory_with_episodes(store, RecordingDocumentStore())
    memory.doc_store.rows = {
        low.episode_id: _row(low),
        high.episode_id: _row(high),
    }
    store.add_vectors(
        vectors=[[1.0], [2.0]],
        metadata=[{"memory_type": "episodic"}, {"memory_type": "episodic"}],
        ids=[low.episode_id, high.episode_id],
    )

    try:
        memory.forget(threshold=0.5)
    except RuntimeError as error:
        assert str(error) == "vector delete failed"
    else:
        raise AssertionError("forget should propagate vector deletion failure")

    assert set(memory.doc_store.rows) == {low.episode_id, high.episode_id}
    assert set(memory._episodes) == {low.episode_id, high.episode_id}
    assert memory.sessions == {"session": [low.episode_id, high.episode_id]}
    assert set(store.vectors) == {low.episode_id, high.episode_id}


def test_clear_restores_prior_sqlite_deletes_when_document_delete_fails():
    store = InMemoryVectorStore(collection_name="episodic")
    doc_store = RecordingDocumentStore(fail_on_delete="high")
    memory, low, high = _memory_with_episodes(store, doc_store)
    doc_store.rows = {
        low.episode_id: _row(low),
        high.episode_id: _row(high),
    }
    store.add_vectors(
        vectors=[[1.0], [2.0]],
        metadata=[{"memory_type": "episodic"}, {"memory_type": "episodic"}],
        ids=[low.episode_id, high.episode_id],
    )

    try:
        memory.clear()
    except RuntimeError as error:
        assert str(error) == "document delete failed"
    else:
        raise AssertionError("clear should propagate document deletion failure")

    assert set(doc_store.rows) == {low.episode_id, high.episode_id}
    assert set(memory._episodes) == {low.episode_id, high.episode_id}
    assert memory.sessions == {"session": [low.episode_id, high.episode_id]}
    assert set(store.vectors) == {low.episode_id, high.episode_id}
