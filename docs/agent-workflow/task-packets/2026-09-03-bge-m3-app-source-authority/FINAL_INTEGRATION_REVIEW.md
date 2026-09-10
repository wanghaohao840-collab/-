# Final Integration Review: BGE-M3 application source authority

- Source review: REVIEW.md (including parser-limit compatibility and Windows-path refinements).
- Reviewed worktree: a33b071, codex/bge-m3-runtime-identity, accepted uncommitted E3-A1/A2 plus this E3-A3 packet.
- Review date: 2026-09-03
- Result: accepted

## Delivered packet inventory

| Packet | Status | Commit | Owned files | Verification |
|---|---|---|---|---|
| bge-m3-app-source-authority-01 | done | uncommitted | app/rag_authority.py, app/rag_inventory.py, source_stream.py, source_json.py, source_inventory.py, test_rag_source_authority.py, records | 168 focused / 647 combined passed |
| bge-m3-app-source-authority-02 | done | uncommitted | source_json.py orphan import, records | 168 focused / 647 combined passed again |

## Combined diff reviewed

Actual complete contents were read and matched reviewed implementation listings; parser/finalization changes were compared with preserved pre-E3-A3 source snapshots. After packet done, source/path binding and completion/failure paths were inspected again. A subsequent import finding was corrected through packet 02, not an ad hoc review edit; its one-line change and consumers were re-reviewed after completion.

Added: two application management modules, one shared source parser, 52-case test module and this delivery's records.
Modified relative to E3-A2: source_json.py parser relocation/limit forwarding; source_inventory.py optional finalizer, authority_receipt and fingerprint domain, owner-iterator closure.
Excluded pre-existing work: all other E3-A1/A2 files/requirements/spec history/output, E2 runtime and stable checkout. Governing spec changes only describe this delivery's actual progress.

## Cross-packet interface audit

| Producer | Consumer | Contract | Result / evidence |
|---|---|---|---|
| app.db users + history.json | AppOwnershipSource | canonical account UUID/status; opaque valid document ID; full EOF receipt | pass, rag_authority.py:89; legacy/inactive and malformed/orphan tests |
| AppOwnershipSource.records | build_inventory owners iterator | namespace/document/user triples; no runtime initialization | pass, rag_inventory.py:43, :57; SQLite duplicate/missing ownership tests |
| second AppOwnershipSource scan | verify_owners callback | exact receipt after full source scan, before complete commit | pass, rag_inventory.py:45; source_inventory.py:202 |
| callback SHA receipt | inventory hash/inspection | authority bound into v2 fingerprint; invalid receipt/tampering rejects | pass, source_inventory.py:87, :233; receipt tests |
| BoundedJSONReader/read_json_value | JSON cache + history readers | 64 KiB reads, bounded values, same errors; no duplicate parser | pass, source_stream.py:12; original size-limit regression unchanged |
| versioned_cache_path | application source binding | exact guarded user legacy/managed cache location | pass, managed/legacy source tests |
| complete Qdrant source | application ownership wrapper | all namespaces/accounts including disabled; source reader remains read-only | pass, whole-collection adapter fixture + prior embedded source regression |

## Requirement coverage

- Authority from account/history records, not vector payload or original file paths: satisfied.
- Preserve historical IDs and source provenance, include inactive accounts and explicit never-initialized users: satisfied.
- Refuse missing/corrupt/orphan/contradictory/changed authority; no silent empty replacement: satisfied.
- Streaming read bounds; private fields not copied; SQLite on-disk uniqueness and no broad owner list: satisfied.
- Source/destination confinement, reparse ancestors, Windows ADS/device/alias refusal and no overwrite: satisfied.
- No successful inventory after incomplete owner/source scan or failed second authority scan: satisfied.
- Existing parser/runtime/data formats preserved; pre-release inventory v2 change explicit: satisfied.
- Offline tests and stable interpreter checks: satisfied.
- Full E3 production requirements: intentionally NOT claimed; controller, candidates, checkpoints and release gates remain below.

## Overlap and duplication audit

No conflicting file edits. One bounded parser implementation; source_json aliases retain test seam and forward the existing limit. No auth/history/runtime helper was reused in a way that creates defaults/data. No dependency change or competing application ownership store. Core remains independent of app: the app passes a callback, lower RAG does not import app.

