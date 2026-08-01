"""Deterministic golden-case checks for multi-document QA traces."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    query: str
    mode: str
    document_ids: tuple[str, ...]
    required_document_ids: tuple[str, ...]
    forbidden_document_ids: tuple[str, ...]
    required_answer_markers: tuple[str, ...] = ()
    required_prompt_markers: tuple[str, ...] = ()
    minimum_llm_calls: int = 1


@dataclass(frozen=True)
class EvaluationResult:
    case_id: str
    passed: bool
    failures: tuple[str, ...] = ()


def load_cases(path: str | Path) -> list[EvaluationCase]:
    raw_cases = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        EvaluationCase(
            case_id=item["case_id"],
            query=item["query"],
            mode=item["mode"],
            document_ids=tuple(item["document_ids"]),
            required_document_ids=tuple(item.get("required_document_ids", [])),
            forbidden_document_ids=tuple(item.get("forbidden_document_ids", [])),
            required_answer_markers=tuple(item.get("required_answer_markers", [])),
            required_prompt_markers=tuple(item.get("required_prompt_markers", [])),
            minimum_llm_calls=int(item.get("minimum_llm_calls", 1)),
        )
        for item in raw_cases
    ]


def evaluate_trace(
    case: EvaluationCase,
    *,
    prompts: Sequence[str],
    output: str,
) -> EvaluationResult:
    failures: list[str] = []
    prompt_text = "\n\n".join(str(prompt) for prompt in prompts)
    combined = f"{prompt_text}\n\n{output}"

    if len(prompts) < case.minimum_llm_calls:
        failures.append(
            f"expected at least {case.minimum_llm_calls} LLM calls, got {len(prompts)}"
        )
    for document_id in case.required_document_ids:
        if document_id not in combined:
            failures.append(f"required document {document_id!r} is absent")
    for document_id in case.forbidden_document_ids:
        if document_id in prompt_text:
            failures.append(f"forbidden document {document_id!r} leaked into prompts")
    for marker in case.required_answer_markers:
        if marker not in output:
            failures.append(f"required answer marker {marker!r} is absent")
    for marker in case.required_prompt_markers:
        if marker not in prompt_text:
            failures.append(f"required prompt marker {marker!r} is absent")

    return EvaluationResult(case.case_id, not failures, tuple(failures))


def format_results(results: Iterable[EvaluationResult]) -> str:
    lines: list[str] = []
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        lines.append(f"[{status}] {result.case_id}")
        lines.extend(f"  - {failure}" for failure in result.failures)
    return "\n".join(lines)


def assert_all_pass(results: Iterable[EvaluationResult]) -> None:
    failures = [result for result in results if not result.passed]
    if failures:
        raise AssertionError(format_results(failures))
