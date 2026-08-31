---
id: "notes-vertical-slice-06"
title: "完成 Notes 产品发布验收"
status: "blocked"
parallel-safe: false
depends-on: ["notes-vertical-slice-01", "notes-vertical-slice-02", "notes-vertical-slice-03", "notes-vertical-slice-04", "notes-vertical-slice-05"]
base-commit: "8ac2775dc2cb0563095f0fad5b4a83abcbf52fb9"
owner: "Codex /root/notes_packet_06"
---

# Task Packet: 完成 Notes 产品发布验收

## Goal

用真实 FastAPI/React、三档 Playwright、Penpot 绑定、完整回归、依赖审计和 Docker Linux smoke 证明 Notes 垂直切片可发布，并更新产品交接文档；不借此增加产品功能。

## Non-goals

- 不新增未批准功能，不重构已通过的领域/API/UI。
- 不使用 route interception、生产可选 fake engine、浏览器截图替代 Penpot 或 skip 绕过失败。
- 不提交 `.env`、运行数据库、上传文件、Memory、报告、trace 或失败截图。

## Delivery context

前五包分别交付设计、领域、API、React 和跨入口。本包只负责真实组合验证、必要的局部验收修正及文档证据。其完成后仍需 Codex 对 combined diff 做独立最终集成评审。

## Relevant files and current interfaces

- `web/e2e/qa-runtime.py` — deterministic real-server adapter pattern isolated to E2E executable.
- `web/e2e/python-runtime.ts` — project venv resolution and environment sanitization.
- `web/e2e/fixtures.ts` — app process lifecycle/fixture extension seam.
- `web/e2e/qa.spec.ts` — real document import/QA/screenshot/Axe pattern.
- `web/playwright.config.ts` — workers=1 and desktop/tablet/mobile projects; snapshots use `<spec>-snapshots`.
- `deploy/smoke_test.py:295-322` — supported `--env-file` Docker health/smoke command.
- `docs/product-ui/penpot-handoff.md:207-221` — design/runtime evidence distinction.
- All completed packet handoffs are required evidence; existing changes to preserve are every dependency output and review artifact.

## Prerequisites

### Packet dependencies

- All packets `notes-vertical-slice-01` through `05` must be `done` with commits and passing handoffs.

### Repository/base state

- Base plan commit: `8ac2775dc2cb0563095f0fad5b4a83abcbf52fb9`; start from the integrated dependency commits.
- Working tree must contain no unexplained changes.

### External prerequisites

- Connected Penpot source for final fresh-read confirmation.
- npm dependencies installed, Chromium available for Playwright.
- Running Docker Linux daemon for the final gate.

## Explicit change boundary

### Allowed files

- Modify: `docs/agent-workflow/task-packets/2026-08-30-notes-vertical-slice/06-notes-release-acceptance.md` (status and handoff only)
- Create: `web/e2e/notes-runtime.py`
- Create: `web/e2e/notes.spec.ts`
- Modify: `web/e2e/fixtures.ts`
- Create/Update: `web/e2e/notes.spec.ts-snapshots/notes-*.png`
- Modify: `docs/product-ui/penpot-handoff.md`
- Modify: `docs/product-ui/penpot-component-map.json`
- Modify: `docs/product-ui/README.md`
- Modify: `README.md`
- Modify: `tests/deploy/test_notes_product_contract.py`
- Test-scoped corrective edits: only files owned by packets 01–05, and only when required to make an already-approved acceptance criterion pass; record every such edit in handoff.

### Allowed behavior changes

- Add E2E-only deterministic runtime and acceptance tests.
- Correct narrow integration/visual defects within the approved contract.
- Update design/product/deployment documentation with observed evidence.

### Forbidden changes

- No scope expansion, new API/DB format, production fake, skipped Notes scenario or unreviewed snapshot overwrite.
- No credentials/runtime artifacts or broad cleanup.
- Do not weaken assertions, audits, user isolation, tombstone/version, deletion fence, sanitization or Docker gates to obtain green output.

## Interface contract

### Consumes

- Every produced interface/invariant and implementation handoff from packets 01–05.
- Existing deterministic QA adapter seam through `ApplicationServices(qa_answer_engine=...)`.

