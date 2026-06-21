from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from hello_agents.memory.base import MemoryConfig, MemoryItem


class WorkingMemory:
    """工作记忆实现

    特点：
    - 容量有限，默认 50 条
    - TTL 自动清理，默认 60 分钟
    - 纯内存存储，访问速度快
    - 混合检索：简易 TF-IDF + 关键词匹配
    """

    def __init__(self, config: MemoryConfig, storage_backend=None):
        self.config = config
        self.storage_backend = storage_backend

        self.max_capacity = getattr(config, "working_memory_capacity", 50) or 50
        self.max_age_minutes = getattr(config, "working_memory_ttl", 60) or 60

        self.memories: List[MemoryItem] = []

    def add(self, memory_item: MemoryItem) -> str:
        """添加工作记忆"""
        self._expire_old_memories()

        if len(self.memories) >= self.max_capacity:
            self._remove_lowest_priority_memory()

        self.memories.append(memory_item)
        return memory_item.id

    def retrieve(self, query: str, limit: int = 5, **kwargs) -> List[MemoryItem]:
        """混合检索：TF-IDF 向量化 + 关键词匹配"""
        self._expire_old_memories()

        if not query:
            return []

        min_importance = kwargs.get("min_importance", 0.0)

        vector_scores = self._try_tfidf_search(query)

        scored_memories = []
        for memory in self.memories:
            if memory.importance < min_importance:
                continue

            vector_score = vector_scores.get(memory.id, 0.0)
            keyword_score = self._calculate_keyword_score(query, memory.content)

            if vector_score > 0:
                base_relevance = vector_score * 0.7 + keyword_score * 0.3
            else:
                base_relevance = keyword_score

            time_decay = self._calculate_time_decay(memory.timestamp)
            importance_weight = 0.8 + memory.importance * 0.4

            final_score = base_relevance * time_decay * importance_weight

            if final_score > 0:
                scored_memories.append((final_score, memory))

        scored_memories.sort(key=lambda x: x[0], reverse=True)
        return [memory for _, memory in scored_memories[:limit]]

    def forget(
        self,
        strategy: str = "importance_based",
        threshold: float = 0.1,
        max_age_days: int = 30
    ) -> int:
        """遗忘工作记忆"""

        before_count = len(self.memories)

        if strategy == "importance_based":
            self.memories = [
                m for m in self.memories
                if m.importance >= threshold
            ]

        elif strategy == "time_based":
            now = datetime.now()
            max_age = timedelta(days=max_age_days)

            self.memories = [
                m for m in self.memories
                if now - self._parse_timestamp(m.timestamp) <= max_age
            ]

        elif strategy == "capacity_based":
            if len(self.memories) > self.max_capacity:
                self.memories.sort(
                    key=lambda m: (
                        m.importance,
                        self._parse_timestamp(m.timestamp)
                    )
                )
                self.memories = self.memories[-self.max_capacity:]

        else:
            return 0

        return before_count - len(self.memories)

    def clear(self) -> None:
        """清空工作记忆"""
        self.memories.clear()

    def count(self) -> int:
        """返回工作记忆数量"""
        self._expire_old_memories()
        return len(self.memories)

    def _expire_old_memories(self) -> None:
        """清理超过 TTL 的工作记忆"""

        if not self.memories:
            return

        now = datetime.now()
        ttl = timedelta(minutes=self.max_age_minutes)

        self.memories = [
            memory
            for memory in self.memories
            if now - self._parse_timestamp(memory.timestamp) <= ttl
        ]

    def _remove_lowest_priority_memory(self) -> None:
        """容量满时删除优先级最低的记忆

        优先级计算：
        - 重要性越低，越容易删除
        - 时间越久远，越容易删除
        """

        if not self.memories:
            return

        def priority(memory: MemoryItem) -> float:
            importance = memory.importance
            time_decay = self._calculate_time_decay(memory.timestamp)
            return importance * time_decay

        lowest = min(self.memories, key=priority)
        self.memories.remove(lowest)

    def _try_tfidf_search(self, query: str) -> Dict[str, float]:
        """简易 TF-IDF 检索

        不依赖 sklearn，避免额外安装依赖。
        返回：
        {
            memory_id: similarity_score
        }
        """

        if not query or not self.memories:
            return {}

        docs = [memory.content for memory in self.memories]
        query_tokens = self._tokenize(query)

        if not query_tokens:
            return {}

        doc_tokens = [self._tokenize(doc) for doc in docs]

        document_frequency = Counter()
        for tokens in doc_tokens:
            for token in set(tokens):
                document_frequency[token] += 1

        total_docs = len(doc_tokens)

        def tfidf_vector(tokens: List[str]) -> Dict[str, float]:
            token_count = Counter(tokens)
            total_tokens = len(tokens) or 1

            vec = {}
            for token, count in token_count.items():
                tf = count / total_tokens
                idf = math.log((total_docs + 1) / (document_frequency.get(token, 0) + 1)) + 1
                vec[token] = tf * idf

            return vec

        query_vec = tfidf_vector(query_tokens)

        scores: Dict[str, float] = {}

        for memory, tokens in zip(self.memories, doc_tokens):
            doc_vec = tfidf_vector(tokens)
            score = self._cosine_dict(query_vec, doc_vec)

            if score > 0:
                scores[memory.id] = score

        return scores

    def _calculate_keyword_score(self, query: str, content: str) -> float:
        """关键词匹配分数"""

        query_tokens = set(self._tokenize(query))
        content_tokens = set(self._tokenize(content))

        if not query_tokens or not content_tokens:
            return 0.0

        overlap = query_tokens & content_tokens
        if not overlap:
            return 0.0

        recall_score = len(overlap) / len(query_tokens)
        precision_score = len(overlap) / len(content_tokens)

        # 更重视 query 中关键词是否命中
        return recall_score * 0.8 + precision_score * 0.2

    def _calculate_time_decay(self, timestamp: str) -> float:
        """计算时间衰减分数

        工作记忆更重视最近内容。
        TTL 内逐渐衰减，最低保留 0.1。
        """

        try:
            memory_time = self._parse_timestamp(timestamp)
            age_minutes = max(
                0.0,
                (datetime.now() - memory_time).total_seconds() / 60
            )

            if self.max_age_minutes <= 0:
                return 1.0

            decay = math.exp(-age_minutes / self.max_age_minutes)
            return max(0.1, min(1.0, decay))

        except Exception:
            return 0.5

    def _parse_timestamp(self, timestamp: str) -> datetime:
        """解析时间戳"""

        if isinstance(timestamp, datetime):
            return timestamp

        if not timestamp:
            return datetime.now()

        try:
            return datetime.fromisoformat(str(timestamp))
        except Exception:
            return datetime.now()

    def _tokenize(self, text: str) -> List[str]:
        """中英文混合分词

        - 中文按单字切
        - 英文按单词切
        """

        if not text:
            return []

        tokens: List[str] = []

        for ch in text:
            if "\u4e00" <= ch <= "\u9fff":
                tokens.append(ch)

        english_words = re.findall(r"[A-Za-z0-9_]+", text.lower())
        tokens.extend(english_words)

        return tokens

    def _cosine_dict(
        self,
        vec_a: Dict[str, float],
        vec_b: Dict[str, float]
    ) -> float:
        """稀疏向量余弦相似度"""

        if not vec_a or not vec_b:
            return 0.0

        common_keys = set(vec_a.keys()) & set(vec_b.keys())
        dot = sum(vec_a[k] * vec_b[k] for k in common_keys)

        norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
        norm_b = math.sqrt(sum(v * v for v in vec_b.values()))

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot / (norm_a * norm_b)