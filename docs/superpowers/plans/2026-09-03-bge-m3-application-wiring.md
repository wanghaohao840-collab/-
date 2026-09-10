# BGE-M3 application wiring plan

**Scope:** E2-B3 only. Wire the already-tested managed runtime and index registry into the RAG factory, `RAGTool`, and per-user application runtime while preserving the production-safe `simple` default. Do not build or activate a production index.

## Invariants

- `RAG_EMBEDDING_PROVIDER=simple` (or absent) selects the legacy path only when the explicit application data root has no active/failed remote index record. Existing registries are validated even for simple. This preserves the design's no-silent-downgrade invariant after activation.
- Any non-simple provider is parsed by the strict embedding settings/runtime. Invalid or incomplete settings fail; there is no fallback to simple.
- Managed mode requires the application’s explicit absolute data root. The registry is always `<data_root>/vector_indexes/rag/registry.json`; it never follows the process working directory or a per-user cache path.
- JSON and Qdrant receive the same backend-specific runtime/identity rules already implemented. Factory wiring never creates a managed cache or collection.
- `RAGTool` retains the data root and passes it for the default and every lazily-created namespace pipeline.
- `UserRuntimeRegistry` supplies `UserStorage.data_root`; no credential value is stored on `UserRuntime` or written to application data.

## Task 1: Add factory and application wiring tests

Cover legacy default behavior, managed JSON construction from environment with an active registry/cache, managed Qdrant construction with an existing physical collection, missing/relative data root, invalid provider/key with no fallback, `RAGTool` namespace propagation, and `UserRuntimeRegistry` propagation.

## Task 2: Implement a single managed dependency resolver

Add a small factory helper that receives backend, explicit data root, and a settings mapping (defaulting to `os.environ`), builds the backend-specific runtime, returns no managed dependencies for simple, and otherwise returns the runtime plus `IndexRegistry(data_root)`.

## Task 3: Wire factory, tool, and app runtime

- Extend `create_rag_pipeline` with explicit `data_root` and optional settings/transport seams.
- Pass managed dependencies to either backend without leaking unrelated keyword arguments.
- Extend `RAGTool` to retain/forward the data root and seams for all namespaces.
- Pass `self.storage.data_root` from `UserRuntimeRegistry`.

## Task 4: Verify and record evidence

Run focused factory/tool/runtime tests, all memory tests, relevant application/session tests, `compileall`, `pip check`, and `git diff --check`. Review fail-closed behavior, path ownership, namespace propagation, legacy compatibility, secrets, and scope. Record exact evidence and commit without changing production environment values.

## Result

Implemented in the isolated `codex/bge-m3-runtime-identity` worktree without production activation.

- Red state: managed JSON/Qdrant factory tests and data-root propagation tests failed because the factory ignored embedding settings and the tool/runtime did not carry the application root.
- Focused application wiring, tool, runtime, session, and user-isolation group: 52 passed in 113.43s; 13 pre-existing Neo4j driver destructor warnings only.
- Full memory-domain regression: 395 passed in 114.91s; five pre-existing Neo4j driver destructor warnings only.
- `compileall`, `pip check`, and `git diff --check` passed.

Implementation notes:

- A single resolver builds the backend-specific runtime from an explicit settings mapping (process environment by default). After the correction below, `simple` returns legacy only if the explicit data root has no activated/failed remote index; any invalid non-simple configuration fails without fallback.
- Managed providers require an explicit absolute data root and use its strict `IndexRegistry`. Relative paths are not silently normalized by `RAGTool`.
- The factory passes the managed dependencies into either backend but never creates a managed cache or collection.
- `RAGTool` forwards the same data root to its default and lazy namespace pipelines; `UserRuntimeRegistry` supplies `UserStorage.data_root`.
- No production environment value, cache, registry, Qdrant collection, container, or scheduled task was changed. `RAG_EMBEDDING_PROVIDER=simple` remains the deployment template default until E3.

## E2 acceptance correction (closed after regression)

Review found that the earlier resolver returned legacy before consulting an existing registry, contrary to specification section 4. The focused tests did not exercise nonempty managed Qdrant stats either; its document-only projection discards the fingerprint before validation.

