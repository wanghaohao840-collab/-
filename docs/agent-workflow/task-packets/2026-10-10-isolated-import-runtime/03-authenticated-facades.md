---
id: "2026-10-10-isolated-import-runtime-03"
title: "Shared sessions and transaction-bound isolated import admission"
status: "ready"
parallel-safe: false
depends-on: ["2026-10-10-isolated-import-runtime-02"]
base-commit: "44e97cf755da7360059342b7b9e9279062f8b9e1"
owner: "GPT-6 Sol High"
---

# Task Packet: shared authenticated import facades

## Assignment gate

Task02 is accepted at source commit `18e1f1b591e767373c9f81e4fd45c5c2f5b882f8`; the base above adds only its progress record. Root and Sol checked current interfaces; Sol's private report is `.runtime/isolated-import-runtime/p3-plan-reality-sol-n1.md`, SHA256 `815d005d610e3ed0e8498b02289f50ee7cd21625e193446f846a8b3d8f88f837`. The three existing-source seams below amend the source plan's earlier three-new-file boundary. Independent Astra High corrective review n2 is READY with no remaining blockers: `p3-plan-astra-n2.md`, SHA256 `a67ed9ab4525eb59020cfce41ecc11039096ca5f49ade36c5184baca9a9c8994`. It binds the pre-bookkeeping packet `af908ff93e86794a78f47a3fac07194fa1329a005c42c5043eea57a9f0fae8e0` and synchronized source plan `7dfc64c1e0dd5e620fa466addbdf7dc6bada9c75f06b2650b48821fecfaa2c0a`. Root authorizes the assigned Sol High implementation within this packet; every pytest stage still requires reviewed runner and explicit root GO.

## Goal

Two independently opened isolated API-role runtimes share persisted login/session/logout authority. The authenticated facade refuses untrusted users and invalid CSRF before consuming uploads, and commits staged import references only after rechecking the successful readiness proof in the same transaction as batch creation. Tenant-bound status and published-document readback use PG/S3/published Qdrant authority without a local runtime or embedding engine.

## Non-goals

- No HTTP routes, registration, cancel/retry, Worker loop, publication/recovery execution, schema initializer, migration or production composition.
- No real embedding provider adapter in this packet. A missing provider hook still refuses ordinary admission. Synthetic hooks are test composition evidence only; Task04 must supply the real exact-profile adapter before Task05 exposes live admission.
- No S3 rollback claim: failed SQL admission can leave newly staged immutable objects without committed references. Do not delete retained objects or add a cleanup subsystem here.

## Relevant current interfaces

- `app/postgres_auth.py:14-49`: `PostgresAuthService(database).authenticate(username,password) -> User`; registration exists but must not be exposed.
- `app/postgres_sessions.py:15-105`: frozen `SharedSession` hides token/CSRF in repr. `PostgresSessionRepository.create/get/validate_csrf/delete` persists sessions, locks token before fresh DB-clock expiry check, and rechecks active user. Reuse these methods.
- `app/session.py:24-32`: `UserSession` requires a local runtime and assistant. Do not construct it or fake those fields; the new facade returns existing `SharedSession` instead.
- `app/isolated_import_runtime.py`: accepted settings, owned pool/lazy clients, complete single-statement authority capture and final proof validation. `ordinary_ready` currently returns only a boolean. Preserve this public result while adding the proof/cursor seam below.
- `app/postgres_import_artifacts.py:38-80,120-132`: `submit_uploads(user_id,uploads)` verifies active user before stream access, stages immutable S3 objects, then locks active user and rejects unresolved gate before batch/task/reference creation. It currently has no readiness-stamp callback.
- `app/import_persistence.py:216-232`: inherited PG control repository `list_batches(user_id,limit=50)`, `get_batch(user_id,batch_id)`, `get_task(user_id,task_id)`; missing items return None.
- `app/import_publication_evidence.py:48,621-633`: `PublicationEvidenceError` covers unrelated failures as well as unresolved gates. The gate query uses exact user and requires READ COMMITTED caller-owned transaction after the user lock.
- `app/postgres_history_document_witnesses.py:105-169`: trusted RAG pairing, full History evidence and pinned retained-reference verification.
- `app/postgres_document_objects.py:121-163`: History-visible pinned S3 read with deletion fence and record hash; alone it does not prove vector membership.
- `app/vector_generation_service.py:130-132` and `hello_agents/memory/storage/generation_vector_store.py:300-364`: no-engine generation view and full payload scroll with `expected_manifest=(count,digest)`.
- `app/published_rag_reads.py:79-118`: requires a real matching embedding runtime. Do not fabricate one to satisfy readback.
- `api/schemas/auth.py`: cookie response consumers need only username/CSRF. Existing auth router exposes register and cannot be mounted wholesale by Task05.

