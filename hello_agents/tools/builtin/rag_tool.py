# hello_agents/tools/builtin/rag_tool.py

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Optional

from hello_agents.tools.base import Tool
from hello_agents.core.llm import HelloAgentsLLM
from hello_agents.memory.rag.pipeline import create_rag_pipeline


class RAGTool(Tool):
    """RAG工具

    支持：
    - add_text：添加文本知识
    - add_document：添加 txt / md / pdf 文档
    - search：检索知识库
    - ask：基于检索结果问答
    - stats：查看知识库统计
    - clear：清空知识库
    """

    def __init__(
        self,
        knowledge_base_path: str = "./knowledge_base",
        qdrant_url: Optional[str] = None,
        qdrant_api_key: Optional[str] = None,
        collection_name: str = "rag_knowledge_base",
        rag_namespace: str = "default"
    ):
        super().__init__(
            name="rag",
            description="RAG工具 - 支持文档添加、知识库检索和文档问答"
        )

        self.knowledge_base_path = knowledge_base_path
        self.qdrant_url = qdrant_url
        self.qdrant_api_key = qdrant_api_key
        self.collection_name = collection_name
        self.rag_namespace = rag_namespace

        self._pipelines: Dict[str, Any] = {}

        self.llm = HelloAgentsLLM()

        default_pipeline = create_rag_pipeline(
            qdrant_url=self.qdrant_url,
            qdrant_api_key=self.qdrant_api_key,
            collection_name=self.collection_name,
            rag_namespace=self.rag_namespace
        )

        self._pipelines[self.rag_namespace] = default_pipeline

    def get_parameters(self) -> Dict[str, Any]:
        """Tool 抽象方法：参数说明"""

        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "操作类型：add_text/add_document/search/ask/stats/delete_document/clear"
                },
                "text": {
                    "type": "string",
                    "description": "要添加的文本"
                },
                "file_path": {
                    "type": "string",
                    "description": "要导入的本地文档路径，支持 .txt / .md / .pdf"
                },
                "query": {
                    "type": "string",
                    "description": "检索或问答的问题"
                },
                "document_id": {
                    "type": "string",
                    "description": "文档 ID，可用于限制只检索某个文档"
                },
                "limit": {
                    "type": "integer",
                    "description": "返回结果数量"
                },
                "min_score": {
                    "type": "number",
                    "description": "最低相似度分数"
                },
                "rag_namespace": {
                    "type": "string",
                    "description": "RAG 命名空间"
                }
            },
            "required": ["action"]
        }

    def run(self, action: Optional[str] = None, **kwargs) -> str:
        """Tool 抽象方法：运行入口"""

        if action is None:
            action = kwargs.pop("action", None)

        return self.execute(action, **kwargs)

    def execute(self, action: Optional[str] = None, **kwargs) -> str:
        """RAGTool 统一执行入口"""

        if not action:
            return "❌ 请提供 action，例如 add_text/add_document/search/ask/stats/clear"

        action = action.lower().strip()

        try:
            if action == "add_text":
                return self._add_text(**kwargs)

            if action == "add_document":
                return self._add_document(**kwargs)

            if action == "search":
                return self._search(**kwargs)

            if action in ["citation", "cite", "format_citations"]:
                return self._citation(**kwargs)

            if action == "ask":
                return self._ask(**kwargs)

            if action == "stats":
                return self._stats(**kwargs)

            if action == "delete_document":
                return self._delete_document(**kwargs)

            if action == "clear":
                return self._clear(**kwargs)

            return f"❌ 不支持的 RAG 操作: {action}"

        except Exception as e:
            return f"❌ RAG 操作失败: {e}"

    def _get_pipeline(self, rag_namespace: Optional[str] = None):
        """获取指定 namespace 的 RAG 管道"""

        namespace = rag_namespace or self.rag_namespace

        if namespace not in self._pipelines:
            self._pipelines[namespace] = create_rag_pipeline(
                qdrant_url=self.qdrant_url,
                qdrant_api_key=self.qdrant_api_key,
                collection_name=self.collection_name,
                rag_namespace=namespace,
            )

        return self._pipelines[namespace]

    def _add_text(
            self,
            text: str,
            document_id: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None,
            rag_namespace: Optional[str] = None,
            replace_existing: bool = True,
            save_cache: bool = True,
            **kwargs
    ) -> str:
        """添加文本知识"""

        pipeline = self._get_pipeline(rag_namespace)

        result = pipeline.add_text(
            text=text,
            document_id=document_id,
            metadata=metadata or {},
            replace_existing=replace_existing,
            save_cache=save_cache,
        )

        if result.get("success"):
            msg = (
                f"✅ 文本知识添加成功\n"
                f"- document_id: {result.get('document_id')}\n"
                f"- chunks_added: {result.get('chunks_added')}"
            )

            if result.get("chunks_removed"):
                msg += f"\n- chunks_removed: {result.get('chunks_removed')}"

            if result.get("cache_path"):
                msg += f"\n- cache_path: {result.get('cache_path')}"

            return msg

        return f"❌ 文本知识添加失败: {result.get('message')}"

    def _add_document(
            self,
            file_path: str,
            document_id: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None,
            rag_namespace: Optional[str] = None,
            encoding: str = "utf-8-sig",
            replace_existing: bool = True,
            **kwargs
    ) -> str:
        """添加本地 txt / md / pdf / docx 文档到知识库"""

        path = Path(file_path)

        if not path.exists():
            return f"❌ 文件不存在: {file_path}"

        if not path.is_file():
            return f"❌ 不是有效文件: {file_path}"

        suffix = path.suffix.lower()

        # ✅ 修改点 1：这里加入 .docx
        if suffix not in [".txt", ".md", ".pdf", ".docx"]:
            return f"❌ 当前 add_document 只支持 .txt / .md / .pdf / .docx 文件，当前文件类型: {suffix}"

        document_id = document_id or path.stem

        try:
            if suffix in [".txt", ".md"]:
                try:
                    text = path.read_text(encoding=encoding)
                except UnicodeDecodeError:
                    text = path.read_text(encoding="gbk")

            # ✅ 修改点 2：新增 Word 文档读取分支
            elif suffix == ".docx":
                from docx import Document

                doc = Document(str(path))
                paragraphs = []

                for para in doc.paragraphs:
                    text_part = para.text.strip()
                    if text_part:
                        paragraphs.append(text_part)

                text = "\n\n".join(paragraphs)

                if not text.strip():
                    return f"❌ Word 文档未提取到有效文本: {file_path}"

            elif suffix == ".pdf":

                from pypdf import PdfReader

                reader = PdfReader(str(path))

                pipeline = self._get_pipeline(rag_namespace)

                # 先删除同一个 document_id 的旧 chunks，避免重复导入
                removed = 0

                if replace_existing and hasattr(pipeline, "_remove_document_chunks"):
                    removed = pipeline._remove_document_chunks(document_id)

                total_chunks = 0
                total_pages = 0

                for i, page in enumerate(reader.pages):

                    page_number = i + 1

                    page_text = page.extract_text() or ""

                    if not page_text.strip():
                        continue

                    total_pages += 1

                    page_metadata = {
                        "file_path": str(path),
                        "file_name": path.name,
                        "file_suffix": suffix,
                        "document_id": document_id,
                        "source_type": "pdf",
                        "page_number": page_number,
                        **(metadata or {})
                    }

                    # 关键：每页添加时不保存缓存
                    result = pipeline.add_text(
                        text=page_text,
                        document_id=document_id,
                        metadata=page_metadata,
                        replace_existing=False,
                        save_cache=False,
                    )

                    if result.get("success"):
                        total_chunks += int(result.get("chunks_added", 0))

                if total_chunks == 0:
                    return f"❌ PDF 未提取到有效文本: {file_path}"

                # 关键：所有页处理完后，只保存一次缓存
                if hasattr(pipeline, "_save_cache"):
                    pipeline._save_cache()

                return (
                    f"✅ PDF 文档添加成功\n"
                    f"- document_id: {document_id}\n"
                    f"- pages_added: {total_pages}\n"
                    f"- chunks_added: {total_chunks}\n"
                    f"- chunks_removed: {removed}"
                )

            else:
                return f"❌ 不支持的文件类型: {suffix}"

        except Exception as e:
            return f"❌ 文件读取失败: {e}"

        if not text.strip():
            return f"❌ 文件内容为空: {file_path}"

        final_metadata = {
            "file_path": str(path),
            "file_name": path.name,
            "file_suffix": suffix,
            "document_id": document_id,
            "source_type": "document",
            **(metadata or {})
        }

        # PDF 当前是“页码写入正文”的简单方案。
        # 后续如果要精确页码，需要改成一页一页 add_text。
        if suffix == ".pdf":
            final_metadata["page_number"] = ""
            final_metadata["page_note"] = "页码已写入 chunk 正文标题中，例如：# 第 1 页"

        return self._add_text(
            text=text,
            document_id=document_id,
            metadata=final_metadata,
            rag_namespace=rag_namespace,
            replace_existing=replace_existing,
        )

    def _search(
        self,
        query: str,
        limit: int = 5,
        min_score: float = 0.0,
        rag_namespace: Optional[str] = None,
        document_id: Optional[str] = None,
        **kwargs
    ) -> str:
        """搜索知识库"""

        pipeline = self._get_pipeline(rag_namespace)

        results = pipeline.search(
            query=query,
            limit=limit,
            min_score=min_score,
            document_id=document_id,
        )

        if not results:
            if document_id:
                return f"🔍 未在文档 {document_id} 中找到与 '{query}' 相关的知识"
            return f"🔍 未找到与 '{query}' 相关的知识"

        lines = [f"🔍 找到 {len(results)} 条相关知识:"]

        for i, item in enumerate(results, start=1):
            content = item.get("content", "")
            score = float(item.get("score", 0.0))
            metadata = item.get("metadata", {}) or {}

            content = self._clean_preview_text(content, max_len=220)
            source_text = self._format_source(metadata)

            page_number = metadata.get("page_number", "")
            file_name = metadata.get("file_name", "")
            title = f"{i}. "

            if page_number:
                title += f"第 {page_number} 页"
            else:
                title += "未知页码"

            title += f" | score: {score:.4f}"

            if source_text:
                lines.append(
                    f"{title}\n"
                    f"来源: {source_text}\n"
                    f"内容摘要:\n{content}"
                )
            else:
                lines.append(
                    f"{title}\n"
                    f"内容摘要:\n{content}"
                )

        return "\n".join(lines)

    def _citation(
            self,
            query: str,
            limit: int = 5,
            min_score: float = 0.0,
            rag_namespace: Optional[str] = None,
            document_id: Optional[str] = None,
            style: str = "normal",
            **kwargs
    ) -> str:
        """生成可复制引用格式"""

        if not query or not query.strip():
            return "❌ 请提供引用检索关键词"

        pipeline = self._get_pipeline(rag_namespace)

        results = pipeline.search(
            query=query,
            limit=limit,
            min_score=min_score,
            document_id=document_id,
        )

        if not results:
            if document_id:
                return f"🔍 未在文档 {document_id} 中找到与 '{query}' 相关的引用内容"
            return f"🔍 未找到与 '{query}' 相关的引用内容"

        lines = [
            "📌 可复制引用：",
            "",
            f"检索关键词：{query}",
        ]

        if document_id:
            lines.append(f"限定文档：{document_id}")

        lines.append("")

        for i, item in enumerate(results, start=1):
            content = item.get("content", "")
            metadata = item.get("metadata", {}) or {}
            score = float(item.get("score", 0.0))

            file_name = metadata.get("file_name", "未知文件")
            page_number = metadata.get("page_number", "未知页码")

            preview = self._clean_preview_text(content, max_len=260)

            if style == "markdown":
                lines.append(
                    f"> **引用 {i}**  \n"
                    f"> **来源**：《{file_name}》，第 {page_number} 页  \n"
                    f"> **相关度**：{score:.4f}  \n"
                    f"> **内容**：{preview}"
                )
                lines.append("")
            else:
                lines.append(
                    f"{i}. 《{file_name}》，第 {page_number} 页。\n"
                    f"相关度：{score:.4f}\n"
                    f"引用内容：{preview}\n"
                )

        return "\n".join(lines)

    def _ask(
            self,
            query: str,
            limit: int = 5,
            min_score: float = 0.0,
            rag_namespace: Optional[str] = None,
            document_id: Optional[str] = None,
            summary_mode: bool = False,
            **kwargs
    ) -> str:
        """基于检索结果问答"""

        pipeline = self._get_pipeline(rag_namespace)

        if summary_mode and document_id and hasattr(pipeline, "get_document_summary_context"):
            results = pipeline.get_document_summary_context(
                document_id=document_id,
                limit=max(limit, 12)
            )
        else:
            results = pipeline.search(
                query=query,
                limit=limit,
                min_score=min_score,
                document_id=document_id,
            )

        if not results:
            if document_id:
                return f"🔍 未在文档 {document_id} 中找到与 '{query}' 相关的知识，无法生成答案"
            return f"🔍 未找到与 '{query}' 相关的知识，无法生成答案"

        context_parts = []

        for i, item in enumerate(results):
            metadata = item.get("metadata", {}) or {}
            source_text = self._format_source(metadata)
            content = item.get("content", "")

            if source_text:
                context_parts.append(
                    f"[资料{i + 1} | {source_text}]\n{content}"
                )
            else:
                context_parts.append(
                    f"[资料{i + 1}]\n{content}"
                )

        context = "\n\n".join(context_parts)

        prompt = f"""请根据下面资料回答用户问题。

要求：
1. 只能基于资料回答。
2. 如果资料中没有相关内容，请明确说明“资料中没有提到”。
3. 回答要简洁、准确。
4. 如果资料来源中包含文件名、文档ID或页码，请在回答中适当说明。

用户问题：
{query}

资料：
{context}

请给出回答：
"""

        answer = self.llm.generate(prompt)

        sources = []

        for i, item in enumerate(results, start=1):
            metadata = item.get("metadata", {}) or {}
            source_text = self._format_source(metadata)

            if source_text:
                sources.append(f"{i}. {source_text}")

        source_output = "\n".join(sources) if sources else "暂无明确来源信息"

        return (
            f"🤖 RAG回答:\n{answer}\n\n"
            f"📚 参考知识条数: {len(results)}\n"
            f"📌 参考来源:\n{source_output}"
        )

    def _stats(
        self,
        rag_namespace: Optional[str] = None,
        **kwargs
    ) -> str:
        """查看知识库统计"""

        pipeline = self._get_pipeline(rag_namespace)
        stats = pipeline.stats()

        lines = [
            "📊 RAG知识库统计:",
            f"- collection_name: {stats.get('collection_name')}",
            f"- rag_namespace: {stats.get('rag_namespace')}",
            f"- document_count: {stats.get('document_count')}",
            f"- chunk_count: {stats.get('chunk_count')}",
            f"- dimension: {stats.get('dimension')}",
        ]

        if stats.get("cache_path"):
            lines.append(f"- cache_path: {stats.get('cache_path')}")

        if stats.get("cache_exists") is not None:
            lines.append(f"- cache_exists: {stats.get('cache_exists')}")

        return "\n".join(lines)

    def _delete_document(
            self,
            document_id: str,
            rag_namespace: Optional[str] = None,
            **kwargs
    ) -> str:
        """删除指定 document_id 的文档知识"""

        if not document_id:
            return "❌ document_id 不能为空"

        pipeline = self._get_pipeline(rag_namespace)

        if not hasattr(pipeline, "delete_document"):
            return "❌ 当前 RAG pipeline 不支持 delete_document"

        result = pipeline.delete_document(document_id)

        if result.get("success"):
            return (
                f"✅ 文档删除成功\n"
                f"- document_id: {result.get('document_id')}\n"
                f"- chunks_removed: {result.get('chunks_removed')}\n"
                f"- cache_path: {result.get('cache_path')}"
            )

        return f"❌ 文档删除失败: {result.get('message')}"

    def _clear(
        self,
        rag_namespace: Optional[str] = None,
        **kwargs
    ) -> str:
        """清空知识库"""

        pipeline = self._get_pipeline(rag_namespace)
        result = pipeline.clear()

        return result.get("message", "已清空知识库")

    def _clean_preview_text(self, text: str, max_len: int = 220) -> str:
        """清理检索结果预览文本，避免展示过长、过乱"""

        if not text:
            return ""

        # 统一换行和空格
        text = str(text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        lines = []

        for line in text.split("\n"):
            line = line.strip()
            if line:
                lines.append(line)

        cleaned = " ".join(lines)

        # 压缩多余空格
        while "  " in cleaned:
            cleaned = cleaned.replace("  ", " ")

        if len(cleaned) > max_len:
            cleaned = cleaned[:max_len] + "..."

        return cleaned

    def _format_source(self, metadata: Dict[str, Any]) -> str:
        """格式化来源信息"""

        if not metadata:
            return ""

        file_name = metadata.get("file_name", "")
        document_id = metadata.get("document_id", "")
        page_number = metadata.get("page_number", "")

        source_parts = []

        if file_name:
            source_parts.append(f"文件: {file_name}")

        if document_id:
            source_parts.append(f"文档ID: {document_id}")

        if page_number:
            source_parts.append(f"第 {page_number} 页")

        return " | ".join(source_parts)