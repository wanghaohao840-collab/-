---
id: "2026-10-10-isolated-import-runtime-02"
title: "Open a trusted isolated runtime with separate recovery and ordinary readiness"
status: "ready"
parallel-safe: false
depends-on: ["2026-10-10-isolated-import-runtime-01"]
base-commit: "d81f0c3494d02bca51fb312c602d215edf6f98a0"
owner: "unassigned"
---

# Task Packet: explicit isolated runtime identity and readiness

## Reviewed assignment gate

Task 01 is accepted and delivered at this base. Astra High independently returned READY for this complete implementation contract on 2026-10-11. Report: `.runtime/isolated-import-runtime/p2-plan-astra-n2.md`, SHA256 `16130c01e2e8c57a4c4300eb8ed173838ec2cd19a8565af4312077331a686553`. No Task 02 source or tests were run while preparing or reviewing it. Implementation still requires one assigned Sol High worker and the root-controlled pytest lane. The review does not accept an implemented runtime.

## Goal

Create one opt-in, immutable, role-aware composition boundary. An API or Worker process can open only a specifically named disposable PostgreSQL schema and can report recovery readiness from that database identity alone. The ordinary readiness decision requires a versioned S3 bucket, both exact Qdrant identities, active user status, the already published History/RAG and Memory/episode baseline, **and** a profile-bound provider readiness report supplied by a later composition packet. With no provider report it is false. Missing or contradictory baseline is never treated as empty.

## Non-goals

- No API routes, session facade, upload admission, Worker loop, source parse, embedding engine/probe execution, publication, recovery mutation, or production initializer.
- No schema/bucket/collection creation, migration, new account registration, retained-data migration, ordinary bootstrap change, or protected-source Qdrant access.
- No claim that a passing readiness probe proves import execution, restart recovery, or production readiness.

## Delivery context

Task 01 added an optional SQL tenant filter to ordinary claim, ordinary expiry, and publication recovery claim. Later packets will pass this runtime's nonempty `trusted_user_ids` to all three selectors. This packet only defines trusted configuration, opens owned resources, and supplies independent readiness probes. The approved first milestone concerns two **pre-attested** users in newly created isolated resources. If a provider or a user's baseline is unavailable, ordinary import must refuse while database-backed recovery and status reads remain possible.

## Relevant files and current interfaces

