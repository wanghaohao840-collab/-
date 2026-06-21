# assistants/pdf_learning_assistant.py

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from hello_agents.tools.builtin.memory_tool import MemoryTool
from hello_agents.tools.builtin.rag_tool import RAGTool


class PDFLearningAssistant:
    """PDF 学习助手

    功能：
    1. 导入 PDF 文档
    2. 基于 PDF 内容问答
    3. 记录用户问题
    4. 添加学习笔记
    5. 回忆历史学习内容
    6. 查看学习统计
    """

    def __init__(self, user_id: str = "user123"):
        self.user_id = user_id
        self.session_id = datetime.now().strftime("session_%Y%m%d_%H%M%S")

        self.memory_tool = MemoryTool(
            user_id=user_id,
            memory_types=["working", "episodic", "semantic"]
        )

        self.rag_tool = RAGTool(
            knowledge_base_path="./knowledge_base",
            collection_name="pdf_learning_collection",
            rag_namespace=f"pdf_{user_id}"
        )

        self.current_document: Optional[str] = None
        self.current_document_id: Optional[str] = None

        self.history_path = Path("./memory_data") / f"learning_history_{user_id}.json"
        self.history_path.parent.mkdir(parents=True, exist_ok=True)

        self.history = self._load_history()

        self.stats = {
            "session_start": datetime.now().isoformat(),
            "documents_loaded": 0,
            "questions_asked": 0,
            "notes_added": 0,
        }

    def load_document(self, pdf_path: str) -> str:
        """导入文档，支持 PDF / TXT / Markdown"""

        path = Path(pdf_path)

        if not path.exists():
            return f"❌ 文件不存在: {pdf_path}"

        suffix = path.suffix.lower()

        supported_suffixes = [".pdf", ".txt", ".md", ".markdown", ".docx"]

        if suffix not in supported_suffixes:
            return f"❌ 当前只支持 PDF / TXT / Markdown / Word 文件: {suffix}"

        document_id = path.stem

        result = self.rag_tool.execute(
            "add_document",
            file_path=str(path),
            document_id=document_id,
            metadata={
                "user_id": self.user_id,
                "session_id": self.session_id,
                "document_id": document_id,
                "document_name": path.name,
                "document_path": str(path),
                "file_suffix": suffix,
                "source_type": "document",
            }
        )

        # 更新当前文档状态
        self.current_document = str(path)
        self.current_document_id = document_id

        # 更新统计
        self.stats["documents_loaded"] += 1

        # 记录到学习历史
        self.history.setdefault("documents", [])

        self.history["documents"].append({
            "document_id": document_id,
            "document_name": path.name,
            "document_path": str(path),
            "file_suffix": suffix,
            "session_id": self.session_id,
            "loaded_at": datetime.now().isoformat()
        })

        self._save_history()

        # 记录到记忆系统
        self.memory_tool.execute(
            "add",
            content=f"用户导入了文档：{path.name}",
            memory_type="episodic",
            importance=0.8,
            event_type="document_loaded",
            session_id=self.session_id,
            metadata={
                "document_id": document_id,
                "document_name": path.name,
                "document_path": str(path),
                "file_suffix": suffix,
            }
        )

        return result

    def ask(self, question: str, limit: int = 5) -> str:
        """基于当前 PDF 问答"""

        if not question.strip():
            return "❌ 问题不能为空"

        self.stats["questions_asked"] += 1

        self.memory_tool.execute(
            "add",
            content=f"用户提出问题：{question}",
            memory_type="working",
            importance=0.6,
            session_id=self.session_id
        )

        if not self.current_document_id:
            return "❌ 当前没有选择 PDF。请先上传 PDF 或在下拉框中选择一个 PDF。"

        summary_keywords = ["总结", "主要讲了什么", "核心内容", "概括", "主要内容", "全文"]

        summary_mode = any(keyword in question for keyword in summary_keywords)

        if summary_mode:
            limit = max(limit, 12)

        answer = self.rag_tool.execute(
            "ask",
            query=question,
            limit=limit,
            min_score=0.12,
            document_id=self.current_document_id,
            summary_mode=summary_mode
        )

        self.history["questions"].append({
            "question": question,
            "answer": answer,
            "document": self.current_document,
            "session_id": self.session_id,
            "asked_at": datetime.now().isoformat()
        })

        self._save_history()

        self.memory_tool.execute(
            "add",
            content=f"用户针对 PDF 提问：{question}\n系统回答：{answer[:300]}",
            memory_type="episodic",
            importance=0.7,
            event_type="pdf_qa",
            session_id=self.session_id
        )

        return answer

    def search(self, query: str, limit: int = 5) -> str:
        """只检索当前 PDF 内容，不生成回答"""

        if not query or not query.strip():
            return "❌ 检索关键词不能为空"

        if not self.current_document_id:
            return "❌ 当前没有选择 PDF。请先上传 PDF 或在下拉框中选择一个 PDF。"

        return self.rag_tool.execute(
            "search",
            query=query,
            limit=limit,
            min_score=0.08,
            document_id=self.current_document_id
        )

    def add_note(self, note: str, concept: Optional[str] = None) -> str:
        """添加学习笔记"""

        if not note.strip():
            return "❌ 笔记内容不能为空"

        self.stats["notes_added"] += 1

        content = note
        if concept:
            content = f"关于【{concept}】的学习笔记：{note}"

        result = self.memory_tool.execute(
            "add",
            content=content,
            memory_type="semantic",
            importance=0.85,
            knowledge_type="learning_note",
            concept=concept or "",
            session_id=self.session_id
        )

        self.history["notes"].append({
            "concept": concept or "",
            "note": note,
            "content": content,
            "session_id": self.session_id,
            "created_at": datetime.now().isoformat()
        })

        self._save_history()

        return result

    def clear_all_notes(self) -> str:
        """清空全部学习笔记：只清理学习历史中的 notes，不影响 PDF 文档和问答历史"""

        notes = self.history.get("notes", [])
        removed_notes = len(notes)

        # 1. 清空本地学习历史中的学习笔记
        self.history["notes"] = []

        # 2. 保存学习历史 JSON
        self._save_history()

        # 3. 重置统计
        if "notes_added" in self.stats:
            self.stats["notes_added"] = 0

        if "concepts_learned" in self.stats:
            self.stats["concepts_learned"] = 0

        return (
            "✅ 已清空全部学习笔记\n\n"
            f"- 删除历史学习笔记: {removed_notes} 条\n"
            "- PDF 文档和问答历史未删除\n"
            "- 当前 RAG 知识库未删除\n"
            "- 当前版本仅清理本地学习历史 notes"
        )

    def recall(self, query: str, limit: int = 5) -> str:
        """回忆历史学习内容：同时查询 MemoryTool 和本地学习历史 JSON"""

        if not query or not query.strip():
            return "❌ 回忆关键词不能为空"

        query = query.strip()

        # 1. 先查当前运行中的 MemoryTool
        memory_result = self.memory_tool.execute(
            "search",
            query=query,
            limit=limit
        )

        # 2. 再查本地 JSON 历史
        history_hits = []

        documents = self.history.get("documents", [])
        questions = self.history.get("questions", [])
        notes = self.history.get("notes", [])

        # 查历史文档
        for item in documents:
            text = f"{item.get('document_name', '')} {item.get('document_path', '')}"
            if query in text:
                history_hits.append(
                    f"[历史文档] {item.get('document_name')} | {item.get('loaded_at')}"
                )

        # 查历史问答
        for item in questions:
            question = str(item.get("question", ""))
            answer = str(item.get("answer", ""))

            if query in question or query in answer:
                short_answer = answer[:300].replace("\n", " ")
                history_hits.append(
                    f"[历史问答] 问题：{question}\n回答摘要：{short_answer}..."
                )

        # 查历史笔记
        for item in notes:
            concept = str(item.get("concept", ""))
            note = str(item.get("note", ""))
            content = str(item.get("content", ""))

            if query in concept or query in note or query in content:
                history_hits.append(
                    f"[历史笔记] 【{concept or '未命名概念'}】{note}"
                )

        # 3. 组合结果
        lines = []

        lines.append("一、当前记忆系统检索结果")
        lines.append(memory_result)

        lines.append("\n二、本地历史记录检索结果")

        if history_hits:
            for i, item in enumerate(history_hits[:limit], start=1):
                lines.append(f"{i}. {item}")
        else:
            lines.append(f"未在本地历史记录中找到与「{query}」相关的内容")

        return "\n".join(lines)

    def get_stats(self) -> str:
        """查看学习统计"""

        memory_summary = self.memory_tool.execute("summary")
        rag_stats = self.rag_tool.execute("stats")

        return (
            "📘 PDF 学习助手统计:\n"
            f"- user_id: {self.user_id}\n"
            f"- session_id: {self.session_id}\n"
            f"- 当前文档: {self.current_document or '暂无'}\n"
            f"- 当前文档ID: {self.current_document_id or '暂无'}\n"
            f"- 已导入文档数: {self.stats['documents_loaded']}\n"
            f"- 提问次数: {self.stats['questions_asked']}\n"
            f"- 学习笔记数: {self.stats['notes_added']}\n\n"
            f"{memory_summary}\n\n"
            f"{rag_stats}"
        )

    def generate_report(self) -> str:
        """生成学习报告"""

        memory_summary = self.memory_tool.execute("summary")
        rag_stats = self.rag_tool.execute("stats")

        documents = self.history.get("documents", [])
        questions = self.history.get("questions", [])
        notes = self.history.get("notes", [])

        recent_documents = documents[-5:]
        recent_questions = questions[-10:]
        recent_notes = notes[-10:]

        doc_text = "\n".join(
            [
                f"{i + 1}. {item.get('document_name')} | {item.get('loaded_at')}"
                for i, item in enumerate(recent_documents)
            ]
        ) or "暂无导入记录"

        qa_text = "\n\n".join(
            [
                f"{i + 1}. 问题：{item.get('question')}\n回答：{str(item.get('answer'))[:300]}..."
                for i, item in enumerate(recent_questions)
            ]
        ) or "暂无问答记录"

        note_text = "\n".join(
            [
                f"{i + 1}. 【{item.get('concept') or '未命名概念'}】{item.get('note')}"
                for i, item in enumerate(recent_notes)
            ]
        ) or "暂无学习笔记"

        report = f"""
    📘 PDF 智能学习报告

    一、学习基本信息
    - user_id: {self.user_id}
    - session_id: {self.session_id}
    - 当前文档: {self.current_document or "暂无"}
    - 历史导入文档数: {len(documents)}
    - 历史提问次数: {len(questions)}
    - 历史学习笔记数: {len(notes)}
    - 历史文件路径: {self.history_path}

    二、最近导入的文档
    {doc_text}

    三、记忆系统状态
    {memory_summary}

    四、RAG 知识库状态
    {rag_stats}

    五、最近问答记录
    {qa_text}

    六、最近学习笔记
    {note_text}

    七、推荐复习方向
    1. 优先复习最近保存的学习笔记，尤其是你标注过概念名称的内容。
    2. 回顾最近问过的问题，整理其中反复出现的关键词。
    3. 对当前 PDF 继续追问：“核心概念是什么？”、“有哪些重点章节？”、“有哪些易混点？”。
    4. 对重要知识点继续添加学习笔记，形成长期语义记忆。
    """

        return report.strip()

    def _load_history(self) -> Dict[str, Any]:
        """加载学习历史"""

        if not self.history_path.exists():
            return {
                "documents": [],
                "questions": [],
                "notes": [],
                "sessions": []
            }

        try:
            text = self.history_path.read_text(encoding="utf-8")
            data = json.loads(text)

            if not isinstance(data, dict):
                return {
                    "documents": [],
                    "questions": [],
                    "notes": [],
                    "sessions": []
                }

            data.setdefault("documents", [])
            data.setdefault("questions", [])
            data.setdefault("notes", [])
            data.setdefault("sessions", [])

            return data

        except Exception:
            return {
                "documents": [],
                "questions": [],
                "notes": [],
                "sessions": []
            }

    def _save_history(self) -> None:
        """保存学习历史"""

        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)

            self.history["last_updated"] = datetime.now().isoformat()

            self.history_path.write_text(
                json.dumps(self.history, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

        except Exception as e:
            print(f"[WARNING] 学习历史保存失败: {e}")

    def get_documents(self):
        """获取历史导入过的文档列表，用于 Gradio 下拉框"""

        documents = self.history.get("documents", [])

        choices = []

        seen = set()

        for item in documents:
            document_id = item.get("document_id") or Path(item.get("document_path", "")).stem
            document_name = item.get("document_name") or document_id

            if not document_id:
                continue

            if document_id in seen:
                continue

            seen.add(document_id)

            label = f"{document_name} | {document_id}"
            choices.append(label)

        return choices

    def select_document(self, selected: str) -> str:
        """选择当前 PDF 文档"""

        if not selected:
            return "❌ 请选择一个 PDF 文档"

        # selected 格式：文件名 | document_id
        if "|" in selected:
            document_id = selected.split("|")[-1].strip()
        else:
            document_id = selected.strip()

        documents = self.history.get("documents", [])

        matched = None

        for item in documents:
            item_document_id = item.get("document_id") or Path(item.get("document_path", "")).stem

            if item_document_id == document_id:
                matched = item
                break

        if matched:
            self.current_document_id = document_id
            self.current_document = matched.get("document_path")

            return (
                f"✅ 当前 PDF 已切换\n"
                f"- 文档名: {matched.get('document_name')}\n"
                f"- document_id: {self.current_document_id}\n"
                f"- 路径: {self.current_document}"
            )

        self.current_document_id = document_id

        return f"✅ 当前 document_id 已设置为: {self.current_document_id}"

    def delete_current_document(self) -> str:
        """删除当前选中的 PDF：删除 RAG chunks + 删除历史文档记录 + 重置当前 PDF"""

        if not self.current_document_id:
            return "❌ 当前没有选择 PDF，无法删除"

        document_id = self.current_document_id
        current_document = self.current_document

        # 1. 删除 RAG 知识库中的该文档 chunks
        rag_result = self.rag_tool.execute(
            "delete_document",
            document_id=document_id
        )

        # 2. 从历史 documents 中删除
        documents = self.history.get("documents", [])
        new_documents = []

        removed_docs = 0

        for item in documents:
            item_document_id = item.get("document_id") or Path(item.get("document_path", "")).stem

            if item_document_id == document_id:
                removed_docs += 1
                continue

            new_documents.append(item)

        self.history["documents"] = new_documents

        # 3. 可选：删除该 PDF 相关问答记录
        questions = self.history.get("questions", [])
        new_questions = []

        removed_questions = 0

        for item in questions:
            item_doc = str(item.get("document", ""))

            if current_document and item_doc == current_document:
                removed_questions += 1
                continue

            if document_id and document_id in item_doc:
                removed_questions += 1
                continue

            new_questions.append(item)

        self.history["questions"] = new_questions

        # 4. 保存历史
        self._save_history()

        # 5. 重置当前文档
        self.current_document_id = None
        self.current_document = None

        return (
            f"{rag_result}\n\n"
            f"🧹 学习历史已同步清理\n"
            f"- 删除历史文档记录: {removed_docs} 条\n"
            f"- 删除相关问答记录: {removed_questions} 条\n"
            f"- 当前 PDF 已重置"
        )

    def clear_all_documents(self) -> str:
        """清空全部 PDF：清空 RAG 知识库 + 清理学习历史中的文档和问答记录 + 重置当前 PDF"""

        # 1. 清空 RAG 知识库全部 chunks
        rag_result = self.rag_tool.execute("clear")

        # 2. 统计清理前数量
        old_documents = self.history.get("documents", [])
        old_questions = self.history.get("questions", [])

        removed_documents = len(old_documents)
        removed_questions = len(old_questions)

        # 3. 清空 PDF 文档历史和问答历史
        self.history["documents"] = []
        self.history["questions"] = []

        # 注意：这里不清空 notes
        # 因为学习笔记可能是用户主动保存的长期知识
        # 如果你想连笔记一起清空，可以把下面这一行取消注释
        # self.history["notes"] = []

        self._save_history()

        # 4. 重置当前 PDF 状态
        self.current_document = None
        self.current_document_id = None

        # 5. 重置统计中的文档和问题数量
        self.stats["documents_loaded"] = 0
        self.stats["questions_asked"] = 0

        return (
            f"✅ 已清空全部 PDF\n\n"
            f"{rag_result}\n\n"
            f"🧹 学习历史已同步清理\n"
            f"- 删除历史文档记录: {removed_documents} 条\n"
            f"- 删除相关问答记录: {removed_questions} 条\n"
            f"- 当前 PDF 已重置\n"
            f"- 学习笔记已保留"
        )

    def export_report_markdown(self) -> str:
        """导出学习报告为 Markdown 文件"""

        from pathlib import Path
        from datetime import datetime

        report_dir = Path("./reports")
        report_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path = report_dir / f"learning_report_{self.user_id}_{timestamp}.md"

        report = self.generate_report()

        file_path.write_text(report, encoding="utf-8")

        return str(file_path)

    def export_report_docx(self) -> str:
        """导出学习报告为格式更美观的 Word 文件"""

        from pathlib import Path
        from datetime import datetime

        from docx import Document
        from docx.shared import Pt
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn

        report_dir = Path("./reports")
        report_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path = report_dir / f"learning_report_{self.user_id}_{timestamp}.docx"

        report = self.generate_report()

        doc = Document()

        # =========================
        # 全局字体设置
        # =========================
        styles = doc.styles

        normal_style = styles["Normal"]
        normal_style.font.name = "微软雅黑"
        normal_style._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
        normal_style.font.size = Pt(11)

        # =========================
        # 标题
        # =========================
        title = doc.add_heading("PDF 智能学习报告", level=1)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER

        for run in title.runs:
            run.font.name = "微软雅黑"
            run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
            run.font.size = Pt(20)
            run.bold = True

        # =========================
        # 基本信息
        # =========================
        info = doc.add_paragraph()
        info.alignment = WD_ALIGN_PARAGRAPH.CENTER

        run = info.add_run(
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}    "
            f"用户：{self.user_id}"
        )
        run.font.name = "微软雅黑"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
        run.font.size = Pt(10)

        doc.add_paragraph("")

        # =========================
        # 正文
        # =========================
        for raw_line in report.splitlines():
            line = raw_line.strip()

            if not line:
                doc.add_paragraph("")
                continue

            # 去掉开头的报告标题，避免重复
            if line.startswith("📘 PDF 智能学习报告"):
                continue

            # 一级小节标题
            if (
                    line.startswith("一、")
                    or line.startswith("二、")
                    or line.startswith("三、")
                    or line.startswith("四、")
                    or line.startswith("五、")
                    or line.startswith("六、")
                    or line.startswith("七、")
                    or line.startswith("八、")
                    or line.startswith("九、")
                    or line.startswith("十、")
            ):
                heading = doc.add_heading(line, level=2)

                for run in heading.runs:
                    run.font.name = "微软雅黑"
                    run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
                    run.font.size = Pt(15)
                    run.bold = True

                continue

            # 项目符号
            if line.startswith("- "):
                p = doc.add_paragraph(style="List Bullet")
                run = p.add_run(line[2:])
                run.font.name = "微软雅黑"
                run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
                run.font.size = Pt(11)
                continue

            # 数字列表
            if len(line) > 2 and line[0].isdigit() and line[1] in [".", "．", "、"]:
                p = doc.add_paragraph(style="List Number")
                run = p.add_run(line)
                run.font.name = "微软雅黑"
                run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
                run.font.size = Pt(11)
                continue

            # 普通段落
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(6)
            p.paragraph_format.line_spacing = 1.25

            run = p.add_run(line)
            run.font.name = "微软雅黑"
            run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
            run.font.size = Pt(11)

        # =========================
        # 页脚式结尾
        # =========================
        doc.add_paragraph("")
        end = doc.add_paragraph("—— 由 PDF 智能学习助手自动生成 ——")
        end.alignment = WD_ALIGN_PARAGRAPH.CENTER

        for run in end.runs:
            run.font.name = "微软雅黑"
            run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
            run.font.size = Pt(9)
            run.italic = True

        doc.save(str(file_path))

        return str(file_path)


