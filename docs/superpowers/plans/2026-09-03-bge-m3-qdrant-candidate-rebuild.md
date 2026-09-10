# BGE-M3 Recoverable Qdrant Candidate Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use the existing user-selected inline execution fallback; executing-plans is unavailable. Steps use checkbox syntax.

**Goal:** Rebuild an authority-bound inventory into a resumable disk candidate and idempotently publish it only to a new Qdrant physical collection.

**Architecture:** Extend the verified inventory with a read-only ordered chunk iterator. A new candidate SQLite file stores identity, source fingerprint, per-row vectors and an ordered checkpoint transactionally. Each invocation revalidates the live source through a required callback; completed vectors are reused. Qdrant publication creates/requires only the target identity collection, upserts deterministic logical IDs in bounded batches and verifies exact count.

**Tech Stack:** Python, SQLite, existing RAGEmbeddingRuntime/IndexIdentity/VectorStore, pytest.

## Global constraints

- Source inventory must be authority-bound; generic E3-A2 inventories are refused.
- No real source, key, network, container, registry activation, deletion, cutover, JSON final publication or production change.
- Preserve content, logical IDs, namespace, document ID/version and provenance. Replace only embedding_fingerprint.
- Batch size is 1–8 and checkpoint writes are transactional. Resume requires exact inventory summary, target identity and live-source verification.
- New files are exclusive-create; existing checkpoint is opened only as the same migration. Target collection must be empty on first publish; resumes are idempotent.
- Existing isolated worktree a33b071 plus accepted uncommitted E3-A1/A2/A3; no commit/push/merge.

### Task 1: Ordered inventory chunks

Modify source_inventory.py and its test. Add iter_chunks(path, expected, require_authority=False), yielding validated SourceChunk ordered by namespace/document/chunk_index/chunk_id from one read-only snapshot. When require_authority is true, authority_receipt must be lowercase SHA-256. Existing APIs remain compatible.

### Task 2: Recoverable Qdrant candidate

Create candidate_rebuild.py and its test.

Interfaces:
- CandidateSummary(migration_id, state, embedded_count, published_count, total_count, fingerprint).
- build_candidate(path, migration_id, inventory_path, inventory, identity, embedding, verify_live_source, batch_size=8).
- publish_qdrant_candidate(path, expected, identity, store, batch_size=100).
- inspect_candidate(path, expected=None).

Behavior: validate exact migration/identity/profile/source receipt; exclusive-create a FULL-synchronous SQLite checkpoint; reread and digest-check all prior ordered rows; embed only missing consecutive batches; store record/vector/checkpoint atomically; require a second live verification before embedded. Publication creates/requires only the new derived physical collection, refuses preexisting nonempty first target, upserts bounded deterministic logical IDs with preserved metadata and replaced target fingerprint, verifies exact count after each batch, and resumes idempotently. Never delete data; failures retain recoverable state and safe error codes.

Tests cover build/batching, interruption/resume without repeated embedding, mismatches, authority refusal, corruption, provenance, nonempty target, publish interruption/resume, count mismatch and immutability.

### Verification

    & 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory/rag/test_candidate_rebuild.py tests/memory/rag/test_source_inventory.py tests/test_rag_source_authority.py -q --tb=short --junitxml=output/e3-a4-focused.xml
    & 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory -q --tb=short --junitxml=output/e3-a4-memory.xml
    & 'D:/python_self_agent/venv/Scripts/python.exe' -m compileall -q hello_agents/memory/rag tests/memory/rag
    & 'D:/python_self_agent/venv/Scripts/python.exe' -m pip check
    git diff --check

## Self-review

No placeholders or interface ambiguity. This unit does not claim JSON publication, maintenance locking, live source enumeration, quality validation or cutover. Full implementation is constrained to the two modules/tests and workflow records; interface mismatch requires packet revision before coding.
