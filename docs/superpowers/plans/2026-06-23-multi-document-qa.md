# Multi-Document QA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit 1-10 document scoped retrieval, comparison, and map-reduce summary while preserving the existing single-document behavior and `document_id` data isolation.

**Architecture:** Keep the existing one-way dependency chain `UI -> PDFLearningAssistant -> RAGTool -> SimpleRAGPipeline`. Put scope normalization and source dedupe in the RAG memory layer, prompt-budget/context shaping in the RAG tool layer, and UI-only selection parsing in a small UI helper module. Do not change persisted chunk/cache format and do not introduce Qdrant, Neo4j, saved document sets, or new dependencies.

**Tech Stack:** Python standard library, `unittest`, existing JSON-backed `SimpleRAGPipeline`, existing `HelloAgentsLLM`, existing Gradio UI.

## Global Constraints

- Before implementation, read `PROJECT_KNOWLEDGE.md` and treat current code/tests/runtime behavior as authoritative when history disagrees.
- Preserve `document_id` isolation: selected scope must never silently expand to unselected documents.
- Support explicit user selection of 1-10 already imported documents.
- Modes: `auto`, `joint`, `compare`, `summary`.
- Auto priority: `compare > summary > joint`.
- Compare requires at least 2 documents and must not silently downgrade to joint QA.
- Compare base evidence: `COMPARE_BASE_CHUNKS_PER_DOC = 2`.
- Compare extra evidence: `COMPARE_EXTRA_CHUNKS_PER_DOC = 3`.
- Prompt budget constants: `LLM_CONTEXT_WINDOW_TOKENS = 8_192`, `ANSWER_OUTPUT_TOKEN_RESERVE = 1_024`, `DOCUMENT_SUMMARY_OUTPUT_TOKEN_RESERVE = 384`, `TOKEN_SAFETY_MARGIN = 512`, `MIN_TRUNCATED_CHARS = 200`, `SUMMARY_MAX_WORKERS = 3`.
- `HelloAgentsLLM.estimate_tokens(text)` uses a matching tokenizer when available; initial implementation uses `len(text or "")` with safety margin and adds no dependency.
- Source dedupe key is `(document_id, normalized_page_number, content_digest)`.
- `content_digest` is SHA-256 hex over full, untruncated content after Unicode NFKC normalization, CRLF/CR newline normalization to `\n`, and leading/trailing whitespace trim.
- Runtime dedupe must not use `chunk_index`, top-level `id`, or `metadata.memory_id`.
- Budget compression operates only on copied result dictionaries and copied text; it must never mutate pipeline chunks or persisted JSON cache.
- Truncated context sources must be marked with `truncated=True`; removed chunks must not appear in the model context or final sources.
- Use `unittest`; do not add `pytest` or any other test dependency.
- Keep existing `document_id`-only public calls compatible.
- Do not migrate existing history or chunk cache.

---

## File Structure

- Create `hello_agents/memory/rag/result_utils.py`: shared scope normalization, page normalization, full-content digest, and source dedupe utilities. This lives under `memory/rag` so both Pipeline and Tool can depend on it without reversing dependencies.
- Modify `hello_agents/memory/rag/pipeline.py`: add `document_ids` support to `search()`, keep `document_id` compatibility, and replace page-only dedupe with full source dedupe.
- Modify `hello_agents/core/llm.py`: add context-window config and `estimate_tokens(text)`.
- Create `hello_agents/tools/builtin/rag_context.py`: constants, copied-result budget fitting, source reference formatting, and context block construction.
- Modify `hello_agents/tools/builtin/rag_tool.py`: add document-scope validation, mode dispatch, joint QA/search, compare mode, map-reduce summary mode, and truncated source formatting.
- Modify `assistants/pdf_learning_assistant.py`: parse multi-document labels, resolve modes, validate public Assistant inputs, and write compatible history records.
- Create `ui/document_selection.py`: pure helpers for Gradio multi-select labels, mode labels, and single-document-only operations.
- Modify `ui/gradio_app.py`: switch QA/search dropdowns to multi-select, add QA mode radio, and keep delete/switch as single-document operations.
- Create tests under `tests/` using `unittest`:
  - `tests/memory/rag/test_result_utils.py`
  - `tests/memory/rag/test_pipeline_multi_document.py`
  - `tests/core/test_llm_budget.py`
  - `tests/tools/test_rag_context.py`
  - `tests/tools/test_rag_tool_multi_document.py`
  - `tests/assistants/test_pdf_learning_assistant_multi_document.py`
  - `tests/ui/test_document_selection.py`

## Implementation Tasks

### Task 1: RAG result scope and source identity utilities

**Files:**
- Create: `hello_agents/memory/rag/result_utils.py`
- Modify: `hello_agents/memory/rag/pipeline.py`
- Test: `tests/memory/rag/test_result_utils.py`
- Test: `tests/memory/rag/test_pipeline_multi_document.py`

