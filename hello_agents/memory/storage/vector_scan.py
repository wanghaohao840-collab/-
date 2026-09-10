from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Iterator
import uuid

from hello_agents.memory.rag.errors import RAGOperationError


NativePointId = int | str


class VectorScanError(RAGOperationError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(
            f"Qdrant scan rejected: {code}", operation="scroll", retryable=False
        )


@dataclass(frozen=True)
class VectorScanRecord:
    # Keep transport identity separate from the application's logical ID.
    storage_id: NativePointId
    logical_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class VectorScanPage:
    records: tuple[VectorScanRecord, ...]
    next_offset: NativePointId | None


def native_point_id(value: object) -> NativePointId:
    if type(value) is int and 0 <= value <= 2**64 - 1:
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, str):
        try:
            uuid.UUID(value)
        except (ValueError, AttributeError):
            pass
        else:
            return value
    raise VectorScanError("point_id")


def cursor_key(value: NativePointId) -> tuple[str, int]:
    # UUID textual variants describe the same physical position.
    return ("int", value) if type(value) is int else ("uuid", uuid.UUID(value).int)


def read_qdrant_page(
    fetch: Callable[..., Any],
    *,
    collection_name: str,
    scroll_filter: Any,
    offset: NativePointId | None,
    page_size: int,
) -> VectorScanPage:
    if type(page_size) is not int or not 1 <= page_size <= 256:
        raise VectorScanError("page_size")
    if offset is not None:
        offset = native_point_id(offset)
    response = fetch(
        collection_name=collection_name,
        scroll_filter=scroll_filter,
        offset=offset,
        limit=page_size,
        with_payload=True,
        with_vectors=False,
    )
    if not isinstance(response, (tuple, list)) or len(response) != 2:
        raise VectorScanError("response")
    batch, next_offset = response
    if not isinstance(batch, (tuple, list)) or len(batch) > page_size:
        raise VectorScanError("page_size")
    if next_offset is not None:
        next_offset = native_point_id(next_offset)
        if not batch or (
            offset is not None and cursor_key(next_offset) == cursor_key(offset)
        ):
            raise VectorScanError("cursor")
    records = []
    seen = set()
    for raw in batch:
        storage_id = native_point_id(getattr(raw, "id", None))
        key = cursor_key(storage_id)
        if key in seen:
            raise VectorScanError("duplicate_point")
        seen.add(key)
        payload = getattr(raw, "payload", None)
        if not isinstance(payload, dict):
            raise VectorScanError("payload")
        logical_id = payload.get("_vector_store_id", str(storage_id))
        if (
            not isinstance(logical_id, str)
            or not logical_id
            or len(logical_id) > 512
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in logical_id)
        ):
            raise VectorScanError("logical_id")
        try:
            payload = copy.deepcopy(payload)
        except (TypeError, ValueError, RecursionError):
            raise VectorScanError("payload") from None
        records.append(VectorScanRecord(storage_id, logical_id, payload))
    return VectorScanPage(tuple(records), next_offset)


def iter_qdrant_pages(
    read: Callable[[NativePointId | None], VectorScanPage],
    *,
    offset: NativePointId | None,
    max_pages: int,
) -> Iterator[VectorScanPage]:
    if type(max_pages) is not int or not 1 <= max_pages <= 1_000_000:
        raise VectorScanError("max_pages")
    if offset is not None:
        offset = native_point_id(offset)
    seen = set() if offset is None else {cursor_key(offset)}
    for _ in range(max_pages):
        page = read(offset)
        next_offset = page.next_offset
        if next_offset is not None:
            key = cursor_key(next_offset)
            if key in seen:
                raise VectorScanError("cursor")
            seen.add(key)
        yield page
        if next_offset is None:
            return
        offset = next_offset
    # Reaching a bound with a continuation is failure, never a complete scan.
    raise VectorScanError("page_limit")
