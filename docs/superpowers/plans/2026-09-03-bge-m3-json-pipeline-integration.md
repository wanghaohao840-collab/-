# BGE-M3 managed JSON pipeline integration plan

**Scope:** E2-B2b only. Integrate the strict embedding runtime, full index identity, active-index registry, and versioned JSON cache into `SimpleRAGPipeline`. Do not change application factory wiring, production environment variables, Qdrant data, scheduled tasks, or the stable checkout.

## Invariants

- Legacy JSON mode remains behaviorally compatible and remains the default until E2-B3 explicitly wires managed mode.
- Managed mode requires both an embedding runtime and registry; partial configuration is rejected.
- Startup requires an exact active registry entry and an existing, valid versioned cache. Missing, corrupt, inactive, or mismatched state never falls back to a legacy cache or another model.
- Every managed public read and write revalidates the active full identity. A registry cutover invalidates an already-running old pipeline.
- Managed document vectors and query vectors come from the same runtime and never use padding, truncation, local fallback, or re-embedding during cache load.
- Managed mutations are all-or-nothing across the process-visible store and durable cache: build a complete candidate store, durably replace the cache, then atomically swap the store reference.
- Managed callers cannot use `save_cache=False`, direct `chunks` assignment, or private incremental delete/save hooks to bypass the commit boundary.
- External metadata cannot override system identity, document, chunk, content, namespace, version, or vector-store fields.
- No API key or authorization header is persisted.

## Task 1: Add failing managed-mode contract tests

Create `tests/memory/rag/test_managed_json_pipeline.py` covering:

- partial configuration, missing registry, inactive registry, missing cache, corrupt cache, and same-dimension/full-fingerprint mismatch fail closed;
- a valid explicit empty cache can start;
- managed add/replace uses document batches and managed search uses the query endpoint;
- a later embedding-batch failure leaves both the live store and cache unchanged;
- a cache-write failure leaves both the live store and prior cache unchanged;
- successful replace/delete/clear persist and survive restart;
- protected metadata cannot be forged and no secret is serialized;
- a registry identity switch after construction blocks reads and writes;
- `save_cache=False`, direct `chunks` assignment, `_remove_document_chunks`, and `_save_cache` are rejected in managed mode;
- legacy mode continues to support current callers.

Run the new test module and confirm failures are for missing integration behavior.

## Task 2: Implement managed construction and guarded startup

Modify `hello_agents/memory/rag/pipeline.py`:

- add keyword-only `embedding_runtime` and `index_registry` dependencies;
- derive `IndexIdentity("json", collection_name, runtime.profile)`;
- build `JsonIndexCache` from the legacy path, expose its versioned path as `cache_path`, and preserve the original path separately;
- require the exact active registry record before loading the strict cache;
- build a fresh in-memory collection at the exact profile dimension and load only validated points;
- keep the existing constructor/load behavior unchanged when managed dependencies are absent.

## Task 3: Implement atomic managed reads and writes

- Add a re-entrant per-pipeline lock and an active-identity guard.
- Route managed document preparation through `prepare_document_chunks(..., embedding_runtime=...)`.
- Build replacements before any mutation, preserve document creation time/version semantics, merge with unaffected points, write the strict cache, then swap in the prebuilt store.
- Route managed `add_text` through the same preparation/commit boundary for replace and append behavior.
- Use `runtime.embed_query` for managed search.
- Guard `chunks`, search, stats, summary-context, document-chunk, and document-list reads.
- Implement managed delete and clear as full candidate commits.
- Reject legacy incremental/private mutation hooks in managed mode.

## Task 4: Verify regressions and record evidence

Run:

1. `tests/memory/rag/test_managed_json_pipeline.py`
2. JSON/RAG pipeline and import-progress focused tests
3. all `tests/memory`
4. `compileall`, `pip check`, and `git diff --check`

Record exact results here, self-review the diff for identity, atomicity, compatibility, secret handling, and scope, then commit E2-B2b. Do not activate production configuration.

## Result

Implemented in the isolated `codex/bge-m3-runtime-identity` worktree without production activation.

- Red state: the new managed test module failed 7/7 because the constructor did not accept managed dependencies.
- Managed pipeline and preparation tests: 12 passed in 1.55s.
- Strict cache plus managed pipeline tests after nested secret-field hardening: 29 passed in 2.04s.
- Legacy JSON pipeline/import/graph compatibility group: 95 passed in 143.42s; five pre-existing Neo4j driver destructor warnings only.
- Full memory-domain regression: 384 passed in 94.71s; five pre-existing Neo4j driver destructor warnings only.
- `compileall`, direct pipeline parse, `pip check`, and `git diff --check` passed.

Implementation notes:

- Managed construction now requires the runtime and registry together, validates the exact active identity, and strictly loads only the versioned cache.
- Managed mutations build a complete candidate store, durably commit the strict cache, and swap the live store only after persistence succeeds.
- Managed reads revalidate the registry and capture a consistent store reference; an identity switch invalidates an existing pipeline.
- The old incremental PDF-import hooks remain available only in legacy mode. Managed mode rejects nondurable writes and bypass hooks so E2-B3 must route application imports through `replace_document`.
- Sensitive metadata names are recursively removed during trusted preparation and rejected if found in a strict cache. Runtime credentials and metadata credentials are not serialized.
- No production cache, registry, environment, container, or scheduled task was changed.
