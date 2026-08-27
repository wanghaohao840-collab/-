---
id: "qa-vertical-slice-07"
title: "Prove QA release readiness"
status: "ready"
parallel-safe: false
depends-on: ["qa-vertical-slice-06", "qa-vertical-slice-06a", "qa-vertical-slice-06b", "qa-vertical-slice-06c"]
base-commit: "6b1548972cc3819d45c89edf0939931d80c4d362"
owner: "unassigned"
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

- [ ] Real-server E2E covers the full QA product loop without route interception or model network calls.
- [ ] Recovery/race/isolation/security tests prove the accepted invariants and safe observable failures.
- [ ] Eight viewport visuals and accessibility checks pass with documented deliberate differences only.
- [ ] Python, Node, design, dependency and Docker gates pass; docs accurately mark only `/qa` complete among future product slices.

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
docker compose --env-file .runtime/qa-docker/deploy.env -p zhiyan-qa-20260825 build app qdrant
docker compose --env-file .runtime/qa-docker/deploy.env -p zhiyan-qa-20260825 up -d app qdrant
& 'D:\python_self_agent\venv\Scripts\python.exe' deploy/smoke_test.py --env-file .runtime/qa-docker/deploy.env --deep
docker compose --env-file .runtime/qa-docker/deploy.env -p zhiyan-qa-20260825 down --remove-orphans
```

Expected: build/up/deep smoke/down PASS and no container remains. Run cleanup even after failure.

## Stop conditions

Stop with a reality-conflict report if any dependency packet is not done, the deterministic seam is production-accessible, acceptance requires production edits, Docker target paths/project identity are uncertain, or a gate cannot prove its criterion.

## Implementation handoff

Replace this section with packet ID/status, complete acceptance evidence, files/interfaces, exact command outcomes/counts, scope confirmation, deviations/residual risks and commit. Once status is `done`, request the mandatory Codex final integration review; do not declare the feature complete yourself.
