---
id: "bge-m3-bounded-reader-01"
title: "Bounded Qdrant source reader"
status: "done"
parallel-safe: false
depends-on: []
base-commit: "a33b071"
owner: "Codex-inline"
---

# Task Packet: Bounded Qdrant source reader

## Goal

Add independently usable read-only payload page methods to QdrantVectorStore. A caller can read one bounded page or lazily traverse pages, stop consumption, and resume using the native next offset without materializing the collection.

## Non-goals

No JSON adapter, inventory acceptance, re-embedding, production writes, cutover, model enablement, maintenance operations or dependency changes. Do not replace legacy scroll.

## Delivery context

BGE-M3 migration must retain original content and provenance and eventually prove complete per-scope digests. Existing scroll accumulates a collection in memory and cannot support bounded recovery. This packet supplies only its Qdrant transport prerequisite; durable checkpoints, ownership and source stability are subsequent serial units. A partial scan must never be treated as a valid inventory.

## Relevant files and current interfaces

- `hello_agents/memory/storage/vector_store.py:399`: QdrantVectorStore owns client/model conversion, `_call(operation, func, *args, **kwargs)` retries/error mapping and `_filter(filters)`.
- `hello_agents/memory/storage/vector_store.py:605`: existing scroll returns list[VectorPoint]; preserve it and its callers.
- `hello_agents/memory/storage/vector_store.py:53`: VectorPoint combines logical ID/vector/payload; do not reuse it for physical migration identity.
- `hello_agents/memory/rag/errors.py`: RAGOperationError accepts operation/retryable; transport mapping already sanitizes keys.
- `tests/memory/storage/test_qdrant_vector_store.py`: injectable fake client plus retry_delays=() seam.
- `tests/memory/storage/test_vector_store_contract.py`: protocol compatibility.
- `tests/memory/rag/test_managed_qdrant_pipeline.py`: existing managed consumers.
- Existing changes to preserve: output/ reports; stable checkout unrelated changes; new accepted plan.

## Prerequisites

### Packet dependencies

None; E2 is completed in the base commit.

### Repository/base state

Base a33b071, branch codex/bge-m3-runtime-identity, worktree D:/python_self_agent/.worktrees/bge-m3-runtime-identity. Existing test fixture prevents real dotenv and service configuration from loading.

### External prerequisites

Existing D:/python_self_agent/venv/Scripts/python.exe with pytest and qdrant-client 1.18.0. No service, credentials or network needed.

## Explicit change boundary

### Allowed files

- Create: `hello_agents/memory/storage/vector_scan.py`.
- Modify: `hello_agents/memory/storage/vector_store.py` imports and two Qdrant-only methods.
- Test: `tests/memory/storage/test_qdrant_scan.py`.
- Delivery records: `docs/superpowers/plans/2026-09-03-bge-m3-bounded-source-reader.md`; `docs/agent-workflow/task-packets/2026-09-03-bge-m3-bounded-source-reader/REVIEW.md`; this packet; `docs/agent-workflow/task-packets/2026-09-03-bge-m3-bounded-source-reader/FINAL_INTEGRATION_REVIEW.md`.
- Progress only: `docs/superpowers/specs/2026-09-03-bge-m3-rag-embedding-design.md`.
- Generated evidence only: output/e3-a1-focused.xml and output/e3-a1-memory.xml (not committed).

### Allowed behavior changes

Add scroll_page and iter_scroll_pages to the Qdrant implementation only. Introduce safe scan errors/types/helpers local to storage.

### Forbidden changes

Stable checkout, real env/keys, providers, Memory model configuration, runtime registry, collection schemas, existing retrieval and VectorStore protocol, existing scroll, Windows scheduled tasks, live containers/databases and unrelated output. No production activation, Neo4j start, commit or push.

## Interface contract

### Consumes

client.scroll(collection_name, scroll_filter, offset, limit, with_payload=True, with_vectors=False) returns (records, next_offset); each record has id and dict payload. Use _call("scroll", client.scroll, ...) and _filter(filters), never raw HTTP.

### Produces

- NativePointId = int | str, only unsigned 64-bit integers (not bool) or UUIDs. UUID objects become strings; integer 0 stays int.
- Frozen VectorScanRecord(storage_id, logical_id, payload) and VectorScanPage(records: tuple, next_offset).
- VectorScanError(code): RAGOperationError(operation="scroll", retryable=False), stable code only, no input/payload in message.
- scroll_page(collection_name, filters=None, *, offset=None, page_size=128) -> VectorScanPage.
- iter_scroll_pages(collection_name, filters=None, *, offset=None, page_size=128, max_pages=10000) -> Iterator[VectorScanPage].

### Invariants

Existing APIs and data unchanged. One successful page request unless existing bounded transport retry applies. No auto-create/delete/upsert. Scope predicates copied at first iteration and forwarded on every page. Payload copies independent of SDK objects. Native physical and logical IDs are not interchangeable.

## Required behavior

- Exact-int page size 1..256; exact-int page budget 1..1,000,000. Reject invalid input before remote scroll.
- Validate response shape, page length, record IDs, payload dict and logical ID (nonempty string <=512, no ASCII control characters). Fall back to physical string ID only when logical key is absent.
- Reject duplicate physical IDs within a page and repeated/cyclic continuation cursors, canonicalizing UUID variants for comparisons.
- Reject empty pages with a continuation and unchanged offset. Terminal empty page is allowed.
- Iterator is lazy, does not read ahead, stops when closed, resumes from native cursor. Reaching budget with continuation raises page_limit rather than silently completing.
- Transport failure mid-scan propagates typed sanitized error, never converts to EOF.
- No claim of snapshot, bounded bytes, durable recovery or verified ownership/full inventory.

