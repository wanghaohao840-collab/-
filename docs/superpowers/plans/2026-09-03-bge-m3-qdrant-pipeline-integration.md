# BGE-M3 managed Qdrant pipeline integration plan

**Scope:** E2-B2c only. Bind `QdrantRAGPipeline` to the strict embedding runtime, versioned physical collection, and active registry. Do not activate production configuration, create/migrate a real collection, or change the stable checkout.

## Invariants

- Legacy Qdrant mode remains the default and keeps its existing create-if-missing behavior until E2-B3 wires managed mode.
- Managed mode requires both runtime and registry and derives the physical collection solely from the full trusted `IndexIdentity`.
- Managed startup requires the exact active registry entry and an already-existing physical collection with the exact dimension and distance. It performs no collection or payload-index creation.
- Managed document and query vectors use the same runtime; padding, truncation, model fallback, and legacy embedder access are forbidden.
- Every managed public operation revalidates the active registry identity. Every returned or written point is checked for namespace, document identity, and full embedding fingerprint.
- External metadata cannot override system identity and secret-bearing metadata is neither sent to Qdrant nor returned from a trusted preparation path.
- Qdrant does not provide a multi-request transaction for batched document replacement. Once a managed remote mutation begins, any ambiguous upsert/delete failure must fail the registry entry closed and poison the current pipeline; recovery is an explicit E3 rebuild/validation/reactivation, never silent continuation on a potentially mixed index.

## Task 1: Add managed Qdrant contract tests

Create a focused test module covering partial configuration, missing/inactive registry, missing/incompatible physical collection, no auto-create/index writes on startup, physical collection naming, shared document/query runtime, fingerprint propagation, protected metadata, forged result rejection, post-construction registry switch, and mutation-failure fail-closed behavior. Retain legacy constructor tests.

## Task 2: Implement managed construction and runtime binding

- Add optional `embedding_runtime` and `index_registry` dependencies.
- Preserve the caller’s base collection separately; derive `IndexIdentity("qdrant", base, profile)` and use only its physical collection in managed mode.
- Require the active registry and call `require_collection`; skip `ensure_collection` and payload-index creation.
- Route document preparation to `embed_documents` and queries to `embed_query`.

## Task 3: Guard points and remote mutations

- Revalidate active identity at all public entry points.
- Validate flattened result metadata before returning or using points.
- Validate prepared point identity before writes.
- If a remote write/delete fails after mutation starts, mark the registry record `failed`, poison the instance, and propagate the operation failure. Do not auto-reactivate.

## Task 4: Verify and record evidence

Run the new tests, existing Qdrant/RAG/import contract tests, all memory tests, `compileall`, `pip check`, and `git diff --check`. Review for physical-name derivation, read-only startup, point identity, fail-closed mutation handling, secret handling, compatibility, and scope. Record exact results and commit without production activation.

## Result

Implemented in the isolated `codex/bge-m3-runtime-identity` worktree without production activation.

- Red state: the new managed test module failed 7/7 because the existing pipeline ignored the managed dependencies and continued with its legacy embedder/collection.
- Managed Qdrant contracts: 7 passed in 1.80s.
- Existing Qdrant, backend-selection, import-progress, and managed compatibility group: 97 passed in 100.63s; four pre-existing Neo4j driver destructor warnings only.
- Full memory-domain regression: 391 passed in 112.94s; five pre-existing Neo4j driver destructor warnings only.
- `compileall`, `pip check`, and `git diff --check` passed.

Implementation notes:

- Managed startup derives the physical name from the trusted full identity, requires the active registry entry, and calls only the read-only collection verifier. It does not create a collection or payload index.
- Managed document batches and query vectors use the same runtime. Returned and written points must match the full embedding fingerprint, namespace, and document identity.
- Prepared metadata strips nested credential fields; managed reads reject credential-bearing or forged remote payloads.
- A remote mutation failure after the write/delete boundary marks the registry record `failed`, poisons the live instance, and propagates the error. Reactivation requires the later E3 rebuild and validation flow.
- Legacy Qdrant behavior remains unchanged. No real Qdrant collection, registry, environment, container, or scheduled task was mutated.
