"""Pure helpers for document label selection across assistant and UI layers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class ParsedDocumentLabel:
    label: str
    document_name: str
    document_id: str


@dataclass(frozen=True)
class DocumentScope:
    labels: Optional[list[str]]
    document_ids: Optional[list[str]]
    document_names: Optional[list[str]]


def parse_document_label(label: str) -> ParsedDocumentLabel:
    text = str(label or "").strip()
    if not text:
        raise ValueError("document label cannot be empty")

    if "|" in text:
        name, doc_id = text.rsplit("|", 1)
        document_name = name.strip()
        document_id = doc_id.strip()
    else:
        document_name = text
        document_id = text

    if not document_id:
        raise ValueError("document_id cannot be empty")

    return ParsedDocumentLabel(
        label=text,
        document_name=document_name or document_id,
        document_id=document_id,
    )


def build_document_scope(selection: Optional[Sequence[str] | str]) -> DocumentScope:
    if selection is None:
        return DocumentScope(labels=None, document_ids=None, document_names=None)

    values = [selection] if isinstance(selection, str) else list(selection)
    labels: list[str] = []
    document_ids: list[str] = []
    document_names: list[str] = []
    seen: set[str] = set()

    for value in values:
        if not str(value or "").strip():
            continue
        parsed = parse_document_label(str(value))
        if parsed.document_id in seen:
            continue
        seen.add(parsed.document_id)
        labels.append(parsed.label)
        document_ids.append(parsed.document_id)
        document_names.append(parsed.document_name)

    return DocumentScope(
        labels=labels,
        document_ids=document_ids,
        document_names=document_names,
    )


def primary_document_label(selection: Optional[Sequence[str] | str]) -> Optional[str]:
    scope = build_document_scope(selection)
    if scope.labels is None:
        return None
    if len(scope.labels) > 1:
        raise ValueError("single-document operation received multiple documents")
    return scope.labels[0] if scope.labels else None
