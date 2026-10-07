---
id: "restart-publication-evidence-03"
title: "Bounded restart recovery and remaining mutation guards"
status: "in_progress"
parallel-safe: false
depends-on: ["restart-publication-evidence-02"]
base-commit: "cb06adb31be518d465eefdf70cbe3be36d458214"
owner: "GPT-6 Sol High (serial implementation)"
---

# Task Packet: Bounded restart recovery and remaining mutation guards

> Serial Sol High implementation is in progress. Packet 2 is accepted at `2f39378`; coherent Task 7 is accepted at `686ec52` with 303 complete passing cases and independent Astra Spec/Quality/Delivery PASS. The original approved contract and historical base bindings below are retained. The current Task 8 source boundary is specified in the accepted Task 7 handoff; Task 8/9, whole Packet 3 and runtime/production acceptance remain pending.

## Goal

After Packet 2 is independently accepted, add restart recovery for one exact evidence-bearing publication attempt. Recovery must either acknowledge an already committed result from detached proof, or permanently revoke both reserved generation IDs and resolve a qualifying pre-terminal attempt. Every remaining direct mutation path and deletion dependency that could invalidate proof must honor the unresolved per-user gate. Unknown external outcomes stay held and are never replayed.

## Non-goals

- Do not change native Worker/API/bootstrap composition, public routes, or default runtime behavior. The durable path and any narrow Worker seam remain disabled in ordinary composition.
- Do not replay S3 or Qdrant writes, infer rollback from timeout/absence/zero points, or call the original publication callback during recovery.
- Do not migrate historical episodes, create user baselines/profiles, mutate source Qdrant or retained targets, change recall policy, or port local SQLite deletion/clear or Memory-sync workers into distributed mode.
- Do not treat pre-attempt immutable source upload or orphan cleanup as publication proof. Guard relevant SQL source/task admission and unresolved source retention separately, including direct deletion of source and audit rows.
- Do not add an ambient trusted flag, reconstructed attempt/capability, general callback bypass, or recovery-lease access to live publication writers.
- Do not enable runtime or production cutover. These require separate acceptance.

## Delivery context

The approved design is `docs/superpowers/specs/2026-10-02-restart-publication-evidence-design.md`; the implementation sequence and global constraints are in `docs/superpowers/plans/2026-10-02-restart-publication-evidence.md`, Packet 3 Tasks 7–9. PostgreSQL is structured authority, S3 stores immutable document bytes, and Qdrant stores vector generations. Packet 1 creates bounded immutable intent, an unresolved user gate, and two permanent ID reservations. Packet 2 owns live publication, exact write-once evidence slots, terminal commit, and detached read-only proof. Packet 3 may consume those authorities only after Packet 2 has passed its independent review.

The fixed-source inventory `.runtime/distributed-cutover/restart-mutator-inventory.md` is static scope evidence from source `fa1c25169b4547e7db7d2875595afafb5662a2d4`; it is not implementation acceptance. It identifies ungated direct PostgreSQL `ImportStore` controls, inherited control methods, coordinator/completion callbacks, witness insertion, vector `_index(create=True)`, source/task/audit writes and parent cascades. Current runtime bootstrap refusal does not protect directly callable repositories.

Authority mutation lock order is **user → ordinary user mutation lease → task → task audit → evidence/gate → recovery lease → queue → reservations/generation rows**, with RAG before episode. Recheck database-clock liveness after blocking locks. A recovery queue row must be committed before waiting for user authority. Expiring/releasing an ordinary lease never resolves the gate.

## Accepted Packet 2 dependency and source bindings

- Accepted source: `2f39378e2228a65ee68dd2a1e3b06d38a31a9412`; tested BASE: `d6cc2ac913b3cc5704dde6bd8b482a6c7dcb90a0`; this packet's base: `cb06adb31be518d465eefdf70cbe3be36d458214`.
- Independent Spec/Quality/Delivery report: `.runtime/distributed-cutover/restart-packet2-2f39378-final-delivery-astra.md`, SHA256 `62E80161A618503C9D2620130751B69B2647BC1A3B2B30F2E38965CE3BFB466E`; audit JSON `7BC4392290C119A03176C591B7B0DA788248409482885A1D90520530789388A7`.
- Final source manifest SHA256 `783618FD5919D45F82E231FF7263C0C90B745C272F7534662D62F469FDB8BFCB`; complete raw commit binder `F65ADAFB3C42B07EE1C4F8FB38478D5AE2C89E23C3996C381C439411C8B6FA64`. All 29 reviewed/tested/working/committed raw paths match; 17 actual source/test changes. Final three groups: 194/159/29, 382 cases, zero failures/errors/skips and each exit 0. No historical/focused count is added.
- Documentation descendant `cb06adb` was freshly observed at the remote branch with `ls-remote` exit 0. The earlier push session became unavailable before its outer exit could be recovered; that missing exit is not fabricated. This is independent-branch delivery, not main/runtime/production acceptance.
- Fresh mechanical inventory and semantic advisory: `restart-packet3-mutator-inventory-7836-luna.{json,md}` (`B1AA77BABAE6D85AEBF92DB749188EF9F486833CC48C0CDCE3DE165C48BE4D2C` / `7C32F4251FD894FC89A3CA697E3D2C8893D3273AC01247B3D1117BD313A34AD3`) and `restart-packet3-7836-binding-advisory-astra.md` (`6C6CD1F422D05D37C97E838548C87CBC0A1288A57E4764826B796E8D942A4EC0`). Scanner labels are not semantic guard proof; the final implementation must close all actual entrances.
- Reviewed contract revision: `restart-packet3-7836-contract-revision-sol.md` (`1F0021A0629211B3EBB173B1C1B01D3AEC5150B480B98FDE172CC12AFA4ABA73`), independent delta `restart-packet3-7836-contract-delta-astra.md` (`468A001000BBF8E9DEE6BB0EC8BE139BFBC8602E87FE9073D2CD1FC6CACBAED2`), READY-CONDITIONAL proposal only. Its B1/B2/B3 and exact adapter/commands are incorporated here; the corrected precise SQL design below is independently READY; final actual-packet READY is recorded below.

Accepted `AttemptKey` is `app.import_publication_evidence.AttemptKey(user_id: str, task_id: str, task_lease_version: int)`, frozen. `FrozenEvidence` contains `key, intent, phase, phase_version, observation_reason, intent_hash, document_slot, rag_sealed_slot, episode_sealed_slot, terminal_slot`; shallow frozen containers are data, not authority. `_14` format is `canonical-json-zlib-1`; maximum canonical intent 64 MiB, compressed payload 8 MiB, each slot 256 KiB, aggregate 9 MiB. Slots are canonical bounded UTF-8 JSON. Document slot has `(user_id, document_id, bucket, key, version_id, sha256, size_bytes, record_hash, final_expected_hash)`; final hash is SHA256 of `b"1:" + ASCII intent_hash + b":" + canonical(unsigned first-eight-field slot)`. Sealed slots retain the exact 17-field representation and UUID owner tuple validated by the accepted codec; copy the accepted validator, not a guessed shortened projection. Terminal slot stores the final expected hash and ordered RAG/episode 20-field receipts, including UTC-microsecond created/sealed/published timestamps. Intent starts version 1; every null-to-slot/observation CAS increments once, RAG seal remains document_verified, episode seal yields pair_sealed, terminal append yields terminal_committed, acknowledgement/proper abandonment increment once. No duplicate slot rewrite is legal.

The original private C signatures are `_issue_live_publication(rag_scope, attempt, new_rag_points, *, event_vector, event_profile) -> _LivePublication`, `_execute_live_publication(live) -> DetachedImportMemoryPublication`, `_FixedTerminalWork.run(cursor, admission)`. `try_begin_committing(attempt, *, live: object | None = None)` and `complete(attempt, publish=None, *, terminal_work: object | None = None)` preserve no-gate forms. Opaque exact-type tokens use original issuer registries; the original `_TerminalAdmission` covers only `terminal_entry, task_source, snapshot, memory_document, document, witness`, pair_sealed state, cursor/active transaction/txid and exact values. Packet 3 adds only the precisely declared vector operation and separate release continuation below. Original domain admission closes before `_finish`; it cannot be reactivated or used as release authority.

`DurablePublicationUnknown(attempt_key: AttemptKey, phase: str)` lives in `app.import_memory_publication`; do not consume legacy `ImportMemoryPublicationUnknown(context, expected, phase, cause)` as the durable contract. `ImportPublicationProofService(database).prove_exact(user_id, task_id, task_lease_version)` in `app.import_publication_proof` returns `DetachedImportMemoryPublication | None` or `PublicationProofUnknown(attempt_key, phase)` using one repeatable-read/read-only cursor. Detached result fields are `pair, event_id, history_version, memory_version, document_ref, witness, proof`; envelope fields `attempt_key, intent_hash, final_expected_hash, phase, phase_version, rag_receipt, episode_receipt`. Neither carries a live attempt/token/admission/callback or private pair context. `None` is not rollback permission; recovery independently reloads exact header/private bytes and all predicates before abandonment.

The following accepted raw hashes are mandatory serial inputs. New Packet 3 source/test files are absent until assigned; existing verification-only paths do not grant edit ownership.

| Path | Accepted raw SHA256 |
|---|---|
| `app/import_document_publication.py` | `24e18be34e0cf18ed3de91bb94b87a70ebe7cd4fc7c4d0b5d5cf8200fc8c82eb` |
| `app/import_memory_publication.py` | `2c03b2d1729f5db8bd3aa173aef10f917d305622137b3e65d07347d08960875b` |
| `app/import_vector_publication.py` | `ed4fbf260c57da3a442bb5e769d19b7ac253e3bf3d2d2e278086154cb011c8b1` |
| `app/import_publication_evidence.py` | `5dc303431b3cac5172670e66200977e86b523fb989b40a9497ad6ff7a611c250` |
| `app/import_publication_proof.py` | `ea0e5ff3606c85265e7e9204a7cbdd1a6e364c549feb80db3b4ed9f85933b57d` |
| `app/postgres_document_objects.py` | `180484f35ff92a01e7382710cbd4a5aed9749317150f40f09fd3b1ede760fe6f` |
| `app/postgres_history_document_witnesses.py` | `51c6d41727f9ac51b2c29728cb476dcdac5e22acbb062e5082c1bc8547557b50` |
| `app/postgres_import_leases.py` | `2867177e53add6d5c919d8ac4d8991192248c3856a652fe79ab98d2f7d334145` |
| `app/postgres_memory_documents.py` | `0c023e1bbccccc589460cf942b8c3dced03ac7e6c4f7a0d0cbf0c72ba49a512b` |
| `app/postgres_snapshots.py` | `d7cf29945acaf434bbb1057aa7e8a08ee4659a47503b90c51542d81f7453a18f` |
| `app/postgres_vector_generations.py` | `fda391c9ab79797625f90b4293783ff1f98a4ae4baa91a5aa41206210ffd1de0` |
| `app/vector_generation_service.py` | `738873199df2e19ec0a8a9307ad11702b84acc6181e89a19340afd02980ab6f2` |
| `hello_agents/memory/storage/generation_vector_store.py` | `152bd25208d1fe24d48311fddad1205f7ea78921c926dc52cc37a3fd4b923a77` |
| `migrations/versions/20261002_14_import_publication_evidence.py` | `c42da93851d52f00f8a52a57be040ddac2ff6c32f2029c67be9d484bcf31e448` |
| `tests/integration/test_import_publication_evidence.py` | `4ad7ab00601ad9ebbbef632d4f40c15a5299d56c1293a8b1b215376e42679b3b` |
| `tests/integration/test_import_publication_proof.py` | `f57b54c292eabd30124abc3e8ebb74e6233839c0f33206b2dffb06413bb9052e` |
| `tests/integration/test_import_memory_publication.py` | `31a0f713221cd49f331829d82b9cb2801bde81b335aca105ae20417e342a675d` |
| `tests/integration/test_import_vector_publication.py` | `a602244e4f2415ebb960efb827b696913400237f9441a884a533e54458855a85` |
| `tests/integration/test_import_memory_fault_matrix.py` | `da20d6819b6b13ff307ddee04eda8566fcae3f37ce2105ab0a9f2ea16b38062b` |
| `tests/integration/test_import_memory_consistency.py` | `aefc774bda9f8cb24329925abfed441834eff8f2c48c89682e8baabb0c6f1516` |
| `tests/integration/test_import_memory_preflight.py` | `676d334b6daad23a0bb027fc5f3216851313e0a704c1767eb5f69382e8fedd10` |
| `tests/integration/test_import_document_publication.py` | `5b97402921771136ed7e26ce0d6e3cdff30e95b5d745cefe12a09cbf46c7512c` |
| `tests/integration/test_postgres_snapshots.py` | `694cd0857ad4ccfd85085741066c59c5db68c79fd1f057b1a6f9a2996a8b9736` |
| `tests/integration/test_postgres_memory_documents.py` | `9a9b2a0fe01028af20ab5ae30b892f2086fefb046357b53665a96292d5f905f9` |
| `tests/integration/test_postgres_document_objects.py` | `98b666ec918d5f41f21aa757ccfd9606b1d23cb331b9f581f4090448c0ab7bff` |
| `tests/integration/test_postgres_import_leases.py` | `46435a243a7ecc23f62275f47b16ef4e77ef2e7ed8241c95ad1f2de433c3dc23` |
| `tests/integration/test_postgres_vector_generations.py` | `cdc37c09535824f04b4911cec4373f295ac63caf42585e7796ca87d1d38d18ec` |
| `tests/memory/storage/test_generation_vector_store.py` | `83f11d23beacc1b50ce454390e5dbf3b730dfc931d6b88590b83d5607156ee30` |
| `tests/integration/test_vector_generation_service.py` | `4d64d8dac55c98752188d5022f2d0e0e64c1e61358b1c661b4c61b0c5307b1e9` |

### Additional owned baseline inputs

These nine existing owned inputs supplement the accepted 29-path table above.
Working bytes are the serial edit baseline. Four raw files match their base
blobs; the five marked `CRLF checkout / LF blob` match only after newline
normalization and retain their existing bytes. Both hashes are recorded; this
does not weaken the accepted 29-path raw equality. The five explicitly new
files are absent at preparation; they are not pre-existing implementation.

| Path | Working raw SHA256 | Base blob SHA256 | Comparison |
|---|---|---|---|
| `app/postgres_coordination.py` | `969b9087cabfe3cb650407a5ed7427275bf5228172712348eebb7ac243ee8fa8` | `1629015e47398921841b84212ced72a199b541162ac8099c0107f94e54803748` | CRLF checkout / LF blob |
| `app/import_persistence.py` | `21854d859586e4fc0231d60ee3745af0f6e5057645b77f9ced851e1dc3299026` | `90ead19124a5281c505e700f24ebaae123564c7f26b5bf374ed0d7c854fbcfa2` | CRLF checkout / LF blob |
| `app/import_repository.py` | `967c667acf0699a2b86e1d975fd3932ee7f4ef850fd3f61101528b83b56cdf71` | `df504733e5c6f2a319874232057c71c515dec323ea7c6c0ffee699048475c443` | CRLF checkout / LF blob |
| `app/postgres_import_artifacts.py` | `f2f15ea070cbe3563a3c3e8d9a91cbca4ac0dc367288d46533050a5fc4841f31` | `f2f15ea070cbe3563a3c3e8d9a91cbca4ac0dc367288d46533050a5fc4841f31` | raw equal |
| `tests/integration/test_postgres_coordination.py` | `e7b5ff0a28b12449149c087acb7143ec6828955537f0616b2ecda754fee592fe` | `014500e0f73bf5ac901593fbca630318592ecf33d9a61c62e36c7020eae8a1c0` | CRLF checkout / LF blob |
| `tests/app/test_postgres_vector_generations.py` | `a67eba54093df6fafd9ee9a6e4fa8171c398d0ea8932c90c97cf419011dc4924` | `a67eba54093df6fafd9ee9a6e4fa8171c398d0ea8932c90c97cf419011dc4924` | raw equal |
| `tests/integration/test_postgres_imports.py` | `51932ba27ca675fb064c2cd7cb18f37293131fc515f0a10b5f26d1b51615eb78` | `51932ba27ca675fb064c2cd7cb18f37293131fc515f0a10b5f26d1b51615eb78` | raw equal |
| `tests/integration/test_postgres_import_artifacts.py` | `150896cf1e3c9acc598c3477442a1294a88d31427bb31d6e42cced369cdaa154` | `150896cf1e3c9acc598c3477442a1294a88d31427bb31d6e42cced369cdaa154` | raw equal |
| `tests/test_import_repository.py` | `8ddee8f696fd985c06c6fde0294bd244e99ecfb94f7494efb0a6ebb07d087ba0` | `99a724d188fb47aaf6e7bd5d5423dbd5bb85d0680290e9e198eff2931c29cf3f` | CRLF checkout / LF blob |

## Relevant files and current interfaces

