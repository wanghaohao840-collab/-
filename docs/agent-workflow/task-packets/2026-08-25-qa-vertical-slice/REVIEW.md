# Plan Review: QA vertical slice

- Source plan: `docs/superpowers/plans/2026-08-25-qa-vertical-slice.md`
- Reviewed commit: `6b1548972cc3819d45c89edf0939931d80c4d362`
- Review date: `2026-08-25`
- Verdict: `accepted-with-revisions`

## Repository evidence

- Relevant implementation:
  - `app/database.py:97` — the existing idempotent SQLite initializer is the migration integration point; QA tables do not yet exist.
  - `app/runtime.py:42,129-135` — one user-scoped runtime registry already owns foreground and background leases; QA must reuse it.
  - `hello_agents/tools/builtin/rag_tool.py:405-413` — `execute_result()` returns an envelope but currently resets shared `_last_action_data`; request-local QA adaptation is required.
  - `assistants/pdf_learning_assistant.py:473-507` and `app/summary_tasks.py:26` — direct-Python in-memory summary APIs exist and need one-release compatibility, while product callers migrate to durable jobs.
  - `app/document_library.py:36,68` and `app/coordination.py:106` — document deletion is currently synchronous and coordinated; QA deletion must fence in-flight work before cleanup.
  - `hello_agents/memory/types/episodic.py:184` — exact episode-ID deletion has a failure-atomic internal seam, but no public QA-safe removal interface exists.
  - `app/bootstrap.py:24,40,82-89` — `ApplicationServices` is the single lifecycle/composition root.
  - `api/app.py:82-84`, `api/dependencies.py:30`, `api/errors.py:174` — authenticated route, dependency and common error-envelope integration points are present.
  - `web/src/App.tsx:11-24`, `web/src/layout/AppShell.tsx:11`, `web/src/pages/DocumentsPage.tsx:169` — React Router/TanStack application shell and document handoff points exist; `/qa` remains a placeholder.
  - `web/src/api/client.ts:10` and `web/src/layout/navigation.ts:8` — the shared API error and navigation contracts must be extended, not bypassed.
- Relevant tests:
  - `tests/test_p0_data_integrity.py` — existing persistence/isolation regression boundary.
  - `tests/test_assistant_user_isolation.py` — runtime and user-scope boundary.
  - `tests/memory/test_episodic_vector_cleanup.py` — episodic/vector deletion failure behavior.
  - `tests/api/test_document_routes.py` and `tests/api/test_app_lifecycle.py` — document API and lifecycle compatibility seams.
  - `tests/design/test_penpot_component_map.mjs:137` — tracked Penpot/component mapping contract.
  - `web/src/layout/AppShell.test.tsx:155` and `web/src/pages/DocumentsPage.test.tsx:135` — responsive shell and document-page regression seams.
- Configuration/runtime facts:
  - Repository Python commands must use `D:\python_self_agent\venv\Scripts\python.exe` and repository-local `--basetemp` on Windows.
  - React uses React 19, React Router 7, TanStack Query 5, Vitest and Playwright from `web/package.json`.
  - Penpot file `3be9e5e1-190f-8090-8008-6ff3f3dcd54c` is the approved design source; exported PNGs are tracked evidence, not substitutes for live board verification.
- Existing worktree changes to preserve:
  - none at reviewed commit; the plan revisions were committed before packetization.

## Findings

### Blocking

- None.

### Required revisions

- Resolved: one `client_request_id` cannot be unique on both messages in a turn. Only the user row stores it; a partial unique index enforces idempotency and `turn_id` links the assistant row.
- Resolved: rendered conversation history must not become the retrieval query. `QaAnswerRequest` now separates `question` from `conversation_context`; retrieval sees only the current question and only the answer prompt sees bounded context.
- Resolved: context truncation must remove complete turns, not positional message pairs. The accepted builder groups completed user/assistant rows by `turn_id` and drops the oldest full turn.
- Resolved: Memory synchronization requires `pending/running/completed/failed/not_required`, conditional lease ownership, expiry/reclaim and stale-owner rejection.
- Resolved: startup recovery must not fail pending summary messages backed by queued/running jobs; only orphaned synchronous pending messages become interrupted.
- Resolved: deleting `app/summary_tasks.py` and public direct-Python methods would be an unapproved breaking change. They remain deprecated for one release while product paths stop calling them.
- Resolved: a rollback copy cannot override privacy deletion. Legacy data is non-authoritative, receives no new questions, and affected entries/copies are removed by scoped deletion.

