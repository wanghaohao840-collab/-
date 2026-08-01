# Multi-User System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local multi-user system so every authenticated user has isolated documents, learning notes, QA history, learning reports, and memory.

**Architecture:** SQLite owns identity, report indexes, and legacy migration state. User data lives under `data/users/<user_uuid>/`, Gradio `State` stores only a high-entropy session token, and a server-side session registry maps that token to a session-scoped assistant. Same-user sessions share one `UserRuntime` that owns RAG, Memory, history, reports, and a user-level lock.

**Tech Stack:** Python 3, stdlib `sqlite3`, `hashlib.scrypt`, `hmac.compare_digest`, `secrets`, `uuid`, `threading.RLock`, `pathlib`, `json`, `tempfile`, `os.replace`, pytest 7.4.4, Gradio, existing `hello_agents` Memory and RAG tools, `python-docx`, `pypdf`.

## Global Constraints

- Preserve dependency direction: UI -> Assistant -> Tool -> Memory/RAG/Storage.
- Use SQLite local auth; username is unique; no email verification, password reset, admin roles, OAuth, JWT, public API, account deletion, or report deletion in version one.
- Normalize usernames with `strip()`, Unicode NFKC, and `casefold()` for uniqueness; display the NFKC value.
- Username length is 3-32 Unicode characters; allowed characters are letters, digits, `_`, `-`, and `.`; first and last characters must be letters or digits.
- Password length is 8-128 Unicode characters.
- Password hashing must use `hashlib.scrypt` with `n=16384`, `r=8`, `p=1`, `dklen=32`, and a per-user 16-byte random salt.
- Password comparison must use `hmac.compare_digest()`.
- Session tokens must use `secrets.token_urlsafe(32)`.
- Sessions expire after 12 idle hours.
- The in-process session registry may hold at most 128 active sessions; cleanup expired sessions before rejecting a new login.
- Gradio `State` stores only the session token; it must not store user UUIDs, passwords, password hashes, user paths, or `PDFLearningAssistant` objects.
- Internal user identity and directory names use immutable UUIDs, not usernames.
- User data root is `data/users/<user_uuid>/`.
- User document uploads are copied to `documents/<document_uuid><validated_suffix>`.
- RAG must receive an explicit per-user cache path: `data/users/<user_uuid>/rag/rag_cache.json`.
- JSON persistence writes to a temporary file in the same directory, flushes and closes it, then replaces the target with `os.replace()`.
- `data/`, migration backups, runtime reports, uploaded files, RAG cache, memory snapshots, and SQLite runtime databases must not be committed.
- Same-user browser sessions share one `UserRuntime`; each browser session has its own assistant and its own current-document state.
- Long LLM generation must not hold the user write lock; hold the lock only while reading snapshots and committing results.
- Legacy data is claimed by one logged-in user through an explicit one-time migration flow; ambiguous `memory.db` records are skipped, not guessed.

---

## File Structure Map

Create these new focused application modules:

- `app/__init__.py`: marks application service package.
- `app/storage.py`: safe user path construction, suffix validation, and atomic JSON read/write.
- `app/database.py`: SQLite connection factory, schema creation, transaction helper, and foreign-key enforcement.
- `app/auth.py`: username normalization, password hashing, registration, authentication, and user lookup.
- `app/history.py`: per-user learning history repository for documents, notes, questions, and deletion semantics.
- `app/memory_repository.py`: JSON snapshot repository for working, episodic, and semantic memories.
- `app/runtime.py`: shared per-user runtime creation, locking, and runtime registry.
- `app/reports.py`: report snapshot persistence, SQLite report index, reading, listing, and Word export.
- `app/session.py`: token registry, session expiration, login/register/logout, and assistant lookup.
- `app/migration.py`: legacy data scan, manifest creation, one-time claim, backup, staging, merge, and skipped-summary reporting.

Modify existing modules:

- `.gitignore`: ignore `data/`.
- `hello_agents/tools/builtin/rag_tool.py`: accept and pass per-user `cache_path`; expose retrieval/generation helpers so locks can be released during LLM calls.
- `hello_agents/memory/rag/pipeline.py`: make cache writes atomic.
- `hello_agents/memory/manager.py`: accept a snapshot repository and persist after supported memory mutations.
- `assistants/pdf_learning_assistant.py`: refactor from fixed `user123` ownership to a session-scoped facade backed by `UserRuntime`.
- `ui/gradio_app.py`: remove module-level global assistant and route every event through the session registry.
- `README.md`: document local registration/login, data isolation, data root, and migration.

Create these tests:

