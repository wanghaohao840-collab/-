from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.storage import read_json, write_json_atomic


EMPTY_HISTORY = {"documents": [], "questions": [], "notes": [], "sessions": []}

class CorruptHistoryError(RuntimeError):
    """Raised when persisted history cannot be safely read."""


class HistoryRepository:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        try:
            data = read_json(self.path, default=deepcopy(EMPTY_HISTORY))
        except Exception as exc:
            raise CorruptHistoryError(
                f"History is corrupt and was not modified: {self.path}"
            ) from exc
        if not isinstance(data, dict):
            raise CorruptHistoryError(
                f"History has an invalid root value and was not modified: {self.path}"
            )
        for key, value in EMPTY_HISTORY.items():
            current = data.setdefault(key, deepcopy(value))
            if not isinstance(current, list):
                raise CorruptHistoryError(
                    f"History field {key!r} is invalid and was not modified: {self.path}"
                )
        return data

    def save(self, data: dict[str, Any]) -> None:
        for key, value in EMPTY_HISTORY.items():
            data.setdefault(key, deepcopy(value))
        write_json_atomic(self.path, data)

    def quarantine_and_reset(self) -> Path:
        """Quarantine the active file and atomically replace with clean state.

        The quarantine is failure-atomic: the active content is *copied*
        to a backup path first, then the clean replacement is written
        atomically.  If the write step fails the original active file is
        untouched.  Backup names include a UUID component so two
        quarantines in the same second never collide.
        """
        import shutil

        if not self.path.exists():
            raise FileNotFoundError(self.path)
        backup = self.path.with_name(
            f"{self.path.name}.corrupt-"
            f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
            f"{uuid.uuid4().hex[:8]}"
        )
        # 1. Stage backup durably before touching active.
        shutil.copy2(self.path, backup)
        try:
            # 2. Atomically write clean replacement.
            self.save(deepcopy(EMPTY_HISTORY))
        except Exception:
            # Roll back: remove the staged backup so we don't leave
            # a stranded file.  The active file is still intact.
            backup.unlink(missing_ok=True)
            raise
        return backup

    def restore(self, backup: Path | str) -> None:
        backup_repo = HistoryRepository(backup)
        data = backup_repo.load()
        # validate_schema is already called inside load(); this call
        # is explicit so that callers who bypass load() still get
        # validation before the atomic write.
        self.validate_schema(data)
        self.save(data)

    @staticmethod
    def validate_schema(data: Any) -> None:
        """Raise :exc:`CorruptHistoryError` if *data* does not conform
        to the required History schema.

        Callers should validate a candidate payload before calling
        :meth:`save` so that an invalid backup never replaces the
        active file.
        """
        if not isinstance(data, dict):
            raise CorruptHistoryError(
                "History root value must be a dict"
            )
        for key, value in EMPTY_HISTORY.items():
            if not isinstance(data.get(key), list):
                raise CorruptHistoryError(
                    f"History field {key!r} is missing or not a list"
                )

    def add_document(self, item: dict[str, Any]) -> None:
        self.update(lambda data: data["documents"].append(item))

    def upsert_document(self, item: dict[str, Any]) -> dict[str, Any]:
        document_id = str(item["document_id"])

        def mutate(data: dict[str, Any]) -> None:
            for index, current in enumerate(data["documents"]):
                if str(current.get("document_id", "")) == document_id:
                    data["documents"][index] = dict(item)
                    return
            data["documents"].append(dict(item))

        return self.update(mutate)

    def add_question(self, item: dict[str, Any]) -> None:
        self.update(lambda data: data["questions"].append(item))

    def add_note(self, item: dict[str, Any]) -> None:
        self.update(lambda data: data["notes"].append(item))

    def update(self, mutation) -> dict[str, Any]:
        """Reload, mutate and atomically persist the latest snapshot."""
        data = self.load()
        mutation(data)
        self.save(data)
        return data

    def clear_notes(self) -> int:
        data = self.load()
        removed = len(data["notes"])
        data["notes"] = []
        self.save(data)
        return removed

    def delete_document(self, document_id: str) -> tuple[int, int]:
        data = self.load()
        old_documents = data["documents"]
        old_questions = data["questions"]
        data["documents"] = [
            item for item in old_documents if item.get("document_id") != document_id
        ]
        data["questions"] = [
            item
            for item in old_questions
            if item.get("document_id") != document_id
            and document_id not in (item.get("document_ids") or [])
        ]
        self.save(data)
        return len(old_documents) - len(data["documents"]), len(old_questions) - len(data["questions"])

    def clear_documents(self) -> tuple[int, int]:
        data = self.load()
        removed_documents = len(data["documents"])
        removed_questions = len(data["questions"])
        data["documents"] = []
        data["questions"] = []
        self.save(data)
        return removed_documents, removed_questions
