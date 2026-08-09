---
id: "product-ui-closure-03"
title: "Remove inert Login persistence control"
status: "ready"
parallel-safe: false
depends-on: ["product-ui-closure-02"]
base-commit: "ef93550f6b0616815314baf2f62263b43536a17e"
owner: "unassigned"
---

# Task Packet: Remove inert Login persistence control

## Goal

React Login no longer promises persistent login, continues submitting only username/password, and passes unit/accessibility/visual acceptance across desktop/tablet/mobile against packet 02's Penpot exports.

## Non-goals

- Do not implement persistent sessions, refresh tokens, storage, Cookie expiry, or new auth API fields.
- Do not change Register, session-expired, logout, CSRF, routing, navigation, or non-Login baselines.
- Do not edit Penpot source or reference exports.

## Delivery context

The checkbox is default-checked but inert; its state never reaches the API. The user chose strategy A: remove it while keeping the existing session Cookie and 12-hour in-memory sliding expiry. Packet 02 provides the new three-tier design reference; this packet synchronizes only the browser implementation.

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

- `product-ui-closure-02` must be `done`, including the three inspected Penpot Login PNGs.

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

### Allowed behavior changes

- Remove the inert control and its empty layout space.
- Add Tablet Login to the reviewed visual inventory.

### Forbidden changes

- No API/Python/Session/Cookie/CSRF/storage/Penpot/component/token/deployment edits.
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
- Three Login visual snapshots match inspected Penpot references except acceptable rasterization differences.

## Implementation guidance

1. Change unit/visual contract tests first and record RED.
2. Delete only the remember JSX and dedicated CSS.
3. Remove only the tablet Login skip and add only `login-tablet.png` to inventory.
4. Run unit/type/lint/build and accessibility before snapshots.
5. Run Login visual test without update, compare each browser result to corresponding Penpot PNG, then targeted `--update-snapshots` and no-update recheck.
6. Verify non-Login snapshot hashes are unchanged.

## Acceptance criteria

- [ ] Focused tests are RED before implementation and GREEN after it.
- [ ] Login DOM contains zero remember checkbox/control in all viewports.
- [ ] Login request remains exactly `{username,password}`; no auth/session/storage interface changed.
- [ ] Frontend unit/type/lint/build and three-project accessibility pass.
- [ ] Exactly 16 reviewed snapshots exist; only three Login images changed/added and no-update visual test passes.
- [ ] Manual desktop/tablet/mobile Penpot comparison finds no unexplained layout/wrapping/clipping difference.

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

Stop if packet 02 is not done, the real fixture cannot start, auth payload currently differs, removing the row exposes an unexplained Penpot/code layout conflict, a non-Login snapshot changes, or acceptance requires files outside the allowed set.

## Implementation handoff

Replace this placeholder using the template format. Include RED/GREEN counts, exact payload proof, axe/focus results, three screenshot dimensions/hashes/manual comparison, unchanged non-Login hash proof, commands, scope, and commit.
