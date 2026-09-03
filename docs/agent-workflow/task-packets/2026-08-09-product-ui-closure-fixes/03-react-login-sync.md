---
id: "product-ui-closure-03"
title: "Remove inert Login persistence control"
status: "done"
parallel-safe: false
depends-on: ["product-ui-closure-01"]
base-commit: "ef93550f6b0616815314baf2f62263b43536a17e"
owner: "Codex"
---

# Task Packet: Remove inert Login persistence control

## Goal

React Login no longer promises persistent login, continues submitting only username/password, and passes unit/accessibility/visual acceptance across desktop/tablet/mobile. Final source-to-browser comparison remains an integration dependency on packet 02.

## Non-goals

- Do not implement persistent sessions, refresh tokens, storage, Cookie expiry, or new auth API fields.
- Do not change Register, session-expired, logout, CSRF, routing, navigation, or non-Login baselines.
- Do not edit Penpot source or reference exports.

## Delivery context

The checkbox is default-checked but inert; its state never reaches the API. The user chose strategy A: remove it while keeping the existing session Cookie and 12-hour in-memory sliding expiry. The approved closure design is sufficient for the browser change; packet 02 independently updates the live design source, and final integration compares its exports with these baselines.

## Relevant files and current interfaces

- `web/src/pages/LoginPage.tsx:189-192` — `.remember-control` JSX.
- `web/src/styles/global.css:197-209` — dedicated remember styles.
- `web/src/auth/AuthProvider.test.tsx:216` — current checked assertion and real login request test seam.
- `web/e2e/accessibility.spec.ts:23` — current browser checked assertion.
- `web/e2e/visual.spec.ts:11-16` — Login baseline and tablet skip.
- `web/tests/visual-acceptance-contract.test.ts` — exact 15-image inventory.
- `web/e2e/fixtures.ts` — real unified-server/auth fixture; must not be mocked.
- Existing changes to preserve: packets 01-02 commits and workflow handoffs.

## Prerequisites

### Packet dependencies

- `product-ui-closure-01` must be `done`.
- Final integration, not this code change, requires `product-ui-closure-02` to be done with three inspected Penpot Login PNGs.

### Repository/base state

- Existing Login request body is `{username, password}`.
- Playwright projects remain desktop 1440×1024, tablet 1024×768, mobile 390×844.

### External prerequisites

- Shared project venv must start the real single-worker unified server.
- Playwright browser revision already installed or installable.

## Explicit change boundary

### Allowed files

- Modify: `web/src/pages/LoginPage.tsx`
- Modify: `web/src/styles/global.css`
- Modify: `web/src/auth/AuthProvider.test.tsx`
- Modify: `web/e2e/accessibility.spec.ts`
- Modify: `web/e2e/visual.spec.ts`
- Modify: `web/tests/visual-acceptance-contract.test.ts`
- Replace: `web/e2e/visual.spec.ts-snapshots/login-desktop.png`
- Create: `web/e2e/visual.spec.ts-snapshots/login-tablet.png`
- Replace: `web/e2e/visual.spec.ts-snapshots/login-mobile.png`
- Replace: Login-derived validation/server/session-expired snapshots for all three viewports
- Replace: three AppShell and one More-drawer snapshot to reconcile the branch's already-canonical `/legacy/` label

### Allowed behavior changes

- Remove the inert control and its empty layout space.
- Add Tablet Login to the reviewed visual inventory.

### Forbidden changes

- No API/Python/Session/Cookie/CSRF/storage/Penpot/component/token/deployment edits.
- Do not change AppShell or drawer behavior; their accepted binary updates may only reflect the already-implemented canonical `/legacy/` copy.
- No `page.route`, `route.fulfill`, history-state injection, snapshot masks, bulk snapshot update, or unrelated snapshot changes.

## Interface contract

### Consumes

- Existing Login submit path and real Playwright unified-server fixture.
- Packet 02's three Penpot Login reference images.

### Produces

- Login DOM with no named remember checkbox and unchanged `{username,password}` request body.
- Exact 16-image visual inventory including `login-tablet.png`.

### Invariants

- Browser session Cookie remains HttpOnly/SameSite=Lax/Path=/; no browser storage.
- Password visibility remains 44×44 and accessible.
- Real auth/CSRF/session flows and all non-Login baselines remain unchanged.

## Required behavior

- Unit and E2E tests explicitly assert absence of “保持登录状态”.
- Exact Login request body has no new field.
- Accessibility passes all three viewports with serious/critical=0 and visible keyboard focus.
- Three Login visual snapshots pass manual browser inspection; the live Penpot comparison remains a final-integration dependency on packet 02.

