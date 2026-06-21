from __future__ import annotations

import re
import uuid
from typing import Dict, List, Any, Optional

from hello_agents.memory.base import (
    MemoryConfig,
    MemoryItem,
    BaseMemory,
    Entity,
    Relation,
)
from hello_agents.memory.embedding import get_text_embedder
from hello_agents.memory.storage.qdrant_store import QdrantConnectionManager
from hello_agents.memory.storage.neo4j_store import Neo4jGraphStore


class SemanticMemory(BaseMemory):
    """语义记忆实现

    当前版本是可运行兜底版：
    - 使用 embedding.py 中的兜底嵌入器
    - 使用 qdrant_store.py 中的向量存储
    - 使用 neo4j_store.py 中的图存储兜底实现
    - 支持 add / retrieve / forget / clear / count
    """

    def __init__(self, config: MemoryConfig, storage_backend=None):
        super().__init__(config, storage_backend)

        self.config = config
        self.storage_backend = storage_backend

        # 嵌入模型
        self.embedding_model = get_text_embedder()

        # 向量存储
        self.vector_store = QdrantConnectionManager.get_instance(
            qdrant_url=getattr(config, "qdrant_url", None),
            qdrant_api_key=getattr(config, "qdrant_api_key", None),
            collection_name=getattr(config, "qdrant_collection", "hello_agents_vectors"),
        )

        # 图存储
        self.graph_store = Neo4jGraphStore(
            uri=getattr(config, "neo4j_uri", None),
            username=getattr(config, "neo4j_username", "neo4j"),
            password=getattr(config, "neo4j_password", None),
            database=getattr(config, "neo4j_database", "neo4j"),
        )

        # 内存缓存
        self.entities: Dict[str, Entity] = {}
        self.relations: List[Relation] = []
        self.memories: Dict[str, MemoryItem] = {}

    def add(self, memory_item: MemoryItem) -> str:
        """添加语义记忆"""

        self.memories[memory_item.id] = memory_item

        # 1. 生成文本嵌入
        embedding = self.embedding_model.encode(memory_item.content)

        if hasattr(embedding, "tolist"):
            embedding = embedding.tolist()

        if isinstance(embedding, list) and embedding and isinstance(embedding[0], list):
            embedding = embedding[0]

        embedding = [float(x) for x in embedding]

        # 2. 提取实体和关系
        entities = self._extract_entities(memory_item.content)
        relations = self._extract_relations(memory_item.content, entities)

        # 3. 存储到图数据库兜底实现
        for entity in entities:
            self.entities[entity.entity_id] = entity
            self._add_entity_to_graph(entity, memory_item)

        for relation in relations:
            self.relations.append(relation)
            self._add_relation_to_graph(relation, memory_item)

        # 4. 存储到向量数据库兜底实现
        metadata = {
            "memory_id": memory_item.id,
            "memory_type": "semantic",
            "content": memory_item.content,
            "importance": memory_item.importance,
            "timestamp": memory_item.timestamp,
            "entities": [e.entity_id for e in entities],
            "entity_count": len(entities),
            "relation_count": len(relations),
            **(memory_item.metadata or {}),
        }

        self.vector_store.add_vectors(
            vectors=[embedding],
            metadata=[metadata],
            ids=[memory_item.id],
        )

        return memory_item.id

    def retrieve(self, query: str, limit: int = 5, **kwargs) -> List[MemoryItem]:
        """检索语义记忆"""

        if not query:
            return []

        user_id = kwargs.get("user_id")
        min_importance = kwargs.get("min_importance", 0.0)

        vector_results = self._vector_search(
            query=query,
            limit=limit * 2,
            user_id=user_id,
            min_importance=min_importance,
        )

        graph_results = self._graph_search(
            query=query,
            limit=limit * 2,
            user_id=user_id,
            min_importance=min_importance,
        )

        combined_results = self._combine_and_rank_results(
            vector_results=vector_results,
            graph_results=graph_results,
            query=query,
            limit=limit,
        )

        return combined_results[:limit]

    def forget(
        self,
        strategy: str = "importance_based",
        threshold: float = 0.1,
        max_age_days: int = 30
    ) -> int:
        """遗忘语义记忆"""

        before = len(self.memories)

        if strategy == "importance_based":
            self.memories = {
                mid: item
                for mid, item in self.memories.items()
                if item.importance >= threshold
            }

        # 当前兜底版暂时只实现 importance_based
        return before - len(self.memories)

    def clear(self) -> None:
        """清空语义记忆"""

        self.memories.clear()
        self.entities.clear()
        self.relations.clear()

    def count(self) -> int:
        """返回语义记忆数量"""

        return len(self.memories)

    def _vector_search(
        self,
        query: str,
        limit: int = 10,
        user_id: Optional[str] = None,
        min_importance: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """向量检索"""

        query_vector = self.embedding_model.encode(query)

        if hasattr(query_vector, "tolist"):
            query_vector = query_vector.tolist()

        if isinstance(query_vector, list) and query_vector and isinstance(query_vector[0], list):
            query_vector = query_vector[0]

        query_vector = [float(x) for x in query_vector]

        where = {"memory_type": "semantic"}

        if user_id:
            where["user_id"] = user_id

        try:
            hits = self.vector_store.search_similar(
                query_vector=query_vector,
                limit=limit,
                where=where,
            )

            filtered = []
            for hit in hits:
                metadata = hit.get("metadata", {}) or {}
                importance = float(metadata.get("importance", 0.5))
                if importance >= min_importance:
                    filtered.append(hit)

            if filtered:
                return filtered

        except Exception:
            pass

        return self._fallback_keyword_search(
            query=query,
            limit=limit,
            user_id=user_id,
            min_importance=min_importance,
        )

    def _graph_search(
        self,
        query: str,
        limit: int = 10,
        user_id: Optional[str] = None,
        min_importance: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """图检索兜底"""

        results = []

        for memory_id, item in self.memories.items():
            if user_id and item.metadata.get("user_id") != user_id:
                continue

            if item.importance < min_importance:
                continue

            score = self._keyword_score(query, item.content)

            if score > 0:
                results.append({
                    "memory_id": memory_id,
                    "content": item.content,
                    "importance": item.importance,
                    "similarity": score,
                    "metadata": item.metadata,
                    "timestamp": item.timestamp,
                })

        results.sort(key=lambda x: float(x.get("similarity", 0.0)), reverse=True)
        return results[:limit]

    def _combine_and_rank_results(
        self,
        vector_results,
        graph_results,
        query,
        limit
    ) -> List[MemoryItem]:
        """混合排序结果"""

        combined: Dict[str, Dict[str, Any]] = {}

        # 合并向量结果
        for result in vector_results:
            metadata = result.get("metadata", {}) or {}
            memory_id = metadata.get("memory_id") or result.get("id")

            if not memory_id:
                continue

            combined[memory_id] = {
                "memory_id": memory_id,
                "content": metadata.get("content", ""),
                "importance": float(metadata.get("importance", 0.5)),
                "timestamp": metadata.get("timestamp"),
                "metadata": metadata,
                "vector_score": float(result.get("score", 0.0)),
                "graph_score": 0.0,
            }

        # 合并图结果
        for result in graph_results:
            memory_id = result.get("memory_id")

            if not memory_id:
                continue

            if memory_id in combined:
                combined[memory_id]["graph_score"] = float(result.get("similarity", 0.0))
            else:
                combined[memory_id] = {
                    "memory_id": memory_id,
                    "content": result.get("content", ""),
                    "importance": float(result.get("importance", 0.5)),
                    "timestamp": result.get("timestamp"),
                    "metadata": result.get("metadata", {}),
                    "vector_score": 0.0,
                    "graph_score": float(result.get("similarity", 0.0)),
                }

        ranked = []

        for memory_id, result in combined.items():
            vector_score = result["vector_score"]
            graph_score = result["graph_score"]
            importance = float(result.get("importance", 0.5))

            base_relevance = vector_score * 0.7 + graph_score * 0.3
            importance_weight = 0.8 + importance * 0.4
            combined_score = base_relevance * importance_weight

            item = MemoryItem(
                content=result.get("content", ""),
                memory_type="semantic",
                importance=importance,
                metadata={
                    **(result.get("metadata") or {}),
                    "combined_score": combined_score,
                    "vector_score": vector_score,
                    "graph_score": graph_score,
                },
            )

            item.id = memory_id

            if result.get("timestamp"):
                item.timestamp = result["timestamp"]

            ranked.append((combined_score, item))

        ranked.sort(key=lambda x: x[0], reverse=True)

        return [item for _, item in ranked[:limit]]

    def _fallback_keyword_search(
        self,
        query: str,
        limit: int,
        user_id: Optional[str] = None,
        min_importance: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """关键词兜底检索"""

        results = []

        for memory_id, item in self.memories.items():
            if user_id and item.metadata.get("user_id") != user_id:
                continue

            if item.importance < min_importance:
                continue

            score = self._keyword_score(query, item.content)

            if score > 0:
                results.append({
                    "id": memory_id,
                    "score": score,
                    "metadata": {
                        "memory_id": memory_id,
                        "memory_type": "semantic",
                        "content": item.content,
                        "importance": item.importance,
                        "timestamp": item.timestamp,
                        **(item.metadata or {}),
                    }
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    def _extract_entities(self, text: str) -> List[Entity]:
        """简单实体抽取兜底"""

        words = re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]{2,}", text)

        entities = []
        seen = set()

        for word in words[:10]:
            if word in seen:
                continue

            seen.add(word)

            entity = Entity(
                entity_id=str(uuid.uuid4()),
                name=word,
                entity_type="concept",
                metadata={}
            )

            entities.append(entity)

        return entities

    def _extract_relations(self, text: str, entities: List[Entity]) -> List[Relation]:
        """简单关系抽取兜底"""

        relations = []

        for i in range(len(entities) - 1):
            relations.append(
                Relation(
                    source_id=entities[i].entity_id,
                    target_id=entities[i + 1].entity_id,
                    relation_type="related_to",
                    metadata={"source": "simple_extractor"}
                )
            )

        return relations

    def _add_entity_to_graph(self, entity: Entity, memory_item: MemoryItem) -> None:
        """添加实体到图存储"""

        if hasattr(self.graph_store, "add_entity"):
            self.graph_store.add_entity(
                entity_id=entity.entity_id,
                properties={
                    "name": entity.name,
                    "entity_type": entity.entity_type,
                    "memory_id": memory_item.id,
                    **(entity.metadata or {}),
                }
            )

    def _add_relation_to_graph(self, relation: Relation, memory_item: MemoryItem) -> None:
        """添加关系到图存储"""

        if hasattr(self.graph_store, "add_relation"):
            self.graph_store.add_relation(
                source_id=relation.source_id,
                target_id=relation.target_id,
                relation_type=relation.relation_type,
                properties={
                    "memory_id": memory_item.id,
                    **(relation.metadata or {}),
                }
            )

    def _keyword_score(self, query: str, content: str) -> float:
        """关键词相似度"""

        if not query or not content:
            return 0.0

        query_terms = set(query.lower().split())
        content_terms = set(content.lower().split())

        if query_terms and content_terms:
            overlap = query_terms & content_terms
            score = len(overlap) / len(query_terms)
            if score > 0:
                return score

        if query in content:
            return 1.0

        return 0.0