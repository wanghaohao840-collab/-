import hashlib
import unittest

from hello_agents.memory.rag.result_utils import (
    content_digest,
    dedupe_results_by_source,
    normalize_document_scope,
    normalize_page_number,
    resolve_qa_mode,
)


class ResultUtilsTests(unittest.TestCase):
    def test_resolve_qa_mode_preserves_manual_mode_and_auto_priority(self):
        self.assertEqual(resolve_qa_mode("请总结", "joint"), "joint")
        self.assertEqual(resolve_qa_mode("请对比并总结", "auto"), "compare")
        self.assertEqual(resolve_qa_mode("请总结", "auto"), "summary")
        self.assertEqual(resolve_qa_mode("普通问题", "auto"), "joint")

    def test_resolve_qa_mode_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            resolve_qa_mode("问题", "unknown")

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
        self.assertNotEqual(
            content_digest("Alpha" * 40 + "A"),
            content_digest("Alpha" * 40 + "B"),
        )

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

        deduped = dedupe_results_by_source(
            [first, duplicate, different_full_content, other_document],
            limit=10,
        )

        self.assertEqual(len(deduped), 3)
        self.assertIs(deduped[0], first)
        self.assertIn(different_full_content, deduped)
        self.assertIn(other_document, deduped)


if __name__ == "__main__":
    unittest.main()
