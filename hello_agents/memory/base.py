from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional
import uuid


@dataclass
class Episode:
    episode_id: str
    session_id: str
    timestamp: str
    content: str
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Entity:
    entity_id: str
    name: str
    entity_type: str = "concept"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Relation:
    source_id: str
    target_id: str
    relation_type: str = "related_to"
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class MemoryConfig:
    database_path: str = "./memory_data/memory.db"

    working_memory_capacity: int = 50
    working_memory_ttl: int = 60

    qdrant_url: Optional[str] = None
    qdrant_api_key: Optional[str] = None
    qdrant_collection: str = "hello_agents_vectors"
    qdrant_vector_size: int = 384

    neo4j_uri: Optional[str] = None
    neo4j_username: str = "neo4j"
    neo4j_password: Optional[str] = None
    neo4j_database: str = "neo4j"


@dataclass
class MemoryItem:
    content: str
    memory_type: str = "working"
    importance: float = 0.5
    metadata: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class BaseMemory:
    def __init__(self, config: MemoryConfig, storage_backend=None):
        self.config = config
        self.storage_backend = storage_backend

    def add(self, memory_item: MemoryItem) -> str:
        raise NotImplementedError

    def retrieve(self, query: str, limit: int = 5, **kwargs):
        raise NotImplementedError