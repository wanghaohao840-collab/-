from __future__ import annotations

import hashlib
import json
import logging
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.database import transaction
from app.history import HistoryRepository
from app.storage import SUPPORTED_DOCUMENT_SUFFIXES, UserStorage, read_json, write_json_atomic

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MigrationResult:
    status: str
    migration_key: str
    backup_path: str | None = None
    manifest_path: str | None = None
    skipped_summary: str | None = None
    error_summary: str | None = None
    conflict_summary: str | None = None


class LegacyMigrationService:
    """Claims identifiable single-user legacy data through a validated staging area."""

    def __init__(self, db_path: Path | str, storage: UserStorage, legacy_root: Path | str):
        self.db_path = Path(db_path)
        self.storage = storage
        self.legacy_root = Path(legacy_root).resolve()
        self.migration_key = "legacy-user123"

    def _files(self) -> dict[str, list[Path]]:
        files = [path for path in self.legacy_root.rglob("*") if path.is_file()]
        # F6: Exclude the active data root, legacy backups, and migration
        # staging trees so repeated scans never discover migrated output or
        # backup copies.
        excluded = {
            self.storage.data_root.resolve(),
            self.storage.data_root.resolve() / "legacy_backups",
            self.storage.data_root.resolve() / "migration_staging",
        }
        _is_excluded = files.copy()
        files = [
            path for path in files
            if not any(
                path.resolve() == excluded_root
                or str(path.resolve()).startswith(str(excluded_root) + "\\")
                or str(path.resolve()).startswith(str(excluded_root) + "/")
                for excluded_root in excluded
            )
        ]
        history = [
            path for path in files
            if path.name == "learning_history_user123.json"
        ]
        documents = [
            path for path in files
            if path.suffix.lower() in SUPPORTED_DOCUMENT_SUFFIXES
            and any(part.lower() in {"uploads", "documents"} for part in path.parts)
        ]
        rag = [
            path for path in files
            if path.suffix.lower() == ".json" and "rag" in path.name.lower()
        ]
        reports = [
            path for path in files
            if path.suffix.lower() in {".md", ".docx"}
            and "report" in str(path.parent).lower()
        ]
        memory = [
            path for path in files
            if path.suffix.lower() == ".json"
            and "memor" in path.name.lower()
            and path not in history
        ]
        claimed = set(history + documents + rag + reports + memory)
        return {
            "history": history,
            "documents": documents,
            "rag_cache": rag,
            "reports": reports,
            "memory": memory,
            "skipped": [path for path in files if path not in claimed],
        }

    def scan(self) -> dict:
        groups = self._files()
        result = {}
        for kind, paths in groups.items():
            result[kind] = {
                "exists": bool(paths),
                "count": len(paths),
                "size": sum(path.stat().st_size for path in paths),
                "paths": [str(path) for path in paths],
            }
            if kind == "history":
                result[kind]["path"] = str(paths[0]) if paths else str(
                    self.legacy_root / "memory_data" / "learning_history_user123.json"
                )
        return result

    def claim(self, user_id: str) -> MigrationResult:
        now = datetime.now(timezone.utc).isoformat()
        with transaction(self.db_path) as conn:
            row = conn.execute(
                "select * from data_migrations where migration_key = ?",
                (self.migration_key,),
            ).fetchone()
            # F1: Ownership check MUST happen before any status return.
            # A different user must never receive success or claimant
            # details — not for completed, failed, or in-progress rows.
            if row is not None and row["claimed_by_user_id"] != user_id:
                return MigrationResult(
                    "blocked", self.migration_key,
                    error_summary=(
                        "Migration has already been claimed"
                    ),
                )
            if row and row["status"] == "completed":
                return MigrationResult(
                    status=row["status"],
                    migration_key=row["migration_key"],
                    backup_path=row["backup_path"],
                    manifest_path=row["manifest_path"],
                    skipped_summary=row["skipped_summary"],
                    error_summary=row["error_summary"],
                    conflict_summary=row["conflict_summary"],
                )
            if row is None:
                conn.execute(
                    """insert into data_migrations
                       (migration_key, claimed_by_user_id, status, started_at)
                       values (?, ?, 'in_progress', ?)""",
                    (self.migration_key, user_id, now),
                )
            else:
                conn.execute(
                    """update data_migrations set
                       status = 'in_progress', started_at = ?, completed_at = null,
                       error_summary = null, conflict_summary = null
                       where migration_key = ?""",
                    (now, self.migration_key),
                )

        run_id = str(uuid.uuid4())
        backup_dir = self.storage.data_root / "legacy_backups" / f"{self.migration_key}-{run_id}"
        staging = self.storage.data_root / "migration_staging" / f"{self.migration_key}-{run_id}"
        manifest_path = backup_dir / "manifest.json"
        try:
            groups = self._files()
            backup_dir.mkdir(parents=True, exist_ok=False)
            staging.mkdir(parents=True, exist_ok=False)
            manifest = {"migration_key": self.migration_key, "user_id": user_id, "files": []}
            for kind, paths in groups.items():
                for source in paths:
                    relative = source.relative_to(self.legacy_root)
                    backup = backup_dir / "source" / relative
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, backup)
                    manifest["files"].append({
                        "kind": kind,
                        "source": str(source),
                        "relative": str(relative),
                        "size": source.stat().st_size,
                        "sha256": self._sha256(source),
                    })
            write_json_atomic(manifest_path, manifest)
            publish_summary = self._stage_and_commit(user_id, groups, staging)
            skipped = publish_summary.get("skipped_summary",
                                           f"{len(groups['skipped'])} unrecognized file(s)")
            conflicts = publish_summary.get("conflict_summary")
            with transaction(self.db_path) as conn:
                conn.execute(
                    """update data_migrations set status = 'completed', completed_at = ?,
                       backup_path = ?, manifest_path = ?, skipped_summary = ?,
                       conflict_summary = ?, error_summary = null
                       where migration_key = ?""",
                    (datetime.now(timezone.utc).isoformat(), str(backup_dir),
                     str(manifest_path), skipped, conflicts, self.migration_key),
                )
            shutil.rmtree(staging, ignore_errors=True)
            return MigrationResult(
                "completed", self.migration_key, str(backup_dir),
                str(manifest_path), skipped, conflict_summary=conflicts,
            )
        except Exception as exc:
            shutil.rmtree(staging, ignore_errors=True)
            sanitized = _sanitize_error(exc)
            with transaction(self.db_path) as conn:
                conn.execute(
                    """update data_migrations set status = 'failed', backup_path = ?,
                       manifest_path = ?, error_summary = ? where migration_key = ?""",
                    (str(backup_dir), str(manifest_path), sanitized, self.migration_key),
                )
            return MigrationResult(
                "failed", self.migration_key, str(backup_dir),
                str(manifest_path), error_summary=sanitized,
            )

    # ── staging, validation, publication, rollback ─────────────────────

    def _stage_and_commit(self, user_id: str, groups: dict[str, list[Path]],
                          staging: Path) -> dict[str, str]:
        """Stage every artifact, validate, then publish atomically.

        Returns a summary dict with ``skipped_summary`` and optional
        ``conflict_summary`` used by :meth:`claim`.
        """
        paths = self.storage.ensure_user_dirs(user_id)

        # ── Phase 1: Stage everything to the staging directory ────────
        plan: dict[str, list] = {
            "files": [],           # {source, target, kind}
            "report_inserts": [],  # {report_id, title}
            "skipped": [],         # str descriptions
            "conflicts": [],       # str descriptions
        }
        ambiguous_memories = 0

        if groups["history"]:
            self._stage_history(groups["history"][0], staging, paths, plan)

        for source in groups["documents"]:
            self._stage_document(source, staging, paths, plan)

        if groups["rag_cache"]:
            self._stage_rag(groups["rag_cache"][0], staging, paths, plan)

        if groups["memory"]:
            ambiguous_memories = self._stage_memory(
                groups["memory"][0], user_id, staging, paths, plan)

        for source in groups["reports"]:
            self._stage_report(source, user_id, staging, paths, plan)

        # ── Phase 2: Validate staged artifacts ────────────────────────
        self._validate_plan(plan)

        # ── Phase 3: Publish to final destinations ────────────────────
        self._publish_plan(plan, user_id)

        # ── Build readable summaries ──────────────────────────────────
        skipped_parts = []
        if ambiguous_memories:
            skipped_parts.append(f"{ambiguous_memories} ambiguous memory item(s)")
        if groups["skipped"]:
            skipped_parts.append(
                f"{len(groups['skipped'])} unrecognized file(s)")
        if plan["skipped"]:
            skipped_parts.extend(plan["skipped"])

        result: dict[str, str] = {
            "skipped_summary": "; ".join(skipped_parts) if skipped_parts
                               else "none",
        }
        if plan["conflicts"]:
            result["conflict_summary"] = "; ".join(plan["conflicts"])
        return result

    # ── per-kind staging helpers ──────────────────────────────────────

    def _stage_history(self, source: Path, staging: Path, paths,
                       plan: dict) -> None:
        data = read_json(source, default={})
        staged = staging / "history.json"
        HistoryRepository(staged).save(data)
        HistoryRepository(staged).load()  # validate
        plan["files"].append({
            "source": staged, "target": paths.history, "kind": "history",
        })

    def _stage_document(self, source: Path, staging: Path, paths,
                        plan: dict) -> None:
        relative = source.relative_to(self.legacy_root)
        document_id = self._deterministic_id("doc", str(relative))
        target = paths.documents / f"{document_id}{source.suffix}"

        conflict = self._check_conflict(source, target, f"document {relative}")
        if conflict == "same":
            return  # idempotent — already present
        if conflict == "different":
            plan["conflicts"].append(f"document {relative} already exists; skipped")
            return

        staged_doc = staging / "documents" / target.name
        self._copy_validated(source, staged_doc)
        plan["files"].append({
            "source": staged_doc, "target": target, "kind": "document",
        })

    def _stage_rag(self, source: Path, staging: Path, paths,
                   plan: dict) -> None:
        data = read_json(source, default={})
        staged = staging / "rag_cache.json"
        write_json_atomic(staged, data)
        read_json(staged, default={})  # validate round-trip
        plan["files"].append({
            "source": staged, "target": paths.rag_cache, "kind": "rag_cache",
        })

    def _stage_memory(self, source: Path, user_id: str, staging: Path,
                      paths, plan: dict) -> int:
        data = read_json(source, default={})
        memories = data.get("memories", []) if isinstance(data, dict) else []
        owned = [
            item for item in memories
            if (item.get("metadata") or {}).get("user_id") in {"user123", user_id}
        ]
        ambiguous = len(memories) - len(owned)
        staged = staging / "memory.json"
        write_json_atomic(staged, {"user_id": user_id, "memories": owned})
        read_json(staged, default={})  # validate round-trip
        plan["files"].append({
            "source": staged, "target": paths.memory_snapshot, "kind": "memory",
        })
        return ambiguous

    def _stage_report(self, source: Path, user_id: str, staging: Path,
                      paths, plan: dict) -> None:
        if source.suffix.lower() != ".md":
            return
        relative = source.relative_to(self.legacy_root)
        report_id = self._deterministic_id("report", str(relative))
        expected_path = f"reports/{report_id}.md"
        target = paths.reports / f"{report_id}.md"

        # Query all three identity columns.  Only an exact match
        # (same user, expected relative_path, file present) is
        # idempotent.  Everything else is a conflict whose message
        # must not expose another user's ID.
        with transaction(self.db_path) as conn:
            existing = conn.execute(
                "select id, user_id, relative_path from report_records "
                "where id = ?",
                (report_id,),
            ).fetchone()
        if existing:
            if existing["user_id"] != user_id:
                plan["conflicts"].append(
                    f"report {relative} already claimed; skipped")
                return
            if existing["relative_path"] != expected_path:
                plan["conflicts"].append(
                    f"report {relative} row path mismatch; skipped")
                return
            if not target.exists():
                plan["conflicts"].append(
                    f"report {relative} file missing from expected path; "
                    "skipped")
                return
            # Same user, correct path, file present — but content
            # must also match for true idempotency.
            if self._sha256(source) != self._sha256(target):
                plan["conflicts"].append(
                    f"report {relative} content differs from existing "
                    "record; skipped")
                return
            return  # idempotent — same user, correct path, file present, content matches

        # No existing row — check for file-level collision at destination.
        conflict = self._check_conflict(source, target, f"report {relative}")
        if conflict == "same":
            # Row is missing but file is present — insert the row.
            plan["report_inserts"].append(
                {"report_id": report_id, "title": source.stem})
            return
        if conflict == "different":
            plan["conflicts"].append(
                f"report {relative} already exists; skipped")
            return

        staged_report = staging / "reports" / target.name
        self._copy_validated(source, staged_report)
        plan["files"].append({
            "source": staged_report, "target": target, "kind": "report",
        })
        plan["report_inserts"].append(
            {"report_id": report_id, "title": source.stem})

    # ── publication helpers ───────────────────────────────────────────

    def _validate_plan(self, plan: dict) -> None:
        """Ensure every staged file in *plan* exists and has non-zero size."""
        for entry in plan["files"]:
            src = entry["source"]
            if not src.exists():
                raise FileNotFoundError(f"Staged artifact missing: {src}")
            if src.stat().st_size == 0:
                raise ValueError(f"Staged artifact is empty: {src}")

    def _publish_plan(self, plan: dict, user_id: str) -> None:
        """Copy staged files to final destinations via atomic temporary
        files; insert report rows.  Pre-existing destination files are
        journaled so they can be restored on failure.

        Every ``.migrating`` temp file is cleaned up in a ``finally``
        block regardless of outcome.  ``.pre-migration.bak`` journals
        are removed only after all files and report rows have committed
        successfully.
        """
        published: list[Path] = []
        # {target: backup_of_original}
        replaced: dict[Path, Path] = {}
        inserted_report_ids: list[str] = []
        # Track every .migrating temp so it is cleaned unconditionally.
        tmp_paths: list[Path] = []
        try:
            for entry in plan["files"]:
                target = entry["target"]
                if target.exists():
                    backup = target.with_name(
                        f".{target.name}.pre-migration.bak")
                    shutil.copy2(target, backup)
                    replaced[target] = backup
                # Publish via same-directory temporary file then
                # atomic rename so a failed copy never leaves a
                # partial target.
                tmp = target.with_name(f".{target.name}.migrating")
                tmp_paths.append(tmp)
                self._copy_validated(entry["source"], tmp)
                tmp.replace(target)
                published.append(target)
            for rep in plan["report_inserts"]:
                with transaction(self.db_path) as conn:
                    conn.execute(
                        """insert into report_records (id, user_id, title,
                           relative_path, created_at) values (?, ?, ?, ?, ?)""",
                        (rep["report_id"], user_id, rep["title"],
                         f"reports/{rep['report_id']}.md",
                         datetime.now(timezone.utc).isoformat()),
                    )
                inserted_report_ids.append(rep["report_id"])
        except Exception:
            self._rollback(published, inserted_report_ids, replaced)
            raise
        finally:
            # F2: always clean .migrating temp files.
            for tmp in tmp_paths:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except Exception as exc:
                    logger.warning(
                        "migrating temp cleanup failed for %s: %s", tmp, exc)
        # F3: only after complete success, remove pre-migration journals.
        for backup in replaced.values():
            try:
                if backup.exists():
                    backup.unlink()
            except Exception as exc:
                logger.warning(
                    "journal cleanup failed for %s: %s", backup, exc)

    def _rollback(self, published_files: list[Path],
                  report_ids: list[str],
                  replaced: dict[Path, Path] | None = None) -> None:
        """Best-effort reversal of partially published state.
        Restores pre-existing files from their journals when available."""
        if replaced is None:
            replaced = {}
        for path in published_files:
            try:
                backup = replaced.get(path)
                if backup is not None and backup.exists():
                    backup.replace(path)
                elif path.exists():
                    path.unlink()
            except Exception as exc:
                logger.warning("rollback unlink failed for %s: %s", path, exc)
        # Clean up any unreferenced journals.
        for backup in replaced.values():
            try:
                if backup.exists():
                    backup.unlink()
            except Exception:
                pass
        for rid in report_ids:
            try:
                with transaction(self.db_path) as conn:
                    conn.execute("delete from report_records where id = ?", (rid,))
            except Exception as exc:
                logger.warning(
                    "rollback report row delete failed for %s: %s", rid, exc)

    # ── identity and conflict helpers ─────────────────────────────────

    def _deterministic_id(self, kind: str, relative_path: str) -> str:
        """Return a stable ID so retry publishes each artifact once."""
        key = f"{self.migration_key}|{kind}|{relative_path}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _check_conflict(self, source: Path, target: Path,
                        label: str) -> str:
        """Return ``"same"``, ``"different"``, or ``"absent"``."""
        if not target.exists():
            return "absent"
        if self._sha256(source) == self._sha256(target):
            return "same"
        return "different"

    # ── validated copy ────────────────────────────────────────────────

    @staticmethod
    def _copy_validated(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if source.stat().st_size != target.stat().st_size:
            raise IOError(f"Migration copy validation failed: {source}")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()


def _sanitize_error(exc: Exception) -> str:
    """Remove filesystem paths and credentials from error messages stored
    in the DB."""
    msg = str(exc)
    # First remove pure-path lines, then scrub embedded paths from
    # the remaining lines.
    lines = [line for line in msg.splitlines()
             if not _looks_like_pure_path(line)]
    scrubbed = [_scrub_embedded_paths(line) for line in lines]
    detail = " | ".join(line.strip() for line in scrubbed if line.strip())
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def _looks_like_pure_path(text: str) -> bool:
    """Return True when *text* is only a filesystem path with no
    message attached."""
    stripped = text.strip()
    # A line that looks like it's nothing but a path.
    if _is_drive_letter_prefix(stripped) and stripped.count("\\") >= 2:
        return True
    if stripped.startswith("/") and ("/" in stripped[1:]):
        return True
    if stripped.startswith("\\\\"):
        return True
    return False


def _scrub_embedded_paths(text: str) -> str:
    """Replace any embedded absolute paths in *text* with ``[...]``."""
    import re
    # Windows drive-letter paths: C:\..., D:\...
    text = re.sub(
        r'(^|\s|[("""])[A-Za-z]:\\(?:[^\\/:*?""<>|\s]+\\)*[^\\/:*?""<>|.\s]+',
        r'\1[...]',
        text,
    )
    # Windows UNC paths: \\server\share\...
    text = re.sub(
        r'(^|\s|[("""])\\\\[^\s]+\\(?:[^\\/:*?""<>|\s]+\\)*[^\\/:*?""<>|.\s]+',
        r'\1[...]',
        text,
    )
    # Unix absolute paths: /home/...
    text = re.sub(
        r'(^|\s|[("""])/(?:[^\s/]+/)+[^\s/]+',
        r'\1[...]',
        text,
    )
    # URL credentials: https://user:password@host
    text = re.sub(r'://[^:@\s]+:[^@\s]+@', r'://creds@', text)
    return text


def _is_drive_letter_prefix(text: str) -> bool:
    return len(text) >= 2 and text[1] == ":" and text[0].isalpha()
