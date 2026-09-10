---
id: "bge-m3-app-source-authority-01"
title: "Authority-bound application source inventory"
status: "done"
parallel-safe: false
depends-on: ["bge-m3-source-inventory-01"]
base-commit: "a33b071"
owner: "Codex-inline"
---

# Task Packet: Authority-bound application source inventory

## Goal

Create a verifiable source inventory only when application account/document authority is complete and unchanged through a second scan, with the authority receipt bound into the persisted fingerprint.

## Non-goals

No production source scan, command-line deployment tool, maintenance lock/ACL provisioning, candidate embeddings/rebuild/checkpoints, registry cutover, Windows task or UI changes. No source changes, new containers or Neo4j startup. JSON invocation covers one explicit cache, not fleet enumeration.

## Delivery context and verified files

RAG source payloads are not sufficient proof of ownership. app.db users and each users/{canonical UUID}/history.json are the authority. Runtime app/database.py:368 creates files/parents, HistoryRepository.load supplies defaults, UserRuntimeRegistry.get_or_create initializes directories/recovery: do not call these in management discovery. app/runtime.py names namespaces pdf_{user_id}. app/migration.py:282 emits 16-character document IDs; retain valid legacy document strings. History has no authoritative chunk count; source_inventory.py checks complete source counts/contiguous chunks and missing owner partitions.

source_json.py already provides 64 KiB streaming with 8 MiB input budget, 32 nesting depth/100,000 nodes and canonical 2 MiB values. Move the parser into source_stream.py once, with aliases for old private test seams. json_index_cache.py:59 versioned_cache_path supplies the exact managed file name after guarding its parent. source_inventory.py uses SQLite uniqueness and complete/failed states; add authority verification BEFORE complete/commit.

## Prerequisites

- E3-A1 and E3-A2 done/accepted, present but uncommitted.
- a33b071, branch codex/bge-m3-runtime-identity, isolated directory D:/python_self_agent/.worktrees/bge-m3-runtime-identity.
- Existing stable interpreter D:/python_self_agent/venv/Scripts/python.exe and installed ijson==3.4.0.post0. No external services or environment credentials.
- Preserve all other dirty source/docs/requirements/output changes; no E2 runtime redesign.

## Explicit change boundary

### Allowed files

Create app/rag_authority.py, app/rag_inventory.py, hello_agents/memory/rag/source_stream.py, tests/test_rag_source_authority.py.
Modify hello_agents/memory/rag/source_json.py ONLY parser relocation; hello_agents/memory/rag/source_inventory.py ONLY authority callback/fingerprint/iterator closure.
Records: docs/superpowers/plans/2026-09-03-bge-m3-app-source-authority.md; docs/agent-workflow/task-packets/2026-09-03-bge-m3-app-source-authority/REVIEW.md, 01-app-source-authority.md, FINAL_INTEGRATION_REVIEW.md; governing embedding spec progress. Test reports output/e3-a3-focused.xml and output/e3-a3-regression.xml.

### Allowed behavior changes

Add application-owned management library and optional inventory finalization callback; source artifacts become pre-release v2 fingerprints with authority_receipt. Old pre-release inventory artifacts must be regenerated, not silently treated as authority-bound. Runtime caches/app DB schemas stay unchanged.

### Forbidden changes

Stable checkout, real .env/keys/data/collections, source auth/history/documents, application runtime behavior, LLM/embedding network, dependency versions, containers/tasks, old indexes, Git commits/push/merge.

## Interface contract

Consumes SQLite users(id,status), filesystem users/{id}/history.json document_id and optional user_id, existing JsonChunkSource/QdrantChunkSource, versioned_cache_path, SourceInventoryError, build_inventory.

Produces:
- AppOwnershipSource(data_root, *, namespace=None).records() -> generator of (namespace, document_id, user_id); completed_token set only at successful EOF, overlap refused.
- checked_path(value, *, directory=False, missing=False) -> absolute confined Path or safe SourceInventoryError.
- build_app_inventory(data_root, path, *, source) -> InventorySummary.
- build_inventory(path, *, source, owners, verify_owners: Callable[[],str] | None=None). Callback runs after full source/partition checks, validates lowercase SHA-256 and binds authority_receipt to source-inventory-v2 fingerprint. Source and owner generators close on failure.
- BoundedJSONReader/read_json_value shared by both streaming readers; aliases preserve existing source_json test seams.

## Required behavior and invariants

