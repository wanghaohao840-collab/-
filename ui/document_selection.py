"""Compatibility wrapper for document selection helpers."""

from assistants.document_selection import (
    DocumentScope,
    ParsedDocumentLabel,
    build_document_scope,
    parse_document_label,
    primary_document_label,
)

__all__ = [
    "DocumentScope",
    "ParsedDocumentLabel",
    "build_document_scope",
    "parse_document_label",
    "primary_document_label",
]
