from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from app.qa_models import QaMessage


@dataclass(frozen=True)
class QaContextWindow:
    rendered: str
    included_message_ids: tuple[str, ...]
    used_summary: bool
    truncated: bool


class QaContextBuilder:
    def __init__(
        self,
        max_input_tokens: int,
        token_estimator: Callable[[str], int] | None = None,
    ) -> None:
        self.max_input_tokens = max(1, int(max_input_tokens))
        self._estimate = token_estimator or (lambda value: len(str(value or "")))

    def build(
        self,
        *,
        rolling_summary: str | None,
        summary_through_message_id: str | None,
        messages: Sequence[QaMessage],
        current_question: str,
    ) -> QaContextWindow:
        complete_turns = self._complete_turns(
            messages, summary_through_message_id
        )
        summary = str(rolling_summary or "").strip() or None
        truncated = False
        rendered = self._render(complete_turns, summary, current_question)

        while complete_turns and self._estimate(rendered) > self.max_input_tokens:
            complete_turns.pop(0)
            truncated = True
            rendered = self._render(complete_turns, summary, current_question)

        if summary and self._estimate(rendered) > self.max_input_tokens:
            original = summary
            keep = len(original)
            while keep > 0 and self._estimate(rendered) > self.max_input_tokens:
                keep //= 2
                summary = original[-keep:] if keep else None
                rendered = self._render(complete_turns, summary, current_question)
            truncated = True

        if self._estimate(rendered) > self.max_input_tokens:
            truncated = True
        included = tuple(
            item.id for turn in complete_turns for item in turn
        )
        return QaContextWindow(
            rendered=rendered,
            included_message_ids=included,
            used_summary=bool(summary),
            truncated=truncated,
        )

    @staticmethod
    def _complete_turns(
        messages: Sequence[QaMessage], boundary_id: str | None
    ) -> list[tuple[QaMessage, QaMessage]]:
        after_boundary = boundary_id is None
        grouped: dict[str, list[QaMessage]] = {}
        order: list[str] = []
        for message in messages:
            if message.id == boundary_id:
                after_boundary = True
                continue
            if not after_boundary or message.status != "completed":
                continue
            if message.turn_id not in grouped:
                grouped[message.turn_id] = []
                order.append(message.turn_id)
            grouped[message.turn_id].append(message)

        turns: list[tuple[QaMessage, QaMessage]] = []
        for turn_id in order:
            items = grouped[turn_id]
            users = [item for item in items if item.role == "user"]
            assistants = [item for item in items if item.role == "assistant"]
            if len(users) == 1 and len(assistants) == 1:
                turns.append((users[0], assistants[0]))
        return turns

    @staticmethod
    def _render(
        turns: Sequence[tuple[QaMessage, QaMessage]],
        summary: str | None,
        current_question: str,
    ) -> str:
        parts = [f"历史摘要：{summary}"] if summary else []
        for user, assistant in turns:
            parts.extend((f"用户：{user.content}", f"助手：{assistant.content}"))
        parts.append(f"当前问题：{current_question}")
        return "\n\n".join(parts)