## Prerequisites and preserved state

- Task02 packet must remain done. Its source/test raw SHA are `5ed6e0614596689969c97ac64f6754e9228b8a7128831da633ad550854fdb0a2` / `b16832c62a591707534654692200a67a366d5dfe48d6f15038f0b518b0f87513`. Its final independent report is `p2-final-accepted-astra-n4.md`, SHA256 `1ee8dfa7a3bc225b88b42c6b15bb5f1eab73e0c1d14fe7e11f7d271b3c63cd26`.
- Preserve the 15 pre-existing raw modified paths: `app/coordination.py`, `app/document_library.py`, `app/history.py`, `app/import_models.py`, `app/import_worker.py`, `app/memory_repository.py`, `app/qa_deletion.py`, `app/qa_persistence.py`, `app/qa_repository.py`, `app/recovery.py`, `migrations/versions/20260926_01_business_schema.sql`, `migrations/versions/20260928_09_import_leases.py`, `tests/conftest.py`, `tests/test_import_worker.py`, `tests/test_user_mutation_coordination.py`.
- Fixed interpreter: `D:\python_self_agent\venv\Scripts\python.exe`. Only the root-assigned Sol pytest lane may run tests, serially after runner review/GO.
- Live fixtures use only the assigned disposable PG/S3/target Qdrant identities recorded by Task02's final receipts. Last observed ports 59497/59498/49889 are not identities; verify IDs, named volumes, ports, health before each live run. Protected source Qdrant stays exited. Never touch retained schema `cutover_combined_refs_20260930_0f7592dc3978`, bucket `cutover-paired-bf20-69fcab0f0cbd4300a0545f2008c15779` or generation `571fd200-2e6b-46ad-b012-24f8ae6d01e9`.

## Explicit change boundary

Create only:

- `app/distributed_import_sessions.py`: PG session facade and code-only trusted-session rejection.
- `app/distributed_import_service.py`: authenticated admission/status/readback facade and sanitized result/errors.
- `tests/integration/test_distributed_import_facades.py`: pure compatibility/bomb tests and dedicated real disposable fixtures/cases.

Modify only:

- `app/isolated_import_runtime.py`: opaque readiness stamp, transaction-cursor comparison and public lazy storage-client accessor; retain accepted settings/ordinary/recovery semantics.
- `app/postgres_import_artifacts.py`: optional keyword-only transaction callback at the existing active-user/gate-to-batch boundary. Defaults preserve all current calls.
- `app/import_publication_evidence.py`: unresolved-gate exception subtype emitted only by the exact authoritative gate branch; preserve base exception compatibility and all SQL/lock/fence behavior.

No other source/tests/routes, dependencies, schema, selectors or publication/recovery mechanics are owned. Existing test files may be run, never edited. Root owns packet/plan/progress bookkeeping. Return a reality conflict rather than expanding this boundary.

## Interface contract proposed for review

### Sessions

`DistributedImportSessions(runtime: IsolatedImportRuntime)` consumes its PG pool and trusted UUIDs. Produce `login(username,password)->str`, `get_session(token)->SharedSession`, `validate_csrf(token,csrf_token)->SharedSession`, `logout(token)->None`. Never expose register. Authenticate then check allowlist before session creation; resolve token/active user and check allowlist on every later use. Preserve existing invalid-session/CSRF exception types; introduce a code-only `UntrustedSessionError(PermissionError)` for 403. Token and user IDs come from the server repository, not request bodies.

