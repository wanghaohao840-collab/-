---
id: "bge-m3-source-inventory-01"
title: "Read-only disk-backed source inventory"
status: "done"
parallel-safe: false
depends-on: ["bge-m3-bounded-reader-01"]
base-commit: "a33b071"
owner: "Codex-inline"
---

# Task Packet: Read-only disk-backed source inventory

## Goal

Produce a new inspectable SQLite inventory only after a complete, validated JSON or Qdrant source scan. Expose per-authoritative-user/document counts, byte sizes, exact source and model-independent content digests without loading the whole source into memory.

## Non-goals

No actual production source scan, user/history enumeration, maintenance lock, candidate migration/re-embedding, registry activation, durable rebuild checkpoints, quality assessment, CLI, Windows operations or UI changes. Inventory completeness is conditional on a complete authority list provided by the management layer; no inference from vector data.

## Delivery context

BGE-M3 migration keeps existing chunks and provenance. Existing JSON loads whole files; Qdrant has the accepted E3-A1 bounded interface but not complete-source inventory. Source ownership is untrusted until checked against an explicit management-supplied (namespace, document_id, user_id) list. Existing history stores owner/document relationships, not reliable chunk counts, so this unit checks complete source counts, no missing owner partitions and contiguous chunk indexes. Actual source stability also needs maintenance locking and a complete rescan before reuse.

## Relevant files and current interfaces

- hello_agents/memory/rag/pipeline.py:966: legacy writer fields collection_name/rag_namespace/dimension/updated_at/chunk_count/chunks; chunk id/document_id/content/vector/metadata.
- hello_agents/memory/rag/json_index_cache.py:18: managed schema-2 envelope; identity is full IndexIdentity; chunks appear first under sorted serialization and header validation must not assume order.
- hello_agents/memory/rag/qdrant_pipeline.py:469: content/document/namespace/index/version/timestamps top-level; remaining fields nested under metadata.
- hello_agents/memory/rag/index_identity.py: IndexIdentity.from_dict/require_match and require_point_identity check exact identity.
- hello_agents/memory/rag/prepare.py: contains_secret_metadata guards nested sensitive keys.
- hello_agents/memory/storage/vector_store.py:612: additive scroll_page/iter_scroll_pages; existing require_collection and count; no writes needed.
- tests/memory/rag/test_json_index_cache.py and tests/memory/storage/test_qdrant_scan.py: regression and fixture contracts.
- Existing changes to preserve: E3-A1 code/tests/records/spec progress and output/. Do not edit or commit them as part of this packet.

## Prerequisites

### Packet dependencies

bge-m3-bounded-reader-01 is done and its final review accepted. Its implementation is uncommitted and present, so this packet is NOT parallel-safe.

### Repository/base state

a33b071 plus E3-A1, branch codex/bge-m3-runtime-identity at D:/python_self_agent/.worktrees/bge-m3-runtime-identity. Test conftest isolates real dotenv and database settings.

### External prerequisites

D:/python_self_agent/venv/Scripts/python.exe; install ijson==3.4.0.post0 only. No running services, credentials or production data.

## Explicit change boundary

### Allowed files

- Create: hello_agents/memory/rag/source_records.py
- Create: hello_agents/memory/rag/source_json.py
- Create: hello_agents/memory/rag/source_qdrant.py
- Create: hello_agents/memory/rag/source_inventory.py
- Create: tests/memory/rag/test_source_inventory.py
- Modify: requirements.txt — one pinned ijson line only.
- Records: docs/superpowers/plans/2026-09-03-bge-m3-source-inventory.md; this directory REVIEW.md, 01-source-inventory.md and FINAL_INTEGRATION_REVIEW.md.
- Progress only: docs/superpowers/specs/2026-09-03-bge-m3-rag-embedding-design.md.
- Synthetic verification output: output/e3-a2-focused.xml and output/e3-a2-memory.xml, untracked.

### Allowed behavior changes

Add isolated source reader/validation/inventory library APIs and fixed parser dependency. Source data and existing runtime behavior remain unchanged.

### Forbidden changes

Stable checkout, E3-A1 behavior, real .env/keys/data/collections, provider/model/Memory configuration, cache formats, application ownership/history logic, Windows tasks/containers, active registry, old indexes, unrelated output. No source deletion, overwrite, merge, commit or push.

