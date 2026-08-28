# Final Integration Review: QA vertical slice

- Source review: `REVIEW.md`
- Reviewed commit/worktree: `7f3b91fbf72931cb98cf0dd7fad04a3b143780a6`; clean worktree
- Review date: `2026-08-28`
- Result: `changes-required`

## Delivered packet inventory

| Packet | Status | Commit | Owned files | Verification |
|---|---|---|---|---|
| `qa-vertical-slice-01` | done | `d87fb6c` | Penpot handoff, eight source PNGs, design contract | PASS |
| `qa-vertical-slice-02` | done | `958f136` | QA schema/models/repositories | PASS |
| `qa-vertical-slice-03` | done | `1966956`, `15c8abe` | answer adapter, context, synchronous service | PASS |
| `qa-vertical-slice-04` | done | `87a9026`, `7c83b6c`, `9791035` | workers, Memory, deletion, migration, legacy consumers | PASS |
| `qa-vertical-slice-05` | done | `6d4a790` | application lifecycle and QA REST API | PASS |
| `qa-vertical-slice-06` | done | `904e3b2` | React QA data layer/workspace | PASS |
| `qa-vertical-slice-06a` | done | `632dd45` | retry history and contrast correction | PASS |
| `qa-vertical-slice-06b` | done | `10057c6` | tablet viewport correction | PASS |
| `qa-vertical-slice-06c` | done | `e8f1d2d` | responsive delete entry | PASS |
| `qa-vertical-slice-06d` | done | `2845019` | citation/composer/summary interactions | PASS |
| `qa-vertical-slice-06e` | done | `7d798cf` | current-source synchronization | PASS |
| `qa-vertical-slice-06f` | done | `e67f314` | durable history single source | PASS |
| `qa-vertical-slice-06g` | done | `0162cea` | coordination contract alignment | PASS |
| `qa-vertical-slice-06h` | done | `4fc6122` | existing browser contract alignment | PASS |
| `qa-vertical-slice-07` | done | `eb47112` | real-server E2E, visual and release evidence | PASS |

## Combined diff reviewed

- Files added: QA domain/repository/service/worker/deletion/migration modules; QA API schemas/routes; React QA data/components/page; QA tests, E2E runtime/spec and sixteen Penpot/runtime reference images; workflow artifacts.
- Files modified: additive database schema, single application composition/lifecycle, document deletion contract, Assistant/RAG request-local behavior, legacy Gradio/report consumers, React shell/document handoff, operations documentation and existing browser contracts.
- Combined size: `112 files changed, 12198 insertions, 193 deletions` from `6b1548972cc3819d45c89edf0939931d80c4d362` through `7f3b91f`.
- Pre-existing changes excluded from this review: none; the reviewed worktree was clean.

## Cross-packet interface audit

| Producer | Consumer | Contract checked | Result | Evidence |
|---|---|---|---|---|
| QA SQLite schema | `QaRepository` / `QaJobRepository` / deletion repository | composite ownership FKs, partial uniqueness, statuses and leases | pass | `app/database.py:72`, `app/database.py:99`, `app/database.py:170`, `app/database.py:226` |
| `QaRepository` | `QaService` / workers | pending/idempotent turns, conditional completion, retry linkage, Memory claims | pass | `app/qa_repository.py:212`, `app/qa_repository.py:244`, `app/qa_repository.py:278`, `app/qa_repository.py:558` |
| `RagQaAnswerEngine` / `QaContextBuilder` | `QaService` / summary worker | current-question retrieval, bounded completed-turn context and immutable source drafts | pass | `app/qa_answer_engine.py:44`, `app/qa_context.py:23`, `app/qa_service.py:184`, `app/qa_worker.py:178` |
| durable job/deletion repositories | lifecycle workers and document library | reclaim, fencing, stale-owner rejection and background runtime leases | pass | `app/qa_job_repository.py:102`, `app/qa_deletion.py:228`, `app/qa_deletion.py:456`, `app/bootstrap.py:164` |
| `QaService` | authenticated FastAPI routes | owner from session, CSRF on mutations, safe status/error DTOs | pass | `api/routes/qa.py:121`, `api/routes/qa.py:213`, `api/routes/qa.py:236`, `api/schemas/qa.py:48` |
| QA REST DTOs | React types/API/hooks | conversation/message/source/job/deletion shapes and 1500 ms resource polling | partial | `api/schemas/qa.py:48`, `web/src/features/qa/types.ts:7`, `web/src/features/qa/queries.ts:30` |
| Penpot QA boards | React workspace and browser baselines | desktop/tablet/mobile state semantics, responsive geometry and accessibility | pass | `docs/product-ui/penpot-handoff.md`, `web/src/styles/qa.css`, `web/e2e/qa.spec.ts` |

## Requirement coverage