- [x] Add regressions for simple/missing-provider with active or failed remote registry, corrupt registry, Qdrant nonempty stats, and wrong-backend runtime injection.
- [x] In the resolver, validate an existing registry at the explicit root before returning legacy; reject active/failed remote entries, while allowing pre-activation planned/building/validated entries.
- [x] For managed Qdrant stats, retain full identity metadata; verify requested document scopes as well as namespaces in returned records.
- [x] Recheck active identity after embeddings and before mutations; failed mutation invalidation must not overwrite a newer registry identity.
- [x] Run focused red/green tests and memory/application regression with durable XML reports. Previous all-suite process/report was lost; it is not acceptance evidence.

These are corrections to the approved identity/isolation contract, not a new architecture or production activation. Continue serially in the existing isolated branch.

### Regression-harness correction

The deployment recheck reproduced two tests' dependency on the ignored real `deploy/.env`. Their health scripts now run from temporary repositories with synthetic configuration and a no-op toast sink; recovery/status behavior remains real and Docker/HTTP remain mocked. This changes tests only, not the production notification policy.

The first full-suite rerun was explicitly stopped after observing connections from its Python process to an already-running local Neo4j Desktop Java service. `hello_agents/core/llm.py` calls `load_dotenv()` during import; without a worktree `.env`, discovery can load the parent checkout's configuration, and `RAGTool` then auto-configures the graph store. No Neo4j container was started. That interrupted run is not acceptance evidence, and it cannot establish that all test behavior was isolated.

`tests/conftest.py` now disables dotenv discovery before application imports and removes inherited application provider settings. Explicit `NEO4J_TEST_*` / `QDRANT_TEST_*` opt-in remains available for deliberate integration runs; per-test mocks still supply their own settings. Subprocess tests prove both credential isolation and opt-in preservation. No real configuration file is copied or modified.

Initial corrective evidence: 9 failing regression cases reproduced downgrade/stats/scope/write-invalidation defects; four more failing cases reproduced backend mismatch and stale post-embedding queries. After fixes, the targeted managed-pipeline/Windows group passed **98 tests, 3 skips** (89.89s); skips protect existing trusted fallback telemetry. Harness/factory/registry checks passed **36 tests** (3.50s). Durable reports are under the worktree's untracked `output/`; final full-suite evidence is recorded separately after completion.

### Final E2 evidence (2026-09-03)

- Full Python suite: **1521 passed, 11 skipped in 501.87s**, process exit 0. Report: `output/e2-full-offline-acceptance.xml`. Includes Memory/RAG, API, QA, Notes, import, deployment, and legacy UI regression; no warnings were reported.
- The 11 skips were six explicitly opt-in live database tests (Neo4j/Qdrant), two Windows symbolic-link privilege checks, and three tests protecting existing fallback state. Live database tests were not enabled; Neo4j was not started.
- After that run, the fallback fixture was moved to a temporary script repository without deleting or relocating existing telemetry. Final Windows operations plus environment-harness rerun: **83 passed, no skips in 135.89s**, process exit 0. This explicitly covers all three previously skipped fallback tests and the final provider-prefix isolation guard. Report: `output/e2-final-harness-acceptance.xml`. These counts overlap the full run and must not be added together as unique tests.
- `compileall`, `pip check`, and `git diff --check` passed. Stable App/Qdrant remained healthy in read-only Docker checks. No stable code, real `.env`, production registry/index, container configuration, or scheduled-task definition was changed.
- E2's isolated implementation/acceptance is complete. Production embedding activation and end-to-end migration acceptance remain E3 work; no live deep smoke or real vector rebuild was performed by this correction.

Next: prepare the detailed E3 migration plan against the approved specification, explicitly carrying the readiness findings below into source inventory, bounded rebuilding, maintenance coordination, cutover/recovery, quality evaluation, and stable integration.

### E3 readiness findings (not implemented by E2)

- `Enter-OperationsLock` exists, but health auto-recovery and login startup do not currently acquire it. E3 must coordinate both before stopping App for rebuilding, including crash-resume maintenance state.
- The registry compare-and-set protects in-process invalidation. It is not a cross-process transaction or a replacement for E3's operations lock, stopped writers, and durable cutover journal.
- Existing deep smoke uses a temporary application data root. Managed testing therefore needs an explicitly initialized temporary registry and per-run target, not a reference to the production registry or broad cleanup of production namespaces.
- JSON uses per-user caches; explicit empty/new-user initialization must be distinguished from a missing cache for an existing user. Do not weaken managed startup's missing-cache rejection to make smoke or new-user creation pass.
- Candidate migration state must remain separate from the active registry until verified cutover; a failed staging rebuild must not replace the active source entry.
