# hello_agents/memory/rag/embedding_profile.py
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import re
from typing import Mapping
from urllib.parse import urlsplit

from hello_agents.memory.rag.errors import RAGEmbeddingError


class EmbeddingFailure(RAGEmbeddingError):
    def __init__(self, code: str, *, status_code: int | None = None,
                 retryable: bool = False):
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(f"RAG embedding failed: {code}")


@dataclass(frozen=True)
class EmbeddingProfile:
    provider: str
    endpoint: str
    model: str
    revision: str
    dimension: int
    normalization: str = "l2-v1"
    document_preprocessing: str = "identity-v1"
    query_preprocessing: str = "identity-v1"
    chunking: str = "existing-rag-v1"
    distance: str = "Cosine"

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(asdict(self), sort_keys=True,
                             separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EmbeddingSettings:
    profile: EmbeddingProfile
    api_key: str = field(default="", repr=False, compare=False)
    timeout_seconds: float = 20
    max_retries: int = 2
    batch_size: int = 8

    def __post_init__(self) -> None:
        if self.profile.provider == "simple":
            return
        profile = self.profile
        # Enforce the same remote limits for direct construction and env loading.
        if (profile.provider != "siliconflow"
                or profile.endpoint != "https://api.siliconflow.cn/v1"
                or profile.model != "BAAI/bge-m3"
                or type(profile.dimension) is not int or profile.dimension != 1024
                or not isinstance(profile.revision, str)
                or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", profile.revision)
                or not isinstance(self.api_key, str) or not self.api_key
                or not self.api_key.isascii() or "${" in self.api_key
                or any(ord(ch) < 33 or ord(ch) == 127 for ch in self.api_key)
                or type(self.timeout_seconds) not in (int, float)
                or not 0 < self.timeout_seconds <= 20
                or type(self.max_retries) is not int or not 0 <= self.max_retries <= 2
                or type(self.batch_size) is not int or not 1 <= self.batch_size <= 8):
            raise EmbeddingFailure("configuration")

    @classmethod
    def from_env(cls, values: Mapping[str, str | None], *,
                 candidate: bool = False) -> EmbeddingSettings:
        def get(name, default):
            value = values.get("RAG_EMBEDDING_" + name, default)
            return str(value if value is not None else "").strip()

        provider = "siliconflow" if candidate else get("PROVIDER", "simple")
        if provider == "simple":
            return cls(EmbeddingProfile("simple", "", "SimpleEmbedding",
                                        "simple-v1", 384))
        if provider != "siliconflow":
            raise EmbeddingFailure("configuration")
        try:
            endpoint = get("BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
            parts = urlsplit(endpoint)
            if (parts.scheme != "https" or parts.hostname != "api.siliconflow.cn"
                    or parts.port not in (None, 443) or parts.username is not None
                    or parts.password is not None or parts.query or parts.fragment
                    or parts.path != "/v1" or "?" in endpoint or "#" in endpoint
                    or any(ch.isspace() for ch in endpoint)):
                raise ValueError()
            endpoint = "https://api.siliconflow.cn/v1"
            model = get("MODEL", "BAAI/bge-m3")
            dimension = int(get("DIMENSION", "1024"))
            revision = get("REVISION", "siliconflow-bge-m3-v1")
            api_key = get("API_KEY", "")
            timeout = float(get("TIMEOUT_SECONDS", "20"))
            retries = int(get("MAX_RETRIES", "2"))
            batch_size = int(get("BATCH_SIZE", "8"))
        except (TypeError, ValueError, OverflowError):
            raise EmbeddingFailure("configuration") from None
        profile = EmbeddingProfile(provider, endpoint, model, revision, dimension)
        return cls(profile, api_key, timeout, retries, batch_size)


def normalize_vector(value: object, dimension: int) -> list[float]:
    if not isinstance(value, list) or len(value) != dimension:
        raise EmbeddingFailure("response_vector")
    if any(type(item) not in (float, int) for item in value):
        raise EmbeddingFailure("response_vector")
    try:
        vector = [float(item) for item in value]
    except (ValueError, OverflowError):
        raise EmbeddingFailure("response_vector") from None
    if not all(math.isfinite(item) for item in vector):
        raise EmbeddingFailure("response_vector")
    scale = max((abs(item) for item in vector), default=0)
    if scale == 0:
        raise EmbeddingFailure("response_vector")
    scaled = [item / scale for item in vector]
    norm = math.hypot(*scaled)
    return [item / norm for item in scaled]
