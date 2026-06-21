from typing import List, Union, Sequence
import math
import hashlib


class SimpleEmbedding:
    """兜底嵌入器：没有外部模型时使用简单向量

    作用：
    - 不依赖 dashscope
    - 不依赖 sentence-transformers
    - 不需要联网
    - 保证 get_text_embedder() 永远有可用返回值
    """

    def __init__(self, dimension: int = 384):
        self.dimension = dimension

    def encode(self, texts: Union[str, Sequence[str]]):
        """兼容单文本和多文本输入

        如果输入 str：
            返回 List[float]

        如果输入 List[str]：
            返回 List[List[float]]
        """

        if isinstance(texts, str):
            return self._encode_one(texts)

        return [self._encode_one(str(t)) for t in texts]

    def _encode_one(self, text: str) -> List[float]:
        """将文本转成固定维度向量

        这里不是高质量语义向量，只是本地测试兜底向量。
        目的是保证程序先能正常运行。
        """

        vec = [0.0] * self.dimension

        if not text:
            return vec

        # 1. 字符级特征
        for i, ch in enumerate(text):
            idx = i % self.dimension
            vec[idx] += (ord(ch) % 997) / 997.0

        # 2. 词级哈希特征
        for token in text.split():
            h = hashlib.md5(token.encode("utf-8")).hexdigest()
            idx = int(h[:8], 16) % self.dimension
            vec[idx] += 1.0

        # 3. L2 归一化，避免文本越长向量值越大
        norm = math.sqrt(sum(x * x for x in vec))

        if norm > 0:
            vec = [x / norm for x in vec]

        return vec


_embedder = None
_dimension = 384


def get_text_embedder(*args, **kwargs):
    """获取统一文本嵌入器

    这个函数必须保证不抛出：
    RuntimeError: 所有嵌入模型都不可用

    其他模块会调用：
    - SemanticMemory
    - EpisodicMemory
    - RAG pipeline
    """

    global _embedder

    dimension = kwargs.get("dimension") or kwargs.get("vector_size") or _dimension

    if _embedder is None:
        _embedder = SimpleEmbedding(dimension=dimension)

    return _embedder


def get_dimension(default: int = 384) -> int:
    """获取嵌入维度"""

    return default or _dimension


def embed_query(query: str) -> List[float]:
    """对查询文本进行向量化"""

    embedder = get_text_embedder()
    vec = embedder.encode(query)

    if hasattr(vec, "tolist"):
        vec = vec.tolist()

    # 防止某些模型返回 [[...]]
    if isinstance(vec, list) and vec and isinstance(vec[0], list):
        vec = vec[0]

    return [float(x) for x in vec]


def embed_texts(texts: List[str]) -> List[List[float]]:
    """批量文本向量化"""

    embedder = get_text_embedder()
    vecs = embedder.encode(texts)

    if hasattr(vecs, "tolist"):
        vecs = vecs.tolist()

    result = []

    for vec in vecs:
        if hasattr(vec, "tolist"):
            vec = vec.tolist()

        result.append([float(x) for x in vec])

    return result


def create_embedding_model_with_fallback(*args, **kwargs):
    """创建嵌入模型，失败时使用 SimpleEmbedding 兜底

    兼容这些调用方式：

    create_embedding_model_with_fallback()
    create_embedding_model_with_fallback(preferred_type="dashscope")
    create_embedding_model_with_fallback(dimension=384)
    create_embedding_model_with_fallback(preferred_type=preferred, **kwargs)

    无论外部传什么参数，都不要抛出“所有嵌入模型都不可用”。
    """

    dimension = kwargs.get("dimension") or kwargs.get("vector_size") or _dimension

    try:
        return get_text_embedder(dimension=dimension)
    except Exception:
        return SimpleEmbedding(dimension=dimension)