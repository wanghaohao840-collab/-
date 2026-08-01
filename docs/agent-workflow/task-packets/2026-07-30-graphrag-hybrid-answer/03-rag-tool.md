---
packet: graphrag-hybrid-03-rag-tool
status: "done"
parallel-safe: false
base-commit: "614f84e plus existing Neo4j worktree"
---

# Hybrid RAG answer composition

Own `hello_agents/tools/builtin/rag_tool.py`,
`tests/tools/test_rag_tool_graph.py`, and
`tests/tools/test_rag_tool_multi_document.py`. Extend ordinary `ask` with
`graph_mode=off|auto|required` (default `auto`) and bounded node/relation
limits. For ordinary joint/single ask, obtain graph context only for selected
documents after vector retrieval, append it to the prompt, and format `G-*`
graph citations. Auto mode falls back to vector-only on unavailable/not-ready
or driver errors; required mode returns an explicit failure before LLM
generation. Do not change compare/summary behavior.

Prerequisite: packet 02.

Verification:

```powershell
D:\Anaconda\python.exe -m pytest tests/tools/test_rag_tool_graph.py tests/tools/test_rag_tool_multi_document.py -q
```

## Implementation handoff

- Status: done
- Files changed: `hello_agents/tools/builtin/rag_tool.py`,
  `tests/tools/test_rag_tool_graph.py`,
  `tests/tools/test_rag_tool_multi_document.py`
- Acceptance: `off|auto|required`, selected-document scoping, `G-*`
  citations, fallback and required-mode behavior implemented.
- Verification: combined tool suite — `34 passed`.
- Commit: not committed
