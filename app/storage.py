from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SUPPORTED_DOCUMENT_SUFFIXES: set[str] = {".pdf", ".txt", ".md", ".markdown", ".docx"}


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
        )

    def ensure_user_dirs(self, user_id: str) -> UserPaths:
        paths = self.user_paths(user_id)
        paths.documents.mkdir(parents=True, exist_ok=True)
        paths.rag_cache.parent.mkdir(parents=True, exist_ok=True)
        paths.memory_snapshot.parent.mkdir(parents=True, exist_ok=True)
        paths.reports.mkdir(parents=True, exist_ok=True)
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

    def report_path(self, user_id: str, report_id: str, suffix: str = ".md") -> Path:
        if suffix not in {".md", ".docx"}:
            raise ValueError(f"Unsupported report type: {suffix}")
        paths = self.ensure_user_dirs(user_id)
        target = paths.reports / f"{report_id}{suffix}"
        return self.assert_within_user(user_id, target)

    def assert_within_user(self, user_id: str, path: Path | str) -> Path:
        root = self.user_paths(user_id).root.resolve()
        resolved = Path(path).resolve()
        if resolved != root and root not in resolved.parents:
            raise UnsafePathError(f"Path escapes user data root: {resolved}")
        return resolved


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
