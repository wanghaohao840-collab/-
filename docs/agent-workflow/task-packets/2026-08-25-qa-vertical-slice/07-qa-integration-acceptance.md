---
id: "qa-vertical-slice-07"
title: "Prove QA release readiness"
status: "done"
parallel-safe: false
depends-on: ["qa-vertical-slice-06", "qa-vertical-slice-06a", "qa-vertical-slice-06b", "qa-vertical-slice-06c", "qa-vertical-slice-06d", "qa-vertical-slice-06e", "qa-vertical-slice-06f", "qa-vertical-slice-06g", "qa-vertical-slice-06h"]
base-commit: "6b1548972cc3819d45c89edf0939931d80c4d362"
owner: "codex"
---

# Task Packet: Prove QA release readiness

## Goal

Add deterministic real-server QA E2E and operations documentation, then prove functional, visual, accessibility, isolation, failure/restart, dependency, security and Docker acceptance without production backdoors.

## Non-goals

- No feature redesign, opportunistic business-code fixes or future-route implementation.
- No network model dependency in E2E.
- No Docker data/env mutation outside worktree-local `.runtime`.

## Delivery context

Focused tests do not prove the combined producer/consumer contracts. This packet creates a test-only injected answer adapter through the approved composition seam, exercises the real FastAPI/React server, records visual baselines against packet 01, documents recovery/rollback, and supplies evidence for the mandatory final integration review.

## Relevant files and current interfaces

- `web/package.json` — exact typecheck/lint/unit/build/E2E commands and existing locked dependencies.
- `tests/deploy/test_qa_product_contract.py` — tracked design/product contract from packet 01.
- `tests/api/test_app_lifecycle.py` — actual service startup/stop seam.
- `app/bootstrap.py` — packet 05 test-only `qa_answer_engine` injection; production environment/API must not expose it.
- `deploy/smoke_test.py` and compose configuration — existing deep deployment verification.
- `docs/product-ui/README.md`, `docs/product-ui/penpot-handoff.md`, `README.md` — route/design/operations handoff surfaces.
- Existing changes to preserve: all completed prior packets.

## Prerequisites

### Packet dependencies

- `qa-vertical-slice-06` must be `done` and all prior packet handoffs must report passing focused verification.

### Repository/base state

- Base commit plus all prior packet commits/handoffs.
- The real `/qa` route, durable workers and responsive UI exist.

### External prerequisites

- Docker Linux daemon available for the Docker gate; if unavailable, mark only that gate blocked with exact daemon evidence.
- Chromium/Playwright browser dependencies installed.

## Explicit change boundary

### Allowed files

- Create: `web/e2e/qa-runtime.py`, `web/e2e/qa.spec.ts`
- Modify: `web/e2e/accessibility.spec.ts`, `web/e2e/visual.spec.ts` and shared E2E fixtures only for reusable QA setup
- Create/Update: eight `web/e2e/*qa*.png` Playwright visual baselines
- Modify: `tests/deploy/test_qa_product_contract.py`
- Modify: `docs/product-ui/README.md`, `docs/product-ui/penpot-handoff.md`, `README.md`
- Modify: deployment/contract tests only where documentation/QA route matrix is asserted

### Allowed behavior changes

- Test/operations evidence and documentation only.

### Forbidden changes

- No production app/API/service/worker/UI code, dependency/lockfile, compose manifest or deployment secret edits.
- No fake engine environment variable, API endpoint or production import path.
- Never overwrite `deploy/.env`, user data roots, existing Docker projects or global visual snapshots.
- If acceptance exposes a product defect, stop and request a corrective packet; do not patch it here.

## Interface contract

### Consumes

- Completed packets 01–06 and `ApplicationServices.create(..., qa_answer_engine=DeterministicQaAnswerEngine())` as a Python-only test seam.

### Produces

- Real-server E2E for document handoff, fixed scope, ask/idempotent reload, citations, summary progress/cancel, retry and delete.
- Failure/recovery evidence for pending sync restart, summary lease reclaim, cancel/completion, conditional conflicts, delete/in-flight answer, Memory retry and cross-user probing.
- Eight visual baselines: desktop default/summary/delete; tablet default/sources; mobile default/sources/failure.
- Operations docs for route flag, polling/recovery, backup/deletion/legacy implications and later SSE/distributed evolution.

### Invariants

- Deterministic adapter uses only test-created document IDs/names/content and cannot be selected in production.
- Logs/responses/DOM contain no raw prompts, secrets, paths, owner/Memory/lease IDs or exception strings.
- E2E uses real API/auth/storage/lifecycle and does not intercept QA responses.

## Required behavior

- Reload during/after ask/summary reconstructs state from server resources; retry does not duplicate a turn.
- Cross-user IDs remain safe not-found and concurrent operations have one conditional winner.
- Visuals have no unexpected diff/overflow at exact Penpot viewports and axe has no serious/critical violations.
- Docker deep smoke passes with worktree-local env/data; cleanup always runs.