## Implementation guidance

1. Change unit/visual contract tests first and record RED.
2. Delete only the remember JSX and dedicated CSS.
3. Remove only the tablet Login skip and add only `login-tablet.png` to inventory.
4. Run unit/type/lint/build and accessibility before snapshots.
5. Run Login visual test without update, compare each browser result to corresponding Penpot PNG, then targeted `--update-snapshots` and no-update recheck.
6. Keep non-Login behavior unchanged. If an existing baseline is stale, require an exact visible explanation before updating it.

## Acceptance criteria

- [ ] Focused tests are RED before implementation and GREEN after it.
- [ ] Login DOM contains zero remember checkbox/control in all viewports.
- [ ] Login request remains exactly `{username,password}`; no auth/session/storage interface changed.
- [ ] Frontend unit/type/lint/build and three-project accessibility pass.
- [ ] Exactly 16 reviewed snapshots exist and the complete no-update visual suite passes. Login-derived state snapshots may move only because the removed row changes their underlying form layout.
- [ ] Manual desktop/tablet/mobile browser inspection finds no layout/wrapping/clipping defect; Penpot comparison is deferred to final integration.

## Test and verification commands

```powershell
Set-Location web
npm test -- src/auth/AuthProvider.test.tsx tests/visual-acceptance-contract.test.ts
npm run typecheck
npm run lint
npm run build
npx playwright test e2e/accessibility.spec.ts
npx playwright test e2e/visual.spec.ts --grep "login baseline"
Set-Location ..
```

The targeted snapshot update is allowed once, only after manual comparison:

```powershell
Set-Location web
npx playwright test e2e/visual.spec.ts --grep "login baseline" --update-snapshots
npx playwright test e2e/visual.spec.ts --grep "login baseline"
Set-Location ..
```

Expected: all commands exit 0; visual inventory is exactly 16; three Login baselines pass no-update.

## Stop conditions

Stop if the real fixture cannot start, auth payload currently differs, removing the row exposes an unexplained browser layout conflict, a non-Login snapshot changes without an exact visible explanation, or acceptance requires files outside the revised allowed set.

## Implementation handoff

- Status: done
- Files changed:
  - `web/src/pages/LoginPage.tsx`
  - `web/src/styles/global.css`
  - `web/src/auth/AuthProvider.test.tsx`
  - `web/e2e/accessibility.spec.ts`
  - `web/e2e/visual.spec.ts`
  - `web/tests/visual-acceptance-contract.test.ts`
  - `web/e2e/visual.spec.ts-snapshots/*.png` listed in the revised change boundary
  - this packet, plan, and review dependency records
- Acceptance criteria:
  - [x] RED: focused Vitest produced 2 expected failures (remember control present; tablet baseline absent).
  - [x] DOM contains no remember control and auth payload remains username/password only.
  - [x] Unit, type, lint, build, axe/focus, authentication, CSRF, and routing gates pass.
  - [x] Exactly 16 snapshots exist; fixed-baseline Playwright passes all runnable cases.
  - [x] Desktop 1440×1024, tablet 1024×768, and mobile 390×844 Login images were opened and manually inspected with no clipping or unexplained alignment defect.
- Verification:
  - `npm test` — PASS, 65 tests in 6 files.
  - `npm run lint`; `npm run typecheck`; `npm run build` — PASS.
  - `playwright test` — PASS, 28 passed and 2 intentional viewport skips in 3.4 minutes.
  - Focused process-lifecycle retry — PASS, 4 passed.
  - Login SHA-256: desktop `296544BE631539CF62FAEA0D0768E089B5834546C5BB5AC165071114097FD5EE`; tablet `B9203EFB13DF4A27C2850C88C53B509D628597D0D8AC95FA3C32837E6430C8AB`; mobile `313603286496E868DB9DA43B9D45F9C9CD9D2BCA9F21BEA08F05D4FF3C4FC19C`.
- Deviations:
  - Two runtime stop/restart visual tests use Playwright's `test.slow()` because the real Windows FastAPI+Gradio lifecycle exceeded the default 30-second budget under full-suite load; assertions were not relaxed.
  - Existing AppShell/More baselines showed `/legacy` while the branch code and docs already require `/legacy/`; four baselines were reconciled after exact diff inspection.
- Residual risks:
  - None for this packet. Packet 02 is now done; its three live Penpot Login exports were opened alongside the browser baselines and the remember removal, responsive widths, vertical rhythm, wrapping, and clipping were accepted.
- Commit:
  - `6ea34fd` (`fix: remove inert login persistence control`)
