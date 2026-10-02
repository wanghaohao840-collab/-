# Restart-safe unknown publication evidence — design draft

**Status:** DRAFT / REVIEW PENDING USER APPROVAL. This is an architecture proposal, not approved or implemented.
**Scope:** The opt-in Import-Memory RAG + episode publication path before independent Worker wiring.
**Out of scope:** runtime/API composition, historical episode migration, new-user episode baseline/profile,
production cutover, source Qdrant and retained-target mutation, and any new recall policy.

## Binding facts and invariant

- The approved distributed design makes PostgreSQL the structured authority, S3 the durable byte authority,
  Qdrant the vector backend, and requires a separate fenced Worker and restart acceptance.
- Current `ImportVectorPairPublicationService` allocates both generation UUIDs inside `_plan`; the frozen
  context, sealed descriptors and owner tuple live only in memory or an `ImportPairUnknown` exception.
- Current `ImportMemoryPublicationService` builds `_Expected` after `_prepare_document`, which already
  performs an immutable S3 put. `_Expected` and the new receipt projections are retained only in process;
  `_terminal` captures the projections, including immutable timestamps, inside the completion callback.
- `VerifiedDocumentRef` is a capability issued by its own `PostgresDocumentObjectRepository` instance.
  Its scalar reference is evidence for comparison, never a serialized/reconstructed publication token.
- C's success proof requires the fixed pair/task/audit, exact old and new receipts, History and Memory,
  Memory document row, pinned document, History witness, and deletion fences in one read-only snapshot.
  Current authority rows alone cannot manufacture the lost expected context or new receipt timestamp.
- Historical episodes keep original text, metadata, IDs and timestamps. Their vector recall remains
  `pending provenance`; this proposal neither re-embeds them nor calls their vectors faithfully migrated.

**Safety invariant:** before the first publication-related S3/Qdrant write, one committed PostgreSQL
transaction freezes the intent, permanently reserves both UUIDs and installs a durable user publication
gate. Every proof-related shared mutation checks that gate under the user lock. Terminal success commits
receipt projections with domain publication, but retains the gate until strict proof/ack. A restart only
proves or quarantines; it never replays an unknown write, reuses either UUID, treats absence as rollback,
or reconstructs a verified capability.

## Alternatives

| Approach | Benefit | Failure or cost |
| --- | --- | --- |
| **A. Recommended: attempt-bound PostgreSQL evidence row and bounded private payload** | Smallest new authority; one database transaction can bind terminal receipts to task/domain writes. The Worker can find Unknown without an exception object. | Requires splitting read-only planning from the S3 put, passing preallocated IDs into the pair service, and a recovery-only exact-abandon path. Full snapshots require explicit size/privacy limits. |
| B. Immutable S3 evidence object plus PostgreSQL manifest | Keeps large snapshots outside PostgreSQL and offers object versioning. | A lost S3 put response before the manifest creates an unindexed orphan; two systems must be reconciled before the first document write. Cannot make terminal receipt/object update atomic with PostgreSQL. More failure states than A. |
| C. Reconstruct expected data from current PostgreSQL authority on restart | No new full-snapshot payload. | Unsound: current state may have advanced, old receipts may have retired, timestamps and issuance provenance are lost. It would weaken C's proof and is rejected. |

If the bounded payload is too large, fail closed before publication; an S3 evidence design would
require separate approval and its own proof contract.

## Minimal durable shape (proposal, no migration written)

One `import_publication_evidence` row per `(user_id, task_id, task_lease_version)` with immutable
`schema_version`, `intent_payload` and `intent_hash`. In its insert transaction also create one
`user_publication_gates` row keyed by user and exact attempt and two `generation_reservations` rows keyed
globally by UUID. The gate survives ordinary lease expiry; reservations/tombstones survive evidence and
audit cleanup permanently. Enforce one unresolved gate per user. A private payload column may be
physically separate, but remains part of the same PostgreSQL transaction and user-scoped authority.

- **Identity/fence:** user, task, document, attempt worker, task lease token/version, user mutation owner,
  token/version, created/updated database timestamps. Retain exact types; do not accept a new attempt
  under an old evidence key. Gate states are `unresolved`, `resolved`; a terminal commit remains unresolved
  until strict detached proof/ack, including when its response is lost.
