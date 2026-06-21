from typing import List, Dict, Any, Optional
import math


class QdrantVectorStore:
    def __init__(self, qdrant_url: str = None, qdrant_api_key: str = None, collection_name: str = "default"):
        self.qdrant_url = qdrant_url
        self.qdrant_api_key = qdrant_api_key
        self.collection_name = collection_name
        self.vectors = {}

    def add_vectors(self, vectors: List[List[float]], metadata: List[Dict[str, Any]], ids: List[str]):
        for vec, meta, item_id in zip(vectors, metadata, ids):
            self.vectors[item_id] = {
                "id": item_id,
                "vector": vec,
                "metadata": meta
            }

    def search_similar(
        self,
        query_vector: List[float],
        limit: int = 5,
        score_threshold: Optional[float] = None,
        where: Dict[str, Any] = None
    ) -> List[Dict[str, Any]]:
        results = []

        for item_id, item in self.vectors.items():
            if where:
                ok = True
                for k, v in where.items():
                    if item["metadata"].get(k) != v:
                        ok = False
                        break
                if not ok:
                    continue

            score = self._cosine_similarity(query_vector, item["vector"])
            if score_threshold is not None and score < score_threshold:
                continue

            results.append({
                "id": item_id,
                "score": score,
                "metadata": item["metadata"]
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    def _cosine_similarity(self, a, b):
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


class QdrantConnectionManager:
    _instances = {}

    @classmethod
    def get_instance(cls, **kwargs):
        collection_name = kwargs.get("collection_name", "default")
        if collection_name not in cls._instances:
            cls._instances[collection_name] = QdrantVectorStore(
                qdrant_url=kwargs.get("qdrant_url"),
                qdrant_api_key=kwargs.get("qdrant_api_key"),
                collection_name=collection_name
            )
        return cls._instances[collection_name]


def _create_default_vector_store(dimension: int = 384):
    return QdrantVectorStore(collection_name="default")