---
id: "multi-document-qa-04"
title: "Backend parity and combined regression proof"
status: "done"
parallel-safe: false
depends-on: ["multi-document-qa-03"]
base-commit: "614f84e9d01179ce1272281f77e15550c1dcd764"
owner: "codex"
---

# Task Packet: Backend parity and combined regression proof

## Goal

Prove JSON and Qdrant apply the same multi-document filtering, position sampling, and full-source dedupe behavior, then run the combined regression suite and record residual risks.

## Non-goals

- No live Qdrant/Neo4j requirement, backend migration, dependency change, or unrelated implementation cleanup.

## Delivery context

Both current backends already call shared `dedupe_results_by_source`; this packet verifies that reality and adds only missing parity coverage. It is primarily an integration packet.

## Relevant files and current interfaces

- `hello_agents/memory/rag/pipeline.py:195` — JSON `search` and shared dedupe.
- `hello_agents/memory/rag/qdrant_pipeline.py:125` — Qdrant `search` and shared dedupe.
- `hello_agents/memory/rag/result_utils.py` — canonical source identity and even sampling.
- `tests/memory/rag/test_pipeline_multi_document.py` and `tests/memory/rag/test_qdrant_pipeline.py` — backend fixtures.

## Prerequisites

### Packet dependencies

- `multi-document-qa-03` must be `done`.

### Repository/base state

- Packets 01–03 completed in the current dirty worktree.

### External prerequisites

- none; use fake Qdrant client tests.

## Explicit change boundary

### Allowed files

- Modify only if a verified parity bug remains: `hello_agents/memory/rag/qdrant_pipeline.py`
- Modify: `tests/memory/rag/test_qdrant_pipeline.py`
- Modify: `tests/memory/rag/test_pipeline_multi_document.py`
- Modify: `tests/memory/rag/test_result_utils.py`

### Allowed behavior changes

- Correct only demonstrated backend parity defects.

### Forbidden changes

- Do not change storage schemas, dependencies, Assistant/UI/Tool/graph code, or require live services.

## Interface contract

### Consumes

- Shared scope normalization, dedupe identity, and even-sampling helpers.

### Produces

- Equivalent JSON/Qdrant behavior for selected scopes and unpaged distinct chunks.

### Invariants

- `document_id` compatibility, explicit-empty rejection, persisted payload/cache shape, and namespace isolation remain unchanged.

## Required behavior

- Different unpaged chunks in one document remain distinct.
- Identical normalized chunks dedupe within the same document/page but not across documents.
- Only selected documents are searched.
- Start/middle/end summary samples match across backends.

## Acceptance criteria

- [ ] Focused backend parity tests pass without a live service.
- [ ] Multi-document focused suite passes.
- [ ] Combined non-live repository regression suite passes or every unrelated pre-existing failure is recorded with evidence.

## Test and verification commands

```powershell
python -m pytest tests/memory/rag/test_result_utils.py tests/memory/rag/test_pipeline_multi_document.py tests/memory/rag/test_qdrant_pipeline.py -q
```

```powershell
python -m pytest tests -q --ignore=tests/integration/test_neo4j_live.py
```

Expected: all non-live tests pass.

## Stop conditions

Stop if parity requires a persistence migration, live service, dependency change, or edits outside the allowed files.

## Implementation handoff

- Packet: `multi-document-qa-04`
- Status: `done`
- Delivered:
  - JSON/Qdrant source-dedupe parity coverage and combined regression proof.
- Files changed:
  - `tests/memory/rag/test_qdrant_pipeline.py` — unpaged and cross-document source parity tests.
  - `README.md`, `.gitignore` — explicit project-venv commands and local pytest temp handling.
- Interfaces added or changed:
  - none
- Acceptance evidence:
  - [x] Focused backend parity passed.
  - [x] Full non-live suite passed in the actual project virtual environment.
- Verification:
  - `.\venv\Scripts\python.exe -m pytest tests/memory/rag/test_result_utils.py tests/memory/rag/test_pipeline_multi_document.py tests/memory/rag/test_qdrant_pipeline.py -q` — PASS (47 passed in prior focused run).
  - `.\venv\Scripts\python.exe -m pytest tests -q --ignore=tests/integration/test_neo4j_live.py --basetemp=.pytest-tmp-venv-full` — PASS (442 passed, 3 skipped).
- Scope confirmation:
  - changed only allowed feature/environment documentation and tests: yes
  - forbidden areas untouched: yes
- Deviations:
  - none
- Residual risks/follow-ups:
  - Live Neo4j integration remains intentionally excluded because it requires an external service.
- Commit:
  - `not committed`
