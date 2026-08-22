# Task 5 Report: Responsive React Document Library

## Status

Done and ready for independent re-review from implementation base `1ac419a`. Initial implementation: `9571ee9`; review corrections: `2a6ea75`, `aed0d5f` and `3b22198`.

## Delivered

- Real authenticated `/documents` route; the other five protected navigation routes still render the migration page.
- Packet 04 public DTOs and exact document/import list, upload, retry, retry-all, cancel and delete routes through `useAuth().request<T>()`.
- TanStack Query keys, active-only 2 s polling, focus refresh, active-to-success document invalidation, server-authoritative mutation cache writes and document/import refresh after delete success or failure.
- Zero-lifetime inactive document/import caches prevent fixed query keys from retaining one authenticated user's data for the next user. Each import mutation captures the exact Query instance at startup and writes its authoritative batch only if that same instance is still current and observed at success; TanStack visibility is the sole focus-refetch owner.
- Loading, reloadable error, stale-data preservation, exact empty, complete/filter, active, partial-failure and terminal states with no production fake data.
- Responsive 342/856/1096 px content, approved filter/action widths, desktop/tablet modal, 390 x 594 mobile bottom sheet and token-only styling.
- Import validation for 20 files, 100 MiB each and 500 MiB per batch; real repeated-file `FormData` without a manual multipart content type.
- Focus trapping, Shift+Tab, Escape, trigger return, original overflow restoration, qualified action names, delete error focus, native lists, one deduplicated polite live region, 44 px targets and reduced motion.
- Server-stage task copy, pending-cancel suppression, active-first mixed-batch titles, non-interactive terminal summaries and filename-qualified 10% live progress milestones.
- Verified Penpot component-map entries for `DocumentRow`, `ImportTaskRow` and `FilePicker` using the exact approved container/state identities recorded in Packet 05.

## RED to GREEN evidence

- RED: the three focused suites exited 1 before implementation. All seven initial page expectations observed the migration placeholder; query and import-dialog suites could not resolve their not-yet-created modules/styles.
- Initial GREEN: three focused files, 17 tests passed.
- Expanded GREEN: three focused files, 25 tests passed after adding delete failure, real FormData, stale-data preservation, exact task actions, terminal state and file-limit coverage.
- Final regression: nine files, 90 tests passed.

### Independent-review correction round

- Corrective RED command across query, batch and page tests: 10 failed / 18 passed. It reproduced retained user-A cache data, five missing stage mappings, incorrect mixed/cancel/terminal presentation and missing live stage/milestone content.
- Refined focus-only RED: expected two requests but received three when a completed hidden-to-visible refetch was followed by the browser focus event.
- Corrective GREEN: query, batch and page tests passed 28/28; all four Task 5 focused files passed 34/34.
- Full corrected regression: ten files, 99 tests passed.

### Second independent re-review correction

- RED: a real deferred submit started with user A mounted, then resolved after A unmounted and the zero-lifetime imports query disappeared. `cacheServerBatch()` recreated the default-lifetime cache containing `late-user-a.md`.
- GREEN: `cacheServerBatch()` locates the exact imports query and writes only when `getObserversCount() > 0`. The late result leaves the key absent and user B never renders A; the still-mounted authoritative cache-write regression remains green.
- Corrected query suite: 9/9. All Task 5 focused tests: 35/35. Full frontend: 100/100.

### Final independent re-review correction

- RED: user A started a deferred submit, unmounted and lost the old query; user B then mounted a new observed exact query before A succeeded. The observer-only guard replaced B's cache and DOM value with `late-user-a.md`.
- GREEN: all four server-batch mutations capture their exact imports Query identity in `onMutate`; shared success handling writes only when the current Query is that captured instance and remains observed. B's cache and DOM stay `user-b-import.md`, while the existing active-page authoritative-write test remains green.
- Corrected query suite: 10/10. All Task 5 focused tests: 36/36. Full frontend: 101/101.

## Verification

- `npm ci` — PASS; 274 locked packages installed/audited.
- `npm test` — PASS; 10 files, 101 tests.
- `npm run typecheck` — PASS.
- `npm run lint` — PASS.
- `npm run build` — PASS; 114 modules transformed.
- `node --test tests/design/test_penpot_component_map.mjs` — PASS; 6/6.
- `node scripts/design_tokens.mjs --check design/tokens/zhiyan.tokens.json web/src/styles/tokens.css` — PASS.
- `git diff --check` — PASS; line-ending notices only.
- `web/dist` and `web/node_modules` are still ignored.

## Self-review

- Compared the complete implementation diff with `1ac419a`; all changed implementation paths are explicitly allowed by the revised brief.
- Confirmed no changes to backend, shared component internals, AuthProvider, AppShell/navigation, E2E, Penpot sources/exports, dependencies/config/lockfiles or generated tokens.
- Confirmed TypeScript contains only Packet 04 public fields and production UI renders API data only.
- Confirmed the three component-map entries point to real exported components and exact verified Penpot identities.
- Compared the corrective diff with `3ad5252`; it contains only seven Task 5-owned query, batch, page, style and focused-test paths.
- Confirmed user-A document/import caches are removed before user-B mount, focus has one refetch owner, and repeated identical live summaries do not mutate the DOM.
- Compared the second corrective diff with `265bb99`; production/test changes are limited to `queries.ts` and `queries.test.tsx`, and the three Task 5 handoff ledgers record the evidence.
- Confirmed a late mutation cannot create an absent or inactive imports query, while a mounted page still receives the returned server batch before invalidation.
- Compared the final corrective diff with `69126e8`; production/test changes are limited to `queries.ts` and `queries.test.tsx`, plus these three Task 5 handoff ledgers.
- Confirmed a mutation started against user A cannot write into a replacement user-B Query instance even when B is active before A resolves; submit, retry, retry-failed and cancel share the same identity gate.

## Deviations and concerns

- No implementation deviations.
- The lock audit still reports one pre-existing high-severity advisory; dependency work is outside this packet.
- During concurrent gate execution, one unchanged `ProtectedRoute` test transiently asserted before its login DOM committed. It passed 4/4 alone and the required serial full suite passed 90/90; no Auth changes were made.
- Packet 06 owns browser/E2E viewport acceptance. Independent re-review remains required before integration acceptance.

## Handoff

See `docs/agent-workflow/task-packets/2026-08-15-document-library-vertical-slice/05-react-document-library.md`. Progress is `ready for independent re-review`.
