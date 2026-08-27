---
id: "qa-vertical-slice-06a"
title: "Correct QA retry history and text contrast"
status: "done"
parallel-safe: false
depends-on: ["qa-vertical-slice-06"]
base-commit: "19b2563"
owner: "codex"
---

# Task Packet: Correct QA retry history and text contrast

## Goal

Correct two product defects found by real-server Packet 07 acceptance: retrying a failed answer must retain the original user question while adding only one linked assistant attempt, and ordinary QA copy must meet WCAG AA contrast at desktop, tablet and mobile widths.

## Non-goals

- No API request/response change, route redesign, global token change or unrelated UI cleanup.
- No replacement of the durable message model or migration of existing history into another storage backend.
- No E2E fixture, screenshot, operations-documentation or deployment change; those remain owned by Packet 07.

## Delivery context

The approved QA design says a retry preserves the failed record and creates a new assistant message linked by `retry_of_message_id`. The current service calls the ordinary ask insertion path, which creates a second user row. The accepted Penpot handoff also assigns `color.text.primary` to small ordinary copy; current QA CSS uses the lower-contrast secondary token on light surfaces. Packet 07 forbids production edits, so these corrections are isolated here before acceptance resumes.

## Relevant files and current interfaces

- `app/qa_service.py:223` — `QaService.retry(...)` resolves the original user message but currently calls `create_pending_turn(...)`.
- `app/qa_repository.py:212` — `create_pending_turn(...)` owns ordinary ask idempotency and inserts a user/assistant pair.
- `app/database.py:99` — durable QA messages and request-id indexes; assistant rows intentionally do not store ask `client_request_id`.
- `app/qa_context.py:85` — completed context is grouped by `turn_id`, so a retry assistant can reuse the original turn without copying its user row.
- `web/src/styles/qa.css:4` — QA ordinary supporting copy currently uses `--color-text-secondary` and fails Axe contrast on canvas, brand and surface backgrounds.
- Existing changes to preserve: uncommitted Packet 07 E2E fixtures/specs and visual baselines.

## Prerequisites

### Packet dependencies

- `qa-vertical-slice-06` is `done` at `19b2563`.

### Repository/base state

- Base commit: `19b2563`.
- Durable QA messages, retry API and responsive QA workspace exist.

### External prerequisites

- Repository `venv`; Node dependencies already installed.

## Explicit change boundary

### Allowed files

- Modify: `app/database.py`, `app/qa_repository.py`, `app/qa_service.py`
- Modify: `web/src/styles/qa.css`
- Test: `tests/test_qa_repository.py`, `tests/test_qa_service.py`, `tests/deploy/test_qa_product_contract.py`, QA-focused frontend tests only if required
- Modify: this packet and Packet 07 dependency metadata

### Allowed behavior changes

- Add a durable retry-request ledger and repository operation that inserts only a retry assistant for an existing failed turn.
- Use the primary text token for QA supporting copy that must meet WCAG AA.

### Forbidden changes

- Do not change public HTTP shapes, ordinary ask idempotency, source isolation, deletion fencing, global design tokens, dependency manifests or Packet 07 artifacts.
- Do not add a production-selectable fake engine or expose request/lease/owner internals.

## Interface contract

### Consumes

- `QaService.retry(session_token, failed_assistant_message_id, client_request_id)`.
- `PendingTurn(user_message, assistant_message, duplicate)` and existing message `turn_id`/`retry_of_message_id` fields.

### Produces

- `QaRepository.create_pending_retry(...) -> PendingTurn`, atomically returning the original user message plus one new or idempotently existing retry assistant.
- Durable retry-request identity scoped by user and conversation, with one canonical child attempt for each failed assistant.

### Invariants

- A retry never copies the user message; its assistant reuses the original `turn_id` and links to the failed assistant.
- The same request or target is idempotent; different users cannot observe or reuse another user's IDs.
- Existing databases initialize without destructive message rewriting; deletion cascades remove retry ledger rows.
- Ordinary asks continue storing `client_request_id` only on user messages.

## Required behavior

