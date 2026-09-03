# Plan Review: product UI closure fixes

- Source plan: `docs/superpowers/plans/2026-08-09-product-ui-closure-fixes.md`
- Reviewed commit: `ef93550f6b0616815314baf2f62263b43536a17e`
- Review date: `2026-08-09`
- Verdict: `accepted-with-revisions`

## Repository evidence

- Relevant implementation:
  - `web/package.json`: direct dev dependency is Ajv `8.17.1`; `ajv-formats` is `3.0.1`.
  - `requirements.txt`: production runtime dependencies only; `httpx2` is absent.
  - `Dockerfile:23-25`: the runtime image copies and installs only `requirements.txt`.
  - `README.md:201`: local development currently installs `requirements.txt` directly.
  - `web/src/pages/LoginPage.tsx:189-192`: Login renders a default-checked remember checkbox but submits no remember field.
  - `web/src/styles/global.css:197-209`: dedicated remember-control CSS exists.
  - `api/session.py` and auth routes: session remains an HttpOnly browser-session Cookie backed by in-memory state with 12-hour sliding idle expiry.
  - `compose.yaml`: app uses `${DEPLOY_ENV_FILE:-deploy/.env}`, `${APP_BIND_ADDRESS}`, `${APP_PORT}`, and `${DEPLOY_DATA_ROOT}`; Qdrant is internal-only.
  - `deploy/smoke_test.py`: shallow smoke already proves app/Qdrant health, `/legacy/config`, data write, and local package import without an LLM call.
  - `docs/product-ui/penpot-handoff.md`: records the Penpot file/pages/components and six current reference boards, but omits Tablet Login and still claims remember rows.
- Relevant tests:
  - `web/src/auth/AuthProvider.test.tsx:216`: asserts the inert checkbox is checked.
  - `web/e2e/accessibility.spec.ts:23`: asserts the same control in the real browser.
  - `web/e2e/visual.spec.ts:13`: skips Tablet Login.
  - `web/tests/visual-acceptance-contract.test.ts`: locks the existing 15-image inventory.
  - `tests/deploy/test_product_ui_readme.py`: validates product-UI documentation prerequisites/order.
  - `tests/api/test_mounts.py:353-402`: real cold subprocess import/lifespan late-binding check; only its process ceiling may be adjusted when measured import time exceeds 45 seconds.
  - `tests/design/test_penpot_component_map.mjs`: validates live Penpot-to-code mappings against Ajv.
- Configuration/runtime facts:
  - Docker Desktop Linux Engine `29.6.2` and Compose are available; an existing healthy root-workspace deployment owns port 7860.
  - The active Penpot file is expected to be `知研 · 智能文档学习助手` (`3be9e5e1-190f-8090-8008-6ff3f3dcd54c`). Connector calls must be fresh and may not be replaced by repository handoff assumptions.
  - The TestClient warning comes from Starlette falling back to `httpx`; installing `httpx2==2.9.1` in the test environment removes that fallback without a framework rewrite.
- Existing worktree changes to preserve:
  - `docs/superpowers/plans/2026-08-09-product-ui-closure-fixes.md` (new plan created from the approved spec).
  - A partially materialized packet-01 batch appeared during packetization: `requirements-dev.txt`, dependency contract, README/Ajv edits, and the isolated `test_mounts.py` timeout adjustment. It matches accepted scope but remains unaccepted until lockfile, warning-as-error, audit, and focused regression evidence pass.

## Findings

### Blocking

- None.

### Required revisions

- Keep `httpx2` out of runtime `requirements.txt`; introduce `requirements-dev.txt` so the Docker production image does not ship a test adapter.
- Treat the exact Penpot Tablet Login and internal child IDs as live external state: discover them by a bounded read-only preflight and stop on ambiguity before mutation. Do not invent IDs in the packet.
- Extend visual acceptance from 15 to 16 images because the approved closure design explicitly adds Tablet Login.
- Run Docker validation in a unique Compose project and port, with a worktree-contained absolute data root; protect the existing 7860 stack with before/after identity evidence.
- The verified Windows cold import of `server` takes about 46.4 seconds while
  application service start/stop completes in under 0.2 seconds. Expand only
  the isolated late-binding test timeout from 45 to 90 seconds so the test
  measures lifecycle behavior rather than filesystem import-cache speed.

