---
id: "document-search-04"
title: "Stable publication and final acceptance"
status: "done"
parallel-safe: false
depends-on: ["document-search-03"]
base-commit: "daeb24f"
owner: "Codex-inline"
---

# Task Packet: Stable publication and final acceptance

## Goal

The reviewed search slice is documented, copied into the stable directory, safely published and accepted using real BGE-M3 retrieval and production operational gates.

## Non-goals

- New feature implementation, external search, Neo4j, infrastructure redesign, commit/push.

## Delivery context

Only stable `D:\python_self_agent` is operated by scheduled tasks. Publication must use its cold-backup/scan/health/deep-smoke controller and end in mandatory Codex integration review.

## Relevant files and current interfaces

- `deploy/windows/Update-Deployment.ps1` — serialized safe updater.
- `deploy/smoke_test.py` — health/Qdrant/real LLM deep smoke.
- `docs/product-ui/README.md`, `PROJECT_KNOWLEDGE.md` — current product truth.
- Packets 01–03 handoffs must be `done`.

## Prerequisites

- Packets 01–03 done. Docker Desktop/App/Qdrant available. Real environment remains configured without printing it.

## Explicit change boundary

- Modify product documentation and packet handoffs; create final integration review.
- Copy only the reviewed files owned by Packets 01–03 into stable.
- Forbidden: copy `.env`, data, backups, outputs or Git metadata; bypass scans; manual partial schema edit; start Neo4j; delete rollback evidence; commit/push.

## Interface contract

- Consumes: reviewed isolated outputs and release controller.
- Produces: stable image, safe report/backup, real search/provenance evidence, final `accepted|changes-required|blocked` review.
- Invariants: localhost-only App, Qdrant POSIX volume, two containers, hidden operations/background notifications.

## Required behavior

- Operational rollback must use the safe updater's paired image **and cold-backup data restoration**, not an old-image-only switch while retaining new document-source rows. Read-only evidence: `Update-Deployment.ps1` rollback path calls `Restore-Deployment.ps1` before rollback health/smoke. Packet 02 only establishes old-table read compatibility; old code cannot scrub the new source table during subsequent document deletions. Validate the actual release/rollback evidence and document this limitation.

- Hash-compare copy set, rerun stable tests, safe update and deep smoke.
- A temporary isolated database/user namespace inside the published App container proves scoped real-BGE-M3 search and document-source notes using the unchanged production provider configuration and Qdrant. The shipped HTTP assets and authentication boundary are checked separately; the real browser QA-confirmation handoff is covered by three-viewport E2E. Cleanup uses existing document deletion plus disposal of the strictly bounded temporary root, never production SQL/account deletion.
- Final review records exact counts, report/archive, source/image hashes, deviations and residual risks.

## Implementation guidance

Follow Plan Task 4. Do not patch implementation during formal final review; create corrective packets if findings exist.

## Acceptance criteria

- [x] Documentation truthfully distinguishes local vs online search.
- [x] Full Python/frontend/E2E/audit suites pass.
- [x] Safe updater and repeated deep smoke pass without Neo4j.
- [x] Corrected isolated real-service workflow passes and is cleaned; submitted for separate mandatory final review.

## Test and verification commands

Use the exact combined/release commands in `REVIEW.md` and Plan Task 4. Expected: zero failures and a successful update report.

## Stop conditions

Stop for any failed security gate, backup, production isolation, prerequisite packet, unknown dirty-file overlap, or final-review finding requiring code change.

## Implementation handoff

- Status: done (2026-09-05); mandatory review recorded separately.
- Files: product README, PROJECT_KNOWLEDGE, 42 implementation/test paths in COPY_MANIFEST.json, release/packet records. Hashes rechecked: 42 matches, zero mismatches in stable and worktree.
- Verification: stable full Python 1756 passed / 8 skipped; frontend 188 passed; browser 31 passed / 2 expected viewport skips; 18 design contracts; typecheck/lint/build and fresh npm audit (zero vulnerabilities) passed. Final focused Python rerun: 106 passed / one existing local-Qdrant warning, 19.58 seconds.
- Release: update-20260905T004248Z.json succeeded; backup assistant-20260905T004522Z.tar.gz. Exact commands and fresh real-service checks are in RELEASE_ACCEPTANCE.md. Post-recovery deep smoke and 11 search/source/isolation checks passed; fixtures cleaned. Live HTTP/assets/auth verified separately from isolated browser tests.
- Deviations: persistent custom proxy bypass required user UI save; production account database was not used for disposable fixture accounts. No Penpot update claimed.
- Residual risks: provider/proxy availability; single-process admission; old-image rollback requires paired data restore; existing bundle size/local-Qdrant warning and previously observed health-task hang remain documented follow-ups.
- Scope: no unrelated work overwritten, credentials changed, model switched or Neo4j started. Not committed; not pushed.

