# Plan Review: BGE-M3 application source authority

- Source plan: docs/superpowers/plans/2026-09-03-bge-m3-app-source-authority.md
- Reviewed commit/worktree: a33b071 plus accepted uncommitted E3-A1/A2.
- Review date: 2026-09-03
- Verdict: accepted

## Repository evidence

- app/database.py:10 users id/status schema, :368 connect creates parent/database; this must not be used.
- app/auth.py:106 register generates UUID account IDs. No filter of inactive accounts is safe for migration.
- app/runtime.py user RAG namespace pdf_{user_id}, app/storage.py user_paths points to per-user history/cache.
- app/history.py load defaults missing histories; app/migration.py:282 produces non-UUID legacy document IDs. Management reader cannot reuse runtime defaulting or demand document UUIDs.
- json_index_cache.py:59 versioned_cache_path is pure naming but calls resolve; guard its parent before using it.
- source_inventory.py builds new SQLite artifacts and already validates owner/chunk completeness. Extension adds before-complete authority validation, rather than a post-completion wrapper check.
- source_json.py's bounded parser is moved once and imported by both readers, keeping aliases and limits.
- Baseline command: auth/history/user-storage/source-inventory tests, 71 passed in 14.85s.
- Existing dirty E3-A1/E3-A2 source, tests, dependency and documentation are prerequisites; modify only the listed additive seams, preserve all other changes/output.

## Findings

### Blocking

None.

### Required revisions

None. Full plan includes account namespace validation, sidecar refusal, source/destination confinement and callback failure lifecycle.

### Non-blocking notes

No production entry CLI. immutable source reads require the future maintenance controller to stop writers and checkpoint; double scans/stat checks are extra guards. Windows private ACL provisioning/verification is a controller gate, not claimed here. One JSON invocation covers one explicit runtime cache; all-cache enumeration remains a controller responsibility.

## Accepted scope

- Read-only streaming application ownership discovery, full receipt and revalidation.
- Ownership-aware inventory finalization and protected-layout path confinement.
- Source preservation, no sensitive history values copied into artifact, inactive users included.
- No original-document reparsing, auth/runtime changes, data migration, service calls, Windows operations, stable checkout or Git publication.

## Packet graph

| Packet | Depends on | Parallel-safe | Owned files | Outcome |
|---|---|---:|---|---|
| 01-app-source-authority.md | accepted E3-A2 source inventory | no | app/rag_authority.py, app/rag_inventory.py, source_stream.py, source_json.py parser move, source_inventory.py finalizer, new test, records/spec | authority-bound inventory |

## Packet readiness audit

| Packet | Goal/non-goals | Context/interfaces | Prerequisites | Boundary | Acceptance/tests | Forbidden changes | Handoff | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 01-app-source-authority.md | yes | yes | yes | yes | yes | yes | yes | yes |

## Integration verification

```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/test_rag_source_authority.py tests/memory/rag/test_source_inventory.py tests/memory/storage/test_qdrant_scan.py tests/test_auth_service.py tests/test_history_repository.py tests/test_user_storage.py -q --tb=short --junitxml=output/e3-a3-focused.xml
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory tests/test_auth_service.py tests/test_history_repository.py tests/test_user_storage.py tests/test_document_library_service.py tests/test_legacy_migration.py tests/test_legacy_migration_recovery.py tests/test_rag_source_authority.py -q --tb=short --junitxml=output/e3-a3-regression.xml
& 'D:/python_self_agent/venv/Scripts/python.exe' -m compileall -q app/rag_authority.py app/rag_inventory.py hello_agents/memory/rag/source_stream.py hello_agents/memory/rag/source_json.py hello_agents/memory/rag/source_inventory.py tests/test_rag_source_authority.py
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pip check
git diff --check
```

## Final integration review requirement

After packet 01 is done, inspect actual files/diff and record FINAL_INTEGRATION_REVIEW.md: interface/default/error agreement, parser relocation uniqueness, source and generator lifecycle, missing/corrupt/changed authority, no runtime data effects, additive artifact format and compatibility, combined tests. Verdict accepted/changes-required/blocked; use corrective packets for review findings.

## Open decisions

None for this increment. Inline execution was explicitly selected; no new agent/task or execution-choice prompt.

## Accepted compatibility amendment

First regression found source_json._MAX_VALUE_BYTES is an existing patched test seam. Preserve that constant and pass its value to the shared reader, whose optional max_value_bytes may only reduce the existing 8 MiB bound. No test weakening or owned-file expansion. Accepted; packet may resume after this amendment.

## Windows normalized-path acceptance refinement

Additional missing-leaf tests reproduce acceptance of ADS names, trailing-dot/space aliases and reserved devices. Require lexical Windows path rejection before filesystem checks; native isreserved with older-Python fallback. Five red cases added, no special file opened. Accepted within owned authority/test files and existing path confinement contract.

## Corrective packet 02 readiness

Final import audit found obsolete source_json.py hashlib import after parser relocation. 02-parser-import-cleanup.md is accepted/ready: one source import removal plus matching plan/review records, depends on done packet 01, no parallel work or new behavior. Existing focused/combined checks are proportionate verification; no new behavioral test needed for an unused import.