## Interface contract

### Consumes

QdrantVectorStore.require_collection(collection, dimension), count(collection), iter_scroll_pages(collection, page_size=128, max_pages=10000) -> pages with VectorScanRecord; IndexIdentity and require_point_identity; existing nested secret-key validation; ijson.basic_parse(binary read(size), use_float=True, buf_size=65536).

### Produces

- SourceChunk(storage_id, chunk_id, namespace, document_id, content, metadata); safe SourceInventoryError(code).
- JsonChunkSource(path, *, collection, namespace, dimension, identity=None); QdrantChunkSource(store, *, collection, dimension, identity=None, page_size=128, max_pages=10000).
- Sources expose records() generator, contract dict including backend/collection/namespace/dimension/source_identity, identity, and completed_token only on successful EOF.
- build_inventory(path, *, source, owners) -> InventorySummary(count, partitions, fingerprint). Source is a supported adapter, not arbitrary iterator.
- inspect_inventory(path, *, expected=None) -> verified summary; iter_partitions(path, *, expected) -> PartitionSummary(namespace, document_id, user_id, count, content_bytes, source_digest, content_digest).

### Invariants

Source is untouched. New destination exclusive-create only. No completed inventory after partial/failed input. No scalar coercion of logical IDs/versions; integer/UUID native physical IDs preserved. No unknown ownership or mixed document versions. Rebuild can compare content digests excluding physical ID and embedding fingerprint while retaining exact source digest separately.

## Required behavior

- Both JSON envelopes in any key order; exact fields/count/namespace/dimension/identity/time validation at EOF; duplicate keys (including nested), malformed/trailing input, invalid vectors, zero vectors and wrong metadata refused.
- 64 KiB reads, 8 MiB input budget between values, depth 32, 100,000 nodes/value, 2 MiB canonical record. No entire-file string or collection list.
- Source stat signature checked before/after including opened handle; full SHA receipt only after successful exhaustion. Closing early leaves no receipt. Concurrent iteration on one source is refused.
- Qdrant uses require_collection (no auto-create), bounded full pages and before/after exact count; passes only if full scanned count agrees. Same-count content races require caller full rescan under maintenance later.
- Flatten Qdrant metadata only when duplicate keys agree; require content/id/namespace/doc consistency, valid version/index and identity; reject nested secrets.
- SQLite ownership/physical/logical/index uniqueness; one owner per namespace, no missing listed documents, contiguous indexes starting 0, no mixed versions.
- Artifact lifecycle building -> complete on success, failed or noncomplete on interruption. SQL errors sanitized, original transport/source errors retain typed handling; input generator and DB close.
- Source/partition digests deterministic for row order; preserve full content/source metadata. Read-only inspect compares expected fingerprint and rejects changed source/corrupt/incomplete artifact.
- Source descriptor/digests never contain keys; private chunk records stay only in private artifact, not logs.

## Implementation guidance

Use complete code listings in the source plan, validated against the repository interfaces above. Write new tests and confirm missing-module red; then pin/install parser and create four modules. Keep reader completion separate from a successful page, and keep ownership input outside raw source conversion. Run focused then all memory tests before handoff. Test-only synthetic files are created in pytest temporary directories.

## Acceptance criteria

- [x] Legacy and managed JSON stream correctly with intact IDs/provenance, bounded reads and validated EOF.
- [x] Qdrant complete bounded scans, counts and scope/metadata/native IDs validated.
- [x] SQLite deduplicates on disk, rejects unknown/missing owner partitions, gaps and mixed versions.
- [x] Restart inspection and per-partition source/content digests work; changed source, partial scan, parser failure, corruption and destination overwrite are refused.
- [x] All listed tests and compile/dependency/diff checks pass; existing source/runtime remains unchanged.

## Test and verification commands

