"""Bounded JSON event values shared by migration source readers."""
from __future__ import annotations

import hashlib

from hello_agents.memory.rag.source_records import SourceInventoryError

_BUFFER = 64 * 1024
_MAX_VALUE_BYTES = 8 * 1024 * 1024


class BoundedJSONReader:
    def __init__(self, stream, *, max_value_bytes=_MAX_VALUE_BYTES):
        if type(max_value_bytes) is not int or not 1 <= max_value_bytes <= _MAX_VALUE_BYTES:
            raise SourceInventoryError("value_size")
        self.max_value_bytes = max_value_bytes
        self.stream = stream
        self.hash = hashlib.sha256()
        self.pending = 0

    def read(self, size):
        if size == 0:
            return b""
        data = self.stream.read(min(size, _BUFFER) if size > 0 else _BUFFER)
        self.pending += len(data)
        if self.pending > self.max_value_bytes:
            raise SourceInventoryError("value_size")
        self.hash.update(data)
        return data


def read_json_value(events, first, depth=0, budget=None):
    budget = [100_000] if budget is None else budget
    budget[0] -= 1
    if depth > 32 or budget[0] < 0:
        raise SourceInventoryError("value_size")
    event, value = first
    if event == "start_map":
        result = {}
        while True:
            event, key = next(events)
            if event == "end_map":
                return result
            if event != "map_key" or key in result:
                raise SourceInventoryError("duplicate_key")
            result[key] = read_json_value(events, next(events), depth + 1, budget)
    if event == "start_array":
        result = []
        while True:
            item = next(events)
            if item[0] == "end_array":
                return result
            result.append(read_json_value(events, item, depth + 1, budget))
    if event in {"null", "boolean", "number", "string", "integer", "double"}:
        return value
    raise SourceInventoryError("json")
