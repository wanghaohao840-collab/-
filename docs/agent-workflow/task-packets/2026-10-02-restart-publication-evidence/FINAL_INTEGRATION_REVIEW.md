# Final Integration Review: Restart-safe publication evidence

- Source review: `REVIEW.md`
- Reviewed source commit: `5d0e66a88d3f9d39231c757f3b2d07768fe51cb9`; whole-feature BASE `bc3176387ec4e41d452c5cac6e07bce1ba5fbc20`.
- Review date: 2026-10-10, Asia/Shanghai.
- Result: accepted within the approved private opt-in library/disposable integration scope.

All three implementation packets are done before this review. This review combines the independently reviewed whole-feature N8 code/contract preparation with completed current formal regression evidence, actual source Delivery and fresh committed-tree binding. It does not promote an earlier preparation to runtime acceptance or claim a new full line-by-line review of unchanged accepted code.

## Delivered packet inventory

| Packet | Status | Commit | Owned contribution | Verification |
|---|---|---|---|---|
| 01 | done | `bc6aa90b088b70b6684cd3157f5aa10171935745` | Bounded intent, durable gate and permanent reservations | Accepted source/Spec/Quality/Delivery; final corrective158 cases, separately scoped |
| 02 | done | `2f39378e2228a65ee68dd2a1e3b06d38a31a9412` | Original issuance, write-once slots, atomic terminal and detached proof | Accepted382 final cases/Spec/Quality/Delivery, separately scoped |
| 03 | done | Task7 `686ec52`, Task8 `6622a1e`, Task9 `5d0e66a88d3f9d39231c757f3b2d07768fe51cb9` | Queue/recovery/retention, remaining guards, `_16`, private default-OFF adapter | Task7/8 accepted303/160 separately; current Task9 final430/offline207, actual Spec/Quality/Delivery PASS |

All four earlier source commits are actual ancestors of the reviewed source tree. Delivery is into `codex/distributed-cutover-vertical-slice`; main inclusion and remote push are separate observations.

## Combined diff reviewed

Whole-feature raw changes:32 =11 additions +19 content changes +2 EOL-only changes (`app/import_repository.py`, `tests/test_import_repository.py`). The thirty semantic paths and their responsibilities are listed below. Exact per-path raw before/after copies and unified diffs are retained in `.runtime/distributed-cutover/packet3-feature-n8-combined-review-inputs/`; source diff SHA `43950D333B95D4CAA6A7862113FAC0F53A891F23DD73A71CCD6CC7FE4DE2A54F`.

Root's actual committed-tree checker exits0, SHA `BA463BBA768B5A1C3F0B1EA3F934038D892429271CA3AF09F2C70094F1C52CB5`: all74 committed blobs equal the reviewed/tested N8 inputs, and actual whole-feature32 changed paths exactly match the prepared combined review. Task9 contributes exactly17 authorized changes with soleparentd9. Coordinator documentation and unrelated dirty/EOL-only checkout paths are excluded from source delivery; preserved175-input context confirms69raw equal/106unchanged EOL-only paths.

