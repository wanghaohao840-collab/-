from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
from threading import RLock
from typing import Iterable
import uuid

from hello_agents.memory.rag.errors import RAGConfigError
from hello_agents.memory.rag.index_identity import (
    IndexIdentity, IndexIdentityError,
)

_RECORD_FIELDS = {
    "identity", "source_index", "migration_id", "validation", "state",
}
_VALIDATION_FIELDS = {
    "passed", "checked_at", "chunk_count", "content_digest",
    "scope_leaks", "recall_at_5", "mrr_at_5",
}
_DOCUMENT_FIELDS = {"schema_version", "entries"}
_STATES = {"planned", "building", "validated", "active", "failed"}
_MIGRATION_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_HEX_DIGEST = re.compile(r"[a-f0-9]{64}")
_LOCK = RLock()


class IndexRegistryError(RAGConfigError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"RAG index registry rejected: {code}")


def _safe_text(value: object, *, maximum: int, empty: bool = False) -> bool:
    return (
        isinstance(value, str)
        and (empty or bool(value))
        and len(value) <= maximum
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _score(value: object) -> float | None:
    if value is None:
        return None
    if type(value) not in (int, float):
        raise IndexRegistryError("validation")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise IndexRegistryError("validation")
    return result


@dataclass(frozen=True)
class IndexValidation:
    passed: bool
    checked_at: str
    chunk_count: int
    content_digest: str
    scope_leaks: int
    recall_at_5: float | None = None
    mrr_at_5: float | None = None

    def __post_init__(self) -> None:
        try:
            checked_at = datetime.fromisoformat(
                self.checked_at.replace("Z", "+00:00")
            )
            valid_timestamp = (
                self.checked_at.endswith("Z")
                and checked_at.tzinfo is not None
                and checked_at.utcoffset() == timezone.utc.utcoffset(checked_at)
            )
        except (AttributeError, TypeError, ValueError, OverflowError):
            valid_timestamp = False
        if (
            type(self.passed) is not bool
            or not _safe_text(self.checked_at, maximum=64)
            or not valid_timestamp
            or type(self.chunk_count) is not int
            or self.chunk_count < 0
            or not isinstance(self.content_digest, str)
            or not _HEX_DIGEST.fullmatch(self.content_digest)
            or type(self.scope_leaks) is not int
            or self.scope_leaks < 0
        ):
            raise IndexRegistryError("validation")
        object.__setattr__(self, "recall_at_5", _score(self.recall_at_5))
        object.__setattr__(self, "mrr_at_5", _score(self.mrr_at_5))

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "checked_at": self.checked_at,
            "chunk_count": self.chunk_count,
            "content_digest": self.content_digest,
            "scope_leaks": self.scope_leaks,
            "recall_at_5": self.recall_at_5,
            "mrr_at_5": self.mrr_at_5,
        }

    @classmethod
    def from_dict(cls, value: object) -> "IndexValidation":
        try:
            if not isinstance(value, dict) or set(value) != _VALIDATION_FIELDS:
                raise IndexRegistryError("validation")
            return cls(**value)
        except (TypeError, ValueError, KeyError):
            raise IndexRegistryError("validation") from None


@dataclass(frozen=True)
class IndexRecord:
    identity: IndexIdentity
    source_index: str | None
    migration_id: str
    validation: IndexValidation | None
    state: str

    def __post_init__(self) -> None:
        if not isinstance(self.identity, IndexIdentity):
            raise IndexRegistryError("record")
        if (
            self.source_index is not None
            and not _safe_text(self.source_index, maximum=512)
        ):
            raise IndexRegistryError("record")
        if (
            not isinstance(self.migration_id, str)
            or not _MIGRATION_ID.fullmatch(self.migration_id)
            or self.state not in _STATES
            or (
                self.validation is not None
                and not isinstance(self.validation, IndexValidation)
            )
        ):
            raise IndexRegistryError("record")
        if self.state in {"validated", "active"} and (
            self.validation is None
            or not self.validation.passed
            or self.validation.scope_leaks != 0
        ):
            raise IndexRegistryError("unvalidated")
        if (
            self.state == "active"
            and self.identity.profile.provider != "simple"
            and self.validation is not None
            and self.validation.chunk_count > 0
            and (
                self.validation.recall_at_5 is None
                or self.validation.recall_at_5 < 0.90
                or self.validation.mrr_at_5 is None
                or self.validation.mrr_at_5 < 0.75
            )
        ):
            raise IndexRegistryError("quality")

    def to_dict(self) -> dict[str, object]:
        return {
            "identity": self.identity.to_dict(),
            "source_index": self.source_index,
            "migration_id": self.migration_id,
            "validation": (
                None if self.validation is None else self.validation.to_dict()
            ),
            "state": self.state,
        }

    @classmethod
    def from_dict(cls, value: object) -> "IndexRecord":
        try:
            if not isinstance(value, dict) or set(value) != _RECORD_FIELDS:
                raise IndexRegistryError("record")
            validation = value["validation"]
            return cls(
                identity=IndexIdentity.from_dict(value["identity"]),
                source_index=value["source_index"],
                migration_id=value["migration_id"],
                validation=(
                    None if validation is None
                    else IndexValidation.from_dict(validation)
                ),
                state=value["state"],
            )
        except (TypeError, ValueError, KeyError, IndexIdentityError):
            raise IndexRegistryError("record") from None