- `app/postgres_import_leases.py` — Packet 2 owns only `try_begin_committing`, `complete`, and necessary private terminal-admission helpers. Packet 3 receives the remaining lease/control/recovery methods after Packet 2 review; evidence-bearing expired and terminal-response-loss attempts must enter a sanitized non-due hold.
- `app/postgres_coordination.py` — ordinary acquire, publication callbacks, heartbeat/release and liveness paths. Gate checks must precede ordinary callback execution; recovery must not clear a gate through these ordinary methods.
- `app/import_persistence.py` — PostgreSQL `ImportControlRepository` and directly constructible `ImportStore` writers. Cover `request_cancel`, `retry_task`, `retry_failed_in_batch`, `cancel_queued`, `request_running_cancel`, `retry_task`, `retry_batch`, `touch_batch`, `insert_batch`, and `insert_tasks`; a checked factory alone does not secure an escaped store/cursor.
- `app/import_repository.py` — PostgreSQL task/store factory and caller-owned transaction boundary, including `PostgresImportTaskRepository.create_batch_in_transaction`; distinguish worker methods explicitly disabled by `_lease_required` from inherited control methods that remain callable.
- `app/postgres_import_artifacts.py` — SQL task/batch and `import_objects` admission after immutable source upload. Preserve the exact source pin; unresolved references must prevent direct source deletion and source/task/parent cascades. Pair this with direct `import_task_attempts` deletion negatives. Pre-attempt upload remains outside the publication-before-I/O rule.
- `app/import_publication_evidence.py` — Packet 1 codec/repository and Packet 2 live write-once slots. Packet 3 may add only reviewed recovery resolution/retention operations; separate resolved private payload/FK lifetime from permanent UUID tombstones.
- `app/postgres_vector_generations.py` — Packet 2 publication owns stage/seal/live publication. Packet 3 owns recovery-only pair revocation, the absent-row reservation case, and any remaining low-level guard such as `_index(create=True)` after serial handoff.
- `app/postgres_document_objects.py` and `app/postgres_history_document_witnesses.py` — Packet 2 exact terminal admission; Packet 3 closes remaining low-level paths. Bare witness `insert` must establish/validate the user-first transaction and guard before SQL.
- `app/postgres_snapshots.py` and `app/postgres_memory_documents.py` — Packet 1 ordinary gate denial and Packet 2 exact terminal admission. Packet 3 verifies no direct update/delete or callback route bypasses the gate; do not broaden their ownership without a reviewed boundary update.
- `app/qa_deletion.py`, `app/document_library.py`, `app/coordination.py`, `assistants/pdf_learning_assistant.py`, `app/history.py`, `app/memory_repository.py`, `app/recovery.py`, `hello_agents/memory/manager.py`, and `app/qa_repository.py` — inventory classifies these current paths as local-only or distributed-disabled. Keep them disabled; any native distributed deletion/fence writer newly found by the fresh call-site inventory must be explicitly added to the owned-file list before dispatch.
- Tests: create `tests/integration/test_import_publication_recovery.py`; consume accepted detached proof behavior from `tests/integration/test_import_publication_proof.py`; extend `tests/integration/test_postgres_import_leases.py`, `tests/integration/test_postgres_vector_generations.py`, `tests/app/test_postgres_vector_generations.py`, `tests/integration/test_import_memory_fault_matrix.py`, `tests/integration/test_postgres_snapshots.py`, `tests/integration/test_postgres_memory_documents.py`, `tests/integration/test_postgres_imports.py`, `tests/integration/test_postgres_import_artifacts.py`, and `tests/test_import_repository.py`. Verify these exact module filenames and necessity against the accepted Packet 2 tree before dispatch.

## Prerequisites

### Packet dependencies

- `restart-publication-evidence-02` must be `done` and independently accepted. Packet 1 must remain accepted. No concurrent worker may own shared files.
- The accepted Packet 2 names, signatures, `_14`, wire rules and raw source hashes are bound above. New Packet 3 private release/vector/recovery operations are proposed interfaces to implement only after final READY; they are not existing Packet 2 APIs. Use no older attempt-only append permission.

### Repository/base state

- Base commit: `cb06adb31be518d465eefdf70cbe3be36d458214`, accepted Packet 2 documentation descendant. Source commit `2f39378e2228a65ee68dd2a1e3b06d38a31a9412` is its immediate parent. All 29 source/test raw files are identical to the accepted manifest. Coordinator-owned draft/plan changes and the byte-identical stat-only lease-test M are explicitly preserved; only the designated serial Sol implementer may receive source ownership after the recorded READY and explicit root dispatch.
- Packet 1 `_14` and accepted Packet 2 implementation must be present and unchanged from their accepted hashes. Migration `_15` follows `_14`.
- Required ownership is serial. Packet 2 may have touched shared files that Packet 3 later receives; do not edit them before review and handoff.
- Fresh `rg` inventory over `app` and migrations must classify every proof-related SQL writer as guarded, disabled in distributed mode, or read-only. Add any newly discovered native deletion/fence writer to this packet before assigning it.

### External prerequisites

- Windows repository virtual environment: `D:/python_self_agent/venv/Scripts/python.exe` (verify actual worktree path mapping before dispatch).
- Disposable PostgreSQL, S3, and target Qdrant test resources configured by `POSTGRES_TEST_URL`, `S3_TEST_ENDPOINT`, `S3_TEST_ACCESS_KEY`, `S3_TEST_SECRET_KEY`, and `GENERATION_QDRANT_TEST_URL`. Never use retained or production resources; do not print credentials. Required integration tests cannot be counted as accepted if skipped for missing dependencies.
- One designated Packet 3 worker owns the sole pytest lane. Use repository-local `--basetemp` paths. Do not run tests until the worker confirms the disposable identities and lane are available.

### Current disposable service identities (2026-10-04)

Root restored only the three previously authorized disposable containers after
fresh exact-ID/mount/loopback inspection; each `docker start` exited 0. PostgreSQL
`pg_isready` exited 0; MinIO and target Qdrant readiness returned 200. This is
service preparation, not authenticated business or product acceptance. The
protected source was observed only and remains exited. The snapshot has no
Python process; the implementer must recheck its sole pytest lane before tests.
Runtime secrets stay in process-local test configuration and are not in this packet.

| Role | Container and full ID | Loopback endpoint | Retained mount |
|---|---|---|---|
| PostgreSQL disposable | `zhiyan-dist-bf20-postgres-tests-20261002` / `1223c178a3381daf0a26616d761150e8ff2287ff5ba3cd27525abf0ade7a4fe7` | `127.0.0.1:59497` | `zhiyan-dist-bf20-postgres-tests-20261002-data` → `/var/lib/postgresql/data` |
| S3 disposable | `zhiyan-dist-bf20-minio-tests-20261002` / `68a974561f1e456bdb0fae7a5be425301b967777779e920166cc6a508c60cb46` | `http://127.0.0.1:59498` | `zhiyan-dist-bf20-minio-tests-20261002-data` → `/data` |
| Qdrant target disposable | `zhiyan-dist-bf20-qdrant-vector-target` / `6bb7e303df12694d9094ce11398b7f05c811cb58dd70c4ba48f78ce628d4f017` | `http://127.0.0.1:60266` | `zhiyan-dist-bf20-qdrant-vector-target` → `/qdrant/storage` |
| Qdrant protected source — read-only observation | `zhiyan-dist-bf20-qdrant` / `bb5a56d410d7f2da22a49a853b68dfa907a35e87afd306e886f469866b14f771` | `exited; no current endpoint; do not use historical 59577` | `zhiyan-dist-bf20-qdrant-data` → `/qdrant/storage` |

Target Qdrant's old `59574` was dynamic and is no longer current. Set
`GENERATION_QDRANT_TEST_URL=http://127.0.0.1:60266` only after confirming the exact
target container and current binding; never substitute source Qdrant, old
59499/59500 endpoints, retained PG/S3 or the main application. A future restart
requires a fresh binding check instead of trusting this cached port. Snapshot
`restart-packet3-disposable-services-after.json` SHA256
`764B1D79E03E1D8E69BE3477B82E24647FA8E88800F16E87F95CE8906A56E705`;
before snapshot SHA256
`E8EEE29AC8B3BE79A450B61D13C2D9EA3C8F64E8BB66EA3DFC3C3AA406BDDD7C`.

## Explicit change boundary

### Allowed files

- Create: `migrations/versions/20261002_15_import_publication_recovery.py`, `app/import_publication_recovery.py`, `app/import_publication_worker_adapter.py`, `tests/integration/test_import_publication_recovery.py`, `tests/app/test_import_publication_worker_adapter.py`.
- Narrow modify after Packet 2 handoff: `app/import_memory_publication.py` only for B1/B2 below.
- Modify after Packet 2 handoff: `app/postgres_import_leases.py`, `app/postgres_coordination.py`, `app/import_persistence.py`, `app/import_repository.py`, `app/postgres_import_artifacts.py`, `app/import_publication_evidence.py`, `app/postgres_vector_generations.py`, `app/postgres_document_objects.py`, `app/postgres_history_document_witnesses.py`.
- Serial test ownership after Packet 2 acceptance: `tests/integration/test_import_publication_evidence.py`, `tests/integration/test_import_publication_proof.py`, `tests/integration/test_postgres_import_leases.py`, `tests/integration/test_postgres_coordination.py`, `tests/integration/test_postgres_vector_generations.py`, `tests/app/test_postgres_vector_generations.py`, `tests/integration/test_postgres_imports.py`, `tests/integration/test_postgres_import_artifacts.py`, `tests/integration/test_import_memory_fault_matrix.py`, `tests/integration/test_postgres_snapshots.py`, `tests/integration/test_postgres_memory_documents.py`, `tests/test_import_repository.py`. Preserve the accepted negatives; adapt raw tamper fixtures only where `_15` newly rejects their setup.
- Modify after fresh inventory and explicit ownership amendment only: a native distributed deletion/fence writer identified by the inventory; relevant existing PostgreSQL import-control/fault tests listed above.
- `hello_agents/memory/storage/generation_vector_store.py` is Packet 2-owned unless the accepted Packet 2 review explicitly hands off a concrete remaining callback change to Packet 3.
- Additional owned existing tests required by the coherent first slice: `tests/integration/test_import_memory_publication.py`. The other compatibility command modules (`tests/test_import_worker.py`, `tests/test_user_mutation_coordination.py`, consistency/preflight/vector/document publication) are verification-only unless already explicitly owned above; merely appearing in a command grants no edit permission. This packet grants no open-ended scope.

### Allowed behavior changes

- Detect evidence before ordinary expired-lease recovery changes task eligibility. Hold evidence-bearing and terminal-response-loss attempts as sanitized `needs_reconciliation`, keep the gate unresolved, keep both reservations, and prevent due/retry/cancel/release paths from erasing unknown outcome.
- Add a bounded, fair recovery claim queue (oldest first per user; fair across users) using a short `FOR UPDATE SKIP LOCKED` claim transaction, committed before a separate user-first authority transaction. Validate recovery token/version/phase/expiry with DB time after blocking locks; reject unsupported transaction isolation/autocommit.
- Run detached exact proof before metadata changes. Exact successful proof may atomically acknowledge `proved_succeeded` and resolve the same gate. A mismatch, unreadable proof, terminal state without complete proof, or unsafe predicate leaves a non-due manual hold.
- For eligible unproved pre-terminal evidence, atomically revoke **both** permanent UUID reservations even if one or both generation rows are absent; only exact owned `staging|sealed` rows may be abandoned. Keep RAG then episode lock order. A late Qdrant write into a revoked namespace remains unpublishable.
- Gate every active direct proof-related writer after user lock, including coordinator and task acquisition, `complete_import` callbacks, vector index creation, witness insertion, PostgreSQL `ImportStore` and inherited control methods, source/task admission and relevant deletion/fence paths.
- Protect unresolved evidence dependencies from direct and cascading deletion of task, batch, user, source and audit rows, including direct `import_objects` and `import_task_attempts` deletion. Separate private evidence-payload lifetime from immutable task/receipt/generation parent lifetime: payload cleanup cannot authorize deleting tasks or detaching immutable receipts/generations. No applicable approved numeric PostgreSQL cleanup window or cleanup implementation is established by the retention baseline. Policy is TBD; with no applicable approved/configured policy, cleanup stays OFF. Do not substitute 30 days or add cleanup authority. Preserve both permanent UUID tombstones and global uniqueness independently of private payload lifetime.
- Add a narrow opt-in internal Worker adapter that calls C once, catches Unknown, requests detached proof and returns stable non-due `needs_reconciliation`; it stays unused by default runtime/API/bootstrap composition.

### Forbidden changes

- Do not claim missing generation rows, elapsed time, absent Qdrant points or a failed/lost response prove rollback.
- Do not invoke live C/B code, token verification/issuance, external S3/Qdrant writes, task-success callback, or a reconstructed private Packet 2 context from recovery.
- Do not let recovery lease call ordinary live publication writers, or let an ordinary task/user lease acknowledge proof or resolve the gate.
- Do not allow arbitrary user callbacks, direct PG store construction, escaped cursors, mixed-user batch mutation, witness insertion, `_index(create=True)`, direct source/audit deletion, or parent cascades to bypass gate/retention guards.
- Do not delete or rewrite immutable receipts, weaken existing object/witness/vector history protections, delete permanent UUID tombstones, or let any future private-payload cleanup remove unresolved proof dependencies or imply task/receipt-parent deletion. No-policy cleanup is a no-op; a 30-day default is not authorized.
- Do not wire Worker/API/bootstrap, alter deployment/runtime flags, touch production/protected resources, or broaden into local SQLite lifecycle migration.

## Interface contract — independently READY implementation contract

The accepted Packet 2 dependency, exact types and raw hashes above are closed.
This contract incorporates the reviewed B1/B2/B3 revision and the finite D1/D2
corrections. Earlier advisory proposals remain historical evidence, not current
interface placeholders. The corrected precise `_15` SQL/trigger contract is included below after
independent design READY. Independent final review of this actual packet, owned-file bindings and
synchronized plan is recorded below. Serial root assignment activates only
the declared implementation boundary; service/lane checks precede test invocation.

### Binding safety boundary

An unresolved evidence attempt owns one user gate and two globally unique reserved generation UUIDs. Lease expiry never resolves that gate. Recovery reads exact detached proof first, then either acknowledges the same fully proved already-succeeded attempt or atomically revokes both IDs and abandons a strictly eligible pre-terminal attempt. Unknown, corrupt, mismatched, unreadable or incomplete terminal evidence stays held. Recovery never reconstructs a `VerifiedDocumentRef`, calls S3/Qdrant, replays stage/seal/publish, changes a succeeded task to a failure, or grants a live publication capability. The original task/user lease tuple and a separate recovery authority lease have disjoint powers.

### Accepted baseline facts and proposed schema changes

- `_01` constrains `import_tasks.status` to `queued|running|retry_wait|succeeded|failed|cancelled`; `needs_reconciliation` is not a persisted task status. `_09` gives `(task_id, lease_version)` audit identity and ordinary task/user lease tuples. `claim_next` currently selects due queued/retry-wait tasks, and `recover_expired` currently requeues expired running tasks.
- `_14` keeps full private intent and slots on `import_publication_evidence`, while permanent `generation_reservations` and gates FK to that row. It has immutable slot/phase guards, a one-unresolved-gate-per-user index, and permanent reservation DELETE denial. `_15` must split permanent small identity from private payload lifetime; it cannot simply remove the reservation FK and permit UUID reuse.
- `_14` evidence NO ACTION references block task/batch/user ancestor deletion while evidence exists. Direct `import_objects` and `import_task_attempts` deletion are not protected by `_14`. `_10` vector generations FK to tasks; `_11` forbids generation DELETE and preserves immutable owner/receipt fields. Private payload lifetime cannot imply deletion of those retained parents or receipts.
- No applicable approved numeric PostgreSQL retention policy or cleanup helper exists at this pin. Cleanup is OFF; no 30-day default, age cutoff or task/receipt deletion authority is introduced here.
- Exact accepted Packet 2 immutable slot/hash/proof bindings are listed above. Recovery consumes only detached selectors/results and independently revalidates authority predicates; no worker may infer a publication capability from those values.

### Proposed `_15` schema and lifetime contract