- `app/deployment.py:29-110` — `DeploymentSettings.from_env(env)` reads ordinary `APP_DATA_MODE`, `APP_PROCESS_ROLE`, `DATABASE_URL`, pool and S3 keys. It is not an isolated identity validator; `safe_summary()` excludes credentials. Do not alter ordinary defaults or bootstrap.
- `app/postgres.py:14-81` — `PostgresDatabase(database_url, *, min_size=1, max_size=10, timeout=10.0)` owns a lazy `ConnectionPool`; `open()` waits for it, `close()` closes it **only after** `_opened` became true, `ping()` runs `select 1`, and `transaction()` yields a cursor. No schema identity helper exists; a failed `pool.open(wait=True)` can leave a half-open pool that `database.close()` alone will not close.
- `app/object_store.py:69-82` — `S3ObjectStore(client, bucket).check_ready()` checks bucket existence and requires versioning `Enabled`; it returns `None` or raises. It does not own/close the boto3 client.
- `hello_agents/memory/storage/vector_store.py:406-445` — `QdrantVectorStore(url=..., api_key=..., retry_delays=...)` and `require_collection(name, dimension, distance='Cosine')` check an existing physical collection without creating one. `ensure_collection()` creates and is forbidden here.
- `hello_agents/memory/rag/embedding_profile.py:21-47` and `hello_agents/memory/rag/index_identity.py:22-87` — exact `EmbeddingProfile` fields/fingerprint and `IndexIdentity('qdrant', base_collection, profile)` derive the physical collection; compare the full profile and identity, not only dimension or base name.
- `hello_agents/memory/rag/embedding_runtime.py:48-115` — `build_rag_embedding(values, backend='qdrant') -> RAGEmbeddingRuntime` builds the supported real `simple` or `siliconflow` engines. It has no generic `check_ready`; a future packet must use a real provider call and report the exact full profile. Task 02 must not instantiate or call an embedding engine. `EmbeddingSettings` caps SiliconFlow request timeout at 20 seconds.
- `app/postgres_vector_generations.py:67-98,145-178,409-414` — `VectorScope(tenant_id, 'rag'|'episode', namespace, identity)`, `PostgresVectorGenerationAuthority.read_head(scope)`, and `publication_receipt(scope, generation_id)` read persisted authority. `read_head` returns `missing` for absent authority; this is **not** an empty baseline.
- `app/postgres_snapshots.py:75-88` — `PostgresSnapshotRepository.read(user_id, 'history'|'memory') -> VersionedSnapshot | None`; `None` is absent, not a synthetic empty snapshot.
- `app/postgres_history_document_witnesses.py:31-48,66-73,105-149` — `read_current(rag_scope) -> Pairing` validates registered identity, head and publication receipt; `document_evidence(user_id, history.data)`, `check_document_pairing(pairing, evidence, history.version)`, and `verify_retained_references(scope, evidence)` validate History witness and retained refs. Missing witness is acceptable only under that existing method's exact snapshot-version rule.
- `app/published_episode_reads.py:283-296,418-424` — `PublishedEpisodeReadFactory(VectorGenerationService(...), trusted_episode_scope).open_operation(user_id)` captures and validates a paired episode head, publication receipt, Memory snapshot, documents and vector points. Its internal `_capture_bundle` is not a public runtime contract; use the public `open_operation` for the readiness proof.
- `app/import_memory_publication.py:922-963` — current issuance preflight requires exact RAG namespace `pdf_{user_id}`, a real History snapshot and matching episode scope/profile; Task 02 must reject inconsistent identity before later Task 04 can issue publication.
- `tests/integration/test_postgres_auth_sessions.py:28-61`, `tests/integration/test_s3_object_store.py:15-39`, `tests/integration/test_import_memory_publication.py:36-52` — disposable schema/pool, versioned bucket, and real Qdrant fixture patterns. The new test file may reuse the S3 and publication helpers where scopes match, but must create its own schema fixture: `shared_database` overwrites `DATABASE_URL` with its test DSN, which would falsely appear to be an ordinary identity collision in this packet's explicit configuration parser.
- Task 01 delivered `app/postgres_import_leases.py:91,483` and `app/import_publication_recovery.py:266` optional keyword-only `allowed_user_ids`; this runtime must never produce an empty or `None` isolated filter.
- Preserve the 15 pre-existing modified paths listed in `REVIEW.md`; none is owned by this packet. Treat their current raw bytes as the pre-existing worktree state.

## Prerequisites

### Packet dependencies

- `01-tenant-selection.md` must remain `done` at accepted source base `d81f0c3494d02bca51fb312c602d215edf6f98a0`; recheck its selector signatures before Task 02 begins.

### Repository/base state

- Base commit: `d81f0c3494d02bca51fb312c602d215edf6f98a0`. Check `git status --short` and preserve all 15 pre-existing modifications; do not normalize their line endings.
- Migration head in this base is `20261007_16` (`migrations/versions/20261007_16_publication_dependency_isolation.py:6`). The isolated schema must already have the complete head; Task 02 must not upgrade it.
- Runtime configuration is explicit; it may never infer the two account IDs or vector profiles from an existing local runtime, retained resource, or an empty table.

### External prerequisites

- Unit tests need no services. Real readiness test needs a **new**, disposable schema, versioned bucket and separate RAG/episode collections on the assigned test PG/S3/target-Qdrant services, plus two fixture accounts with real paired baselines. The test owns and tears down only its generated resources.
- The existing private test environment provides `POSTGRES_TEST_URL`, `S3_TEST_ENDPOINT`, `S3_TEST_ACCESS_KEY`, `S3_TEST_SECRET_KEY`, and `GENERATION_QDRANT_TEST_URL`. A missing required endpoint makes the real test skip; a skip cannot satisfy acceptance. Do not print credentials or DSNs.
- The protected source Qdrant is exited. Port numbers are observations, not resource identity or authority; verify test service identity immediately before a real test run.

## Explicit change boundary

### Allowed files

- Create: `app/isolated_import_runtime.py` — settings, resource ownership, readiness probes, cleanup.
- Create: `tests/test_isolated_import_runtime.py` — service-independent settings/ownership tests and marked real-service readiness test using existing disposable fixture helpers.
- No other source, test, migration, manifest, API, script, or fixture file is owned. If a separate integration file proves necessary, return a reality-conflict report for a packet amendment before editing it.

### Allowed behavior changes