From isolated worktree, after writing tests only:
```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory/rag/test_source_inventory.py -q --tb=short
```
Expected missing source_inventory module. Then install pinned dependency and implement:
```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pip install --disable-pip-version-check ijson==3.4.0.post0
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory/rag/test_source_inventory.py tests/memory/storage/test_qdrant_scan.py tests/memory/rag/test_json_index_cache.py -q --tb=short --junitxml=output/e3-a2-focused.xml
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory -q --tb=short --junitxml=output/e3-a2-memory.xml
& 'D:/python_self_agent/venv/Scripts/python.exe' -m compileall -q hello_agents/memory/rag tests/memory/rag
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pip check
git diff --check
```
Expected all applicable offline tests pass, checks exit 0. Inspect new untracked files directly as ordinary git diff omits them.

## Stop conditions

If verified source formats/interfaces no longer match, dependency unavailable, acceptance cannot be proven, pre-existing changes overlap, or more files/authority are needed, append a reality-conflict report and revise this packet/review before proceeding. Do not silently migrate, infer ownership or expand scope.

## Implementation handoff

- Packet: bge-m3-source-inventory-01.
- Status: done.
- Delivered: complete read-only source adapters and disk-backed inventory core; no production migration.
- Files changed:
  - source_records.py: canonical chunk/metadata/identity validation and independent logical/physical IDs.
  - source_json.py: legacy/schema-2 streaming, bounded parsing, safe EOF/header validation and file-change checks.
  - source_qdrant.py: complete bounded scan and count receipt using E3-A1, no source writes.
  - source_inventory.py: exclusive new SQLite artifacts, ownership/uniqueness checks, partition source/content digests and read-only inspection.
  - tests/memory/rag/test_source_inventory.py: 47 parameterized cases including embedded Qdrant and Windows stat regression.
  - requirements.txt: ijson==3.4.0.post0 only; installed successfully into existing venv without upgrading other dependencies.
  - This plan/packet/review/final review and governing spec E3 progress.
- Interfaces: SourceChunk, JsonChunkSource, QdrantChunkSource, build_inventory, inspect_inventory, iter_partitions and summary types match packet contracts.
- Acceptance evidence:
  - [x] JSON preserves content/metadata and native stream bounds; managed identity validated even after chunks.
  - [x] Qdrant embedded test uses actual existing adapter and leaves source points unchanged.
  - [x] Ownership, cross-page UUID alias/ID/index duplicate, gap, mixed version and partial source guards pass.
  - [x] Reopen/read-only inspection, changed-source comparison, interrupt/disk failure and source immutability pass.
  - [x] Model identity changes source digest while identical content/provenance retains content digest.
  - [x] Focused and combined regression plus compile/dependency/diff checks pass.
- Verification from isolated root:
```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory/rag/test_source_inventory.py tests/memory/storage/test_qdrant_scan.py tests/memory/rag/test_json_index_cache.py -q --tb=short --junitxml=output/e3-a2-focused.xml
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory -q --tb=short --junitxml=output/e3-a2-memory.xml
& 'D:/python_self_agent/venv/Scripts/python.exe' -m compileall -q hello_agents/memory/rag tests/memory/rag
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pip check
git diff --check
```
  - Focused: 114 passed in 11.78s, output/e3-a2-focused.xml.
  - Memory: 503 passed in 15.25s, output/e3-a2-memory.xml. Groups overlap.
  - Compile/dependency/diff: exit 0; pip reports No broken requirements found.
  - Initial red: expected missing source_inventory module, one collection error.
  - Initial implementation run: 102 passed, 6 failed because Windows stat/fstat ctime comparison was cross-API. Fixed with same-API before/after comparison and a deterministic regression; all reruns above pass.
- Scope confirmation: only owned files changed; E3-A1/stable checkout/runtime/provider/data/containers/tasks untouched. No live source, LLM or embedding calls; embedded client uses synthetic records.
- Deviations: none in behavior/scope. Serial inline implementation is the user-selected fallback; no agent dispatch.
- Residual follow-ups:
  - Management layer must supply complete authoritative account/document ownership, hold the maintenance lock and rescan/compare before use; this core does not discover owners.
  - Qdrant same-count races require full content comparison under maintenance, not count checks alone.
  - SQLite artifacts contain private content; eventual controller must place them in protected App data and backups, not Git/output logs.
  - Candidate manifest/rebuild/checkpoints, maintenance/cutover journal/rollback, quality tests and actual deep smoke/stable integration remain pending.
- Commit: not committed; not pushed.
