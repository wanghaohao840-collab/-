from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


class DocumentNotFoundError(LookupError):
    def __init__(self) -> None:
        super().__init__("document was not found")


class DocumentImportActiveError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("document import is active")


class DocumentDeleteFailedError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("document deletion failed")


@dataclass(frozen=True)
class DocumentLibraryItem:
    document_id: str
    name: str
    file_suffix: str
    size_bytes: int | None
    loaded_at: str | None
    status: Literal["ready"] = "ready"


class DocumentLibraryService:
    def __init__(self, session_registry, storage, import_service) -> None:
        self.session_registry = session_registry
        self.storage = storage
        self.import_service = import_service

    def list_documents(self, session_token: str) -> tuple[DocumentLibraryItem, ...]:
        session = self.session_registry.get_session(session_token)
        user_id = str(session.user_id)
        with session.runtime.lock:
            history = session.runtime.history.load()
            latest = self._latest_records(history.get("documents", []))
            items = [
                item
                for record in latest.values()
                if (item := self._project_record(user_id, record)) is not None
            ]

        dated = sorted(
            (item for item in items if item.loaded_at is not None),
            key=lambda item: (
                self._parse_loaded_at(item.loaded_at),
                item.document_id,
            ),
            reverse=True,
        )
        undated = sorted(
            (item for item in items if item.loaded_at is None),
            key=lambda item: item.document_id,
        )
        return tuple((*dated, *undated))

    def delete_document(self, session_token: str, document_id: str) -> None:
        session = self.session_registry.get_session(session_token)
        user_id = str(session.user_id)
        with session.runtime.lock:
            history = session.runtime.history.load()
            records = history.get("documents", [])
            record = self._latest_records(records).get(document_id)
            if record is None or self._project_record(user_id, record) is None:
                raise DocumentNotFoundError()
            try:
                for related in records:
                    if (
                        isinstance(related, dict)
                        and related.get("document_id") == document_id
                    ):
                        raw_path = related["document_path"]
                        if not isinstance(raw_path, str) or not raw_path.strip():
                            raise ValueError("invalid document source path")
                        self._safe_document_path(user_id, raw_path)
            except (KeyError, OSError, TypeError, ValueError):
                logger.warning("document deletion source preflight failed")
                raise DocumentDeleteFailedError() from None
            if self.import_service.has_active_task_for_document(
                user_id, document_id
            ):
                raise DocumentImportActiveError()
            try:
                result = session.assistant.delete_document(document_id)
            except Exception:
                logger.warning("coordinated document deletion failed")
                raise DocumentDeleteFailedError() from None
            if (
                getattr(result, "document_id", None) != document_id
                or not isinstance(getattr(result, "documents_removed", None), int)
                or result.documents_removed < 1
                or getattr(result, "skipped_source_files", None) != 0
            ):
                logger.warning("coordinated document deletion was incomplete")
                raise DocumentDeleteFailedError()

        try:
            self.session_registry.clear_document_selection(user_id, document_id)
        except Exception:
            logger.warning("document selection invalidation failed")
            raise DocumentDeleteFailedError() from None

    @staticmethod
    def _latest_records(records: object) -> dict[str, dict]:
        latest: dict[str, dict] = {}
        if not isinstance(records, list):
            return latest
        for record in records:
            if not isinstance(record, dict):
                logger.warning("skipping malformed document history record")
                continue
            document_id = record.get("document_id")
            if not isinstance(document_id, str) or not document_id.strip():
                logger.warning("skipping malformed document history record")
                continue
            latest[document_id] = record
        return latest

    def _project_record(
        self, user_id: str, record: dict
    ) -> DocumentLibraryItem | None:
        try:
            document_id = record["document_id"]
            name = record["document_name"]
            file_suffix = record["file_suffix"]
            raw_path = record["document_path"]
            if not all(
                isinstance(value, str) and value.strip()
                for value in (document_id, name, file_suffix, raw_path)
            ):
                raise ValueError("invalid document history value")
            source = self._safe_document_path(user_id, raw_path)
            normalized_name = Path(name.replace("\\", "/")).name
            if normalized_name != name or source.suffix.lower() != file_suffix.lower():
                raise ValueError("document display metadata is inconsistent")
            loaded_at = record.get("loaded_at")
            if not isinstance(loaded_at, str) or not loaded_at.strip():
                loaded_at = None
            else:
                try:
                    self._parse_loaded_at(loaded_at)
                except ValueError:
                    logger.warning("invalid document loaded_at was ignored")
                    loaded_at = None
            try:
                size_bytes = source.stat().st_size if source.is_file() else None
            except OSError:
                size_bytes = None
            return DocumentLibraryItem(
                document_id=document_id,
                name=name,
                file_suffix=file_suffix,
                size_bytes=size_bytes,
                loaded_at=loaded_at,
            )
        except (KeyError, OSError, TypeError, ValueError):
            logger.warning("skipping malformed document history record")
            return None

    @staticmethod
    def _parse_loaded_at(value: str) -> datetime:
        normalized = f"{value[:-1]}+00:00" if value.endswith(("Z", "z")) else value
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            raise ValueError("loaded_at must be ISO-8601") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("loaded_at must include a timezone")
        return parsed.astimezone(timezone.utc)

    def _safe_document_path(self, user_id: str, value: str) -> Path:
        source = Path(value)
        if not source.is_absolute():
            raise ValueError("document source path must be absolute")

        raw_document_root = self.storage.user_paths(user_id).documents
        self._reject_reparse_point(raw_document_root)
        document_root = raw_document_root.resolve(strict=False)
        normalized = Path(os.path.abspath(source))
        try:
            relative = normalized.relative_to(document_root)
        except ValueError:
            raise ValueError("document source path is outside documents root") from None

        current = document_root
        self._reject_reparse_point(current)
        for part in relative.parts:
            current /= part
            self._reject_reparse_point(current)

        resolved = normalized.resolve(strict=False)
        if resolved != normalized:
            raise ValueError("document source path resolves unexpectedly")
        return resolved

    @staticmethod
    def _reject_reparse_point(path: Path) -> None:
        if not (path.exists() or path.is_symlink()):
            return
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        if path.is_symlink() or attributes & 0x400:
            raise ValueError("document source path contains a reparse point")


logger = logging.getLogger(__name__)
