# Plan Review: Neo4j knowledge graph

- Source plan: `docs/superpowers/plans/2026-07-29-neo4j-knowledge-graph.md`
- Reviewed commit: `614f84e`
- Review date: `2026-07-29`
- Verdict: `accepted-with-revisions`

## Repository evidence

- Relevant implementation:
  - `hello_agents/memory/storage/neo4j_store.py`: in-memory placeholder only; no driver, transactions, Cypher, delete, or durable queries.
  - `hello_agents/memory/types/semantic.py`: directly constructs the placeholder and must remain usable when Neo4j is unconfigured.
  - `hello_agents/memory/rag/pipeline.py`: JSON/in-memory RAG backend exposes document-scoped chunks internally but no public complete-document loader.
  - `hello_agents/memory/rag/qdrant_pipeline.py`: Qdrant backend can scroll scoped payloads but exposes only sampled summary context.
  - `hello_agents/tools/builtin/rag_tool.py`: central document import/delete action boundary and structured-result seam.
- Relevant tests:
  - `tests/memory/test_semantic_fallback.py`: semantic-memory compatibility baseline.
  - `tests/tools/test_rag_tool_backend_contract.py`: both RAG backends must retain equivalent behavior.
  - No Neo4j or graph tests currently exist.
- Configuration/runtime facts:
  - `requirements.txt` has no Neo4j driver.
  - `pytest -q` stops during collection because this interpreter lacks Gradio; non-UI verification remains runnable.
  - No live Neo4j or LLM is required for default tests.
- Existing worktree changes to preserve:
  - The worktree contains extensive staged multi-user, Qdrant, Tool, assistant, README, requirements, and test changes. Neo4j work must be layered onto them without reset or broad rewrites.

## Findings

### Blocking

- None.

### Required revisions

- Graph service creation must be optional so existing RAG and SemanticMemory initialization remain available without Neo4j configuration.
- Because the current LLM wrapper returns error strings instead of raising most provider failures, extraction must classify both raised exceptions and wrapper error prefixes.
- All packets are serial; central integration files are already dirty and must preserve current behavior.
- The committed Neo4j design predates the repository's multi-user runtime. The accepted implementation must scope every stored node and every query by `(rag_namespace, document_id)`, and stable knowledge IDs must include the namespace.
- Graph state belongs under the owning user's RAG directory, and runtime shutdown must close the graph driver through `UserRuntime -> RAGTool -> KnowledgeGraphService`.

### Non-blocking notes

- The live integration test will normally skip.
- GraphRAG retrieval remains a named non-goal despite the broader module label.

## Accepted scope

- Goal: real Neo4j-backed, document-isolated knowledge graph lifecycle integrated weakly with RAG.
- In scope: driver storage, extraction/validation, state/recovery, concurrency, query/retry/delete APIs, Tool integration, tests, docs.
- Out of scope: graph-enhanced answer retrieval, UI, cross-document merge, background queue, multi-process locks.
- Compatibility requirements: legacy RAG string results and both JSON/Qdrant backends continue to work with no Neo4j configuration.
- Architecture/data-isolation constraints: one-way dependency direction; all graph operations require and filter both `rag_namespace` and `document_id`; RAG succeeds independently of graph failures.

## Packet graph

| Packet | Depends on | Parallel-safe | Owned files | Outcome |
|---|---|---:|---|---|
| `01-storage.md` | none | no | store, storage export, dependency, storage tests | real Neo4j adapter |
| `02-domain.md` | 01 | no | graph contracts/extractor/state and tests | validated graph payload and durable status |
| `03-service.md` | 01, 02 | no | graph service/export and tests | lifecycle/query API |
| `04-integration.md` | 03 | no | RAG pipelines, Tool, runtime lifecycle, semantic compatibility, integration tests/docs | end-to-end weak integration |

## Packet readiness audit

| Packet | Goal/non-goals | Context/interfaces | Prerequisites | Change boundary | Acceptance/tests | Forbidden changes | Handoff format | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `01-storage.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `02-domain.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `03-service.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `04-integration.md` | yes | yes | yes | yes | yes | yes | yes | yes |

## Integration verification

- `pytest -q tests/memory/graph tests/memory/storage/test_neo4j_store.py tests/tools/test_rag_tool_graph.py tests/integration/test_neo4j_live.py`
- `D:\Anaconda\python.exe -m pytest -q`

## Final integration review requirement

- Output: `docs/agent-workflow/task-packets/2026-07-29-neo4j-knowledge-graph/FINAL_INTEGRATION_REVIEW.md`
- Required after: every implementation packet is `done`
- Result must be: `accepted | changes-required | blocked`
- Required checks: cross-packet contracts, requirement coverage, overlap, central integration, architecture, compatibility, persistence, isolation, and regression verification.

## Open decisions

- None.