### Runtime stamp and storage clients

- `prepare_admission(user_id: str) -> IsolatedAdmissionStamp | None`: run the same complete ordinary proof as Task02, including a fresh provider report, then retain its **same final validated** authority tuple in an opaque process-local stamp. Never call ordinary_ready then recapture a different tuple and stamp it without its own validation.
- `recheck_admission(cursor, user_id: str, stamp: IsolatedAdmissionStamp) -> None`: require active READ COMMITTED caller-owned transaction. The caller has already locked this user and checked its active status/gate. Compare a fresh single-statement authority tuple from this exact cursor to the stamped final tuple; perform no provider/S3/Qdrant I/O, no new connection/transaction and no other-user lock. Mismatch/closed runtime/forged/foreign-runtime/foreign-user/reused stamp raises a code-only `IsolatedAdmissionRejected` and prevents SQL commit.
- Stamps are owner-issued and bound to runtime/user, single use at admission attempt, with hidden payload/repr. Direct construction, copies or replacement cannot authorize a request; validate identity against the issuing runtime's live registry. Keep lifecycle/registry locks process-local and do not hold them across external proof I/O. Closing runtime invalidates outstanding stamps. A failed attempt does not make its stamp reusable.
- `discard_admission(stamp: IsolatedAdmissionStamp) -> None`: idempotent owner-bound invalidation. Deep-copy/freeze the verified tuple at issuance, use original object identity for registry membership, and atomically consume before any cursor comparison. Never restore a stamp after SQL rollback. The facade must discard in `finally` on every path after successful prepare, including invalid stream/size, S3 failure, gate/disabled-user failure before the callback, and callback failure. No unbounded registry/cache; close invalidates all entries atomically, and preparation completing after close cannot publish a usable entry. Copies/forged/foreign handles cannot consume a different original entry.
- `storage_clients() -> tuple[S3ObjectStore,QdrantVectorStore]` exposes the existing lifecycle-fenced lazy clients to the trusted facade. It does not create buckets/collections, attest provider readiness or run during facade startup.
- `ordinary_ready` remains a point-in-time boolean, not a cached stamp or admission permission. Existing Task02 tests must remain valid.

### Artifact and gate seams

- Extend `PostgresImportArtifactService.submit_uploads(user_id,uploads, *, admission_recheck: Callable[[object,str],None] | None = None)`. If supplied, call it once after `_require_active_locked(cursor,user_id)` and before batch/task/reference mutation, inside the same cursor/transaction. Never consume streams again on callback failure or silently retry the whole request. None retains old behavior.
- Add `UnresolvedPublicationGate(PublicationEvidenceError)`. Emit it only where the existing exact-user unresolved gate query actually finds a row. No lock/query changes, no user data in its public error. Existing base catches still work.

### Import facade and readback

`DistributedImportService(runtime, sessions)` creates no external clients until a request. Require `sessions.runtime is runtime` and its repository uses `runtime.database`; both objects must have the same trusted API-role composition. Tokens created in another API runtime sharing the trusted database still resolve through this runtime's own server repository. Produce:

- `submit_uploads(token, csrf_token, uploads: list[ImportUpload]) -> ImportBatchSummary`: validate CSRF through server repository; check trusted active user; prepare successful stamp before touching streams or storage; call artifacts with its transaction callback. The callback first invokes the exact cursor-only session recheck below, then consumes/rechecks the stamp; no independent connection while locked. Discard the stamp in facade `finally` even if artifact fails before invoking the callback.
- `list_batches(token, *, limit=50)`, `get_batch(token,batch_id)`, `get_task(token,task_id)`: resolve current server session on every call, then user-scoped repository read. Recovery/status reads do not require ordinary provider readiness. Missing/foreign items expose identical not-found behavior.
- `read_published_document(token,document_id) -> PublishedImportDocument`: trusted tenant proof using History/RAG pairing/witness, matching reference, verified pinned S3 bytes and full no-engine published generation manifest. DTO contains only logical document ID, safe display filename, suffix, size and content bytes (repr=False), never object keys/version IDs/URLs/raw exception or snapshots.
- Bind before/after complete relevant PG authority with final-data validation against the external proof, at most one full retry. The readback tuple is one parameter-bound SQL statement containing active user, exact configured RAG index/head/full published receipt, full History version/payload and witness, all relevant document references, and all requested-document `qa_deletion_fences` rows/status/attempt_count in deterministic order. A queued/running fence or failed fence with attempt_count<3 is effective and must be absent in the final validated data. Equal invalid before/after fence states cannot pass through a briefly visible intermediate public read. This readback capture may live in the new service; do not broaden Task02's ordinary tuple or edit existing document-source code merely to add it.
- The generation view must equal the proven full head/receipt; scan the entire unfiltered corpus with `view.scroll(identity.physical_collection, expected_manifest=(pairing.expected_count,pairing.content_digest))`. Require trusted scope and at least one requested-document point in the verified corpus. Bind final index/head/receipt/witness/reference/History record to the proof. If using `read_document_bytes`, verify returned bytes' SHA256 and size against the **final proven** pinned reference, since that method independently captures its reference before S3 I/O. Derive safe filename/suffix from that same final History record. Reject changed/inconsistent/zero-chunk proof as 503; initially absent/foreign documents share 404. No user/DB lock across object/vector I/O.
- `DistributedImportError` exposes only code/status_code/retryable. Trusted rejection 403; missing item 404; only typed gate error **plus fresh exact-user unresolved gate evidence** yields 409 `needs_reconciliation`, retryable=false; base evidence errors, changed stamp/identity/proof and dependency uncertainty yield 503. Never classify all PublicationEvidenceError as 409.

### Exact session recheck and error boundaries

Add `DistributedImportSessions.recheck_csrf_in_transaction(cursor, token: str | None, csrf_token: str | None, *, expected_user_id: str) -> None`. The artifact already holds this user's active-row lock and has checked its gate. Hash the original server-resolved token using the existing SHA256 convention, select its auth_sessions row by token_hash **FOR UPDATE NOWAIT**, then obtain `clock_timestamp()` after successful row acquisition. Validate persisted user_id equals expected initially authenticated user, allowlist, nonempty CSRF using compare_digest, and expires_at strictly greater than that fresh clock. Retain the session lock through batch commit. Do not refresh or create a session here, acquire capacity locks, or call repository.get/validate_csrf on a new connection.

NOWAIT is required: session creation deletes expired tokens under its capacity lock and subsequently inserts a user-FK reference needing KEY SHARE; blocking user-first admission on that token can deadlock with login. Map `LockNotAvailable` to code-only 503 and abort without restaging or automatic retry. If logout commits first, missing token refuses; if admission has already locked its valid token, logout waits and linearizes after admission commit. Tokens expiring during user-lock wait must refuse using the post-session-lock clock.

The session facade retains InvalidSessionError/InvalidCsrfTokenError. Import facade maps invalid/expired/revoked session to sanitized 401, invalid CSRF or untrusted user to 403, foreign/missing item to 404. Only catch UnresolvedPublicationGate for possible 409: first let the artifact transaction roll back and release locks, then query the exact initially authenticated user's current unresolved gate in a fresh short read. Present evidence yields 409 needs_reconciliation/retryable=false; cleared/wrong-user/missing gate, classification query failure, base PublicationEvidenceError, NOWAIT conflict and stamp/proof uncertainty yield sanitized 503. Never classify while the failed transaction still holds its user lock.

## Required verification