- Add only new opt-in symbols. Construction of `IsolatedImportSettings` from a mapping must be pure and fail before network I/O. `open_isolated_runtime(settings)` may contact only the configured isolated PostgreSQL identity; ordinary provider probes are lazy.
- Expose `recovery_ready()` and `ordinary_ready(user_id)` as read-only, repeatable probes. They may return false for unavailable dependencies; configuration or identity errors fail closed without exposing secrets.

### Forbidden changes

- Do not edit `app/bootstrap.py`, `app/deployment.py`, `app/runtime.py`, existing routes, selectors, publication/recovery implementations, migrations, existing tests or any of the 15 dirty files.
- Do not invoke `ensure_collection`, create bucket/schema, initialize missing head/receipt/snapshot, write to any baseline, or call a product local/ordinary distributed fallback.
- Never connect to protected source Qdrant; never point this packet at retained schema `cutover_combined_refs_20260930_0f7592dc3978`, bucket `cutover-paired-bf20-69fcab0f0cbd4300a0545f2008c15779`, or retained generation `571fd200-2e6b-46ad-b012-24f8ae6d01e9`. Reject configured identifiers that match those protected identities and reject ordinary configured resource identity reuse.
- No public registration, HTTP routes, background Worker thread, embedding call, or import task claim.

## Interface contract

### Consumes

- `DeploymentSettings.from_env(env)` only to compare existing ordinary resource identity when ordinary keys are present; do not require ordinary distributed bootstrap to validate/open.
- `PostgresDatabase`, `S3ObjectStore`, `QdrantVectorStore`, `IndexIdentity`, `VectorScope`, `PostgresVectorGenerationAuthority`, `PostgresSnapshotRepository`, `PostgresHistoryDocumentWitnessRepository`, `PublishedEpisodeReadFactory`, and `VectorGenerationService` with the signatures above.

### Produces