## Implementation guidance

Write tests first using fake scroll responses, then confirm missing-module red state. Add the small typed/helper module and two adapter methods before Qdrant's existing scroll (not protocol or in-memory implementation). Validate integer zero, UUID conversions, page/cursor limits, scope-copy, cancellation/resume, response corruption and transport failures. Embedded Qdrant test creates only synthetic in-memory data, paginates one namespace and checks total count unchanged. Use the accepted plan's complete source listings as implementation guidance.

## Acceptance criteria

- [x] Page interface returns payload-only bounded pages, separate IDs and safe native cursor, proven by fake and embedded-client tests.
- [x] Lazy cancellation/resume, frozen scope, finite traversal and all malformed-response guards pass.
- [x] Errors preserve typed transport handling and redact credentials.
- [x] Legacy protocol/scroll/search/upsert unchanged; focused and memory regression pass without live services.
- [x] Compile, dependency and diff checks pass; progress clearly limited to E3-A1.

## Test and verification commands

Run in the isolated worktree. First, after writing tests only:

```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory/storage/test_qdrant_scan.py -q --tb=short
```

Expected: missing vector_scan module (red). Then implement and run:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest tests/memory/storage/test_qdrant_scan.py tests/memory/storage/test_qdrant_vector_store.py tests/memory/storage/test_vector_store_contract.py tests/memory/rag/test_managed_qdrant_pipeline.py -q --tb=short --junitxml=output/e3-a1-focused.xml
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest tests/memory -q --tb=short --junitxml=output/e3-a1-memory.xml
& 'D:\python_self_agent\venv\Scripts\python.exe' -m compileall -q hello_agents/memory/storage tests/memory/storage
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pip check
git diff --check
```

Expected: all applicable tests pass, live-service opt-in tests skipped; compile/pip/diff checks exit 0. Inspect git diff for only additive methods/imports, and new files directly because untracked files are not in git diff.

## Stop conditions

Report blocked and append the repository reality-conflict format if base interfaces differ, tests cannot demonstrate acceptance, an owned-file boundary is insufficient, existing work overlaps, or a dependency is missing. Revise review/packet before resuming, not hidden scope expansion.

## Implementation handoff

- Packet: bge-m3-bounded-reader-01.
- Status: done.
- Delivered: bounded read-only Qdrant payload pages and finite lazy traversal with native cursor handoff.
- Files changed:
  - `hello_agents/memory/storage/vector_scan.py`: typed page records, native-ID/response validation, finite cycle-safe traversal.
  - `hello_agents/memory/storage/vector_store.py`: imports plus scroll_page/iter_scroll_pages only.
  - `tests/memory/storage/test_qdrant_scan.py`: fake transport, malformed input/response and embedded-client coverage.
  - This packet, REVIEW.md and the source plan: reviewed scope, complete test guidance, delivery evidence.
  - Governing specification: E3-A1 progress only, recorded during final review.
- Interfaces added: NativePointId, VectorScanError, VectorScanRecord, VectorScanPage, scroll_page and iter_scroll_pages exactly as specified above; no legacy interface changed.
- Acceptance evidence:
  - [x] Bounded payload pages and separate identities: fake-client requests plus embedded seven-record/two-scope round trip.
  - [x] Lazy stop/resume, frozen scope, finite page budget, UUID-equivalent cycles and malformed payload/logical IDs: new test module.
  - [x] Transport errors and retry compatibility: timeout, unchanged retry cursor/scope, authentication redaction.
  - [x] Compatibility: 74 focused and 456 memory tests pass (overlapping groups, not additive).
  - [x] Compile, pip check and diff checks exit 0.
- Verification (worktree root):
```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest tests/memory/storage/test_qdrant_scan.py tests/memory/storage/test_qdrant_vector_store.py tests/memory/storage/test_vector_store_contract.py tests/memory/rag/test_managed_qdrant_pipeline.py -q --tb=short --junitxml=output/e3-a1-focused.xml
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest tests/memory -q --tb=short --junitxml=output/e3-a1-memory.xml
& 'D:\python_self_agent\venv\Scripts\python.exe' -m compileall -q hello_agents/memory/storage tests/memory/storage
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pip check
git diff --check
```
  - Focused: 74 passed in 2.17s; output/e3-a1-focused.xml.
  - Memory: 456 passed in 9.61s; output/e3-a1-memory.xml.
  - Compile: exit 0. Dependency check: No broken requirements found. Diff: no whitespace errors.
  - Red check before module creation: expected ModuleNotFoundError, one collection error in 1.56s.
- Scope confirmation:
  - Changed only allowed files: yes.
  - Forbidden areas untouched: yes; no real provider/service calls, container/task changes, stable merge or model activation.
- Deviations:
  - User-selected inline serial implementation, not delegated to Claude Code. Added seven parameterized cases for existing UUID, integer limit, retry and invalid-ID requirements; no behavior/scope expansion.
- Residual risks/follow-ups:
  - Cursor handoff alone is not durable migration recovery. Inventory ownership, cross-page duplicate/content digest validation and source stability are pending E3-A2.
  - Payload count is bounded, not malicious payload bytes; iteration is not a snapshot.
  - E3 candidate rebuild, maintenance/recovery/cutover, quality evaluation and production deep smoke remain incomplete.
- Commit: not committed; no commit/push requested.