**Interfaces:**
- Produces: `normalize_document_scope(document_id: Optional[str] = None, document_ids: Optional[list[str]] = None) -> Optional[list[str]]`
- Produces: `normalize_page_number(value: Any) -> Optional[str]`
- Produces: `content_digest(content: str) -> str`
- Produces: `dedupe_results_by_source(results: list[dict], limit: int) -> list[dict]`
- Produces: `SimpleRAGPipeline.search(query, limit=5, min_score=0.0, document_id=None, document_ids=None, **kwargs)`

- [ ] **Step 1: Write failing utility tests**

Create `tests/memory/rag/test_result_utils.py`:

```python
import hashlib
import unittest

from hello_agents.memory.rag.result_utils import (
    content_digest,
    dedupe_results_by_source,
    normalize_document_scope,
    normalize_page_number,
)


class ResultUtilsTests(unittest.TestCase):
    def test_normalize_document_scope_keeps_none_distinct_from_empty_list(self):
        self.assertIsNone(normalize_document_scope())
        self.assertEqual(normalize_document_scope(document_ids=[]), [])
        self.assertEqual(normalize_document_scope(document_ids=["", "  "]), [])

    def test_normalize_document_scope_preserves_order_and_dedupes(self):
        self.assertEqual(
            normalize_document_scope(document_ids=["doc-b", "doc-a", "doc-b", ""]),
            ["doc-b", "doc-a"],
        )

    def test_normalize_document_scope_rejects_conflict(self):
        with self.assertRaises(ValueError) as ctx:
            normalize_document_scope(document_id="doc-a", document_ids=["doc-a"])
        self.assertIn("document_id", str(ctx.exception))
        self.assertIn("document_ids", str(ctx.exception))

    def test_normalize_page_number_collapses_missing_values(self):
        self.assertIsNone(normalize_page_number(None))
        self.assertIsNone(normalize_page_number(""))
        self.assertIsNone(normalize_page_number("   "))
        self.assertEqual(normalize_page_number(3), "3")
        self.assertEqual(normalize_page_number(" 003 "), "003")

    def test_content_digest_uses_full_normalized_content_sha256(self):
        text = "  Alpha\r\nBeta  "
        expected = hashlib.sha256("Alpha\nBeta".encode("utf-8")).hexdigest()
        self.assertEqual(content_digest(text), expected)
        self.assertNotEqual(content_digest("Alpha" * 40 + "A"), content_digest("Alpha" * 40 + "B"))

    def test_dedupe_uses_document_page_and_full_content_digest(self):
        first = {
            "content": "Same prefix " + "A" * 200,
            "metadata": {"document_id": "doc-1", "page_number": None},
            "score": 0.9,
        }
        duplicate = {
            "content": "Same prefix " + "A" * 200,
            "metadata": {"document_id": "doc-1"},
            "score": 0.7,
        }
        different_full_content = {
            "content": "Same prefix " + "B" * 200,
            "metadata": {"document_id": "doc-1"},
            "score": 0.8,
        }
        other_document = {
            "content": "Same prefix " + "A" * 200,
            "metadata": {"document_id": "doc-2"},
            "score": 0.6,
        }

        deduped = dedupe_results_by_source([first, duplicate, different_full_content, other_document], limit=10)

        self.assertEqual(len(deduped), 3)
        self.assertIs(deduped[0], first)
        self.assertIn(different_full_content, deduped)
        self.assertIn(other_document, deduped)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run utility tests and verify they fail**

Run: `python -m unittest tests.memory.rag.test_result_utils -v`

Expected: FAIL or ERROR because `hello_agents.memory.rag.result_utils` does not exist.

- [ ] **Step 3: Implement `result_utils.py`**

Create `hello_agents/memory/rag/result_utils.py`:

```python
"""Utilities for document-scoped RAG result handling."""

from __future__ import annotations

import hashlib
import unicodedata
from typing import Any, Optional


def normalize_document_scope(
    document_id: Optional[str] = None,
    document_ids: Optional[list[str]] = None,
) -> Optional[list[str]]:
    """Normalize legacy and multi-document scope parameters.

    Returns:
        None: caller did not specify a scope.
        []: caller explicitly provided an empty scope.
        list[str]: non-empty, de-duplicated document ids in first-seen order.
    """
    if document_id is not None and document_ids is not None:
        raise ValueError("document_id 与 document_ids 不能同时传入")

    if document_id is not None:
        doc_id = str(document_id).strip()
        return [doc_id] if doc_id else []

    if document_ids is None:
        return None

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_id in document_ids:
        doc_id = str(raw_id).strip()
        if not doc_id or doc_id in seen:
            continue
        normalized.append(doc_id)
        seen.add(doc_id)
    return normalized


