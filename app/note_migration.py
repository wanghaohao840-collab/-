from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.database import connect
from app.note_models import NoteMigrationResult, NoteValidationError
from app.note_repository import NoteRepository, utc_now
from app.runtime import UserRuntimeRegistry
from app.storage import UserStorage


logger = logging.getLogger(__name__)


class NoteMigrationService:
    MIGRATION_VERSION = 1

    def __init__(
        self,
        db_path: Path | str,
        storage: UserStorage,
        repository: NoteRepository,
        runtime_registry: UserRuntimeRegistry,
    ) -> None:
        self.db_path = Path(db_path)
        self.storage = storage
        self.repository = repository
        self.runtime_registry = runtime_registry

    def ensure_user_migrated(self, user_id: str) -> NoteMigrationResult:
        runtime = self.runtime_registry.get_or_create(user_id)
        with runtime.lock:
            history = runtime.history.load()
            raw_notes = history.get("notes", [])
            if not isinstance(raw_notes, list):
                raw_notes = []
            memory_items = _legacy_memory_items(runtime)
            available_memory_ids = set(memory_items)
            occurrences: dict[str, int] = defaultdict(int)
            imported = 0
            skipped = 0
            matched = 0
            conn = connect(self.db_path)
            try:
                conn.execute("begin immediate")
                already_matched = {
                    row["legacy_memory_id"]
                    for row in conn.execute(
                        "select legacy_memory_id from note_legacy_imports where user_id=? and legacy_memory_id is not null",
                        (user_id,),
                    )
                }
                available_memory_ids.difference_update(already_matched)
                for raw in raw_notes:
                    canonical = _canonical_legacy_record(raw)
                    if canonical is None:
                        skipped += 1
                        continue
                    occurrence = occurrences[canonical]
                    occurrences[canonical] += 1
                    source_digest = hashlib.sha256(canonical.encode()).hexdigest()
                    legacy_key = hashlib.sha256(
                        f"v{self.MIGRATION_VERSION}:{canonical}:{occurrence}".encode()
                    ).hexdigest()
                    existing = conn.execute(
                        "select 1 from note_legacy_imports where user_id=? and legacy_import_key=?",
                        (user_id, legacy_key),
                    ).fetchone()
                    if existing is not None:
                        skipped += 1
                        continue
                    value = json.loads(canonical)
                    note_id = _legacy_note_id(user_id, legacy_key)
                    memory_id = _find_exact_memory(
                        value, memory_items, available_memory_ids
                    )
                    timestamp = _legacy_timestamp(value.get("created_at"))
                    try:
                        self.repository.create_in_transaction(
                            conn,
                            user_id,
                            str(value.get("note") or ""),
                            str(value.get("concept") or "") or None,
                            (),
                            f"legacy:{legacy_key}",
                            note_id=note_id,
                            now=timestamp,
                        )
                    except NoteValidationError:
                        skipped += 1
                        continue
                    conn.execute(
                        """
                        insert into note_legacy_imports (
                            user_id,legacy_import_key,note_id,source_digest,
                            legacy_memory_id,imported_at
                        ) values (?,?,?,?,?,?)
                        """,
                        (user_id, legacy_key, note_id, source_digest, memory_id, utc_now()),
                    )
                    imported += 1
                    if memory_id is not None:
                        matched += 1
                        available_memory_ids.remove(memory_id)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            unmatched = len(available_memory_ids)
            if unmatched:
                logger.warning(
                    "Learning-note migration left %d unmatched semantic memories for one user",
                    unmatched,
                )
            return NoteMigrationResult(imported, skipped, matched, unmatched)

    def migrate_known_users(self) -> tuple[tuple[str, NoteMigrationResult], ...]:
        if not self.storage.users_root.is_dir():
            return ()
        results: list[tuple[str, NoteMigrationResult]] = []
        for directory in sorted(self.storage.users_root.iterdir(), key=lambda item: item.name):
            if not directory.is_dir():
                continue
            with connect(self.db_path) as conn:
                exists = conn.execute(
                    "select 1 from users where id=?", (directory.name,)
                ).fetchone()
            if exists is not None:
                results.append((directory.name, self.ensure_user_migrated(directory.name)))
        return tuple(results)


def _canonical_legacy_record(raw: object) -> str | None:
    if not isinstance(raw, dict):
        return None
    note = raw.get("note")
    if not isinstance(note, str) or not note.strip():
        return None
    value = {
        "note": note,
        "content": raw.get("content") if isinstance(raw.get("content"), str) else note,
        "concept": raw.get("concept") if isinstance(raw.get("concept"), str) else "",
        "session_id": raw.get("session_id") if isinstance(raw.get("session_id"), str) else "",
        "created_at": raw.get("created_at") if isinstance(raw.get("created_at"), str) else "",
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _legacy_note_id(user_id: str, legacy_key: str) -> str:
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, f"learning-notes:{user_id}")
    return str(uuid.uuid5(namespace, legacy_key))


def _legacy_timestamp(value: object) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError):
        return utc_now()


def _legacy_memory_items(runtime: object) -> dict[str, object]:
    manager = getattr(getattr(runtime, "memory_tool", None), "memory_manager", None)
    semantic = getattr(manager, "memory_types", {}).get("semantic") if manager else None
    memories = getattr(semantic, "memories", {}) if semantic is not None else {}
    return {
        str(memory_id): item
        for memory_id, item in memories.items()
        if getattr(item, "metadata", {}).get("knowledge_type") == "learning_note"
    }


def _find_exact_memory(
    legacy: dict[str, object],
    memories: dict[str, object],
    available_ids: set[str],
) -> str | None:
    for memory_id in sorted(available_ids):
        item = memories[memory_id]
        metadata = getattr(item, "metadata", {}) or {}
        if (
            getattr(item, "content", None) == legacy.get("content")
            and str(metadata.get("concept", "")) == str(legacy.get("concept", ""))
            and str(metadata.get("session_id", "")) == str(legacy.get("session_id", ""))
        ):
            return memory_id
    return None
