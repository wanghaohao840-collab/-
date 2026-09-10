---
id: overview-learning-insights-01
title: Real overview and learning insights vertical slice
status: done
parallel-safe: false
depends-on: []
base-commit: daeb24f
owner: Codex-inline
---

# Task Packet: Real overview and learning insights

## Goal

Authenticated users can see truthful overview/statistics and generate, list, read and download their own learning reports from the React product UI.

## Non-goals

- Inferred time, mastery, streaks, event-store migration, async reports, report deletion, Neo4j.

## Delivery context

The routes are currently placeholders. Existing document, QA, note and report services own user data; the new slice must aggregate only through their public interfaces.

## Relevant files and current interfaces

- `api/app.py` composes routers and `ApplicationServices`.
- `app/document_library.py:list_documents` is the document read boundary.
- `app/qa_service.py:report_turns` is the completed-QA boundary.
- `app/note_service.py:count/recent` is the note boundary.
- `app/reports.py` owns immutable report files and records.
- `web/src/App.tsx` maps the two target paths to placeholders.

## Prerequisites

- No packet dependencies or external service requirement for focused tests.
- Preserve all pre-existing dirty worktree changes.

## Explicit change boundary

- May create/modify insights service, domain aggregate methods, insights schemas/routes, app wiring, React insight feature/pages/styles and their focused tests/docs.
- Must not change authentication tokens, persistence ownership, document/QA/note mutation semantics, RAG backend, Neo4j, or user data.

## Interface contract

- Produces authenticated `/api/v1/overview` and `/api/v1/insights/*` JSON/download interfaces and React pages.
- All reads are user-scoped; POST requires CSRF; downloads never reveal a server path.

## Required behavior

- Empty and populated data are truthful; deleted/fenced records remain excluded by their owner domain.
- Daily activity is UTC and bounded to 7–90 days.
- Report not found is 404; other errors use safe API boundaries.

## Acceptance criteria

- [x] API isolation, CSRF, report download, note deletion and document deletion-fence tests pass.
- [x] UI tabs/actions/empty states and user/late-result cache isolation tests pass.
- [x] production frontend builds and existing vertical slices regressions pass.
- [x] deployed App and Qdrant pass deep smoke.

## Test and verification commands

Run the commands listed in `REVIEW.md`; all must exit zero.

## Stop conditions

Stop if existing public interfaces differ, user isolation cannot be proven, or implementation requires an unlisted persisted-data redesign.

## Implementation handoff

- Status: done; published from the stable directory and verified on 2026-09-04.
- Verification already passed on 2026-09-04:
  - `venv/Scripts/python.exe -m pytest -q --tb=short --basetemp=deploy-state/pytest-overview-accepted`: 1700 passed, 8 skipped.
  - `npm --prefix web test -- --run --maxWorkers=2`: 168 passed across 21 files.
  - `npm --prefix web run typecheck`, `lint`, `build:app`: passed.
  - With `PYTHON_DOTENV_DISABLED=1`, `RAG_BACKEND=json`, `RAG_EMBEDDING_PROVIDER=simple`, run `npx playwright test e2e/auth-shell.spec.ts e2e/insights.spec.ts e2e/visual.spec.ts` from `web`: 31 passed, 2 expected viewport skips, no snapshot updates.
  - Insights E2E includes real report generation, both downloads, all three viewport sizes and no serious/critical axe violations.
  - `npm --prefix web audit --audit-level=low`: 0 vulnerabilities after a targeted dev-only `fast-uri` 3.1.5 → 3.1.7 lock update.
- Production checkpoint:
  - `deploy-state/reports/update-20260903T160713Z.json` succeeded, including fixed OpenSSL, image scans, cold backup and deep smoke.
  - The subsequent publication was interrupted after image build; Docker Desktop was found stopped at 2026-09-04 09:45 local time. No completed report exists for that attempt.
  - Retained its candidate images as `python_self_agent-{app,qdrant}:interrupted-20260903T161845Z`, restored stable tags from the last verified containers, and started those same containers without recreating data.
  - Default smoke passed after recovery. The fresh update completed successfully: `deploy-state/reports/update-20260904T014726Z.json`, completed at 2026-09-04T01:56:28Z, including cold backup, both image security gates, health and deep smoke.
  - Repeated `venv/Scripts/python.exe deploy/smoke_test.py --env-file deploy/.env --deep`: all four checks passed, including temporary document import, retrieval and a real LLM answer.
  - Both containers healthy; App exposed only on `127.0.0.1:7860`. `/overview` and `/insights` return 200; unauthenticated `/api/v1/overview` returns 401. Served JS/CSS SHA256 values match the running container assets; backend insights source hashes match the stable checkout.
  - All four Windows scheduled-task actions include `-WindowStyle Hidden`. Health readback is healthy; successful release notification was recorded in `deploy-state/notifications/update.latest.json`, without a toast.
- Deviations: old shell/More screenshots intentionally updated for the real overview; legacy migration CTA E2E now targets `/search` because `/notes` is implemented. No Penpot source export was changed in this delivery.
- Residual risks: current read model reflects retained records, not immutable all-time events; production bundle remains slightly above Vite's advisory chunk-size threshold.
- Commit: not committed or pushed.