1. Keep `import_publication_evidence` as a small immutable attempt/header authority keyed `(user_id, task_id, task_lease_version)`, retaining its original tuple, `schema_version`, `intent_format`, `intent_hash`, monotone phase/version, observation reason and timestamps. Move the bounded `intent_payload`, `canonical_bytes`, `document_slot`, `rag_sealed_slot`, `episode_sealed_slot`, and `terminal_slot` to a one-to-one `import_publication_private_payloads` table with the same exact key, an FK to the header, the existing length/aggregate bounds, and byte-for-byte accepted Packet 2 payload/slot encodings. Backfill existing `_14` rows atomically and adapt accepted Packet 2 readers/writers only after its serial handoff. A resolved header remains small and permanent; reservations and gates continue to FK to that header, so neither ID nor a resolved gate can lose identity when a future approved private-payload expiry runs. A pre-existing `_14` row cannot lose its payload during migration. The migration must not silently reinterpret its phase or hashes; unsupported formats hold. The exact cross-table CAS and replacement trigger strategy is fixed below.
2. `import_publication_recovery_queue`: exact attempt key FK to header, `queued_at`, `due_at`, `state` (`pending|claimed|manual_hold|resolved`), `queue_version`, nullable `claim_token`, `claim_expires_at`, `last_claimed_at`, sanitized `reason_code`, `transient_count` (nonnegative integer) and `updated_at`. Uniqueness is the exact attempt key. Insert it **in the same transaction as durable intent/gate/UUID reservation**, with `due_at` no earlier than the greater original task/user lease expiry; backfill one row for every unresolved legacy `_14` header during migration. Thus a committed terminal response loss is discoverable after restart even when no Worker survives to catch Unknown and `recover_expired` sees no `running` task. Original heartbeat may extend the leases; recovery checks their current database-clock expiry under authority locks and reschedules an identity-exact pre-terminal attempt if either ordinary lease remains unexpired, without calling it a manual mismatch. An explicit Unknown can bring the queue due earlier only for read-only proof; it cannot grant abandon while the original tuple lives. `manual_hold` has no due time and cannot be claimed automatically. Requeue after a transient database outage is bounded by the policy below, never an outcome proof. A change from manual hold requires an explicit reviewed operator/reinspection path; ordinary task retry cannot do it. Queue rows for unresolved attempts cannot be deleted by age.
3. `import_publication_recovery_leases`: exact attempt key FK to header, recovery `owner`, UUID `token`, strictly increasing `version` on new grant/takeover, `heartbeat_at`, `expires_at`, and captured queue `claim_token/queue_version`; only one current holder per attempt. Duration uses the coordinator's integer 1–86400-second validation, default 60 seconds for both queue and recovery. This row is distinct from `user_mutation_leases` and task lease fields. A permanent small `import_publication_recovery_token_issuance` table has global `token uuid PRIMARY KEY`, `kind queue|recovery`, exact attempt key, issued version/time, and DELETE denial; every new queue claim and recovery grant/takeover inserts its fresh token in the same transaction, so even an older ABA token cannot be reissued. Same-holder renewal preserves token/version and adds no issuance row. Lease/queue triggers reject version rollback and identity changes. All authority checks use PostgreSQL `clock_timestamp()` after potentially blocking locks, comparing exact worker, token and version, queue capture, unresolved gate, phase/version and exact attempt. Recovery takeover occurs only after expiry under the user-first lock order. Renewal cannot change scope; expiry gives no success/abandon inference.
4. `import_publication_recovery_schedule`: one row per user, `last_claimed_at` updated with the short queue claim to rotate fairness. The queue stores only metadata and IDs; full snapshots remain in the private table. Deletion of schedule metadata cannot delete proof or a reservation.
5. Keep `generation_reservations`' global UUID PK and permanent DELETE denial. `reserved` remains permanently reserved after proved success; `revoked` is permanent after exact abandonment. Both states bar UUID reuse. `_15` does not delete/detach vector generations or relax `_10`–`_13` immutable receipts, task FKs, document references or History witnesses. The private payload FK lifetime is separate from those retained parent identities.
6. Retention **OFF**: `_15` may create the schema split and a predicate/guard that would reject private-payload deletion unless the header is resolved and its gate resolved, but no scheduler, age cutoff, public endpoint or generic delete helper runs. Do not add a numeric policy, a 30-day fallback, or task/audit/receipt-parent deletion. A future approved policy must separately specify eligible resolved classes, configuration validation, timestamp basis, cutoff, backup/privacy handling, and tests. Unresolved payload and every exact source/audit/proof dependency remain. With policy absent, even resolved private payload remains.
7. Add database-level source/audit dependency protection, not just checked repositories. `import_objects` `DELETE` and updates of pinned bucket/key/version/digest/size or `(task_id,user_id)` refuse while any unresolved evidence for that task/user exists. For `import_task_attempts`, deny direct `DELETE` while its exact evidence is unresolved and make the identity/owner/token tuple immutable. The paired `ended_at/end_reason` changes at most once: while unresolved, permit null→`succeeded` only after the same transaction has appended the exact terminal evidence slot/`terminal_committed` phase, and permit null→`lease_expired` only when database-visible predicates prove the exact old task/audit/user tuple, both ordinary leases expired at a fresh `clock_timestamp()`, task still `running` and not succeeded, and header pre-terminal with the gate unresolved. The latter transition is shared by `recover_expired` and direct queue recovery; the trigger must not depend on a Python method name or session flag. Deny arbitrary end-reason/result rewrites, unpaired end fields and all other end transitions under the gate. Preserve exact live-attempt heartbeat/last-stage updates under the user-first repository gate; they are not blanket-denied by the trigger. This narrow trigger/order must be reconciled against accepted Packet 2 `complete`/`_finish` SQL before READY; do not impose a guard that blocks its terminal success transaction. Retain evidence/header NO ACTION FKs to task/user so task, batch, user ancestor cascades fail while relevant evidence exists; direct source/audit deletion requires its own negative tests. Once a header is resolved, existing `_10/_11` generation/task and witness/object constraints still govern parents. No arbitrary direct SQL deletion is an authorized cleanup path.

### `_15` cross-table phase/slot CAS and migration order

The child has `payload_phase_version bigint NOT NULL` in addition to the copied private bytes. For every unresolved header update, including an observation-only version increment, the writer first locks the header `FOR UPDATE` under the user lock and compares exact phase/version and immutable hashes. In that same caller transaction it updates the child with `WHERE payload_phase_version=:old_version`, appending only permitted null-to-value slots (or leaving bytes unchanged for metadata-only transitions) and setting `payload_phase_version=:old_version+1`; then it CASes the header with `WHERE phase_version=:old_version` to the same new version and allowed phase. Both affected-row counts must equal one. The child SQL fence may read the header but must not acquire a reverse header row lock after the child UPDATE has locked its row. The repository locks header before child. Immediate child checks and the precisely reviewed deferred event rules reject private-byte rewrite, premature slot append, wrong transition, or an update without the expected old version; the header guard retains immutable identity/hash and monotone transition rules. Deferred constraint triggers on **both** header and child evaluate the final transaction state: while unresolved, exactly one child exists, its mirrored version equals the header version, required slots are present for `document_verified`, `pair_sealed`, and `terminal_committed`, premature slots are absent, and the existing per-slot/aggregate byte limits hold. A transaction may have a temporary child-first intermediate state but cannot commit one. No direct child append can commit without the paired header CAS; no header phase/version CAS can commit without a paired child update. Terminal `proved_succeeded` and `abandoned` also mirror the version while payload retention is OFF. A future approved resolved-payload deletion would first change the deferred rule for resolved headers only; this packet provides no deletion job or policy.

In one PostgreSQL migration transaction, create and backfill the child from `_14` bytes and phase versions, validate key/count/byte/hash equivalence for all legacy rows, install child/header guards and deferred cross-table checks, replace the old single-row trigger references, then drop the old private columns only after all accepted Packet 2 writers/readers are serially adapted. If a validation or DDL step fails, roll back the entire migration. Do not temporarily disable immutable-slot checks, rewrite canonical bytes, synthesize unsupported formats or expose a committed header without its child. Fault-inject after child append and after header CAS, race stale expected versions, and upgrade a populated `_14` schema; each failed transaction leaves both tables unchanged.

### Schema-valid hold and ordinary claim exclusion

`needs_reconciliation` is a sanitized outward `reconciliation_status` projection for an unresolved gate/evidence or a manual recovery hold. It is **not** written into `import_tasks.status` or `ImportStatus`. For an expired pre-terminal `running` task with evidence, `recover_expired` closes only the exact ordinary task/user tuple and audit end, then stores `status='retry_wait'`, the existing stage value, `next_attempt_at=NULL`, a fixed sanitized `error_code='needs_reconciliation'` and generic summary; it seeds only a missing exact recovery queue row and preserves any existing disposition/capture under D2 in the same transaction. It does not reset frozen task/source data, progress, attempt counters, evidence phase or reservations. `_DUE` and every ordinary claim path must add `NOT EXISTS` unresolved user gate / unresolved exact evidence, so NULL due time cannot requeue this hold. The gate is the durable exclusion authority, not a distant timestamp sentinel. A `succeeded` task with terminal response loss remains `succeeded` byte-for-byte; its unresolved gate and recovery queue produce the same outward reconciliation projection until strict acknowledgement. A terminal phase that failed full proof is held with `manual_hold` observation and the task remains succeeded if it already was succeeded. No recovery path demotes success.

Use one private `close_expired_evidence_attempt_in_transaction(cursor, exact_key, frozen_tuple)` from both `recover_expired` and direct queue `abandon_exact_in_transaction`; this helper never starts, commits or rolls back a transaction. Its caller has already locked user → ordinary user lease → exact task → exact audit → header/gate and then checks fresh database time. The first call requires `status='running'`, pre-terminal header, exact audit `ended_at/end_reason IS NULL`, exact frozen task worker/token/version and user owner/token/version, and **both** ordinary lease expiries `<= clock_timestamp()`. It updates that audit once to `ended_at=now,end_reason='lease_expired'` (no success/error rewrite), changes only task `status='retry_wait'`, preserves its stage/progress/started_at/counters and frozen owner/lease columns, sets `next_attempt_at=NULL`, `finished_at=NULL`, `updated_at=now`, `error_code='needs_reconciliation'` and a fixed generic summary, and expires the exact task and ordinary user lease with `LEAST(existing_expires_at,now)` CAS. It touches the batch `updated_at` consistently with pinned lease recovery; it does not resolve gate, queue, phase, reservations or generations. A repeated call is a read-only exact match of that closed audit plus held task and expired original lease tuple; any partial/mismatched state fails closed. The scanner ensures the exact queue row exists in its same transaction using D2: seed only a missing row, preserve existing pending scheduling/captures/manual hold, and never reopen resolved. Direct queue abandonment requires its already claimed exact row. No new owner can acquire an unresolved gated user between these transactions.

For a direct queue discovery of a pre-terminal still-`running` attempt with no prior `recover_expired`, strict proof runs first. If abandon predicates pass, `abandon_exact_in_transaction` calls the shared closure **before** reservation/generation revocation and evidence/gate/queue CAS in the same transaction, so a later failure rolls back the audit/task closure too. If the scanner already made the hold, the helper validates its exact closed shape and the abandon transaction proceeds. After both-ID revoke, evidence `abandoned`, gate resolution, queue resolution and recovery-lease closure all succeed, a final CAS sets this same held task `next_attempt_at=now` and clears only the reconciliation error fields; it remains `retry_wait` with old stage and old expired tuple until a fresh ordinary claim assigns new tokens/versions and reruns current-source/head preflight. The final CAS and all prior writes commit together. No audit gets a second end transition, no fresh claim starts before gate resolution, and no succeeded task is changed by this path. Without evidence, the scanner keeps the pinned ordinary recovery behavior.

The outward adapter returns `needs_reconciliation` without exposing private phase, hashes, source keys, UUIDs, SQL errors or proof contents. Internally, raw `ImportTaskRecord.status` remains one of the six schema/model values. Ordinary `retry_task`, `retry_failed_in_batch`, cancel and direct store controls must refuse while the user gate is unresolved; they cannot set a due status or clear hold. After exact abandonment, a fresh claim is permitted only after gate resolution and queue disposition commit; it uses a new task/user tuple, new UUIDs and current source/head preflight. A retry of a previously `succeeded` task is never allowed.

### Bounded fair queue and separate authority transaction

The private interfaces proposed for Packet 3 are:

```python
@dataclass(frozen=True)
class RecoveryClaim:
    user_id: str
    task_id: str
    task_lease_version: int
    worker_id: str
    queue_claim_token: UUID
    queue_version: int
    queue_expires_at: datetime
    recovery_token: UUID
    recovery_version: int
    recovery_expires_at: datetime

class RecoveryLeaseLost(RuntimeError): ...
class RecoveryUnavailable(RuntimeError): ...
RecoveryOutcome = Literal['proved_succeeded', 'abandoned', 'manual_hold', 'retry_later']

class PostgresImportPublicationRecoveryRepository:
    def claim_next(self, worker_id: str, lease_seconds: int = 60) -> RecoveryClaim | None: ...
    def prove_or_hold(self, claim: RecoveryClaim) -> RecoveryOutcome: ...
    def ack_success(self, claim: RecoveryClaim, proof: DetachedImportMemoryPublication) -> Literal['proved_succeeded']: ...
    def ack_proved_in_transaction(self, cursor, claim: RecoveryClaim, proof: DetachedImportMemoryPublication) -> None: ...
    def abandon_exact(self, claim: RecoveryClaim) -> Literal['abandoned']: ...
    def abandon_exact_in_transaction(self, cursor, claim: RecoveryClaim) -> None: ...
    def renew(self, claim: RecoveryClaim, lease_seconds: int = 60) -> RecoveryClaim: ...
```

`DetachedImportMemoryPublication` is the accepted result type from `app.import_publication_proof`, with the exact fields bound above; no replacement type is proposed. It does not contain or issue the original live `ImportAttempt`, `_PairContext`, `_Expected` or `VerifiedDocumentRef`. `RecoveryClaim` has only recovery metadata and no task/user mutation lease. A stale or malformed claim raises `RecoveryLeaseLost` before any mutation; database unavailability raises `RecoveryUnavailable`, with no fabricated outcome. The outcome string is private and never an `ImportStatus` value. The public wrappers own one `READ COMMITTED` transaction, acquire the full user-first locks and final queue capture lock, call the matching in-cursor core exactly once, then commit or roll back the whole outcome. The in-cursor cores require an already active caller-owned `READ COMMITTED` transaction and locked rows, revalidate exact authority/fresh clock/proof or no-proof facts themselves, perform only SQL on that cursor, and never begin, commit or nest a transaction. Any caller composing closure, revocation and queue/gate resolution must use the in-cursor core; wrappers are for standalone calls only. `abandon_exact_in_transaction` reloads frozen evidence from its locked header/private row; it never trusts a caller-supplied mutable observation.

`claim_next` first performs one bounded short `READ COMMITTED` queue transaction: choose at most one oldest eligible pending or expired-claimed attempt per user with a window/rank over `(queued_at, task_id, task_lease_version)`, order those users by persisted `last_claimed_at NULLS FIRST, user_id`, acquire a candidate queue row `FOR UPDATE SKIP LOCKED`, recheck `(state='pending' AND due_at<=now) OR (state='claimed' AND claim_expires_at<=now)` using a freshly sampled database clock for that check, unresolved gate, absence of a **committed** live recovery lease for that exact key, and phase, then CAS queue token/version/expiry and rotate schedule. The queue-only claimant reads recovery-lease liveness without taking a recovery-lease row lock while holding queue; its decision is provisional, and a racing authority grant must later acquire the queue lock and either win first or discard its stale capture. A claim on a busy row skips it; a permanent proof failure moves to `manual_hold`, and a transient or still-live original gets a finite future `due_at`. The query must not use a fixed leading candidate prefix that repeatedly hides other users. Limit the per-call lock/claim to one queue row; the user gate currently permits only one unresolved attempt per user, but fairness still rotates users. **Commit and release the queue row before touching a user lock.** If the identity-exact pre-terminal tuple has either ordinary lease unexpired after the later authority locks, defer this queue item until the greater fresh expiry plus one second without changing task/evidence/gate; a live original attempt is not a proof mismatch.

In the second `READ COMMITTED` authority transaction, lock **user → ordinary user lease → task → exact audit → header/gate → recovery-lease row → queue row → exact reservations/generation rows** (RAG then episode ID order). No queue lock is held while waiting for user, audit, gate or recovery lease. Only **after** these waits does the transaction lock the exact queue row (or conditional `UPDATE ... RETURNING` lock), reread captured token/version/state/expiry and unresolved gate with a fresh `clock_timestamp()`, and CAS the same capture before granting/taking over the recovery lease. The short queue transaction has already granted and committed the capture. This second authority transaction verifies/CASes that existing capture and grants/takes over only the recovery lease after its final queue lock; it never recreates or extends the first queue claim. On the initial recovery grant, recovery expiry equals the still-live captured queue expiry and receives only its remaining lifetime. Later explicit dual renewal follows the strict extension rule below. If a queue-only claimant replaced the capture during any wait, the old transaction rolls back and grants nothing. If a user/recovery-row wait outlives queue expiry, it discards the claim; never grant a late lease. Queue claim itself grants no proof-status or revocation permission. Subsequent `ack`, `abandon`, `manual_hold`, `retry_later` and `renew` transactions take the same user-first locks and final queue lock, compare exact queue capture **after all blocking waits**, then use a fresh clock for lease liveness; no stale pre-lock read may authorize a write. A queue-only claim may race a live authority transaction but cannot commit a new capture before its old expiry; the authority transaction's final queue lock decides the winner. If authority fails, let the queue lease expire or release it by exact queue CAS; another user remains schedulable.

Private scheduling constants are fixed for this packet: queue and recovery default lease 60 seconds; `lease_seconds` is a strict integer in `1..86400` and sets both durations. `claim_next` attempts at most **3** queue candidates/expired-capture conflicts per invocation, locks at most one at a time and returns `None` after exhaustion without gaining authority or altering an unrelated row. Transient read/DB errors that can be recorded use persistent `transient_count`: increment by one and set `due_at=now+min(5*2^(count-1),300)` seconds for counts 1 through 6 with queue `reason_code='transient'`; on the seventh recorded transient set `state='manual_hold'`, `due_at=NULL`, `reason_code='manual_hold'`, `transient_count=7`, close the exact queue/recovery claim by the existing CAS, and require reviewed reinspection. `recovery_unavailable` is only the sanitized adapter disposition/error classification, not a queue reason value. A DB outage with no safe CAS leaves the captured claim to expire and raises `RecoveryUnavailable`; it does not fabricate a retry outcome or increment a counter. An identity-exact pre-terminal tuple with either ordinary lease still unexpired is **not** a transient failure: set `state='pending'`, `due_at=greatest(current task expiry,current exact user-lease expiry)+1 second`, clear claim metadata, close the recovery lease by exact CAS, and return `retry_later`; repeated legitimate heartbeats may move this due time without exhausting `transient_count`. One `renew(claim, lease_seconds)` call takes the authority lock order and queue row, verifies the still-live exact queue/recovery owner/token/version and gate with fresh post-wait clock, captures one fresh clock and **both OLD expiry values before either write**, requires both old expiries live with exact identities, then chooses each renewed expiry strictly greater than its own old value, e.g. `GREATEST(old_expiry, now + lease_seconds * interval '1 second') + interval '1 microsecond'`. Exact old-value CAS must affect one row on each side; both updates roll back on either failure. Renewal preserves both tokens/versions, creates no issuance, and returns the refreshed expiries without requiring the two new expiry values equal. It cannot revive an expired side or change scope; if either expiry is stale, the holder loses authority and a new claim/takeover needs fresh UUID token and incremented versions. No background/unbounded renewal loop is part of the repository. `manual_hold`, `resolved` and `retry_later` close the recovery lease and queue claim together; no recovery lease may remain live after queue resolution.

