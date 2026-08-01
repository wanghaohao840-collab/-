---
packet: graphrag-hybrid-02-service
status: "done"
parallel-safe: false
base-commit: "614f84e plus existing Neo4j worktree"
---

# Ready-gated graph context service

Own `hello_agents/memory/graph/service.py`,
`hello_agents/memory/graph/__init__.py`, and
`tests/memory/graph/test_service.py`. Add
`KnowledgeGraphService.get_graph_context(document_id, query, *,
node_limit=8, relation_limit=16)`. Normalize query terms into capped
alphanumeric runs/CJK bigrams, gate on `ready`, forward the namespace and
limits to the store, and return the existing canonical graph envelope.
Sanitize store errors using the existing state helpers.

Prerequisite: packet 01.

Verification:

```powershell
D:\Anaconda\python.exe -m pytest tests/memory/graph/test_service.py -q
```

## Implementation handoff

- Status: done
- Files changed: `hello_agents/memory/graph/service.py`,
  `tests/memory/graph/test_service.py`
- Acceptance: ready gate, bounded Unicode terms, namespace forwarding and
  sanitized errors implemented.
- Verification: service suite — `11 passed`.
- Commit: not committed