### Non-blocking notes

- The full Python suite is long-running (historically about 12 minutes); Task 4 must use a hard upper bound and communicate progress, but may not replace the full result with historical totals.
- Penpot export may require a cache-warm attempt within the connector's 30-second limit; every committed PNG must still come from a successful live `export_shape` write.

## Accepted scope

- Goal: resolve all five named closure items with reproducible evidence and no residual advisory/warning/inert-control/design-source/Docker gate.
- In scope: Ajv/httpx2 dev dependencies, Login remember removal, Penpot blank-board and field-fill cleanup, three Login references/baselines, isolated Docker smoke, durable closure documentation.
- Out of scope: persistent login, refresh tokens, FastAPI/Starlette major upgrades, deep LLM smoke, RAG/Memory redesign, production deployment mutation, unrelated visual redesign.
- Compatibility requirements: existing auth request/response/error shapes, Cookie/CSRF, `/legacy/`, single worker, data paths and user/document isolation remain unchanged.
- Architecture/data-isolation constraints: `UI/API → Session/ApplicationServices → Assistant → Tool → Memory/RAG/Storage`; one shared `ApplicationServices`; no client token persistence; isolated Docker and Penpot write scopes.

## Packet graph

| Packet | Depends on | Parallel-safe | Owned files | Outcome |
|---|---|---:|---|---|
| `01-dependency-closure.md` | none | no | dependency manifests, root setup docs, dependency contract | zero npm advisory and zero TestClient warning |
| `02-penpot-source-cleanup.md` | 01 | no | Penpot source, handoff contract/docs, Penpot Login references | clean source and three authoritative Login exports |
| `03-react-login-sync.md` | 01 | no | Login React/CSS/tests/E2E/browser Login baselines | inert control removed and three browser viewports accepted |
| `04-docker-release-gate.md` | 02, 03, 05 | no | closure docs/test and isolated ephemeral Docker state | verified Linux delivery plus full closure report |
| `05-docker-build-tsconfig.md` | 03 | no | Docker application-only TypeScript build contract | reproducible Linux image build without E2E sources |

## Packet readiness audit

| Packet | Goal/non-goals | Context/interfaces | Prerequisites | Change boundary | Acceptance/tests | Forbidden changes | Handoff format | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `01-dependency-closure.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `02-penpot-source-cleanup.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `03-react-login-sync.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `04-docker-release-gate.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `05-docker-build-tsconfig.md` | yes | yes | yes | yes | yes | yes | yes | yes |

All five packets are done. Packet 02 completed against the freshly connected Penpot file using bounded semantic parent/shape assertions and fixed IDs; packet 03 then passed the final source-to-browser comparison across all three viewports.

## Integration verification

- `D:\python_self_agent\venv\Scripts\python.exe -m pytest -q -W error::starlette.testclient.StarletteDeprecationWarning --basetemp=.runtime/pytest-product-ui-closure`
- `node scripts/design_tokens.mjs --check design/tokens/zhiyan.tokens.json web/src/styles/tokens.css`
- `node --test tests/design/test_design_tokens.mjs tests/design/test_penpot_component_map.mjs tests/design/test_penpot_handoff.mjs`
- `Set-Location web; npm ci; npm audit; npm audit --omit=dev; npm test; npm run typecheck; npm run lint; npm run build; npx playwright test; Set-Location ..`
- isolated Compose build/up/shallow-smoke/down with before/after 7860 evidence
- `git diff --check`

## Final integration review requirement

- Output: `docs/agent-workflow/task-packets/2026-08-09-product-ui-closure-fixes/FINAL_INTEGRATION_REVIEW.md`
- Required after: every implementation packet is `done`
- Result must be: `accepted | changes-required | blocked`
- Required checks:
  - cross-packet interfaces and exact dependency/runtime separation
  - missing requirements or duplicate implementation
  - Penpot source → reference PNG → browser snapshot traceability
  - auth/session/Cookie/CSRF and data-isolation invariants
  - Docker isolation, cleanup, and original-stack preservation
  - full combined regression and security/artifact scans

## Open decisions

- None. User approved strategy A: remove “保持登录状态” without adding persistent sessions.
