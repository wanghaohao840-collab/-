from datetime import datetime
from typing import List, Optional, Dict, Any

from hello_agents.tools.base import Tool
from hello_agents.memory.base import MemoryConfig
from hello_agents.memory.manager import MemoryManager


class MemoryTool(Tool):
    """记忆工具 - 为 Agent 提供记忆功能"""

    def __init__(
        self,
        user_id: str = "default_user",
        memory_config: Optional[MemoryConfig] = None,
        memory_types: Optional[List[str]] = None,
        memory_repository: Any = None
    ):
        super().__init__(
            name="memory",
            description="记忆工具 - 可以存储和检索对话历史、知识和经验"
        )

        self.user_id = user_id
        self.memory_config = memory_config or MemoryConfig()
        self.memory_types = memory_types or ["working", "episodic", "semantic"]

        self.current_session_id = None
        self.conversation_count = 0

        self.memory_manager = MemoryManager(
            config=self.memory_config,
            user_id=user_id,
            enable_working="working" in self.memory_types,
            enable_episodic="episodic" in self.memory_types,
            enable_semantic="semantic" in self.memory_types,
            enable_perceptual="perceptual" in self.memory_types,
            snapshot_repository=memory_repository
        )

    def close(self) -> None:
        close = getattr(self.memory_manager, "close", None)
        if callable(close):
            close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def get_parameters(self) -> dict:
        """返回工具参数定义

        Tool 抽象基类要求必须实现这个方法。
        """

        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "记忆操作类型",
                    "enum": [
                        "add",
                        "search",
                        "summary",
                        "stats",
                        "update",
                        "remove",
                        "forget",
                        "consolidate",
                        "clear_all"
                    ]
                },
                "content": {
                    "type": "string",
                    "description": "要添加的记忆内容"
                },
                "query": {
                    "type": "string",
                    "description": "搜索记忆时使用的查询内容"
                },
                "memory_type": {
                    "type": "string",
                    "description": "单个记忆类型：working / episodic / semantic / perceptual"
                },
                "memory_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "多个记忆类型"
                },
                "importance": {
                    "type": "number",
                    "description": "记忆重要性，范围 0.0 到 1.0"
                },
                "limit": {
                    "type": "integer",
                    "description": "搜索结果数量限制"
                },
                "min_importance": {
                    "type": "number",
                    "description": "搜索时的最低重要性过滤阈值"
                },
                "file_path": {
                    "type": "string",
                    "description": "感知记忆对应的文件路径"
                },
                "modality": {
                    "type": "string",
                    "description": "感知记忆模态，例如 image / audio / text"
                },
                "strategy": {
                    "type": "string",
                    "description": "遗忘策略：importance_based / time_based / capacity_based"
                },
                "threshold": {
                    "type": "number",
                    "description": "遗忘阈值"
                },
                "max_age_days": {
                    "type": "integer",
                    "description": "基于时间遗忘时的最大保留天数"
                },
                "from_type": {
                    "type": "string",
                    "description": "记忆整合的来源类型"
                },
                "to_type": {
                    "type": "string",
                    "description": "记忆整合的目标类型"
                },
                "importance_threshold": {
                    "type": "number",
                    "description": "记忆整合的重要性阈值"
                }
            },
            "required": ["action"]
        }

    def run(self, action: str = None, **kwargs) -> str:
        """工具运行入口

        Tool 抽象基类要求必须实现这个方法。
        这里直接转发到 execute()。
        """

        if action is None:
            action = kwargs.pop("action", None)

        if not action:
            return "❌ 缺少 action 参数"

        return self.execute(action, **kwargs)

    def execute(self, action: str, **kwargs) -> str:
        lock = getattr(self, "coordination_lock", None)
        if lock is None:
            return self._execute_unlocked(action, **kwargs)
        with lock:
            return self._execute_unlocked(action, **kwargs)

    def _execute_unlocked(self, action: str, **kwargs) -> str:
        """执行记忆操作

        支持的操作：
        - add: 添加记忆
        - search: 搜索记忆
        - summary: 获取记忆摘要
        - stats: 获取统计信息
        - update: 更新记忆
        - remove: 删除记忆
        - forget: 遗忘记忆
        - consolidate: 整合记忆
        - clear_all: 清空所有记忆
        """

        if action == "add":
            return self._add_memory(**kwargs)

        elif action == "search":
            return self._search_memory(**kwargs)

        elif action == "summary":
            return self._get_summary(**kwargs)

        elif action == "stats":
            return self._get_stats(**kwargs)

        elif action == "update":
            return self._update_memory(**kwargs)

        elif action == "remove":
            return self._remove_memory(**kwargs)

        elif action == "forget":
            return self._forget(**kwargs)

        elif action == "consolidate":
            return self._consolidate(**kwargs)

        elif action == "clear_all":
            return self._clear_all(**kwargs)

        else:
            return f"❌ 不支持的记忆操作: {action}"

    def _add_memory(
        self,
        content: str = "",
        memory_type: str = "working",
        importance: float = 0.5,
        file_path: str = None,
        modality: str = None,
        **metadata
    ) -> str:
        """添加记忆"""

        try:
            if not content:
                return "❌ 添加记忆失败: content 不能为空"

            if self.current_session_id is None:
                self.current_session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

            if memory_type == "perceptual" and file_path:
                inferred = modality or self._infer_modality(file_path)
                metadata.setdefault("modality", inferred)
                metadata.setdefault("raw_data", file_path)
                metadata.setdefault("file_path", file_path)

            metadata.update({
                "session_id": self.current_session_id,
                "timestamp": datetime.now().isoformat(),
                "user_id": self.user_id
            })

            memory_id = self.memory_manager.add_memory(
                content=content,
                memory_type=memory_type,
                importance=importance,
                metadata=metadata,
                auto_classify=False
            )

            return f"✅ 记忆已添加 (ID: {memory_id[:8]}...)"

        except Exception as e:
            return f"❌ 添加记忆失败: {str(e)}"

    def _search_memory(
        self,
        query: str,
        limit: int = 5,
        memory_types: List[str] = None,
        memory_type: str = None,
        min_importance: float = 0.1
    ) -> str:
        """搜索记忆"""

        try:
            if not query:
                return "❌ 搜索记忆失败: query 不能为空"

            if memory_type and not memory_types:
                memory_types = [memory_type]

            results = self.memory_manager.retrieve_memories(
                query=query,
                limit=limit,
                memory_types=memory_types,
                min_importance=min_importance
            )

            if not results:
                return f"🔍 未找到与 '{query}' 相关的记忆"

            formatted_results = []
            formatted_results.append(f"🔍 找到 {len(results)} 条相关记忆:")

            for i, memory in enumerate(results, 1):
                memory_type_label = {
                    "working": "工作记忆",
                    "episodic": "情景记忆",
                    "semantic": "语义记忆",
                    "perceptual": "感知记忆"
                }.get(memory.memory_type, memory.memory_type)

                content_preview = (
                    memory.content[:80] + "..."
                    if len(memory.content) > 80
                    else memory.content
                )

                formatted_results.append(
                    f"{i}. [{memory_type_label}] {content_preview} (重要性: {memory.importance:.2f})"
                )

            return "\n".join(formatted_results)

        except Exception as e:
            return f"❌ 搜索记忆失败: {str(e)}"

    def _get_summary(self, **kwargs) -> str:
        """获取记忆摘要"""

        try:
            if hasattr(self.memory_manager, "get_summary"):
                return self.memory_manager.get_summary()

            return "📊 当前 MemoryManager 暂未实现 get_summary()"

        except Exception as e:
            return f"❌ 获取记忆摘要失败: {str(e)}"

    def _get_stats(self, **kwargs) -> str:
        """获取记忆统计信息"""

        try:
            if hasattr(self.memory_manager, "get_stats"):
                stats = self.memory_manager.get_stats()

                lines = ["📈 记忆统计信息:"]
                lines.append(f"user_id: {stats.get('user_id')}")
                lines.append(f"启用记忆类型: {stats.get('enabled_memory_types')}")

                memory_counts = stats.get("memory_counts", {})
                lines.append("记忆数量:")

                for memory_type, count in memory_counts.items():
                    lines.append(f"- {memory_type}: {count}")

                return "\n".join(lines)

            return "📈 当前 MemoryManager 暂未实现 get_stats()"

        except Exception as e:
            return f"❌ 获取统计信息失败: {str(e)}"

    def _update_memory(self, **kwargs) -> str:
        """更新记忆"""

        try:
            if hasattr(self.memory_manager, "update_memory"):
                result = self.memory_manager.update_memory(**kwargs)
                return f"✅ 记忆已更新: {result}"

            return "⚠️ 当前 MemoryManager 暂未实现 update_memory()"

        except Exception as e:
            return f"❌ 更新记忆失败: {str(e)}"

    def _remove_memory(self, **kwargs) -> str:
        """删除记忆"""

        try:
            if hasattr(self.memory_manager, "remove_memory"):
                result = self.memory_manager.remove_memory(**kwargs)
                return f"✅ 记忆已删除: {result}"

            return "⚠️ 当前 MemoryManager 暂未实现 remove_memory()"

        except Exception as e:
            return f"❌ 删除记忆失败: {str(e)}"

    def _forget(
        self,
        strategy: str = "importance_based",
        threshold: float = 0.1,
        max_age_days: int = 30
    ) -> str:
        """遗忘记忆"""

        try:
            count = self.memory_manager.forget_memories(
                strategy=strategy,
                threshold=threshold,
                max_age_days=max_age_days
            )

            return f"🧹 已遗忘 {count} 条记忆（策略: {strategy}）"

        except Exception as e:
            return f"❌ 遗忘记忆失败: {str(e)}"

    def _consolidate(
        self,
        from_type: str = "working",
        to_type: str = "episodic",
        importance_threshold: float = 0.7
    ) -> str:
        """整合记忆"""

        try:
            count = self.memory_manager.consolidate_memories(
                from_type=from_type,
                to_type=to_type,
                importance_threshold=importance_threshold
            )

            return (
                f"🔄 已整合 {count} 条记忆为长期记忆"
                f"（{from_type} → {to_type}，阈值={importance_threshold}）"
            )

        except Exception as e:
            return f"❌ 整合记忆失败: {str(e)}"

    def _clear_all(self, **kwargs) -> str:
        """清空所有记忆"""

        try:
            if hasattr(self.memory_manager, "clear_all"):
                return self.memory_manager.clear_all()

            return "⚠️ 当前 MemoryManager 暂未实现 clear_all()"

        except Exception as e:
            return f"❌ 清空记忆失败: {str(e)}"

    def _infer_modality(self, file_path: str) -> str:
        """根据文件扩展名推断模态"""

        if not file_path:
            return "unknown"

        lower_path = file_path.lower()

        image_exts = [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"]
        audio_exts = [".mp3", ".wav", ".m4a", ".flac", ".aac"]
        video_exts = [".mp4", ".avi", ".mov", ".mkv", ".wmv"]
        text_exts = [".txt", ".md", ".json", ".csv", ".xml", ".html", ".py", ".js"]

        if any(lower_path.endswith(ext) for ext in image_exts):
            return "image"

        if any(lower_path.endswith(ext) for ext in audio_exts):
            return "audio"

        if any(lower_path.endswith(ext) for ext in video_exts):
            return "video"

        if any(lower_path.endswith(ext) for ext in text_exts):
            return "text"

        return "unknown"
