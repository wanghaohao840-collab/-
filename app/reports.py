from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.database import transaction
from app.storage import UserStorage, write_text_atomic


@dataclass(frozen=True)
class ReportRecord:
    id: str
    user_id: str
    title: str
    relative_path: str
    created_at: str


class ReportService:
    def __init__(self, db_path: Path | str, storage: UserStorage):
        self.db_path = Path(db_path)
        self.storage = storage

    def create_markdown_snapshot(self, user_id: str, title: str, content: str) -> ReportRecord:
        report_id = str(uuid.uuid4())
        path = self.storage.report_path(user_id, report_id, ".md")
        write_text_atomic(path, content)
        record = ReportRecord(
            id=report_id,
            user_id=user_id,
            title=title,
            relative_path=f"reports/{report_id}.md",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        try:
            with transaction(self.db_path) as conn:
                conn.execute(
                    """
                    insert into report_records (id, user_id, title, relative_path, created_at)
                    values (?, ?, ?, ?, ?)
                    """,
                    (record.id, record.user_id, record.title, record.relative_path, record.created_at),
                )
        except Exception:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            raise
        return record

    def list_reports(self, user_id: str) -> list[ReportRecord]:
        with transaction(self.db_path) as conn:
            rows = conn.execute(
                """
                select id, user_id, title, relative_path, created_at
                from report_records
                where user_id = ?
                order by created_at desc
                """,
                (user_id,),
            ).fetchall()
        records = [ReportRecord(**dict(row)) for row in rows]
        return [
            record
            for record in records
            if self.storage.assert_within_user(user_id, self.storage.user_paths(user_id).root / record.relative_path).exists()
        ]

    def read_report(self, user_id: str, report_id: str) -> str:
        with transaction(self.db_path) as conn:
            row = conn.execute(
                "select relative_path from report_records where user_id = ? and id = ?",
                (user_id, report_id),
            ).fetchone()
        if row is None:
            raise FileNotFoundError(report_id)
        path = self.storage.assert_within_user(
            user_id,
            self.storage.user_paths(user_id).root / row["relative_path"],
        )
        return path.read_text(encoding="utf-8")

    def report_file_path(self, user_id: str, report_id: str) -> Path:
        with transaction(self.db_path) as conn:
            row = conn.execute(
                "select relative_path from report_records where user_id = ? and id = ?",
                (user_id, report_id),
            ).fetchone()
        if row is None:
            raise FileNotFoundError(report_id)
        return self.storage.assert_within_user(
            user_id,
            self.storage.user_paths(user_id).root / row["relative_path"],
        )
