"""Journaled registry activation for embedding-index cutovers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import uuid

from hello_agents.memory.rag.index_registry import (
    IndexRecord,
    IndexRegistry,
    IndexRegistryError,
    registry_key,
)
from hello_agents.memory.rag.source_records import canonical, digest


_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_STATES = {"prepared", "registry_written", "complete", "rolled_back"}


class CutoverFailure(IndexRegistryError):
    pass


@dataclass(frozen=True)
class CutoverJournal:
    schema_version: int
    migration_id: str
    key: str
    previous: dict[str, object] | None
    candidate: dict[str, object]
    state: str
    created_at: str
    updated_at: str
    journal_digest: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _journal_path(registry: IndexRegistry, migration_id: str) -> Path:
    return registry.registry_root / "cutovers" / f"{migration_id}.json"


def _write(path: Path, body: dict[str, object], *, replace_existing: bool) -> CutoverJournal:
    body["journal_digest"] = digest(["index-cutover-v1", body])
    journal = CutoverJournal(**body)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical(asdict(journal)))
            stream.flush()
            os.fsync(stream.fileno())
        if not replace_existing and path.exists():
            raise CutoverFailure("journal_exists")
        os.replace(temporary, path)
    except CutoverFailure:
        raise
    except (OSError, UnicodeError):
        raise CutoverFailure("journal_write") from None
    finally:
        temporary.unlink(missing_ok=True)
    return journal


def inspect_cutover(registry: IndexRegistry, migration_id: str) -> CutoverJournal:
    if not _ID.fullmatch(migration_id or ""):
        raise CutoverFailure("journal")
    try:
        raw = json.loads(_journal_path(registry, migration_id).read_text(encoding="utf-8"))
        journal = CutoverJournal(**raw)
        body = dict(raw)
        saved = body.pop("journal_digest")
        previous = None if journal.previous is None else IndexRecord.from_dict(journal.previous)
        candidate = IndexRecord.from_dict(journal.candidate)
        if (
            journal.schema_version != 1
            or journal.state not in _STATES
            or journal.migration_id != migration_id
            or journal.key != registry_key(candidate.identity)
            or candidate.state != "active"
            or (previous is not None and registry_key(previous.identity) != journal.key)
            or saved != digest(["index-cutover-v1", body])
        ):
            raise ValueError()
        return journal
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise CutoverFailure("journal") from None


def begin_cutover(registry: IndexRegistry, candidate: IndexRecord) -> CutoverJournal:
    if not isinstance(candidate, IndexRecord) or candidate.state != "active":
        raise CutoverFailure("candidate")
    key = registry_key(candidate.identity)
    entries = registry.load() if registry.path.exists() else {}
    previous = entries.get(key)
    if previous == candidate:
        raise CutoverFailure("already_active")
    stamp = _now()
    return _write(
        _journal_path(registry, candidate.migration_id),
        {
            "schema_version": 1,
            "migration_id": candidate.migration_id,
            "key": key,
            "previous": None if previous is None else previous.to_dict(),
            "candidate": candidate.to_dict(),
            "state": "prepared",
            "created_at": stamp,
            "updated_at": stamp,
        },
        replace_existing=False,
    )


def apply_cutover(registry: IndexRegistry, migration_id: str) -> CutoverJournal:
    journal = inspect_cutover(registry, migration_id)
    if journal.state == "registry_written":
        registry.require_active(IndexRecord.from_dict(journal.candidate).identity)
        return journal
    if journal.state != "prepared":
        raise CutoverFailure("transition")
    previous = None if journal.previous is None else IndexRecord.from_dict(journal.previous)
    candidate = IndexRecord.from_dict(journal.candidate)
    entries = registry.load() if registry.path.exists() else {}
    current = entries.get(journal.key)
    if current == candidate:
        pass  # Recovery after registry replace but before journal replace.
    else:
        registry.replace_if_current(
            journal.key,
            expected=previous,
            replacement=candidate,
        )
    body = asdict(journal)
    body.pop("journal_digest")
    body.update(state="registry_written", updated_at=_now())
    return _write(_journal_path(registry, migration_id), body, replace_existing=True)


def complete_cutover(registry: IndexRegistry, migration_id: str) -> CutoverJournal:
    journal = inspect_cutover(registry, migration_id)
    if journal.state == "complete":
        return journal
    if journal.state != "registry_written":
        raise CutoverFailure("transition")
    candidate = IndexRecord.from_dict(journal.candidate)
    registry.require_active(candidate.identity)
    body = asdict(journal)
    body.pop("journal_digest")
    body.update(state="complete", updated_at=_now())
    return _write(_journal_path(registry, migration_id), body, replace_existing=True)


def rollback_cutover(registry: IndexRegistry, migration_id: str) -> CutoverJournal:
    journal = inspect_cutover(registry, migration_id)
    if journal.state == "rolled_back":
        return journal
    if journal.state == "complete":
        raise CutoverFailure("rollback_requires_data_check")
    previous = None if journal.previous is None else IndexRecord.from_dict(journal.previous)
    candidate = IndexRecord.from_dict(journal.candidate)
    entries = registry.load() if registry.path.exists() else {}
    current = entries.get(journal.key)
    if current == candidate:
        registry.replace_if_current(
            journal.key,
            expected=candidate,
            replacement=previous,
        )
    elif current != previous:
        raise CutoverFailure("concurrent_change")
    body = asdict(journal)
    body.pop("journal_digest")
    body.update(state="rolled_back", updated_at=_now())
    return _write(_journal_path(registry, migration_id), body, replace_existing=True)


def require_no_incomplete_cutover(registry: IndexRegistry) -> None:
    root = registry.registry_root / "cutovers"
    if not root.exists():
        return
    try:
        paths = sorted(root.glob("*.json"))
    except OSError:
        raise CutoverFailure("journal") from None
    for path in paths:
        journal = inspect_cutover(registry, path.stem)
        if journal.state not in {"complete", "rolled_back"}:
            raise CutoverFailure("incomplete")
