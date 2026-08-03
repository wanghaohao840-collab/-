---
id: "graphrag-cross-document-entities-03"
title: "Compare/summary canonical evidence and acceptance"
status: "done"
parallel-safe: false
depends-on: ["graphrag-cross-document-entities-02"]
base-commit: "e0d11b9a775642c10a0237ad7d8e7335cb64ba71"
owner: "Codex"
---

# Task Packet: Compare/summary canonical evidence and acceptance

## Goal

Comparison and multi-document summary use bounded cross-document canonical
evidence with stable citations; local runtime configuration and documentation
match the verified Neo4j service; all acceptance gates pass.

## Non-goals

- UI, fuzzy linking, vector ranking, ordinary single-document behavior, or
  distributed graph locks.

## Delivery context

Per-document graph augmentation already exists. Shared entities belong in the
compare prompt and summary reduce phase, never in an individual summary map.

## Relevant files and current interfaces

- `hello_agents/tools/builtin/rag_tool.py:1603` — graph source construction.
- `hello_agents/tools/builtin/rag_tool.py:1886` — comparison composition.
- `hello_agents/tools/builtin/rag_tool.py:2043` — summary prefetch/map/reduce.
- `tests/tools/test_rag_tool_multi_document.py:25` — fake graph service.
- `README.md:252` — current GraphRAG lifecycle/isolation documentation.
- `.env` — local runtime Neo4j settings; secret values must not be emitted.
- Existing changes to preserve: accepted compare/summary graph augmentation,
  MMR, caching, progress/cancellation, structured output, and driver cleanup.

## Prerequisites

### Packet dependencies

- `graphrag-cross-document-entities-02` must be done.

### Repository/base state

- Service method and storage implementation match packets 01-02.

### External prerequisites

- Local Neo4j listening on `localhost:7687` with the previously supplied
  credential.

## Explicit change boundary

### Allowed files

- Modify: `.env`
- Modify: `hello_agents/tools/builtin/rag_tool.py`
- Test: `tests/tools/test_rag_tool_multi_document.py`
- Modify: `README.md`
- Modify: this plan's three packet files
- Create: this plan's `FINAL_INTEGRATION_REVIEW.md`

### Allowed behavior changes

- Add cross-document evidence to compare/reduce and source/action metadata.
- Point local Neo4j configuration at the verified local instance.

### Forbidden changes

- Do not expose credentials, change vector retrieval, put shared context in map
  prompts, change single-document output, edit UI, or touch unrelated dirty
  files.

## Interface contract

### Consumes

- Task 2's service result envelope with `data.entities`.
- Existing `off|auto|required` semantics and graph citation formatting.

### Produces

- Stable canonical `G-*` sources with `document_ids`.
- Compare prompt inclusion and structured citation allowlisting.
- Summary reduce-only inclusion, fetched before map work.

### Invariants

- Required service/query failure precedes every LLM call.
- Empty shared results are valid.
- Auto failure and off mode preserve current results.
- Per-document graph source shape remains compatible.

## Required behavior

- Call cross-document service once for selected IDs in compare/summary.
- Bound appended shared context within the existing token budget.
- Include canonical sources in final formatting and action data.
- Keep summary map/cache inputs document-local; add shared evidence to reduce
  allowed IDs and prompt only.
- Update docs and execute live/full acceptance without leaking secrets.

## Implementation guidance

Keep the new helper next to existing graph-context/source helpers. Extend
formatting to recognize `document_ids` without removing `document_id`. Use the
canonical identity fields to derive deterministic citation IDs. Test prompt
placement by inspecting every fake LLM call.

## Acceptance criteria

- [ ] `.env` app settings connect to local Neo4j without logging secrets.
- [ ] Compare and summary reduce expose correct shared context and `G-*` refs.
- [ ] Summary maps remain free of cross-document shared context.
- [ ] Off/auto/required and empty-success behavior pass before/after LLM gates.
- [ ] Real Neo4j canonical lifecycle test executes and passes.
- [ ] Focused, full, compile, dependency, and diff gates pass.
- [ ] Workflow handoffs and final integration review are complete.

## Test and verification commands

Use the exact focused, live, full, compileall, pip-check, and diff-check
commands in the source implementation plan. Expected: all configured tests
pass; the live Neo4j test is not skipped; static/dependency checks exit zero.

## Stop conditions

Stop on service contract mismatch, inability to connect to the approved local
Neo4j, overlap with new concurrent edits in owned files, secret exposure, or
required changes outside the allowed boundary.

## Implementation handoff

- Packet: `graphrag-cross-document-entities-03`
- Status: `done`
- Delivered: compare and summary reduce consume bounded canonical cross-document
  evidence with stable `G-*` citations; summary map inputs remain document-local.
- Files changed: `hello_agents/tools/builtin/rag_tool.py`,
  `tests/tools/test_rag_tool_multi_document.py`, `README.md`, local `.env`, and
  workflow handoffs.
- Verification: focused PASS (`88 passed`); live Neo4j PASS (`1 passed`, not
  skipped); repository domains PASS (`622 passed, 6 skipped`); compile/import
  and diff checks PASS.
- Scope confirmation: vector retrieval, single-document behavior, credentials,
  UI, namespace isolation, and existing GraphRAG behavior were preserved.
- Deviations: the very long full suite was executed by non-overlapping test
  domains to avoid the command timeout; together they cover the complete suite.
- Residual risks/follow-ups: exact normalized-name matching intentionally does
  not merge aliases.
- Commit: `not committed`