- `IsolatedImportSettings.from_env(role: Literal['api', 'worker'], env: Mapping[str, str] | None = None) -> IsolatedImportSettings`: frozen dataclass; any invalid role, disabled flag, missing key, malformed value or unsafe identity raises `IsolatedImportConfigurationError(ValueError)` with key/code only, no values or credentials. The same validation runs in `__post_init__` for direct construction.
- Required exact isolated keys: `ISOLATED_IMPORT_ENABLED=1`, `ISOLATED_IMPORT_DATABASE_URL` (PostgreSQL DSN with exactly one explicit `options=-csearch_path=<schema>` and no fallback path), `ISOLATED_IMPORT_SCHEMA`, `ISOLATED_IMPORT_S3_ENDPOINT_URL`, `ISOLATED_IMPORT_S3_BUCKET`, `ISOLATED_IMPORT_QDRANT_URL`, `ISOLATED_IMPORT_RAG_COLLECTION`, `ISOLATED_IMPORT_EPISODE_COLLECTION`, `ISOLATED_IMPORT_RAG_PROFILE_JSON`, `ISOLATED_IMPORT_EPISODE_PROFILE_JSON`, and `ISOLATED_IMPORT_TRUSTED_USER_IDS` (comma-separated canonical UUIDs). Exact profiles use the `EmbeddingProfile` dataclass field names, with no unknown/missing fields other than dataclass defaults explicitly accepted in the packet test. Both full profiles must be explicitly provided; no BGE-M3 to SimpleEmbedding substitution.
- Private credentials remain in the existing environment mechanism: `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `QDRANT_API_KEY` if required by the configured target, and the password in the isolated DSN. `S3_REGION` defaults to `us-east-1`. All credential-bearing settings fields use `repr=False`; `safe_summary()` may report booleans and nonsensitive identity labels only. The S3 pair is all-or-none. Never echo raw DSN, query string, token, client exception or source content in public status/errors.
- Optional isolated keys and defaults: `ISOLATED_IMPORT_PG_POOL_MIN=1`, `ISOLATED_IMPORT_PG_POOL_MAX=10`, `ISOLATED_IMPORT_LEASE_SECONDS=60`, `ISOLATED_IMPORT_HEARTBEAT_SECONDS=10`, `ISOLATED_IMPORT_ATTEMPT_SECONDS=300`, `ISOLATED_IMPORT_PROVIDER_TIMEOUT_SECONDS=30`, `ISOLATED_IMPORT_STOP_WAIT_SECONDS=10`, `ISOLATED_IMPORT_PROVIDER_MAX_RETRIES=2`. Positive integral values, `min<=max`, `heartbeat<lease/3`, `provider_timeout<=attempt`, and finite retry cap `0..2` are mandatory. The 30-second default is the later Task 04 outer attempt-call budget; when Task 03/04 creates a SiliconFlow runtime, its **effective** request timeout must be `min(20, configured_provider_timeout)` to satisfy the existing provider contract. Task 02 only validates these numbers.
- `trusted_user_ids: frozenset[str]` is immutable, nonempty and made only of canonical UUID strings; reject duplicates and whitespace-only entries rather than silently broadening or dropping users. For this first two-account milestone require exactly two IDs.
- Startup contract for the factory specified below: rely on already validated typed settings, open its **own** `PostgresDatabase` pool, then verify `current_schema()` and `current_setting('search_path')` resolve exactly the configured isolated schema and `alembic_version` equals `20261007_16` in that schema. Reject `public`, retained schema, missing schema/ledger/tables or a search path with fallback to `public`; close an opened pool on any startup failure. If `PostgresDatabase.open()` itself raises before `_opened=True`, explicitly close its underlying pool in the exception path because the current `PostgresDatabase.close()` is a no-op in that state. Cover this exact injected half-open failure in a unit test. Do not connect to S3/Qdrant during this gate.
- `ProviderReadiness`: frozen, internal-report dataclass with `available: bool`, `rag_profile: EmbeddingProfile`, `episode_profile: EmbeddingProfile`; the profile fields use `repr=False`. `available=True` is a claim from the later caller, not proof generated by Task 02. A future Task 03/04 provider adapter must derive this report from actual profile-matched engine calls before admission.
- `open_isolated_runtime(settings: IsolatedImportSettings, *, provider_probe: Callable[[], ProviderReadiness] | None = None) -> IsolatedImportRuntime`: the optional hook is called fresh during each `ordinary_ready` check. `None` is fail-closed. Task 02 does not supply a real hook; a unit fake tests only the composition contract. A later packet must own the real provider adapter. This is a deliberate refinement of the plan's proposed one-argument signature so Task 02 does not falsely claim provider health.
- `IsolatedImportRuntime.settings`, `.database`, `.role`, `.trusted_user_ids`, `.recovery_ready() -> bool`, `.ordinary_ready(user_id: str) -> bool`, `.close() -> None`, and context-manager entry/exit. `close()` is idempotent and closes this runtime's PG pool and any clients it created; two API instances and a Worker have distinct pools/clients. The API role has no publisher, claim loop, embedding engine or background thread. Worker execution remains Task 04.
- `recovery_ready()` reruns database ping and schema/ledger identity checks and returns true only when those pass. It requires no object store, Qdrant, embedding provider, head, receipt, History or Memory. A configured identity mismatch is a hard fail-closed error; transient PostgreSQL outage returns false.
- `ordinary_ready(user_id)` returns false for malformed, nontrusted or inactive users, any unavailable ordinary dependency, missing/incompatible baseline, absent provider hook, unavailable provider report or report profiles unequal to the exact configured profiles. It never mutates data. It checks `recovery_ready()`, `S3ObjectStore.check_ready()`, both exact **physical** collections with `QdrantVectorStore.require_collection(..., profile.dimension, profile.distance)`, and the per-user pairing procedure below. Collection existence alone never counts as provider readiness. Keep internal diagnostics sanitized.

### Invariants

- The settings' full `IndexIdentity` for RAG and episode binds configured base collection and profile; physical collection names are derived, never supplied from an untrusted head. Namespace is exactly `pdf_{user_id}` for RAG and `episodes` for episode.
- For RAG, read a real History snapshot and `PostgresHistoryDocumentWitnessRepository.read_current(rag_scope)`; compute `document_evidence`, call `check_document_pairing`, then `verify_retained_references`. Require a nonmissing head and publication receipt paired by `read_current`. If no History snapshot, head, receipt or valid witness/snapshot pairing exists, ordinary is false even for an empty corpus.
- For episode, `PublishedEpisodeReadFactory(..., trusted_episode_scope).open_operation(user_id)` must prove the published head/receipt and complete Memory/vector/document pairing. Missing Memory snapshot or mismatched profile makes ordinary false; do not bypass its checks by looking only at a head.
- Recovery readiness never depends on ordinary readiness. A provider outage, absent tenant baseline or held/unknown publication cannot suppress database-backed recovery. This packet does not decide whether any queued recovery operation succeeds.
- The provider hook must return `ProviderReadiness(available=True, rag_profile=<exact settings RAG profile>, episode_profile=<exact settings episode profile>)` to permit ordinary readiness. Task 02 checks type and exact equality and treats absent, false, mismatched, or throwing hooks as ordinary false. It does not make a provider call. Task 03/04 must add a real adapter that constructs the correct engine(s), verifies their profiles and actual availability, then supplies the report; until that is delivered, no ordinary admission may interpret storage/baseline checks as fully ready.
- RAG/History and episode/Memory public reads use separate transactions, so one pass is not a cross-store snapshot. Before and after the external checks, capture a complete per-user PostgreSQL authority tuple in one short, single-statement snapshot, using a parameter-bound SQL aggregate/CTE. Include active user status, both full index/head/publication-receipt rows, the History witness, both snapshot versions **and payloads**, all `memory_documents` rows including content/metadata, and all tenant document-object references. Sort every multirow projection deterministically; compare exact values, not only head/snapshot versions. Keep these captures free of external I/O and do not hold user locks across S3/Qdrant/provider calls. Compare the RAG validated `Pairing`, episode operation head, History evidence and snapshot versions to the final authority tuple. If captures differ or public validation disagrees, retry the complete proof at most once, then return false. `PostgresMemoryDocumentStore` can change rows without advancing head/snapshot versions; tests must delete a row during episode vector scroll while leaving those versions unchanged and prove readiness cannot accept the stale bundle. Task 03 must still recheck admission because a readiness result is a point-in-time observation, not a lasting grant.
- Equal before/after tuples alone are insufficient: an invalid row set could become valid during public operation capture, then return to the same invalid state. Validate the **final captured data itself**: full trusted index/head/receipt/witness pairing, History evidence and references, and Memory snapshot-to-document pairing. For episode data, apply the existing pure projection and metadata/canonical comparison rules (`published_episode_reads._episode`, `_metadata`, `_json`, or equivalent exact pure validation) to the final snapshot and rows, rejecting duplicates, foreign users, missing rows and payload differences. Do not call the private `_capture_bundle`. Bind final episodic membership and payloads to `operation.scroll(min_importance=...)`; choose the finite minimum of zero and all final episodic importance values, so negative-importance entries are included. Check exact IDs, payload equality, receipt count and trusted profile/head against the final tuple. Default `scroll()`/`count()` with threshold zero is not a full-corpus proof. Recheck final RAG witness and retained-reference evidence against the already verified trusted proof. Include a deterministic invalid-before → valid-public-capture → invalid-after ABA case; it must remain ordinary-unready even when all revisions and before/after tuples compare equal.
- Specifically require `operation.receipt` generation/revision/snapshot version, expected count and content digest to equal the final published receipt projection; the public factory has verified the vector manifest against that digest. Full-scroll IDs/payloads must then equal the final pure snapshot/document projection and its count. An empty baseline still requires an actual published zero-count receipt and no extra episodic document rows; absence of a row set or default-filter emptiness is not evidence of a valid empty publication.

## Required behavior

1. Default/`0`/`false`/missing isolated enablement fails before any client or pool is created. Settings must reject malformed URLs (wrong schemes, credentials embedded in endpoint URLs, ambiguous schema options), public/retained schema or bucket, configured ordinary resource identity reuse, duplicate/equal RAG and episode collections, profile mismatch, empty/invalid trusted IDs, and timing violations before I/O. The opened connection then proves the actual database/schema identity, since string comparison alone cannot rule out DSN aliases.
2. Resource identity is explicit and dedicated. Compare isolated values to ordinary `DATABASE_URL`, `S3_BUCKET`, and ordinary Qdrant collection keys when present; never silently inherit them. Reject the known protected/retained identifiers above. Test-only generated schema, bucket and collections must be unique and newly created by the integration fixture.
3. `open_isolated_runtime` validates PostgreSQL identity and migration head. It checks the following exact in-schema relations by qualified name and rejects a missing one: `alembic_version`, `users`, `auth_sessions`, `user_snapshots`, `memory_documents`, `import_batches`, `import_tasks`, `import_objects`, `import_task_attempts`, `import_user_schedule`, `user_mutation_leases`, `document_objects`, `vector_indexes`, `vector_heads`, `vector_generations`, `history_document_witnesses`, `import_publication_evidence`, `user_publication_gates`, `generation_reservations`, `import_publication_private_payloads`, `import_publication_recovery_queue`, `import_publication_recovery_leases`, `import_publication_recovery_token_issuance`, and `import_publication_recovery_schedule`. If S3, Qdrant or embedding provider is down later, the opened runtime still reports recovery-ready as long as PG identity is healthy. Two independently opened API-role runtimes and one Worker-role runtime must have separate PG pool objects and deterministic close paths.
4. `ordinary_ready(user_id)` verifies active user status from PostgreSQL for the exact allowlisted UUID and repeats provider/baseline checks on each call, so a stale process cache cannot admit a newly disabled user or a baseline that changed. This probe does not make upload admission atomic; Task 03 must recheck during admission.
5. Public-safe status contains only `recovery_ready`, user-scoped `ordinary_ready`, and sanitized codes; no raw exceptions, URL, SQL, object/version keys, tokens, source bytes, full snapshots, or any `EmbeddingProfile` field. Even profile `endpoint` may contain credentials or query text despite `validate_profile` accepting it as a string; keep full profiles out of `repr(settings)`, `repr(ProviderReadiness)` and `safe_summary()`.

## Implementation guidance

- Build the pure parser first and cover direct settings construction as well as `from_env`; use URL parsing plus explicit PostgreSQL `options` validation, not string suffix checks. Do not call `DeploymentSettings.validate()` on ordinary local mode as a prerequisite to isolated mode.
- Use exact `IndexIdentity`/`VectorScope` construction and existing read-only methods. `QdrantVectorStore.require_collection()` is a read-only validation call; avoid `ensure_collection()`.
- Use one owned PG pool per runtime; make S3/Qdrant client creation lazy so their outage cannot prevent recovery. `QdrantVectorStore` has no `close()`; close the owned `raw.client` when it supports `close()`. API role may create read-only Qdrant clients for baseline/readback, but no embedding engine or publication service. Worker role still has no task loop until Task 04.
- Separate configuration errors and durable identity conflicts from temporary provider failures in internal diagnostics. Keep returned booleans and public-safe summary deterministic. On a partial open, close every object already created.
- The real test should provision two distinct user baselines using existing fixture patterns. Name real-service test functions `test_real_*` so they can be selected without adding a marker registration file. Build a dedicated settings mapping that keeps any genuine pre-existing ordinary `DATABASE_URL` for identity comparison and does not inherit a fixture-injected test DSN as an ordinary value. Assert the exact generated resource IDs before touching services, then test: with **no** provider hook both users' ordinary readiness is false; with a clearly labeled synthetic, exact-profile hook the storage/baseline composition returns true. This synthetic result is not accepted as real provider health. Also test untrusted/disabled user false, missing History or Memory false, identity/profile mismatch false, concurrent publication changing one head between probes fails or performs the single bounded retry, S3 or Qdrant outage yields ordinary false while recovery remains true, PG outage yields both false, and no newly created table/collection/bucket appears after readiness calls.

## Acceptance criteria

- [ ] Pure settings tests show default-off and every missing/contradictory/unsafe/timing case fails before any network constructor; direct dataclass construction cannot evade the same checks; `repr`/safe errors never include credential markers.
- [ ] Exactly two canonical trusted UUIDs become a nonempty `frozenset`; both role values produce isolated runtime objects with separate resource ownership and idempotent cleanup. API role starts no Worker/publisher/embedding background activity.
- [ ] An actual newly created, migrated isolated schema validates its own `current_schema`, search path and ledger; a wrong schema or ordinary/retained identity is rejected. Startup only needs PG.
- [ ] An injected `PostgresDatabase.open()` half-open failure releases the underlying pool despite `_opened=False`; no provider constructor runs and no leaked pool remains. A test settings mapping retains a genuine ordinary identity while ignoring only the new test fixture's DSN substitution.
- [ ] Real versioned bucket and both exact physical Qdrant collections plus two pre-attested user baselines yield `recovery_ready() is True`. With no provider hook, `ordinary_ready(user_id) is False` for each; an explicitly synthetic exact-profile hook makes the storage/baseline composition true. Record that this is **not** real provider readiness or admission acceptance. An absent or contradictory History/RAG/episode/Memory baseline yields ordinary false without creating substitute data.
- [ ] With PG still healthy, unavailable S3 or Qdrant yields recovery true and ordinary false; with PG unavailable, both are false. All probe operations are read-only.
- [ ] Absent/false/mismatched/throwing provider hooks yield ordinary false while recovery remains true; a correctly typed exact-profile synthetic hook is only a contract test. Task 03/04 remains responsible for real provider-key, provider-health and embedding-profile acceptance.
- [ ] A forced RAG/episode head or snapshot change between baseline reads cannot make a mixed publication appear ordinary-ready; at most one complete recheck is attempted.
- [ ] An independent Memory-document mutation with unchanged head/snapshot versions is observed by the complete authority tuple. A controlled row deletion during episode vector scroll either fails validation immediately or makes the whole bounded recheck return false; the old captured episode bundle cannot make readiness true.
- [ ] An ABA test with equal invalid before/after row sets and a valid intermediate public episode capture cannot return true. Final-data validation binds all episodic entries, including negative importance, to the verified public operation without treating default-threshold results as the full corpus.
- [ ] No existing ordinary application behavior or the 15 pre-existing dirty paths changes.

## Test and verification commands

Only the root-assigned Sol High pytest lane runs these, serially, after a separate GO. Run from repository root with `D:\python_self_agent\venv\Scripts\python.exe` and a unique in-repo basetemp for each command. Never use the source Qdrant endpoint.

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/test_isolated_import_runtime.py -k 'not real' --basetemp=.runtime/pytest-isolated-runtime-p2-unit-n1 --junitxml=.runtime/isolated-import-runtime/p2-unit-n1.xml
```