- Include all accounts/statuses, not just active. Canonical user UUID, nonblank status. No directory for known account means uninitialized; existing directory with missing history is an error. Unknown directory/account association fails.
- Never infer owners from chunk metadata, file name, original document path or a guessed default. Historical non-UUID document IDs remain valid. Original files are not reopened.
- Stream histories itemwise. Required documents list, optional questions/notes/sessions lists; duplicate/unknown root fields or malformed values refuse. Optional record user_id must agree. Selected-scope duplicate document IDs rejected by inventory SQLite, no arbitrary winner.
- Read accounts mode=ro&immutable=1; reject app.db-wal/-shm/-journal even when empty. Require quiescent source, check DB/path signatures before/after, rescan ALL accounts/histories before finalization even for selected JSON namespace.
- No link/reparse components/ancestors. Destination must be a NEW direct child .sqlite of pre-existing data_root/vector_indexes/rag/inventories. Source JSON path equals the selected account's legacy/versioned runtime cache. Never create user directories or default histories.
- Identity/profile/source chunk and count checks remain intact. Source errors and authority errors leave failed or otherwise noncomplete artifact; no accepted inventory after partial enumeration.
- Receipt includes all account statuses/data-directory presence and complete history file hashes. Only ownership triples and receipt are stored; passwords/QA/notes not copied or logged. Returned errors redact paths and private values.
- Path confinement does not guarantee private Windows ACL or cross-file atomicity. Future controller must provision/check permissions, acquire operations.lock, stop writers, select current backend, enumerate every JSON cache and rescan chunk content before recovery/cutover.

## Implementation guidance

Use full code listings in the reviewed same-name plan. Write exact tests first, prove missing-module red, then create modules/update two described seams. No speculative CLI or dependency change. Retain the existing stat/fstat Windows ctime distinction. Close nested history generators explicitly.

## Acceptance criteria

- [x] Synthetic multi-user/inactive/legacy-ID authority scan and empty never-initialized accounts work without source writes.
- [x] Missing/corrupt/orphan/unsafe/conflicting authority and journal sidecars fail closed with safe errors.
- [x] JSON legacy/managed path binding, Qdrant all-owner integration and cross-user exclusion pass.
- [x] Authority change between owner/chunk scan or during enumeration prevents completed artifact; receipt tampering and duplicate/missing owners fail.
- [x] Source parser regression, bounded large history reads, generator closure, sensitive-data minimization and all listed combined checks pass.

## Test and verification commands

Initial red:
```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/test_rag_source_authority.py -q --tb=short
```
Expected missing app.rag_authority collection error. After implementation:
```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/test_rag_source_authority.py tests/memory/rag/test_source_inventory.py tests/memory/storage/test_qdrant_scan.py tests/test_auth_service.py tests/test_history_repository.py tests/test_user_storage.py -q --tb=short --junitxml=output/e3-a3-focused.xml
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory tests/test_auth_service.py tests/test_history_repository.py tests/test_user_storage.py tests/test_document_library_service.py tests/test_legacy_migration.py tests/test_legacy_migration_recovery.py tests/test_rag_source_authority.py -q --tb=short --junitxml=output/e3-a3-regression.xml
& 'D:/python_self_agent/venv/Scripts/python.exe' -m compileall -q app/rag_authority.py app/rag_inventory.py hello_agents/memory/rag/source_stream.py hello_agents/memory/rag/source_json.py hello_agents/memory/rag/source_inventory.py tests/test_rag_source_authority.py
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pip check
git diff --check
```
Expected applicable offline tests pass and compile/pip/diff checks exit 0. Review untracked files explicitly.

## Stop conditions

Mismatch with actual schema/paths, missing E3 prerequisites, unsafe data access, unavailable parser, conflicting file ownership, required files/authority outside boundary or failing acceptance requiring changed behavior must be reported as reality-conflict and resolved by revising review/packet, not silent scope expansion.

## Implementation handoff format

On completion record packet ID/status, exact files and interface changes, checkbox evidence, exact command results/counts, source/stable/runtime scope confirmation, deviations/residual gates and not-committed status. Final combined integration review follows separately.

## Reality-conflict report and bounded resolution

- Packet: bge-m3-app-source-authority-01; temporarily blocked during first regression.
- Expected: parser relocation retains existing private test seams with no behavior change.
- Observed: tests/memory/rag/test_source_inventory.py:260 patches source_json._MAX_VALUE_BYTES; moved parser no longer exposes that module constant. First run: 162 passed, 1 failed.
- Impact: old parser-limit regression cannot exercise the preserved source entry.
- Work completed: planned library/code/tests applied; new authority tests pass.
- Resolution: keep source_json._MAX_VALUE_BYTES and pass it explicitly to BoundedJSONReader's bounded optional max_value_bytes argument. Default remains 8 MiB, adjustable only downward; alias remains the same shared class. No old tests or unrelated files changed.
- Decision: Codex plan review accepts this within the existing owned-file compatibility requirement. Resume inline after revising plan/review; no new product choice/authority required.