def registry_key(identity: IndexIdentity) -> str:
    return f"{identity.backend}:{identity.base_collection}"


class IndexRegistry:
    def __init__(self, data_root: Path | str):
        root = Path(data_root)
        if not root.is_absolute():
            raise IndexRegistryError("data_root")
        self.root = root.resolve()
        self.registry_root = self.root / "vector_indexes" / "rag"
        self.path = self.registry_root / "registry.json"

    def load(self) -> dict[str, IndexRecord]:
        with _LOCK:
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                raise IndexRegistryError("missing") from None
            except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
                raise IndexRegistryError("unreadable") from None
            return self._decode(value)

    def save(self, records: Iterable[IndexRecord]) -> None:
        with _LOCK:
            entries: dict[str, IndexRecord] = {}
            try:
                for record in records:
                    if not isinstance(record, IndexRecord):
                        raise IndexRegistryError("record")
                    key = registry_key(record.identity)
                    if key in entries:
                        raise IndexRegistryError("duplicate")
                    entries[key] = record
            except TypeError:
                raise IndexRegistryError("record") from None
            self._save_unlocked(entries)

    def put(self, record: IndexRecord) -> None:
        if not isinstance(record, IndexRecord):
            raise IndexRegistryError("record")
        with _LOCK:
            entries = self.load() if self.path.exists() else {}
            entries[registry_key(record.identity)] = record
            self._save_unlocked(entries)

    def replace_if_current(
        self,
        key: str,
        *,
        expected: IndexRecord | None,
        replacement: IndexRecord | None,
    ) -> None:
        """Conditionally replace one logical index without touching peers."""
        if not isinstance(key, str) or not key:
            raise IndexRegistryError("record")
        if expected is not None and not isinstance(expected, IndexRecord):
            raise IndexRegistryError("record")
        if replacement is not None and (
            not isinstance(replacement, IndexRecord) or registry_key(replacement.identity) != key
        ):
            raise IndexRegistryError("record")
        with _LOCK:
            entries = self.load() if self.path.exists() else {}
            if entries.get(key) != expected:
                raise IndexRegistryError("concurrent_change")
            if replacement is None:
                entries.pop(key, None)
            else:
                entries[key] = replacement
            self._save_unlocked(entries)

    def require_active(self, expected: IndexIdentity) -> IndexRecord:
        if not isinstance(expected, IndexIdentity):
            raise IndexRegistryError("identity")
        record = self.load().get(registry_key(expected))
        if record is None:
            raise IndexRegistryError("entry_missing")
        if record.state != "active":
            raise IndexRegistryError("inactive")
        try:
            record.identity.require_match(expected)
        except IndexIdentityError:
            raise IndexRegistryError("identity") from None
        return record

    def mark_failed_if_active(self, expected: IndexIdentity) -> bool:
        """Invalidate only this identity; never clobber a newer cutover record."""
        with _LOCK:
            entries = self.load()
            key = registry_key(expected)
            record = entries.get(key)
            if record is None or record.identity != expected or record.state != "active":
                return False
            entries[key] = replace(record, state="failed")
            self._save_unlocked(entries)
            return True

    @staticmethod
    def _decode(value: object) -> dict[str, IndexRecord]:
        if (
            not isinstance(value, dict)
            or set(value) != _DOCUMENT_FIELDS
            or type(value.get("schema_version")) is not int
            or value["schema_version"] != 1
            or not isinstance(value.get("entries"), dict)
        ):
            raise IndexRegistryError("schema")
        result: dict[str, IndexRecord] = {}
        for key, raw_record in value["entries"].items():
            record = IndexRecord.from_dict(raw_record)
            if key != registry_key(record.identity) or key in result:
                raise IndexRegistryError("schema")
            result[key] = record
        return result

    def _save_unlocked(self, records: dict[str, IndexRecord]) -> None:
        document = {
            "schema_version": 1,
            "entries": {
                key: records[key].to_dict() for key in sorted(records)
            },
        }
        encoded = json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        self.registry_root.mkdir(parents=True, exist_ok=True)
        temporary = self.registry_root / (
            f".{self.path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except (OSError, UnicodeError):
            raise IndexRegistryError("write") from None
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