| Content/addition path | Combined responsibility inspected |
|---|---|
| `app/import_document_publication.py` | Pure document plan before put; exact source/history reread; original verified continuation; domain admission plumbing |
| `app/import_memory_publication.py` | Complete intent producer; original service/live/fixed-work registries; fixed pair/domain commit; separate one-use finish/release; ordinary transaction activation |
| `app/import_persistence.py` | Direct store/factory and inherited control gates; active RC transaction; mixed-user refusal |
| `app/import_publication_evidence.py` (new) | Typed bounded codec; permanent reservation/queue transaction; copied evidence reader; exact slot CAS and candidate checks |
| `app/import_publication_proof.py` (new) | Detached result/envelope and one-snapshot exact receipt/domain/fence proof |
| `app/import_publication_recovery.py` (new) | Queue capture versus authority transaction; lease renewal; exact acknowledgement/abandonment; finite observation policy |
| `app/import_publication_worker_adapter.py` (new) | Default OFF; original live identity; at-most-once execution; exact Unknown scheduling; fresh proof before returned success |
| `app/postgres_coordination.py` | Ordinary user gate; narrowly bound gated heartbeat; separate terminal release continuation |
| `app/postgres_document_objects.py` | Issuing-repository token identity and copied immutable values; exact domain admission |
| `app/postgres_history_document_witnesses.py` | Bare ordinary insert establishes user-first RC gate boundary; exact witness admission |
| `app/postgres_import_artifacts.py` | SQL source/task admission gate after pre-attempt immutable upload |
| `app/postgres_import_leases.py` | Due exclusion; post-wait liveness; fixed committing/completion; guarded bookkeeping; evidence-bearing expiry hold |
| `app/postgres_memory_documents.py` | Guarded ordinary add/delete and exact admitted event/content/raw metadata |
| `app/postgres_snapshots.py` | Gate before ordinary CAS/update callback; exact admitted kind/version/payload |
| `app/postgres_vector_generations.py` | Permanent UUID checks; guarded index creation; candidate/publish authority; read-only paired recovery validation |
| `app/vector_generation_service.py` | Original issued seals; reserved IDs; durable live checks; no durable ambiguous-seal continuation |
| `hello_agents/memory/storage/generation_vector_store.py` | Complete JSON-native preflight; per-underlying-read/retry, batch and page live checks; shared record decoder |
| `migrations/versions/20261002_14_import_publication_evidence.py` (new) | Immutable attempt, bounded slots, durable gate and permanent reservations |
| `migrations/versions/20261002_15_import_publication_recovery.py` (new) | Byte-exact payload split; mirrored versions/deferred checks; queue/recovery/token authority; source/audit retention; cleanup OFF |
| `migrations/versions/20261007_16_publication_dependency_isolation.py` (new) | Non-RC source/audit UPDATE/DELETE refusal only, additive migration |
| `tests/app/test_import_publication_worker_adapter.py` (new) | Original issuer/default OFF/once-only/Unknown/fresh proof/database-change controls |
| `tests/integration/test_import_document_publication.py` | Historical rev12 fixture seed/release, original head/upgrade/witness and latest-schema control |
| `tests/integration/test_import_memory_preflight.py` | Activate real fixture transaction; preserve collision and zero-write oracle |
| `tests/integration/test_import_memory_publication.py` | Preserve episode vector independently of RAG candidate validation, ordinary and durable paths |
| `tests/integration/test_import_publication_evidence.py` (new) | Codec bounds/types; zero-write planning; gate/lock races; source/base drift; reservation permanence |
| `tests/integration/test_import_publication_proof.py` (new) | Receipt/domain/fence and capability negatives; terminal rollback; response loss; one RR snapshot; retry/page/batch fences |
| `tests/integration/test_import_publication_recovery.py` (new) | Migration and dependency-isolation negatives; queue/lease/ABA/fairness schedules; full rollback; exact two-ID recovery; direct mutators |
| `tests/integration/test_postgres_coordination.py` | IDLE caller cursor remains IDLE on refusal; ordinary lease regressions |
| `tests/integration/test_postgres_import_leases.py` | Revision-correct legacy seed and precise trigger refusal while preserving original full-row postconditions |
| `tests/integration/test_postgres_vector_generations.py` | Revision-correct rev10 seed, preserving upgrade and retained immutable receipt checks |

## Cross-packet interface audit

The following code/contract assessments are explicitly reused from independent whole-feature preparation SHA `9B6FA8F5A0677C4CC981830F243D6024CEC0323A2016C87EEFE05C351EDF738D`. Actual commit equality and current complete formal evidence now close its previously open source/result gates. Static consistency remains a code claim; it is supported separately by the original regression results below.

Here `consistent` means static contract consistency at the frozen inputs. It does not mean formal runtime PASS.