- **Pinned source:** accepted S3 bucket, key, immutable version, SHA-256 and size; the task's immutable
  source metadata and generated document object key. This is a copied pin, not a fresh lookup by name.
- **Candidate intent:** RAG and episode scope keys plus full index identities, distinct UUIDs generated
  before the first S3 document put, expected `VectorHead` fields, expected index revisions and target
  History/Memory snapshot versions. Record the old RAG and episode receipt projections and IDs. Each
  reservation binds exact attempt/owner/scope/base and is `reserved` or permanently `revoked`.
- **Expected domain:** exact frozen task fields, event ID/timestamp/item/metadata, History record,
  complete next History and Memory snapshots, expected document count/digest, and canonical SHA-256
  for each full value. Preserve canonical JSON encoding and typed version fields; never replace these
  values with a digest alone. `record` and next snapshots contain the deterministic bucket/key path,
  not `VersionId`, so they freeze completely before S3 put; no placeholder template is needed. A
  baseline Memory-document-row digest aids diagnostics but cannot replace C's row verification.
- **Document result:** after S3 put and same-repository byte verification, one null-to-value CAS stores
  `(user, document, bucket, key, VersionId, SHA-256, size)` and expected record hash. Validate every
  scalar against intent except the newly issued, valid VersionId; reject a differing second result.
  Derive a separate `final_expected_hash = H(schema_version || intent_hash || canonical(document_result))`.
  The process-local `VerifiedDocumentRef` is held only by the active publication call; the scalar row
  never issues or reconstructs that capability.
- **Sealed candidates:** each fixed-order RAG/episode slot is write-once and binds
  `final_expected_hash`, exact owner key, base/index revision, snapshot version, verified point count
  and content digest before `try_begin_committing`. A precomputed corpus hash may be in intent but
  cannot impersonate Qdrant verification or authorize a second stage/seal.
- **Terminal result:** two ordered, complete `_receipt_projection` values, including `created_at`,
  `sealed_at`, `published_at`, written only after C's `_terminal` checks and in the same transaction as
  both vector publications, History/Memory, document reference/witness and task success.
- **State:** `intent`, `document_verified`, `pair_sealed`, `terminal_committed`,
  `proved_succeeded`, `abandoned`; monotone phase/version CAS and database timestamps. `unknown` and
  `manual_hold` are observation/hold reasons, never phases that demote terminal state, erase receipts,
  fail a task or authorize retry. Only exact expected phase/version transitions fill write-once slots.

Store the complete canonical intent payload as private compressed bytes in PostgreSQL, with digest,
encoded length and format version. **Candidate, unapproved sizing:** at most 64 MiB canonical bytes and
8 MiB compressed per attempt, including duplicated old/new snapshots, typed metadata, source pin and
IDs; document/sealed/terminal slots count toward a separately enforced total row bound. Before the
first external write, reject over-limit requests without truncation; decode via bounded streaming
decompression and reject output exceeding its declared/capped length. Measure representative and
incompressible History/Memory text, per-attempt overhead, concurrent attempts per user and proof cost
before fixing numeric limits. Point count alone cannot establish byte capacity or 100000+1 support.
Local synthetic sizing (2026-10-02; canonical JSON/zlib-6, no test/service): duplicated 100,000-entry
Memory snapshots with 48-byte deterministic text and small typed metadata measured 34.90 MiB canonical
/ 6.97 MiB compressed; 256-byte text measured 74.58 / 9.27 MiB. These omit real History/receipt slots
and are not capacity/performance validation; the larger sample exceeds both proposed limits. Packet 1
freezes numeric bounds after representative and incompressible sizing; arbitrary text has no fixed
100k-entry byte guarantee.
No raw vectors, staged file bytes, credentials, access tokens or stack traces belong in this record.
Restrict reads to the user-scoped repository and a privileged recovery role; redact payloads from API,
logs and task errors. Retain Unknown and terminal proof through the established task/audit retention
window, with no age-only deletion while unresolved. Referenced old/new generation receipts, task audit,
document row and History witness share that retention contract; resolved records follow the approved
window while the small reservation tombstones remain permanent. Follow existing private-data and backup
policy; this proposal adds no new encryption approval gate.