- `tests/test_user_storage.py`
- `tests/test_auth_service.py`
- `tests/test_rag_user_cache.py`
- `tests/test_memory_repository.py`
- `tests/test_history_repository.py`
- `tests/test_user_runtime.py`
- `tests/test_pdf_learning_assistant_mult.user.py`
- `tests/test_report_service.py`
- `tests/test_session_registry.py`
- `tests/test_legacy_migration.py`
- `tests/test_gradio_handlers.py`
- `tests/test_multi_user_end_to_end.py`

## Task Summary

1. User storage primitives and `.gitignore`.
2. SQLite database and authentication service.
3. Per-user RAG cache path and atomic RAG persistence.
4. Persistent Memory snapshot repository.
5. History repository and shared `UserRuntime`.
6. Session-scoped `PDFLearningAssistant`.
7. Report snapshot service.
8. Session registry.
9. Legacy data migration.
10. Gradio UI refactor.
11. End-to-end verification and documentation.

---

### Task 1: User Storage Primitives

**Files:**
- Create: `app/__init__.py`
- Create: `app/storage.py`
- Create: `tests/test_user_storage.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `SUPPORTED_DOCUMENT_SUFFIXES: set[str]`
- Produces: `UnsafePathError(ValueError)`
- Produces: `UserPaths(root: Path, documents: Path, rag_cache: Path, history: Path, memory_snapshot: Path, reports: Path)`
- Produces: `UserStorage(data_root: Path | str)`
- Produces: `UserStorage.ensure_user_dirs(user_id: str) -> UserPaths`
- Produces: `UserStorage.validate_suffix(suffix: str) -> str`
- Produces: `UserStorage.document_path(user_id: str, document_id: str, suffix: str) -> Path`
- Produces: `UserStorage.report_path(user_id: str, report_id: str, suffix: str = ".md") -> Path`
- Produces: `UserStorage.assert_within_user(user_id: str, path: Path | str) -> Path`
- Produces: `read_json(path: Path, default: Any) -> Any`
- Produces: `write_json_atomic(path: Path, data: Any) -> None`

- [ ] **Step 1: Write the failing storage tests**

```python
# tests/test_user_storage.py
import json
from pathlib import Path

import pytest

from app.storage import UnsafePathError, UserStorage, read_json, write_json_atomic


def test_ensure_user_dirs_creates_expected_layout(tmp_path):
    storage = UserStorage(tmp_path / "data")

    paths = storage.ensure_user_dirs("11111111-1111-1111-1111-111111111111")

    assert paths.root == tmp_path / "data" / "users" / "11111111-1111-1111-1111-111111111111"
    assert paths.documents.is_dir()
    assert paths.rag_cache == paths.root / "rag" / "rag_cache.json"
    assert paths.history == paths.root / "history.json"
    assert paths.memory_snapshot == paths.root / "memory" / "memories.json"
    assert paths.reports.is_dir()


def test_document_path_uses_uuid_name_and_validated_suffix(tmp_path):
    storage = UserStorage(tmp_path / "data")

    path = storage.document_path(
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
        ".PDF",
    )

    assert path.name == "22222222-2222-2222-2222-222222222222.pdf"
    assert path.parent.name == "documents"


def test_rejects_path_traversal(tmp_path):
    storage = UserStorage(tmp_path / "data")
    storage.ensure_user_dirs("11111111-1111-1111-1111-111111111111")

    with pytest.raises(UnsafePathError):
        storage.assert_within_user(
            "11111111-1111-1111-1111-111111111111",
            tmp_path / "data" / "users" / "11111111-1111-1111-1111-111111111111" / ".." / "other",
        )


def test_rejects_unsupported_suffix(tmp_path):
    storage = UserStorage(tmp_path / "data")

    with pytest.raises(ValueError, match="Unsupported document type"):
        storage.validate_suffix(".exe")


def test_atomic_json_round_trip(tmp_path):
    target = tmp_path / "state.json"

    write_json_atomic(target, {"ok": True, "items": [1, 2, 3]})

    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True, "items": [1, 2, 3]}
    assert read_json(target, default={}) == {"ok": True, "items": [1, 2, 3]}
    assert read_json(tmp_path / "missing.json", default={"empty": True}) == {"empty": True}
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `python -m pytest tests/test_user_storage.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'app'` or missing symbols from `app.storage`.

- [ ] **Step 3: Implement storage primitives**

```python
# app/__init__.py
"""Application service layer for the document learning assistant."""
```

```python
# app/storage.py
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
```

Add this line to `.gitignore`:

```gitignore
data/
```

- [ ] **Step 4: Run storage tests and verify they pass**

Run: `python -m pytest tests/test_user_storage.py -v`

Expected: PASS for all tests in `tests/test_user_storage.py`.

- [ ] **Step 5: Commit**

```bash
git add .gitignore app/__init__.py app/storage.py tests/test_user_storage.py
git commit -m "feat: add user storage primitives"
```

