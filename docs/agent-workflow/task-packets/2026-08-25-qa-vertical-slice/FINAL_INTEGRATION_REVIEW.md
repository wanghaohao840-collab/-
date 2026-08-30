# Final Integration Review: QA vertical slice

- Source review: `REVIEW.md`
- Reviewed implementation commit: `46ae385e3c5aa406bf6cee8675e732366d72f11d`; controller-owned `.superpowers/sdd/progress.md` remains unstaged and excluded
- Review date: `2026-08-30`
- Result: `changes-required`
- Re-review status: `pending final independent re-review`

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
| `qa-vertical-slice-08` | done | `af2409b`, `94eb558`, `e2ab51b`, `17e23ad`, `8ab4da8`, `ce98906`, `628992e`, `81eb092` | recent paging, active discovery, bounded UI consumption, reload recovery and acceptance evidence | PASS |
| `qa-vertical-slice-09` | done | `6b0494b`, `40ec2b1`, `46ae385` | atomic creation/deletion ordering, live lease recovery, replay-safe document removal/clear-all and regressions | PASS; independent re-review pending |

## Combined diff reviewed

- Files added: QA domain/repository/service/worker/deletion/migration modules; QA API schemas/routes; React QA data/components/page; QA tests, E2E runtime/spec and sixteen Penpot/runtime reference images; workflow artifacts.
- Files modified: additive database schema, single application composition/lifecycle, document deletion contract, Assistant/RAG request-local behavior, legacy Gradio/report consumers, React shell/document handoff, recent-page and active-summary API/frontend seams, E2E recovery evidence, product documentation and one explicitly approved truthful-busy visual baseline.
- Packet 09 implementation adds `13 files changed, 1447 insertions, 167 deletions` from `c0dc5a8` through `46ae385`; it changes only six backend implementation files and seven Python test files.
- Combined implementation size: `116 files changed, 16092 insertions, 214 deletions` from `6b1548972cc3819d45c89edf0939931d80c4d362` through `46ae385` (before this workflow-document update).
- Pre-existing changes excluded from this review: controller-owned `.superpowers/sdd/progress.md`; it was not staged, reverted or included in packet 09.

## Cross-packet interface audit

| Producer | Consumer | Contract checked | Result | Evidence |
|---|---|---|---|---|
| QA SQLite schema | `QaRepository` / `QaJobRepository` / deletion repository | composite ownership FKs, partial uniqueness, statuses and leases | pass | `app/database.py:72`, `app/database.py:99`, `app/database.py:170`, `app/database.py:226` |
| `QaRepository` | `QaService` / workers | pending/idempotent turns, conditional completion, retry linkage, Memory claims | pass | `app/qa_repository.py:212`, `app/qa_repository.py:244`, `app/qa_repository.py:278`, `app/qa_repository.py:558` |
| `RagQaAnswerEngine` / `QaContextBuilder` | `QaService` / summary worker | current-question retrieval, bounded completed-turn context and immutable source drafts | pass | `app/qa_answer_engine.py:44`, `app/qa_context.py:23`, `app/qa_service.py:184`, `app/qa_worker.py:178` |
| durable job/deletion repositories | lifecycle workers and document library | reclaim, fencing, stale-owner rejection and background runtime leases | pass | `app/qa_job_repository.py:102`, `app/qa_deletion.py:228`, `app/qa_deletion.py:456`, `app/bootstrap.py:164` |
| document projection / creation transaction | document deletion fence and payload snapshot | runtime-lock scope plus `BEGIN IMMEDIATE` make both commit orderings serial and prevent a late survivor | pass | `app/qa_service.py:95`, `app/qa_repository.py:41`, `tests/test_qa_deletion.py:206` |
| live scheduling passes | expired final/cancel-requested QA and deletion leases | in-transaction recovery terminalizes exhausted work, releases active discovery/fences, and preserves stale-owner rejection | pass | `app/qa_job_repository.py:102`, `app/qa_job_repository.py:552`, `app/qa_deletion.py:231`, `app/qa_deletion.py:298` |
| durable deletion payload | document library destructive removal | only the claimed fenced replay path accepts an already absent target; RAG/source/History ordering makes destructive replay safe while strict default not-found and ownership preflight remain | pass | `app/qa_deletion.py:722`, `app/document_library.py:113`, `assistants/pdf_learning_assistant.py:896`, `tests/test_qa_deletion.py:607` |
| direct clear-all | RAG, safe source files and History | structured/legacy RAG failure stops first; later unlink failure retains all path metadata and retry converges before History removal | pass | `assistants/pdf_learning_assistant.py:953`, `tests/test_document_library_service.py:680`, `tests/test_document_library_service.py:754` |
| `QaService` | authenticated FastAPI routes | owner from session, CSRF on mutations, safe status/error DTOs | pass | `api/routes/qa.py:121`, `api/routes/qa.py:213`, `api/routes/qa.py:236`, `api/schemas/qa.py:48` |
| QA REST DTOs | React types/API/hooks | bounded conversation/message cursors, active-job envelope and 1500 ms resource polling | pass | `api/routes/qa.py:312`, `web/src/features/qa/api.ts:21`, `web/src/features/qa/queries.ts:39` |
| Penpot QA boards | React workspace and browser baselines | desktop/tablet/mobile state semantics, responsive geometry and accessibility | pass | `docs/product-ui/penpot-handoff.md`, `web/src/styles/qa.css`, `web/e2e/qa.spec.ts` |

