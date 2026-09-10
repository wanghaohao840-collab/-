from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import re
from typing import Any

from hello_agents.memory.rag.embedding_profile import EmbeddingProfile
from hello_agents.memory.rag.errors import RAGConfigError

_NAME = re.compile(r"[A-Za-z0-9_-]+")
_PROFILE_FIELDS = {field.name for field in fields(EmbeddingProfile)}
_IDENTITY_FIELDS = {"schema_version", "backend", "base_collection",
                    "physical_collection", "fingerprint", "profile"}


class IndexIdentityError(RAGConfigError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"RAG index identity rejected: {code}")


def validate_profile(profile: EmbeddingProfile) -> None:
    if type(profile.dimension) is not int or not 1 <= profile.dimension <= 65536:
        raise IndexIdentityError("profile")
    for name, value in asdict(profile).items():
        if name == "dimension":
            continue
        if (not isinstance(value, str) or len(value) > 256
                or (name != "endpoint" and not value)
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
            raise IndexIdentityError("profile")
    if profile.distance != "Cosine":
        raise IndexIdentityError("profile")


def physical_collection_name(base: str, fingerprint: str) -> str:
    if (not isinstance(base, str) or not _NAME.fullmatch(base)
            or not isinstance(fingerprint, str)
            or not re.fullmatch(r"[a-f0-9]{64}", fingerprint)):
        raise IndexIdentityError("name")
    name = base + "__" + fingerprint[:16]
    if len(name) > 255:
        raise IndexIdentityError("name")
    return name


@dataclass(frozen=True)
class IndexIdentity:
    """Serializable identity, not proof of a physical store's provenance.

    A loader must compare this against trusted runtime configuration and verify
    the physical collection before granting reads or writes.
    """

    backend: str
    base_collection: str
    profile: EmbeddingProfile

    def __post_init__(self) -> None:
        if not isinstance(self.backend, str) or self.backend not in {"json", "qdrant"}:
            raise IndexIdentityError("backend")
        validate_profile(self.profile)
        physical_collection_name(self.base_collection, self.profile.fingerprint)

    @property
    def physical_collection(self) -> str:
        return physical_collection_name(self.base_collection, self.profile.fingerprint)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "backend": self.backend,
            "base_collection": self.base_collection,
            "physical_collection": self.physical_collection,
            "fingerprint": self.profile.fingerprint,
            "profile": asdict(self.profile),
        }

    @classmethod
    def from_dict(cls, data: object) -> IndexIdentity:
        try:
            if (not isinstance(data, dict) or set(data) != _IDENTITY_FIELDS
                    or type(data["schema_version"]) is not int
                    or data["schema_version"] != 1
                    or not isinstance(data["profile"], dict)
                    or set(data["profile"]) != _PROFILE_FIELDS):
                raise IndexIdentityError("schema")
            result = cls(data["backend"], data["base_collection"],
                         EmbeddingProfile(**data["profile"]))
            if (data["fingerprint"] != result.profile.fingerprint
                    or data["physical_collection"] != result.physical_collection):
                raise IndexIdentityError("fingerprint")
            return result
        except (TypeError, ValueError, KeyError, AttributeError):
            raise IndexIdentityError("schema") from None

    def require_match(self, expected: IndexIdentity) -> None:
        # Compare full profiles, not merely dimensions or shortened names.
        if self != expected:
            raise IndexIdentityError("mismatch")


def require_point_identity(
    metadata: object, *, identity: IndexIdentity, rag_namespace: str,
    document_id: str | None = None,
) -> None:
    if (not isinstance(rag_namespace, str) or not rag_namespace
            or not isinstance(metadata, dict)
            or metadata.get("embedding_fingerprint") != identity.profile.fingerprint
            or metadata.get("rag_namespace") != rag_namespace
            or not isinstance(metadata.get("document_id"), str)
            or not metadata["document_id"]
            or (document_id is not None and metadata["document_id"] != document_id)):
        raise IndexIdentityError("point")
