from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class InsightsService:
    """User-scoped read model assembled through public domain interfaces."""

    def __init__(self, sessions, documents, qa, notes) -> None:
        self.sessions = sessions
        self.documents = documents
        self.qa = qa
        self.notes = notes

    def stats(self, token: str, *, days: int = 30) -> dict:
        if not 7 <= days <= 90:
            raise ValueError("days must be between 7 and 90")
        session = self.sessions.get_session(token)
        documents = self.documents.list_documents(token)
        completed_turn_count = self.qa.completed_turn_count(token)
        note_count = self.notes.count(session)
        report_count = len(session.runtime.reports.list_reports(session.user_id))
        today = datetime.now(timezone.utc).date()
        start = today - timedelta(days=days - 1)
        since = datetime.combine(start, datetime.min.time(), timezone.utc).isoformat().replace("+00:00", "Z")
        document_dates = [item.loaded_at for item in documents if item.loaded_at and _utc(item.loaded_at).date() >= start]
        qa_dates = self.qa.completed_activity_dates(token, since=since)
        note_dates = self.notes.activity_dates(session, since=since)
        by_kind = {
            "documents": Counter(_utc(value).date() for value in document_dates),
            "questions": Counter(_utc(value).date() for value in qa_dates),
            "notes": Counter(_utc(value).date() for value in note_dates),
        }
        activity = []
        active_days = 0
        for offset in range(days):
            day = start + timedelta(days=offset)
            row = {"date": day.isoformat(), **{key: values[day] for key, values in by_kind.items()}}
            row["total"] = row["documents"] + row["questions"] + row["notes"]
            active_days += int(row["total"] > 0)
            activity.append(row)
        return {
            "document_count": len(documents), "completed_question_count": completed_turn_count,
            "note_count": note_count, "report_count": report_count,
            "active_days": active_days, "window_days": days, "activity": activity,
        }

    def overview(self, token: str) -> dict:
        stats = self.stats(token, days=30)
        documents = self.documents.list_documents(token)[:5]
        turns = self.qa.recent_completed_turns(token, limit=5)
        return {
            "stats": stats,
            "recent_documents": [
                {"document_id": item.document_id, "name": item.name, "loaded_at": item.loaded_at}
                for item in documents
            ],
            "recent_questions": [
                {"question": item.question, "asked_at": item.asked_at, "document_names": list(item.document_names)}
                for item in turns
            ],
        }

    def list_reports(self, token: str) -> list[dict]:
        session = self.sessions.get_session(token)
        return [
            {"id": item.id, "title": item.title, "created_at": item.created_at}
            for item in session.runtime.reports.list_reports(session.user_id)
        ]

    def read_report(self, token: str, report_id: str) -> dict:
        session = self.sessions.get_session(token)
        content = session.runtime.reports.read_report(session.user_id, report_id)
        record = next((item for item in session.runtime.reports.list_reports(session.user_id) if item.id == report_id), None)
        if record is None:
            raise FileNotFoundError(report_id)
        return {"id": record.id, "title": record.title, "created_at": record.created_at, "content": content}

    def create_report(self, token: str) -> dict:
        session = self.sessions.get_session(token)
        stats = self.stats(token, days=30)
        recent_documents = self.documents.list_documents(token)[:5]
        recent_turns = self.qa.recent_completed_turns(token, limit=10)
        recent_notes = self.notes.recent(session, limit=10)
        lines = [
            "# 知研学习报告", "", f"生成时间：{datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}", "",
            "## 学习概况", "",
            f"- 文档：{stats['document_count']}", f"- 已完成问答：{stats['completed_question_count']}",
            f"- 学习笔记：{stats['note_count']}", f"- 近 30 天活跃天数：{stats['active_days']}", "",
            "## 最近文档", "",
            *([f"- {item.name}" for item in recent_documents] or ["- 暂无文档"]), "",
            "## 最近问答", "",
            *([f"- {item.question}" for item in recent_turns] or ["- 暂无问答"]), "",
            "## 最近笔记", "",
            *([f"- {item.concept or item.body_markdown[:80]}" for item in recent_notes] or ["- 暂无笔记"]), "",
            "> 统计基于当前保留的真实记录，不推断阅读时长或知识掌握度。", "",
        ]
        with session.runtime.lock:
            record = session.runtime.reports.create_markdown_snapshot(
                session.user_id, "知研学习报告", "\n".join(lines)
            )
        return self.read_report(token, record.id)

    def download_path(self, token: str, report_id: str, format: str) -> Path:
        session = self.sessions.get_session(token)
        if format == "md":
            return session.runtime.reports.report_file_path(session.user_id, report_id)
        if format == "docx":
            return Path(session.assistant.export_report_docx(report_id=report_id))
        raise ValueError("format")
