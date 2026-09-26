"""Immutable, tenant-scoped S3 artifacts; PostgreSQL owns their published references."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from botocore.exceptions import ClientError


_KINDS = {
    "imports": {".pdf", ".txt", ".md", ".markdown", ".docx"},
    "documents": {".pdf", ".txt", ".md", ".markdown", ".docx"},
    "reports": {".md", ".docx"},
}
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class ObjectIntegrityError(ValueError):
    """An artifact's identity or bytes differ from its authoritative reference."""


def _uuid(value: str) -> str:
    if str(UUID(value)) != value:
        raise ValueError("artifact IDs must be canonical UUIDs")
    return value


def artifact_key(user_id: str, kind: str, artifact_id: str, suffix: str, sha256: str) -> str:
    if kind not in _KINDS or suffix not in _KINDS[kind]:
        raise ValueError("unsupported artifact kind or suffix")
    if not _DIGEST.fullmatch(sha256):
        raise ValueError("artifact SHA-256 is invalid")
    return f"users/{_uuid(user_id)}/{kind}/{_uuid(artifact_id)}/{sha256}{suffix}"


def _validate_key(user_id: str, key: str) -> str:
    parts = key.split("/")
    if len(parts) != 5 or parts[0] != "users" or parts[1] != _uuid(user_id):
        raise ValueError("artifact key is outside the user scope")
    digest, suffix = parts[4][:64], parts[4][64:]
    if artifact_key(user_id, parts[2], parts[3], suffix, digest) != key:
        raise ValueError("artifact key is invalid")
    return digest


@dataclass(frozen=True)
class ObjectRef:
    key: str
    sha256: str
    size_bytes: int
    version_id: str


@dataclass(frozen=True)
class ObjectWrite:
    ref: ObjectRef
    created: bool


class ObjectStore(Protocol):
    def put_immutable(self, user_id: str, key: str, content: bytes) -> ObjectWrite: ...
    def read_verified(self, user_id: str, ref: ObjectRef) -> bytes: ...


class S3ObjectStore:
    def __init__(self, client: Any, bucket: str) -> None:
        if not bucket:
            raise ValueError("object bucket is required")
        self.client = client
        self.bucket = bucket

    def check_ready(self) -> None:
        self.client.head_bucket(Bucket=self.bucket)
        versioning = self.client.get_bucket_versioning(Bucket=self.bucket)
        if versioning.get("Status") != "Enabled":
            raise RuntimeError("object bucket versioning must be enabled")

    def put_immutable(self, user_id: str, key: str, content: bytes) -> ObjectWrite:
        expected = _validate_key(user_id, key)
        digest = hashlib.sha256(content).hexdigest()
        if digest != expected:
            raise ObjectIntegrityError("artifact key does not match content SHA-256")
        try:
            result = self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ContentLength=len(content),
                Metadata={"sha256": digest},
                IfNoneMatch="*",
            )
        except ClientError as error:
            if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
                raise
            existing = self.client.head_object(Bucket=self.bucket, Key=key)
            ref = self._reference(key, digest, len(content), existing)
            if self.read_verified(user_id, ref) != content:
                raise ObjectIntegrityError("existing artifact content conflicts")
            return ObjectWrite(ref=ref, created=False)
        return ObjectWrite(
            ref=self._reference(key, digest, len(content), result), created=True
        )

    @staticmethod
    def _reference(key: str, digest: str, size: int, response: dict) -> ObjectRef:
        version_id = response.get("VersionId")
        if not version_id or version_id == "null":
            raise ObjectIntegrityError("object store did not return a retained version")
        return ObjectRef(key=key, sha256=digest, size_bytes=size, version_id=version_id)

    def read_verified(self, user_id: str, ref: ObjectRef) -> bytes:
        if _validate_key(user_id, ref.key) != ref.sha256 or ref.size_bytes < 0:
            raise ObjectIntegrityError("artifact reference is invalid")
        if not ref.version_id or ref.version_id == "null":
            raise ObjectIntegrityError("artifact reference has no retained version")
        result = self.client.get_object(
            Bucket=self.bucket, Key=ref.key, VersionId=ref.version_id
        )
        body = result["Body"]
        try:
            content = body.read(ref.size_bytes + 1)
        finally:
            body.close()
        if len(content) != ref.size_bytes or hashlib.sha256(content).hexdigest() != ref.sha256:
            raise ObjectIntegrityError("artifact bytes do not match the reference")
        return content
