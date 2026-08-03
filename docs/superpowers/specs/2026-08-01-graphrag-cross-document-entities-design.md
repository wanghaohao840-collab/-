# GraphRAG Cross-Document Canonical Entities Design

## Goal

Add namespace-scoped cross-document entity reconciliation without weakening
the existing `rag_namespace + document_id` isolation boundary. Comparison and
multi-document summary answers should be able to identify concepts, people,
and knowledge points that occur in more than one selected document while
retaining the originating document for every piece of evidence.

This phase also aligns the local runtime configuration with the Neo4j instance
already verified at `neo4j://localhost:7687`.

## Current state

- Extracted `Concept`, `Person`, and `KnowledgePoint` nodes have document-scoped
  IDs and a `normalized_name`.
- Neo4j constraints and all document graph reads/writes use both
  `rag_namespace` and `document_id`.
- Comparison and multi-document summary already consume bounded per-document
  graph contexts and expose stable `G-*` citations.
- Directly merging the document nodes would make replacement and deletion
  unsafe because one physical node could belong to several documents.

## Chosen model

Keep every extracted entity node document-local and introduce a shared
`CanonicalEntity` layer:

```text
(Concept|Person|KnowledgePoint {rag_namespace, document_id, ...})
    -[:REFERS_TO]->
(CanonicalEntity {rag_namespace, entity_type, normalized_name, ...})
```

A canonical entity is unique by:

```text
(rag_namespace, entity_type, normalized_name)
```

`entity_type` prevents a person and a concept with the same text from being
merged. The first version uses the extractor's deterministic
`normalized_name`; it does not use fuzzy similarity or an LLM to infer aliases.
This deliberately favors precision over recall.

Canonical nodes contain only identity/display metadata. Document-specific
descriptions, evidence, chunk IDs, and confidence stay on local nodes and
relationships.

## Write and deletion lifecycle

`Neo4jGraphStore.replace_document_graph()` will create or match canonical
entities in the same transaction that replaces the document graph:

1. Delete the old document-scoped graph.
2. Write the replacement document nodes and extracted relations.
3. For each local concept, person, and knowledge point with a non-empty
   `normalized_name`, merge the namespace-scoped canonical node.
4. Merge a `REFERS_TO` edge from the local entity to that canonical node.
5. Remove canonical nodes in the namespace that no longer have an incoming
   `REFERS_TO` edge.

Document deletion performs the same orphan cleanup after deleting the target
document graph. Canonical nodes referenced by another document therefore
survive. All canonical creation and cleanup queries remain parameterized.

`REFERS_TO` is storage-managed and is not accepted from LLM extraction output.

## Existing graph compatibility

No destructive migration is required. New and rebuilt document graphs receive
canonical links automatically. Cross-document reads also group selected legacy
local entities by `(entity_type, normalized_name)` when a canonical link is not
present, so existing graphs remain queryable before they are rebuilt.

The fallback is read-only and restricted to the explicit namespace and
selected document IDs. It must not create nodes while answering a question.

## Cross-document read API

Add a bounded storage operation:

```python
Neo4jGraphStore.get_cross_document_entities(
    document_ids,
    *,
    query_terms,
    rag_namespace="default",
    entity_limit=12,
    evidence_limit=40,
) -> dict
```

Rules:

- Require 2-10 unique, non-empty document IDs.
- Match only `Concept`, `Person`, and `KnowledgePoint` nodes from those IDs.
- Match query terms against `normalized_name`, `name`, and `title`.
- Return only groups represented in at least two selected documents.
- Return deterministic ordering and bounded evidence.
- Do not return chunk content.
- Include the canonical identity plus document-local members with
  `document_id`, local graph ID, type, display name, and safe metadata.

`KnowledgeGraphService.get_cross_document_entities()` will validate that every
selected document graph is ready before delegating to storage. Its response
uses the existing success/error envelope style.

## RAGTool integration

Cross-document context applies only when at least two documents are selected:

- **Compare:** append one bounded shared-entity block after the existing
  per-document vector and graph evidence. Shared entities receive stable
  `G-*` citations and are allowed by structured comparison validation.
- **Multi-document summary:** fetch shared entities before starting map work so
  `required` failures still occur before any LLM call. Keep shared context out
  of individual map prompts; append it only to the reduce prompt, where
  cross-document conclusions belong.
- **Auto mode:** storage/service failure omits shared context and preserves the
  existing answer path.
- **Required mode:** service/configuration/query failure returns a graph error
  before generation. An empty successful result is not an error because the
  selected documents may legitimately share no entities.
- **Off mode:** do not call the cross-document graph API.

Graph source records for a canonical entity contain `document_ids` rather than
pretending the entity belongs to one document. Existing per-document graph
source shapes remain backward compatible.

## Configuration

Update the untracked/local `.env` values to use the verified local service:

```env
NEO4J_URI=neo4j://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_DATABASE=neo4j
```

`NEO4J_PASSWORD` is copied from the previously supplied local credential;
its value is intentionally omitted here. Existing connection timeout and pool
settings remain unchanged. Secrets must not appear in test output,
documentation, source files, or final reports.

## Failure and isolation guarantees

- No query may select a canonical or local entity outside the requested
  `rag_namespace`.
- Cross-document evidence must be limited to the explicit `document_ids`.
- Replacing or deleting one document must not remove another document's local
  nodes, evidence, or still-referenced canonical entities.
- All limits are validated and bounded before query execution.
- Canonical cleanup must run in the same write transaction as replacement or
  deletion to avoid externally visible orphan states.
- Failed Neo4j connectivity must close newly created drivers, preserving the
  prior phase's lifecycle fix.

## Testing and acceptance

Unit tests must cover:

- schema uniqueness for canonical entities;
- canonical creation and `REFERS_TO` linkage during replacement;
- orphan cleanup during replacement and deletion;
- namespace and selected-document scoping in every cross-document query;
- same normalized name across two documents merges, while different entity
  types do not;
- legacy local-node grouping without canonical links;
- deterministic limits and no chunk-content leakage;
- service ready-state validation and error envelopes;
- compare prompt/source/structured-citation integration;
- summary reduce-only integration;
- `off`, `auto`, and `required` failure behavior before LLM execution.

The live Neo4j test will create two isolated fixture documents sharing one
concept, verify one canonical result, replace one document, delete both, and
verify cleanup. Final acceptance also requires focused GraphRAG tests, the full
repository suite, `compileall`, `pip check`, and `git diff --check` in the
repository `venv`.

## Non-goals

- LLM-based entity linking, embeddings, edit distance, aliases, or manual merge
  review.
- Cross-namespace entity sharing.
- Changing vector retrieval ranking.
- Graph visualization or UI controls.
- Multi-process graph build locking.
- Committing or pushing the shared dirty worktree.
