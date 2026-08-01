from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from app.coordination import UserMutationCoordinator
from app.history import HistoryRepository
from app.memory_repository import MemorySnapshotRepository
from app.recovery import RecoveryService
from app.reports import ReportService
from app.storage import UserPaths, UserStorage
from hello_agents.memory.base import MemoryConfig
from hello_agents.tools.builtin.memory_tool import MemoryTool
from hello_agents.tools.builtin.rag_tool import RAGTool


@dataclass
class UserRuntime:
    user_id: str
    paths: UserPaths
    lock: RLock
    coordinator: UserMutationCoordinator
    rag_tool: RAGTool
    memory_tool: MemoryTool
    history: HistoryRepository
    reports: ReportService
    recovery: RecoveryService

    def close(self) -> None:
        close = getattr(self.rag_tool, "close", None)
        if callable(close):
            close()
        close = getattr(self.memory_tool, "close", None)
        if callable(close):
            close()


class UserRuntimeRegistry:
    def __init__(self, db_path: Path | str, storage: UserStorage):
        self.db_path = Path(db_path)
        self.storage = storage
        self._runtimes: dict[str, UserRuntime] = {}
        self._lock = RLock()

    def get_or_create(self, user_id: str) -> UserRuntime:
        with self._lock:
            runtime = self._runtimes.get(user_id)
            if runtime is not None:
                return runtime

            paths = self.storage.ensure_user_dirs(user_id)
            memory_repo = MemorySnapshotRepository(paths.memory_snapshot, user_id=user_id)
            memory_tool = MemoryTool(
                user_id=user_id,
                memory_config=MemoryConfig(database_path=str(paths.root / "memory" / f"memory_{user_id}.db")),
                memory_types=["working", "episodic", "semantic"],
                memory_repository=memory_repo,
            )
            user_lock = RLock()
            memory_tool.coordination_lock = user_lock
            history_repo = HistoryRepository(paths.history)
            coordinator = UserMutationCoordinator(
                user_id=user_id,
                lock=user_lock,
                history=history_repo,
                document_root=paths.documents,
            )
            backup_dir = paths.root / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            recovery = RecoveryService(
                coordinator=coordinator,
                history_repo=history_repo,
                memory_repo=memory_repo,
                backup_dir=backup_dir,
                memory_manager=memory_tool.memory_manager,
            )
            runtime = UserRuntime(
                user_id=user_id,
                paths=paths,
                lock=user_lock,
                coordinator=coordinator,
                rag_tool=RAGTool(
                    knowledge_base_path=str(paths.rag_cache.parent),
                    collection_name="pdf_learning_collection",
                    rag_namespace=f"pdf_{user_id}",
                    cache_path=str(paths.rag_cache),
                ),
                memory_tool=memory_tool,
                history=history_repo,
                reports=ReportService(self.db_path, self.storage),
                recovery=recovery,
            )
            self._runtimes[user_id] = runtime
            return runtime

    def release_if_unused(self, user_id: str, active_session_count: int) -> None:
        if active_session_count > 0:
            return
        with self._lock:
            runtime = self._runtimes.pop(user_id, None)
        if runtime is not None:
            runtime.close()
