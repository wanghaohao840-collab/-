---
id: "product-ui-closure-04"
title: "Verify Docker delivery and close product UI"
status: "done"
parallel-safe: false
depends-on: ["product-ui-closure-02", "product-ui-closure-03", "product-ui-closure-05"]
base-commit: "ef93550f6b0616815314baf2f62263b43536a17e"
owner: "Codex"
---

# Task Packet: Verify Docker delivery and close product UI

## Goal

The current worktree image passes an isolated Docker Desktop Linux build/runtime/shallow smoke without disturbing the existing 7860 deployment, the full repository acceptance suite passes, and a tracked closure report makes every result reproducible.

## Non-goals

- Do not deploy to production, push images, use real LLM credentials, run deep smoke, enable Neo4j, or mutate the existing 7860 stack.
- Do not change Docker/application code unless validation exposes a genuine defect; report that as a reality conflict first.
- Do not erase broad runtime/workspace directories.

## Delivery context

Docker Desktop Linux Engine is now available, but the branch has not yet proved the current image. An existing root-workspace app/Qdrant deployment owns port 7860 and must remain untouched. Compose already supports an alternate env file, port, and data root; the existing shallow smoke covers service health, legacy config, Qdrant, writeability, and local import without LLM cost.

## Relevant files and current interfaces

- `compose.yaml` — app/qdrant services; reads `DEPLOY_ENV_FILE`, `APP_BIND_ADDRESS`, `APP_PORT`, `DEPLOY_DATA_ROOT`.
- `Dockerfile` — multi-stage web build, Python runtime, non-root app user, healthcheck, single Uvicorn worker entrypoint.
- `deploy/.env.example` — canonical env keys.
- `deploy/smoke_test.py` — `--env-file` shallow smoke; `--deep` is forbidden here.
- `deploy/README.md` — deployment operator flow.
- `tests/deploy/test_product_ui_readme.py` — documentation contract to extend.
- Existing containers: `python_self_agent-app-1` and `python_self_agent-qdrant-1` currently healthy on the root project; exact IDs must be freshly captured.
- Existing changes to preserve: packets 01-03 commits and workflow handoffs.

## Prerequisites

### Packet dependencies

- `product-ui-closure-03` must be `done`.
- `product-ui-closure-05` must be `done`.
- `product-ui-closure-02` must be `done` before the final design-source comparison and integration review.

### Repository/base state

- Worktree clean except packet status/handoff edits.
- `deploy/.env` is not required; create only an ignored isolated env file.

### External prerequisites

- Docker context `desktop-linux`, Linux daemon and Compose available.
- Port `127.0.0.1:17860` free.
- Exact project venv and Playwright browser available for full acceptance.

## Explicit change boundary

### Allowed files/state

- Create: `docs/product-ui/closure-report-2026-08-09.md`
- Modify: `docs/product-ui/README.md`
- Modify: `tests/deploy/test_product_ui_readme.py`
- Ephemeral: `.runtime/closure-docker/**`
- Ephemeral Docker project only: `zhiyan-closure-20260809`

### Allowed behavior changes

- Documentation/test contract only. Runtime validation may create/remove the unique isolated project and its verified data.

### Forbidden changes

- Do not edit app code, dependency manifests, Compose/Dockerfiles/scripts, Penpot, visual baselines, real `.env`, existing containers/networks/volumes, or external services.
- Never run unscoped `docker compose down`, `down --volumes`, deep smoke, or cleanup against the worktree root.

## Interface contract

### Consumes

- Current Dockerfile/Compose/env/smoke contract unchanged.
- Completed packet evidence and current repository state.

### Produces

- A reproducible tracked closure report and product-UI README link/order contract.
- Runtime evidence for current commit image, isolated project cleanup, and original-stack preservation.

### Invariants

- Existing 7860 container IDs/images/health/ports are unchanged before/after.
- Validation uses one app container/one Uvicorn worker and no real credentials.
- All temporary roots are inside the worktree and all processes/resources are cleaned.

## Required behavior

- Unique project name `zhiyan-closure-20260809`, endpoint `127.0.0.1:17860`, absolute worktree-contained data root.
- Build app/qdrant from current worktree; both healthy; non-root app; Qdrant ready; SPA history; `/legacy` 307 to `/legacy/`; `/legacy/config`; writable volume; correct local import; shallow smoke exit 0.
- Cleanup exact project/data only and prove 7860 stack unchanged.
- Full Python/design/frontend/Playwright/unified-smoke/scans pass and all exact totals are recorded.

## Implementation guidance

1. Add a RED doc test for the closure report link and exact isolated workflow.
2. Snapshot Docker version/context and original stack IDs/images/health/ports.
3. Validate 17860 is free. Create `.runtime/closure-docker/deploy.env` from the example using placeholder-invalid LLM values and an absolute data root.
4. Set both `COMPOSE_PROJECT_NAME` and `-p zhiyan-closure-20260809` on every Compose command.
5. `build app qdrant`, `up -d app qdrant`, wait for healthy, and run all packet checks including shallow smoke only.
6. Use a `finally` cleanup path: exact project `down --remove-orphans`, verify labeled resources absent, boundary-check then delete exact runtime root.
7. Repeat original-stack snapshot and fail on any identity/health/port change.
8. Run complete current regressions; do not reuse prior totals.
9. Write the report and README, run the documentation test, stage only allowed files.