def normalize_page_number(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_content_for_digest(content: str) -> str:
    text = unicodedata.normalize("NFKC", content or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def content_digest(content: str) -> str:
    normalized = _normalize_content_for_digest(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def dedupe_results_by_source(results: list[dict], limit: int) -> list[dict]:
    deduped: list[dict] = []
    seen: set[tuple[Optional[str], Optional[str], str]] = set()

    for result in results:
        metadata = result.get("metadata") or {}
        key = (
            metadata.get("document_id"),
            normalize_page_number(metadata.get("page_number")),
            content_digest(result.get("content", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(result)
        if len(deduped) >= limit:
            break

    return deduped
```

- [ ] **Step 4: Write failing Pipeline multi-document tests**

Create `tests/memory/rag/test_pipeline_multi_document.py`:

```python
import tempfile
import unittest

from hello_agents.memory.rag.pipeline import SimpleRAGPipeline


class PipelineMultiDocumentTests(unittest.TestCase):
    def make_pipeline(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        pipeline = SimpleRAGPipeline(cache_dir=tmpdir.name)
        pipeline.dimension = 2
        pipeline._to_vector = lambda text: [1.0, 0.0] if "alpha" in text.lower() else [0.0, 1.0]
        return pipeline

    def test_search_filters_to_selected_document_ids(self):
        pipeline = self.make_pipeline()
        pipeline.add_text("alpha only doc one", {"document_id": "doc-1", "document_name": "One"})
        pipeline.add_text("alpha only doc two", {"document_id": "doc-2", "document_name": "Two"})
        pipeline.add_text("alpha only doc three", {"document_id": "doc-3", "document_name": "Three"})

        results = pipeline.search("alpha", limit=10, document_ids=["doc-2", "doc-1"])

        self.assertEqual({r["metadata"]["document_id"] for r in results}, {"doc-1", "doc-2"})
        self.assertNotIn("doc-3", {r["metadata"]["document_id"] for r in results})

    def test_search_keeps_legacy_document_id_behavior(self):
        pipeline = self.make_pipeline()
        pipeline.add_text("alpha doc one", {"document_id": "doc-1"})
        pipeline.add_text("alpha doc two", {"document_id": "doc-2"})

        results = pipeline.search("alpha", limit=10, document_id="doc-1")

        self.assertEqual([r["metadata"]["document_id"] for r in results], ["doc-1"])

    def test_search_rejects_empty_document_ids_without_expanding_to_all_docs(self):
        pipeline = self.make_pipeline()
        pipeline.add_text("alpha doc one", {"document_id": "doc-1"})

        with self.assertRaises(ValueError) as ctx:
            pipeline.search("alpha", limit=10, document_ids=[])

        self.assertIn("document_ids", str(ctx.exception))

    def test_source_dedupe_does_not_merge_different_unpaged_chunks(self):
        pipeline = self.make_pipeline()
        pipeline.add_text("alpha same prefix A" * 20, {"document_id": "doc-1"})
        pipeline.add_text("alpha same prefix B" * 20, {"document_id": "doc-1"})

        results = pipeline.search("alpha", limit=10, document_ids=["doc-1"])

        self.assertEqual(len(results), 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 5: Run Pipeline tests and verify they fail**

Run: `python -m unittest tests.memory.rag.test_pipeline_multi_document -v`

Expected: FAIL because `SimpleRAGPipeline.search()` does not accept or enforce `document_ids`.

- [ ] **Step 6: Modify `pipeline.py`**

In `hello_agents/memory/rag/pipeline.py`, import:

```python
from .result_utils import dedupe_results_by_source, normalize_document_scope
```

Change `search()` signature to:

```python
def search(
    self,
    query: str,
    limit: int = 5,
    min_score: float = 0.0,
    document_id: Optional[str] = None,
    document_ids: Optional[List[str]] = None,
    **kwargs: Any,
) -> List[Dict[str, Any]]:
```

At the start of `search()` after the empty-query guard, add:

```python
scope = normalize_document_scope(document_id=document_id, document_ids=document_ids)
if scope == []:
    raise ValueError("document_ids 不能为空")
scope_set = set(scope) if scope is not None else None
```

Replace the existing single-document filter:

```python
if document_id and item.metadata.get("document_id") != document_id:
    continue
```

with:

```python
if scope_set is not None and item.metadata.get("document_id") not in scope_set:
    continue
```

Replace the final page-only dedupe call:

```python
return self._dedupe_results_by_page(results, limit)
```

with:

```python
return dedupe_results_by_source(results, limit)
```

Keep `_dedupe_results_by_page()` in place for compatibility if other code imports it, but stop using it in `search()`.

- [ ] **Step 7: Run Task 1 tests and verify they pass**

Run:

```powershell
python -m unittest tests.memory.rag.test_result_utils tests.memory.rag.test_pipeline_multi_document -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit Task 1**

Run:

```powershell
git add hello_agents/memory/rag/result_utils.py hello_agents/memory/rag/pipeline.py tests/memory/rag/test_result_utils.py tests/memory/rag/test_pipeline_multi_document.py
git commit -m "feat: add multi-document RAG scope utilities"
```