| Accepted requirement | Implementing packet(s) | Evidence | Result |
|---|---|---|---|
| Fixed 1–10 document scope and compare validation | 02, 03, 05, 06 | repository/service/API/frontend tests | pass |
| Durable messages, immutable citations and safe retries | 02, 03, 06a, 06e | conditional repository tests plus real-server retry/reload E2E | pass |
| Restart-safe summary, Memory sync and rolling context | 04, 05 | job/worker/Memory/lifecycle tests | pass at backend; UI reconnect discovery fails |
| Conversation/document privacy deletion and late-write fencing | 04, 05, 06c | deletion/coordination/API and browser tests | pass |
| Legacy migration with no new flat-history dual write | 03, 04, 06f, 06g | migration/report/Assistant/coordination tests and diff audit | pass |
| Auth, CSRF, cross-user 404 and safe errors | 05, 07 | API tests and cross-user real-browser probe | pass |
| Responsive, accessible three-viewport workspace | 01, 06, 06a–06e, 07 | 8 Penpot exports, 8 runtime baselines, Axe and browser geometry | pass |
| Cursor-backed long-history UI and reconnectable server state | 02, 05, 06, 07 | combined source audit | fail |

## Overlap and duplication audit

- Conflicting edits: correction packets 06a–06h are sequential, recorded and do not overwrite unrelated packet ownership.
- Duplicate responsibilities/helpers: no second RAG, Session, QueryClient, QA persistence source or production fake engine exists.
- Overwritten packet work: the accidental flat-history restoration from packet 04 was explicitly corrected by 06f and proven by 06g/full regression.
- Missing central integration points: active-summary discovery and React cursor consumption are missing; corrective packet `qa-vertical-slice-08` owns both shared seams.

## Architecture and invariant audit

- Dependency direction: preserved as UI → API/service → Tool → Memory/RAG/Storage; `ApplicationServices` remains the sole lifecycle/composition root.
- Backward compatibility: direct-Python summary compatibility and `/legacy/` remain; document deletion's approved `204` → durable `202` change is integrated with all known consumers.
- Persistence/migration: additive idempotent SQLite schema, deterministic legacy imports, immutable source snapshots and single-source question history are verified.
- Data isolation: composite user ownership, session-derived user identity, cross-user not-found behavior and scoped deletion are verified.
- Failure and concurrency behavior: conditional versions, leases, retry ledgers and fences are sound in backend tests. The React page cannot rediscover an active summary after reload and currently reads only the first cursor page, so its observable recovery/history behavior is incomplete.

## Combined verification

- `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q --basetemp=.runtime/pytest-qa-final` — PASS (`1031 passed, 7 skipped`; skips are existing optional-environment cases, no QA acceptance skip).
- `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/deploy/test_qa_product_contract.py --basetemp=.runtime/pytest-qa-contract-final` — PASS (`4 passed`).
- `npm run typecheck`; `npm run lint`; `npm test -- --run`; `npm run build` — PASS (`14` Vitest files, `117` tests; `121` build modules).
- `npm run test:e2e` — PASS (`58 passed, 2` existing project-conditional skips; QA `12/12`, no QA skip).
- `node --test tests/design/test_design_tokens.mjs tests/design/test_penpot_component_map.mjs tests/design/test_penpot_handoff.mjs` — PASS (`17/17`).
- `python -m pip check`; `npm audit --audit-level=moderate` — PASS (no broken requirements; `0` vulnerabilities).
- isolated Docker Linux build/up plus `deploy/smoke_test.py --deep` — PASS (app/Qdrant health, HTTP, Qdrant write/import, document retrieval and LLM answer); cleanup left no container.
- `git diff --check` and security/DTO/design-path scans — PASS; only explicit test credential fixtures matched the broad literal scan.

## Findings

### Blocking

- None.

### Changes required

- `[P1]` `web/src/features/qa/api.ts:16` and `:20` hard-code only the first `100` conversations and first `200` messages and discard `next_cursor`. Because repository message pages start with the oldest rows (`app/qa_repository.py:152`), a conversation over 200 messages omits its newest answer/pending state, breaking source selection, busy state and truthful history. Implement recent-first message paging plus explicit older-history loading and cursor-backed conversation loading.
- `[P1]` `web/src/pages/QaPage.tsx:34` keeps `summaryJobId` only in component memory; no repository/service/API discovery contract maps a conversation to its active durable summary job. Reload loses progress/cancel, and `busy` at `web/src/pages/QaPage.tsx:64` ignores active jobs, allowing an invalid ask attempt during summary execution. Add user-scoped active-job discovery, restore it on load/switch, and disable conflicting actions while active.
- Packet 07's reload/recovery wording exceeds its browser proof: `web/e2e/qa.spec.ts` reloads completed and failed ordinary answers but never reloads an active summary or a >200-message conversation. Add focused API/frontend tests and a real-server active-summary reload scenario.

### Residual risks

- Polling remains an intentional transport boundary pending SSE/WebSocket; distributed deployment still requires shared Session, locks, task dispatch/wakeup and consistent storage.
- Penpot MCP plugin `2.17.0` against Penpot `2.17.2` remains a documented tooling-only patch warning; verified reads/writes/exports passed.

## Decision

Result is `changes-required`. Backend persistence, isolation, recovery and deletion are strong and the current short-history product loop passes all gates, but the accepted long-lived product contract is not complete while the UI can hide recent messages and cannot recover an active summary after reload. Corrective packet `qa-vertical-slice-08` must be completed and the affected plus combined gates rerun before this review can become `accepted`.