Each `prove_or_hold` begins with a read-only exact detached proof, including `intent`, absent generation rows, and reported exceptions. The proof transaction is a single `REPEATABLE READ READ ONLY` snapshot and does not hold the queue or user lock. A successful proof enters the short `ack_success` authority transaction; an expected no-success result may enter `abandon_exact` only if every pre-terminal predicate below passes. Decode/hash error, missing evidence/header/payload, contradictory rows, a terminal phase without full proof, or unexpected proof mismatch enters `manual_hold` through a recovery-lease-validated metadata CAS, preserving the phase and both IDs. An identity-exact pre-terminal tuple with either ordinary lease still unexpired yields `retry_later` at the greater fresh expiry plus one second, not a manual hold. Database outage produces `retry_later` only after a new database connection can safely CAS queue scheduling; if no database write is possible, let the claim expire and report `RecoveryUnavailable`. Do not infer rollback from a timeout or absence.

### Exact acknowledgement, abandonment and observation results

`ack_success`/`ack_proved_in_transaction` are recovery-only **metadata** CAS, not a second completion callback. Under the full user-first lock order, recovery-lease-row then final queue-row serialization, and fresh database clock after those waits, compare the claim token/version/expiry, exact key, unchanged header phase/version and hashes used by detached proof, the unresolved gate, exact task and audit tuple and task `status='succeeded'`, exact complete ordered RAG/episode terminal receipts (including immutable `created_at`, `sealed_at`, `published_at`) and domain witnesses. The proof must establish B's task/audit/receipt and C's History/Memory/document/witness/fence rule against saved expected values. The metadata transaction may only move `terminal_committed → proved_succeeded`, resolve this exact gate, set queue `resolved`, and close the recovery lease. `succeeded` task fields and domain rows are untouched. If any read changed after proof, roll back and return/record a hold; never use a stale proof envelope. A second exact acknowledgement may be an idempotent read of the already resolved same attempt, never a new write or an opportunity to acknowledge a different hash.

`abandon_exact`/`abandon_exact_in_transaction` are allowed only from `intent|document_verified|pair_sealed`; terminal phases never abandon. The authority transaction first revalidates final queue capture and recovery lease under their ordered row locks, then same header/version, intent hash, final expected hash when present, both frozen IDs/reservation scope and exact task/audit/user tuple at a fresh post-wait database clock. It requires both original task and user mutation leases expired, task not succeeded, exact audit not successful, no matching terminal pair receipts or terminal slot, and unresolved gate. It locks RAG then episode reservations and any extant generation rows. Every present generation must match reservation ID, tenant, vector kind, namespace/index key, base revision and original owner/task/user token/version and be `staging|sealed`; `published|retired|abandoned`, mismatch or unreadability means manual hold. A missing generation row is not proof of failure, but its **reservation is still revoked**. In one transaction call the shared ordinary closure (or verify its exact prior hold), set both reservation states permanently `revoked`, set only qualifying present generation rows `abandoned`, CAS evidence to `abandoned`, resolve the exact gate, set queue resolved, close recovery lease and schedule the held task as a fresh `retry_wait` only after those same writes succeed. Any failed predicate rolls the entire transaction back: no audit-only close, one-ID result or gate release. A late Qdrant upsert may land in a revoked namespace but cannot publish through SQL; physical cleanup does not restore its identity.

Observation policy is finite: `proved_succeeded` only after full proof/ack; `abandoned` only after all predicates and both-ID revocation; `manual_hold` for terminal/no full proof, corrupt or mismatched authority, unexpected generation state or unresolved ambiguity; `retry_later` for an identity-exact pre-terminal original tuple with either ordinary lease still unexpired, or a known transient reader/database failure with the bounded queue policy above. The live tuple is never classified `manual_hold` merely for extending its heartbeat. Observation `unknown|manual_hold` never changes a terminal phase to pre-terminal or demotes task success. No output claims publication failure solely from an absent row, zero Qdrant points, expired clock or missing external response.

### All-mutator closure and serial file ownership

Narrow additional serial ownership: `app/import_memory_publication.py` only for the separately issued terminal-release continuation, fixed callback return plumbing, and exact vector-publication admission branch/calls specified below. This is the reviewed B1/B2 amendment; no general C rewrite or candidate-store ownership is granted.

Packet 3's proposed owned source set after accepted Packet 2 handoff is `migrations/versions/20261002_15_import_publication_recovery.py` (new), `app/import_publication_recovery.py` (new), `app/import_publication_worker_adapter.py` (new and mandatory), `app/import_publication_evidence.py`, `app/import_memory_publication.py` (B1/B2 only), `app/postgres_import_leases.py`, `app/postgres_coordination.py`, `app/import_persistence.py`, `app/import_repository.py`, `app/postgres_import_artifacts.py`, `app/postgres_vector_generations.py`, `app/postgres_document_objects.py`, and `app/postgres_history_document_witnesses.py`. `app/postgres_snapshots.py`, `app/postgres_memory_documents.py`, `app/import_publication_proof.py`, `app/import_vector_publication.py`, `app/import_document_publication.py`, `app/vector_generation_service.py` and `hello_agents/memory/storage/generation_vector_store.py` are read/verification targets; edit only via a reviewed ownership amendment if accepted Packet 2 reveals an exact missing guard or callback. No parallel edit of a shared file. The narrow Worker adapter is opt-in/default OFF and does not wire native runtime/API/bootstrap.

Close all active direct entries at the layer that executes SQL, under a user-first transaction and gate check: `PostgresImportLeaseRepository.claim_next`, heartbeat/progress/fail/cancel/release/recover and completion boundaries; coordinator acquire, publication callback, heartbeat/release; direct `ImportStore` constructor methods and caller-owned cursor/factory, inherited `ImportControlRepository` cancel/retry/batch writes; `PostgresImportTaskRepository.create_batch_in_transaction`; SQL source/task admission after pre-attempt S3 upload; vector `_index(create=True)`, stage/seal/publish/abandon and candidate read/upload/verify admission; document object and witness direct insertion; snapshot CAS/update and Memory document add/delete; any native distributed deletion/clear/fence writer found by the fresh call-site inventory. A checked factory alone cannot protect an escaped `ImportStore` or cursor. Guard each proof-dependent mutation after the user lock, and recheck ordinary attempt/gate/reservation authority after every blocking lock and before external I/O. The exact original live attempt may use only Packet 2's issued admission in allowed phase; a recovery lease never satisfies that check. Ordinary owners, including a different live user lease, fail while the gate is unresolved. Reads and other users continue.

The gated **live heartbeat** remains the existing `PostgresImportLeaseRepository.heartbeat(attempt, lease_seconds=60) -> ImportAttempt`: it requires the exact original task/user token/version and audit identity, unresolved gate keyed to that attempt, pre-terminal `intent|document_verified|pair_sealed`, live leases at the fresh post-lock clock, and no recovery ownership. It may extend only the original heartbeat/lease timestamps; it does not require Packet 2's terminal callback admission or issue a publication capability. Its call to `PostgresUserMutationCoordinator.heartbeat_in_transaction(cursor, handle, lease_seconds=60, *, gated_attempt_key=(user_id,task_id,task_lease_version), task_lease_token=UUID)` must validate that same tuple; a coordinator heartbeat without that binding refuses an unresolved gate. Outside a gate, its old signature/behavior remains. No progress, fail, cancel, release or retry path can use this heartbeat exception as a generic bypass.

## B1 — a separate one-use terminal release continuation

Current `app/import_memory_publication.py:147-193` issues `_TerminalAdmission` for a live, `pair_sealed` domain callback. `app/postgres_import_leases.py:274-282` closes it in `finally` before `_finish`; `_finish:290-315` writes task/audit/batch, checks succeeded liveness, releases user, then CAS-expires the task. The current terminal append at `app/import_memory_publication.py:563-566` discards its returned `FrozenEvidence`. Extending the domain admission lifetime or using its closed state as permission is forbidden. Add **only** the following private proposed interfaces after Packet 2 serial handoff:

```python
# app/import_memory_publication.py; token itself carries no mutable public fields
class _TerminalReleaseBinding:
    __slots__ = ('__weakref__',)

def _issue_terminal_release_binding(admission, cursor,
                                    terminal_evidence: FrozenEvidence) -> _TerminalReleaseBinding: ...
def _require_terminal_release_binding(binding, cursor, attempt: ImportAttempt,
                                      *, operation: Literal['finish', 'release']) -> None: ...
def _close_terminal_release_binding(binding) -> None: ...

# app/postgres_import_leases.py
def _finish(self, cursor, attempt, row, status, reason, *, progress=None,
            error_code=None, error_summary=None, delay=None, unstarted=False,
            terminal_release: _TerminalReleaseBinding | None = None): ...

# app/postgres_coordination.py
def release_in_transaction(self, cursor, handle, *,
                           terminal_release: _TerminalReleaseBinding | None = None): ...
```

Issuer storage is private `WeakKeyDictionary[_TerminalReleaseBinding, _ReleaseIssue]`, registered **once**, exact type only, with a strong reference in `_ReleaseIssue` to the original admission (provenance only). Freeze identity, not merely equal values: original C service, its database, `issue.imports`, coordinator and RAG/episode authority services; original `_LivePublication`, `_FixedTerminalWork`, callback `__self__ is work` and `__func__ is _FixedTerminalWork.run`; original admission and its issuing `_AdmissionIssue`; original `ImportAttempt is issue.original_attempt`, source/document key and `AttemptKey`; exact worker, task lease UUID/version and user owner/lease UUID/version; exact cursor object, active `READ COMMITTED` transaction and `txid_current()`; original `pair_sealed` phase/version, immutable `intent_hash` and document `final_expected_hash`; returned `terminal_committed` phase/version, exact terminal slot bytes and SHA-256; and both complete ordered 20-field RAG/episode receipt projections. Receipt order is `generation_id, tenant_id, vector_kind, namespace, index_key, base_revision, index_revision, publication_revision, publication_snapshot_version, expected_count, content_digest, owner, user_lease_token, user_lease_version, task_id, task_lease_token, task_lease_version, created_at, sealed_at, published_at`. The issuer re-reads the header/private slot and receipt rows under the same user-first transaction before accepting a use. A caller cannot supply a dictionary, equal copy, subclass, alternate service/callback, detached proof, recovery claim, or reconstructed attempt to register a binding.

Precise sequence in one caller-owned completion transaction: original fixed callback publishes the two vector heads and domain writes; `append_terminal_in_transaction` returns its `FrozenEvidence`; inside that **successfully completed callback**, check the exact appended bytes/phase/version, issue one release binding, and return it from `_FixedTerminalWork.run`. `complete` checks `type(binding) is _TerminalReleaseBinding` and issuer identity for its exact work, admission, attempt and cursor. It closes the domain admission in its existing `finally` even if callback fails. After callback and admission closure, `complete` calls `_finish(..., 'succeeded', 'succeeded', progress=100, terminal_release=binding)` in its same transaction. Before task SQL, `_finish` checks the binding for `finish`, requires the exact still-live running/committing task and unended audit plus unresolved same-attempt gate and terminal evidence, and marks only its private `finish` state. It admits solely fixed succeeded/succeeded, progress 100, `error_code/error_summary/delay=None`, `unstarted=False`, and exact task/audit/batch bookkeeping. Direct `_finish`, `_progress`, `_touch` or a caller-provided row gains no gate exception. The fixed `try_begin_committing` branch alone may call `_progress('committing', unchanged_progress)` after its existing exact saved-seal/reservation checks; no general progress bypass is added.

After task success, audit success and batch touch, the same `_finish` calls `release_in_transaction(..., terminal_release=binding)`. Release checks private state `finish done / release unused`, original issuer/cursor/transaction and callback identity, exact succeeded task/ended audit, terminal bytes/hash/20-field receipts, unchanged unresolved gate and owner tuple, with a fresh DB clock after blocking locks. It treats the **closed** admission only as stored provenance, never calls `_require_terminal_admission` or revives pair/domain permission, and does not use default `_live(status='running', ended=False)` at the success point. Preserve user-lease release followed by task lease-expiry CAS; any failure rolls back every terminal write. Consume release exactly once; close/invalidate binding on **every** `complete` exit, including callback failure, post-append failure, rollback, commit-response loss and normal return. The registry token remains invalid even if SQL rolled back; no replay, gate resolution or vector/domain permission follows. Ordinary release with `terminal_release=None` refuses an unresolved gate. No lease release is permitted for preterminal fail/cancel through this exception.

`app/import_memory_publication.py` is a **new narrow serial owned source** for the issuer, fixed callback return and vector admission call below. Do not use the original domain admission as the release capability. Retain its `pair_sealed` validation and closure timing.

## B2 — exact vector publication admission and direct helper closure

Current `_publish` in `app/postgres_vector_generations.py:247-290` has no gate argument; C calls it twice from the fixed callback at `app/import_memory_publication.py:543-553`. Proposed signature retains all existing parameters and adds a final keyword-only `admission=None`:

```python
def _publish(self, cursor, scope, authority, generation_id, *, expected_revision,
             expected_index_revision, snapshot_version, expected_count=None,
             content_digest=None, admission=None): ...
```

The original `_run_fixed_terminal` passes its active `_TerminalAdmission` for exactly RAG then episode. `_require_terminal_admission(admission, cursor, 'vector_publish', authority_repository, scope, authority, generation_id, expected_revision, expected_index_revision, snapshot_version, expected_count, content_digest)` gains **only** this operation. It checks exact admission type/active issuer/cursor/txid/original work and original `pair_sealed` snapshot; `authority_repository is` the corresponding issuing `rag_service.authority` or `episode_service.authority`; `scope is` the issuer's planned scope and matches the exact frozen scope key and registered identity; `authority is issue.original_attempt`; `generation_id` is the exact RAG or episode planned UUID; `expected_revision` equals the planned head/base revision, `expected_index_revision` the planned index revision, `snapshot_version` the frozen next History/Memory version; explicit `expected_count` and `content_digest` equal the issued `_SealedGeneration`, its saved sealed slot and current sealed candidate row. `None` count/digest is invalid for this gated path. It checks both original reservations remain `reserved`, exact key/scope/owner tuple, and the unresolved same-attempt gate. After `_publish` obtains blocking head, candidate and reservation locks, repeat these checks and fresh ordinary task/user liveness **before** any head, previous-generation, candidate publication or receipt write. The validator is not callable with a release binding, recovery lease, equal copied token, other service, other cursor or new transaction. A no-admission call takes the ordinary no-gate check after user lock and before mutation. `publish_user`, `complete_import` and coordinator publication callbacks check the gate before invoking any caller callback; their no-gate form remains compatible.

Make `_index(cursor, scope)` read-only (remove `create=True` and its insert); `stage` alone inserts a missing index **after** its existing user lock, exact ordinary no-gate or issued live candidate check, and reservation check, then rechecks identity/index revision. No public/helper call may create an index without stage authority. Every ordinary `stage`, including a UUID with no generation row after gate resolution or physical Qdrant cleanup, refuses **any** UUID present in the permanent global `generation_reservations` table, whether `reserved` or `revoked`. Only the original live issued path may stage its exact still-`reserved` UUID. Ordinary `abandon` refuses a gate; it cannot change a permanent reservation or serve as recovery resolution. Recovery's all-or-none two-ID revoke/abandon uses its separate in-cursor authority transaction. Preserve accepted stage/seal candidate-store admission; do not claim new ownership of candidate-store, B, document, snapshot or Memory source from scanner labels alone. If a verified code defect requires more, root must amend tracked ownership before assignment.

## B3 — one coherent first `_15` slice, then proof resolution

The accepted Packet 2 source still stores private bytes on the header: its `reserve_intent` writes private columns directly at `app/import_publication_evidence.py:661-669`, and `_read_exact_in_cursor` / `_append_slot` read and update them at `:789-819,872-901`. `_15` cannot commit a dropped-column schema with those methods. **Task 7 owns** migration `_15`, `app/import_publication_evidence.py` reader/writer adaptation, `app/import_publication_recovery.py` queue/claim/closure foundation, `app/postgres_import_leases.py`, `app/postgres_coordination.py`, B1's narrow issuer/finish/release plumbing, B2's exact vector callback/admission and `_publish` branch, and evidence/proof/terminal compatibility tests, all after Packet 2 handoff. Task 8 owns subsequent strict proof acknowledgement and exact two-ID revocation against the already adapted schema; it may extend evidence's resolution CAS in the same serial ownership. Task 9 owns remaining direct guards and private adapter. **Task 7 must deliver passing fixed terminal success under `_15` as one coherent tested commit boundary.** No supported intermediate commit may combine dropped private columns with old readers/writers or a gate that breaks the fixed callback.

