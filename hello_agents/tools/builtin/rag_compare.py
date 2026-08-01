from __future__ import annotations

import json
import re
from typing import Any, Optional


COMPARISON_KEYS = (
    "common_points",
    "differences",
    "per_document_evidence",
    "missing_information",
)
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL | re.IGNORECASE)


def parse_structured_comparison(
    answer: str,
    *,
    allowed_citation_ids: set[str],
    selected_document_ids: set[str],
) -> Optional[dict[str, Any]]:
    """Parse and validate the bounded comparison JSON contract."""

    text = str(answer or "").strip()
    fenced = _JSON_FENCE_RE.search(text)
    candidate = fenced.group(1) if fenced else text
    try:
        value = json.loads(candidate)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or any(key not in value for key in COMPARISON_KEYS):
        return None
    if not all(isinstance(value[key], list) for key in COMPARISON_KEYS):
        return None

    for item in value["common_points"]:
        if not _valid_text_citations(item, allowed_citation_ids):
            return None
    for item in value["differences"]:
        if not isinstance(item, dict) or not isinstance(item.get("topic"), str):
            return None
        documents = item.get("documents")
        if not isinstance(documents, list):
            return None
        for document in documents:
            if not _valid_document_item(
                document,
                allowed_citation_ids,
                selected_document_ids,
            ):
                return None
    for item in value["per_document_evidence"]:
        if not _valid_document_item(
            item,
            allowed_citation_ids,
            selected_document_ids,
        ):
            return None
    for item in value["missing_information"]:
        if (
            not isinstance(item, dict)
            or str(item.get("document_id", "")) not in selected_document_ids
            or not isinstance(item.get("note"), str)
        ):
            return None
    return value


def render_comparison_markdown(value: dict[str, Any]) -> str:
    """Render validated comparison data for clients that only support Markdown."""

    lines = ["## 共同点"]
    lines.extend(_render_text_items(value["common_points"]))
    lines.extend(["", "## 差异点"])
    if value["differences"]:
        for difference in value["differences"]:
            lines.append(f"- **{difference['topic']}**")
            for document in difference["documents"]:
                lines.append(
                    f"  - `{document['document_id']}`: "
                    f"{document.get('text') or document.get('summary', '')}"
                    f"{_citation_suffix(document)}"
                )
    else:
        lines.append("- 暂无")

    lines.extend(["", "## 逐文档依据"])
    for item in value["per_document_evidence"]:
        lines.append(
            f"- `{item['document_id']}`: "
            f"{item.get('summary') or item.get('text', '')}"
            f"{_citation_suffix(item)}"
        )
    if not value["per_document_evidence"]:
        lines.append("- 暂无")

    lines.extend(["", "## 信息缺失"])
    for item in value["missing_information"]:
        lines.append(f"- `{item['document_id']}`: {item['note']}")
    if not value["missing_information"]:
        lines.append("- 无")
    return "\n".join(lines)


def _valid_text_citations(item: Any, allowed: set[str]) -> bool:
    if not isinstance(item, dict) or not isinstance(item.get("text"), str):
        return False
    citations = item.get("citations")
    return (
        isinstance(citations, list)
        and all(isinstance(value, str) and value in allowed for value in citations)
    )


def _valid_document_item(
    item: Any,
    allowed_citations: set[str],
    selected_documents: set[str],
) -> bool:
    if not isinstance(item, dict):
        return False
    if str(item.get("document_id", "")) not in selected_documents:
        return False
    if not isinstance(item.get("text", item.get("summary")), str):
        return False
    citations = item.get("citations")
    return (
        isinstance(citations, list)
        and all(
            isinstance(value, str) and value in allowed_citations
            for value in citations
        )
    )


def _render_text_items(items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return ["- 暂无"]
    return [
        f"- {item['text']}{_citation_suffix(item)}"
        for item in items
    ]


def _citation_suffix(item: dict[str, Any]) -> str:
    citations = item.get("citations") or []
    return " " + " ".join(f"[{citation}]" for citation in citations) if citations else ""
