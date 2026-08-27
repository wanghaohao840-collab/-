from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.database import connect
from app.history import HistoryRepository
from app.qa_models import conversation_title
from app.storage import read_json, write_json_atomic


MIGRATION_VERSION = 1


@dataclass(frozen=True)
class QaLegacyMigrationResult:
    imported_count: int
    skipped_count: int
    source_digest: str
    migration_version: int


@dataclass(frozen=True)
class _LegacyTurn:
    question: str
    answer: str
    document_ids: tuple[str, ...]
    document_names: tuple[str, ...]
    mode: str
    asked_at: str
    record_digest: str


class QaLegacyMigrationService:
    """Import truthful flat legacy questions into isolated single-turn QA rows."""

    def __init__(self, db_path: Path | str, storage=None) -> None:
        self.db_path = Path(db_path)
        self.storage = storage

    def ensure_user_migrated(self, user_id: str, history) -> QaLegacyMigrationResult:
        data = history.load()
        raw_questions = data.get("questions", [])
        raw_documents = data.get("documents", [])
        documents = _document_names(raw_documents)
        projections = [
            _safe_projection(item, documents) for item in raw_questions
        ] if isinstance(raw_questions, list) else []
        canonical = json.dumps(
            projections,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        source_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        turns = tuple(
            turn
            for projection in projections
            if (turn := _turn_from_projection(projection)) is not None
        )
        skipped_count = len(projections) - len(turns)
        completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        conn = connect(self.db_path)
        try:
            conn.execute("begin immediate")
            previous = conn.execute(
                "select * from qa_legacy_imports where user_id = ?", (user_id,)
            ).fetchone()
            if (
                previous is not None
                and previous["migration_version"] == MIGRATION_VERSION
                and previous["source_digest"] == source_digest
            ):
                conn.commit()
                return QaLegacyMigrationResult(
                    previous["imported_count"],
                    previous["skipped_count"],
                    previous["source_digest"],
                    previous["migration_version"],
                )

            conn.execute(
                "delete from qa_conversations where user_id = ? and origin = 'legacy_json'",
                (user_id,),
            )
            for index, turn in enumerate(turns):
                self._insert_turn(conn, user_id, turn, index)
            conn.execute(
                """
                insert into qa_legacy_imports (
                    user_id, migration_version, source_digest, imported_count,
                    skipped_count, completed_at
                ) values (?, ?, ?, ?, ?, ?)
                on conflict(user_id) do update set
                    migration_version = excluded.migration_version,
                    source_digest = excluded.source_digest,
                    imported_count = excluded.imported_count,
                    skipped_count = excluded.skipped_count,
                    completed_at = excluded.completed_at
                """,
                (
                    user_id,
                    MIGRATION_VERSION,
                    source_digest,
                    len(turns),
                    skipped_count,
                    completed_at,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return QaLegacyMigrationResult(
            len(turns), skipped_count, source_digest, MIGRATION_VERSION
        )

    def scrub_conversations(
        self, user_id: str, history, conversation_ids: tuple[str, ...]
    ) -> int:
        targets = set(conversation_ids)
        if not targets:
            return 0

        def scrub(data: dict) -> int:
            documents = _document_names(data.get("documents", []))
            kept = []
            removed = 0
            valid_index = 0
            for item in data.get("questions", []):
                turn = _turn_from_projection(_safe_projection(item, documents))
                if turn is None:
                    kept.append(item)
                    continue
                conversation_id = _legacy_conversation_id(
                    user_id, turn, valid_index
                )
                valid_index += 1
                if conversation_id in targets:
                    removed += 1
                else:
                    kept.append(item)
            data["questions"] = kept
            return removed

        data = history.load()
        removed = scrub(data)
        if removed:
            history.save(data)
        self._scrub_backups(user_id, scrub)
        return removed

    def scrub_document(self, user_id: str, history, document_id: str) -> int:
        def scrub(data: dict) -> int:
            old = list(data.get("questions", []))
            data["questions"] = [
                item
                for item in old
                if not isinstance(item, dict)
                or (
                    item.get("document_id") != document_id
                    and document_id not in (item.get("document_ids") or [])
                )
            ]
            data["documents"] = [
                item
                for item in data.get("documents", [])
                if not isinstance(item, dict)
                or item.get("document_id") != document_id
            ]
            return len(old) - len(data["questions"])

        data = history.load()
        removed = scrub(data)
        history.save(data)
        self._scrub_backups(user_id, scrub)
        return removed

    def _scrub_backups(self, user_id: str, scrub) -> None:
        if self.storage is None:
            return
        backup_root = self.storage.data_root / "legacy_backups"
        if not backup_root.is_dir():
            return
        for manifest_path in backup_root.glob("*/manifest.json"):
            manifest = read_json(manifest_path, default={})
            if not isinstance(manifest, dict) or manifest.get("user_id") != user_id:
                continue
            changed = False
            for entry in manifest.get("files", []):
                if not isinstance(entry, dict) or entry.get("kind") != "history":
                    continue
                relative = Path(str(entry.get("relative") or ""))
                source_root = manifest_path.parent / "source"
                source = (source_root / relative).resolve(strict=False)
                try:
                    source.relative_to(source_root.resolve(strict=False))
                except ValueError:
                    continue
                if not source.is_file():
                    continue
                repository = HistoryRepository(source)
                data = repository.load()
                if scrub(data):
                    repository.save(data)
                    entry["size"] = source.stat().st_size
                    entry["sha256"] = _sha256(source)
                    changed = True
            if changed:
                write_json_atomic(manifest_path, manifest)

    @staticmethod
    def _insert_turn(conn, user_id: str, turn: _LegacyTurn, index: int) -> None:
        identity = f"qa-legacy:{user_id}:{turn.record_digest}:{index}"
        conversation_id = _legacy_conversation_id(user_id, turn, index)
        turn_id = str(uuid5(NAMESPACE_URL, f"{identity}:turn"))
        user_message_id, assistant_message_id = sorted(
            (
                str(uuid5(NAMESPACE_URL, f"{identity}:message:0")),
                str(uuid5(NAMESPACE_URL, f"{identity}:message:1")),
            )
        )
        conn.execute(
            """
            insert into qa_conversations (
                id, user_id, title, origin, created_at, updated_at,
                last_message_at
            ) values (?, ?, ?, 'legacy_json', ?, ?, ?)
            """,
            (
                conversation_id,
                user_id,
                conversation_title(turn.question),
                turn.asked_at,
                turn.asked_at,
                turn.asked_at,
            ),
        )
        conn.executemany(
            """
            insert into qa_conversation_documents (
                conversation_id, user_id, document_id, document_name, position
            ) values (?, ?, ?, ?, ?)
            """,
            [
                (conversation_id, user_id, document_id, document_name, position)
                for position, (document_id, document_name) in enumerate(
                    zip(turn.document_ids, turn.document_names)
                )
            ],
        )
        conn.execute(
            """
            insert into qa_messages (
                id, conversation_id, user_id, turn_id, role, status, mode,
                content, source_state, memory_sync_status, created_at,
                updated_at, completed_at
            ) values (?, ?, ?, ?, 'user', 'completed', ?, ?, 'none',
                      'not_required', ?, ?, ?)
            """,
            (
                user_message_id,
                conversation_id,
                user_id,
                turn_id,
                turn.mode,
                turn.question,
                turn.asked_at,
                turn.asked_at,
                turn.asked_at,
            ),
        )
        conn.execute(
            """
            insert into qa_messages (
                id, conversation_id, user_id, turn_id, role, status, mode,
                content, source_state, memory_sync_status, created_at,
                updated_at, completed_at
            ) values (?, ?, ?, ?, 'assistant', 'completed', ?, ?,
                      'legacy_unavailable', 'not_required', ?, ?, ?)
            """,
            (
                assistant_message_id,
                conversation_id,
                user_id,
                turn_id,
                turn.mode,
                turn.answer,
                turn.asked_at,
                turn.asked_at,
                turn.asked_at,
            ),
        )


def _document_names(raw_documents: object) -> dict[str, str]:
    result: dict[str, str] = {}
    if not isinstance(raw_documents, list):
        return result
    for item in raw_documents:
        if not isinstance(item, dict):
            continue
        document_id = str(item.get("document_id") or "").strip()
        document_name = str(item.get("document_name") or "").strip()
        if document_id and document_name:
            result[document_id] = Path(document_name.replace("\\", "/")).name
    return result


def _safe_projection(item: object, documents: dict[str, str]) -> dict:
    if not isinstance(item, dict):
        return {"valid": False}
    question = item.get("question")
    answer = item.get("answer")
    asked_at = item.get("asked_at")
    raw_ids = item.get("document_ids")
    if not isinstance(raw_ids, list):
        scalar = item.get("document_id")
        raw_ids = [scalar] if isinstance(scalar, str) else []
    ids = [str(value).strip() for value in raw_ids if str(value).strip()]
    ids = sorted(set(ids))

    raw_names = item.get("document_names")
    supplied_names = raw_names if isinstance(raw_names, list) else []
    name_by_id = {
        str(document_id).strip(): Path(str(name).replace("\\", "/")).name
        for document_id, name in zip(raw_ids, supplied_names)
        if str(document_id).strip() and str(name).strip()
    }
    legacy_name = item.get("document")
    if len(ids) == 1 and isinstance(legacy_name, str) and legacy_name.strip():
        name_by_id.setdefault(ids[0], Path(legacy_name.replace("\\", "/")).name)
    names = [name_by_id.get(document_id) or documents.get(document_id) or document_id
             for document_id in ids]
    mode = str(item.get("mode") or "auto").strip().lower()
    if mode not in {"auto", "joint", "compare", "summary"}:
        mode = "auto"
    if mode == "compare" and len(ids) < 2:
        mode = "auto"
    valid = (
        isinstance(question, str)
        and bool(question.strip())
        and isinstance(answer, str)
        and bool(answer.strip())
        and bool(ids)
        and _valid_timestamp(asked_at)
    )
    return {
        "valid": valid,
        "question": question.strip() if isinstance(question, str) else "",
        "answer": answer.strip() if isinstance(answer, str) else "",
        "document_ids": ids,
        "document_names": names,
        "mode": mode,
        "asked_at": asked_at if isinstance(asked_at, str) else "",
    }


def _turn_from_projection(projection: dict) -> _LegacyTurn | None:
    if not projection.get("valid"):
        return None
    canonical = json.dumps(
        projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return _LegacyTurn(
        question=projection["question"],
        answer=projection["answer"],
        document_ids=tuple(projection["document_ids"]),
        document_names=tuple(projection["document_names"]),
        mode=projection["mode"],
        asked_at=projection["asked_at"],
        record_digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _legacy_conversation_id(user_id: str, turn: _LegacyTurn, index: int) -> str:
    identity = f"qa-legacy:{user_id}:{turn.record_digest}:{index}"
    return str(uuid5(NAMESPACE_URL, f"{identity}:conversation"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
