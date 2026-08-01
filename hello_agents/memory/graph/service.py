from __future__ import annotations

import uuid
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable, Iterator, Optional

from hello_agents.memory.graph.contracts import graph_response
from hello_agents.memory.graph.extractor import GraphExtractionError
from hello_agents.memory.graph.state import (
    GraphStateRepository,
    sanitize_error,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeGraphService:
    """Document-scoped graph extraction, lifecycle, and query service."""

    def __init__(
        self,
        *,
        store: Any,
        extractor: Any,
        state_repository: GraphStateRepository,
        chunk_loader: Optional[Callable[[str], list[dict[str, Any]]]] = None,
        rag_namespace: str = "default",
        uuid_factory: Callable[[], Any] = uuid.uuid4,
        now: Callable[[], str] = _utc_now,
    ):
        self.store = store
        self.extractor = extractor
        self.state_repository = state_repository
        self.chunk_loader = chunk_loader
        self.rag_namespace = str(rag_namespace or "default")
        self._uuid_factory = uuid_factory
        self._now = now
        self._locks_guard = Lock()
        self._locks: dict[str, dict[str, Any]] = {}
        self._recover_interrupted_builds()

    @property
    def lock_registry_size(self) -> int:
        with self._locks_guard:
            return len(self._locks)

    def close(self) -> None:
        close = getattr(self.store, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _document_id(value: str) -> str:
        document_id = str(value or "").strip()
        if not document_id:
            raise ValueError("document_id is required")
        return document_id

    @contextmanager
    def _document_lock(self, document_id: str) -> Iterator[None]:
        with self._locks_guard:
            entry = self._locks.get(document_id)
            if entry is None:
                entry = {"lock": Lock(), "references": 0}
                self._locks[document_id] = entry
            entry["references"] += 1
            lock = entry["lock"]
        acquired = False
        try:
            lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                lock.release()
            with self._locks_guard:
                entry["references"] -= 1
                if (
                    entry["references"] == 0
                    and self._locks.get(document_id) is entry
                ):
                    self._locks.pop(document_id, None)

    def _recover_interrupted_builds(self) -> None:
        for state in self.state_repository.list_by_status("building"):
            document_id = state["document_id"]
            try:
                stored = self.store.get_document_build(
                    document_id,
                    rag_namespace=self.rag_namespace,
                )
                if (
                    stored
                    and stored.get("build_id") == state.get("build_id")
                    and stored.get("graph_status") == "ready"
                ):
                    self.state_repository.upsert(
                        document_id,
                        status="ready",
                        build_id=state.get("build_id"),
                    )
                else:
                    self.state_repository.upsert(
                        document_id,
                        status="failed",
                        build_id=state.get("build_id"),
                        error_type="InterruptedBuild",
                        error_message="进程中断，请重试",
                    )
            except Exception as error:
                self.state_repository.upsert(
                    document_id,
                    status="failed",
                    build_id=state.get("build_id"),
                    error_type="RecoveryCheckFailed",
                    error_message=sanitize_error(error),
                )

    def build_document_graph(
        self,
        document_id: str,
        chunks: list[dict[str, Any]],
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        try:
            document_id = self._document_id(document_id)
        except ValueError as error:
            return self._error("", "pending", error)
        with self._document_lock(document_id):
            return self._build_document_graph_locked(
                document_id,
                chunks,
                metadata or {},
            )

    def _build_document_graph_locked(
        self,
        document_id: str,
        chunks: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        existing = self.state_repository.get(document_id) or {}
        attempt_count = int(existing.get("attempt_count", 0)) + 1
        build_id = str(self._uuid_factory())
        started_at = self._now()
        self.state_repository.upsert(
            document_id,
            status="building",
            build_id=build_id,
            attempt_count=attempt_count,
            llm_attempt_count=0,
            updated_at=started_at,
        )
        llm_attempt_count = 0
        try:
            graph = self.extractor.extract(
                document_id,
                chunks,
                metadata,
                rag_namespace=self.rag_namespace,
            )
            llm_attempt_count = int(graph.llm_attempt_count)
            counts = self.store.replace_document_graph(
                document_id,
                build_id,
                graph.to_store_payload(),
                rag_namespace=self.rag_namespace,
            )
            updated_at = self._now()
            state = self.state_repository.upsert(
                document_id,
                status="ready",
                build_id=build_id,
                attempt_count=attempt_count,
                llm_attempt_count=llm_attempt_count,
                updated_at=updated_at,
            )
            return graph_response(
                success=True,
                document_id=document_id,
                status="ready",
                data={
                    "build_id": build_id,
                    "node_count": int(counts.get("node_count", 0)),
                    "relation_count": int(
                        counts.get("relation_count", 0)
                    ),
                    "attempt_count": attempt_count,
                    "llm_attempt_count": llm_attempt_count,
                    "updated_at": state["updated_at"],
                },
            )
        except Exception as error:
            if isinstance(error, GraphExtractionError):
                llm_attempt_count = error.llm_attempt_count
            sensitive_chunk_text = [
                str(chunk.get("content") or "")
                for chunk in chunks
                if chunk.get("content")
            ]
            safe_message = sanitize_error(
                error,
                secrets=sensitive_chunk_text,
            )
            updated_at = self._now()
            state = self.state_repository.upsert(
                document_id,
                status="failed",
                build_id=build_id,
                attempt_count=attempt_count,
                llm_attempt_count=llm_attempt_count,
                error_type=error.__class__.__name__,
                error_message=safe_message,
                updated_at=updated_at,
            )
            return graph_response(
                success=False,
                document_id=document_id,
                status="failed",
                data={
                    "build_id": build_id,
                    "attempt_count": attempt_count,
                    "llm_attempt_count": llm_attempt_count,
                    "updated_at": state["updated_at"],
                },
                error_type=state["error_type"],
                error_message=state["error_message"],
            )

    def get_graph_status(self, document_id: str) -> dict[str, Any]:
        try:
            document_id = self._document_id(document_id)
        except ValueError as error:
            return self._error("", "pending", error)
        state = self.state_repository.get(document_id)
        if state is None:
            return graph_response(
                success=True,
                document_id=document_id,
                status="pending",
                data={
                    "build_id": None,
                    "attempt_count": 0,
                    "llm_attempt_count": 0,
                    "updated_at": None,
                },
            )
        failed = state["status"] in {"failed", "cleanup_pending"}
        return graph_response(
            success=not failed,
            document_id=document_id,
            status=state["status"],
            data={
                "build_id": state.get("build_id"),
                "attempt_count": int(state.get("attempt_count", 0)),
                "llm_attempt_count": int(
                    state.get("llm_attempt_count", 0)
                ),
                "updated_at": state.get("updated_at"),
            },
            error_type=state.get("error_type") if failed else None,
            error_message=state.get("error_message") if failed else None,
        )

    def retry_document_graph(self, document_id: str) -> dict[str, Any]:
        try:
            document_id = self._document_id(document_id)
        except ValueError as error:
            return self._error("", "pending", error)
        with self._document_lock(document_id):
            state = self.state_repository.get(document_id)
            status = state.get("status") if state else "pending"
            if status == "cleanup_pending":
                return self._delete_document_graph_locked(document_id)
            if status != "failed":
                return graph_response(
                    success=False,
                    document_id=document_id,
                    status=status,
                    data={},
                    error_type="RetryNotAllowed",
                    error_message="文档当前状态不支持重试",
                )
            if self.chunk_loader is None:
                return graph_response(
                    success=False,
                    document_id=document_id,
                    status=status,
                    data={},
                    error_type="ChunkLoaderUnavailable",
                    error_message="Graph retry has no RAG chunk loader",
                )
            try:
                chunks = self.chunk_loader(document_id)
            except Exception as error:
                return self._error(document_id, status, error)
            metadata = self._metadata_from_chunks(document_id, chunks)
            return self._build_document_graph_locked(
                document_id,
                chunks,
                metadata,
            )

    @staticmethod
    def _metadata_from_chunks(
        document_id: str,
        chunks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not chunks:
            return {"name": document_id}
        metadata = dict(chunks[0].get("metadata") or {})
        metadata.pop("content", None)
        metadata["name"] = (
            metadata.get("file_name")
            or metadata.get("name")
            or document_id
        )
        return metadata

    def delete_document_graph(self, document_id: str) -> dict[str, Any]:
        try:
            document_id = self._document_id(document_id)
        except ValueError as error:
            return self._error("", "pending", error)
        with self._document_lock(document_id):
            return self._delete_document_graph_locked(document_id)

    def _delete_document_graph_locked(
        self,
        document_id: str,
    ) -> dict[str, Any]:
        existing = self.state_repository.get(document_id) or {}
        try:
            counts = self.store.delete_document(
                document_id,
                rag_namespace=self.rag_namespace,
            )
            updated_at = self._now()
            self.state_repository.upsert(
                document_id,
                status="deleted",
                build_id=existing.get("build_id"),
                updated_at=updated_at,
            )
            return graph_response(
                success=True,
                document_id=document_id,
                status="deleted",
                data={
                    "nodes_removed": int(
                        counts.get("nodes_removed", 0)
                    ),
                    "relations_removed": int(
                        counts.get("relations_removed", 0)
                    ),
                    "updated_at": updated_at,
                },
            )
        except Exception as error:
            updated_at = self._now()
            state = self.state_repository.upsert(
                document_id,
                status="cleanup_pending",
                build_id=existing.get("build_id"),
                error_type=error.__class__.__name__,
                error_message=sanitize_error(error),
                updated_at=updated_at,
            )
            return graph_response(
                success=False,
                document_id=document_id,
                status="cleanup_pending",
                data={"updated_at": updated_at},
                error_type=state["error_type"],
                error_message=state["error_message"],
            )

    def _ready_state(
        self,
        document_id: str,
    ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        state = self.state_repository.get(document_id)
        status = state.get("status") if state else "pending"
        if status != "ready":
            return state, graph_response(
                success=False,
                document_id=document_id,
                status=status,
                data={},
                error_type="GraphNotReady",
                error_message="Document graph is not ready",
            )
        return state, None

    @staticmethod
    def _graph_query_terms(query: str) -> list[str]:
        text = str(query or "").strip().lower()
        if not text:
            return []
        terms: list[str] = []
        for run in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text):
            if run not in terms:
                terms.append(run)
            if re.fullmatch(r"[\u4e00-\u9fff]+", run):
                for index in range(len(run) - 1):
                    bigram = run[index:index + 2]
                    if bigram not in terms:
                        terms.append(bigram)
        return terms[:12]

    def get_graph_context(
        self,
        document_id: str,
        query: str,
        *,
        node_limit: int = 8,
        relation_limit: int = 16,
    ) -> dict[str, Any]:
        try:
            document_id = self._document_id(document_id)
            _, unavailable = self._ready_state(document_id)
            if unavailable:
                return unavailable
            query_terms = self._graph_query_terms(query)
            result = self.store.get_graph_context(
                document_id,
                query_terms=query_terms,
                rag_namespace=self.rag_namespace,
                node_limit=node_limit,
                relation_limit=relation_limit,
            )
            return graph_response(
                success=True,
                document_id=document_id,
                status="ready",
                data={
                    "entities": list(result.get("entities") or []),
                    "relations": list(result.get("relations") or []),
                    "query_terms": query_terms,
                },
            )
        except Exception as error:
            return self._error(str(document_id or ""), "ready", error)

    def get_document_graph(
        self,
        document_id: str,
        *,
        node_cursor: Optional[str] = None,
        relation_cursor: Optional[str] = None,
        node_limit: int = 100,
        relation_limit: int = 100,
        include_chunk_content: bool = False,
    ) -> dict[str, Any]:
        try:
            document_id = self._document_id(document_id)
            _, unavailable = self._ready_state(document_id)
            if unavailable:
                return unavailable
            result = self.store.get_document_graph(
                document_id,
                rag_namespace=self.rag_namespace,
                node_cursor=node_cursor,
                relation_cursor=relation_cursor,
                node_limit=node_limit,
                relation_limit=relation_limit,
                include_chunk_content=include_chunk_content,
            )
            return graph_response(
                success=True,
                document_id=document_id,
                status="ready",
                data={
                    "nodes": list(result.get("nodes") or []),
                    "relations": list(result.get("relations") or []),
                },
                page=result.get("page") or {
                    "node_limit": node_limit,
                    "relation_limit": relation_limit,
                    "next_node_cursor": None,
                    "next_relation_cursor": None,
                },
            )
        except Exception as error:
            return self._error(str(document_id or ""), "ready", error)

    def get_chapter_tree(self, document_id: str) -> dict[str, Any]:
        try:
            document_id = self._document_id(document_id)
            _, unavailable = self._ready_state(document_id)
            if unavailable:
                return unavailable
            chapters = self.store.get_chapters(
                document_id,
                rag_namespace=self.rag_namespace,
            )
            if len(chapters) > 2000:
                return graph_response(
                    success=False,
                    document_id=document_id,
                    status="ready",
                    data={"chapters": []},
                    error_type="ChapterTreeTooLarge",
                    error_message="Chapter tree exceeds 2000 chapters",
                )
            nodes = {
                chapter["chapter_id"]: {
                    "chapter_id": chapter["chapter_id"],
                    "title": chapter.get("title"),
                    "level": chapter.get("level"),
                    "order": chapter.get("order"),
                    "heading_path": list(
                        chapter.get("heading_path") or []
                    ),
                    "chunk_ids": [
                        value for value in chapter.get("chunk_ids", [])
                        if value is not None
                    ],
                    "children": [],
                }
                for chapter in chapters
            }
            roots = []
            for chapter in sorted(
                chapters,
                key=lambda value: (
                    int(value.get("order", 0)),
                    str(value.get("chapter_id")),
                ),
            ):
                node = nodes[chapter["chapter_id"]]
                parent = nodes.get(chapter.get("parent_id"))
                if parent:
                    parent["children"].append(node)
                else:
                    roots.append(node)
            return graph_response(
                success=True,
                document_id=document_id,
                status="ready",
                data={"chapters": roots},
            )
        except Exception as error:
            return self._error(str(document_id or ""), "ready", error)

    def get_concept_relations(
        self,
        document_id: str,
        concept: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return self._typed_relations(
            document_id,
            node_label="Concept",
            relation_types=(
                "RELATED_TO",
                "PART_OF",
                "IS_A",
                "CONTRASTS_WITH",
            ),
            name=concept,
            cursor=cursor,
            limit=limit,
            nodes_key="concepts",
            relations_key="relations",
        )

    def get_knowledge_dependencies(
        self,
        document_id: str,
        knowledge_point: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return self._typed_relations(
            document_id,
            node_label="KnowledgePoint",
            relation_types=("DEPENDS_ON", "PREREQUISITE_OF"),
            name=knowledge_point,
            cursor=cursor,
            limit=limit,
            nodes_key="knowledge_points",
            relations_key="dependencies",
        )

    def get_person_relations(
        self,
        document_id: str,
        person: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return self._typed_relations(
            document_id,
            node_label="Person",
            relation_types=("RELATED_TO",),
            name=person,
            cursor=cursor,
            limit=limit,
            nodes_key="persons",
            relations_key="relations",
        )

    def _typed_relations(
        self,
        document_id: str,
        *,
        node_label: str,
        relation_types: tuple[str, ...],
        name: Optional[str],
        cursor: Optional[str],
        limit: int,
        nodes_key: str,
        relations_key: str,
    ) -> dict[str, Any]:
        try:
            document_id = self._document_id(document_id)
            _, unavailable = self._ready_state(document_id)
            if unavailable:
                return unavailable
            result = self.store.get_typed_relations(
                document_id,
                rag_namespace=self.rag_namespace,
                node_label=node_label,
                relation_types=relation_types,
                name=(str(name).strip().casefold() if name else None),
                cursor=cursor,
                limit=limit,
            )
            return graph_response(
                success=True,
                document_id=document_id,
                status="ready",
                data={
                    nodes_key: list(result.get("nodes") or []),
                    relations_key: list(result.get("relations") or []),
                },
                page=result.get("page") or {
                    "limit": limit,
                    "next_cursor": None,
                },
            )
        except Exception as error:
            return self._error(str(document_id or ""), "ready", error)

    @staticmethod
    def _error(
        document_id: str,
        status: str,
        error: Exception,
    ) -> dict[str, Any]:
        return graph_response(
            success=False,
            document_id=document_id,
            status=status,
            data={},
            error_type=error.__class__.__name__,
            error_message=sanitize_error(error),
        )