`_15` keeps a permanent small `import_publication_evidence` header keyed by exact attempt, including identity, schema/format/hash, phase/version, reason and timestamps. One-to-one `import_publication_private_payloads` holds exact original compressed intent, canonical size, document/RAG seal/episode seal/terminal slot bytes and mirrored `payload_phase_version`; header/gate and both permanent global UUID reservations retain their current FKs and immutable identity. Preserve Packet 2's 8 MiB payload, 64 MiB canonical, 256 KiB per slot and 9 MiB aggregate bounds and exact canonical encodings/hashes. For every unresolved transition, lock header under user-first authority, update child first with exact old version and null-to-value slot rule, then CAS header phase/version; require both affected counts one. Mirror metadata-only observation and terminal resolution versions. Deferred checks on **both** tables require one child and equal versions, phase-required/premature slots and size limits at commit; temporary child-first state is permitted inside the transaction. Migration backfills every `_14` byte, slot, hash and phase/version and every unresolved queue row in one PostgreSQL migration transaction; validate key/count/byte/hash equality before dropping old private columns and replacing guards. Accepted proof reads through `_read_exact_in_cursor` and reconstructs the same `FrozenEvidence` value. No production proof-service change is established solely by the split.

Adapt `reserve_intent` so one transaction inserts header, exact child payload, unresolved gate, both reservations **and initial queue row** before returning. Initial `due_at` is no earlier than `greatest(fresh DB task lease expiry, fresh DB user lease expiry)` under the original tuple, not stale handle timestamps. A rollback leaves no queue/header/child/gate/reservation. Migration backfills all unresolved legacy attempts, including already-succeeded terminal response loss, using the greater current DB expiries; a scanner later seeds **missing rows only** and never changes existing `pending`, `claimed`, `manual_hold` or `resolved` dispositions (D2). No Worker/Unknown/scanner call is needed for discovery after a committed new intent. A resolved row with an unresolved gate is contradictory and fails closed.

The same Task 7 migration installs direct `import_objects` pinned source UPDATE/DELETE denial and direct `import_task_attempts` DELETE/identity/owner/token and end-transition guards. Unresolved task/batch/user ancestor cascades remain blocked by header NO ACTION FKs. Audit success trigger follows actual SQL order: vector/domain writes → terminal child/header append → domain admission close → task success → audit success → batch touch → bound user release → task-expiry CAS → commit. It may demand exact terminal header/slot, matching succeeded task/owner tuple **before** audit success; it must not demand already-expired user lease or a Python release flag. Audit `lease_expired` closure instead requires original running/preterminal/unended tuple, both ordinary expiries at fresh DB time, then audit closure before task is changed to non-due `retry_wait`. Do not reuse `_finish` for this closure. Raw DELETE row triggers already hold target rows; implement fail-closed dependencies without reversing the user-first repository lock order, and prove intent/closure races with two actual SQL connections. No private payload cleanup is added: policy TBD, no 30-day fallback, cleanup OFF even after resolution; permanent tombstones and retained task/receipt/generation parents survive.


Existing local SQLite deletion, QA clear, Memory-sync and standalone flows remain as pinned and distributed-disabled; do not port them or enable native runtime/API/bootstrap. Pre-attempt immutable source upload is outside the C publication-before-I/O barrier, while its SQL task/source admission must honor the user gate. Orphan S3 upload cleanup is never outcome evidence.

`app/import_publication_worker_adapter.py` is mandatory, private opt-in and default OFF; its exact composition and outward disposition are:

```python
# app/import_publication_worker_adapter.py, opt-in only
@dataclass(frozen=True)
class FrozenDisposition:
    attempt_key: AttemptKey
    status: Literal['committed_success', 'needs_reconciliation']
    task_id: str
    reconciliation_reason: Literal['none', 'durable_unknown', 'recovery_unavailable']
    # No context, expected pair, token, source key, UUID, hash, SQL error or proof bytes.

class ImportPublicationWorkerAdapter:
    def __init__(self, service: ImportMemoryPublicationService,
                 recovery: PostgresImportPublicationRecoveryRepository): ...
    def run_once(self, live: _LivePublication) -> FrozenDisposition: ...
```

Constructor/run validate `type(live) is _LivePublication`, the registered original issuer `service._require_live_issue(live)`, and matching database/recovery identity before calling **only** `service._execute_live_publication(live)` once. Do not accept or store a user callback, `Callable`, copied handle, reconstructed `_PairContext/_Expected`, token or detached result as execution authority. On a normal returned `DetachedImportMemoryPublication`, check exact proof key and freshly committed succeeded task/terminal result, then emit `FrozenDisposition(key,'committed_success',task_id,'none')`; a raised exception or standalone proof without fresh authoritative terminal/task validation never becomes success. On exact `DurablePublicationUnknown(attempt_key, phase)` from the original service, check key/phase selector only, schedule the already seeded queue by exact CAS and trigger/read detached proof through recovery. Return `needs_reconciliation` with `durable_unknown`; if scheduling/read fails, use `recovery_unavailable` and leave seed queue for restart. Stable outward disposition contains no private phase, source, hashes, candidate IDs or SQL diagnostic. Never call `_execute_live_publication` again, reuse the consumed handle, invoke ordinary `fail`/retry, or wire native Worker/API/bootstrap. A non-evidence exception may follow old Worker classification **only after** a fresh authoritative no-gate/no-evidence check; ambiguous evidence stays held.


The bound owned test set is new `tests/integration/test_import_publication_recovery.py` and `tests/app/test_import_publication_worker_adapter.py` plus targeted extensions of `tests/integration/test_import_publication_evidence.py`, `tests/integration/test_import_publication_proof.py`, `tests/integration/test_postgres_import_leases.py`, `tests/integration/test_postgres_coordination.py`, `tests/integration/test_postgres_vector_generations.py`, `tests/app/test_postgres_vector_generations.py`, `tests/integration/test_postgres_imports.py`, `tests/integration/test_postgres_import_artifacts.py`, `tests/integration/test_import_memory_fault_matrix.py`, `tests/integration/test_postgres_snapshots.py`, `tests/integration/test_postgres_memory_documents.py`, and `tests/test_import_repository.py`. Packet 2 owns its evidence/proof and other shared tests until independently accepted; Packet 3 receives the necessary `_15` fixture adaptations only at serial handoff, with no concurrent edits. In particular, any raw source/audit-tamper fixture that changes behavior under `_15` denial must preserve negative coverage by using a legal fixture setup or asserting the new SQL denial, never silently drop the mismatch test. The accepted hashes/paths and exact runnable commands are bound in this packet; later ownership amendments require explicit review. Test direct PostgreSQL construction and caller-owned cursors, not only guarded service facades.

The adapter consumes only the accepted durable Unknown key/phase and original service/live identity. Ordinary composition is unwired. Only a normally returned detached terminal result followed by fresh authoritative committed validation yields committed_success; exception/standalone proof cannot do so without the declared recovery acknowledgement path. Tests have explicit owned versus verification-only boundaries below.

## Precise `_15` SQL/trigger contract — independently reviewed design

The following complete design contract is the serial implementation input,
not an already-applied migration or executable full migration script. Its exact
DDL, transition predicates, lock restrictions, deferred history rule and
required SQL schedules supplement the interfaces above. No unresolved schema
choice is delegated to an implementation worker. If source reality contradicts
these rules, report the exact conflict rather than inventing a bypass.

Sol v3 raw SHA256 `86824AE3E8C182360092A31AD38C0916337F18232A806E1B55C547B3A0E5946B`;
Astra v3 independent design review SHA256 `4A223815FB3370E903151211EF1B9E720FEE5C35A3DB9FE301A8DF79716326EB`.
Earlier v1 `B7129056...8573` was CHANGES REQUIRED (reverse FK locks, renewal
versioning, abandoned slot history). V2 `D9DA5D47...718E` closed those three but
omitted the permanent manual/resolved queue guard; v3 restores it. Their reports
remain historical and are not assigned contracts. The independent review does
not claim SQL, tests or product implementation ran.

### Existing names and lock foundation

`_01` has `users(id)`, `import_batches(id,user_id)` and
`import_tasks(id,batch_id,user_id,document_id,status,stage,progress,
next_attempt_at,claimed_by,lease_token,lease_version,user_lease_token,
user_lease_version,lease_expires_at,...)`. Task status is limited to
`queued|running|retry_wait|succeeded|failed|cancelled`.
`_07` has `user_mutation_leases(user_id,owner,lease_token,lease_version,
lease_expires_at)`; `_08` has `import_objects(task_id PRIMARY KEY,user_id,
bucket,object_key,version_id,sha256,size_bytes)` with task `ON DELETE CASCADE`;
`_09` has `import_task_attempts(task_id,lease_version)` with exact worker,
task/user tokens and versions, end fields and task `ON DELETE CASCADE`.
`_10/_11` retain immutable `vector_generations` and published receipts;
`_12/_13` retain document references and History witnesses.
`_14` evidence has PK `(user_id,task_id,task_lease_version)`, `NO ACTION`
user/task FKs, and is the FK parent of one gate and two globally unique
generation reservations. Keep those FKs and the existing reservation DELETE
denial; do not detach the tombstones. Header DELETE remains forbidden.

Repository writers lock `users` then `user_mutation_leases`, task, exact audit,
header/gate, recovery lease, queue, then RAG and episode reservations/generations.
Before inserting the first header, `reserve_intent` additionally locks the
exact audit and pinned `import_objects` row `FOR SHARE`, in that order, and
checks the full original tuple/source. `FOR SHARE` conflicts with a raw
`DELETE` and with a non-key `UPDATE`; `FOR KEY SHARE` is insufficient for
source mutation. Direct source/audit row triggers **never acquire the user
lock**: their triggering UPDATE/DELETE already holds a row lock. Their
dependency query is a fail-closed read, so the intent's `FOR SHARE` lock and
subsequent insert serialize the two directions without a reverse-lock cycle.
If raw deletion wins first, intent observes missing source/audit and aborts;
if intent wins first, deletion waits and its trigger observes the committed
header. Prove both schedules using two SQL connections and barriers. Header
FKs, not these row triggers, block task/batch/user cascades. An unresolved
header is never removed, so those NO ACTION links remain durable.

### `_15` header and private payload DDL

Keep on `import_publication_evidence`: `user_id,task_id,
task_lease_version,document_id,worker_id,task_lease_token,user_lease_token,
user_lease_version,schema_version,intent_format,intent_hash,phase,
phase_version,observation_reason,created_at,updated_at`, with their `_14`
types/checks, immutable identity, legal phase transitions and monotone
version. Move **only** `intent_payload,canonical_bytes,document_slot,
rag_sealed_slot,episode_sealed_slot,terminal_slot` to this private child:

```sql
CREATE TABLE import_publication_private_payloads (
  user_id text NOT NULL,
  task_id text NOT NULL,
  task_lease_version bigint NOT NULL CHECK (task_lease_version > 0),
  intent_payload bytea NOT NULL CHECK (octet_length(intent_payload) <= 8388608),
  canonical_bytes bigint NOT NULL CHECK (canonical_bytes BETWEEN 1 AND 67108864),
  document_slot bytea CHECK (octet_length(document_slot) <= 262144),
  rag_sealed_slot bytea CHECK (octet_length(rag_sealed_slot) <= 262144),
  episode_sealed_slot bytea CHECK (octet_length(episode_sealed_slot) <= 262144),
  terminal_slot bytea CHECK (octet_length(terminal_slot) <= 262144),
  payload_phase_version bigint NOT NULL CHECK (payload_phase_version > 0),
  PRIMARY KEY (user_id,task_id,task_lease_version),
  FOREIGN KEY (user_id,task_id,task_lease_version)
    REFERENCES import_publication_evidence(user_id,task_id,task_lease_version)
    ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
  CHECK (octet_length(intent_payload)
    + coalesce(octet_length(document_slot),0)
    + coalesce(octet_length(rag_sealed_slot),0)
    + coalesce(octet_length(episode_sealed_slot),0)
    + coalesce(octet_length(terminal_slot),0) <= 9437184)
);
```

Replace `_14` `import_publication_evidence_guard` only when the child,
backfill, deferred checks and adapted application image are installed. The
new BEFORE header guard compares all retained immutable fields exactly as
`_14`, denies DELETE, requires INSERT `phase='intent'`, version 1 and null
reason, enforces `new.phase_version=old.phase_version+1`, old terminal
immutability, and exactly `_14` phase edges:
`intent→document_verified|abandoned`,
`document_verified→pair_sealed|abandoned`,
`pair_sealed→terminal_committed|abandoned`,
`terminal_committed→proved_succeeded` (same-phase observation **or a RAG
seal append while `document_verified`** advances version without changing
phase). It sets `updated_at=clock_timestamp()`.
The child BEFORE guard denies DELETE; INSERT must use version 1 and no slots;
UPDATE keeps key, intent bytes and canonical size identical, advances
`payload_phase_version` exactly one, keeps each nonnull slot byte-identical,
and permits only null-to-value slot additions. The `abandoned`,
`proved_succeeded`, `manual_hold` and `unknown` metadata transitions still
advance child version even when bytes stay equal. The child BEFORE guard
performs no `FOR UPDATE` on header/users: raw child UPDATE owns its row first
and a reverse header lock would deadlock with repository header-first writes.
It may read the header without row locks; final validity is checked below.
Ordinary cleanup is OFF; these guards confer no DELETE privilege after
resolution.

Install **both** `AFTER INSERT OR UPDATE CONSTRAINT TRIGGER ... DEFERRABLE
INITIALLY DEFERRED FOR EACH ROW`: `import_publication_header_complete_15`
on the header and `import_publication_payload_complete_15` on the child.
Both call one `import_publication_complete_15(user_id,task_id,version)` that
loads final header and child, requires exactly one of each, matching key and
`phase_version=payload_phase_version`, all bounds above, and the same `_14`
slot shape: `intent` has no slots; `document_verified` has document but no
episode/terminal and may have its RAG seal; `pair_sealed` has document and
both seals but no terminal;
`terminal_committed|proved_succeeded` has all four; `abandoned` preserves
whatever preterminal slots existed and cannot gain one at abandonment. For
`abandoned`, terminal is null; absence of document implies both seals null;
episode seal implies RAG seal and document; a RAG-only seal is permitted
from a partially sealed `document_verified` attempt.
The SQL function checks phase shape against final rows. A separate
`import_publication_abandon_history_15` deferred `AFTER UPDATE` constraint
trigger on **child** uses event `OLD` and `NEW` slot tuples, then makes a
plain, unlocked read of the current header. It requires a header to exist
and `header.phase_version >= NEW.payload_phase_version` whenever fired,
including `SET CONSTRAINTS import_publication_abandon_history_15 IMMEDIATE`.
If the current header is `abandoned`, its version equals the event NEW
version, and any slot changed in that child event, it raises. A prior legal
append v2 followed by no-slot-change abandonment v3 in one transaction is
allowed; append plus abandonment both at v2 is denied. Early consumption of
the child event while header is still v1 is denied rather than silently
marking the event checked. Old `abandoned|proved_succeeded` header updates
remain impossible, so the forbidden event cannot be hidden by a later phase.
No child trigger locks the header; repository writers lock header first.
A missing child fails at commit even for a header
INSERT or metadata-only header UPDATE. A missing header fails child INSERT or
UPDATE. A direct child-only mutation fails version equality. Use a final-row
lookup, not the event's `NEW` image, because child-first is temporarily
inconsistent inside the transaction. Do not silently tolerate a null child.

The history trigger's operative PL/pgSQL predicate is:

```sql
SELECT phase, phase_version INTO final_phase, final_version
  FROM import_publication_evidence
  WHERE user_id=NEW.user_id AND task_id=NEW.task_id
    AND task_lease_version=NEW.task_lease_version;
IF NOT FOUND OR final_version < NEW.payload_phase_version THEN
  RAISE EXCEPTION 'Child event lacks paired header version';
END IF;
IF final_phase='abandoned' AND final_version=NEW.payload_phase_version
   AND (NEW.document_slot,NEW.rag_sealed_slot,
        NEW.episode_sealed_slot,NEW.terminal_slot)
       IS DISTINCT FROM
       (OLD.document_slot,OLD.rag_sealed_slot,
        OLD.episode_sealed_slot,OLD.terminal_slot) THEN
  RAISE EXCEPTION 'Abandon cannot append publication proof';
END IF;
```

Every evidence transition takes header `FOR UPDATE` under user-first locks,
updates child first with `WHERE payload_phase_version=:old_version` and
slot-null/old-bytes predicates, then header with `WHERE phase=:old_phase AND
phase_version=:old_version AND intent_hash=:old_hash`; both affected counts
must be exactly one. The same rule covers observation and resolution without
slot changes. `_read_exact_in_cursor` joins the child and fails closed if
missing, decodes the identical compressed bytes/hash and reconstructs the
accepted `FrozenEvidence`; detached proof needs no new service API.

