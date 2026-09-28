"""Shared upload validation for local staging and object publication."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable

from app.import_models import ImportLimits
from app.storage import SUPPORTED_DOCUMENT_SUFFIXES

STAGING_CHUNK_BYTES = 1024 * 1024


class ImportLimitError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class ImportUpload:
    original_name: str
    stream: BinaryIO


@dataclass(frozen=True)
class PendingImport:
    upload: ImportUpload
    original_name: str
    suffix: str
    task_id: str
    document_id: str


def validate_suffix(suffix: str) -> str:
    normalized = suffix.lower()
    if normalized not in SUPPORTED_DOCUMENT_SUFFIXES:
        raise ValueError(f"Unsupported document type: {suffix}")
    return normalized


def inspect_upload(
    upload: ImportUpload, suffix_validator: Callable[[str], str] = validate_suffix
) -> PendingImport:
    if not isinstance(upload, ImportUpload):
        raise ValueError("Uploaded document is invalid")
    raw_name = str(upload.original_name or "").replace("\\", "/")
    original_name = Path(raw_name).name
    if (
        not original_name
        or original_name in {".", ".."}
        or "\x00" in original_name
        or not hasattr(upload.stream, "read")
    ):
        raise ValueError("Uploaded document name is invalid")
    suffix = suffix_validator(Path(original_name).suffix)
    return PendingImport(
        upload=upload,
        original_name=original_name,
        suffix=suffix,
        task_id=str(uuid.uuid4()),
        document_id=str(uuid.uuid4()),
    )


def validate_file_count(count: int, limits: ImportLimits) -> None:
    if count == 0:
        raise ImportLimitError(
            "import_no_files", "at least one file is required", status_code=400
        )
    if count > limits.max_files:
        raise ImportLimitError(
            "import_too_many_files",
            f"batch cannot contain more than {limits.max_files} files",
            status_code=413,
        )


@dataclass
class StreamSizeCounter:
    limits: ImportLimits
    batch_bytes: int = 0

    def check(self, chunk: bytes, file_bytes: int) -> int:
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise ValueError("Uploaded document stream must be binary")
        chunk_bytes = chunk.nbytes if isinstance(chunk, memoryview) else len(chunk)
        file_bytes += chunk_bytes
        self.batch_bytes += chunk_bytes
        if file_bytes > self.limits.max_file_bytes:
            raise ImportLimitError(
                "import_file_too_large",
                f"each file must be at most {self.limits.max_file_bytes} bytes",
                status_code=413,
            )
        if self.batch_bytes > self.limits.max_batch_bytes:
            raise ImportLimitError(
                "import_batch_too_large",
                f"batch must be at most {self.limits.max_batch_bytes} bytes",
                status_code=413,
            )
        return file_bytes