- Pure bombs verify invalid session/CSRF/trust refusal before stream reads/S3 writes, stamp forgery/copy/replay rejection, callback location and unchanged None default, sanitized error/DTO repr. Keep missing-module preimplementation RED as actual evidence before writing new facade source.
- Stamp tests also cover concurrent consume (at most one success), dataclass replacement if applicable, callback SQL rollback, pre-callback stream/S3/gate/session failures followed by attempted replay, idempotent discard and close-during-prepare. No failed path leaves a usable retained stamp.
- Real generated schema/bucket/collections with two trusted accounts/baselines and one out-of-allowlist account: cross-pool token/CSRF resolution; logout/disabled-user refusal; A cannot read B batch/task/document; no register surface; valid immutable upload records source refs in PG.
- Deterministic staging-time gate insertion, account disable, session logout, RAG/episode head or History/Memory-row mutation: admission must fail with no committed batch/task/object reference. Assert the staged immutable object may remain. No lock across external I/O; bound lock wait/timeouts and inspect actual exit.
- Session race tests: logout-before-recheck refuses; logout waits behind a successfully locked token until admission commits; expiry during user-lock wait uses fresh clock; expired-token cleanup/login versus user-first admission produces NOWAIT refusal without deadlock, no batch/references, and login eventually completes. Use deterministic two-pool barriers and finite waits.
- Typed gate with current matching evidence returns 409, wrong-user gate and cleared gate/non-gate evidence error return 503. Recovery/status reads still work when provider/storage ordinary readiness is unavailable.
- Nonempty published-document readback validates witness, pinned version, full manifest and membership. Missing/foreign doc, changed head, missing S3 version, mismatched receipt/manifest and invalid-valid-invalid capture cannot return data. No synthetic embedding engine.
- Readback race tests explicitly insert a deletion fence during S3/vector reads, expose equal invalid fence states around an intermediate visible public read, and return different intermediate source bytes in a reference ABA. Final visibility/reference/hash validation must reject each, without a user lock across I/O.
- Preserve prior settings, lifecycle, freshness and all 15 dirty paths; independent Astra concurrency/consistency review after exact-byte testing.

## Test commands

Run serially with fixed venv, reviewed durable private runner, fresh in-repo basetemp/XML, actual exit receipts and before/after SHA. Runner supplies the five existing fixture keys only to live children. No service skip counts as acceptance.

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/integration/test_distributed_import_facades.py -k 'not real' --basetemp=.runtime/pytest-isolated-runtime-p3-unit-n1 --junitxml=.runtime/isolated-import-runtime/p3-unit-n1.xml
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/integration/test_distributed_import_facades.py -k real --basetemp=.runtime/pytest-isolated-runtime-p3-real-n1 --junitxml=.runtime/isolated-import-runtime/p3-real-n1.xml
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/integration/test_distributed_import_facades.py --basetemp=.runtime/pytest-isolated-runtime-p3-full-n1 --junitxml=.runtime/isolated-import-runtime/p3-full-n1.xml
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/test_isolated_import_runtime.py tests/integration/test_postgres_auth_sessions.py tests/integration/test_postgres_import_artifacts.py tests/integration/test_import_memory_publication.py --basetemp=.runtime/pytest-isolated-runtime-p3-regression-n1 --junitxml=.runtime/isolated-import-runtime/p3-regression-n1.xml
```

GREEN requires actual exits 0, zero failure/error/skip, whole-file IDs exactly unit union real, and distinct final file/regression IDs counted without repeated selections. Failed pretest or interrupted runs remain historical, not overwritten.

## Independent corrective contract, accepted for implementation

First Astra review `p3-plan-astra-n1.md` required four corrections: NOWAIT session recheck to avoid login cleanup/FK lock cycle; explicit final deletion-fence/reference/byte validation in readback; complete stamp issuance/discard/close lifecycle; exact error and constructor boundaries. These are specified above. Independent n2 review closed all four and confirmed implementability plus source-plan/Task05 synchronization. Ready bookkeeping does not alter that reviewed contract. Real provider admission stays deferred, never supplied by test hooks.

## Stop conditions and handoff

Stop and report the repository reality-conflict template for stale constructors, unmet predecessor, lock-order conflict, invalid verification or required edits outside the six owned files. Draft status must not be bypassed. After ready implementation, handoff must enumerate exact source/test SHA, actual commands/counts/XML/log/exit hashes, generated resource IDs, acceptance observations, preserved dirt, deviations and residual provider/Worker/API limits. Root alone finalizes done/commit/push after independent acceptance.
