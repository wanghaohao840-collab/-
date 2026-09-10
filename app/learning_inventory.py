"""Fresh offline document authority for a stopped-writer learning migration.

Not a migration permit: the coordinator owns maintenance, trusted ACLs and the
source filesystem namespace throughout inventory and import. Never bootstrap
runtime services here. The importer rechecks deletion fences in its transaction.
"""
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sqlite3

from app.document_library import DocumentLibraryService
from app.learning_source import LegacySourceError, read_history_source
from app.storage import UserStorage


class DocumentInventoryError(ValueError):
    """Static error code; no source content or paths."""


@dataclass(frozen=True)
class DocumentInventory:
    user_id: str
    source_sha256: str
    ready_document_ids: frozenset[str]


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _nonfinite(_value):
    raise ValueError('nonfinite value')


def _finite_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError('nonfinite value')
    return parsed


def read_ready_documents(data_root, *, user_id: str) -> DocumentInventory:
    try:
        payload = read_history_source(data_root, user_id=user_id)
    except LegacySourceError:
        raise DocumentInventoryError('HISTORY_UNAVAILABLE') from None
    if payload is None:
        raise DocumentInventoryError('HISTORY_UNAVAILABLE')
    try:
        history = json.loads(payload.decode('utf-8'), object_pairs_hook=_object,
                             parse_constant=_nonfinite, parse_float=_finite_float)
        if (not isinstance(history, dict) or 'documents' not in history or
                not set(history) <= {'documents', 'questions', 'notes', 'sessions'} or
                any(not isinstance(value, list) for value in history.values()) or
                any(not isinstance(record, dict) for record in history['documents'])):
            raise ValueError('invalid history')
    except (ValueError, RecursionError):
        raise DocumentInventoryError('HISTORY_INVALID') from None
    for record in history['documents']:
        if 'user_id' in record and record['user_id'] != user_id:
            raise DocumentInventoryError('HISTORY_OWNER')

    root = Path(data_root).absolute()
    library = DocumentLibraryService(None, UserStorage(root), None)
    projected = library.project_history_documents(user_id, history['documents'])
    try:
        with closing(sqlite3.connect((root / 'app.db').as_uri() + '?mode=ro', uri=True)) as conn:
            conn.execute('pragma query_only=on')
            conn.execute('begin')
            account = conn.execute('select status from users where id=?', (user_id,)).fetchone()
            if account is None or account[0] != 'active':
                raise DocumentInventoryError('USER_UNAVAILABLE')
            ready = frozenset(item.document_id for item in projected if not conn.execute(
                "select 1 from qa_deletion_fences where user_id=? and target_type='document' and target_id=? limit 1",
                (user_id, item.document_id)).fetchone())
        if read_history_source(root, user_id=user_id) != payload:
            raise DocumentInventoryError('HISTORY_CHANGED')
    except LegacySourceError:
        raise DocumentInventoryError('HISTORY_UNAVAILABLE') from None
    except sqlite3.Error:
        raise DocumentInventoryError('REGISTRY_UNAVAILABLE') from None
    return DocumentInventory(user_id, hashlib.sha256(payload).hexdigest(), ready)
