---
id: "graphrag-cross-document-entities-01"
title: "Canonical persistence and bounded cross-document reads"
status: "done"
parallel-safe: false
depends-on: []
base-commit: "e0d11b9a775642c10a0237ad7d8e7335cb64ba71"
owner: "Codex"
---

# Task Packet: Canonical persistence and bounded cross-document reads

## Goal

Neo4j persists namespace-scoped canonical identities for document-local
entities and exposes a safe bounded query for identities shared by selected
documents.

## Non-goals

- Fuzzy/LLM alias resolution, service/RAGTool integration, UI, or migrations.
- Changing extracted entity IDs or document graph response formats.

## Delivery context

Current local entities are intentionally document-scoped. A storage-managed
canonical layer must group them without transferring evidence ownership or
breaking delete/replace isolation. Existing graphs without links remain
readable through query-time grouping.

## Relevant files and current interfaces

- `hello_agents/memory/storage/neo4j_store.py:49` — constraints and relation
  templates; `REFERS_TO` must remain separate from LLM relations.
- `hello_agents/memory/storage/neo4j_store.py:188` — one-transaction replace.
- `hello_agents/memory/storage/neo4j_store.py:443` — bounded read pattern.
- `hello_agents/memory/storage/neo4j_store.py:657` — scoped delete transaction.
- `tests/memory/storage/test_neo4j_store.py:12` — recording driver seam.
- `tests/integration/test_neo4j_live.py` — live lifecycle fixture.
- Existing changes to preserve: driver-close fix and graph-context methods.

## Prerequisites

### Packet dependencies

- None.

### Repository/base state

- Base commit above plus documented shared dirty worktree.
- Neo4j driver 5.28.4 in repository `venv`.

### External prerequisites

- Local Neo4j is required only for final live verification, not unit delivery.

## Explicit change boundary

### Allowed files

- Modify: `hello_agents/memory/storage/neo4j_store.py`
- Test: `tests/memory/storage/test_neo4j_store.py`
- Test: `tests/integration/test_neo4j_live.py`

### Allowed behavior changes

- Add canonical schema/link/cleanup Cypher and the public bounded read API.

### Forbidden changes

- Do not edit extractor, service, RAGTool, UI, vector storage, dependency files,
  or unrelated tests.
- Do not merge local entity nodes, expose Chunk content, or weaken namespace
  and document filters.

## Interface contract

### Consumes

- Existing local entity properties: graph ID, `normalized_name`, display name,
  `rag_namespace`, and `document_id`.

### Produces

- `get_cross_document_entities(document_ids, *, query_terms,
  rag_namespace="default", entity_limit=12, evidence_limit=40) -> Dict`.
- `{"entities": [...]}` with canonical identity and safe local members.

### Invariants

- Only exact same-type normalized names reconcile.
- Local evidence and lifecycle remain document-scoped.
- Orphan cleanup is transactional and namespace-scoped.

## Required behavior

- Link Concept, Person, and KnowledgePoint nodes with non-empty normalized names
  on every replacement.
- Clean only canonical nodes with no incoming `REFERS_TO` in the namespace.
- Validate 2-10 unique selected IDs and bounded limits.
- Return only entities represented in at least two selected documents.
- Support legacy local nodes without canonical links and sanitize `content`.

## Implementation guidance

Use dedicated query markers so recording tests can identify calls. Group the
read by `labels(local)[0]` and `local.normalized_name`; canonical linkage is
metadata enrichment, not required for grouping. Sort canonical groups and
members deterministically before returning.

## Acceptance criteria

- [ ] Replacement links canonical entities and cleans orphans in one write.
- [ ] Deletion preserves referenced canonical nodes and cleans true orphans.
- [ ] Cross-document query is namespace/selection scoped and parameterized.
- [ ] Legacy grouping, type separation, bounds, ordering, and sanitization pass.
- [ ] Existing storage and live lifecycle behavior remains compatible.

## Test and verification commands

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/storage/test_neo4j_store.py -q --basetemp=.runtime/pytest-cross-entity-storage
```

Expected: all tests pass.

## Stop conditions

Stop on stale interfaces, overlap outside listed prior GraphRAG changes,
required edits outside allowed files, broken document isolation, or invalid
verification. Append the repository reality-conflict report instead of
expanding scope.

## Implementation handoff

- Packet: `graphrag-cross-document-entities-01`
- Status: `done`
- Delivered:
  - Added namespace-scoped `CanonicalEntity` uniqueness and storage-managed
    `REFERS_TO` links during replacement.
  - Added transactional canonical orphan cleanup on replacement and deletion.
  - Added bounded, parameterized cross-document exact-name grouping with legacy
    local-node fallback and content sanitization.
  - Extended the live fixture to cover two documents sharing an entity.
- Files changed:
  - `hello_agents/memory/storage/neo4j_store.py`
  - `tests/memory/storage/test_neo4j_store.py`
  - `tests/integration/test_neo4j_live.py`
- Interfaces added or changed:
  - `Neo4jGraphStore.get_cross_document_entities(...) -> Dict[str, Any]`
- Acceptance evidence:
  - [x] Canonical schema/link/cleanup behavior covered by storage tests.
  - [x] Namespace/document scoping, limits, ordering, and content redaction
    covered by storage tests.
  - [x] Two-document canonical lifecycle executed against local Neo4j.
- Verification:
  - `pytest tests/memory/storage/test_neo4j_store.py -q` — PASS (`18 passed`)
  - `pytest tests/integration/test_neo4j_live.py -q` with local credentials —
    PASS (`1 passed`, not skipped)
- Scope confirmation:
  - changed only allowed files: yes
  - forbidden areas untouched: yes
- Deviations:
  - none
- Residual risks/follow-ups:
  - Exact normalized-name matching intentionally does not resolve aliases.
- Commit:
  - `not committed`
