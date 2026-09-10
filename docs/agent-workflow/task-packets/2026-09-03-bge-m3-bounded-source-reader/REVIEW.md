# Plan Review: BGE-M3 bounded source reader

- Source plan: `docs/superpowers/plans/2026-09-03-bge-m3-bounded-source-reader.md`
- Reviewed commit: `a33b071`
- Review date: 2026-09-03
- Verdict: accepted-with-revisions

## Repository evidence

- Relevant implementation:
  - `hello_agents/memory/storage/vector_store.py:399`: Qdrant adapter; `scroll` at line 605 accumulates all results. Existing `_call` and `_filter` supply transport/error and scope boundaries.
  - `hello_agents/memory/rag/errors.py`: safe typed operation/authentication/connection exceptions.
- Relevant tests:
  - `tests/memory/storage/test_qdrant_vector_store.py`: fake-client adapter seam.
  - `tests/memory/rag/test_managed_qdrant_pipeline.py`: managed pipeline compatibility. Baseline combined group: 20 passed in 5.27s.
  - `tests/conftest.py`: disables automatic dotenv loading and clears live service configuration.
- Configuration/runtime facts:
  - Existing isolated worktree points to the stable repository common Git directory; existing venv has qdrant-client and pytest.
  - Embedded-client tests require no running service; no real provider configuration is read.
- Existing worktree changes to preserve:
  - Untracked `output/` reports; unrelated dirty stable checkout is outside this worktree.
  - Newly authored plan is the reviewed input, not pre-existing implementation.

## Findings

### Blocking

- None after the revision below.

### Required revisions

- Correct the malformed logical-ID fixture from a literal backslash-plus-n to a Python newline escape. Applied to the plan before packet readiness; the test now actually exercises control-character rejection.

### Non-blocking notes

- Native cursor handoff is not a durable checkpoint or snapshot. Source ownership, full-scan duplicate/digest auditing and source stability remain E3-A2 requirements.
- Count bounds do not impose a byte cap on malicious SDK payloads. Traversal cursor history is bounded by the explicit page budget.

## Accepted scope

- Goal: bounded read-only Qdrant page interface for later migration inventory.
- In scope: typed physical/logical IDs, payload-only pages, finite lazy traversal, safe validation and existing transport errors.
- Out of scope: JSON adapter, candidate rebuild, registry cutover, Windows controller, model activation, real services.
- Compatibility requirements: leave public VectorStore protocol and all existing scroll/search/upsert/collection behavior unchanged.
- Architecture/data-isolation constraints: SDK details stay storage-local; forward copied scope predicates; authoritative ownership verification is not implied by a successful page.

## Packet graph

| Packet | Depends on | Parallel-safe | Owned files | Outcome |
|---|---|---:|---|---|
| 01-bounded-reader.md | none; E2 base present | no | vector_scan.py, additive vector_store.py methods, test_qdrant_scan.py, listed delivery records | independently tested bounded reader |

## Packet readiness audit

| Packet | Goal/non-goals | Context/interfaces | Prerequisites | Change boundary | Acceptance/tests | Forbidden changes | Handoff format | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 01-bounded-reader.md | yes | yes | yes | yes | yes | yes | yes | yes |

## Integration verification

Run from the isolated worktree:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest tests/memory/storage/test_qdrant_scan.py tests/memory/storage/test_qdrant_vector_store.py tests/memory/storage/test_vector_store_contract.py tests/memory/rag/test_managed_qdrant_pipeline.py -q --tb=short --junitxml=output/e3-a1-focused.xml
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest tests/memory -q --tb=short --junitxml=output/e3-a1-memory.xml
& 'D:\python_self_agent\venv\Scripts\python.exe' -m compileall -q hello_agents/memory/storage tests/memory/storage
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pip check
git diff --check
```

Expected: no failures; optional live-service tests remain skipped without explicit opt-in.

## Final integration review requirement

- Output: `docs/agent-workflow/task-packets/2026-09-03-bge-m3-bounded-source-reader/FINAL_INTEGRATION_REVIEW.md`
- Required after: packet 01 is done.
- Result: accepted, changes-required or blocked, limited to E3-A1.
- Required checks: actual producer/consumer contracts, missing requirements, duplicate/overlapping implementation, central integration, architecture/compatibility/persistence/isolation, combined regression.

## Open decisions

- None for E3-A1. User authorized serial implementation; no worker delegation, Git commit, push or stable deployment is included.