### Produces

- Real-server `notes-runtime.py`, Notes E2E, four representative snapshots, Axe/overflow/touch assertions.
- Final design-to-code mapping/handoff and product/deployment documentation.
- Exact focused/full/frontend/design/audit/Docker verification evidence for final Codex review.

### Invariants

- E2E data root is per-run temporary and authenticated; no endpoint mocks.
- Production cannot select the deterministic test adapter.
- Snapshot changes require comparison to packet 01 direct Penpot exports and documented differences.
- Docker failure/daemon absence blocks delivery rather than becoming a waiver.

## Required behavior

- Real flow: register/login, create Markdown Note, preview, reload, versioned edit, search/filter/page.
- QA completed answer/citation opens draft, explicit save persists server-resolved source.
- Delete source document, wait durable deletion, Note remains with scrubbed “来源已删除”.
- Two browser contexts produce real 409 and preserve stale local draft.
- Failed projection is non-blocking and explicit retry converges.
- Clear confirmation empties active list while API/DB invariants prove tombstones.
- Desktop/tablet/mobile have no unexpected horizontal page overflow, serious/critical Axe violations or undersized mobile primary action.

## Implementation guidance

Reuse QA E2E runtime patterns and one real server process. Keep deterministic engine only in `notes-runtime.py`. Create expected screenshots intentionally after behavior is stable, inspect against direct Penpot exports at original size, and never use automatic snapshot update as review. Run focused gates before full/Docker gates; preserve failing evidence until fixed.

## Acceptance criteria

- [ ] All real-server Notes scenarios pass with no endpoint interception or new skip.
- [ ] Four representative snapshots are inspected against Penpot and mapping/handoff are fresh.
- [ ] Focused/full Python, Vitest, typecheck, lint, build, Playwright, design, `pip check` and moderate npm audit pass.
- [ ] Docker Linux build/up/health/smoke/down passes with one application worker.
- [ ] Repository contains no sensitive/runtime artifacts and `git diff --check` is silent.
- [ ] All dependency criteria remain covered; no central interface was implemented twice or bypassed.

## Test and verification commands

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_note_models.py tests/test_note_repository.py tests/test_note_migration.py tests/test_note_projection.py tests/test_note_service.py tests/test_note_source_deletion.py tests/api/test_note_routes.py tests/integration/test_note_legacy_cutover.py tests/deploy/test_notes_product_contract.py --basetemp=.runtime/pytest-notes-focused
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q --basetemp=.runtime/pytest-notes-full
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pip check
Set-Location web
npm test -- --run
npm run typecheck
npm run lint
npm run build
npx playwright test e2e/notes.spec.ts --workers=1
npm audit --audit-level=moderate
Set-Location ..
node --test tests/design/test_penpot_handoff.mjs tests/design/test_penpot_component_map.mjs
docker compose config
if (-not (Test-Path -LiteralPath deploy/.env)) { Copy-Item -LiteralPath deploy/.env.example -Destination deploy/.env }
docker compose --env-file deploy/.env up --build -d
& 'D:\python_self_agent\venv\Scripts\python.exe' deploy/smoke_test.py --env-file deploy/.env
docker compose --env-file deploy/.env down
git status --short
git diff --check
```

Expected: every gate PASS; no unreviewed snapshot update; Docker services are stopped afterward.

## Stop conditions

Stop with a reality-conflict report if any prerequisite packet is not done, handoff evidence is missing, Penpot fresh read or Docker daemon is unavailable, an accepted interface differs, a fix requires scope expansion, or verification cannot prove the criterion.

## Implementation handoff

### Reality-conflict report

- Packet: `notes-vertical-slice-06`
- Status: blocked
- Expected by packet:
  - A connected Penpot source must support a final fresh read of file `3be9e5e1-190f-8090-8008-6ff3f3dcd54c`, whose current checked-in authority is revision `152` with the fifteen Notes board IDs recorded in `docs/product-ui/penpot-handoff.md`.
  - A running Docker Linux daemon must support the required compose build, health and smoke gate.
- Observed in repository/runtime:
  - `Test-NetConnection 127.0.0.1 -Port 4401 -InformationLevel Quiet` returned `False`; no listener was reported on ports `4400`, `4401` or `4402`; `Invoke-WebRequest http://127.0.0.1:4401/mcp` failed because the target actively refused the connection.
  - `docker info --format '{{.OSType}}|{{.ServerVersion}}'` failed against `npipe:////./pipe/dockerDesktopLinuxEngine` because the named pipe does not exist.
  - Dependency Packets 01–05 are `done`; integrated HEAD is `99f83fe94a79483d174a29e78e3c7b1d2a30ef75`. The only pre-existing worktree changes are the controller-owned `.superpowers/sdd/progress.md` and untracked `REVIEW.md`, both preserved unchanged.