| Producer | Consumer | Contract checked | Result | Current evidence |
|---|---|---|---|---|
| C `_plan_intent` / document `_plan_document` | `reserve_intent` | Copied complete source/task/scopes/old receipts/snapshots/event/domain and two UUIDs precede external publication; no VersionId capability reconstruction | consistent | `app/import_memory_publication.py:988`; `app/import_document_publication.py:222`; `app/import_publication_evidence.py:640` |
| Intent/header/private payload transaction | Original live issuance and recovery queue | Gate, both permanent IDs and exact discoverable queue commit with immutable intent; ambiguous reservation stops publication | consistent | evidence `:640`; C `:640`; migration `_15:114` |
| Original verified document / issued seals | Write-once slots | Issuer identity, exact key/phase/version/hash; original document continuation; complete returned seals; duplicate append refusal | consistent | C `:690`; evidence `:925`, `:971`, `:990` |
| Reserved live candidate | Vector authority/service/writer | Reserved UUID, exact scope/owner/state; stage pre/post checks; live authority before each underlying retry/read/batch/page; ambiguity does not replay or abandon | consistent | vector authority `:177`, `:198`; vector service `:182`; generation writer `:370` |
| Fixed terminal work | Vector, History, Memory, document and witness writers | Exact operation-specific admission; original cursor/transaction and values; ordinary callback denied before invocation | consistent | C `:189`, `:759`; leases `:311`; snapshot `:106`; Memory documents `:61`; witness `:218` |
| Terminal slot and actual receipts | `_finish` / coordinator release | Pair/domain/slot/task/audit share one transaction. Domain admission closes first; separate original one-use binding validates receipts and fresh live tuple; ordinary release cannot clear gate | consistent | C `:369`, `:392`; leases `:311`, `:372`; coordinator `:196` |
| Retained immutable intent and full slots | Detached proof | One RR READ ONLY snapshot; all 20 receipt fields/timestamps, old receipts, exact same-version snapshots or retained later values, pinned row/witness/fence; no live capability | consistent | proof `:85`, `:156`, `:223` |
| Short committed queue capture | Separate recovery authority grant | No queue lock while waiting for user authority; user/ordinary lease/task/audit/header/gate/recovery/queue order; fresh final-capture/clock checks; permanent tokens | consistent | recovery `:176`, `:265`; `_15` queue/token guards |
| Detached proof / recovery claim | Ack metadata transaction | Recheck envelope, exact current pair/domain and claim after locks; only metadata/gate/queue/recovery closure; task success not demoted | consistent | recovery `:650`, `:578` |
| Exact expired preterminal tuple | Shared ordinary closure, pair revocation and fresh requeue | Both ordinary leases expired; full reservations/generation identities; both IDs revoked even when row absent; all closure/revocation/resolution/requeue atomic | consistent | recovery `:95`, `:697`; vector authority `:25` |
| Ordinary shared mutations | Gate + narrow original exceptions | Direct store/cursor, callbacks, witness/object/snapshot/Memory/vector and lease helpers refuse unresolved gate; only original heartbeat/fixed publication exceptions | consistent | persistence `:18`; leases `:71`, `:234`, `:372`; evidence `:621` |
| Private adapter | Original C, exact queue scheduler and detached proof | Disabled default; exact original service/database/live identity; execute once; only valid durable Unknown schedules; even Unknown with proof remains held; normal result needs separate equal fresh proof | consistent | adapter `:38`, `:58`; recovery `:214` |

Lock-order detail: helper re-locking of already-owned rows is not a new grant of authority. The shared expiry helper is consumed under the caller's full user-first authority locks. Live sealed-reservation locking uses sorted UUIDs and recovery validates RAG then episode; both enter through the same user serialization, so these inspected paths do not acquire the two reservation sets concurrently in opposite order. No lock-order finding is introduced solely from the different inner iteration order. `_live` and final recovery checks use fresh database time after potentially blocking waits.

## Requirement coverage

