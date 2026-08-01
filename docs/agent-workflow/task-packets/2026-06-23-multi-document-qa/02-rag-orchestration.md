---
id: "multi-document-qa-02"
title: "Concurrent summary and fair comparison orchestration"
status: "done"
parallel-safe: false
depends-on: ["multi-document-qa-01"]
base-commit: "614f84e9d01179ce1272281f77e15550c1dcd764"
owner: "codex"
---

# Task Packet: Concurrent summary and fair comparison orchestration

## Goal

Integrate bounded context helpers into RAG Tool, execute per-document summaries with at most three workers and deterministic reduction, and preserve two relevant base chunks per comparison document or fail clearly on capacity.

## Non-goals

- No UI, Assistant, backend persistence, graph, or dependency changes.

## Delivery context

The active `_ask` already supports joint/compare/summary and stable citations. Summary map calls are serial, reduce truncation can cut away complete document summaries, and comparison capacity handling does not guarantee selected-document coverage.

## Relevant files and current interfaces

- `hello_agents/tools/builtin/rag_tool.py:1174` — active `_ask` public behavior.
- `hello_agents/tools/builtin/rag_tool.py:1428` — comparison retrieval and evidence selection.
- `hello_agents/tools/builtin/rag_tool.py:1496` — partial map-reduce implementation.
- `hello_agents/tools/builtin/rag_tool.py:872` — dead `_legacy_ask` implementation.
- `tests/tools/test_rag_tool_multi_document.py` — current fake Pipeline/LLM seams.
- Existing changes to preserve: graph actions and all unrelated tool methods.

## Prerequisites

### Packet dependencies

- `multi-document-qa-01` must be `done`.

### Repository/base state

- Context helper interfaces from packet 01 are available.

### External prerequisites

- none

## Explicit change boundary

### Allowed files

- Modify: `hello_agents/tools/builtin/rag_tool.py`
- Modify: `tests/tools/test_rag_tool_multi_document.py`

### Allowed behavior changes

- Replace inline fitting with shared helpers; change comparison and summary orchestration; remove dead `_legacy_ask` only.

### Forbidden changes

- Do not change Tool action names, Pipeline signatures, persisted formats, Assistant/UI, graph behavior, or unrelated RAG actions.

## Interface contract

### Consumes

- Packet 01 budget/source helper API.
- `pipeline.search(...)` and `pipeline.get_document_summary_context(document_id, limit)`.

### Produces

- Existing `_ask` modes with bounded prompts, deterministic stable sources, bounded concurrent map tasks, and clear failure summaries.

### Invariants

- At most three map tasks execute concurrently.
- Output and sources follow selected document order regardless of completion order.
- No unselected document or removed/truncated-away source enters prompts or output.

## Required behavior

- Comparison reserves two available above-threshold base chunks per selected document, allows at most three extras per document, and admits extras by global score within budget.
- If protected base evidence at the minimum floor cannot fit, return a capacity error without calling the LLM.
- Summary map failures do not cancel other documents; all failures skip reduce.
- Reduce keeps at least 200 characters from every successful summary or fails instead of dropping a document.
- Translate recognizable provider context-length errors into the explicit capacity message without retrying a shortened request.

## Implementation guidance

- Use `ThreadPoolExecutor(max_workers=min(3, len(document_ids)))` and collect futures into a mapping keyed by document ID.
- Preserve ordered assembly after all futures finish.
- Keep protected flags runtime-only and remove them from formatted source concerns.

## Acceptance criteria

- [ ] Concurrent map execution never exceeds three workers and reduction order is deterministic.
- [ ] Partial and total map failures follow the accepted failure behavior.
- [ ] Comparison base evidence and extras follow quota/budget rules.
- [ ] Joint, compare, and summary prompts and final sources contain only actually used results.
- [ ] Legacy `document_id` calls remain compatible and dead `_legacy_ask` is removed.

## Test and verification commands

```powershell
python -m pytest tests/tools/test_rag_tool_multi_document.py tests/tools/test_rag_tool_backend_contract.py tests/tools/test_rag_tool_graph.py -q
```

Expected: all tests pass.

## Stop conditions

Stop if changes require editing Pipeline, Assistant/UI, graph contracts, or dependency manifests.

## Implementation handoff

- Packet: `multi-document-qa-02`
- Status: `done`
- Delivered:
  - Bounded joint QA, fair comparison evidence, and deterministic bounded-concurrency map-reduce summaries.
- Files changed:
  - `hello_agents/tools/builtin/rag_tool.py` — orchestration and dead legacy ask removal.
  - `tests/tools/test_rag_tool_multi_document.py` — capacity, concurrency, and failure tests.
- Interfaces added or changed:
  - Active ask modes preserve their public signatures; `_build_context` now delegates to packet 01 helpers.
- Acceptance evidence:
  - [x] Comparison capacity preserves base coverage or fails before LLM use.
  - [x] Summary maps use at most three workers and preserve selected order.
  - [x] Partial/all map failures are observable and isolated.
- Verification:
  - `python -m pytest tests/tools/test_rag_tool_multi_document.py tests/tools/test_rag_tool_backend_contract.py tests/tools/test_rag_tool_graph.py -q --basetemp=.pytest-tmp-multi-doc-packet2` — PASS (29 passed).
- Scope confirmation:
  - changed only allowed files: yes
  - forbidden areas untouched: yes
- Deviations:
  - none
- Residual risks/follow-ups:
  - Assistant/UI validation remains packet 03.
- Commit:
  - `not committed`