## Interface and transaction boundaries

1. **Read-only plan:** preflight task/source, explicit episode profile and attested complete episode
   bundle, History/document witness, old receipts and both heads. Validate both complete corpora,
   collisions, candidate sizes and canonical payloads. Freeze event timestamp, two UUIDs, deterministic
   document key and complete record/next History/Memory before the S3 put. These values form immutable
   `intent_payload/intent_hash`; the future VersionId is absent from the record and snapshots.
2. **Commit intent and barrier:** under live task/user lease and fixed user-first locks, compare the
   pinned task/source/bases and insert evidence, gate and two globally unique reservations atomically.
   A duplicate UUID is a refusal, never a retry. The gate is effective on commit even if the process
   dies immediately. Ambiguous commit response stops all external writes until exact-key read-only
   lookup; missing row alone cannot authorize reuse of reserved IDs.
3. **Document write:** only the original live attempt passes the gate. Recheck both leases and gate
   immediately before S3 I/O; use the frozen key/source version and verify returned immutable version
   and bytes through the issuing repository. One null-to-value CAS appends the scalar result and
   `final_expected_hash`. A lost put response leaves an observation of Unknown; known key without exact
   verified VersionId is not a publication ref and cannot be supplied by recovery.
4. **Pair preparation:** pass both reserved UUIDs and frozen plans to B; B generates no replacement ID.
   Every relevant stage/seal/publish/abandon path validates reservation attempt/scope/phase and
   `not revoked` under user-first locks, as well as the existing live owner tuple. Preserve current
   `stage` `_owner` pre/post-insert checks and same-transaction user/task/audit locks. Recheck gate and
   live leases before each Qdrant operation. Save both sealed slots once, under original attempt, before
   `try_begin_committing`. Uncertain write/response stops; no same-ID stage/upload/seal replay.
5. **Terminal:** B's callback publishes both vectors; C's `_terminal` validates and writes domain
   state using the original same-repository `VerifiedDocumentRef`. In that same PostgreSQL transaction,
   recheck gate, exact live tuple, immutable hashes and sealed slots; append both actual complete ordered
   receipt projections and phase CAS to `terminal_committed`. Task success/audit, vectors, History/Memory,
   document row/witness and evidence commit or roll back together. Terminal does not release the gate;
   only later strict proof/ack may do so. No post-commit journal may claim terminal success.
6. **Read-only proof:** on any lost terminal response, service/Worker restart, or evidence ambiguity,
   load the evidence by exact user/task/attempt key and verify its schema, hashes and completeness.
   In one repeatable-read read-only PostgreSQL snapshot, compare full frozen context and stored actual
   projections to B's task/audit/receipt proof and C's complete domain proof. A public success result
   requires *all* checks. Keep old receipts' immutable projections even if their state is retired.
   Current heads or snapshots may have advanced; locate the exact document/event and exact generation
   receipts, while preserving C's strict witness, row and fence checks. Do not fabricate expected
   projections from live rows. Read errors, missing rows or a failed proof remain Unknown/hold. A matching
   proof is followed by a short recovery metadata CAS that acknowledges success and releases the gate
   only after revalidating the same exact evidence/task tuple under user-first locks.

The detached proof reader consumes a validated evidence value, not an `ImportMemoryPublicationUnknown`
instance or private in-memory `_PairContext`. It constructs only read-only selectors/descriptors.
It cannot call `publish`, `stage`, `seal`, `put_immutable`, `verify_for_publication` to mint a token,
or `complete` while resolving. A byte-read for a pinned published object may be an additional check,
but a successful PostgreSQL proof must still use the exact saved reference and C domain witnesses.
Existing C database proof remains the success rule; pinned byte reads are optional diagnostics only.

**Shared-writer gate contract:** under the user-row lock, every entry that can change proof-dependent
History or Memory snapshots, Memory document rows, document objects, History witnesses, RAG/episode
heads/receipts, deletion/clear fences or their retained references checks the durable gate before
mutation. This includes direct `PostgresSnapshotRepository.compare_and_swap_in_transaction/update` and
`PostgresMemoryDocumentStore.add_document_in_transaction/delete_document_in_transaction`; public routes,
Worker claims and coordinator acquisition checks are additional entry controls, not substitutes.
While unresolved, only the gate's exact original attempt with live task/user tuple in an allowed phase
may mutate for its planned publication. Other business writers, including holders of ordinary user
leases, fail closed; terminal rechecks after blocking locks. Shared mutation paths without this check
remain disabled in distributed mode until wired. Reads and other users continue independently.