- Impact:
  - The mandatory Penpot fresh-read/original-size comparison and Docker Linux build/up/health/smoke/down acceptance gates cannot be executed. Continuing would create unverified snapshots and incomplete release evidence, contrary to the packet invariants and stop conditions.
- Work completed before pause:
  - Read the repository context, workflow, Packet 06 brief and dependency handoffs; verified the isolated worktree, dependency completion and integrated HEAD.
  - Changed only this packet from `ready` to `in_progress`, then to `blocked`; no product, test, E2E, snapshot, design-map or release-document file was changed.
  - Performed only the prerequisite probes listed above. No acceptance command was represented as passing.
- Recommended resolution:
  - Restore the official local Penpot MCP bridge at `http://127.0.0.1:4401/mcp` with the target Penpot file/plugin connected, and start Docker Desktop with the Linux engine. Then resume Packet 06 without changing its acceptance criteria.
- Decision required:
  - Can the connected Penpot MCP bridge and Docker Linux daemon be restored so Packet 06 can resume its required real-server, visual and container gates?

### Implementation handoff

- Status: blocked
- Files changed:
  - `docs/agent-workflow/task-packets/2026-08-30-notes-vertical-slice/06-notes-release-acceptance.md`
- Acceptance criteria:
  - [ ] Real-server Notes scenarios — not started because external prerequisites failed before implementation.
  - [ ] Four representative snapshots and fresh Penpot mapping — blocked by unavailable Penpot MCP bridge.
  - [ ] Focused/full/frontend/design/dependency gates — not started after the mandatory stop condition.
  - [ ] Docker Linux build/up/health/smoke/down — blocked by unavailable Linux daemon.
  - [x] Repository/controller-owned state remained untouched; no runtime artifact, secret, snapshot, trace or failure screenshot was created.
  - [ ] Dependency criteria integration proof — not started after the mandatory stop condition.
- Verification:
  - `git rev-parse HEAD` — PASS (`99f83fe94a79483d174a29e78e3c7b1d2a30ef75`).
  - Packet 01–05 front matter/progress handoffs — PASS (all five dependencies recorded `done`).
  - `Test-NetConnection 127.0.0.1 -Port 4401 -InformationLevel Quiet` — FAIL (`False`).
  - `Get-NetTCPConnection -LocalPort 4400,4401,4402 -ErrorAction SilentlyContinue` — FAIL (no listeners returned).
  - `Invoke-WebRequest -Uri 'http://127.0.0.1:4401/mcp' -Method Get -TimeoutSec 5` — FAIL (connection actively refused).
  - `docker info --format '{{.OSType}}|{{.ServerVersion}}'` — FAIL (`dockerDesktopLinuxEngine` named pipe missing).
- Penpot evidence:
  - Fresh-read revision: unavailable; checked-in dependency authority remains revision `152` only and was not presented as fresh evidence.
  - Board IDs: the fifteen Packet 01 IDs remain recorded in `docs/product-ui/penpot-handoff.md`; none was re-read in this blocked attempt.
  - Screenshot comparison: not performed; no runtime snapshot was created or updated.
- Docker evidence:
  - Linux daemon unavailable before compose execution; no container was started, so no cleanup was necessary.
- Corrective edits:
  - None.
- Deviations:
  - None; the packet stop condition was followed without waiving or weakening a gate.
- Residual risks:
  - All Packet 06 implementation and release-acceptance work remains outstanding until both external prerequisites are restored.
- Commit:
  - To be recorded in the final blocked handoff response; the packet cannot embed its own commit hash without a self-referential follow-up commit.