Migration order inside one transaction, with application writers quiesced:
create child and copy **all** `_14`
rows, including terminal/resolved rows, with byte-for-byte original fields
and `payload_phase_version=phase_version`; assert exact key count, key set,
every `bytea` equality, canonical size, hash and phase/version equality;
check each copied row through the accepted codec and explicit final-row
`import_publication_complete_15` call, since installing deferred triggers
does not retroactively enqueue checks for copied rows. Check exact gate and
reservation cardinality/disposition, including succeeded/terminal
response-loss rows. Create the new queue/leases/issuance/schedule tables,
backfill queue and schedule; install new guards/deferred checks; drop old
header private columns only after validation.
Keep original gate/reservation/user/task FKs and indexes. Downgrade raises;
it cannot discard retained proof or tombstones. Migration must fail and roll
back as one unit on corrupt/missing original data, queue seed conflict or
constraint failure. A fresh database gets the same tables and reserve-intent
seed behavior without a separate scanner dependency.

### Queue and permanent recovery-token DDL

```sql
CREATE TABLE import_publication_recovery_queue (
  user_id text NOT NULL, task_id text NOT NULL,
  task_lease_version bigint NOT NULL CHECK (task_lease_version > 0),
  queued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  due_at timestamptz,
  state text NOT NULL CHECK (state IN ('pending','claimed','manual_hold','resolved')),
  queue_version bigint NOT NULL CHECK (queue_version > 0),
  claim_token uuid, claim_expires_at timestamptz, last_claimed_at timestamptz,
  reason_code text NOT NULL CHECK (reason_code IN
    ('seed','unknown','ordinary_live','transient','manual_hold','proved_succeeded','abandoned')),
  transient_count integer NOT NULL DEFAULT 0 CHECK (transient_count BETWEEN 0 AND 7),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (user_id,task_id,task_lease_version),
  FOREIGN KEY (user_id,task_id,task_lease_version)
    REFERENCES import_publication_evidence(user_id,task_id,task_lease_version),
  CHECK ((state='pending' AND due_at IS NOT NULL AND claim_token IS NULL
           AND claim_expires_at IS NULL)
      OR (state='claimed' AND due_at IS NULL AND claim_token IS NOT NULL
           AND claim_expires_at IS NOT NULL AND last_claimed_at IS NOT NULL)
      OR (state IN ('manual_hold','resolved') AND due_at IS NULL
           AND claim_token IS NULL AND claim_expires_at IS NULL))
);
CREATE INDEX import_publication_recovery_due_15
  ON import_publication_recovery_queue(due_at,queued_at,user_id,task_id)
  WHERE state='pending';
CREATE INDEX import_publication_recovery_expired_claim_15
  ON import_publication_recovery_queue(claim_expires_at,queued_at,user_id,task_id)
  WHERE state='claimed';
CREATE TABLE import_publication_recovery_leases (
  user_id text NOT NULL, task_id text NOT NULL,
  task_lease_version bigint NOT NULL CHECK (task_lease_version > 0),
  owner text NOT NULL CHECK (length(btrim(owner)) > 0),
  token uuid NOT NULL UNIQUE, version bigint NOT NULL CHECK (version > 0),
  heartbeat_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
  queue_claim_token uuid NOT NULL, queue_version bigint NOT NULL CHECK (queue_version > 0),
  PRIMARY KEY (user_id,task_id,task_lease_version),
  FOREIGN KEY (user_id,task_id,task_lease_version)
    REFERENCES import_publication_evidence(user_id,task_id,task_lease_version)
);
CREATE TABLE import_publication_recovery_token_issuance (
  token uuid PRIMARY KEY,
  kind text NOT NULL CHECK (kind IN ('queue','recovery')),
  user_id text NOT NULL, task_id text NOT NULL,
  task_lease_version bigint NOT NULL CHECK (task_lease_version > 0),
  issued_version bigint NOT NULL CHECK (issued_version > 0),
  issued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  FOREIGN KEY (user_id,task_id,task_lease_version)
    REFERENCES import_publication_recovery_queue(user_id,task_id,task_lease_version)
    ON DELETE NO ACTION,
  UNIQUE (kind,user_id,task_id,task_lease_version,issued_version)
);
CREATE TABLE import_publication_recovery_schedule (
  user_id text PRIMARY KEY REFERENCES users(id),
  last_claimed_at timestamptz NOT NULL
);
```

BEFORE UPDATE/DELETE guards on queue and lease deny DELETE and immutable-key
changes. Queue **new claim or state transition** requires version exactly
`old+1`; a new claim requires a fresh token issued for that exact new queue
version, while transitions **into** pending/manual/resolved clear the active claim
token and do not create issuance. The ordinary BEFORE UPDATE guard first
rejects **every** UPDATE when `OLD.state IN ('manual_hold','resolved')`,
including no-op updates. Its only admitted ordinary state edges are
`pending→claimed`, `pending→pending` for an exact due/reason CAS such as
Unknown scheduling, `claimed→pending` for bounded deferral, transient retry
or expired-capture reset, and `claimed→manual_hold|resolved` for a reviewed
resolution. `claimed→claimed` with a **new** claim token/version is allowed
only when the old capture has expired and a fresh permanent token is issued;
the separate pure-renew exception below is the only version-preserving edge.
No ordinary claim, scanner, retry, reason change, token replacement or
version increment may reopen `manual_hold` or `resolved`; operator
reinspection would require a separately approved contract and is not
implemented by a flag or method name. A distinct queue **pure-renew** branch
allows `claimed→claimed` with the same attempt key, queue version, token,
`queued_at`, `last_claimed_at`, `due_at=NULL`, `reason_code`, `transient_count`
and state; only `claim_expires_at` and `updated_at` may change. Its old
`claim_expires_at` must be later than one fresh clock after all locks and its
new expiry must be strictly later than the old expiry. The matching recovery
lease must have the exact same capture and remain live; the paired renewal
operation below is the only repository path using this branch. It cannot
alter state, reason, count, owner or token, or revive an expired capture.
The lease **new grant/takeover** requires version 1 on first INSERT, then
`old.version+1` on each later takeover, a fresh issued token and an expired
old lease at a fresh DB clock; it captures the final queue token/version.
The lease **pure-renew** keeps owner/token/version/queue capture and changes
only `heartbeat_at,expires_at`, requiring old expiry live and new expiry
strictly later. Closing a lease is a separate CAS setting
`expires_at=LEAST(old.expires_at,clock_timestamp())`, preserving all identity.
The issuance BEFORE UPDATE/DELETE guard always raises; its INSERT is only
after the transaction owns the queue row. A deferred final-row validator
uses **plain SELECTs without row locks** to match each newly issued queue
or recovery token to the exact key, kind, new grant version and captured
token; it does not lock header or user. Issuance is permanent even after
queue disposition changes. Global `token` PK prevents reuse across queue
and recovery kinds, users and time. At commit each `claimed` queue token and
each recovery lease token must have its matching issued row; pending,
manual_hold and resolved queues require no current claim token and no
impossible NULL-token issuance. Seed queue version is 1 without issuance;
first claim is version 2, later new claims increment from the preceding
queue version. First recovery lease grant is version 1; subsequent new
grants/takeovers increment its prior version. Pure renew and closure issue
no UUID. No `ON DELETE CASCADE` applies to these tables.

The permanent dependency chain is `token issuance → permanent recovery
queue → permanent evidence header → retained task/user`; the queue's FK to
header remains. A queue-only claimant locks the queue before token INSERT;
the issuance FK now obtains `KEY SHARE` on the **same queue row in its own
transaction**, never a header/user parent. Recovery grant inserts its token
only after the user-first authority transaction has locked its final queue
row. Queue-only triggers and issuance validators must not acquire row locks
on users, header, source, audit or recovery lease. A plain provisional read
of recovery lease liveness is allowed; final queue CAS decides races.

`reserve_intent` inserts header, child, gate, **both** reservations and queue
row in one transaction, after verifying current locked task/audit/source.
Seed `state='pending',queue_version=1,reason_code='seed'` and
`due_at=GREATEST(t.lease_expires_at,u.lease_expires_at)` read from the fresh
exact task/user rows; missing or mismatched expiry fails. Backfill one queue
row for every existing unresolved gate/header, including a succeeded task
whose terminal commit response was lost; use the exact retained tuple and
current DB expiries. A resolved header/gate does not seed. Contradictory
gate/phase disposition fails migration. Scanner uses
`INSERT ... ON CONFLICT (user_id,task_id,task_lease_version) DO NOTHING`
**only** for a missing row; it never rewrites pending, claimed, manual or
resolved state (D2). `reserve_intent` also INSERTs the user's
`import_publication_recovery_schedule` row (or checks/updates an existing
one) while it already holds the user-first locks, **before** any queue-only
claim can see committed intent. Migration backfills one schedule row for
every unresolved user before enabling claims. A schedule row has immutable
`user_id` and cannot be DELETEd by claim code. No queue-only transaction may
INSERT or UPSERT this users-FK table while it holds a queue lock.

Queue claim: in a short transaction rank each user's oldest due row by
`(queued_at,task_id,task_lease_version)`, rotate candidate users by persisted
`last_claimed_at NULLS FIRST,user_id`, and attempt one row
`FOR UPDATE SKIP LOCKED`; treat an expired `claimed` capture as eligible for
a new claim after its exact expiry (the old token stays permanently issued),
as well as a due `pending` row. The candidate predicate is
`(state='pending' AND due_at<=clock_timestamp()) OR
(state='claimed' AND claim_expires_at<=clock_timestamp())`; it must not rely
on `due_at` for expired claimed rows because that field is NULL. Recheck
eligibility, unresolved gate, phase and no
committed live recovery lease with fresh clock. Try at most three candidates,
with no fixed leading prefix that hides later users. CAS queue
`pending|expired claimed→claimed`, version `old+1`, fresh unique token inserted into issuance,
expiry `clock_timestamp()+:seconds*interval '1 second'`, due null, rotate
schedule by **UPDATE of the existing row with affected count one**, then
**commit before user lock**. Missing schedule aborts the claim transaction;
it must not be repaired while queue locked. Strict `type(seconds) is int`,
`1..86400`, default 60. The queue-only live-lease read is provisional.
Second authority transaction takes the full user-first lock order, final
queue row last, and validates exact captured token/version/expiry, header
phase/version, gate and fresh clock. It grants/takes over recovery lease only
if absent/expired, inserts a new permanent recovery token and increments
version; a competing captured claim loses at final queue CAS. It cannot
replace a committed live recovery lease. Same-holder renewal preserves both
tokens/versions and rejects if either expiry elapsed. For renewal, hold
user → ordinary user lease → task → audit → header/gate → recovery lease →
**final queue**, capture one `clock_timestamp()` after the final wait, and
compare **both old expiry values** with that clock before either UPDATE.
Require exact claim token/version in queue, exact owner/recovery
token/version/captured queue tuple in lease, and matching permanent issuance
rows for each token. Choose one new expiry per row strictly greater than its
own old expiry (for example `GREATEST(old_expiry,now+:seconds)+interval '1
microsecond'`); update the two expiry/heartbeat fields with exact old-value
CAS and require both affected counts one. The transaction rolls back both
on failure after either write. It never uses a newly extended first lease
to establish that its **old** authority was live and never inserts issuance.
Explicit Unknown may
make queue due early for **read-only proof** by exact CAS; it never enables
preterminal revocation while either original lease lives. D1 reschedules
identity-exact preterminal original to `GREATEST(current task expiry,
current user expiry)+interval '1 second'`, leaving task/evidence/gate and
transient count unchanged. A recorded transient uses count 1..6 and
`LEAST(5*power(2,count-1),300)` seconds; seventh goes non-due
`manual_hold`. Failed/unavailable CAS leaves capture to expire; no inferred
outcome. Cleanup remains OFF without a separately approved retention policy.

### Direct SQL dependency guards

Install `BEFORE UPDATE OR DELETE` on `import_objects` and
`import_task_attempts` in Task 7. For source UPDATE, if any **unresolved**
header exists for `(old.user_id,old.task_id)`, reject a change to
`(task_id,user_id,bucket,object_key,version_id,sha256,size_bytes)`; source
DELETE with the same predicate always raises. Do not add a user-row lock to
this trigger. Direct audit DELETE rejects if the exact
`(old.user_id,old.task_id,old.lease_version)` header is unresolved. Audit
UPDATE always keeps `(task_id,user_id,lease_version,worker_id,lease_token,
user_lease_token,user_lease_version,started_at)` immutable. While unresolved,
permit heartbeat/last-stage changes only when the exact same audit remains
unended and the repository's user-first live-attempt check authorizes them;
the DB trigger additionally denies any identity/end-field escape.

For unresolved audit `ended_at` transition, require old pair null and new
pair nonnull exactly once; deny all rewrites. `end_reason='succeeded'` requires
matching header `phase='terminal_committed'` with nonnull exact child
`terminal_slot`, current task already `status='succeeded'`, matching
worker/task token/version/user token/version, same unresolved gate and a
task finish/terminal tuple consistent with `_finish`. **Do not require user
lease expiration:** actual fixed success order is terminal append → task
success → audit success → batch touch → bound user release → task-expiry CAS.
`end_reason='lease_expired'` requires header preterminal
`intent|document_verified|pair_sealed`, current task still `running`,
matching old audit and task ownership tuple, matching user lease tuple,
`t.lease_expires_at<=clock_timestamp()` **and**
`u.lease_expires_at<=clock_timestamp()`, unresolved same gate, and no
terminal slot. Actual expiry closure order is audit while task `running` →
task to non-due `retry_wait` → exact ordinary lease expiry; it never calls
ordinary `_finish`. The trigger must not require the later task status at
the audit step. Other end reasons under an unresolved header raise. A
resolved header does not create an audit-delete or task cleanup privilege;
existing retention/FKs still apply. A raw update cannot claim repository
authority merely by a SQL setting, Python method name, or trusted flag.

