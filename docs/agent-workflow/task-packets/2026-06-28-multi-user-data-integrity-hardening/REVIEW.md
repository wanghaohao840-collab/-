# Plan Review: Multi-User Data Integrity Hardening

- Source plan:
  `docs/superpowers/plans/2026-06-28-multi-user-data-integrity-hardening.md`
- Reviewed commit: `614f84e9d01179ce1272281f77e15550c1dcd764`
- Review date: `2026-06-28`
- Verdict: `accepted-with-revisions`

## Repository evidence

- `app/runtime.py:53-68`: one `RLock` is shared by same-user assistants;
  `MemoryTool` receives it through `coordination_lock`.
- `assistants/pdf_learning_assistant.py:561-565`: History mutations reload and
  atomically save under the runtime lock.
- `assistants/pdf_learning_assistant.py:652-685`: delete/clear currently
  coordinate RAG, History, and source-file unlink under the same lock.
- `assistants/pdf_learning_assistant.py:693-746`: report exports use
  `ReportService`; selected Word export reads the immutable Markdown snapshot.
- `ui/gradio_app.py:93-120`: upload validates suffix, uses `UserStorage`, stages
  a temporary file, and compensates failed import.
- `app/history.py:21-37` and `app/memory_repository.py:22-34`: corrupt JSON is
  fail-closed rather than treated as empty.
- `app/migration.py:87-161`: migration records status, backup, manifest, and
  failure, but `_stage_and_commit()` publishes several final resources
  incrementally and retry can duplicate generated IDs/rows.
- `tests/test_p0_data_integrity.py`: basic concurrent notes, source deletion,
  and immutable Word snapshot tests exist.
- Baseline:
  `17 passed in 10.98s` for the focused History, runtime, report, storage,
  migration, and P0 integrity suite.
- Existing worktree changes: extensive uncommitted multi-user, Qdrant,
  multi-document, and workflow changes. Every worker must preserve them.

## Findings

### Blocking

- None for packet preparation.

### Required revisions to the originating audit

- Report integration, upload staging, original-file deletion, structured
  question scope, fail-closed repositories, and most migration scaffolding are
  already implemented. They become regression requirements rather than new
  feature tasks.
- “Atomic” must mean a documented process-local coordination and compensation
  contract; filesystem, SQLite, JSON, and RAG cannot share one real transaction.
- Migration requires the largest remaining implementation change because final
  state is currently published before the whole run succeeds.

### Non-blocking notes

- `self.history` remains a session cache. Reads that require freshness should
  capture a repository snapshot instead of trusting this cache.
- Existing session counters are session-local and need not be globally exact.

## Accepted scope

- Goal: harden process-local user mutations, migration recovery, corruption
  recovery, and cross-boundary acceptance coverage.
- In scope: coordination contract, migration publish/rollback/retry, explicit
  recovery services, comprehensive local tests.
- Out of scope: distributed locking, infrastructure redesign, live external
  services, unrelated UI redesign.
- Compatibility: preserve existing Assistant methods, report records, History
  fields, RAG formats, and legacy single-document calls.
- Constraints: `UI -> Assistant -> Tool -> Memory/RAG/Storage`, user UUID root,
  explicit `document_id`, no LLM generation under the write lock.

## Packet graph

| Packet | Depends on | Parallel-safe | Owned files | Outcome |
|---|---|---:|---|---|
| `01-user-mutation-coordination.md` | none | no | runtime, assistant, coordination tests | Explicit mutation contract |
| `02-migration-recovery.md` | none | yes | migration, migration tests | Retry-safe migration |
| `03-corruption-recovery.md` | 01 | no | history/memory recovery, UI handlers, tests | Explicit recovery |
| `04-end-to-end-isolation.md` | 01, 02, 03 | no | integration/handler tests only | Design-level acceptance |

Packets 01 and 02 have disjoint owned files and may run in parallel. Packets 03
and 04 must wait for their declared dependencies.

## Packet readiness audit

| Packet | Goal/non-goals | Context/interfaces | Prerequisites | Change boundary | Acceptance/tests | Forbidden changes | Handoff format | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 01 | yes | yes | yes | yes | yes | yes | yes | yes |
| 02 | yes | yes | yes | yes | yes | yes | yes | yes |
| 03 | yes | yes | yes | yes | yes | yes | yes | yes |
| 04 | yes | yes | yes | yes | yes | yes | yes | yes |

## Integration verification

- `$env:TEMP=(Resolve-Path '.runtime/pytest-tmp').Path; $env:TMP=$env:TEMP; D:\Anaconda\python.exe -m pytest -q`
- `python -m compileall -q app assistants hello_agents ui tests`

## Final integration review requirement

- Output:
  `docs/agent-workflow/task-packets/2026-06-28-multi-user-data-integrity-hardening/FINAL_INTEGRATION_REVIEW.md`
- Required after: packets 01-04 are `done`
- Result: `accepted | changes-required | blocked`
- Must audit cross-packet coordination/recovery interfaces, missing audit
  requirements, duplicate mutation logic, and the combined regression suite.

## Open decisions

- None. A reality conflict must block the affected packet and return to Codex.
