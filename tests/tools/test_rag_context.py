import copy

import pytest

from hello_agents.tools.builtin.rag_context import (
    ContextCapacityError,
    citation_id,
    context_budget,
    fit_context,
)


class CharacterLLM:
    context_window_tokens = 2000

    def estimate_tokens(self, text):
        return len(text or "")


def source_text(metadata, truncated=False):
    value = metadata.get("document_id", "")
    return value + (" | 上下文已截断" if truncated else "")


def result(document_id, content, score=1.0, protected=False):
    return {
        "content": content,
        "score": score,
        "metadata": {"document_id": document_id},
        "_protected": protected,
    }


def test_context_budget_accounts_for_fixed_prompt_and_reserves():
    llm = CharacterLLM()

    assert context_budget(llm, "fixed", output_reserve=100, safety_margin=50) == 1845


def test_fit_context_truncates_copies_at_semantic_floor_and_marks_source():
    original = result("doc-1", "甲" * 500, protected=True)
    snapshot = copy.deepcopy(original)

    fitted = fit_context(
        [original], token_budget=260, llm=CharacterLLM(), format_source=source_text
    )

    assert fitted.truncated is True
    assert len(fitted.results[0]["content"]) >= 200
    assert fitted.results[0]["truncated"] is True
    assert "上下文已截断" in fitted.context
    assert original == snapshot


def test_fit_context_removes_low_score_unprotected_result_first():
    fitted = fit_context(
        [
            result("base", "B" * 200, score=0.9, protected=True),
            result("extra", "E" * 200, score=0.1),
        ],
        token_budget=240,
        llm=CharacterLLM(),
        format_source=source_text,
    )

    assert [item["metadata"]["document_id"] for item in fitted.results] == ["base"]


def test_fit_context_fails_instead_of_truncating_protected_text_below_floor():
    with pytest.raises(ContextCapacityError):
        fit_context(
            [result("doc-1", "甲" * 500, protected=True)],
            token_budget=100,
            llm=CharacterLLM(),
            format_source=source_text,
        )


def test_citation_id_uses_full_content_before_truncation():
    item = result("doc-1", "prefix-" + "A" * 300)
    stable = citation_id(item)
    truncated_copy = copy.deepcopy(item)
    truncated_copy["content"] = truncated_copy["content"][:200]

    assert stable != citation_id(truncated_copy)
