"""Per-user mutation coordination contract.

Every runtime-backed assistant must route same-user writes through
``UserMutationCoordinator``.  The coordinator:

* serializes writes for one user under a shared reentrant lock;
* reloads the latest History snapshot before every commit (fresh merge);
* never holds the lock during LLM generation.

Callers are responsible for checking RAG/Memory mutation results and, when a
step fails after a preceding step succeeded, performing best-effort
compensation (see :meth:`UserMutationCoordinator.compensate_rag_add`).

Reentrant locking is permitted — the coordinator lock is an ``RLock``, and
``MemoryTool.execute`` re-acquires it through its own ``coordination_lock``
reference.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Iterator

from app.history import HistoryRepository

logger = logging.getLogger(__name__)


class UserMutationCoordinator:
    """Coordinates per-user state mutations under a shared lock.

    Contract:

    1. **Serialization** — all same-user writes serialize through
       :attr:`lock` (a reentrant ``RLock``).
    2. **Fresh merge** — every History write reloads the latest persisted
       snapshot before applying the callerʼs mutation.
    3. **LLM generation is external** — the coordinator must never be held
       while an LLM call or RAG ``ask``/``search`` is in-flight.
    4. **Explicit compensation** — when a multi-step mutation fails after a
       preceding step succeeded, the caller must attempt best-effort
       compensation.  The coordinator provides helpers for the common cases.

    Usage::

        # Inside PDFLearningAssistant, given `coord = runtime.coordinator`:

        with coord:
            # ── critical section: RAG mutation + History commit ──
            result = self.rag_tool.execute("add_document", **kwargs)
            if not success:
                return result  # history untouched
            coord.update_history(lambda h: h["documents"].append(item))
            self.history = coord.history.load()

        # ── outside lock: LLM generation ──
        answer = self.rag_tool.execute("ask", query=question, ...)

        # ── short lock: commit structured result ──
        coord.update_history(lambda h: h["questions"].append(record))
    """

    def __init__(
        self,
        user_id: str,
        lock: RLock,
        history: HistoryRepository,
        *,
        document_root: Path | None = None,
    ) -> None:
        self.user_id = user_id
        self.lock = lock
        self.history = history
        self.document_root = document_root

    # ── context manager ──────────────────────────────────────────────

    def __enter__(self) -> UserMutationCoordinator:
        self.lock.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.lock.release()

    @contextmanager
    def critical_section(self) -> Iterator[None]:
        """Explicit named context so callers can document the intent."""
        with self.lock:
            yield

    # ── history helpers (all reload-latest-before-write) ──────────────

    def load_history(self) -> dict[str, Any]:
        """Return the latest persisted snapshot (acquires the lock)."""
        with self.lock:
            return self.history.load()

    def update_history(self, mutation: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        """Reload latest, apply *mutation*, and atomically persist."""
        with self.lock:
            return self.history.update(mutation)

    def delete_document(self, document_id: str) -> tuple[int, int]:
        """Coordinated document deletion from History."""
        with self.lock:
            return self.history.delete_document(document_id)

    def clear_documents(self) -> tuple[int, int]:
        """Coordinated document clear from History."""
        with self.lock:
            return self.history.clear_documents()

    # ── compensation helpers ─────────────────────────────────────────

    def compensate_rag_add(
        self,
        rag_tool: Any,
        document_id: str,
        *,
        reason: str = "history commit failed",
    ) -> None:
        """Best-effort removal of a RAG document added during a failed
        import.  Logs and swallows errors so the original exception can
        propagate."""
        try:
            rag_tool.execute("delete_document", document_id=document_id)
        except Exception:
            logger.warning(
                "compensation failed: delete_document %r after %s",
                document_id,
                reason,
                exc_info=True,
            )

    # ── safe source-file helpers ─────────────────────────────────────

    def safe_unlink(self, path: Path) -> None:
        """Unlink *path* only when it is under :attr:`document_root`.

        Raises :exc:`ValueError` for paths outside the user root.
        """
        resolved = path.resolve()
        if self.document_root is None:
            raise ValueError("document_root is not configured")
        root = self.document_root.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            raise ValueError(
                f"Refusing to unlink path outside user root: {path}"
            ) from None
        if resolved.exists():
            resolved.unlink()
