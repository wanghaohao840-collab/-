# hello_agents/memory/rag/pipeline.py

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from hello_agents.memory.embedding import get_text_embedder, get_dimension, embed_query


class SimpleRAGPipeline:
    """最小可运行 RAG 管道 + 本地 JSON 持久化

    功能：
    1. 支持 add_text
    2. 支持 search
    3. 支持 stats
    4. 支持 clear
    5. 支持本地 JSON 持久化
    """

    def __init__(
        self,
        collection_name: str = "rag_knowledge_base",
        rag_namespace: str = "default",
        qdrant_url: Optional[str] = None,
        qdrant_api_key: Optional[str] = None,
        cache_path: Optional[str] = None,
    ):
        self.collection_name = collection_name
        self.rag_namespace = rag_namespace
        self.qdrant_url = qdrant_url
        self.qdrant_api_key = qdrant_api_key

        self.embedder = get_text_embedder()
        self.dimension = get_dimension(384)

        self.chunks: List[Dict[str, Any]] = []

        # 每一个 collection + namespace 使用一个独立 JSON 缓存文件
        self.cache_path = Path(cache_path) if cache_path else self._default_cache_path()
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

        # 启动时自动加载历史 chunks
        self._load_cache()

    def add_text(
            self,
            text: str,
            document_id: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None,
            replace_existing: bool = True,
            save_cache: bool = True,
    ) -> Dict[str, Any]:
        """添加文本到 RAG 知识库，并可选择是否立即持久化"""

        if not text or not text.strip():
            return {
                "success": False,
                "message": "文本为空，无法添加"
            }

        document_id = document_id or str(uuid.uuid4())
        metadata = metadata or {}

        # 同一个 document_id 默认覆盖旧版本，防止重复导入
        removed = 0
        if replace_existing:
            removed = self._remove_document_chunks(document_id)

        chunk_texts = self._split_text(text)

        added = 0

        existing_count = sum(
            1 for chunk in self.chunks
            if chunk.get("document_id") == document_id
        )

        for index, chunk_text in enumerate(chunk_texts):
            chunk_id = f"{document_id}_{existing_count + index}"

            vector = self._to_vector(chunk_text)

            chunk = {
                "id": chunk_id,
                "document_id": document_id,
                "content": chunk_text,
                "vector": vector,
                "metadata": {
                    "memory_id": chunk_id,
                    "document_id": document_id,
                    "chunk_index": existing_count + index,
                    "content": chunk_text,
                    "memory_type": "rag_chunk",
                    "is_rag_data": True,
                    "data_source": "rag_pipeline",
                    "rag_namespace": self.rag_namespace,
                    "created_at": datetime.now().isoformat(),
                    **metadata,
                }
            }

            self.chunks.append(chunk)
            added += 1

        # 关键：只有 save_cache=True 时才保存
        if save_cache:
            self._save_cache()

        message = f"已添加文本知识，生成 {added} 个 chunk"
        if removed:
            message += f"，并覆盖旧 chunk {removed} 个"

        return {
            "success": True,
            "document_id": document_id,
            "chunks_added": added,
            "chunks_removed": removed,
            "cache_path": str(self.cache_path),
            "message": message,
        }

    def search(
            self,
            query: str,
            limit: int = 5,
            min_score: float = 0.0,
            document_id: Optional[str] = None,
            **kwargs
    ) -> List[Dict[str, Any]]:
        """检索 RAG 知识库，可按 document_id 过滤"""

        if not query:
            return []

        query_vector = self._to_vector(query)

        results = []

        for chunk in self.chunks:
            # 关键：只检索指定 document_id 的 chunk
            if document_id and chunk.get("document_id") != document_id:
                continue

            score = self._cosine_similarity(query_vector, chunk["vector"])

            if score >= min_score:
                results.append({
                    "id": chunk["id"],
                    "score": score,
                    "content": chunk["content"],
                    "metadata": chunk["metadata"],
                })

        results.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)

        results = self._dedupe_results_by_page(
            results=results,
            max_per_page=1,
            limit=limit
        )

        return results

    def stats(self) -> Dict[str, Any]:
        """知识库统计"""

        document_ids = set()

        for chunk in self.chunks:
            document_ids.add(chunk["document_id"])

        return {
            "collection_name": self.collection_name,
            "rag_namespace": self.rag_namespace,
            "document_count": len(document_ids),
            "chunk_count": len(self.chunks),
            "dimension": self.dimension,
            "cache_path": str(self.cache_path),
            "cache_exists": self.cache_path.exists(),
        }

    def clear(self) -> Dict[str, Any]:
        """清空知识库，并同步清空 JSON 缓存"""

        count = len(self.chunks)
        self.chunks.clear()

        self._save_cache()

        return {
            "success": True,
            "message": f"已清空知识库，共删除 {count} 个 chunk",
            "cache_path": str(self.cache_path),
        }

    def delete_document(self, document_id: str) -> Dict[str, Any]:
        """删除指定 document_id 的所有 chunks，并同步保存缓存"""

        if not document_id:
            return {
                "success": False,
                "message": "document_id 不能为空",
                "chunks_removed": 0,
            }

        before = len(self.chunks)

        self.chunks = [
            chunk
            for chunk in self.chunks
            if chunk.get("document_id") != document_id
        ]

        removed = before - len(self.chunks)

        self._save_cache()

        return {
            "success": True,
            "document_id": document_id,
            "chunks_removed": removed,
            "message": f"已删除文档 {document_id}，共删除 {removed} 个 chunk",
            "cache_path": str(self.cache_path),
        }

    def _split_text(
            self,
            text: str,
            chunk_size: int = 800,
            chunk_overlap: int = 120
    ) -> List[str]:
        """按段落优先切分文本，避免固定长度硬切导致语义断裂"""

        text = text.strip()

        if not text:
            return []

        if len(text) <= chunk_size:
            return [text]

        # 1. 先按空行切段
        raw_paragraphs = []

        for part in text.replace("\r\n", "\n").split("\n\n"):
            part = part.strip()
            if part:
                raw_paragraphs.append(part)

        # 如果没有明显空行，就按单行切
        if len(raw_paragraphs) <= 1:
            raw_paragraphs = [
                line.strip()
                for line in text.split("\n")
                if line.strip()
            ]

        chunks = []
        current = ""

        # 2. 合并短段落，尽量让 chunk 接近 chunk_size
        for para in raw_paragraphs:
            if not current:
                current = para
                continue

            candidate = current + "\n\n" + para

            if len(candidate) <= chunk_size:
                current = candidate
            else:
                chunks.extend(
                    self._split_long_text_with_overlap(
                        current,
                        chunk_size=chunk_size,
                        chunk_overlap=chunk_overlap
                    )
                )
                current = para

        if current:
            chunks.extend(
                self._split_long_text_with_overlap(
                    current,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap
                )
            )

        return [chunk.strip() for chunk in chunks if chunk.strip()]

    def _to_vector(self, text: str) -> List[float]:
        """文本转向量"""

        vector = self.embedder.encode(text)

        if hasattr(vector, "tolist"):
            vector = vector.tolist()

        if isinstance(vector, list) and vector and isinstance(vector[0], list):
            vector = vector[0]

        vector = [float(x) for x in vector]

        if len(vector) < self.dimension:
            vector.extend([0.0] * (self.dimension - len(vector)))
        elif len(vector) > self.dimension:
            vector = vector[:self.dimension]

        return vector

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """余弦相似度"""

        if not a or not b:
            return 0.0

        length = min(len(a), len(b))
        a = a[:length]
        b = b[:length]

        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot / (norm_a * norm_b)

    def _default_cache_path(self) -> Path:
        """生成默认缓存路径"""

        safe_collection = self._safe_name(self.collection_name)
        safe_namespace = self._safe_name(self.rag_namespace)

        return Path("./knowledge_base/rag_cache") / f"{safe_collection}__{safe_namespace}.json"

    def _safe_name(self, name: str) -> str:
        """生成安全文件名"""

        name = name or "default"

        safe_chars = []

        for ch in name:
            if ch.isalnum() or ch in ["_", "-", "."]:
                safe_chars.append(ch)
            else:
                safe_chars.append("_")

        return "".join(safe_chars)

    def _load_cache(self) -> None:
        """从 JSON 加载历史 chunks"""

        if not self.cache_path.exists():
            self.chunks = []
            return

        try:
            text = self.cache_path.read_text(encoding="utf-8")
            data = json.loads(text)

            if not isinstance(data, dict):
                self.chunks = []
                return

            chunks = data.get("chunks", [])

            if not isinstance(chunks, list):
                self.chunks = []
                return

            self.chunks = self._normalize_loaded_chunks(chunks)

            print(
                f"[RAG] 已加载本地缓存: {self.cache_path}, "
                f"chunks={len(self.chunks)}"
            )

        except Exception as e:
            print(f"[WARNING] RAG 缓存加载失败: {self.cache_path}, error={e}")
            self.chunks = []

    def _save_cache(self) -> None:
        """保存 chunks 到 JSON"""

        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "collection_name": self.collection_name,
                "rag_namespace": self.rag_namespace,
                "dimension": self.dimension,
                "updated_at": datetime.now().isoformat(),
                "chunk_count": len(self.chunks),
                "chunks": self._serializable_chunks(),
            }

            temp_path = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")

            temp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

            temp_path.replace(self.cache_path)

        except Exception as e:
            print(f"[WARNING] RAG 缓存保存失败: {self.cache_path}, error={e}")

    def _serializable_chunks(self) -> List[Dict[str, Any]]:
        """把 chunks 转成 JSON 可保存格式"""

        serializable = []

        for chunk in self.chunks:
            vector = chunk.get("vector", [])

            if hasattr(vector, "tolist"):
                vector = vector.tolist()

            vector = [float(x) for x in vector]

            serializable.append({
                "id": str(chunk.get("id", "")),
                "document_id": str(chunk.get("document_id", "")),
                "content": str(chunk.get("content", "")),
                "vector": vector,
                "metadata": self._json_safe_dict(chunk.get("metadata", {}) or {}),
            })

        return serializable

    def _normalize_loaded_chunks(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """规范化从 JSON 读取的 chunks"""

        normalized = []

        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue

            content = str(chunk.get("content", ""))
            if not content.strip():
                continue

            vector = chunk.get("vector", [])

            if not isinstance(vector, list) or not vector:
                vector = self._to_vector(content)

            try:
                vector = [float(x) for x in vector]
            except Exception:
                vector = self._to_vector(content)

            if len(vector) < self.dimension:
                vector.extend([0.0] * (self.dimension - len(vector)))
            elif len(vector) > self.dimension:
                vector = vector[:self.dimension]

            chunk_id = str(chunk.get("id") or str(uuid.uuid4()))
            document_id = str(chunk.get("document_id") or chunk_id)

            metadata = chunk.get("metadata", {}) or {}
            if not isinstance(metadata, dict):
                metadata = {}

            metadata.setdefault("memory_id", chunk_id)
            metadata.setdefault("document_id", document_id)
            metadata.setdefault("content", content)
            metadata.setdefault("memory_type", "rag_chunk")
            metadata.setdefault("is_rag_data", True)
            metadata.setdefault("data_source", "rag_pipeline")
            metadata.setdefault("rag_namespace", self.rag_namespace)

            normalized.append({
                "id": chunk_id,
                "document_id": document_id,
                "content": content,
                "vector": vector,
                "metadata": metadata,
            })

        return normalized

    def _json_safe_dict(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """确保 metadata 可以被 JSON 保存"""

        safe = {}

        for key, value in data.items():
            key = str(key)

            if isinstance(value, (str, int, float, bool)) or value is None:
                safe[key] = value
            elif isinstance(value, (list, tuple)):
                safe[key] = [
                    item if isinstance(item, (str, int, float, bool)) or item is None else str(item)
                    for item in value
                ]
            elif isinstance(value, dict):
                safe[key] = self._json_safe_dict(value)
            else:
                safe[key] = str(value)

        return safe

    def _remove_document_chunks(self, document_id: str) -> int:
        """删除指定 document_id 的旧 chunks"""

        before = len(self.chunks)

        self.chunks = [
            chunk
            for chunk in self.chunks
            if chunk.get("document_id") != document_id
        ]

        return before - len(self.chunks)

    def _split_long_text_with_overlap(
            self,
            text: str,
            chunk_size: int = 800,
            chunk_overlap: int = 120
    ) -> List[str]:
        """对超长文本做带 overlap 的切分"""

        text = text.strip()

        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0

        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end].strip()

            if chunk:
                chunks.append(chunk)

            if end >= len(text):
                break

            start = max(0, end - chunk_overlap)

        return chunks

    def get_document_summary_context(
            self,
            document_id: str,
            limit: int = 12
    ) -> List[Dict[str, Any]]:
        """为全文总结类问题获取更合适的上下文

        策略：
        1. 优先取前 3 页
        2. 再按页码间隔抽样
        3. 尽量避免同一页重复太多 chunk
        """

        if not document_id:
            return []

        doc_chunks = [
            chunk
            for chunk in self.chunks
            if chunk.get("document_id") == document_id
        ]

        if not doc_chunks:
            return []

        def get_page_number(chunk):
            metadata = chunk.get("metadata", {}) or {}
            page = metadata.get("page_number", 999999)

            try:
                return int(page)
            except Exception:
                return 999999

        doc_chunks.sort(key=get_page_number)

        # 按页聚合，每页只取前 1 个 chunk，避免重复
        page_map = {}

        for chunk in doc_chunks:
            page = get_page_number(chunk)

            if page not in page_map:
                page_map[page] = chunk

        pages = sorted(page_map.keys())

        selected = []

        # 1. 优先取前 3 页
        for page in pages[:3]:
            selected.append(page_map[page])

        # 2. 再做间隔抽样
        remaining_pages = pages[3:]

        if remaining_pages:
            step = max(1, len(remaining_pages) // max(1, limit - len(selected)))

            for page in remaining_pages[::step]:
                if len(selected) >= limit:
                    break

                selected.append(page_map[page])

        # 3. 转成和 search 返回一致的格式
        results = []

        for chunk in selected[:limit]:
            results.append({
                "id": chunk.get("id"),
                "score": 1.0,
                "content": chunk.get("content", ""),
                "metadata": chunk.get("metadata", {}) or {},
            })

        return results

    def _dedupe_results_by_page(
            self,
            results: List[Dict[str, Any]],
            max_per_page: int = 1,
            limit: int = 5
    ) -> List[Dict[str, Any]]:
        """按 document_id + page_number 去重，避免同一页重复出现太多 chunk"""

        page_counter = {}
        deduped = []

        for item in results:
            metadata = item.get("metadata", {}) or {}

            document_id = metadata.get("document_id") or ""
            page_number = metadata.get("page_number")

            # 没有页码的内容不强制去重
            if page_number in [None, ""]:
                deduped.append(item)
                if len(deduped) >= limit:
                    break
                continue

            key = f"{document_id}__page_{page_number}"

            count = page_counter.get(key, 0)

            if count >= max_per_page:
                continue

            page_counter[key] = count + 1
            deduped.append(item)

            if len(deduped) >= limit:
                break

        return deduped


def create_rag_pipeline(
    qdrant_url: Optional[str] = None,
    qdrant_api_key: Optional[str] = None,
    collection_name: str = "rag_knowledge_base",
    rag_namespace: str = "default",
    cache_path: Optional[str] = None,
    **kwargs
) -> SimpleRAGPipeline:
    """创建 RAG 管道"""

    return SimpleRAGPipeline(
        collection_name=collection_name,
        rag_namespace=rag_namespace,
        qdrant_url=qdrant_url,
        qdrant_api_key=qdrant_api_key,
        cache_path=cache_path,
    )


def index_chunks(
    store=None,
    chunks: Optional[List[Dict[str, Any]]] = None,
    cache_db: Optional[str] = None,
    batch_size: int = 64,
    rag_namespace: str = "default"
) -> None:
    """兼容旧版 index_chunks 调用"""

    if not chunks:
        print("[RAG] No chunks to index")
        return

    if store is None:
        store = create_rag_pipeline(rag_namespace=rag_namespace)

    for chunk in chunks:
        content = chunk.get("content", "")
        metadata = {
            k: v
            for k, v in chunk.items()
            if k != "content"
        }

        if hasattr(store, "add_text"):
            store.add_text(
                text=content,
                document_id=metadata.get("document_id"),
                metadata=metadata,
            )


def _prompt_mqe(query: str, n: int) -> List[str]:
    """查询扩展兜底"""

    return [query]


def _prompt_hyde(query: str) -> Optional[str]:
    """HyDE 兜底"""

    return None


def search_vectors_expanded(
    store=None,
    query: str = "",
    top_k: int = 8,
    rag_namespace: Optional[str] = None,
    only_rag_data: bool = True,
    score_threshold: Optional[float] = None,
    enable_mqe: bool = False,
    mqe_expansions: int = 2,
    enable_hyde: bool = False,
    candidate_pool_multiplier: int = 4,
) -> List[Dict[str, Any]]:
    """兼容旧版增强检索调用"""

    if not query:
        return []

    if store is None:
        store = create_rag_pipeline(rag_namespace=rag_namespace or "default")

    if hasattr(store, "search"):
        return store.search(
            query=query,
            limit=top_k,
            min_score=score_threshold or 0.0,
        )

    return []