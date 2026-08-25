---
id: "document-library-vertical-slice-08"
title: "Track the multi-document golden evaluation fixture"
status: "ready"
parallel-safe: true
depends-on: []
base-commit: "14a990ed6e5260760411b2d7fad0c2ead7dda342"
owner: "unassigned"
---

# Corrective Task Packet: Track the multi-document golden evaluation fixture

## Goal

Make the four-case multi-document QA golden test runnable from every clean clone and Git worktree by versioning its required JSON fixture.

## Non-goals

- Do not change RAG behavior, evaluator logic, expected modes or answer requirements.
- Do not unignore general runtime data, uploads, documents or generated evaluation output.

## Delivery context

`.gitignore` currently ignores every directory named `data/`. The golden test reads `evals/data/multi_document_qa.json`, which exists only as an ignored file in the main checkout and is absent from feature worktrees, causing a deterministic `FileNotFoundError`.

## Relevant files and current interfaces

- `.gitignore:22` — broad `data/` runtime exclusion.
- `tests/evals/test_multi_document_qa_golden.py:39` — fixed repository-relative fixture path.
- `evals/multi_document_qa.py:31` — JSON loader/validator.
- Required fixture contains exactly `joint_scope`, `comparison_structure`, `summary_coverage`, and `missing_document_context` cases with the current query/mode/document/marker/minimum-call fields.
- Existing changes to preserve: all completed feature commits and corrective review artifacts.

## Prerequisites

### Packet dependencies

- none.

### Repository/base state

- Base commit: `14a990ed6e5260760411b2d7fad0c2ead7dda342`.
- The main checkout's ignored source fixture is `D:\python_self_agent\evals\data\multi_document_qa.json`; its four cases are the authoritative current test data.

### External prerequisites

- Project venv; no network/service.

## Explicit change boundary

### Allowed files

- Modify: `.gitignore`
- Create: `evals/data/multi_document_qa.json`
- Modify: this packet for handoff.

### Allowed behavior changes

- Add the narrow ignore exceptions required to track only this one evaluation fixture.

### Forbidden changes

- Do not unignore any other `data/` content or modify Python/evaluator/test logic.
- Do not add user documents, secrets, model output or generated traces.

## Interface contract

### Consumes

- `load_cases(path)` current four-case JSON schema.

### Produces

- A tracked UTF-8 JSON array with the four exact current case IDs and fields.

### Invariants

- General `data/` directories remain ignored.
- Fixture contains synthetic IDs/queries only and no personal or secret data.

## Required behavior

- Add ordered exceptions for `evals/data/` and only `evals/data/multi_document_qa.json` after the broad ignore rule.
- Preserve the current four-case content byte-for-byte or semantically identical with valid JSON formatting.
- A fresh worktree/clone must receive the fixture from Git.

## Implementation guidance

Copy the authoritative four-case JSON into the owned path and verify `git check-ignore` no longer matches that file while an unrelated `data/` path remains ignored.

## Acceptance criteria

- [ ] The golden evaluation test passes in this worktree.
- [ ] Git sees the fixture as addable/tracked, not ignored.
- [ ] Unrelated `data/` content remains ignored.
- [ ] No evaluator or product code changes.

## Test and verification commands

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/evals/test_multi_document_qa_golden.py --basetemp=.runtime/pytest-golden-fixture
git check-ignore -q evals/data/multi_document_qa.json; if ($LASTEXITCODE -eq 0) { exit 1 }
git diff --check
```

Expected: test passes, fixture is not ignored, diff check passes.

## Stop conditions

Stop if the main fixture differs from the four documented case IDs, contains sensitive data, or tracking requires unignoring broader runtime data.

## Implementation handoff

Replace with the workflow handoff template, including fixture provenance, focused result, scope and commit.
