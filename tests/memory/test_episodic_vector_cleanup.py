from hello_agents.memory.base import Episode
from hello_agents.memory.storage.vector_store import InMemoryVectorStore
from hello_agents.memory.types.episodic import EpisodicMemory


def test_episodic_forget_and_clear_remove_vector_entries():
    store = InMemoryVectorStore(collection_name="episodic")
    memory = EpisodicMemory.__new__(EpisodicMemory)
    memory.vector_store = store
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
    store.add_vectors(
        vectors=[[1.0], [2.0]],
        metadata=[{}, {}],
        ids=[low.episode_id, high.episode_id],
    )

    assert memory.forget(threshold=0.5) == 1
    assert set(store.vectors) == {high.episode_id}

    memory.clear()
    assert store.vectors == {}