## Architecture and invariant audit

- Source read path is independent of normal request/session runtime and does not open original documents.
- Accounts open mode=ro&immutable=1 only after journal/WAL/SHM refusal. Signature checks and second authority scan add detection; neither provides the maintenance lock. No source DB/file writes were observed in synthetic immutability tests.
- Consumer completion is transactional in the inventory, not an after-the-fact sidecar flag. Invalid callback receipts and changed owners roll back to failed/noncomplete.
- Existing low-level callers may omit verify_owners for isolated synthetic inventories. The future candidate/controller must require application authority-bound artifacts; do not assume every inspectable generic inventory is production-authorized.
- Scope is one explicit JSON source per invocation, or the entire explicit Qdrant collection. No all-cache enumeration claim.
- Missing original documents/old host paths are not used to invent or change ownership; chunk provenance remains exact. Ambiguous duplicate selected history document IDs fail rather than choosing a winner.
- New destination is exclusive-create only. No cleanup/deletion, overwrite or activation was performed.

## Combined verification

```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/test_rag_source_authority.py tests/memory/rag/test_source_inventory.py tests/memory/storage/test_qdrant_scan.py tests/test_auth_service.py tests/test_history_repository.py tests/test_user_storage.py -q --tb=short --junitxml=output/e3-a3-focused.xml
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory tests/test_auth_service.py tests/test_history_repository.py tests/test_user_storage.py tests/test_document_library_service.py tests/test_legacy_migration.py tests/test_legacy_migration_recovery.py tests/test_rag_source_authority.py -q --tb=short --junitxml=output/e3-a3-regression.xml
& 'D:/python_self_agent/venv/Scripts/python.exe' -m compileall -q app/rag_authority.py app/rag_inventory.py hello_agents/memory/rag/source_stream.py hello_agents/memory/rag/source_json.py hello_agents/memory/rag/source_inventory.py tests/test_rag_source_authority.py
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pip check
git diff --check
```

- Final focused rerun after packet 02: 168 passed in 16.36s, 0 failed/errors/skipped. XML output/e3-a3-focused.xml confirms 52 new cases.
- Final combined rerun after packet 02: 647 passed in 46.57s, 0 failed/errors/skipped. Covers Memory/RAG + auth/history/storage/library/legacy migration + new tests; XML output/e3-a3-regression.xml.
- Groups overlap; do not add their counts.
- Compile exit 0; pip check reports no broken requirements; git diff --check exit 0 with only Git line-ending notices.
- All module contents matched the reviewed code listings after the two documented refinements.
- Initial missing-module red, parser compatibility red and five Windows-path red cases are recorded in packet handoff; all final reruns pass.

## Findings

### Blocking

None for the accepted offline management-library increment.

### Changes required

None remaining. Corrective packet bge-m3-app-source-authority-02 removed the orphan hashlib import only. Shared reader hashing and source aliases remain intact; final reruns pass and packet 02 is done.

### Residual risks / mandatory release gates

1. Private Windows ACL provisioning/checking, operations.lock, App/import writer shutdown, checkpointed database and consistent cold backup remain controller responsibilities. Path checks cannot stop a malicious concurrent reparse swap; production must not use this library without controlled private maintenance conditions.
2. Controller must discover/select actual backend/profile, enumerate every JSON cache or complete Qdrant source, and repeat full chunk inventory/content comparison before reuse. Equal Qdrant counts or matching authority alone are insufficient source snapshots.
3. Pre-release unbound E3-A2 inventories must be regenerated. Candidate builder must require authority-bound v2 artifacts and preserve source changes/recovery semantics.
4. Candidate index creation, batching/rebuild/checkpoints, cutover journal/rollback, retrieval quality gate, live deep smoke and stable integration remain incomplete.
5. No production embeddings activated, original sources/keys read or changed, stable code merged, container/task started or Git changes committed/pushed.

## Decision

Accepted E3-A3: application-authoritative ownership is now wired into inventory acceptance and verified offline. This is not acceptance of the full embedding migration/release.

Final correction audit: both packets done; source_json has no local hashlib import, source_stream still owns SHA hashing, full reviewed source listing matches actual implementation. No additional corrective packets required.