## Requirement coverage

| Accepted requirement | Implementing packet(s) | Evidence | Result |
|---|---|---|---|
| Fixed 1–10 document scope and compare validation | 02, 03, 05, 06 | repository/service/API/frontend tests | pass |
| Durable messages, immutable citations and safe retries | 02, 03, 06a, 06e | conditional repository tests plus real-server retry/reload E2E | pass |
| Restart-safe summary, Memory sync and rolling context | 04, 05, 08 | job/worker/Memory/lifecycle tests, page recovery tests and real-server reload/cancel E2E | pass |
| Conversation/document privacy deletion and late-write fencing | 04, 05, 06c, 09 | deletion/coordination/API/browser tests plus real two-connection commit-order tests | pass |
| Legacy migration with no new flat-history dual write | 03, 04, 06f, 06g | migration/report/Assistant/coordination tests and diff audit | pass |
| Auth, CSRF, cross-user 404 and safe errors | 05, 07 | API tests and cross-user real-browser probe | pass |
| Responsive, accessible three-viewport workspace | 01, 06, 06a–06e, 07 | 8 Penpot exports, 8 runtime baselines, Axe and browser geometry | pass |
| Cursor-backed long-history UI and reconnectable server state | 02, 05, 06, 07, 08 | 202-message repository regression, tied-cursor route tests, infinite-query tests, explicit load controls and real-server reload E2E | pass |
| Live exhausted-lease recovery and replay-safe destructive deletion | 04, 09 | repository/live-worker expiry tests, active visibility assertions and post-delete failure injection | pass |
| Default recent-page and active-summary fence edge cases | 08, 09 | omitted-limit newest-50/cursor regression and API-level safe `404` under a document fence | pass |

## Overlap and duplication audit

- Conflicting edits: correction packets 06a–06h are sequential, recorded and do not overwrite unrelated packet ownership.
- Duplicate responsibilities/helpers: no second RAG, Session, QueryClient, QA persistence source or production fake engine exists.
- Overwritten packet work: the accidental flat-history restoration from packet 04 was explicitly corrected by 06f and proven by 06g/full regression.
- Missing central integration points: none. Packet 08 integrates recent reads and reconnectability; packet 09 closes the service/runtime/database ordering boundary, each live scheduling pass, and the existing deletion replay boundary without creating a second coordination system.

## Architecture and invariant audit

- Dependency direction: preserved as UI → API/service → Tool → Memory/RAG/Storage; `ApplicationServices` remains the sole lifecycle/composition root.
- Backward compatibility: direct-Python summary compatibility and `/legacy/` remain; document deletion's approved `204` → durable `202` change is integrated with all known consumers.
- Persistence/migration: additive idempotent SQLite schema, deterministic legacy imports, immutable source snapshots and single-source question history are verified.
- Data isolation: composite user ownership, session-derived user identity, cross-user not-found behavior and scoped deletion are verified.
- Failure and concurrency behavior: conditional versions, leases, retry ledgers and fences remain sound. Packet 09 reserves SQLite writes before fence reads, keeps document projection through commit under the runtime lock, performs expiry recovery on each atomic scheduling pass, terminalizes exhausted/cancelled work, and makes the stored-fence destructive stage replay-safe. Stale owners remain rejected and terminal deletion fences no longer hide conversations indefinitely.

## Combined verification

- Packet 09 base replay (detached `c0dc5a8` with only the 11 new defect tests applied) — expected RED (`11 failed in 17.73s`); the same selection at `6b0494b` — GREEN (`11 passed in 3.39s`).
- Final clear-all audit added a structured-RAG failure regression: selected RED was `1 failed, 1 passed in 7.31s`; after the guard and replay ordering, the affected selection was GREEN at `5 passed, 33 deselected in 5.83s`.
- `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/test_qa_repository.py tests/test_qa_job_repository.py tests/test_qa_worker.py tests/test_qa_deletion.py tests/test_document_library_service.py tests/test_user_mutation_coordination.py tests/test_qa_service.py tests/api/test_qa_routes.py --basetemp=.runtime/pytest-qa-concurrency-focused-takeover` — PASS (`102 passed in 204.67s`). The brief's nonexistent `tests/test_document_library.py` was replaced by the exact nearest existing suite, `tests/test_document_library_service.py`; the existing coordination suite was added to cover direct Assistant contracts.
- `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q --basetemp=.runtime/pytest-qa-concurrency-full-takeover` — PASS (`1053 passed, 7 skipped in 1284.35s`; skips remain documented optional/project-conditional cases).
- `D:\python_self_agent\venv\Scripts\python.exe -m pip check`; `git diff --check`; packet 09 forbidden-path scan — PASS (no broken requirements, no whitespace errors, and no schema/public DTO/frontend/E2E/snapshot changes).

