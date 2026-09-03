from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SUPPORTED_DOCUMENT_SUFFIXES: set[str] = {".pdf", ".txt", ".md", ".markdown", ".docx"}
IMPORT_ROLLBACK_MARKER_NAME = ".rollback.json"
_IMPORT_ROLLBACK_VERSION = 1
_MAX_IMPORT_ROLLBACK_FILES = 20
_MAX_IMPORT_ROLLBACK_BYTES = 16 * 1024


class UnsafePathError(ValueError):
    """Raised when a resolved path escapes the current user's data root."""


@dataclass(frozen=True)
class UserPaths:
    root: Path
    documents: Path
    rag_cache: Path
    history: Path
    memory_snapshot: Path
    reports: Path
    imports: Path


class UserStorage:
    def __init__(self, data_root: Path | str):
        self.data_root = Path(data_root).resolve()
        self.users_root = self.data_root / "users"

    def user_paths(self, user_id: str) -> UserPaths:
        root = self.users_root / user_id
        return UserPaths(
            root=root,
            documents=root / "documents",
            rag_cache=root / "rag" / "rag_cache.json",
            history=root / "history.json",
            memory_snapshot=root / "memory" / "memories.json",
            reports=root / "reports",
            imports=root / "imports",
        )

    def ensure_user_dirs(self, user_id: str) -> UserPaths:
        paths = self.user_paths(user_id)
        paths.documents.mkdir(parents=True, exist_ok=True)
        paths.rag_cache.parent.mkdir(parents=True, exist_ok=True)
        paths.memory_snapshot.parent.mkdir(parents=True, exist_ok=True)
        paths.reports.mkdir(parents=True, exist_ok=True)
        paths.imports.mkdir(parents=True, exist_ok=True)
        return paths

    def validate_suffix(self, suffix: str) -> str:
        normalized = suffix.lower()
        if normalized not in SUPPORTED_DOCUMENT_SUFFIXES:
            raise ValueError(f"Unsupported document type: {suffix}")
        return normalized

    def document_path(self, user_id: str, document_id: str, suffix: str) -> Path:
        paths = self.ensure_user_dirs(user_id)
        target = paths.documents / f"{document_id}{self.validate_suffix(suffix)}"
        return self.assert_within_user(user_id, target)

    def temporary_document_path(self, user_id: str, document_id: str, suffix: str) -> Path:
        target = self.document_path(user_id, document_id, suffix)
        return self.assert_within_user(user_id, target.with_name(f".{target.name}.uploading"))

    def resolve_import_attempt_paths(
        self, user_id: str, document_id: str, suffix: str
    ) -> tuple[Path, Path]:
        """Derive exact temporary/formal paths from persisted server IDs."""

        normalized_user_id = _uuid_string(user_id, "user_id")
        normalized_document_id = _uuid_string(document_id, "document_id")
        normalized_suffix = self.validate_suffix(suffix)
        root = self.user_paths(normalized_user_id).root
        documents = root / "documents"
        formal = documents / f"{normalized_document_id}{normalized_suffix}"
        temporary = documents / f".{formal.name}.uploading"
        for component in (root, documents, formal, temporary):
            self._reject_staged_reparse_point(component)

        real_root = root.resolve(strict=False)
        real_documents = documents.resolve(strict=False)
        if real_documents != real_root / "documents":
            raise UnsafePathError("Import document path escapes documents root")
        resolved_formal = formal.resolve(strict=False)
        resolved_temporary = temporary.resolve(strict=False)
        if resolved_formal != real_documents / formal.name:
            raise UnsafePathError("Import document path does not match its task")
        if resolved_temporary != real_documents / temporary.name:
            raise UnsafePathError("Temporary import path does not match its task")
        return resolved_temporary, resolved_formal

    def report_path(self, user_id: str, report_id: str, suffix: str = ".md") -> Path:
        if suffix not in {".md", ".docx"}:
            raise ValueError(f"Unsupported report type: {suffix}")
        paths = self.ensure_user_dirs(user_id)
        target = paths.reports / f"{report_id}{suffix}"
        return self.assert_within_user(user_id, target)

    def import_batch_dir(self, user_id: str, batch_id: str) -> Path:
        normalized_user_id = _uuid_string(user_id, "user_id")
        normalized_batch_id = _uuid_string(batch_id, "batch_id")
        paths = self.ensure_user_dirs(normalized_user_id)
        target = paths.imports / normalized_batch_id
        target.mkdir(parents=True, exist_ok=True)
        return self.assert_within_user(normalized_user_id, target)

    def staged_import_path(
        self, user_id: str, batch_id: str, task_id: str, suffix: str
    ) -> Path:
        normalized_user_id = _uuid_string(user_id, "user_id")
        normalized_task_id = _uuid_string(task_id, "task_id")
        target = self.import_batch_dir(normalized_user_id, batch_id) / (
            f"{normalized_task_id}{self.validate_suffix(suffix)}"
        )
        return self.assert_within_user(normalized_user_id, target)

    def partial_staged_import_path(
        self, user_id: str, batch_id: str, task_id: str, suffix: str
    ) -> Path:
        staged = self.staged_import_path(user_id, batch_id, task_id, suffix)
        partial = staged.with_name(f"{staged.name}.partial")
        return self.assert_within_user(user_id, partial)

    def import_rollback_marker_path(self, user_id: str, batch_id: str) -> Path:
        normalized_user_id = _uuid_string(user_id, "user_id")
        batch_dir = self.import_batch_dir(normalized_user_id, batch_id)
        marker = batch_dir / IMPORT_ROLLBACK_MARKER_NAME
        self._reject_staged_reparse_point(marker)
        return self.assert_within_user(normalized_user_id, marker)

    def write_import_rollback_journal(
        self,
        user_id: str,
        batch_id: str,
        files: list[tuple[str, str]],
    ) -> Path:
        """Atomically record exact server-owned staging identities."""

        normalized_files = _normalize_rollback_files(files)
        marker = self.import_rollback_marker_path(user_id, batch_id)
        write_json_atomic(
            marker,
            {
                "version": _IMPORT_ROLLBACK_VERSION,
                "files": [
                    {"task_id": task_id, "suffix": suffix}
                    for task_id, suffix in normalized_files
                ],
            },
        )
        return marker

    def read_import_rollback_journal(
        self, user_id: str, batch_id: str, marker: Path
    ) -> list[tuple[str, str]]:
        """Parse an untrusted marker only after exact location validation."""

        normalized_user_id = _uuid_string(user_id, "user_id")
        normalized_batch_id = _uuid_string(batch_id, "batch_id")
        expected = (
            self.user_paths(normalized_user_id).root
            / "imports"
            / normalized_batch_id
            / IMPORT_ROLLBACK_MARKER_NAME
        )
        self._reject_staged_reparse_point(marker)
        if marker.resolve(strict=False) != expected.resolve(strict=False):
            raise ValueError("Import rollback marker path is invalid")
        if marker.stat().st_size > _MAX_IMPORT_ROLLBACK_BYTES:
            raise ValueError("Import rollback marker is too large")
        data = read_json(marker, None)
        if not isinstance(data, dict) or set(data) != {"version", "files"}:
            raise ValueError("Import rollback marker is invalid")
        if (
            type(data["version"]) is not int
            or data["version"] != _IMPORT_ROLLBACK_VERSION
        ):
            raise ValueError("Import rollback marker version is invalid")
        raw_files = data["files"]
        if not isinstance(raw_files, list):
            raise ValueError("Import rollback marker files are invalid")
        files: list[tuple[str, str]] = []
        for entry in raw_files:
            if not isinstance(entry, dict) or set(entry) != {"task_id", "suffix"}:
                raise ValueError("Import rollback marker entry is invalid")
            if not isinstance(entry["task_id"], str) or not isinstance(
                entry["suffix"], str
            ):
                raise ValueError("Import rollback marker entry is invalid")
            task_id = _uuid_string(entry["task_id"], "task_id")
            suffix = self.validate_suffix(entry["suffix"])
            if entry["task_id"] != task_id or entry["suffix"] != suffix:
                raise ValueError("Import rollback marker entry is not canonical")
            files.append((task_id, suffix))
        return _normalize_rollback_files(files)

    def iter_import_rollback_journals(self):
        """Yield only fixed markers below canonical user/batch UUID directories."""

        if not self.users_root.is_dir():
            return
        for user_dir in self.users_root.iterdir():
            try:
                user_id = _uuid_string(user_dir.name, "user_id")
                if user_id != user_dir.name:
                    continue
                self._reject_staged_reparse_point(user_dir)
                imports = user_dir / "imports"
                self._reject_staged_reparse_point(imports)
                if not imports.is_dir():
                    continue
                for batch_dir in imports.iterdir():
                    try:
                        batch_id = _uuid_string(batch_dir.name, "batch_id")
                        if batch_id != batch_dir.name:
                            continue
                        self._reject_staged_reparse_point(batch_dir)
                        if not batch_dir.is_dir():
                            continue
                        marker = batch_dir / IMPORT_ROLLBACK_MARKER_NAME
                        self._reject_staged_reparse_point(marker)
                        if marker.is_file():
                            yield user_id, batch_id, marker
                    except (OSError, ValueError):
                        continue
            except (OSError, ValueError):
                continue

    def resolve_rollback_staging_paths(
        self, user_id: str, batch_id: str, task_id: str, suffix: str
    ) -> tuple[Path, Path]:
        normalized_user_id = _uuid_string(user_id, "user_id")
        normalized_batch_id = _uuid_string(batch_id, "batch_id")
        normalized_task_id = _uuid_string(task_id, "task_id")
        normalized_suffix = self.validate_suffix(suffix)
        relative = str(
            Path("imports")
            / normalized_batch_id
            / f"{normalized_task_id}{normalized_suffix}"
        )
        staged = self.resolve_staged_import_path(
            normalized_user_id,
            normalized_batch_id,
            normalized_task_id,
            normalized_suffix,
            relative,
        )
        partial = staged.with_name(f"{staged.name}.partial")
        self._reject_staged_reparse_point(partial)
        if partial.resolve(strict=False) != staged.parent / partial.name:
            raise UnsafePathError("Partial import path does not match its task")
        return partial, staged

    def resolve_staged_import_path(
        self,
        user_id: str,
        batch_id: str,
        task_id: str,
        suffix: str,
        recorded_relative_path: str,
    ) -> Path:
        """Validate a persisted staging path without creating directories."""

        normalized_user_id = _uuid_string(user_id, "user_id")
        normalized_batch_id = _uuid_string(batch_id, "batch_id")
        normalized_task_id = _uuid_string(task_id, "task_id")
        normalized_suffix = self.validate_suffix(suffix)
        root = self.user_paths(normalized_user_id).root
        expected_relative = (
            Path("imports")
            / normalized_batch_id
            / f"{normalized_task_id}{normalized_suffix}"
        )
        recorded = Path(recorded_relative_path)
        if recorded.is_absolute() or recorded != expected_relative:
            raise ValueError("Staged import path does not match its task")
        imports = root / "imports"
        batch_dir = imports / normalized_batch_id
        target = batch_dir / f"{normalized_task_id}{normalized_suffix}"
        for component in (imports, batch_dir, target):
            self._reject_staged_reparse_point(component)

        real_imports = imports.resolve(strict=False)
        real_batch_dir = batch_dir.resolve(strict=False)
        expected_real_batch_dir = real_imports / normalized_batch_id
        if real_batch_dir != expected_real_batch_dir:
            raise UnsafePathError("Staged import batch escapes imports root")

        resolved = target.resolve(strict=False)
        expected = real_batch_dir / f"{normalized_task_id}{normalized_suffix}"
        if resolved != expected:
            raise ValueError("Staged import path does not match its task")
        inside_imports = resolved == real_imports or real_imports in resolved.parents
        inside_batch = (
            resolved == real_batch_dir or real_batch_dir in resolved.parents
        )
        if not inside_imports or not inside_batch:
            raise UnsafePathError("Staged import path escapes its batch directory")
        return resolved

    def remove_staged_import_file(
        self,
        user_id: str,
        batch_id: str,
        task_id: str,
        suffix: str,
        recorded_relative_path: str,
    ) -> bool:
        """Remove one exact persisted staging path after validating its identity."""

        staged = self.resolve_staged_import_path(
            user_id,
            batch_id,
            task_id,
            suffix,
            recorded_relative_path,
        )
        existed = staged.is_file()
        staged.unlink(missing_ok=True)
        try:
            staged.parent.rmdir()
        except OSError:
            pass
        return existed

    @staticmethod
    def _reject_staged_reparse_point(path: Path) -> None:
        """Reject link-like staging components before resolving them."""

        if not (path.exists() or path.is_symlink()):
            return
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        if path.is_symlink() or attributes & 0x400:
            raise UnsafePathError("Staged import path contains a link or reparse point")

    def assert_within_user(self, user_id: str, path: Path | str) -> Path:
        root = self.user_paths(user_id).root.resolve()
        resolved = Path(path).resolve()
        if resolved != root and root not in resolved.parents:
            raise UnsafePathError(f"Path escapes user data root: {resolved}")
        return resolved


def _uuid_string(value: str, field_name: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a UUID string") from exc


def _normalize_rollback_files(
    files: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    if not isinstance(files, list) or not 1 <= len(files) <= _MAX_IMPORT_ROLLBACK_FILES:
        raise ValueError("Import rollback marker file count is invalid")
    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in files:
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise ValueError("Import rollback marker entry is invalid")
        task_id = _uuid_string(entry[0], "task_id")
        suffix = str(entry[1]).lower()
        if suffix not in SUPPORTED_DOCUMENT_SUFFIXES or task_id in seen:
            raise ValueError("Import rollback marker entry is invalid")
        seen.add(task_id)
        normalized.append((task_id, suffix))
    return normalized


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
