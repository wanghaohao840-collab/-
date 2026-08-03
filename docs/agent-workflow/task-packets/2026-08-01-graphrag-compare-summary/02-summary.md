---
packet: graphrag-compare-summary-02
status: "done"
parallel-safe: false
base-commit: "614f84e plus documented dirty worktree"
---

# Summary map/cache/reduce integration

Own the same RAGTool and multi-document test files after packet 01. Prefetch
graph context before map work, inject per-document graph evidence, include
graph IDs in reduce allowlists, fingerprint graph context in the summary cache
key, and return graph sources from cache/final output. Required mode must fail
before any LLM call; auto/off preserve existing behavior.

Verification:

```powershell
.\venv\Scripts\python.exe -m pytest tests/tools/test_rag_tool_multi_document.py -q
```

## Handoff

- Prefetched graph context before starting parallel map work, so `required`
  failures happen before any LLM call.
- Each map prompt receives only its document's graph evidence.
- Summary cache keys include graph mode and graph context, preventing stale
  summaries after graph changes.
- `G-*` IDs flow through map source references, reduce allowlists, cached
  results, and final graph-source rendering.
- Existing progress, cancellation, bounded-context, and parallel map behavior
  remains covered by the regression suite.