## Recovery, fencing and fairness

- `recover_expired` detects exact-attempt evidence before changing eligibility. Under authority locks
  it expires ordinary task/user leases and puts that task in a non-due hold, never automatic `queued`.
  The persistent gate remains unresolved; an unresolved terminal commit is also held until proof/ack.
  Ordinary claim excludes unresolved evidence, but the shared-writer gate remains the authority.
- Recovery claims a queue item using a bounded `SKIP LOCKED` transaction, commits that short claim,
  then begins a separate authority transaction. The fixed blocking lock order is **user -> user lease
  -> task -> task audit -> evidence/gate -> exact reservations/generation rows**, with RAG then episode
  ID order. No path may lock evidence before user or hold the queue row while waiting for user.
- Recovery first reads strict proof even for `intent`, absent generation rows or a reported exception.
  `terminal_committed` without exact proof retains its phase with `manual_hold` reason; late Unknown cannot demote
  it. Successful proof gets a recovery-only metadata CAS: confirm same task already `succeeded`, same
  terminal hashes/receipts, and unchanged gate; then mark `proved_succeeded` and release gate. Any
  mismatch is an incident, never a repaired task or replayed C callback.
- For any metadata transition after proof, recovery acquires a **separate, expiring, attempt-bound recovery
  lease** under the user-first authority locks. Its narrowly scoped operations are proof status,
  atomic exact revocation/abandonment and resolved-gate release. It is allowed through the existing
  gate for those operations only; it never inherits the old Worker tuple, never grants S3/Qdrant I/O,
  and cannot invoke ordinary publication. Ordinary user/task leases expire or release normally;
  neither expiry nor release removes the durable gate. Do not keep the old ordinary lease alive as a
  hold or require an ordinary acquire that the gate itself refuses. Reuse the coordinator's 1–86400
  second duration bound; renew or take over only with exact token/version, phase CAS and database-clock
  expiry, rechecking liveness after blocking locks.
- The exact-abandon transaction rechecks old task/user tuple expired, attempt identity, task not
  `succeeded`, no matching terminal receipts, unchanged hashes, pre-terminal evidence phase and both IDs.
  It locks both IDs in fixed order. A present row must match owner/scope/tuple and be `staging|sealed`;
  `published|retired`, mismatched or unreadable rows retain phase with `manual_hold` reason. For **both** IDs, including
  one with no generation row, atomically mark the permanent reservation `revoked`, mark present valid
  rows `abandoned`, CAS evidence `abandoned`, record resolution and release gate. Any failed predicate
  rolls back the entire transition. Absence is never itself failure proof.
- Every stage entry and other candidate mutation checks reservation/attempt/scope/not-revoked in the
  same user-first transaction as its owner check; UUID uniqueness prevents a new owner from adopting
  an old ID. Preserve `stage`'s current user/task/audit lock and post-lock liveness recheck: if old
  stage gets the user lock first, recovery waits and sees its committed row or rollback; if recovery
  revokes first, old stage fails owner/reservation checks. A late in-flight Qdrant write can land only
  in an unpublishable, revoked namespace. Tombstones remain after optional physical cleanup.
- A fresh attempt follows exact abandonment and gate release with fresh task/user tuple, UUIDs and
  preflight against current source/heads; no frozen payload or prior capability becomes new authority.
  Use a bounded separate recovery queue, oldest-first per user and fair across users. Surface a
  sanitized stable `needs_reconciliation` status. PostgreSQL outage authorizes no lease, proof,
  abandonment, retry or external write; resume proof when it returns, never infer rollback from timeout.

## Crash matrix and required proof

