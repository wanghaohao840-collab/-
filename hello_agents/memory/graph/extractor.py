from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from random import random as default_random
from typing import Any, Callable, Iterable, Optional

from hello_agents.memory.graph.contracts import ExtractedGraph


CONCEPT_RELATIONS = {
    "RELATED_TO",
    "PART_OF",
    "IS_A",
    "CONTRASTS_WITH",
}
KNOWLEDGE_RELATIONS = {"DEPENDS_ON", "PREREQUISITE_OF"}
REQUIRED_KEYS = {
    "concepts",
    "knowledge_points",
    "persons",
    "concept_relations",
    "knowledge_dependencies",
    "person_relations",
    "mentions",
}


class GraphExtractionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        llm_attempt_count: int = 0,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.llm_attempt_count = int(llm_attempt_count)
        self.retryable = bool(retryable)


def normalize_name(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def stable_graph_id(
    document_id: str,
    kind: str,
    name: Any,
    rag_namespace: str = "default",
) -> str:
    normalized = normalize_name(name)
    digest = hashlib.sha256(
        f"{rag_namespace}\0{document_id}\0{kind}\0{normalized}".encode("utf-8")
    ).hexdigest()[:24]
    return f"{document_id}:{kind}:{digest}"


class GraphExtractor:
    SYSTEM_PROMPT = (
        "Extract a document knowledge graph. Return JSON only with keys: "
        "concepts, knowledge_points, persons, concept_relations, "
        "knowledge_dependencies, person_relations, mentions. "
        "Use only supplied chunk_id values. Do not invent document scope."
    )

    def __init__(
        self,
        llm: Any,
        *,
        max_batch_chunks: int = 5,
        max_batch_tokens: int = 4000,
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        random: Callable[[], float] = default_random,
    ):
        self.llm = llm
        self.max_batch_chunks = int(max_batch_chunks)
        self.max_batch_tokens = int(max_batch_tokens)
        self.max_attempts = int(max_attempts)
        self._sleep = sleep
        self._random = random
        if self.max_batch_chunks < 1 or self.max_batch_tokens < 1:
            raise ValueError("batch limits must be positive")

    def extract(
        self,
        document_id: str,
        chunks: list[dict[str, Any]],
        metadata: Optional[dict[str, Any]] = None,
        rag_namespace: str = "default",
    ) -> ExtractedGraph:
        document_id = str(document_id or "").strip()
        if not document_id:
            raise ValueError("document_id is required")
        normalized_chunks = self._normalize_chunks(document_id, chunks)
        if not normalized_chunks:
            raise GraphExtractionError("document has no chunks")

        raw_results: list[dict[str, Any]] = []
        attempt_count = 0
        for batch in self._batches(normalized_chunks):
            try:
                result, attempts = self._extract_batch(batch)
            except GraphExtractionError as error:
                error.llm_attempt_count += attempt_count
                raise
            attempt_count += attempts
            raw_results.append(result)

        try:
            graph = self._build_graph(
                document_id,
                normalized_chunks,
                metadata or {},
                raw_results,
                rag_namespace,
            )
        except GraphExtractionError as error:
            error.llm_attempt_count = attempt_count
            raise
        graph.llm_attempt_count = attempt_count
        return graph

    def _normalize_chunks(
        self,
        document_id: str,
        chunks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        normalized = []
        seen = set()
        for index, raw in enumerate(chunks or []):
            metadata = dict(raw.get("metadata") or {})
            chunk_id = str(
                raw.get("id")
                or raw.get("chunk_id")
                or metadata.get("id")
                or metadata.get("memory_id")
                or ""
            ).strip()
            content = str(raw.get("content") or metadata.get("content") or "")
            if not chunk_id or not content.strip():
                continue
            scoped_id = raw.get("document_id") or metadata.get("document_id")
            if scoped_id and str(scoped_id) != document_id:
                raise GraphExtractionError(
                    f"chunk {chunk_id} belongs to another document"
                )
            if chunk_id in seen:
                raise GraphExtractionError(f"duplicate chunk_id: {chunk_id}")
            seen.add(chunk_id)
            metadata["chunk_index"] = int(
                metadata.get("chunk_index", raw.get("chunk_index", index))
            )
            normalized.append(
                {
                    "id": chunk_id,
                    "content": content,
                    "metadata": metadata,
                }
            )
        normalized.sort(
            key=lambda value: (
                int(value["metadata"].get("chunk_index", 0)),
                value["id"],
            )
        )
        return normalized

    def _windows(
        self,
        chunks: Iterable[dict[str, Any]],
    ) -> Iterable[dict[str, Any]]:
        for chunk in chunks:
            content = chunk["content"]
            if len(content) <= self.max_batch_tokens:
                yield chunk
                continue
            for start in range(0, len(content), self.max_batch_tokens):
                yield {
                    "id": chunk["id"],
                    "content": content[start:start + self.max_batch_tokens],
                    "metadata": chunk["metadata"],
                }

    def _batches(
        self,
        chunks: list[dict[str, Any]],
    ) -> Iterable[list[dict[str, Any]]]:
        batch: list[dict[str, Any]] = []
        tokens = 0
        for window in self._windows(chunks):
            window_tokens = max(1, len(window["content"]))
            if batch and (
                len(batch) >= self.max_batch_chunks
                or tokens + window_tokens > self.max_batch_tokens
            ):
                yield batch
                batch = []
                tokens = 0
            batch.append(window)
            tokens += window_tokens
        if batch:
            yield batch

    def _extract_batch(
        self,
        batch: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], int]:
        validation_error = ""
        for attempt in range(1, self.max_attempts + 1):
            retry_after = None
            prompt_data = {
                "chunks": [
                    {"chunk_id": value["id"], "content": value["content"]}
                    for value in batch
                ]
            }
            prompt = json.dumps(prompt_data, ensure_ascii=False)
            if validation_error:
                prompt += f"\nPrevious output error: {validation_error[:300]}"
            try:
                response = self.llm.chat(
                    prompt,
                    system_prompt=self.SYSTEM_PROMPT,
                    temperature=0,
                    max_tokens=3000,
                )
                failure = self._wrapper_failure(str(response))
                if failure is not None:
                    message, retryable = failure
                    if not retryable or attempt >= self.max_attempts:
                        raise GraphExtractionError(
                            message,
                            llm_attempt_count=attempt,
                            retryable=retryable,
                        )
                    validation_error = message
                else:
                    try:
                        return self._parse_response(str(response)), attempt
                    except (ValueError, TypeError) as error:
                        validation_error = str(error)
                        if attempt >= self.max_attempts:
                            raise GraphExtractionError(
                                validation_error,
                                llm_attempt_count=attempt,
                                retryable=True,
                            ) from error
            except GraphExtractionError:
                raise
            except Exception as error:
                retryable = self._is_retryable_exception(error)
                retry_after = self._retry_after_seconds(error)
                if not retryable or attempt >= self.max_attempts:
                    raise GraphExtractionError(
                        f"{error.__class__.__name__}: {error}",
                        llm_attempt_count=attempt,
                        retryable=retryable,
                    ) from error
                validation_error = f"{error.__class__.__name__}: {error}"

            if attempt < self.max_attempts:
                delay = retry_after
                if delay is None:
                    delay = (1 if attempt == 1 else 2) + 0.25 * self._random()
                self._sleep(min(delay, 30))

        raise GraphExtractionError(
            "graph extraction failed",
            llm_attempt_count=self.max_attempts,
        )

    @staticmethod
    def _wrapper_failure(response: str) -> Optional[tuple[str, bool]]:
        lowered = response.lower()
        if response.startswith("[LLM未配置]"):
            return "LLM is not configured", False
        if response.startswith("[LLM调用失败]"):
            non_retryable = any(
                marker in lowered
                for marker in (
                    "authentication",
                    "permission",
                    "invalid key",
                    "401",
                    "403",
                    "content policy",
                    "configuration",
                )
            )
            retryable = any(
                marker in lowered
                for marker in (
                    "timeout",
                    "connection",
                    "rate limit",
                    "server",
                    "500",
                    "502",
                    "503",
                    "504",
                )
            )
            return response[:500], retryable and not non_retryable
        return None

    @staticmethod
    def _is_retryable_exception(error: Exception) -> bool:
        text = f"{error.__class__.__name__}: {error}".lower()
        if any(
            marker in text
            for marker in (
                "authentication",
                "permission",
                "invalid request",
                "configuration",
                "content policy",
                "401",
                "403",
            )
        ):
            return False
        return any(
            marker in text
            for marker in (
                "timeout",
                "connection",
                "rate",
                "server",
                "500",
                "502",
                "503",
                "504",
            )
        )

    @staticmethod
    def _retry_after_seconds(error: Exception) -> Optional[float]:
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", None)
        if not headers:
            return None
        value = headers.get("retry-after") or headers.get("Retry-After")
        if value in (None, ""):
            return None
        try:
            return max(0.0, min(float(value), 30.0))
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(str(value))
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
                return max(0.0, min(seconds, 30.0))
            except (TypeError, ValueError, OverflowError):
                return None

    @staticmethod
    def _parse_response(response: str) -> dict[str, Any]:
        text = response.strip()
        fenced = re.fullmatch(
            r"```(?:json)?\s*(.*?)\s*```",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if fenced:
            text = fenced.group(1)
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSON: {error.msg}") from error
        if not isinstance(value, dict):
            raise ValueError("graph output must be an object")
        missing = REQUIRED_KEYS - set(value)
        if missing:
            raise ValueError(
                f"graph output missing keys: {', '.join(sorted(missing))}"
            )
        for key in REQUIRED_KEYS:
            if not isinstance(value[key], list):
                raise ValueError(f"{key} must be a list")
        return value

    def _build_graph(
        self,
        document_id: str,
        chunks: list[dict[str, Any]],
        metadata: dict[str, Any],
        batches: list[dict[str, Any]],
        rag_namespace: str,
    ) -> ExtractedGraph:
        chapters, graph_chunks = self._build_structure(
            document_id,
            chunks,
            rag_namespace,
        )
        combined = {key: [] for key in REQUIRED_KEYS}
        for batch in batches:
            for key in REQUIRED_KEYS:
                combined[key].extend(batch[key])

        entities: dict[str, list[dict[str, Any]]] = {}
        indexes: dict[str, dict[str, str]] = {}
        for collection, kind, id_key in (
            ("concepts", "concept", "concept_id"),
            ("knowledge_points", "knowledge_point", "knowledge_point_id"),
            ("persons", "person", "person_id"),
        ):
            values, index = self._entities(
                document_id,
                kind,
                id_key,
                combined[collection],
                rag_namespace,
            )
            entities[collection] = values
            indexes[kind] = index

        valid_chunks = {value["chunk_id"] for value in graph_chunks}
        relations: list[dict[str, Any]] = []
        relations.extend(
            self._named_relations(
                combined["concept_relations"],
                indexes["concept"],
                CONCEPT_RELATIONS,
                valid_chunks,
            )
        )
        relations.extend(
            self._named_relations(
                combined["knowledge_dependencies"],
                indexes["knowledge_point"],
                KNOWLEDGE_RELATIONS,
                valid_chunks,
            )
        )
        relations.extend(
            self._person_relations(
                combined["person_relations"],
                indexes["person"],
                valid_chunks,
            )
        )
        relations.extend(
            self._mentions(combined["mentions"], indexes, valid_chunks)
        )
        relations = self._dedupe_relations(relations)

        document = {
            "name": metadata.get("name") or metadata.get("file_name") or document_id,
            "source": metadata.get("source") or metadata.get("file_path") or "",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        return ExtractedGraph(
            document=document,
            chapters=chapters,
            chunks=graph_chunks,
            concepts=entities["concepts"],
            knowledge_points=entities["knowledge_points"],
            persons=entities["persons"],
            relations=relations,
        )

    @staticmethod
    def _heading_parts(value: Any) -> list[str]:
        if isinstance(value, (list, tuple)):
            return [str(part).strip() for part in value if str(part).strip()]
        text = str(value or "").strip()
        if not text:
            return []
        return [
            part.strip()
            for part in re.split(r"\s*(?:>|/|»)\s*", text)
            if part.strip()
        ]

    def _build_structure(
        self,
        document_id: str,
        chunks: list[dict[str, Any]],
        rag_namespace: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        chapters: list[dict[str, Any]] = []
        chapter_ids: dict[tuple[str, ...], str] = {}
        graph_chunks = []
        for chunk in chunks:
            metadata = chunk["metadata"]
            path = self._heading_parts(metadata.get("heading_path"))
            parent_id = None
            for level in range(1, len(path) + 1):
                prefix = tuple(path[:level])
                if prefix not in chapter_ids:
                    chapter_id = stable_graph_id(
                        document_id,
                        "chapter",
                        "\0".join(prefix),
                        rag_namespace,
                    )
                    chapter_ids[prefix] = chapter_id
                    chapters.append(
                        {
                            "chapter_id": chapter_id,
                            "title": prefix[-1],
                            "level": level,
                            "order": int(metadata.get("chunk_index", 0)),
                            "heading_path": list(prefix),
                            "parent_id": parent_id,
                        }
                    )
                parent_id = chapter_ids[prefix]
            graph_chunks.append(
                {
                    "chunk_id": chunk["id"],
                    "content": chunk["content"],
                    "page_number": metadata.get("page_number"),
                    "chunk_index": int(metadata.get("chunk_index", 0)),
                    "chapter_id": parent_id,
                }
            )
        chapters.sort(key=lambda value: (value["order"], value["level"]))
        return chapters, graph_chunks

    @staticmethod
    def _entities(
        document_id: str,
        kind: str,
        id_key: str,
        raw_values: list[dict[str, Any]],
        rag_namespace: str,
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        values: dict[str, dict[str, Any]] = {}
        for raw in raw_values:
            if not isinstance(raw, dict):
                raise GraphExtractionError(f"{kind} entity must be an object")
            name = str(raw.get("name") or "").strip()
            normalized = normalize_name(name)
            if not normalized:
                raise GraphExtractionError(f"{kind} name is required")
            if normalized not in values:
                values[normalized] = {
                    id_key: stable_graph_id(
                        document_id,
                        kind,
                        normalized,
                        rag_namespace,
                    ),
                    "name": name,
                    "normalized_name": normalized,
                    "description": str(raw.get("description") or "").strip(),
                }
        return list(values.values()), {
            normalized: value[id_key] for normalized, value in values.items()
        }

    def _named_relations(
        self,
        raw_values: list[dict[str, Any]],
        entity_index: dict[str, str],
        whitelist: set[str],
        valid_chunks: set[str],
    ) -> list[dict[str, Any]]:
        relations = []
        for raw in raw_values:
            relation_type = str(raw.get("type") or "").upper()
            if relation_type not in whitelist:
                raise GraphExtractionError(
                    f"unsupported relationship type: {relation_type}"
                )
            source = entity_index.get(normalize_name(raw.get("source")))
            target = entity_index.get(normalize_name(raw.get("target")))
            if not source or not target:
                raise GraphExtractionError("dangling relationship endpoint")
            relations.append(
                self._relation(
                    source,
                    target,
                    relation_type,
                    raw,
                    valid_chunks,
                )
            )
        return relations

    def _person_relations(
        self,
        raw_values: list[dict[str, Any]],
        entity_index: dict[str, str],
        valid_chunks: set[str],
    ) -> list[dict[str, Any]]:
        relations = []
        for raw in raw_values:
            source = entity_index.get(normalize_name(raw.get("source")))
            target = entity_index.get(normalize_name(raw.get("target")))
            if not source or not target:
                raise GraphExtractionError("dangling person relationship endpoint")
            relation = self._relation(
                source,
                target,
                "RELATED_TO",
                raw,
                valid_chunks,
            )
            relation["properties"]["relation_name"] = str(
                raw.get("relation_name") or "related_to"
            ).strip()
            relations.append(relation)
        return relations

    def _mentions(
        self,
        raw_values: list[dict[str, Any]],
        indexes: dict[str, dict[str, str]],
        valid_chunks: set[str],
    ) -> list[dict[str, Any]]:
        aliases = {
            "concept": "concept",
            "knowledge_point": "knowledge_point",
            "knowledgepoint": "knowledge_point",
            "person": "person",
        }
        relations = []
        for raw in raw_values:
            chunk_id = self._chunk_id(raw, valid_chunks)
            target_type = aliases.get(normalize_name(raw.get("target_type")))
            if not target_type:
                raise GraphExtractionError("unsupported mention target type")
            target = indexes[target_type].get(normalize_name(raw.get("target")))
            if not target:
                raise GraphExtractionError("dangling mention target")
            relations.append(
                {
                    "source_id": chunk_id,
                    "target_id": target,
                    "type": "MENTIONS",
                    "properties": self._evidence_properties(raw, chunk_id),
                }
            )
        return relations

    def _relation(
        self,
        source_id: str,
        target_id: str,
        relation_type: str,
        raw: dict[str, Any],
        valid_chunks: set[str],
    ) -> dict[str, Any]:
        chunk_id = self._chunk_id(raw, valid_chunks)
        return {
            "source_id": source_id,
            "target_id": target_id,
            "type": relation_type,
            "properties": self._evidence_properties(raw, chunk_id),
        }

    @staticmethod
    def _chunk_id(raw: dict[str, Any], valid_chunks: set[str]) -> str:
        chunk_id = str(raw.get("chunk_id") or "").strip()
        if chunk_id not in valid_chunks:
            raise GraphExtractionError(
                f"relationship source chunk_id is invalid: {chunk_id}"
            )
        return chunk_id

    @staticmethod
    def _evidence_properties(
        raw: dict[str, Any],
        chunk_id: str,
    ) -> dict[str, Any]:
        try:
            confidence = float(raw.get("confidence", 0.5))
        except (TypeError, ValueError) as error:
            raise GraphExtractionError("confidence must be numeric") from error
        return {
            "chunk_id": chunk_id,
            "evidence": str(raw.get("evidence") or "").strip(),
            "confidence": max(0.0, min(1.0, confidence)),
        }

    @staticmethod
    def _dedupe_relations(
        relations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result = []
        seen = set()
        for relation in relations:
            properties = relation.get("properties") or {}
            key = (
                relation["source_id"],
                relation["target_id"],
                relation["type"],
                properties.get("relation_name"),
                properties.get("chunk_id"),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(relation)
        return result