## Acceptance criteria

- [x] Documentation test is RED before report/link and GREEN after.
- [x] Current images build and isolated app/Qdrant become healthy on 17860.
- [x] Non-root, single-worker, health/SPA/legacy/Qdrant/write/import/shallow-smoke checks pass.
- [x] Unique project and isolated data are removed; no residual container/network/process/runtime root remains.
- [x] Existing 7860 stack identity, images, health, and port bindings are unchanged.
- [x] Full Python with Starlette warning-as-error, design gates, zero npm audits, frontend, Playwright, unified smoke, and safety scans pass.
- [x] Closure report contains exact commands/totals/evidence and explicit residual risks.

## Test and verification commands

Focused docs:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest tests/deploy/test_product_ui_readme.py tests/deploy/test_dependency_contract.py -q
```

Compose (every call must keep the exact env/project scope):

```powershell
docker compose --env-file $env:DEPLOY_ENV_FILE -p zhiyan-closure-20260809 build app qdrant
docker compose --env-file $env:DEPLOY_ENV_FILE -p zhiyan-closure-20260809 up -d app qdrant
docker compose --env-file $env:DEPLOY_ENV_FILE -p zhiyan-closure-20260809 ps
& 'D:\python_self_agent\venv\Scripts\python.exe' deploy/smoke_test.py --env-file $env:DEPLOY_ENV_FILE
docker compose --env-file $env:DEPLOY_ENV_FILE -p zhiyan-closure-20260809 down --remove-orphans
```

Full regression:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q -W error::starlette.testclient.StarletteDeprecationWarning --basetemp=.runtime/pytest-product-ui-closure
node scripts/design_tokens.mjs --check design/tokens/zhiyan.tokens.json web/src/styles/tokens.css
node --test tests/design/test_design_tokens.mjs tests/design/test_penpot_component_map.mjs tests/design/test_penpot_handoff.mjs

Set-Location web
npm ci
npm audit
npm audit --omit=dev
npm test
npm run typecheck
npm run lint
npm run build
npx playwright test
Set-Location ..

git diff --check
git status --short
```

Expected: all commands exit 0; full counts recorded; Playwright has only intentional skips; no Docker/runtime residue; original stack unchanged.

## Stop conditions

Stop and append a reality-conflict report if Docker context/daemon differs, port 17860 is occupied, the original stack cannot be safely identified, any Compose command would be unscoped, an app defect requires a forbidden file, deep smoke/credentials become necessary, cleanup target is not provably inside this worktree, or full acceptance cannot be made warning-free.

## Implementation handoff

### Final handoff

- Status: done
- Files changed:
  - `docs/product-ui/closure-report-2026-08-09.md`
  - `docs/product-ui/README.md`
  - `tests/deploy/test_product_ui_readme.py`
  - `.gitattributes`
  - `tests/deploy/test_image_contract.py`
- Acceptance evidence:
  - Docker Desktop Linux 29.6.2 / Compose 5.3.1; isolated project built and ran on `127.0.0.1:17860`.
  - app/Qdrant healthy; image default `10001:10001`, Compose runtime `1000:1000`; one Uvicorn worker; endpoint, redirect, Gradio, readiness, write and shallow-smoke checks passed.
  - Isolated containers/network/data root removed; original 7860 app/Qdrant IDs, images, health and ports unchanged.
  - Python `803 passed, 7 skipped`; design `17/17`; audits zero; Vitest `65/65`; type/lint/build pass; Playwright `28 passed, 2 skipped`.
- Deviation:
  - The first app runtime exposed a CRLF shebang defect after the image had built. Corrective commit `d65fd7d` adds a durable LF checkout contract; the rebuilt image passed every runtime check.
- Residual risks:
  - Deep external-model smoke intentionally excluded by packet scope; full Python output retains existing Neo4j driver destructor warnings, not the closed TestClient warning.
- Commit:
  - `d65fd7d` for the Linux entrypoint correction; closure documentation commit follows this handoff.

## Resolved reality-conflict report

- Packet: `product-ui-closure-04`
- Status: resolved by `product-ui-closure-05`
- Expected by packet:
  - The current Dockerfile builds the React distribution before the isolated runtime starts.
- Observed in repository:
  - `web/tsconfig.json` references `tsconfig.e2e.json`, but Dockerfile's web-build COPY omits that file.
  - Isolated build fails at `npm run build` with `TS5083: Cannot read file '/web/tsconfig.e2e.json'`.
- Impact:
  - The app image cannot be produced; runtime smoke and release acceptance cannot proceed.
- Work completed before pause:
  - Docker Desktop Linux 29.6.2/Compose 5.3.1 verified; existing 7860 container IDs recorded; 17860 free; isolated Compose config validated; Qdrant image built; no isolated containers started.
- Resolution:
  - Corrective packet `product-ui-closure-05` added the application-only TypeScript build contract. The rebuilt image and isolated runtime/shallow smoke passed.
- Decision required:
  - none; the repository evidence uniquely determines the minimal correction.