| Accepted requirement | Implementing packet(s) | Source / negative or compatibility evidence | Final review assessment |
|---|---|---|---|
| Full bounded typed intent and refusal before publication I/O | 1, 2 | evidence codec `:406`, `:436`; evidence tests `:216`, `:241`, `:316`; proof tests `:1122`, `:1143`, `:1177` | present; no unbounded capacity claim |
| Durable gate and permanent two-ID reservation survive lease expiry | 1–3 | evidence `:640`; `_14` guards; evidence tests `:544`, `:767`, `:815` | present |
| Original live issuance, write-once exact slots, no reconstructed token | 2, 3 | C registries/continuations; proof tests `:387`, `:708`, `:743`, `:937`, `:959`, `:981`, `:1042` | present |
| Atomic terminal result plus separate one-use finish/release | 2, 3 | C `:759`; leases `:311`; recovery tests `:1835`, `:1944`, `:2024` | present |
| Strict restart proof with all immutable receipt/domain/fence fields | 2, 3 | proof `:85`, `:156`, `:223`; proof tests `:493`, `:834`, `:892`, `:1053` | present |
| Committed queue capture, bounded fairness, separate lease, fresh clocks and no ABA | 3 Tasks 7–9 | recovery `:265`, `:399`, `:448`; recovery tests `:1797`, `:2303`, `:2638`, `:2912`, `:2972` | present; schedule initializes non-null epoch, so ascending order gives never-claimed users priority |
| Proof-first ack, terminal ambiguity hold and finite transient policy | 3 Task 8 | recovery `:650`, `:755`, `:812`; tests `:771`, `:841`, `:1080`, `:1111`, `:1432` | present |
| Exact both-ID revocation and no-replay fresh retry | 3 Task 8 | recovery `:697`; tests `:726`, `:744`, `:861`, `:886`, `:1151`, `:1182`, `:1299`, `:1353` | present; absence alone is not rollback proof |
| All ordinary mutation entrances and callback-before-invocation gate | 1–3 | direct lower-level guards above; recovery tests `:262`, `:286`, `:325`, `:377`, `:1524`; proof test `:49` | inspected active entrances consistent; accepted-tree175 inventory rebound and Delivery PASS |
| Additive `_14 → _15 → _16`, preserved bytes and failed migration rollback | 1–3 | `_15` backfill/deferred guards; recovery tests `:93`, `:142`, `:397`, `:477`, `:581`, `:2404` | static consistent; original final command completed fresh/populated/failed-DDL cases; reviewer did not rerun migrations |
| Source/audit dependency retention under stale RR snapshots and legal RC controls | 3 Task 9 | `_16` BEFORE UPDATE/DELETE refusal; recovery tests `:169`, `:194`, `:216`, `:2098`, `:2177`, `:2219`, `:2274` | present; INSERT and detached RR read-only role preserved |
| Cleanup OFF and immutable small identities independent of private lifetime | 3 Task 7 | `_15` private DELETE refusal and permanent reservation/token/header guards | present; no age policy or cleanup authority introduced |
| Local/no-gate/standalone compatibility and preserved negative oracles | 1–3 | unchanged local branch in persistence; reviewed legacy fixture amendments; full original no-write and upgrade assertions retained | static consistent; N8 offline207 completed with all original assertions and actualexit0 |
| Default-OFF Worker seam, no native/API/bootstrap composition | 3 Task 9 | adapter default; current app/web call-site search finds only defining service and adapter; bootstrap `:91` still rejects distributed | present; no runtime enablement accepted |

## Overlap and duplication audit

- Serial dependency order is explicit for all three packets (`parallel-safe: false`). Shared C/evidence/lease/vector files are intentional serial handoffs. Their combined current contents retain producer and consumer contracts; no competing parallel ownership or overwritten implementation was identified.
- Packet 3 adapts the Packet 2 reader/writer to the `_15` child table once, preserving codec/slot bytes and paired version CAS. It does not add a second proof authority. Ack calls the existing proof selectors again under authority locks rather than accepting a second reduced proof definition.
- The separate finish/release binding does not extend the domain admission's lifetime. Ordinary batch touch stays guarded; the narrowly issued terminal path uses validated fresh bookkeeping inside its original transaction. Caller-row refusal precedes one-use consumption and DML.
- Candidate preflight and record decoding are shared pure helpers; the durable writer's guarded raw reads cover retries without adding an alternate mutable vector authority. Recovery's paired validator is read-only and does not grant ordinary publication permission.
- The Task 9 baseline ownership check covers exactly 17 current changes. N8 adds only two approved fixture deltas to N7; all product/migration bytes remain N7-identical. The two EOL-only whole-feature paths carry no semantic implementation. Historical migration fixtures use real revision-compatible SQL only in tests; production has no missing-gate-table fallback.
- Migrations are linked once, `_13 → _14 → _15 → _16`; no dependency manifest, central export or native composition change is required for these private imports. No duplicate migration revision was found in this input set.
- Root-owned status summaries contain historical checkpoints as well as later amendments; their older headline counts do not supersede the final N8 manifest or grant acceptance. Root has now recorded the accepted handoff and this final review; earlier checkpoints remain history.

## Architecture and invariant audit

PostgreSQL remains the structured authority, S3 the immutable source/document byte store and Qdrant the generation store. Tenant, document, event, task and exact attempt identities are carried through intent, proof and recovery. No recovered scalar reconstructs a verified document token, live `ImportAttempt`, terminal callback or publication admission. Unknown external writes cannot trigger stage/upload/seal/terminal replay; a late external write to a revoked generation still cannot regain SQL publication authority.

The existing local SQLite deletion/clear/fence and Memory workflows remain outside disabled distributed composition. Current app/web searches found no adapter or private live publication caller beyond the defining C/adapter files, and `app/bootstrap.py:91` still refuses distributed mode. This scoped source check does not constitute authenticated Worker/restart, full business migration, vector provenance, compatible-image rollback, live-service or production verification.

## Combined verification

