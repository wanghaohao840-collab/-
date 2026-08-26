from __future__ import annotations

from dataclasses import replace

from app.qa_context import QaContextBuilder
from app.qa_models import QaMessage


def message(
    message_id: str,
    turn_id: str,
    role: str,
    content: str,
    *,
    status: str = "completed",
) -> QaMessage:
    return QaMessage(
        id=message_id,
        conversation_id="conversation",
        user_id="owner",
        turn_id=turn_id,
        role=role,
        status=status,
        mode="joint",
        content=content,
        source_state="none",
        client_request_id="client" if role == "user" else None,
        retry_of_message_id=None,
        memory_id=None,
        memory_sync_status="not_required",
        memory_sync_attempt_count=0,
        memory_sync_lease_owner=None,
        memory_sync_lease_expires_at=None,
        safe_error_code=None,
        trace_id=None,
        version=0,
        created_at=f"2026-08-26T00:00:0{message_id[-1]}Z",
        updated_at="2026-08-26T00:00:00Z",
        completed_at="2026-08-26T00:00:00Z",
    )


def turns():
    return (
        message("u1", "t1", "user", "旧问题"),
        message("a1", "t1", "assistant", "旧回答"),
        message("u2", "t2", "user", "边界问题"),
        message("a2", "t2", "assistant", "边界回答"),
        message("u3", "t3", "user", "最近问题"),
        message("a3", "t3", "assistant", "最近回答"),
    )


def test_context_uses_summary_then_only_complete_turns_after_boundary() -> None:
    result = QaContextBuilder(max_input_tokens=1000).build(
        rolling_summary="早期结论",
        summary_through_message_id="a2",
        messages=turns(),
        current_question="继续比较",
    )
    assert "早期结论" in result.rendered
    assert "最近问题" in result.rendered and "最近回答" in result.rendered
    assert "旧问题" not in result.rendered and "边界问题" not in result.rendered
    assert result.rendered.endswith("当前问题：继续比较")
    assert result.included_message_ids == ("u3", "a3")


def test_context_drops_oldest_whole_turn_and_ignores_incomplete_turns() -> None:
    items = (*turns(), message("u4", "t4", "user", "未完成问题"))
    result = QaContextBuilder(max_input_tokens=55).build(
        rolling_summary=None,
        summary_through_message_id=None,
        messages=items,
        current_question="当前",
    )
    included = set(result.included_message_ids)
    assert not ({"u1", "a1"} & included) or {"u1", "a1"} <= included
    assert "u4" not in included
    assert result.truncated
    assert result.rendered.endswith("当前问题：当前")


def test_context_excludes_failed_cancelled_and_pending_assistants() -> None:
    base = turns()[:2]
    for status in ("failed", "cancelled", "pending"):
        items = (base[0], replace(base[1], status=status))
        result = QaContextBuilder(max_input_tokens=1000).build(
            rolling_summary=None,
            summary_through_message_id=None,
            messages=items,
            current_question="当前",
        )
        assert result.included_message_ids == ()


def test_context_keeps_full_question_when_it_alone_exceeds_budget() -> None:
    question = "问题" * 100
    result = QaContextBuilder(max_input_tokens=5).build(
        rolling_summary="摘要" * 100,
        summary_through_message_id=None,
        messages=turns(),
        current_question=question,
    )
    assert result.rendered.endswith(f"当前问题：{question}")
    assert result.truncated
