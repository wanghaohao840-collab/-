# Task 5a Report: Penpot Empty-State Alignment

## Status

Done from implementation base `60e4525`; code commit `67132d2`. Ready for independent visual re-review.

## RED to GREEN

- Focused baseline: `web/src/pages/DocumentsPage.test.tsx` passed `12/12` before corrective assertions.
- RED: the empty-state case reported five soft failures for the stale subtitle, visible empty filter, nested limits note, standalone icon class and standalone `文`; the complete/list filter case passed.
- GREEN: the page suite passed `12/12`. The approved subtitle is exact in every state, only real non-empty data exposes the filter, the limit note follows the card, and the title uses a CSS halo without an asset or standalone tile.

## Visual comparison

- Authority: `docs/product-ui/reference/penpot/documents-empty.png`, 1440 × 1024.
- Penpot card border: `x=540..1099`, `y=302..601`; fresh actual: `x=540`, `y=301.796875`, `560 × 300` (device-pixel border `x=540..1099`, `y=302..601`).
- Penpot and fresh actual title halo pixels both occupy `x=788..850`, `y=420..482`.
- Actual content geometry: title line box `y=432.796875`, copy `y=467.796875`, empty import action `x=750`, `y=499.796875`, `140 × 44`, limits line box `y=633.796875` outside the card.
- Responsive audit: tablet card `560 × 320`, mobile card `342 × 320`; import actions remain 44 px and both viewports have no horizontal overflow.
- Fresh actual: ignored `web/test-results/task5a/documents-empty-actual.png`; no Task 6 E2E test or snapshot was invoked, modified or staged.

## Verification

- Task 5 focused tests — PASS, 4 files / 36 tests.
- `npm test` — PASS, 10 files / 101 tests.
- `npm run typecheck` — PASS.
- `npm run lint` — PASS.
- `npm run build` — PASS, 114 modules transformed.
- `node --test tests/design/test_penpot_component_map.mjs` — PASS, 6/6.
- `node scripts/design_tokens.mjs --check design/tokens/zhiyan.tokens.json web/src/styles/tokens.css` — PASS.
- `git diff --check` and scoped `git diff --check 60e4525 -- <allowed paths>` — PASS; line-ending notices only.

## Scope and concerns

- Relative to `60e4525`, changes are limited to the four authorized Task 5 production/test paths and three Task 5a ledgers.
- Auth/query/cache, AppShell/navigation, shared components, backend, dependencies, tokens, Penpot sources/references and E2E remain unchanged.
- The pre-existing Task 6 changes in `web/e2e/accessibility.spec.ts`, `auth-shell.spec.ts`, `visual.spec.ts` and `documents.spec.ts` were preserved and excluded from staging.
- No deviations. Task 6 remains responsible for final three-project browser acceptance.