Expected: all unit cases pass, exit 0, zero errors/failures/skips. Before implementation, RED should be only the absent isolated module; record actual exit and XML rather than assuming it.

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/test_isolated_import_runtime.py -k real --basetemp=.runtime/pytest-isolated-runtime-p2-live-n1 --junitxml=.runtime/isolated-import-runtime/p2-live-n1.xml
```

Expected: real PG/S3/target-Qdrant cases pass, exit 0, **zero skipped**. Stop and report missing prerequisites if any fixture skips; do not count a skip as acceptance. The real-service cases must have `test_real_*` names and create unique isolated schema/bucket/collections with teardown scoped to those exact generated names.

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/test_isolated_import_runtime.py --basetemp=.runtime/pytest-isolated-runtime-p2-full-n1 --junitxml=.runtime/isolated-import-runtime/p2-full-n1.xml
```

Expected: the **entire** new file passes, exit 0, zero skips/errors/failures. Compare its collected test IDs with the unit and real selections; this catches cases accidentally excluded by the `-k` naming split. Do not sum overlapping run counts.

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/test_deployment_settings.py tests/integration/test_postgres_auth_sessions.py tests/integration/test_import_memory_publication.py --basetemp=.runtime/pytest-isolated-runtime-p2-regression-n1 --junitxml=.runtime/isolated-import-runtime/p2-regression-n1.xml
```

Expected: exit 0, zero failures/errors/skips with disposable prerequisite services. Do not add counts from overlapping reruns. Save actual command, output, exit, XML hash and service/resource identities in the handoff.

## Stop conditions

Stop with the repository's reality-conflict report if a cited constructor, public read method, fixture or migration head differs; Task 01's accepted source is absent; the two-user baseline cannot be attested within the allowed test file; the selected schema cannot be proven without changing an existing module; a provider check would write/create a resource; a credential/config key collides with ordinary runtime; or acceptance needs any file outside the two owned paths. Do not mark a draft ready or broaden ownership silently.

## Implementation handoff

After this packet is reviewed, assigned and implemented, replace this placeholder with:

```markdown
## Implementation handoff

- Packet: `2026-10-10-isolated-import-runtime-02`
- Status: `done | blocked`
- Delivered: <specific trusted configuration and readiness result>
- Files changed: <only the two owned paths, with purpose>
- Interfaces added or changed: <exact signatures and failure behavior>
- Acceptance evidence: <criterion-by-criterion real observations>
- Verification: <exact commands, exit, pass/fail/skip counts, XML hashes and resource identities>
- Scope confirmation: changed only allowed files: yes/no; forbidden areas untouched: yes/no
- Deviations: none or approved precise deviation
- Residual risks/follow-ups: <specific limits>
- Commit: <hash or not committed>
```
