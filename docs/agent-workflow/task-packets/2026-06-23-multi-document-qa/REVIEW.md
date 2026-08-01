# Plan Review: multi-document-qa

- Source plan: `docs/superpowers/plans/2026-06-23-multi-document-qa.md`
- Reviewed commit: `614f84e9d01179ce1272281f77e15550c1dcd764`
- Review date: `2026-07-29`
- Verdict: `accepted-with-revisions`

## Repository evidence

- Relevant implementation:
  - `hello_agents/tools/builtin/rag_tool.py:26` already contains a partial token-budget implementation and stable citation IDs; it does not enforce the 200-character floor, does not preserve all comparison base evidence on capacity failure, and executes summary map tasks serially.
  - `hello_agents/core/llm.py:18` has no `estimate_tokens()` or context-window configuration interface.
  - `assistants/pdf_learning_assistant.py:227` already accepts multi-document selections and enforces the 10-document maximum, but does not validate mode before side effects.
  - `ui/gradio_app.py:628` sets `max_choices=10` for search while the question dropdown lacks the same limit.
  - `hello_agents/memory/rag/pipeline.py:238` and `hello_agents/memory/rag/qdrant_pipeline.py:197` both use `dedupe_results_by_source`; backend parity is already implemented in the current worktree.
  - `hello_agents/tools/builtin/rag_tool.py:872` retains `_legacy_ask`; it is dead compatibility code and is safe to remove only while preserving the active `_ask` interface.
- Relevant tests:
  - `tests/tools/test_rag_tool_multi_document.py` covers basic scope, mode, summary shape, truncation-copy safety, and comparison base evidence.
  - `tests/assistants/test_pdf_learning_assistant_multi_document.py` covers scope propagation, history, legacy calls, the 10-document limit, and manual-mode precedence.
  - `tests/memory/rag/test_qdrant_pipeline.py` covers multi-document filtering and summary sampling but lacks an explicit unpaged-source-dedupe parity case.
- Configuration/runtime facts:
  - `requirements.txt` already includes `pytest==8.4.1`; focused pytest verification is runnable.
  - Focused current suite: `65 passed in 8.81s`.
- Existing worktree changes to preserve:
  - The repository has extensive staged and unstaged multi-user, Qdrant, Neo4j, UI, Assistant, and RAG changes. All packet work must be additive and preserve these changes; no reset, checkout, broad formatting, or unrelated cleanup is allowed.

## Findings

### Blocking

- None. The user explicitly authorized Codex to implement after review; the dirty worktree requires surgical edits but does not prevent them.

### Required revisions

- Treat the plan as corrective completion of the current partial implementation, not greenfield work.
- Use a dedicated `rag_context.py` for budget/source shaping and expose token estimation through `HelloAgentsLLM`.
- Make summary map execution bounded and concurrent with deterministic result ordering and per-document failure isolation.
- Enforce minimum retained content, comparison base coverage, and reduce-stage per-document coverage under capacity pressure.
- Keep backend parity work verification-focused because both pipelines already share source dedupe.
- Use the installed pytest suite for combined verification even though the original plan proposed only `unittest`; no new dependency is needed.

### Non-blocking notes

- The repository contains unrelated Neo4j work. Multi-document changes must not alter graph interfaces or tests.

## Accepted scope

- Goal: complete robust 1–10 document scoped joint QA, fair comparison, and bounded map-reduce summary without changing persisted chunk formats.
- In scope: token budgeting, stable source references, truncation/removal policy, bounded summary concurrency, fair comparison evidence, three-layer selection/mode validation, backend parity tests, dead legacy ask cleanup, and regression tests.
- Out of scope: saved document sets, cross-user retrieval, batch deletion, automatic document selection, graph behavior, persistence migration, or new dependencies.
- Compatibility requirements: preserve `document_id` callers, current history records, JSON cache format, current RAG backend contracts, and single-document behavior.
- Architecture/data-isolation constraints: retain `UI -> Assistant -> RAGTool -> RAGPipeline`; selected scope must never expand to unselected documents.

## Packet graph

| Packet | Depends on | Parallel-safe | Owned files | Outcome |
|---|---|---:|---|---|
| `01-context-budget-and-sources.md` | none | no | LLM, new context helper, focused tests | Reusable bounded context and source primitives |
| `02-rag-orchestration.md` | 01 | no | RAG Tool and orchestration tests | Concurrent map-reduce and fair comparison |
| `03-selection-validation.md` | 02 | no | Assistant/UI selection paths and tests | Consistent 1–10 and mode validation |
| `04-backend-parity-and-regression.md` | 03 | no | Pipeline parity tests and integration review inputs | Backend equivalence and combined regression proof |

## Packet readiness audit

| Packet | Goal/non-goals | Context/interfaces | Prerequisites | Change boundary | Acceptance/tests | Forbidden changes | Handoff format | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `01-context-budget-and-sources.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `02-rag-orchestration.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `03-selection-validation.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `04-backend-parity-and-regression.md` | yes | yes | yes | yes | yes | yes | yes | yes |

## Integration verification

- `python -m pytest tests/tools/test_rag_tool_multi_document.py tests/assistants/test_pdf_learning_assistant_multi_document.py tests/ui/test_document_selection.py tests/memory/rag/test_result_utils.py tests/memory/rag/test_pipeline_multi_document.py tests/memory/rag/test_qdrant_pipeline.py -q`
- `python -m pytest tests -q --ignore=tests/integration/test_neo4j_live.py`

## Final integration review requirement

- Output: `docs/agent-workflow/task-packets/2026-06-23-multi-document-qa/FINAL_INTEGRATION_REVIEW.md`
- Required after: every implementation packet is `done`
- Result must be: `accepted | changes-required | blocked`
- Required checks: cross-packet interfaces, missing requirements, duplicate implementation, central integration points, architecture, compatibility, persistence, isolation, and combined regression verification.

## Open decisions

- None. The user approved sequential implementation according to the reviewed recommendations.