These exact current commands completed on the frozen source now delivered in `5d0e66a88d3f9d39231c757f3b2d07768fe51cb9`:

```powershell
D:/python_self_agent/venv/Scripts/python.exe -m pytest tests/integration/test_import_publication_recovery.py tests/app/test_import_publication_worker_adapter.py tests/integration/test_postgres_imports.py tests/integration/test_postgres_import_artifacts.py tests/integration/test_postgres_coordination.py tests/integration/test_postgres_import_leases.py tests/integration/test_postgres_snapshots.py tests/integration/test_postgres_memory_documents.py tests/integration/test_import_memory_fault_matrix.py tests/integration/test_postgres_vector_generations.py tests/integration/test_import_publication_evidence.py tests/integration/test_import_publication_proof.py -q --basetemp=.pytest-tmp-p3-final-n6 --junitxml=C:\Users\11272\.codex\worktrees\bf20\python_self_agent\.runtime\distributed-cutover\restart-packet3-task9-final-n6.xml -p restart_packet3_task7_resource_plugin
D:/python_self_agent/venv/Scripts/python.exe -m pytest tests/test_import_repository.py tests/test_import_worker.py tests/test_user_mutation_coordination.py tests/integration/test_import_memory_consistency.py tests/integration/test_import_memory_preflight.py tests/integration/test_import_memory_publication.py tests/integration/test_import_vector_publication.py tests/integration/test_import_document_publication.py -q --basetemp=.pytest-tmp-p3-offline-local-n6 --junitxml=C:\Users\11272\.codex\worktrees\bf20\python_self_agent\.runtime\distributed-cutover\restart-packet3-task9-offline-local-n6.xml -p restart_packet3_task7_resource_plugin
```

- final-n6:430 passed3556.91s; offline-local-n6:207 passed1231.45s. Both zero failures/errors/skips, all1911 setup/call/teardown reports passed, all actual pytest/meta/ledger/runner/wrapper exits0,637 unique disjoint current cases.
- Root phase checks and full checker actually exit0. Fullcheck SHA `49E43A39B32D679B68526BED40D6897A81AF69A7329853393D2CD31BA4ED11EA`; both247-payload archives and235-payload static capture bound to stable74source/eighthelpers.
- Independent Astra formal Spec/Quality report SHA `9690323E2DB51CB72844F6D6BBBA5259E8677DD31E1A8A6E5CD7CF4448DE7386`; actual-commit Delivery report SHA `43F5B9773DFE97AA5824EB8FABAA3FD1B2E9BA88760FAFB7E35BFFBB7B104071`. Actual raw binder SHA `FD1B768CF25C30B0ADBB450BA848787187C3D98668948D16CD7EAD0ABCEEA79F`.
- Accepted-tree175 inventory SHA `B353CF772FF34D7D3744A661E67DFB70094FE912A455B7360CC99B207F26EF85`; `git -c core.whitespace=cr-at-eol diff --check d9ddeb7f309df326c8d2c7d79e37e8969e880eed 5d0e66a88d3f9d39231c757f3b2d07768fe51cb9` exits0.
- Original dedicated PG59497/S359498/targetQ59539 identities/mounts remained stable; protected sourceQ remained exited. No main/retained source or production data was activated. Recorded service observations and passive resource names are integration evidence only.

Earlier accepted packet results are reused with their own source boundaries, never summed with637. Failed/interrupted/diagnostic runs remain preserved, including incomplete offline-n5. No new pytest was started by Root or reviewers. Root's uppercase-hash helper invocation and reviewer-only comparison diagnostics were corrected without changing source, helpers or original artifacts.

## Findings

### Blocking

None within the approved combined private library scope and exact delivered source.

### Changes required

None. No corrective packet is required by this review.

### Residual risks

Native Worker/API/bootstrap composition remains OFF. Authenticated restart/business journeys, retained business-data migration, historical vector provenance, compatible-image rollback and production cutover remain separate future gates. Historical text/metadata remain retained; vector recall is pending proof. No cleanup policy is enabled. Branch delivery does not establish main inclusion or live product acceptance.

## Decision

Accepted for the approved restart-safe publication evidence and bounded recovery library feature on the stated independent branch. All packets have accepted source boundaries; producer/consumer, lifecycle, overlap, authority, migration and compatibility contracts are consistent; current combined regressions and exact committed-byte bindings passed. This result ends the feature's scoped implementation/integration chain without enabling its separately gated runtime or production composition.
