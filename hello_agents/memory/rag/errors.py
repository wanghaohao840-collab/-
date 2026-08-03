from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import re


class RAGBackendError(Exception):
    """Base class for stable RAG backend errors."""


class RAGConfigError(RAGBackendError):
    """Invalid RAG backend configuration."""


class RAGConnectionError(RAGBackendError):
    """Qdrant service or network failure."""


class RAGAuthenticationError(RAGBackendError):
    """Qdrant credentials were rejected."""


class RAGCollectionError(RAGBackendError):
    """Qdrant collection is missing or incompatible."""


class RAGDocumentTooLargeError(RAGBackendError):
    """A bounded document operation exceeded its chunk limit."""


class RAGEmbeddingError(RAGBackendError):
    """An embedding does not match the configured vector space."""


class RAGOperationError(RAGBackendError):
    """Backend operation failed."""

    def __init__(
        self,
        message: str,
        *,
        operation: str = "",
        document_id: str = "",
        status_code: int | None = None,
        retryable: bool | None = None,
    ):
        self.operation = operation
        self.document_id = document_id
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(message)


_SECRET_QUERY_KEYS = {
    "api_key",
    "apikey",
    "key",
    "token",
    "access_token",
    "authorization",
    "auth",
    "password",
    "passwd",
    "secret",
    "client_secret",
}
_SECRET_KEY_PATTERN = "|".join(
    re.escape(key) for key in sorted(_SECRET_QUERY_KEYS, key=len, reverse=True)
)
_QUOTED_SECRET_ASSIGNMENT_RE = re.compile(
    rf"((?:[\"']?(?:{_SECRET_KEY_PATTERN})[\"']?)\s*[=:]\s*)"
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')",
    flags=re.IGNORECASE,
)


def sanitize_qdrant_url(url: str) -> str:
    if not url:
        return ""

    parts = urlsplit(str(url))
    host = parts.hostname or ""
    netloc = host
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"

    safe_pairs = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        safe_pairs.append((key, "***" if key.lower() in _SECRET_QUERY_KEYS else value))

    return urlunsplit((parts.scheme, netloc, parts.path, urlencode(safe_pairs, safe="*"), parts.fragment))


def sanitize_error_message(message: object, secrets: tuple[str, ...] = ()) -> str:
    text = str(message or "")
    text = re.sub(
        r"([a-z][a-z0-9+.-]*://)(?:[^/@\s]+@)",
        r"\1***@",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(Authorization\s*[:=]\s*)(?:Bearer\s+)?([^\s,;]+)",
        r"\1***",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\b(Bearer\s+)([^\s,;]+)", r"\1***", text, flags=re.IGNORECASE)
    text = _QUOTED_SECRET_ASSIGNMENT_RE.sub(r"\1***", text)
    for key in _SECRET_QUERY_KEYS:
        text = re.sub(
            rf"((?:[\"']?{re.escape(key)}[\"']?)\s*[=:]\s*[\"']?)([^\"'&,;\s}}\]]+)",
            rf"\1***",
            text,
            flags=re.IGNORECASE,
        )
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "***")
    return text