| Crash point | Durable observation | Restart action |
| --- | --- | --- |
| Before intent commit / lost intent response | No external write is permitted; evidence, gate and two reservations commit together or none do. | Read exact journal key first. If unreadable, hold; never reuse allocated IDs or start external I/O. |
| After intent, before/during S3 put | Intent has source pin, expected payload, key and UUIDs; object version may be unknown. | Read-only exact-version/object investigation; never infer no put from absent document row or repeat the put blindly. |
| After verified S3 object, before document result commit | Issued capability may die with process; journal may lack version. | Preserve object as orphan/quarantine; do not reconstruct capability. Resolve by exact object evidence or manual hold. |
| After document result, during RAG/episode stage/upload/seal | One or both candidate rows can be absent, staging or sealed; both reservations persist. | Strict proof first; if unproved, revoke both reservations and abandon qualifying present rows atomically. Missing row is still revoked. |
| After both seals, before/during `try_begin_committing` | Sealed descriptors are durable; committing response may be lost. | Read-only C proof first; no second `try_begin_committing` or upload. |
| Inside terminal before commit | All SQL writes, including journal receipts, must roll back together. | Read-only proof; if not proven, exact candidates require conditional revocation. No missing-row shortcut. |
| Commit succeeded but response lost | Task/domain/receipts/journal projections are atomic; user gate remains. | Exact full proof then metadata CAS/ack releases gate; no replay or intervening same-user mutation. |
| PostgreSQL down / stale Worker / late Qdrant I/O | Outcome unreadable or old writer may finish external I/O. | Hold gate/task, fence on restored DB, retain revoked IDs; no blind retry or cleanup as proof. |

Two mandatory SQL schedules: pause old `stage` after it acquires the user lock, then let recovery wait
and inspect its final commit/rollback; also let recovery revoke an ID with no generation row first,
then resume old `stage` and verify refusal. Repeat stage with a new live owner and after physical
cleanup; both must refuse the revoked UUID. Direct snapshot update and Memory document delete must
refuse under an expired import's unresolved gate while a different user's write proceeds.

## Three sequential implementation packets after design approval

1. **Schema, gate and frozen planner:** own additive PostgreSQL evidence/gate/permanent reservation
   schema, bounded codec, user-scoped repository and read-only C planning split. Define a shared-writer
   guard interface for every proof-related mutator and keep distributed publication disabled until all
   call sites are wired. Contract checks cover typed immutable hash, two reservations in intent commit,
   direct snapshot/Memory-row denial, bounded decode, tenant denial and source/base drift.
2. **Publication and detached proof:** after packet 1's independent review and interface stabilization,
   own B/C integration and exact read-only proof. These packets touch the same C files and **must not
   run in parallel**. Use the original verified capability only in live terminal; append document
   result/sealed slots once and save actual full receipt projections in the terminal transaction.
   Disposable PG/S3/Qdrant checks inject commit-response loss, rollback, timestamp mismatch, domain
   drift and cross-user selectors. No recovery queue ownership in this packet.
3. **Recovery and Worker adapter:** after packet 2's independent review, own lease/claim recovery,
   recovery-only lease, permanent exact-ID revoke and Worker proof-first orchestration. Wire/check
   remaining shared mutators, including deletion and head/fence paths, before enabling publication.
   Tests cover both SQL schedules, all crash rows, DB outage, late Qdrant I/O, gate lifetime,
   cross-user fairness and retention. Separate runtime acceptance still gates bootstrap/product routes.

Delivery order is **1 -> independent review/stable interfaces -> 2 -> independent review -> 3**;
packet 3 also gets independent review before enablement. Existing 100000+1 scale work is separate evidence.

## Dependencies and approval boundary

- New-user episode baseline/profile exposure needs its separate design and acceptance before C serves
  those users. Historical episode vectors already remain `pending provenance`; migration wiring must
  honor that approved policy without synthesizing a baseline or weakening attested reads.
- The structural approval choice is PostgreSQL attempt evidence with immutable intent, write-once
  document/sealed/terminal slots, all-mutator user gate, permanent UUID revocation and bounded recovery.
  Packet 1 freezes numeric bounds from measured data before codec work; no production capacity is
  claimed. Unresolved proof dependencies have no age-only cleanup; resolved records follow the existing
  approved window while tiny UUID tombstones remain permanent. Ambiguous attempts fail closed.
- Worker/API/bootstrap, two-user authenticated restart, compatible image rollback and production migration remain separate gates; this draft is not delivery evidence.