## Windows path verification refinement

Within the normalized-path requirement, reject ADS names, trailing-dot/space aliases and Windows reserved devices before lstat, including missing destination leaves. Five synthetic red cases reproduce this guard gap; use os.path.isreserved when available and PureWindowsPath fallback. No additional file ownership or production action required.

## Implementation handoff

- Packet: bge-m3-app-source-authority-01.
- Status: done.
- Delivered: offline streaming authoritative ownership and authority-bound inventory completion, verified with synthetic data.
- Files:
  - app/rag_authority.py: read-only account/history scan, canonical account namespaces, source change/sidecar/orphan detection, Windows path guards and complete authority receipt.
  - app/rag_inventory.py: protected-layout source/destination binding and second authority scan before finalization.
  - hello_agents/memory/rag/source_stream.py: one shared bounded event parser with optional downward-only input limit.
  - hello_agents/memory/rag/source_json.py: imports shared parser and preserves old limit-test seam; no cache schema/runtime changes.
  - hello_agents/memory/rag/source_inventory.py: optional verify_owners callback, receipt-bound v2 artifact fingerprint and owner-generator closure.
  - tests/test_rag_source_authority.py: 52 parameterized cases.
  - Plan/review/packet/final review and governing spec progress; two synthetic XML reports.
- Interfaces: AppOwnershipSource.records/completed_token; build_app_inventory; checked_path; BoundedJSONReader/read_json_value; additive build_inventory verify_owners callback exactly as reviewed.
- Acceptance evidence:
  - [x] All account statuses and valid legacy document IDs preserved; existing source bytes and file set unchanged.
  - [x] Corruption, missing authority, orphan accounts/directories, journals and reparse ancestors reject with safe errors.
  - [x] Legacy/managed JSON runtime path binding and whole-collection Qdrant owner mapping pass.
  - [x] Mid-scan and pre-finalization authority changes prevent complete state; receipt tampering, duplicates and missing partitions reject.
  - [x] Bounded large-history reads, no copied private values, owner/stream closure, Windows alias/device/ADS rejection and prior parser-limit regression pass.
- Verification (isolated root):
```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/test_rag_source_authority.py tests/memory/rag/test_source_inventory.py tests/memory/storage/test_qdrant_scan.py tests/test_auth_service.py tests/test_history_repository.py tests/test_user_storage.py -q --tb=short --junitxml=output/e3-a3-focused.xml
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory tests/test_auth_service.py tests/test_history_repository.py tests/test_user_storage.py tests/test_document_library_service.py tests/test_legacy_migration.py tests/test_legacy_migration_recovery.py tests/test_rag_source_authority.py -q --tb=short --junitxml=output/e3-a3-regression.xml
& 'D:/python_self_agent/venv/Scripts/python.exe' -m compileall -q app/rag_authority.py app/rag_inventory.py hello_agents/memory/rag/source_stream.py hello_agents/memory/rag/source_json.py hello_agents/memory/rag/source_inventory.py tests/test_rag_source_authority.py
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pip check
git diff --check
```
  - Focused: 168 passed in 16.28s, no failures/errors/skips, output/e3-a3-focused.xml.
  - Combined: 647 passed in 44.60s, no failures/errors/skips, output/e3-a3-regression.xml. Groups overlap.
  - Compile: exit 0. pip check: No broken requirements found. git diff --check: exit 0 (line-ending conversion notices only).
  - Initial missing-module red: expected app.rag_authority collection error.
  - Initial regression: 162 passed/1 failed, old parser-limit seam; preserved without editing old tests and reran successfully.
  - Additional Windows-path red: 5 cases reproduced missing lexical rejection; all pass after scoped protection.
- Scope confirmation: only permitted isolated files changed. No .env/credentials/live data scanned, embedding/LLM calls, real database services, production configuration/indexes, containers/Neo4j, Windows tasks, stable integration or Git publication.
- Deviations: compatible parser limit plumbing and Windows normalized-path refinements documented/reviewed above; no feature-scope expansion.
- Residual follow-ups:
  - Controller must provision/verify private ACLs, hold operations.lock, stop/checkpoint source writers and orchestrate backup. Two scans/stat checks cannot replace this.
  - JSON wrapper covers one explicit user cache. Controller must enumerate all runtime-selected JSON sources, or the complete selected Qdrant collection, and validate active backend/profile.
  - Inventory v2 is pre-release; regenerate earlier unbound E3-A2 artifacts. Candidate builder must require application authority binding and full source rescan before reuse.
  - Candidate creation/batched embeddings/checkpoints, cutover journal/rollback, quality gate/deep smoke/stable integration remain incomplete.
- Commit: not committed; not pushed.
