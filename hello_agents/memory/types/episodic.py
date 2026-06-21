from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from hello_agents.memory.base import MemoryConfig, MemoryItem, Episode
from hello_agents.memory.embedding import create_embedding_model_with_fallback
from hello_agents.memory.storage.document_store import SQLiteDocumentStore
from hello_agents.memory.storage.qdrant_store import QdrantVectorStore


class EpisodicMemory:
    """情景记忆实现

    特点：
    - SQLite + Qdrant 混合存储
    - SQLite 保存结构化内容和上下文
    - Qdrant 保存向量，支持语义检索
    - 支持 session_id、时间范围、重要性过滤
    """

    def __init__(self, config: MemoryConfig, storage_backend=None):
        self.config = config
        self.storage_backend = storage_backend

        self.doc_store = SQLiteDocumentStore(config.database_path)

        self.vector_store = QdrantVectorStore(
            qdrant_url=getattr(config, "qdrant_url", None),
            qdrant_api_key=getattr(config, "qdrant_api_key", None),
            collection_name=getattr(config, "qdrant_collection", "hello_agents_vectors")
        )

        self.embedder = create_embedding_model_with_fallback()

        self.sessions: Dict[str, List[str]] = {}
        self._episodes: Dict[str, Episode] = {}

    def add(self, memory_item: MemoryItem) -> str:
        """添加情景记忆"""

        episode = Episode(
            episode_id=memory_item.id,
            session_id=memory_item.metadata.get("session_id", "default"),
            timestamp=memory_item.timestamp,
            content=memory_item.content,
            context={
                **memory_item.metadata,
                "memory_type": "episodic",
                "importance": memory_item.importance,
                "content": memory_item.content
            }
        )

        if episode.session_id not in self.sessions:
            self.sessions[episode.session_id] = []

        self.sessions[episode.session_id].append(episode.episode_id)
        self._episodes[episode.episode_id] = episode

        self._persist_episode(episode)
        return memory_item.id

    def retrieve(self, query: str, limit: int = 5, **kwargs) -> List[MemoryItem]:
        """混合检索：结构化过滤 + 语义向量检索"""

        if not query:
            return []

        candidate_ids = self._structured_filter(**kwargs)

        hits = self._vector_search(
            query=query,
            limit=max(limit * 5, 20),
            user_id=kwargs.get("user_id")
        )

        results = []

        for hit in hits:
            if self._should_include(hit, candidate_ids, kwargs):
                score = self._calculate_episode_score(hit)
                memory_item = self._create_memory_item(hit)
                results.append((score, memory_item))

        results.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in results[:limit]]

    def forget(
        self,
        strategy: str = "importance_based",
        threshold: float = 0.1,
        max_age_days: int = 30
    ) -> int:
        """遗忘情景记忆"""

        before_count = len(self._episodes)
        keep: Dict[str, Episode] = {}

        for episode_id, episode in self._episodes.items():
            context = episode.context or {}
            importance = float(context.get("importance", 0.5))

            should_forget = False

            if strategy == "importance_based":
                should_forget = importance < threshold

            elif strategy == "time_based":
                age_days = self._calculate_age_days(episode.timestamp)
                should_forget = age_days > max_age_days

            elif strategy == "capacity_based":
                should_forget = importance < threshold

            if not should_forget:
                keep[episode_id] = episode

        self._episodes = keep

        self.sessions = {}
        for episode in self._episodes.values():
            self.sessions.setdefault(episode.session_id, []).append(episode.episode_id)

        return before_count - len(self._episodes)

    def clear(self) -> None:
        """清空情景记忆"""

        self.sessions.clear()
        self._episodes.clear()

    def count(self) -> int:
        """返回情景记忆数量"""

        return len(self._episodes)

    def _persist_episode(self, episode: Episode) -> None:
        """持久化情景记忆到 SQLite 和 Qdrant"""

        metadata = {
            "memory_id": episode.episode_id,
            "episode_id": episode.episode_id,
            "session_id": episode.session_id,
            "timestamp": episode.timestamp,
            "memory_type": "episodic",
            "content": episode.content,
            **(episode.context or {})
        }

        try:
            if hasattr(self.doc_store, "add_document"):
                self.doc_store.add_document(
                    doc_id=episode.episode_id,
                    content=episode.content,
                    metadata=json.dumps(metadata, ensure_ascii=False)
                )
        except Exception as e:
            print(f"[WARNING] SQLite 保存情景记忆失败: {e}")

        try:
            embedding = self.embedder.encode(episode.content)

            if hasattr(embedding, "tolist"):
                embedding = embedding.tolist()

            if isinstance(embedding, list) and embedding and isinstance(embedding[0], list):
                embedding = embedding[0]

            embedding = [float(x) for x in embedding]

            self.vector_store.add_vectors(
                vectors=[embedding],
                metadata=[metadata],
                ids=[episode.episode_id]
            )

        except Exception as e:
            print(f"[WARNING] Qdrant 保存情景记忆失败: {e}")

    def _structured_filter(self, **kwargs) -> Optional[Set[str]]:
        """结构化过滤"""

        if not self._episodes:
            return None

        session_id = kwargs.get("session_id")
        start_time = kwargs.get("start_time")
        end_time = kwargs.get("end_time")
        min_importance = kwargs.get("min_importance", 0.0)
        user_id = kwargs.get("user_id")

        candidate_ids: Set[str] = set()

        for episode_id, episode in self._episodes.items():
            context = episode.context or {}

            if session_id and episode.session_id != session_id:
                continue

            if user_id and context.get("user_id") != user_id:
                continue

            importance = float(context.get("importance", 0.5))
            if importance < min_importance:
                continue

            episode_time = self._parse_timestamp(episode.timestamp)

            if start_time:
                if episode_time < self._parse_timestamp(start_time):
                    continue

            if end_time:
                if episode_time > self._parse_timestamp(end_time):
                    continue

            candidate_ids.add(episode_id)

        return candidate_ids

    def _vector_search(
        self,
        query: str,
        limit: int = 20,
        user_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """向量检索"""

        where = {"memory_type": "episodic"}

        if user_id:
            where["user_id"] = user_id

        try:
            query_vector = self.embedder.encode(query)

            if hasattr(query_vector, "tolist"):
                query_vector = query_vector.tolist()

            if isinstance(query_vector, list) and query_vector and isinstance(query_vector[0], list):
                query_vector = query_vector[0]

            query_vector = [float(x) for x in query_vector]

            hits = self.vector_store.search_similar(
                query_vector=query_vector,
                limit=limit,
                where=where
            )

            if hits:
                return hits

        except Exception as e:
            print(f"[WARNING] Qdrant 情景记忆检索失败，改用关键词兜底: {e}")

        return self._fallback_keyword_search(query, limit, user_id)

    def _fallback_keyword_search(
        self,
        query: str,
        limit: int,
        user_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """关键词兜底检索"""

        query_terms = set(query.lower().split())
        results = []

        for episode in self._episodes.values():
            context = episode.context or {}

            if user_id and context.get("user_id") != user_id:
                continue

            content = episode.content or ""
            content_terms = set(content.lower().split())

            if query_terms:
                overlap = query_terms & content_terms
                score = len(overlap) / len(query_terms)
            else:
                score = 0.0

            if score == 0.0 and query in content:
                score = 1.0

            if score > 0:
                metadata = {
                    "memory_id": episode.episode_id,
                    "episode_id": episode.episode_id,
                    "session_id": episode.session_id,
                    "timestamp": episode.timestamp,
                    "content": episode.content,
                    "memory_type": "episodic",
                    **context
                }

                results.append({
                    "id": episode.episode_id,
                    "score": score,
                    "metadata": metadata
                })

        results.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
        return results[:limit]

    def _should_include(
        self,
        hit: Dict[str, Any],
        candidate_ids: Optional[Set[str]],
        kwargs: Dict[str, Any]
    ) -> bool:
        """判断命中结果是否保留"""

        metadata = hit.get("metadata", {}) or {}

        memory_id = (
            metadata.get("memory_id")
            or metadata.get("episode_id")
            or hit.get("id")
        )

        if candidate_ids is not None and memory_id not in candidate_ids:
            return False

        min_importance = kwargs.get("min_importance", 0.0)
        importance = float(metadata.get("importance", 0.5))

        if importance < min_importance:
            return False

        session_id = kwargs.get("session_id")
        if session_id and metadata.get("session_id") != session_id:
            return False

        user_id = kwargs.get("user_id")
        if user_id and metadata.get("user_id") != user_id:
            return False

        return True

    def _calculate_episode_score(self, hit: Dict[str, Any]) -> float:
        """情景记忆评分算法"""

        metadata = hit.get("metadata", {}) or {}

        vec_score = float(hit.get("score", 0.0))
        recency_score = self._calculate_recency(metadata.get("timestamp"))
        importance = float(metadata.get("importance", 0.5))

        base_relevance = vec_score * 0.8 + recency_score * 0.2
        importance_weight = 0.8 + importance * 0.4

        return base_relevance * importance_weight

    def _calculate_recency(self, timestamp: str) -> float:
        """计算时间近因性"""

        try:
            age_days = self._calculate_age_days(timestamp)
            score = math.exp(-age_days / 7.0)
            return max(0.1, min(1.0, score))
        except Exception:
            return 0.5

    def _create_memory_item(self, hit: Dict[str, Any]) -> MemoryItem:
        """把检索结果转换为 MemoryItem"""

        metadata = hit.get("metadata", {}) or {}

        memory_id = (
            metadata.get("memory_id")
            or metadata.get("episode_id")
            or hit.get("id")
        )

        content = metadata.get("content", "")

        if not content and memory_id in self._episodes:
            content = self._episodes[memory_id].content

        importance = float(metadata.get("importance", 0.5))
        timestamp = metadata.get("timestamp")

        item = MemoryItem(
            content=content,
            memory_type="episodic",
            importance=importance,
            metadata={
                **metadata,
                "retrieval_score": float(hit.get("score", 0.0))
            }
        )

        if memory_id:
            item.id = memory_id

        if timestamp:
            item.timestamp = timestamp

        return item

    def _calculate_age_days(self, timestamp: str) -> float:
        """计算记忆年龄"""

        memory_time = self._parse_timestamp(timestamp)
        delta = datetime.now() - memory_time
        return max(0.0, delta.total_seconds() / 86400)

    def _parse_timestamp(self, timestamp: Any) -> datetime:
        """解析时间戳"""

        if isinstance(timestamp, datetime):
            return timestamp

        if not timestamp:
            return datetime.now()

        try:
            return datetime.fromisoformat(str(timestamp))
        except Exception:
            return datetime.now()