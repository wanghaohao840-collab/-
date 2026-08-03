---
packet: graphrag-compare-summary-01
status: "done"
parallel-safe: false
base-commit: "614f84e plus documented dirty worktree"
---

# Graph routing and compare integration

Own `hello_agents/tools/builtin/rag_tool.py`,
`tests/tools/test_rag_tool_multi_document.py`, and
`tests/tools/test_rag_compare.py`. Forward graph controls into compare and
summary without leaking them into Pipeline kwargs. Implement compare graph
composition, fallback/required behavior, `G-*` structured citation validation,
and final graph source formatting. Preserve current protected per-document
vector results and structured Markdown fallback.

Verification:

```powershell
.\venv\Scripts\python.exe -m pytest tests/tools/test_rag_compare.py tests/tools/test_rag_tool_multi_document.py -q
```

## Handoff

- Forwarded `graph_mode`, `graph_node_limit`, and `graph_relation_limit` from
  the public ask path without passing them to vector retrieval.
- Added selected-document graph context to comparison prompts and final
  sources.
- Structured comparison validation now accepts only the emitted `S-*` and
  `G-*` citation IDs.
- `required` mode exits before generation on a missing, failed, or empty graph
  context; `auto` and `off` preserve vector-only behavior.
- Verification is included in the combined focused gate recorded by
  `FINAL_INTEGRATION_REVIEW.md`.