The trigger implementation must use the following **final-row predicates**
(the transaction's own earlier writes are visible). These are SQL conditions,
not a claim that the calling cursor itself was authenticated. Name the
functions `import_object_dependency_guard_15` and
`import_attempt_dependency_guard_15`, and reject unless each count is exactly
one where equality is required:

```sql
-- Source BEFORE UPDATE/DELETE, against OLD.task_id and OLD.user_id.
EXISTS (
  SELECT 1 FROM import_publication_evidence e
  JOIN user_publication_gates g USING
    (user_id,task_id,task_lease_version)
  WHERE e.user_id=OLD.user_id AND e.task_id=OLD.task_id
    AND g.status='unresolved'
)
-- DELETE: if EXISTS, RAISE. UPDATE: if EXISTS and pinned tuple changes,
-- RAISE. The comparison is IS DISTINCT FROM over every pinned field.

-- Audit exact dependency, not any other attempt for the same task.
EXISTS (
  SELECT 1 FROM import_publication_evidence e
  JOIN user_publication_gates g USING
    (user_id,task_id,task_lease_version)
  WHERE e.user_id=OLD.user_id AND e.task_id=OLD.task_id
    AND e.task_lease_version=OLD.lease_version
    AND g.status='unresolved'
)
-- DELETE: if EXISTS, RAISE. UPDATE of identity/owner tuple: always RAISE.

-- Shared audit end-transition identity predicate, joined on exact _09 key.
t.id=OLD.task_id AND t.user_id=OLD.user_id
AND t.lease_version=OLD.lease_version
AND t.claimed_by=OLD.worker_id AND t.lease_token=OLD.lease_token
AND t.user_lease_token=OLD.user_lease_token
AND t.user_lease_version=OLD.user_lease_version
AND u.user_id=t.user_id AND u.owner=OLD.worker_id
AND u.lease_token=OLD.user_lease_token
AND u.lease_version=OLD.user_lease_version
AND e.user_id=t.user_id AND e.task_id=t.id
AND e.task_lease_version=t.lease_version
AND e.worker_id=OLD.worker_id
AND e.task_lease_token=OLD.lease_token
AND e.user_lease_token=OLD.user_lease_token
AND e.user_lease_version=OLD.user_lease_version
AND g.user_id=e.user_id AND g.task_id=e.task_id
AND g.task_lease_version=e.task_lease_version
AND g.status='unresolved'

-- Add for success, before audit UPDATE in the accepted _finish sequence:
AND t.status='succeeded' AND t.stage='succeeded' AND t.progress=100
AND e.phase='terminal_committed' AND p.terminal_slot IS NOT NULL
AND p.user_id=e.user_id AND p.task_id=e.task_id
AND p.task_lease_version=e.task_lease_version
AND p.payload_phase_version=e.phase_version
-- No user-lease-expired predicate: release happens after this audit UPDATE.

-- Add instead for lease_expired, before task hold UPDATE:
AND t.status='running'
AND e.phase IN ('intent','document_verified','pair_sealed')
AND p.user_id=e.user_id AND p.task_id=e.task_id
AND p.task_lease_version=e.task_lease_version
AND p.payload_phase_version=e.phase_version
AND p.terminal_slot IS NULL
AND t.lease_expires_at<=clock_timestamp()
AND u.lease_expires_at<=clock_timestamp()
-- The OLD audit must be unended and NEW must change only end fields,
-- preserving worker/owner/tokens; no successful receipt may exist for
-- either exact reserved candidate.
```

The final receipt-negative is an exact `NOT EXISTS` for either reservation
joined to `vector_generations` on `generation_id`, matching scope and frozen
owner/task/user tuple, with state `published|retired` or nonnull
`publication_revision/publication_snapshot_version/published_at`. It is an
extra denial condition, never evidence of rollback. Source pin equality and
receipt completeness are still checked by repository proof code. The audit
trigger rejects null→nonnull end fields if `NEW.end_reason` is outside the
two permitted values under an unresolved gate, rejects unequal
`NEW.ended_at`/`NEW.end_reason` nullness, and rejects any subsequent change
to an ended pair. Pure `heartbeat_at`/`last_stage` updates are allowed only
while old/new audit end fields remain null and exact identity is unchanged;
repository liveness remains mandatory for the operation.

The trigger's source/audit existence query alone cannot prove a full user
lock sequence for a raw writer. The SQL predicates and intent's row locks
provide fail-closed dependency protection; repositories still enforce
user-first serialization for admitted operations. In particular, never put
`SELECT users ... FOR UPDATE` inside a raw row trigger and never rely on an
unlocked stale `NOT EXISTS` alone to resolve the concurrent intent race.
Define the source and audit trigger functions as ordinary/default
`VOLATILE` PL/pgSQL functions; do not mark their dependency reads `STABLE`.

### Recovery resolution and helper permissions

Task 8 first obtains detached exact proof without external writes. `None`
is ambiguous: reload header/child and independently check task, audit,
terminal and receipts before any abandon. With full same-attempt proof and
already succeeded task, under the full locks CAS child version then header
`terminal_committed→proved_succeeded`, resolve same gate, queue and recovery
lease in one transaction. Neither domain nor vector publication is called.
For preterminal abandon require both original ordinary leases expired,
exact current or already-held audit/task tuple, no success/terminal slot or
publication receipt; close ordinary tuple if needed. Lock RAG then episode
reservations and generation rows. Both reservations must match frozen IDs,
kind, scope and owner and still be `reserved`; revoke **both** even when a
generation row is absent. Present rows must exactly match original owner,
task/user token/version, index/base and be `staging|sealed`; only these may
transition to `abandoned`. Any published/retired/already-abandoned/wrong-owner
row, mismatched proof or partial SQL count goes to non-due manual hold with
gate unresolved. In the successful single transaction CAS child/header to
`abandoned`, resolve gate, queue and recovery lease, then set held task
`retry_wait.next_attempt_at=now` and clear only the reconciliation error.
Late Qdrant writes cannot publish a revoked UUID; ordinary `stage` rejects
every reservation UUID globally even if no generation row remains.

Task 7 must simultaneously adapt evidence readers/writers, seed and claim
queue, expiry closure, B1 one-use terminal release binding and B2 exact
`vector_publish` admission. `_progress('committing')`, `_touch`, `_finish`,
release and task-expiry CAS get only their named bounded live permissions.
The terminal binding admits fixed `succeeded/succeeded`, progress 100,
task-before-audit ordering and one user release; it does not reopen domain
permission. `_publish` checks original active admission, matching scope,
count/digest/revision, both reserved IDs and fresh liveness after blocking
locks. Task 8 adds proof resolution; Task 9 closes remaining direct import,
coordinator, witness and other remaining direct mutators and the default-OFF
adapter. **Task 7 B2 makes `_index` read-only and puts guarded index INSERT
inside the admitted `stage` path**; Task 9 checks remaining direct-entry
coverage but does not defer this change. No supported Task 7 commit may have dropped header columns with
old `reserve_intent`, `_read_exact_in_cursor` or `_append_slot`, or a SQL
guard that blocks the fixed terminal success. Only admitted `stage` may
insert an index. Recovery lease and ordinary
task/user lease have disjoint powers. `needs_reconciliation` is a sanitized
projection, never a seventh persisted task status. No age cutoff, 30-day
default, payload cleanup, scanner disposition overwrite, Worker bootstrap
or runtime cutover is authorized by this contract.

### Required SQL schedules and binding caveat

Two real-connection barrier tests must cover: (1) source/audit raw
UPDATE/DELETE starts before intent and wins, so intent aborts; reverse
order waits then rejects the raw change, with no deadlock; (2) live stage
versus recovery revoke in both orders, including absent generation rows,
with original/late owner rejection and persistent UUID tombstones after
gate resolution and optional physical Qdrant cleanup. Also test child-only,
header-only and partial migration rollback; same-version slot append plus
abandon denied, legal earlier append then later abandon allowed,
`SET CONSTRAINTS` on the history trigger before its paired header CAS denied;
terminal success and lost
response; task-success-before-audit and expiry-audit-before-task; token ABA,
queue capture/version/expiry, valid dual pure-renew identity/no-ledger-growth,
one-sided expiry and failed-second-write rollback, D1/D2, six
transients/seventh hold. Add two-connection schedules for authority holding
header then waiting on queue versus queue-only claim plus issuance, first
schedule seed under held user authority versus missing-schedule claim, and
raw child UPDATE versus repository header-first update. Verify absence of
reverse-lock deadlocks and exact final capture/version winner in each.
These are
future tests, not results of this design note.

**Review point:** An SQL trigger cannot authenticate a Python-issued live
capability. Consequently the audit/source triggers are integrity fences,
while repository methods must enforce exact issuer and lock provenance.
The final tracked packet must preserve this division; treating a session
setting or method-name flag as DB authority would weaken it.

### Required two-connection SQL schedules and acceptance boundary

1. **Old stage wins lock:** Connection A begins original `stage` and pauses after acquiring the user lock, before generation insert. Connection B starts recovery on the same attempt and blocks on user. Release A to commit or roll back, then B reacquires/rechecks fresh database clock and exact reservation/generation state. If the original tuple still lives, B defers without revocation. Once that tuple expires, B sees A's committed exact `staging|sealed` row or its rollback/absent row and may perform the all-or-none abandon. In both branches no old stage remains publishable after successful abandon. Separately hold A long enough for its original lease to expire before its post-lock check and verify A refuses its own insert/commit.
2. **Revoke absent ID wins lock:** Pause Connection A's old `stage` **before** it acquires the user lock. Connection B locks user first and atomically revokes **both** reservations after the old tuple expires while the target generation row is absent, then resolves gate. Release A; a new live owner also tries `stage` with each old UUID. Both fail on the permanent reservation/attempt/owner check, including after optional physical Qdrant cleanup. A late external upsert cannot turn the revoked ID into a publishable receipt. Run this with two actual SQL connections and barriers, not sleeps or mocked ordering.

Additional disposable-service acceptance must cover proof-first at every approved crash point, task already succeeded with lost response, terminal no full proof, exact old/new immutable receipt fields, source/audit direct DELETE and task/batch/user cascade negatives while unresolved, parent/receipt retention after resolved payload split, absent-row and one-invalid-row all-or-none revocation, lease ABA/takeover, queue fairness under an early permanent hold and a busy user, another user's progress, no-policy cleanup no-op, and default-OFF runtime. No migration, product test or runtime acceptance follows from this design READY; recorded disposable service preparation remains scoped separately.

Finite targeted cases must additionally prove: (a) an expired queue capture is replaced by the short claimant **between** an authority transaction's first read and its final queue lock, once while blocked on user and once while blocked on the recovery-lease row; old token/version grants no lease, renewal, hold or resolution; (b) the queue-only claimant reads committed live recovery lease and cannot replace it, while a race with an uncommitted grant is decided by the final queue lock; (c) direct queue recovery immediately after intent commit, with no intervening `recover_expired`, closes the exact audit once and ordinary tuple, for absent and sealed generation rows, then releases the task for a fresh claim only in the all-or-none abandonment commit; (d) scanner-first closure and direct closure yield the same held row, and succeeded task/audit bytes remain unchanged on ack; (e) in-cursor ack/abandon invoked inside a caller transaction do not nest a transaction and roll back with the caller, while standalone wrappers delegate; (f) original gated heartbeat remains live with a fresh tuple, stale/wrong tuple and generic coordinator heartbeat/release deny, and fixed terminal `_finish` releases its original user lease without releasing the gate; (g) queue default 60, validated `1..86400`, three-candidate cap, six transient reschedules then manual hold, live-heartbeat due movement without transient increment, atomic same-holder dual-expiry renewal, one-side expiry denial, token/version ABA takeover and queue/recovery expiry divergence denial; (h) `_15` child-first/header-second faults, direct child append, stale concurrent phase/version, populated `_14` backfill and exact hash/byte preservation all pass; (i) the default-OFF adapter calls issued C at most once, catches Unknown, schedules/reads detached proof, returns sanitized reconciliation with succeeded task untouched, and never invokes ordinary retry/fail or live-handle reuse. These are real PostgreSQL connection/barrier tests where ordering matters; no mock-only proof substitutes for the queue ABA or two mandatory stage schedules.

### Completed root binding and execution prerequisites

Accepted Packet 2 commit/base/source hashes, independent Delivery review, actual `_14`/slot/proof/receipt/callback names, B1/B2/B3 ownership, fresh inventory and four exact commands are bound in this document. The corrected `_15` SQL/trigger/backfill/lease contract and fresh disposable identities/sole-lane snapshot are included here. Astra final READY on this actual packet and synchronized plan/REVIEW is recorded below. Recheck service identity/health and the sole pytest lane at actual invocation; a failed prerequisite prevents that invocation without changing the accepted design.

### Finite advisory amendments after revised-contract review

These finite rules are incorporated into the proposed contract above. They add
no implementation or readiness claim.

1. **D1 — ordinary expiry skew:** After proof-first reads and the required authority
   locks, an identity-exact pre-terminal original tuple must return `retry_later`
   whenever **either** ordinary task expiry or ordinary user-lease expiry is still
   greater than the fresh database clock. This includes one-expired/one-unexpired
   tuples. Use the existing exact scheduling CAS: due at the greater current expiry
   plus one second, close this recovery lease and queue claim together, preserve
   task/evidence/gate/reservations, and do not increment `transient_count`. Only
   when **both** ordinary expiries have arrived may abandonment eligibility be
   evaluated. Identity/evidence mismatches retain the manual-hold policy. Test
   `E_user <= now < E_task` and the reverse skew using actual SQL/barriers and a
   fresh post-wait clock; no expiry adjustment may manufacture abandonment.
2. **D2 — scanner preserves queue disposition:** The shared ordinary closure does
   not activate a queue item. In the scanner's caller-owned transaction, after
   the user-first authority waits and final ordered queue locking, seed a missing
   exact unresolved row as `pending` only. Existing `pending` retains its scheduling
   and transient history; existing `claimed` retains its capture, token/version and
   expiry, including an expired capture left for the normal claimant takeover;
   `manual_hold` retains its state, NULL due time and reason/count; `resolved` is
   never reopened. A contradictory resolved-row/unresolved-gate state fails closed
   for reviewed reinspection. Any actual queue write uses the established exact
   CAS and lock order. The scanner may close an otherwise eligible task/audit tuple
   without changing a prior manual hold, but it cannot grant authority or authorize
   reinspection. Test seven safely recorded transients leading to manual hold
   before scanner closure, then verify scanner closes the exact expired ordinary
   tuple once while preserving that queue row; also verify valid and expired
   captures are not overwritten.
3. **Outage classification:** A safely recorded transient uses the existing bounded
   `retry_later` policy for recorded counts one through six; the seventh recorded
   transient becomes `manual_hold`. If no safe scheduling CAS is possible, retain
   the claim until expiry and report `RecoveryUnavailable`, without fabricating
   a retry or increment. DB outage alone is not a proof mismatch.

The repeated `still-live` or `live tuple` deferral wording in this draft means
the D1 identity-exact pre-terminal tuple with at least one unexpired ordinary
lease. The scanner uses D2's missing-row-only seed rule and preserves prior queue
dispositions. The six advisory groups remain conceptual corrections; these amendments do not close serial source/interface,
inventory, test-command or independent acceptance prerequisites.

## Required behavior

1. `recover_expired` checks evidence under user → lease → task → audit locks before eligibility changes. With evidence, close ordinary leases and set a sanitized non-due reconciliation hold without changing frozen task/source fields or resolving the gate. Without evidence, retain existing safe recovery behavior.
2. Recovery claims are bounded, oldest-first per user and fair across users. `claim_next` excludes tasks not requiring reconciliation and validates lease token/version/phase/expiry. Commit the queue claim before opening a separate user-first authority transaction.
3. Always run detached exact proof before metadata transition, including phase `intent` and cases with no generation rows. Exact proof plus same already-succeeded task/audit/gate permits acknowledgement only. Terminal-committed evidence without full proof remains terminal with `manual_hold`.
4. For pre-terminal unproved evidence, require expired original task/user tuple, task not succeeded, no matching terminal receipt, unchanged evidence hashes, and every present generation owned by the exact attempt/scope in `staging|sealed`. In one transaction revoke both UUID reservations, abandon only qualifying extant rows, mark evidence abandoned and resolve the gate. An absent row still has its reservation revoked.
5. Preserve both mandatory SQL concurrency schedules with two DB connections and barriers: (a) old `stage` holds user lock while recovery waits, then check its commit/rollback ordering; (b) recovery revokes an absent-row UUID first, then old and new owners attempt `stage` and are refused. Repeat after optional physical Qdrant cleanup and verify both IDs remain unusable. Verify another user's write progresses while a gate is held.
6. Exercise proof-first cases across every approved crash point: intent/put response loss, verified document before result, partial stage/upload/seal, both seals before commit, terminal rollback, committed response loss, DB outage, stale worker and late external I/O. Unknown is never replayed.
7. Close every active mutator named by the fixed inventory and fresh `rg` inventory. Cover ordinary `ImportStore` direct constructor and inherited controls, caller-owned stores/cursors, direct `import_objects` and `import_task_attempts` deletion, task/source/audit parent cascades, witness bare insert, `_index(create=True)`, ordinary coordinator/publication callbacks, and all task/source/audit writes. Preserve local-only distributed-disabled paths.
8. Separate resolved private payload lifetime from permanent UUID tombstones and retained task/receipt identities. Negative tests prove unresolved task/source/audit/parent rows cannot be directly deleted or cascade-deleted; explicitly cover direct `import_objects` and `import_task_attempts` deletion. No applicable numeric PostgreSQL retention policy is established: policy remains TBD and cleanup is OFF absent separate approval/configuration. Do not add a 30-day default or infer task/receipt-parent deletion from payload lifetime; preserve UUID uniqueness permanently.
9. Any narrow Worker adapter catches evidence-bearing Unknown once, schedules detached proof, and returns stable non-due hold. After exact abandonment, a fresh attempt must use a new live task/user tuple, new IDs and current preflight. Adapter remains opt-in and not wired by default.

## Acceptance criteria

- [ ] Fresh disposable migration `_15` upgrades after accepted `_14`; legacy rows remain readable; unresolved dependencies resist direct and cascade deletion, including direct `import_objects` and `import_task_attempts` deletion; private payload identity is separated from permanent UUID tombstones without weakening retained task/receipt/generation parent protections. With policy TBD/no applicable approved configuration, cleanup is a verified no-op; no 30-day default or task/receipt-parent cleanup is introduced.
- [ ] Recovery queue tests demonstrate bounded fair claims, post-lock DB-clock checks, short claim transaction, separate authority transaction, stale token/version rejection, and no queue-row lock while waiting for user.
- [ ] Expired and terminal-response-loss tasks with evidence become sanitized non-due holds; no-evidence recovery preserves prior behavior; gates and both reservations remain unresolved/reserved until exact recovery action.
- [ ] Exact detached proof acknowledges only a matching already-succeeded attempt. Tampering, mismatch, terminal incompleteness, unexpected generation state or wrong owner/scope leaves a manual hold and does not call live publication code. DB outage follows the finite policy above: safely recorded counts one through six defer, the seventh holds, and inability to CAS preserves claim expiry with `RecoveryUnavailable`.
- [ ] Exact pre-terminal abandonment atomically revokes both UUIDs, including absent rows; no partial one-ID resolution occurs. Both mandatory SQL schedules pass, including old/new owners after physical cleanup; another user's writes progress.
- [ ] Every direct low-level mutator in fixed and fresh inventory is demonstrably guarded, explicitly disabled for distributed use, or read-only. Tests cover direct PG `ImportStore`, arbitrary callback, witness insert, `_index(create=True)`, source/task/audit controls and cascades.
- [ ] No-policy cleanup is OFF and cannot delete payloads. Any future retention implementation requires a separately approved/configured applicable policy; this packet does not supply a numeric window. Permanent UUID uniqueness and unresolved proof dependencies survive, and private payload expiry does not delete retained tasks or detach immutable receipts/generations.
- [ ] Local/no-gate and standalone compatibility remain passing. Worker seam remains default OFF and public/runtime/bootstrap stay disabled.
- [ ] Packet 3 review reports required crash cases, SQL schedules, all-mutator inventory and test environment evidence. This packet does not establish production cutover or authenticated restart acceptance.

## Test and verification commands

All test paths below exist today except the two explicitly new test modules. Use `D:/python_self_agent/venv/Scripts/python.exe`, a distinct in-repo `--basetemp` for each serial invocation, real disposable PostgreSQL/S3/target Qdrant identities, and no required-service skip. These commands are **prescribed, not run here**. Fresh disposable `_14 → _15` upgrade with populated intent/slots, migration byte/hash assertions, child/header fault injection, source/audit direct/cascade denial and exact C completion belongs in the first command. Any accepted Packet 2 test fixture that directly mutates source/audit must retain the negative by asserting `_15` denial or using a legal setup.

```powershell
D:/python_self_agent/venv/Scripts/python.exe -m pytest tests/integration/test_import_publication_recovery.py tests/integration/test_import_publication_evidence.py tests/integration/test_import_publication_proof.py tests/integration/test_import_memory_publication.py tests/integration/test_postgres_import_leases.py tests/integration/test_postgres_coordination.py tests/integration/test_postgres_import_artifacts.py -q --basetemp=.pytest-tmp-p3-migrate
D:/python_self_agent/venv/Scripts/python.exe -m pytest tests/integration/test_import_publication_recovery.py tests/integration/test_postgres_vector_generations.py tests/app/test_postgres_vector_generations.py tests/integration/test_import_memory_fault_matrix.py -q --basetemp=.pytest-tmp-p3-revoke
D:/python_self_agent/venv/Scripts/python.exe -m pytest tests/integration/test_import_publication_recovery.py tests/app/test_import_publication_worker_adapter.py tests/integration/test_postgres_imports.py tests/integration/test_postgres_import_artifacts.py tests/integration/test_postgres_coordination.py tests/integration/test_postgres_import_leases.py tests/integration/test_postgres_snapshots.py tests/integration/test_postgres_memory_documents.py tests/integration/test_import_memory_fault_matrix.py tests/integration/test_postgres_vector_generations.py tests/integration/test_import_publication_evidence.py tests/integration/test_import_publication_proof.py -q --basetemp=.pytest-tmp-p3-final
D:/python_self_agent/venv/Scripts/python.exe -m pytest tests/test_import_repository.py tests/test_import_worker.py tests/test_user_mutation_coordination.py tests/integration/test_import_memory_consistency.py tests/integration/test_import_memory_preflight.py tests/integration/test_import_memory_publication.py tests/integration/test_import_vector_publication.py tests/integration/test_import_document_publication.py -q --basetemp=.pytest-tmp-p3-offline-local
```

The first command must cover bound success and rollback injection after terminal append, task success, audit success, batch touch, user release and task-expiry CAS, plus lost commit response with no callback replay; forged/copied/subclassed/other-issuer/cursor/tx binding negatives; exact receipt/hash/lease tamper; populated migration, seed rollback and no scanner/Worker terminal discovery; audit/source races using two real SQL connections. The second must include both mandatory barrier schedules, absent-row permanent UUID refusal after gate resolution and cleanup, vector `vector_publish` two-scope count/digest/revision/reservation negatives, `_index` direct denial and another-user progress. The third must cover D1/D2, queue ABA/tokens/fairness/expiry bounds, six transients plus seventh hold, no-policy cleanup OFF, direct source/audit/task/batch/user deletion, escaped `ImportStore`, inherited controls, callback before-invocation denial, gated heartbeat one-sided expiry and after-lock clock, default-OFF adapter and original no-gate PostgreSQL compatibility. The fourth covers offline/local and live publication compatibility (its `offline-local` basetemp label is only a directory name); any service fixture within it must be disposable and cannot be silently skipped if needed for its assertion. Record command exit, counts/skips, complete meta/XML, tested source hashes and service **types/identities only**, not credentials. Also run `git diff --check` and fresh `rg -n` all-mutator classification at the eventual accepted Packet 3 tree. The sole pytest lane and production/runtime gates remain separate.


## Stop conditions

Stop and report a workflow reality conflict before editing further if Packet 2 is not independently accepted; any frozen interface, hash, schema, callback admission or file ownership differs; the accepted recovery transition cannot satisfy exact proof/atomic two-ID rules; a newly found mutator lacks an owned-file amendment; a direct writer cannot establish user-first authority; cascade retention conflicts with accepted foreign keys; disposable services/lane are unverified; or any test command cannot prove its criterion. Revise packet dependency, ownership and acceptance before resuming. Never improvise new private recovery authority or data design.


## Independent actual-packet readiness and serial dispatch

- Accepted actual-packet input SHA256: `FAD7FF4950985C6D85BEEAD5A622CE99B0A112B48CD840D90D2348D773FDB9A8`.
- Independent Astra report: `.runtime/distributed-cutover/restart-packet3-actual-ready-delta-astra.md`, raw SHA256 `3A26038CA7F9C60E1F1A8540808A44837510DF3F8436481F322F919EF5A389F9`, READY for the corrected actual packet.
- The complete BC45 review `8BC1B718...83062` found only F1/P2's four old queue-interface conflicts; the exact corrected-packet delta closes them. Embedded precise SQL v3 (`86824AE3...5946B`) remains unchanged and independently READY (`4A223815...326EB`). No source implementation or SQL/test execution is inferred from these reviews.
- Routine promotion changes only status/owner, closes pending-readiness wording and appends this record. Base stays `cb06adb`; the documentation-only dispatch commit may be its descendant, with source29 and nine other baseline inputs unchanged as explicitly bound above.
- Implementer: GPT-6 Sol High, sole serial source/test owner and sole pytest lane. Root manages documentation/Git/evidence. GPT-6 Astra High independently reviews concurrency/consistency and exact delivery after meaningful implementation boundaries. Luna High may prepare mechanical logs/snapshots with disjoint ignored output paths.
- Execute Task 7's coherent migration/evidence/queue/fixed-completion slice first, obtain independent implementation review before advancing the subsequent serial Task 8/9 boundaries. Current prescribed services/basetemps must be freshly verified by the implementer. Every product acceptance checkbox stays unchecked until actual corresponding evidence is returned.

## Accepted Task 7 and serial Task 8 handoff (2026-10-05)

- Task 7 source commit: `686ec5279c44bb01324c6154d819af5af41d4b9b`; sole tested parent `317bfa07f7e090974cdba30ff38657c1264b2426`. All 41 current/committed raw source inputs match `.runtime/distributed-cutover/restart-packet3-task7-n1-freeze-manifest.json`, SHA256 `F88078A1EA9501041DC5DACC4E3C9862095594560B8AD9CD5E73BAFF5865A935`. Fifteen actual changed paths comprise 11 semantic changes and four preexisting line-ending differences; no documentation/runtime artifact was committed with the source.
- Complete prescribed first command: 303 passed in 1779.02 seconds; meta, passive resource ledger and detached wrapper child exit 0; zero failures/errors/skips. Prefix `.runtime/distributed-cutover/restart-packet3-task7-migrate-n1`; meta SHA256 `8F5C15D4E2C8C1C6CDDB2852F46D2C3346DB49474AECA176FD46280DC6422C07`, XML `4E2C1CF832A734CC377B33EA37688EC8B910CC529A5B8EE418FBE84BE71D9833`, log `193337C3BA86F600D84CF2F91394326420DE32919C15541861A6BB48CD105528`, ledger `1F4BA0D38F9FC64B023416ECD27A6333F5BC81A906E71E660FC11A09E5C3BCE8`. Historical/focused counts are not added.
- Final independent Spec/Quality PASS: `.runtime/distributed-cutover/restart-packet3-task7-static-f88078a1-resume-astra.md`, SHA256 `DBC7DA4D2B56E46D2BD4DD415C85048CF2B3E0B73BCF3F399A5EC0F0F66883A8`; complete audit `35E1BF065319CCE5A9CA27A74DDB04DDFF1562DB002088646123BBBB0F8C136B`.
- Actual source-commit Delivery PASS: `.runtime/distributed-cutover/restart-packet3-task7-686ec52-delivery-astra.md`, SHA256 `7DC063E83267531C294938704DB9DF7B823FC13F76245A94747442E846439C7E`; complete audit `88E80DE2B3D6A24A7E219441871ADA82DCC4CA694A1AEDFB824E6AD0C7D386CA`. Root read the complete reports and captured zero before/after errors, 41 actual Git raw blobs/modes, unchanged evidence and all stability predicates true. Raw commit binder `restart-packet3-task7-686ec52-raw-commit-binding.json` is `99D70D303C41B2D33FDA16E43E6C98421C4CFDD8C46E0260E658A3FB2E074117`.
- This closes Task 7's coherent migration/private-payload adaptation, recovery queue/claim/expiry closure, B1/B2 and fixed-completion verification boundary only. The whole-packet criteria above remain unchecked pending Task 8/9 and final integration review.

Root authorizes the next serial **Task 8** from accepted source `686ec52` or a verified documentation-only descendant whose 41 raw inputs are unchanged. The old `cb06adb`/Packet 2 table remains historical design input, not a baseline to restore. Sol High owns only `app/import_publication_recovery.py`, `app/import_publication_evidence.py`, `app/postgres_vector_generations.py`, and `tests/integration/test_import_publication_recovery.py`; the other three modules in the prescribed revoke command are verification inputs. Additional edits require a finite reviewed ownership amendment. Root owns documents/Git/evidence; Astra High independently reviews concurrency/consistency; Luna High may write disjoint ignored mechanical evidence.

Implement the approved `prove_or_hold`, `ack_success`, `ack_proved_in_transaction`, `abandon_exact`, and `abandon_exact_in_transaction` interfaces above. Detached proof runs first for every phase, including absent generation rows. Exact already-succeeded proof permits only atomic metadata acknowledgement; unproved qualifying preterminal evidence permits atomic revocation of both permanent UUID reservations, including absent rows, and exact ordinary closure/resolution. Fresh clocks after blocking locks, original tuple/audit/hash/receipt checks, caller-owned READ COMMITTED cores, manual hold and the persisted finite transient policy are unchanged approved requirements. No callback replay, ordinary-writer recovery authority, terminal demotion or external outcome inference is supplied.

Task 8 must include both real SQL barrier schedules, old/new-owner refusal of revoked IDs after optional physical cleanup, another user's progress, and all-or-none rollback negatives. Use the exact second command above with `.pytest-tmp-p3-revoke`, fresh verified disposable PG/S3/target identities and the sole pytest lane. Preserve all prior basetemps/evidence; use exclusive outputs, record full exit/log/meta/XML/passive ledger and before/after raw source hashes, then freeze source/tests for independent Astra review and actual commit binding. Task 9 cannot start before Task 8 acceptance. Runtime/API/bootstrap remain OFF; historical text/metadata preservation and vector recall pending proof remain unchanged.

Current Task 8 execution binding (2026-10-05): Sol restored only the three dedicated disposable container IDs listed above. PostgreSQL and S3 remain on loopback ports 59497 and 59498. The same target Qdrant ID `6bb7e303...f017` uses dynamic allocation; its current actual `NetworkSettings.Ports` binding is `127.0.0.1:56567`, superseding the historical 60266 observation for this execution. Its volume is `zhiyan-dist-bf20-qdrant-vector-target` at `/qdrant/storage`. An empty configured HostPort denotes dynamic allocation; do not recreate the container or reinterpret it as a fixed historical port. Root's filtered identity/mount/state and HTTP readiness record is `.runtime/distributed-cutover/restart-packet3-task8-services-20261005-root.json`, SHA256 `B28A9F46421FCCAD8598130475CBF9CF9BED7EA4C7785C33BC8528A88664CDBB`; Sol separately observed PostgreSQL readiness. Protected source Qdrant remains exited. Fresh per-invocation checks remain required; this readiness observation is not business-flow acceptance.

Current Task 8 execution binding (2026-10-07): the same dedicated target Qdrant ID `6bb7e303...f017` now has dynamic loopback port `51038`, superseding the earlier 56567 observation. PG/S3 retain 59497/59498 and all existing volume identities are unchanged. Root independently captured filtered IDs/mounts/ports/state and HTTP readiness in `.runtime/distributed-cutover/restart-packet3-task8-services-20261007-root.json`, SHA256 `D4C0CFB04466597336A0EEEFF9229AE360B5041D05890F2FACEE632DE925293C`; source Qdrant remains exited. The corrected formal-run helper must bind this fresh port and preserve old helper versions. Per-invocation identity/readiness and sole pytest-lane checks remain required.

## Accepted Task 8 and serial Task 9 handoff (2026-10-07)

Task 8 is accepted at source `6622a1ed544b87a0f93df9161e007f2c3371025b`, sole parent `fc9dfd2e592948291711e4346c36da052407ffc3`. Its frozen 41-path manifest is `B6162DE93796EE8D11DEA2CDD99D973B3D3B8155AFE3E58011AB8A4AB785C959`. The exact prescribed four-module p3-revoke command passed 160 cases in 898.48s, actual pytest/meta/ledger/detached child exits 0, zero failures/errors/skips. Root complete evidence check is `9711EC43B05AF5D7F3C567273A46F676CF18FEE22B224E4637D568226C196180`; full snapshot binding/index are `91023B4E3B31D14ABDBC1E84251CA3E0E0F5898DE591A1A4A1FB58CB7A752E68` / `3B5B508E5225FD6964C67490D455BB6EA7A02DD1FE1F2BE2D1DCCA1467D76FE7`. Independent full-result Spec/Quality report/audit are `6867AB291185D9B81747D93530B33D4239CFB0CB6AD9D48A6830849743EE872E` / `27D489899B63C7071D16A4622BF8E66355CD5D98E44C35BA69E7AD99274469D9`. Actual raw binder is `69B4D2CCF2F84BBC7412161C26BFBC7E6BA4400CA172D40F5A4BFD6F6995877B`; independent Delivery report/audit are `93888D13C584246CC28465A60FAA4B7D075E26F1EDD1F5CA41DC779548B1149B` / `E6D46734EA11481E5EB8B6E2219638016E0E8FC65D9738E9E2D0413E60E46C9E`. Root read both complete reports and parsed complete before/after audits; every 41 actual committed/BASE/index/workspace blob and both snapshots match, only the three Task 8 source/test paths change, and index is empty.

The timestamp capture's original literal equality refusal and intermediate helpers remain preserved. Final independently reviewed ASCII-only timezone-aware integer UTC 100ns equality changes no actual run evidence or product/test bytes. Final capture v5/verifier v7 passed, with full mechanical report `CB22127946CBE898FEF5CB20DB439403BBF4D932DC63DDCC9DE5FD643E144779`. Earlier targeted diagnostics remain separate and are not counted into 160.

Root authorizes serial **Task 9** from accepted source `6622a1e` or a verified docs-only descendant retaining all 41 B616 blobs. Sol High owns the sole source/test/pytest lane; Astra High independently reviews concurrency/consistency; root owns documents/Git/evidence. Begin by freezing actual baseline inputs and inventorying every SQL mutation entry across app and migrations. Classify each as guarded, distributed-disabled, or read-only and identify concrete gaps before editing. Preserve already accepted Task 7/8 behavior and tests.

Task 9 source ownership is `app/import_publication_worker_adapter.py` (new), `app/postgres_coordination.py`, `app/postgres_import_leases.py`, `app/postgres_vector_generations.py`, `app/postgres_document_objects.py`, `app/postgres_history_document_witnesses.py`, `app/import_persistence.py`, `app/import_repository.py`, and `app/postgres_import_artifacts.py`. Test ownership is `tests/app/test_import_publication_worker_adapter.py` (new), `tests/integration/test_import_publication_recovery.py`, `tests/integration/test_import_memory_fault_matrix.py`, `tests/integration/test_postgres_imports.py`, `tests/integration/test_postgres_import_artifacts.py`, `tests/integration/test_postgres_coordination.py`, `tests/integration/test_postgres_import_leases.py`, `tests/integration/test_postgres_snapshots.py`, `tests/integration/test_postgres_memory_documents.py`, `tests/integration/test_postgres_vector_generations.py`, and `tests/test_import_repository.py`. All other final-command modules are verification-only. Additional recovery/evidence/C/candidate-store/native distributed deletion or fence-writer modifications require a finite root-reviewed ownership amendment before editing; appearing in inventory or commands grants no ownership.

Implement the already reviewed all-mutator user-first, after-lock fresh-clock, exact issued-live authority contract and private default-OFF Worker adapter specified above. The adapter executes the originally issued C path once; durable Unknown schedules/reads detached proof and returns sanitized reconciliation, never ordinary retry/fail or handle replay. Normal returned success requires fresh authoritative committed validation. Never use recovery authority for ordinary publication. Fresh retry after exact abandonment requires new ordinary tuple/UUIDs/current-source preflight. Unresolved evidence dependencies and permanent tombstones/retained receipt parents remain protected. No-policy cleanup remains OFF.

Use the exact third/fourth commands above with `.pytest-tmp-p3-final` and `.pytest-tmp-p3-offline-local`; preserve old basetemps and all artifacts. Fresh dedicated PG/S3/target identities/readiness and sole pytest lane must be rechecked at invocation. The last verified target Qdrant port is 51038 on the same dedicated ID/mount, not a fixed allocation guarantee. Run a fresh disposable-schema migration upgrade and `git diff --check`. Freeze exact complete source/test inputs, obtain independent Astra static/result review and actual commit Delivery before Task 9 acceptance. Native Worker/API/bootstrap, protected source Qdrant/retained target, main/production cutover and historical vector recall remain outside this delivery; historical text/metadata retention with vector recall pending proof remains unchanged. Whole Packet 3 still needs final integration review.

## Implementation handoff

Replace this whole-packet placeholder only after Tasks 8/9 and final independent integration review:

```markdown
## Implementation handoff

- Packet: `restart-publication-evidence-03`
- Status: `done | blocked`
- Delivered:
  - concise independently verifiable result
- Files changed:
  - `path` — purpose
- Interfaces added or changed:
  - exact accepted signature/data shape, or `none`
- Acceptance evidence:
  - [x] criterion — evidence
- Verification:
  - `exact command` — PASS/FAIL with counts and skip details
- Scope confirmation:
  - changed only allowed files: yes/no
  - forbidden areas untouched: yes/no
- Deviations:
  - `none` or exact approved deviation
- Residual risks/follow-ups:
  - `none` or precise unverified gate
- Commit:
  - `<hash>` or `not committed`
```
