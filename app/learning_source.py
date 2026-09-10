"""Read a bounded registered-user legacy source; never create or migrate data.

Path checks detect ordinary unsafe paths/races, not privileged adversarial swaps.
An apply coordinator must still stop writers and enforce trusted directory ACLs.
"""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import stat
from uuid import UUID

LIMIT = 16 * 1024 * 1024


class LegacySourceError(ValueError):
    """Static code without source content or filesystem details."""


def _reject_link(info):
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 1024:
        raise LegacySourceError('UNSAFE_PATH')


def _checked(path):
    for component in reversed(path.parents):
        info = component.lstat()
        _reject_link(info)
        if not stat.S_ISDIR(info.st_mode):
            raise LegacySourceError('UNSAFE_PATH')
    info = path.lstat()
    _reject_link(info)
    return info


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_legacy_source(data_root, *, user_id: str) -> bytes | None:
    return _read_registered_source(data_root, user_id=user_id, leaf='learning.json')


def read_history_source(data_root, *, user_id: str) -> bytes | None:
    """Fixed registered-user history, with the same bounded safe-read contract."""
    return _read_registered_source(data_root, user_id=user_id, leaf='history.json')


def _read_registered_source(data_root, *, user_id: str, leaf: str) -> bytes | None:
    if leaf not in ('learning.json', 'history.json'):
        raise LegacySourceError('UNSAFE_PATH')
    try:
        if str(UUID(user_id)) != user_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise LegacySourceError('INVALID_USER') from None
    try:
        root = Path(data_root).absolute()
        if '..' in root.parts or not stat.S_ISDIR(_checked(root).st_mode):
            raise LegacySourceError('UNSAFE_PATH')
        registry = root / 'app.db'
        if not stat.S_ISREG(_checked(registry).st_mode):
            raise LegacySourceError('UNSAFE_PATH')
        with closing(sqlite3.connect(registry.as_uri() + '?mode=ro', uri=True)) as conn:
            conn.execute('pragma query_only=on')
            user = conn.execute('select status from users where id=?', (user_id,)).fetchone()
        if user is None or user[0] != 'active':
            raise LegacySourceError('USER_UNAVAILABLE')
    except (OSError, sqlite3.Error):
        raise LegacySourceError('REGISTRY_UNAVAILABLE') from None
    source = root / 'users' / user_id / leaf
    try:
        before = _checked(source)
    except FileNotFoundError:
        return None
    except OSError:
        raise LegacySourceError('SOURCE_UNAVAILABLE') from None
    try:
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise LegacySourceError('UNSAFE_PATH')
        if before.st_size > LIMIT:
            raise LegacySourceError('SOURCE_TOO_LARGE')
        with source.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            # Windows path and descriptor ctime can differ; compare ctime only
            # within the same query family, not across stat and fstat.
            if _identity(opened)[:4] != _identity(before)[:4]:
                raise LegacySourceError('SOURCE_CHANGED')
            payload = stream.read(LIMIT + 1)
            if len(payload) > LIMIT:
                raise LegacySourceError('SOURCE_TOO_LARGE')
            after = os.fstat(stream.fileno())
        if (_identity(opened) != _identity(after) or
                _identity(before) != _identity(_checked(source)) or len(payload) != before.st_size):
            raise LegacySourceError('SOURCE_CHANGED')
        return payload
    except OSError:
        raise LegacySourceError('SOURCE_UNAVAILABLE') from None
