---
id: "product-ui-closure-01"
title: "Close Ajv advisory and TestClient warning"
status: "ready"
parallel-safe: false
depends-on: []
base-commit: "ef93550f6b0616815314baf2f62263b43536a17e"
owner: "unassigned"
---

# Task Packet: Close dependency findings

## Goal

The frontend resolves direct Ajv `8.20.0` with zero npm audit findings, and the Python test environment uses `httpx2==2.9.1` so importing/running FastAPI `TestClient` emits no Starlette deprecation warning. The production image remains free of the test-only adapter.

## Non-goals

- Do not upgrade FastAPI, Starlette, `httpx<1`, React Router, or ESLint's nested Ajv 6.
- Do not filter or suppress warnings.
- Do not modify application behavior, auth schemas, Docker Compose, or Penpot.

## Delivery context

`ajv@8.17.1` is a direct frontend dev dependency and is affected by the remaining moderate advisory. Starlette 1.3.1 prefers the separate `httpx2` package and warns only when it falls back to `httpx`. `httpx2` is needed for tests, not production, so a new development requirements file must include the existing runtime manifest rather than polluting the Docker layer.

## Relevant files and current interfaces

- `web/package.json` — direct `devDependencies.ajv` is `8.17.1`; `ajv-formats` is `3.0.1`.
- `web/package-lock.json` — authoritative npm resolution.
- `requirements.txt` — production Python runtime dependency source; must remain unchanged.
- `Dockerfile:23-25` — copies/installs only `requirements.txt`.
- `README.md:196-205` — local venv install instructions currently use runtime requirements.
- `tests/design/test_penpot_component_map.mjs` — imports Ajv from `web/node_modules` and constrains compatibility.
- Existing changes to preserve: the uncommitted plan/review/packet files only.

## Prerequisites

### Packet dependencies

- none

### Repository/base state

- Base commit: `ef93550f6b0616815314baf2f62263b43536a17e` plus the review/packet commit.
- Project venv path: `D:\python_self_agent\venv\Scripts\python.exe`.
- Node/npm are already compatible with Vite 7.

### External prerequisites

- npm registry and Python package index access may require approved network escalation.

## Explicit change boundary

### Allowed files

- Create: `requirements-dev.txt`
- Create: `tests/deploy/test_dependency_contract.py`
- Modify: `README.md`
- Modify: `web/package.json`
- Modify: `web/package-lock.json`
- Modify: `tests/api/test_mounts.py`

### Allowed behavior changes

- Development/test installs add `httpx2==2.9.1`.
- Direct frontend Ajv resolves to `8.20.0`.
- The isolated late-binding regression allows a 90-second cold-import budget;
  its service start/stop assertions remain unchanged.

### Forbidden changes

- Do not edit `requirements.txt`, `Dockerfile`, application source, tests other than the measured cold-import timeout in `tests/api/test_mounts.py`, Penpot files, or deployment state.
- Do not add warning filters or npm overrides for unrelated transitive packages.
- Do not add production credentials or generated package directories.

## Interface contract

### Consumes

- `requirements.txt` as the unchanged runtime manifest.
- npm package/lockfile semantics and existing Node design tests.

### Produces

- `requirements-dev.txt` containing exactly an include of runtime requirements plus exact `httpx2==2.9.1`.
- Direct exact `devDependencies.ajv = "8.20.0"` and refreshed lockfile.
- A focused repository contract that prevents the test adapter from entering Docker runtime.

### Invariants

- Production Docker installs only `requirements.txt`.
- All existing API/TestClient behavior remains unchanged except disappearance of the warning.
- `ajv-formats` remains compatible and design mapping validation passes.

## Required behavior

- Local development instructions use `requirements-dev.txt`.
- `pip check`, warning-as-error TestClient import, API tests, `npm audit`, `npm audit --omit=dev`, and frontend gates all pass.
- If `httpx2` fails to eliminate the warning, stop; do not suppress it.

## Implementation guidance

1. Add the focused failing dependency contract described in the plan and prove RED.
2. Create `requirements-dev.txt` with `-r requirements.txt` and exact `httpx2==2.9.1`.
3. Update only the README's local development install command.
4. Run `npm install --save-dev --save-exact ajv@8.20.0` from `web`, then `npm ci`.
5. Install the dev requirements into the exact project venv.
6. If the real late-binding subprocess exceeds 45 seconds, raise only its `subprocess.run` ceiling to 90 seconds without changing assertions or application behavior.
7. Run focused and broader gates before staging only the allowed files.

## Acceptance criteria

- [ ] Dependency contract is RED before implementation and GREEN after it.
- [ ] `npm ls` resolves direct Ajv 8.20.0; both audits report zero vulnerabilities.
- [ ] `pip check` passes and TestClient import/API tests pass with the Starlette warning promoted to error.
- [ ] Dockerfile and runtime `requirements.txt` are unchanged.
- [ ] Frontend unit/type/lint/build and design mapping tests pass.
- [ ] The late-binding subprocess regression passes within the revised 90-second cross-platform budget.

## Test and verification commands

Run from repository root:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest tests/deploy/test_dependency_contract.py tests/api -q -W error::starlette.testclient.StarletteDeprecationWarning --basetemp=.runtime/pytest-closure-deps
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pip check
& 'D:\python_self_agent\venv\Scripts\python.exe' -W error::starlette.testclient.StarletteDeprecationWarning -c "from fastapi.testclient import TestClient; print(TestClient.__module__)"

Set-Location web
npm ls ajv ajv-formats
npm audit
npm audit --omit=dev
npm test
npm run typecheck
npm run lint
npm run build
Set-Location ..

node --test tests/design/test_penpot_component_map.mjs
```

Expected: every command exits 0; both audits report zero; warning-as-error emits no warning.

## Stop conditions

Stop and append a reality-conflict report if the base files differ, registry installation cannot complete, the warning remains with httpx2 installed, an advisory requires an unrelated major upgrade, or any required fix would leave the allowed file set.

## Implementation handoff

Replace this placeholder using the exact handoff format from `docs/agent-workflow/TASK_PACKET_TEMPLATE.md`, including command totals, resolved versions, audit results, scope confirmation, and commit hash.
