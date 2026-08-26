from dataclasses import FrozenInstanceError

import pytest

from app.qa_models import (
    QaConversation,
    QaDocumentCandidate,
    QaValidationError,
    conversation_title,
    decode_cursor,
    encode_cursor,
    normalize_question,
    validate_document_candidates,
    validate_mode,
)


def candidate(
    document_id: str,
    *,
    user_id: str = "owner",
    status: str = "ready",
) -> QaDocumentCandidate:
    return QaDocumentCandidate(document_id, f"{document_id}.pdf", user_id, status)


def test_question_mode_and_document_scope_validation() -> None:
    assert normalize_question("  什么是  RAG？\n") == "什么是 RAG？"
    with pytest.raises(QaValidationError) as empty:
        normalize_question("  \n")
    assert empty.value.code == "QA_QUESTION_REQUIRED"

    with pytest.raises(QaValidationError) as compare:
        validate_mode("compare", (candidate("one"),))
    assert compare.value.code == "QA_COMPARE_REQUIRES_MULTIPLE_DOCUMENTS"

    scope = validate_document_candidates(
        "owner", (candidate("one"), candidate("two"))
    )
    assert [(item.document_id, item.position) for item in scope] == [
        ("one", 0),
        ("two", 1),
    ]


@pytest.mark.parametrize(
    ("documents", "code"),
    [
        ((), "QA_DOCUMENT_COUNT_INVALID"),
        ((candidate("one"), candidate("one")), "QA_DOCUMENT_DUPLICATE"),
        ((candidate("one", user_id="other"),), "QA_DOCUMENT_NOT_FOUND"),
        ((candidate("one", status="processing"),), "QA_DOCUMENT_NOT_READY"),
    ],
)
def test_document_scope_rejects_invalid_candidates(documents, code) -> None:
    with pytest.raises(QaValidationError) as error:
        validate_document_candidates("owner", documents)
    assert error.value.code == code


def test_title_counts_grapheme_clusters_and_cursor_is_opaque() -> None:
    family = "👨‍👩‍👧‍👦"
    assert conversation_title(family * 41) == family * 40

    cursor = encode_cursor("2026-08-26T01:02:03Z", "item-1")
    assert "item-1" not in cursor
    assert decode_cursor(cursor) == ("2026-08-26T01:02:03Z", "item-1")
    with pytest.raises(QaValidationError) as error:
        decode_cursor("not-a-valid-cursor")
    assert error.value.code == "QA_CURSOR_INVALID"


def test_domain_records_are_immutable() -> None:
    conversation = QaConversation(
        id="conversation",
        user_id="owner",
        title="新对话",
        origin="product",
        rolling_summary="",
        summary_through_message_id=None,
        summary_version=0,
        version=0,
        created_at="2026-08-26T00:00:00Z",
        updated_at="2026-08-26T00:00:00Z",
        last_message_at="2026-08-26T00:00:00Z",
    )
    with pytest.raises(FrozenInstanceError):
        conversation.title = "changed"  # type: ignore[misc]
