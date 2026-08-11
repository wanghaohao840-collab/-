---
id: "product-ui-closure-05"
title: "Copy every referenced TypeScript project into Docker web build"
status: "done"
parallel-safe: false
depends-on: ["product-ui-closure-03"]
base-commit: "e4fe055"
owner: "Codex"
---

# Task Packet: Repair Docker TypeScript build context

## Goal

The Docker web-build stage uses a dedicated application-only TypeScript build and omits the root solution config, so Vite sees only app/node configs and the current image compiles without copying Playwright tests into the build context.

## Non-goals

- Do not change TypeScript references, application code, dependency versions, Compose, runtime images, or authentication behavior.
- Do not broaden the Docker context with `COPY web/ ./`.

## Delivery context

The isolated Linux build first failed with `TS5083` because the solution config references the omitted E2E project. Copying only that config then correctly failed with `TS18003` because its Playwright inputs were absent. Production should not copy or compile test trees, so the corrected contract adds a dedicated app/node build script while retaining the full local `build`/`typecheck` gates.

## Relevant files and current interfaces

- `web/tsconfig.json` references `tsconfig.app.json`, `tsconfig.node.json`, and `tsconfig.e2e.json`.
- `Dockerfile` selectively copies app/node configs and must run the application-only script.
- `web/package.json` owns build scripts; the normal build/typecheck scripts continue covering the full solution including E2E.
- `tests/deploy/test_image_contract.py` owns the static image-build contract.

## Prerequisites

- Packet 03 is done; Docker failure evidence is recorded in packet 04.
- Docker Desktop Linux remains available for the final acceptance rerun.

## Explicit change boundary

### Allowed files

- Modify: `Dockerfile`
- Modify: `web/package.json`
- Modify: `tests/deploy/test_image_contract.py`
- Modify: this packet and packet 04 status/handoff

### Forbidden changes

- Do not edit application, frontend, dependencies, Compose, env files, runtime data, or existing deployments.

## Interface contract

- `web/package.json` adds exact `build:app = "tsc -b tsconfig.app.json tsconfig.node.json && vite build"`.
- The Dockerfile copies only app/node config metadata, omits the root solution/E2E configs, and runs `npm run build:app`; it does not copy E2E/test source trees.
- Runtime image contents and entrypoint remain unchanged.

## Acceptance criteria

- [x] Contract test fails before the Dockerfile change and passes after it.
- [x] Dockerfile runs the application-only script with app/node config metadata and without the root solution, E2E config, or test inputs.
- [x] Focused deploy tests pass.
- [x] Isolated app/Qdrant image build succeeds without changing the existing 7860 stack.

## Test and verification commands

```powershell
.\venv\Scripts\python.exe -m pytest tests/deploy/test_image_contract.py -q -p no:cacheprovider
docker compose --env-file .runtime/closure-docker/deploy.env -p zhiyan-closure-20260809 build app qdrant
```

## Stop conditions

Stop if the solution references differ, the build fails after the exact COPY correction for another cause, or a fix would require files outside the allowed boundary.

## Implementation handoff

- Status: done
- Root cause: copying `web/tsconfig.json` into the selective Docker web-build caused Vite to follow the omitted `tsconfig.e2e.json` reference even when `tsc` used only app/node projects.
- RED evidence:
  - The first isolated build failed with `TS5083` for missing `tsconfig.e2e.json`.
  - After adding `build:app`, the real Linux build still failed in Vite with `[vite:build-html] parsing /web/tsconfig.e2e.json failed`.
  - The strengthened Docker contract then failed 1/8 because the root solution config was still copied.
- Final implementation:
  - `web/package.json` defines `build:app = "tsc -b tsconfig.app.json tsconfig.node.json && vite build"`.
  - Docker copies only `tsconfig.app.json`, `tsconfig.node.json`, and `vite.config.ts`, then runs `npm run build:app`.
  - The solution root config, E2E config, and test trees are absent from the web-build stage; local `build` and `typecheck` remain full-solution gates.
- GREEN evidence:
  - `pytest tests/deploy/test_image_contract.py`: 8 passed.
  - Local `npm run build:app`: 105 modules, pass.
  - Docker Desktop Linux build: `zhiyan-closure-20260809-app` and `zhiyan-closure-20260809-qdrant` both built successfully.
  - Existing `python_self_agent-app-1` (`0181c6abff3a`) and `python_self_agent-qdrant-1` (`b57faada1f90`) were not stopped or rebuilt.
- Commit: pending immediately after this handoff.