## Implementation guidance

Start the deterministic server as a child process, wait on an explicit health endpoint, and terminate in `finally`. Keep barriers/failures in the test process. Compare screenshots to both approved state semantics and Playwright baselines. Create `.runtime/qa-docker/deploy.env` with unused host ports and a worktree-local data root; use a unique compose project name.

## Acceptance criteria

- [x] Real-server E2E covers the full QA product loop without route interception or model network calls.
- [x] Recovery/race/isolation/security tests prove the accepted invariants and safe observable failures.
- [x] Eight viewport visuals and accessibility checks pass with documented deliberate differences only.
- [x] Python, Node, design, dependency and Docker gates pass; docs accurately mark only `/qa` complete among future product slices.

## Test and verification commands

```powershell
New-Item -ItemType Directory -Force .runtime | Out-Null
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q --basetemp=.runtime/pytest-qa-final
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pip check
Push-Location web
npm run typecheck
npm run lint
npm test -- --run
npm run build
npm run test:e2e
npm audit --audit-level=moderate
Pop-Location
node --test tests/design/test_design_tokens.mjs tests/design/test_penpot_component_map.mjs tests/design/test_penpot_handoff.mjs
git diff --check
```

Expected: all gates PASS with no skipped QA acceptance test and no moderate-or-higher advisory.

Docker gate (worktree-local values only):

```powershell
docker compose --env-file .runtime/qa-docker/deploy.env -p zhiyan-qa-20260828 build app qdrant
docker compose --env-file .runtime/qa-docker/deploy.env -p zhiyan-qa-20260828 up -d app qdrant
& 'D:\python_self_agent\venv\Scripts\python.exe' deploy/smoke_test.py --env-file .runtime/qa-docker/deploy.env --deep
docker compose --env-file .runtime/qa-docker/deploy.env -p zhiyan-qa-20260828 down --remove-orphans
```

Expected: build/up/deep smoke/down PASS and no container remains. Run cleanup even after failure.

## Stop conditions

Stop with a reality-conflict report if any dependency packet is not done, the deterministic seam is production-accessible, acceptance requires production edits, Docker target paths/project identity are uncertain, or a gate cannot prove its criterion.

## Implementation handoff

Packet `qa-vertical-slice-07` is `done`.

Delivered a deterministic Python-only `QaAnswerEngine` adapter and real FastAPI/React Playwright fixture, full QA browser workflow coverage, eight reviewed responsive visual baselines, and release/recovery/evolution documentation. The adapter is supplied only through the existing `ApplicationServices.create(..., qa_answer_engine=...)` test composition seam; no environment switch, route interception, production import or model-network dependency was introduced.

Acceptance evidence on 2026-08-28:

- Python: `1031 passed, 7 skipped in 894.27s`; the seven skips are existing optional-environment cases and no QA acceptance test is skipped.
- Frontend: TypeScript and ESLint passed; Vitest reported `14` files and `117` tests passed; production build completed with `121` modules.
- Browser: full Playwright matrix reported `58 passed, 2 skipped, 0 failed`; all `12` QA tests passed across desktop, tablet and mobile. The two skips are pre-existing project-conditional cases outside QA.
- Visual/accessibility: all eight tracked QA PNG baselines were inspected at their original viewports; Axe reported no serious/critical violations, and QA tests proved no horizontal overflow plus keyboard drawer focus/escape behavior.
- Design/contracts: `17/17` Node design tests passed; the QA product documentation contract passed.
- Dependencies/security: `pip check` reported no broken requirements and `npm audit --audit-level=moderate` reported `0` vulnerabilities.
- Docker Linux: Docker Desktop server `29.6.2` built both app and Qdrant images under unique project `zhiyan-qa-20260828`; Compose reached healthy state; `deploy/smoke_test.py --deep` passed app/Qdrant health, HTTP, Qdrant data write/import, and temporary document retrieval plus LLM answer. The LLM endpoint was a host-local OpenAI-compatible deterministic process used only by the deployment harness. `down --remove-orphans` completed and `compose ps -a` returned no containers.

Runtime-only Penpot differences are documented in `docs/product-ui/penpot-handoff.md`: tablet chat height preserves the complete composer at `1024 x 768`, and mobile conversation deletion lives in the existing bottom sheet. Both retain approved state semantics and tokens.

Changed surfaces are limited to `web/e2e/qa-runtime.py`, `web/e2e/qa.spec.ts`, shared E2E setup, eight QA snapshots, the tracked QA deployment contract, and product/operations handoff documentation. No production application, API, service, worker, dependency, Compose manifest or secret was changed in this packet.

Residual constraints are intentional and documented: polling remains `1500 ms` until a later SSE/WebSocket transport upgrade; deployment remains single application replica/worker until shared Session, distributed locking, task dispatch/wakeup and consistent storage are available. The mandatory Codex final integration review is the next gate.
