---
packet: graphrag-hybrid-01-storage
status: "done"
parallel-safe: false
base-commit: "614f84e plus existing Neo4j worktree"
---

# Storage graph-context query

Own `hello_agents/memory/storage/neo4j_store.py` and
`tests/memory/storage/test_neo4j_store.py`. Add
`get_graph_context(document_id, *, query_terms, rag_namespace="default",
node_limit=8, relation_limit=16)`. The query must use parameters for all
values, match only the current namespace/document, find Concept,
KnowledgePoint, Person, or Chapter seeds by normalized name/title/description,
expand one hop, and return bounded `entities` and `relations` without Chunk
content. Preserve all existing APIs and fake-driver conventions.

Verification:

```powershell
D:\Anaconda\python.exe -m pytest tests/memory/storage/test_neo4j_store.py -q
```

## Implementation handoff

- Status: done
- Files changed: `hello_agents/memory/storage/neo4j_store.py`,
  `tests/memory/storage/test_neo4j_store.py`
- Acceptance: parameterized namespace/document-scoped seed search and bounded
  one-hop context implemented; Chunk content stripped.
- Verification: storage suite — `10 passed`.
- Commit: not committed
