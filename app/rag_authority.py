"""Read-only account/history authority for offline RAG migration management.

Never instantiate UserRuntimeRegistry, AuthService or HistoryRepository here:
their runtime entry points can initialize directories or default missing data.
The maintenance controller must stop writers; stat checks/rescans are additional
guards, not a cross-file transaction or a substitute for that controller.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import os
from pathlib import Path, PureWindowsPath
import sqlite3
import stat
import uuid

import ijson

from hello_agents.memory.rag.source_records import (
    SourceInventoryError, canonical, digest, require_name,
)
from hello_agents.memory.rag.source_stream import BoundedJSONReader, read_json_value


def checked_path(value: Path | str, *, directory=False, missing=False) -> Path:
    try:
        return _checked_path(value, directory=directory, missing=missing)
    except (OSError, ValueError, TypeError):
        raise SourceInventoryError("authority_path") from None


def _checked_path(value: Path | str, *, directory=False, missing=False) -> Path:
    """Require an absolute, normalized path with no link/reparse ancestors.

    Missing leaves are allowed only explicitly. This confines paths; it does
    not provision Windows ACLs or defeat malicious concurrent directory swaps.
    """
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise SourceInventoryError("authority_path")
    if os.name == "nt":
        reserved = getattr(os.path, "isreserved", lambda part: PureWindowsPath(part).is_reserved())
        if any(":" in part or part.rstrip(" .") != part or reserved(part) for part in path.parts[1:]):
            raise SourceInventoryError("authority_path")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if missing:
                return path
            raise SourceInventoryError("authority_missing") from None
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise SourceInventoryError("authority_path")
        if current != path and not stat.S_ISDIR(info.st_mode):
            raise SourceInventoryError("authority_path")
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode):
        raise SourceInventoryError("authority_path")
    return path


def _stamp(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _user_id(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
        return value
    except (ValueError, AttributeError):
        raise SourceInventoryError("authority_user") from None


def namespace_user(namespace):
    if not isinstance(namespace, str) or not namespace.startswith("pdf_"):
        raise SourceInventoryError("authority_namespace")
    return _user_id(namespace[4:])


class AppOwnershipSource:
    """Stream (namespace, document_id, user_id); receipt only at full EOF.

    All persisted user statuses participate, including inactive accounts.
    namespace limits emitted owners, NOT the account/history validation scan.
    Repeated historical document IDs are passed through; build_inventory rejects
    duplicates on disk for the selected scope rather than guessing which wins.
    Only document_id and optional user_id are ownership facts. Original document
    paths may refer to a former host/container; they are not reopened or used to
    infer ownership. Source chunks retain provenance independently.
    """

    def __init__(self, data_root: Path | str, *, namespace: str | None = None):
        self.root = Path(data_root)
        if not self.root.is_absolute():
            raise SourceInventoryError("authority_path")
        self.selected_user = namespace_user(namespace) if namespace is not None else None
        self.namespace = namespace
        self.completed_token = None
        self._running = False

    def _history(self, path, user_id, hasher):
        checked_path(path)
        before = _stamp(path)
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            # stat/fstat ctime have different meanings on some Windows/Python.
            if before[:4] != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
                raise SourceInventoryError("authority_changed")
            reader = BoundedJSONReader(stream)
            events = iter(ijson.basic_parse(reader, use_float=True, buf_size=65536))
            if next(events)[0] != "start_map":
                raise SourceInventoryError("authority_history")
            seen = set()
            while True:
                event, key = next(events)
                if event == "end_map":
                    break
                if (event != "map_key" or key in seen
                        or key not in {"documents", "questions", "notes", "sessions"}):
                    raise SourceInventoryError("authority_history")
                seen.add(key)
                if next(events)[0] != "start_array":
                    raise SourceInventoryError("authority_history")
                while True:
                    first = next(events)
                    if first[0] == "end_array":
                        break
                    item = read_json_value(events, first)
                    canonical(item)
                    reader.pending = 0
                    if key == "documents":
                        if not isinstance(item, dict):
                            raise SourceInventoryError("authority_document")
                        document_id = require_name(item.get("document_id"))
                        if "user_id" in item and item["user_id"] != user_id:
                            raise SourceInventoryError("authority_owner")
                        yield document_id
                    # Other history fields are validated one value at a time
                    # but never copied to the inventory or sent to providers.
                reader.pending = 0
            if "documents" not in seen or next(events, None) is not None:
                raise SourceInventoryError("authority_history")
            after = os.fstat(stream.fileno())
            if (_stamp(checked_path(path)) != before
                    or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise SourceInventoryError("authority_changed")
            hasher.update(reader.hash.hexdigest().encode("ascii"))

    def _no_journal(self, path):
        # immutable=1 avoids SQLite creating/touching WAL shared-memory files.
        # It is safe here only for a quiescent, checkpointed source. Never
        # silently ignore a WAL or hot rollback journal.
        for suffix in ("-wal", "-shm", "-journal"):
            if _stamp(path.with_name(path.name + suffix)) is not None:
                raise SourceInventoryError("authority_database_busy")

    def _audit_users(self, db, users):
        checked_path(users, directory=True, missing=True)
        if not users.exists():
            return
        with os.scandir(users) as entries:
            for entry in entries:
                user_id = _user_id(entry.name)
                checked_path(Path(entry.path), directory=True)
                if not db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
                    raise SourceInventoryError("authority_orphan")

    def records(self):
        if self._running:
            raise SourceInventoryError("authority_busy")
        self._running = True
        self.completed_token = None
        try:
            checked_path(self.root, directory=True)
            path = checked_path(self.root / "app.db")
            users = checked_path(self.root / "users", directory=True, missing=True)
            self._no_journal(path)
            before = _stamp(path)
            users_before = _stamp(users)
            hasher = hashlib.sha256(b"app-rag-ownership-v1")
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
                db.execute("PRAGMA query_only=ON")
                db.execute("PRAGMA temp_store=FILE")
                db.execute("PRAGMA cache_size=-2048")
                db.execute("BEGIN")
                if db.execute("SELECT type FROM sqlite_master WHERE name='users'").fetchone() != ("table",):
                    raise SourceInventoryError("authority_database")
                if self.selected_user is not None and not db.execute(
                    "SELECT 1 FROM users WHERE id=?", (self.selected_user,)
                ).fetchone():
                    raise SourceInventoryError("authority_owner")
                self._audit_users(db, users)
                for user_id, status in db.execute("SELECT id,status FROM users ORDER BY id"):
                    _user_id(user_id)
                    require_name(status)
                    user_root = checked_path(users / user_id, directory=True, missing=True)
                    exists = user_root.exists()
                    hasher.update(digest([user_id, status, exists]).encode("ascii"))
                    if not exists:
                        continue  # Registered account with no initialized data.
                    with closing(self._history(user_root / "history.json", user_id, hasher)) as history:
                        for document_id in history:
                            if self.selected_user is None or user_id == self.selected_user:
                                yield f"pdf_{user_id}", document_id, user_id
                self._audit_users(db, users)
                self._no_journal(path)
                if _stamp(checked_path(path)) != before or _stamp(users) != users_before:
                    raise SourceInventoryError("authority_changed")
            self.completed_token = hasher.hexdigest()
        except SourceInventoryError:
            raise
        except (OSError, sqlite3.Error, UnicodeError, ValueError, TypeError,
                OverflowError, RecursionError, StopIteration, ijson.JSONError):
            raise SourceInventoryError("authority_unreadable") from None
        finally:
            self._running = False
