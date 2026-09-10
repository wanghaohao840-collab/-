"""Bounded streaming reader for legacy and managed JSON RAG caches."""
from __future__ import annotations

from datetime import datetime
import math
import os
from pathlib import Path
import stat

import ijson

from hello_agents.memory.rag.index_identity import IndexIdentity, IndexIdentityError
from hello_agents.memory.rag.source_records import (
    SourceInventoryError, canonical, json_chunk, require_name,
)

from hello_agents.memory.rag.source_stream import (
    BoundedJSONReader as _Reader, read_json_value as _value,
)

_BUFFER = 64 * 1024
_MAX_VALUE_BYTES = 8 * 1024 * 1024
_LEGACY = {"collection_name", "rag_namespace", "dimension", "updated_at", "chunk_count"}
_MANAGED = {"schema_version", "identity", "rag_namespace", "updated_at", "chunk_count"}


def _signature(info):
    # Windows/Python can expose different ctime meanings via stat vs fstat.
    # Compare identity across APIs, but ctime only against the same API later.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


class JsonChunkSource:
    """Exhaust records() to validate the envelope and obtain completed_token.

    A token is an observed file digest, not a maintenance lock or snapshot.
    The caller must close the generator when abandoning a scan.
    """
    def __init__(self, path: Path | str, *, collection: str, namespace: str,
                 dimension: int, identity: IndexIdentity | None = None):
        self.path = Path(path)
        if not self.path.is_absolute():
            raise SourceInventoryError("path")
        self.collection = require_name(collection)
        self.namespace = require_name(namespace)
        if type(dimension) is not int or not 1 <= dimension <= 65536:
            raise SourceInventoryError("dimension")
        if identity is not None and (
            identity.backend != "json" or identity.physical_collection != collection
            or identity.profile.dimension != dimension
        ):
            raise SourceInventoryError("identity")
        self.dimension, self.identity = dimension, identity
        self.completed_token = None
        self._running = False

    @property
    def contract(self):
        return {"backend": "json", "collection": self.collection, "namespace": self.namespace,
                "dimension": self.dimension,
                "source_identity": self.identity.to_dict() if self.identity else None}

    def _header(self, header, count):
        expected = _MANAGED if self.identity else _LEGACY
        if (set(header) != expected or header.get("rag_namespace") != self.namespace
                or type(header.get("chunk_count")) is not int
                or header["chunk_count"] != count):
            raise SourceInventoryError("header")
        try:
            value = header["updated_at"]
            if not isinstance(value, str):
                raise ValueError()
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if self.identity:
                if (type(header["schema_version"]) is not int or header["schema_version"] != 2
                        or not value.endswith("Z") or parsed.utcoffset().total_seconds() != 0):
                    raise ValueError()
                IndexIdentity.from_dict(header["identity"]).require_match(self.identity)
            elif (header["collection_name"] != self.collection
                  or type(header["dimension"]) is not int
                  or header["dimension"] != self.dimension):
                raise ValueError()
        except (ValueError, TypeError, AttributeError, IndexIdentityError):
            raise SourceInventoryError("header") from None

    def records(self):
        if self._running:
            raise SourceInventoryError("scan_busy")
        self._running = True
        self.completed_token = None
        try:
            info = self.path.lstat()
            if (not stat.S_ISREG(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & 0x400):
                raise SourceInventoryError("path")
            with self.path.open("rb") as stream:
                before = os.fstat(stream.fileno())
                if _signature(before) != _signature(info):
                    raise SourceInventoryError("source_changed")
                reader = _Reader(stream, max_value_bytes=_MAX_VALUE_BYTES)
                events = iter(ijson.basic_parse(reader, use_float=True, buf_size=_BUFFER))
                if next(events)[0] != "start_map":
                    raise SourceInventoryError("header")
                header, seen = {}, set()
                count = 0
                while True:
                    event, key = next(events)
                    if event == "end_map":
                        break
                    if event != "map_key" or key in seen:
                        raise SourceInventoryError("duplicate_key")
                    seen.add(key)
                    if key not in _LEGACY | _MANAGED | {"chunks"}:
                        raise SourceInventoryError("header")
                    if key == "chunks":
                        if next(events)[0] != "start_array":
                            raise SourceInventoryError("chunks")
                        while True:
                            first = next(events)
                            if first[0] == "end_array":
                                break
                            raw = _value(events, first)
                            canonical(raw)
                            if (not isinstance(raw, dict) or not isinstance(raw.get("vector"), list)
                                    or len(raw["vector"]) != self.dimension
                                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in raw["vector"])
                                    or not any(raw["vector"])):
                                raise SourceInventoryError("vector")
                            chunk = json_chunk(raw, self.namespace, self.identity)
                            count += 1
                            reader.pending = 0
                            yield chunk
                    else:
                        header[key] = _value(events, next(events))
                        canonical(header[key])
                    reader.pending = 0
                if "chunks" not in seen or next(events, None) is not None:
                    raise SourceInventoryError("header")
                self._header(header, count)
                after = os.fstat(stream.fileno())
                path_after = self.path.lstat()
                if (_signature(before) != _signature(after)
                        or before.st_ctime_ns != after.st_ctime_ns
                        or _signature(info) != _signature(path_after)
                        or info.st_ctime_ns != path_after.st_ctime_ns):
                    raise SourceInventoryError("source_changed")
                self.completed_token = reader.hash.hexdigest()
        except SourceInventoryError:
            raise
        except (OSError, UnicodeError, ValueError, OverflowError, RecursionError,
                StopIteration, ijson.JSONError):
            raise SourceInventoryError("json") from None
        finally:
            self._running = False