### Non-blocking notes

- REST job/status resources intentionally precede SSE; resource identifiers and state transitions must remain transport-neutral so SSE can be added without replacing persistence.
- `QA_ROUTE_ENABLED=false` is a restart-only presentation rollback. It must not disable recovery workers or resume JSON dual writes.
- Moving document deletion from synchronous `204` to durable `202` is an approved API change and must land with its React consumer and compatibility tests.

## Accepted scope

- Goal: deliver a persistent, responsive `/qa` vertical slice with fixed document scope, ordinary/joint/compare answers, durable summaries, immutable citations, safe retry/restart semantics, legacy migration and cascading deletion.
- In scope: approved Penpot boards/exports; versioned SQLite QA records; request-local RAG adapter; bounded rolling context; QA telemetry; durable summary/Memory/deletion workers; legacy migration and Gradio/report cutover; authenticated REST resources; responsive React UI; real-server E2E, accessibility, security, recovery and Docker gates.
- Out of scope: notes, insights, literature search and learning-center slices; WebSocket/SSE delivery; distributed queues; changing the RAG backend; deleting deprecated direct-Python summary compatibility; model-provider redesign.
- Compatibility requirements: preserve direct-Python summary methods for one release; preserve existing authentication, report and Gradio output shapes; never append ordinary QA to `history.json`; expose safe error codes/trace IDs only; make route disablement independent of recovery.
- Architecture/data-isolation constraints: UI → API/service → Tool → Memory/RAG/Storage; one `ApplicationServices` lifecycle; every persisted/read/mutated row is user-scoped; fixed document snapshots never silently widen; model work occurs outside DB transactions; conditional writes, leases and deletion fences win races without resurrecting deleted data.

## Packet graph

| Packet | Depends on | Parallel-safe | Owned files | Outcome |
|---|---|---:|---|---|
| `01-penpot-qa-source.md` | none | no | Penpot QA boards, handoff, reference exports, design contract | verified responsive visual authority |
| `02-qa-persistence.md` | 01 | no | QA models/schema/conversation/job repositories | isolated durable domain foundation |
| `03-qa-answer-service.md` | 02 | no | RAG adapter, context, service, telemetry | request-local synchronous QA core |
| `04-qa-durable-operations.md` | 03 | no | summary workers, Memory sync, deletion, migration, Gradio/report cutover | restart-safe long-running and privacy operations |
| `05-qa-api-lifecycle.md` | 04 | no | composition root, REST schemas/routes/errors | authenticated recoverable QA API |
| `06-qa-react-workspace.md` | 05 | no | React QA data layer, responsive workspace, route/document handoff | desktop/tablet/mobile product slice |
| `07-qa-integration-acceptance.md` | 06 | no | real-server E2E, visual/accessibility/security/Docker/docs | release evidence and packet handoffs ready for final review |

## Packet readiness audit

| Packet | Goal/non-goals | Context/interfaces | Prerequisites | Change boundary | Acceptance/tests | Forbidden changes | Handoff format | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `01-penpot-qa-source.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `02-qa-persistence.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `03-qa-answer-service.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `04-qa-durable-operations.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `05-qa-api-lifecycle.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `06-qa-react-workspace.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `07-qa-integration-acceptance.md` | yes | yes | yes | yes | yes | yes | yes | yes |

No packet may have `status: ready` while any readiness column is `no`.

## Integration verification

- `& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q --basetemp=.runtime/pytest-qa-final`
- `Push-Location web; npm run typecheck; npm run lint; npm test -- --run; npm run build; npm run test:e2e; Pop-Location`
- `node --test tests/design/test_design_tokens.mjs tests/design/test_penpot_component_map.mjs tests/design/test_penpot_handoff.mjs`
- `& 'D:\python_self_agent\venv\Scripts\python.exe' -m pip check` and `Push-Location web; npm audit --audit-level=moderate; Pop-Location`
- worktree-local `docker compose ... build/up`, `deploy/smoke_test.py --deep`, then `down --remove-orphans` as specified by packet 07.

## Final integration review requirement

- Output:
  `docs/agent-workflow/task-packets/2026-08-25-qa-vertical-slice/FINAL_INTEGRATION_REVIEW.md`
- Required after: every implementation packet is `done`
- Result must be: `accepted | changes-required | blocked`
- Required checks:
  - cross-packet interfaces
  - missing requirements
  - duplicate or overlapping implementation
  - central integration points
  - architecture, compatibility, persistence, and isolation
  - combined regression verification

## Open decisions

- None.
