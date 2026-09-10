# Document search continuation — 2026-09-04

## Final outcome — 2026-09-05

Packets 01–04 are done. FINAL_INTEGRATION_REVIEW.md is accepted. Successful safe release `update-20260905T004248Z.json` is running with healthy App/Qdrant. Persisted proxy bypass, repeated deep smoke, real BGE-M3 scoped search/source-note acceptance and fixture cleanup passed; see RELEASE_ACCEPTANCE.md. Final focused regression: 106 passed. All 42 integrated implementation hashes still match. No commit/push. Historical continuation notes below describe earlier states, not remaining blockers. Next roadmap module is learning center, requiring its own specification and review.

## Current state

- Packet 01 is implemented and verified in `D:/python_self_agent/.worktrees/bge-m3-runtime-identity`, branch `codex/bge-m3-runtime-identity`.
- Stable `D:/python_self_agent` now contains the 42 reviewed search source/test files, hash-matched to the worktree. Previous owned files are retained in `.runtime/search-preintegration-20260904`. Containers still run the previous image pending release gates.
- Packet 02 is now implemented and verified: additive document-source table, server re-resolution, compatible idempotency, scoped source reads/filtering, deletion scrubbing and API contract. Production database has not been migrated.
- Packet 03 React workflow is **done and verified**. Packet 04 release/integration is **in progress**. Stable full regression passed (1756 Python, 188 frontend, 31 browser tests). First publication rolled back successfully after an embedding connection failure; the exact-host proxy bypass repaired it and real deep smoke passed. Second publication failed before container cutover on Debian package HTTP 502. A complete safe updater retry is running. `/search` must not be described as published until its update and real-service acceptance pass.

## Evidence

- Packet 03 final: **188 frontend tests**, **31 browser tests passed / 2 expected skips**, **18 design-contract tests**, typecheck/lint/build/token check passed. Fresh complete Python regression: **1756 passed / 8 skipped / 1 existing local-Qdrant warning**. Final browser run covers all three viewports, source-copy/QA/editable-note actions and source redaction after deletion, with zero serious/critical axe findings or checked overflow. See Packet 03 handoff for exact commands, hashes and screenshots.
- Browser fixture isolation is now enforced in `web/e2e/python-runtime.ts`: disable dotenv and strip inherited external service/Python overrides without mutating the parent. An earlier run was stopped after unintended Neo4j routing retries; do not reuse that partial run as acceptance evidence.
- Fresh release `npm audit --audit-level=low --fetch-timeout=120000 --fetch-retries=1` completed with **0 vulnerabilities**, exit 0; earlier network timeouts are superseded. Stable frontend: **188 passed**, typecheck/lint/build passed, 18 design contracts passed. Stable full Python/browser regressions are running.

- Packet 02: full Python regression **1753 passed / 8 skipped**, final current-code combined regression **118 passed**, API/domain regression **203 passed**. Full run preceded the final 401 handling refinement; the final combined run includes it. See Packet 02 handoff for exact commands and snapshot timing.

- Full Python regression: 1732 passed / 8 skipped, exit 0. Final-current-code search rerun: 37 passed. API regression: 162 passed. Search + note baseline: 82 passed.
- The only reported warning is Qdrant local-client payload indexes being ineffective. External Qdrant integration requires an explicitly configured isolated test URL; production was not used.
- Git whitespace checks passed for the modified tracked implementation files. Existing unrelated dirty/staged/untracked changes were preserved.
- No commit, push, container update, model/index switch, `.env` edit, user-data migration, or Neo4j startup.

## Next implementation requirements

Finish Packet 04 after stable regressions: safe updater with paired cold-backup/image rollback, real BGE-M3 provenance acceptance and repeated deep LLM smoke, then final review. Docker Desktop was restored with existing healthy App/Qdrant containers, loopback-only App and the original ext4 named volume. Four operations remain hidden. COPY_MANIFEST.json records the exact integration set. Read Packet 04's cleanup correction before running the temporary real-service fixture: the live database has no account-deletion API; use an isolated database/namespace inside the published container, not production SQL cleanup. No further UI/backend redesign is needed.

The following Packet 02 requirements have been completed and remain regression constraints:

1. Read Packet 02's reality correction and the corrected plan before editing: no `tests/test_database.py` exists; schema tests belong in the existing note repository test file.
2. Add a companion source table without changing the legacy QA table. Deleted source identity/excerpt must be nullable/redacted; do not claim `executescript` automatically provides whole-script atomicity.
3. Preserve old QA idempotency digests and replay-before-resolution. Source resolution must happen outside the runtime mutation lock. Recheck deletion authority in the write transaction, including deletion that has already completed.
4. Verify source creation/read/filter/deletion/projection and upgrade compatibility before beginning React work. Distinguish old-image database readability from operational rollback safety.

Serial execution remains the user-approved mode. Continue in the existing worktree; do not create a fresh worktree or overwrite production changes.
