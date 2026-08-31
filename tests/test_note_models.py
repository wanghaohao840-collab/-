from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.note_models import NoteFilters, NoteSourceSelector, validate_note_input


def test_note_input_is_normalized_and_deduplicates_tags() -> None:
    body, concept, tags = validate_note_input(
        "  useful markdown  ", "  Concept  ", (" Topic ", "Ｔopic", "topic")
    )

    assert body == "useful markdown"
    assert concept == "Concept"
    assert tags == ("Topic",)


@pytest.mark.parametrize(
    ("body", "concept", "tags", "field"),
    [
        ("", None, (), "body_markdown"),
        ("x" * 20_001, None, (), "body_markdown"),
        ("valid", "x" * 121, (), "concept"),
        ("valid", None, tuple(f"tag-{index}" for index in range(11)), "tags"),
        ("valid", None, ("x" * 33,), "tags"),
    ],
)
def test_note_input_rejects_invalid_values(body, concept, tags, field) -> None:
    with pytest.raises(ValueError, match=field):
        validate_note_input(body, concept, tags)


def test_filters_and_source_selectors_are_immutable() -> None:
    filters = NoteFilters(query="term")
    selector = NoteSourceSelector(kind="qa_citation", qa_message_id="m", citation_id="c")

    with pytest.raises(FrozenInstanceError):
        filters.query = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        selector.citation_id = None  # type: ignore[misc]


def test_source_selector_validates_kind_specific_fields() -> None:
    with pytest.raises(ValueError, match="citation_id"):
        NoteSourceSelector(kind="qa_citation", qa_message_id="m")
    with pytest.raises(ValueError, match="citation_id"):
        NoteSourceSelector(kind="qa_answer", qa_message_id="m", citation_id="c")
