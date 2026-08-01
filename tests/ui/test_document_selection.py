import unittest
from pathlib import Path

from assistants.document_selection import (
    build_document_scope,
    parse_document_label,
    primary_document_label,
)


class DocumentSelectionTests(unittest.TestCase):
    def test_parse_document_label_splits_name_and_document_id(self):
        parsed = parse_document_label("Paper A.pdf | doc-a")

        self.assertEqual(parsed.document_name, "Paper A.pdf")
        self.assertEqual(parsed.document_id, "doc-a")
        self.assertEqual(parsed.label, "Paper A.pdf | doc-a")

    def test_build_document_scope_dedupes_and_preserves_order(self):
        scope = build_document_scope(
            ["B.md | doc-b", "A.md | doc-a", "Again B.md | doc-b", ""]
        )

        self.assertEqual(scope.document_ids, ["doc-b", "doc-a"])
        self.assertEqual(scope.document_names, ["B.md", "A.md"])
        self.assertEqual(scope.labels, ["B.md | doc-b", "A.md | doc-a"])

    def test_build_document_scope_keeps_none_distinct_from_empty_selection(self):
        self.assertIsNone(build_document_scope(None).document_ids)
        self.assertEqual(build_document_scope([]).document_ids, [])

    def test_primary_document_label_requires_one_value_for_single_document_ops(self):
        self.assertEqual(primary_document_label(["A.md | doc-a"]), "A.md | doc-a")
        with self.assertRaises(ValueError):
            primary_document_label(["A.md | doc-a", "B.md | doc-b"])

    def test_both_query_dropdowns_limit_selection_to_ten(self):
        source = (
            Path(__file__).parents[2] / "ui" / "gradio_app.py"
        ).read_text(encoding="utf-8")

        self.assertGreaterEqual(source.count("max_choices=10"), 2)


if __name__ == "__main__":
    unittest.main()