- First retry inserts one pending assistant and a durable request mapping, then executes it.
- Repeating the request or retrying the same failed target returns the canonical assistant without a second engine execution or message.
- Invalid/non-failed/cross-user targets remain safe failures.
- QA supporting text meets at least 4.5:1 contrast on all approved light surfaces.

## Implementation guidance

Add a small `qa_retry_requests` table rather than weakening the existing message-role check. Keep its ownership and foreign keys composite/user-scoped. In one `begin immediate` transaction, validate the request, conversation, failed assistant and paired user; resolve an existing ledger or legacy linked retry; enforce the single-pending invariant; insert only the assistant with the original `turn_id`; then record the request mapping. Preserve lazy compatibility with any pre-ledger linked retry. Keep the CSS correction QA-scoped.

## Acceptance criteria

- [ ] Repository/service tests prove one user row, linked retry assistant, stable idempotency and one engine execution.
- [ ] Existing ask, context/report, deletion and user-isolation behavior remains passing.
- [ ] QA CSS uses the approved accessible semantic token without changing the global palette.
- [ ] Focused Python, frontend and contract gates pass.

## Test and verification commands

Run from repository root:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_repository.py tests/test_qa_service.py tests/deploy/test_qa_product_contract.py --basetemp=.runtime/pytest-qa-corrections
Push-Location web
npm test -- --run src/components/QaWorkspace/QaWorkspace.test.tsx src/pages/QaPage.test.tsx
npm run typecheck
npm run lint
Pop-Location
git diff --check
```

Expected: all commands pass with no skipped correction test.

## Stop conditions

Stop and report `blocked` if the correction requires public API changes, destructive history rewriting, global token changes, or Packet 07 artifact edits.

## Implementation handoff

- Packet: `qa-vertical-slice-06a`
- Status: `done`
- Delivered:
  - Retry now preserves one user question and creates one durable, linked assistant attempt; QA supporting copy uses the approved accessible primary token.
- Files changed:
  - `app/database.py` — add the user/conversation-scoped retry request ledger.
  - `app/qa_repository.py` — add atomic, idempotent assistant-only retry creation and legacy adoption.
  - `app/qa_service.py` — route retries through the dedicated repository operation.
  - `web/src/styles/qa.css` — use primary text color for ordinary QA supporting copy.
  - `tests/test_qa_repository.py`, `tests/test_qa_service.py`, `tests/deploy/test_qa_product_contract.py` — regression and semantic-token evidence.
  - `docs/agent-workflow/task-packets/2026-08-25-qa-vertical-slice/07-qa-integration-acceptance.md` — make acceptance depend on this correction.
- Interfaces added or changed:
  - Added `QaRepository.create_pending_retry(user_id, conversation_id, failed_assistant_message_id, client_request_id, *, now=None) -> PendingTurn`.
  - `QaRepository.create_pending_turn(...)` is again ordinary-ask-only; its temporary retry keyword is removed.
- Acceptance evidence:
  - [x] One user row and two assistant attempts after retry; retry target/request are idempotent.
  - [x] Retry reuses the original turn/question and completed report output remains one logical turn.
  - [x] Retry ledger is user/conversation scoped and cascades on conversation deletion.
  - [x] QA supporting copy is bound to `--color-text-primary` without changing global tokens.
- Verification:
  - `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/test_qa_repository.py tests/test_qa_service.py tests/deploy/test_qa_product_contract.py --basetemp=.runtime/pytest-qa-corrections` — PASS, 20 passed.
  - `npm test -- --run src/components/QaWorkspace/QaWorkspace.test.tsx src/pages/QaPage.test.tsx` — PASS, 14 files / 114 tests.
  - `npm run typecheck` — PASS.
  - `npm run lint` — PASS.
  - `git diff --check` — PASS (line-ending notices only).
- Scope confirmation:
  - changed only allowed files: yes
  - forbidden areas untouched: yes
- Deviations:
  - none
- Residual risks/follow-ups:
  - Packet 07 must rerun real-server Axe and retry lifecycle acceptance and regenerate its baselines.
- Commit:
  - `632dd45`