### Publication preflight — 2026-09-04

- Fresh `npm audit --audit-level=low --fetch-timeout=120000 --fetch-retries=1`: exit 0, **0 vulnerabilities**. This supersedes the earlier network-timeout gate.
- Docker Desktop was stopped. Supported CLI startup restored the existing App/Qdrant containers, both healthy; no container/data recreation. App remains `127.0.0.1:7860`; Qdrant retains `zhiyan_qdrant_data`; no Neo4j container.
- Four existing scheduled tasks retain `-WindowStyle Hidden` and stable paths. Health inspection was running at preflight; the other tasks were Ready.
- Reviewed and copied exactly 42 Packet 01–03 source/test files. Every source/destination SHA-256 matches; overwritten stable files are preserved under ignored `.runtime/search-preintegration-20260904`. Existing unrelated dirty work and secrets were not copied or changed.
- Stable full Python/frontend regression started. Container publication, real search acceptance and final review remain pending.
- Stable frontend verification completed: 188 tests, typecheck/lint/build and 18 design contracts pass. Stable Playwright combination: 31 passed / 2 expected viewport skips (3.0 minutes), no snapshot update.
- Operations preflight found the health task stuck in a running instance since 14:00 (Task Scheduler COM confirmed engine PID 20188). Subsequent launches returned 0x800710E0. Stopped/restarted only the exact `PythonSelfAgent-Health` task through Task Scheduler; it completed with result 0 and fresh healthy status at 13:31:07Z. Containers and other task definitions were not changed. The original hang's cause remains unproven; command deadlines are a follow-up hardening opportunity.

### Release attempt and network repair — 2026-09-05

- First safe update `update-20260904T133143Z.json` passed builds, both image gates and default smoke, then failed deep smoke with embedding connection failure. Paired image/data rollback succeeded with no compensation errors. Backup `assistant-20260904T133417Z.tar.gz` and Qdrant companion checksums were independently verified.
- Diagnosis: direct Windows TLS worked, while the system proxy and Docker container TLS failed. Docker proxy logs confirmed forwarding through the existing Clash Verge proxy. Added only `api.siliconflow.cn` to the existing Windows proxy exclusion and Clash Verge persistent bypass list. Prior Windows value retained in ignored `.runtime/search-proxy-before.json`. Docker Desktop restart loaded the exclusion; existing App/Qdrant containers were started and recovered.
- Container unauthenticated models request now returns 401 with valid TLS. `venv/Scripts/python.exe deploy/smoke_test.py --env-file deploy/.env --deep` passed with real retrieval and LLM answer after repair. No certificate-verification changes, credential changes or model switches.
- A fresh complete safe update has started. Publication/final acceptance still pending its result.

### Resolved reality conflict — production fixture cleanup

- Expected: register a temporary account in the live database, then remove it through supported cleanup.
- Observed before fixture creation: `api/routes/auth.py` supports register/login/session/logout only; repository search found no account deletion/cleanup service. Logout does not remove account records. Direct SQL cleanup would violate the packet's persistence boundary.
- Decision: use the existing deep-smoke isolation pattern inside the published container, with an independent temporary database, generated users and namespace, actual shipped application factory and real production embedding/Qdrant configuration. Copy only the already-validated active index metadata into the temporary root; never manufacture a production activation or change production registry state. Source-note creation/deletion use existing APIs. Verify namespace cleanup before disposing the temporary root; preserve it on failure. No persistent production account is created.
- Keep separate evidence labels: container real-service API acceptance, published HTTP/auth/assets, and isolated three-viewport browser handoff. Do not claim a logged-in browser test against the live production database. A temporary verification harness under ignored `.runtime` is allowed; it is not a new product endpoint or deployment setting.
- This narrower acceptance method preserves the intended real-model, source, isolation and cleanup guarantees without adding an account-deletion feature. No user-data or credentials copy. Packet resumes in progress.