- `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/test_qa_repository.py tests/test_qa_job_repository.py tests/test_qa_service.py tests/api/test_qa_routes.py --basetemp=.runtime/pytest-qa-08-focused` — PASS (`42 passed`).
- `npm exec vitest run src/features/qa/api.test.ts src/features/qa/queries.test.tsx src/components/QaWorkspace/QaWorkspace.test.tsx src/pages/QaPage.test.tsx`; `npm run typecheck`; `npm run lint`; `npm run build` — PASS (`4` files, `25` tests; `121` build modules).
- `npx playwright test e2e/qa.spec.ts --workers=1` — PASS (`12 passed`, `0 skipped`; desktop/tablet/mobile reload recovery all green).
- `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q --basetemp=.runtime/pytest-qa-08-full` — PASS (`1036 passed, 7 skipped`; skips are existing optional/project-conditional cases, no QA acceptance skip).
- `npm test -- --run` — PASS (`14` Vitest files, `128` tests).
- `npm run test:e2e` — PASS (`58 passed, 2` existing project-conditional skips; QA `12/12`, no QA skip).
- `node --test tests/design/test_design_tokens.mjs tests/design/test_penpot_component_map.mjs tests/design/test_penpot_handoff.mjs` — PASS (`17/17`).
- `python -m pip check`; `npm audit --audit-level=moderate` — PASS (no broken requirements; `0` vulnerabilities).
- isolated Docker Linux build/up plus `deploy/smoke_test.py --deep` — PASS (app/Qdrant health, HTTP, Qdrant write/import, document retrieval and LLM answer); cleanup left no container.
- `git diff --check` and security/DTO/design-path scans — PASS; only explicit test credential fixtures matched the broad literal scan.

## Findings

### Blocking

- None.

### Changes required

- The packet 09 implementation has not yet received the controller's required final independent whole-range re-review. The integration result therefore remains `changes-required` even though all known implementation findings and gates are closed; do not treat this document as independent acceptance.
- The earlier packet 08 P1 findings remain closed:
  - Conversations with `202` messages start from the newest 50-row page, preserve chronological rendering, and traverse older opaque-cursor pages without overlap; the UI exposes bounded 20-conversation and 50-message load controls.
  - Active summary discovery is user/conversation scoped; after browser reload the page restores truthful progress/cancel state from the server, disables ask/summary actions, and retains the discovered job through terminal reconciliation.
- Packet 07's prior proof gap is closed by the real-server reload assertion in `web/e2e/qa.spec.ts`; it uses no route interception, browser storage or fixed browser sleep.
- The later whole-branch findings are implemented and locally closed pending that independent decision:
  - Important 1: conversation creation now acquires `BEGIN IMMEDIATE` before the fence read, and event-driven two-connection tests prove deletion-first and creation-first commit orderings. The runtime lock also spans document projection through repository commit, closing the service-level TOCTOU seam.
  - Important 2: every summary/deletion claim pass atomically recovers expired rows; live workers terminalize exhausted and cancel-requested work without restart/notification, active discovery/fences clear, and stale owners remain rejected.
  - Important 3: the claimed durable deletion replay path tolerates an already removed target without changing ordinary not-found behavior; RAG then safe source unlink then History removal preserves retry metadata, injected failure after the first destructive delete retries to completion, and direct clear-all now follows the same replay-safe order with RAG failure protection.
  - Minor coverage: omitted message limit proves newest 50 plus an older cursor, and API active-summary discovery under a document deletion fence proves the existing safe `404`.

### Residual risks

- Polling remains an intentional transport boundary pending SSE/WebSocket; distributed deployment still requires shared Session, locks, task dispatch/wakeup and consistent storage.
- The service-side document projection/commit lock is process-local. SQLite immediate transactions protect fence/creation ordering across database connections; a future multi-process document-history implementation still requires a shared coordination design.
- Exhausted deletion failures intentionally become terminal and release the active fence under the existing failure contract; operators still need explicit remediation/retry policy for any underlying external cleanup failure.
- Penpot MCP plugin `2.17.0` against Penpot `2.17.2` remains a documented tooling-only patch warning; verified reads/writes/exports passed.
- The user explicitly approved updating only `web/e2e/qa.spec.ts-snapshots/qa-summary-desktop.png` because active-summary mutual exclusion now truthfully disables conflicting controls. The full visual matrix passed and no other snapshot changed.

## Decision

Result remains `changes-required / pending final independent re-review`. Packet 09 closes the three Important findings, the clear-all replay audit gap, and two Minor coverage gaps in implementation; all focused/full/dependency/diff gates are green, but acceptance is deliberately withheld until the controller receives a clean independent review of `c0dc5a8..46ae385` and records that decision.
