# Integration progress

## 2026-09-09 — Baseline inspection

- Current branch: codex/batch-import-async-tasks, HEAD daeb24f; index initially empty.
- Search and managed embedding source files match the corresponding learning worktree. Learning schema/service/API/UI registration is absent in the current tree.
- Existing learning deletion hook performs plan deletion in the same transaction as note source scrubbing; retain this boundary.
- Initial backend/frontend regression processes were interrupted before a final result became available. No passing result claimed; rerun after integration with durable local output.
- Production recovery scripts/records already present are a separate pending workstream and remain unstaged.

## 2026-09-09 — Integration and regression

- Imported the existing SQLite learning domain and API/UI wiring (12 shared paths, 29 new paths in `learning-import.json`). No new learning algorithm or RAG implementation.
- Added combined learning-plan/source-note cleanup assertions to the existing deletion regression. Plan/task cleanup and note redaction use one transaction; user body survives.
- Shared RAG source comparison found no semantic conflict. Trimmed trailing blank lines reported by `git diff --cached --check` in 10 source/review files.
- First post-integration backend invocation used an unexpanded Windows wildcard and collected no tests; reran with an explicit PowerShell file list. It is not passing evidence.

### Fresh backend evidence

Python is the repository `venv/Scripts/python.exe`. Logs are untracked `.runtime/integration-*.log`.

```powershell
$learningTests = @(Get-ChildItem tests/test_learning*.py | ForEach-Object FullName)
.\venv\Scripts\python.exe -m pytest -q tests/api tests/memory/rag tests/memory/storage tests/tools tests/evals tests/test_document_search.py tests/test_note_models.py tests/test_note_repository.py tests/test_note_service.py tests/test_note_source_deletion.py tests/test_user_runtime.py tests/test_rag_source_authority.py tests/test_regression_environment.py tests/test_qa_deletion.py tests/test_document_library_service.py @learningTests --basetemp=.pytest-tmp-integrated --tb=short
.\venv\Scripts\python.exe -m pytest -q tests/test_app_bootstrap.py tests/test_note_migration.py tests/test_note_projection.py tests/test_qa_service.py tests/test_qa_worker.py tests/assistants tests/ui tests/integration --basetemp=.pytest-tmp-integrated-compat --tb=short
.\venv\Scripts\python.exe -m pytest -q tests/deploy --basetemp=.pytest-tmp-integrated-deploy --tb=short
```

- Main targeted set: **1387 passed**, one existing local-Qdrant payload-index warning, 165.25 s, exit 0.
- Assistant/legacy UI/shared lifecycle compatibility: **286 passed, 6 conditional skips**, 127.31 s, exit 0.
- Deployment suite against the working tree (includes deferred Windows changes): **216 passed, 1 conditional skip**, 190.74 s, exit 0. This is not acceptance of the deferred production recovery.

### Staged-only boundary verification

Exported the index with `git checkout-index --all --prefix=.runtime/integration-index/`.
From that directory, using the repository venv, ran:

```text
-m pytest -q tests/test_app_bootstrap.py tests/test_learning_plan_deletion.py tests/test_note_source_deletion.py tests/api/test_search_routes.py tests/api/test_learning_routes.py tests/memory/rag/test_application_embedding_wiring.py tests/deploy/test_compose_contract.py tests/deploy/test_dependency_contract.py tests/deploy/test_image_contract.py --basetemp=.pytest-tmp-index --tb=short
```

Initial result: **72 passed, 1 failed**. Root cause: excluding all Windows scripts omitted the `--pull --no-cache` build hunk required by the staged Qdrant image contract. Staged only that hunk, preserving the separate fixed-image recovery guard in the working tree. Refreshed the exported file and ran:

```text
-m pytest -q tests/deploy/test_compose_contract.py tests/deploy/test_windows_release.py --basetemp=.pytest-tmp-index-deploy --tb=short
```

Result: **25 passed**, 16.56 s, exit 0. No product source changed after the 72 passing checks.

### Frontend and browser evidence

`D:/NODEJS_fastapi/npm.cmd --prefix web test -- --maxWorkers=2`: **196 passed / 25 files**, exit 0.
`npm --prefix web run build` (includes TypeScript project build) and `npm --prefix web run lint`: exit 0.
Existing main bundle size warning remains (542.03 kB).

From `web`, with `APP_COOKIE_SECURE=false` for isolated HTTP fixtures:

- `npm run test:e2e -- search.spec.ts learning.spec.ts insights.spec.ts`: **9 passed**, all three viewports, 3.2 min. Real services, isolated deterministic model fixtures; no paid-provider quality claim.
- `npm run test:e2e -- visual.spec.ts --update-snapshots`: **22 passed, 2 expected viewport skips**. Updated only differing snapshots.
- `npm run test:e2e -- notes.spec.ts qa.spec.ts --grep 'approved visual' --update-snapshots --output=test-results/navigation`: **6 passed**.
- Snapshot review: shell/document/notes desktop differences are confined to the new learning navigation row (x=28..124, y=424..444); mobile More expands for learning. QA baselines also lacked the already implemented note-save buttons. Inspected current desktop QA/shell and mobile learning screenshots; no runtime/style changes were made to satisfy snapshots.
- Non-updating verification `npm run test:e2e -- visual.spec.ts --grep 'authenticated shell|documents .* baseline|mobile More' --output=test-results/verify-shell`: **10 passed, 2 expected skips**, 51.7 s.

## 2026-09-10 — Final verification and handoff

- QA/notes non-updating verification (`npm run test:e2e -- notes.spec.ts qa.spec.ts --grep 'approved visual' --output=test-results/verify-notes-qa`) initially produced **5 passed, 1 failed**. The desktop summary differed by 307 pixels: expected `starting`, actual `queued`. The test waited only for status-bar visibility, so it could capture the queue before the worker started.
- Changed only `web/e2e/qa.spec.ts` to wait for the real fixture's `starting · 0%` before taking that screenshot. No snapshot tolerance, response mock, production behavior or fixture delay was changed.
- Non-updating `npm run test:e2e -- qa.spec.ts --grep 'approved visual' --project=desktop --repeat-each=2 --output=test-results/verify-qa-stage`: **2 passed**, 35.4 s, exit 0. Together with the unchanged five passing cases this closes the affected visual scenarios.
- Fresh `npm --prefix web run typecheck`: exit 0.
- Manifest and index match exactly: **230 files**. No unresolved merge markers in staged text; common credential/private-key patterns had no hits. `git diff --cached --check` passes.
- Commit boundary is documented in README.md. The updater has one deliberately staged build hunk and an unstaged recovery hunk. All other deferred operations and incident records remain intact.
- Status: complete for local code integration and targeted regression. **Not committed, not pushed, no production deployment or migration executed.** Existing Qdrant-local payload-index and bundle-size warnings remain; external-service/privilege tests were conditional skips, not executed acceptance.

## 2026-09-10 — Authorized local commit

- User requested review and local commit of the prepared integration. No push or deployment is included.
- Parent is now `7f65be0` (UI design documentation only); it preserves the reviewed implementation baseline. No application code has changed since the recorded passing regressions.
- Fresh precommit checks: all 230 staged paths match the manifest; whitespace and merge-marker checks pass; no common private-key/API-token patterns found. Runtime data, backups and credentials are excluded.
- The updater contains only the approved `--pull --no-cache` build hunk in the index. Recorded SHA-256 values for 39 deferred tracked/untracked worktree files to verify preservation after commit.
- This record and the implementation are delivered together in one local integration commit. Earlier uncommitted status entries describe the preceding checkpoints.
