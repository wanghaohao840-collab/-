from __future__ import annotations

import copy
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from threading import Event, RLock
from typing import Any, Callable, Optional


@dataclass
class SummaryTask:
    task_id: str
    status: str
    completed: int
    total: int
    stage: str
    current_document_id: Optional[str]
    result: Optional[str]
    error: Optional[str]
    created_at: str
    updated_at: str


class SummaryTaskManager:
    """Run bounded background summary jobs with observable progress and cancellation."""

    TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})

    def __init__(self, max_workers: int = 2, max_tasks: int = 64):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._max_tasks = max(1, int(max_tasks))
        self._tasks: dict[str, SummaryTask] = {}
        self._futures: dict[str, Future] = {}
        self._cancel_events: dict[str, Event] = {}
        self._lock = RLock()

    def start(
        self,
        runner: Callable[[Callable[..., None], Event], str],
        *,
        total: int,
    ) -> dict[str, Any]:
        task_id = str(uuid.uuid4())
        now = self._now()
        task = SummaryTask(
            task_id=task_id,
            status="queued",
            completed=0,
            total=max(0, int(total)),
            stage="queued",
            current_document_id=None,
            result=None,
            error=None,
            created_at=now,
            updated_at=now,
        )
        cancel_event = Event()
        with self._lock:
            self._evict_old_tasks_locked()
            self._tasks[task_id] = task
            self._cancel_events[task_id] = cancel_event
            self._futures[task_id] = self._executor.submit(
                self._run,
                task_id,
                runner,
                cancel_event,
            )
        return self.get(task_id)

    def get(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(str(task_id or ""))
            if task is None:
                raise KeyError("summary task not found")
            return copy.deepcopy(asdict(task))

    def cancel(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(str(task_id or ""))
            if task is None:
                raise KeyError("summary task not found")
            if task.status in self.TERMINAL_STATUSES:
                return copy.deepcopy(asdict(task))
            self._cancel_events[task.task_id].set()
            future = self._futures.get(task.task_id)
            if future is not None:
                future.cancel()
            task.status = "cancelled"
            task.stage = "cancelled"
            task.updated_at = self._now()
            return copy.deepcopy(asdict(task))

    def close(self) -> None:
        with self._lock:
            for event in self._cancel_events.values():
                event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run(
        self,
        task_id: str,
        runner: Callable[[Callable[..., None], Event], str],
        cancel_event: Event,
    ) -> None:
        self._update(task_id, status="running", stage="mapping")

        def progress_callback(
            *,
            completed: int,
            total: int,
            stage: str,
            document_id: Optional[str] = None,
        ) -> None:
            if cancel_event.is_set():
                return
            self._update(
                task_id,
                completed=max(0, int(completed)),
                total=max(0, int(total)),
                stage=str(stage),
                current_document_id=document_id,
            )

        try:
            result = runner(progress_callback, cancel_event)
            if cancel_event.is_set():
                self._update(task_id, status="cancelled", stage="cancelled")
            else:
                self._update(
                    task_id,
                    status="completed",
                    stage="completed",
                    result=str(result),
                )
        except Exception as error:
            if cancel_event.is_set():
                self._update(task_id, status="cancelled", stage="cancelled")
            else:
                self._update(
                    task_id,
                    status="failed",
                    stage="failed",
                    error=f"{error.__class__.__name__}: {error}",
                )

    def _update(self, task_id: str, **changes: Any) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            if task.status == "cancelled" and changes.get("status") != "cancelled":
                return
            for key, value in changes.items():
                setattr(task, key, value)
            task.updated_at = self._now()

    def _evict_old_tasks_locked(self) -> None:
        if len(self._tasks) < self._max_tasks:
            return
        terminal_ids = [
            task_id
            for task_id, task in self._tasks.items()
            if task.status in self.TERMINAL_STATUSES
        ]
        while len(self._tasks) >= self._max_tasks and terminal_ids:
            task_id = terminal_ids.pop(0)
            self._tasks.pop(task_id, None)
            self._futures.pop(task_id, None)
            self._cancel_events.pop(task_id, None)
        if len(self._tasks) >= self._max_tasks:
            raise RuntimeError("too many active summary tasks")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
