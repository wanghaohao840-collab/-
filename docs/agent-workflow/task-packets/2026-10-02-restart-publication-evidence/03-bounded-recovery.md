---
id: "restart-publication-evidence-03"
title: "Bounded restart recovery and remaining mutation guards"
status: "done"
parallel-safe: false
depends-on: ["restart-publication-evidence-02"]
base-commit: "cb06adb31be518d465eefdf70cbe3be36d458214"
owner: "GPT-6 Sol High (serial implementation)"
---

# Task Packet: Bounded restart recovery and remaining mutation guards

> Packet 1 and Packet 2 are done. Packet 3 Tasks 7, 8 and 9 are accepted on this independent branch; Task9 source `5d0e66a` has637 current complete passing cases and independent Spec/Quality/Delivery PASS. Whole-feature final integration is recorded separately in `FINAL_INTEGRATION_REVIEW.md`. Native/API/bootstrap remains OFF; main/runtime/production acceptance is outside this delivery.

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

## Task 9 finite authority amendment and implementation GO (2026-10-07)

Root read the complete Sol inventory and Astra independent preparation (`E848E634DD7BEAEE2E9DDDCC55582FE7023589F2976345ED2215021A27ABC247`, audit `626FDB5EDF0FAC4BDA2E0645B70A2D1BB53EC5225B206418AFB552A703A82D48`). Root grants only two additional Task 9 source changes: `app/import_publication_evidence.py:require_no_gate_in_transaction` validates an active caller-owned READ COMMITTED transaction before its existing gate SELECT, retaining user-lock responsibility at callers and leaving detached RR READ ONLY proof untouched; `app/import_publication_recovery.py:schedule_unknown(key: AttemptKey)` plus a private scalar exact-key reader if strictly needed, with the narrow scheduling contract below. No new test ownership is needed beyond the existing Task 9 set. Other source/codec/reserve/slot/C/proof/candidate-store/migration logic remains outside this amendment.

`schedule_unknown` is observation/scheduling only, never recovery execution authority. Validate exact key/header/gate/already-seeded queue consistency in active READ COMMITTED under existing user -> ordinary lease -> task -> audit -> evidence/gate -> recovery lease -> queue order. Re-read current ordinary expiries and fresh database time after waits. For an existing pending row only, exact version/state CAS to pending with version+1 and reason unknown; preserve transient_count and set due_at no earlier than max(old due_at, current task expiry, current user expiry, fresh clock). Existing claimed/manual_hold/resolved rows remain byte-for-byte unchanged and are not reopened. Missing/contradictory queue, unsafe authority or failed CAS returns sanitized RecoveryUnavailable. Do not create/steal/renew recovery captures, seed repair rows, acknowledge/abandon, or mutate ordinary task/audit/lease/evidence/gate/reservations. Intent commit already seeds its queue atomically, not merely at expiry.

The adapter executes only the original issued C path once. After normal returned detached result, a separate fresh exact detached proof must validate the same key and complete authoritative succeeded task/audit/terminal/domain result before committed_success. Durable Unknown schedules the exact existing queue then reads fresh detached proof; even successful standalone proof remains needs_reconciliation/durable_unknown until separate recovery acknowledgement. Failed scheduling/read produces sanitized recovery_unavailable. No ordinary fail/retry, handle replay, arbitrary callback, native wiring or proof/C modification is authorized.

Root verified baseline v2 (72 ordered paths, 70 present/two new absent) SHA256 `1992E7334867ABCDC833794D1905322E50C0DEBE2A3F5FF9BB19A04F0780CBDE` against actual HEAD raw blobs, all 41 B616 inputs, and the 15 saved original-byte backups (`B8464B3E663449C5F8E9A03A742BC796BD8BC30C7048AB71F1260FA2DD022EFD`). Root check SHA256 is `BE2A208DA517CF122FCE6CAD0B4277D0FAC43BA3AF577743501E80411229CB20`. Baseline v3 preserves the exact 72 paths/hashes and updates only the two finite ownership flags and provenance. The 15 newline adjustments are mechanical baseline preparation only, not logical ownership; all v1/v2/backups remain retained. Git diff has no source-content change from that preparation, notwithstanding stat/autocrlf warnings.

Sol High has implementation GO within the default ownership plus these two finite scopes and the sole pytest lane. First add a real two-connection/barrier negative in owned recovery tests for raw SQL RR snapshot established before intent/gate commit then source UPDATE/DELETE. This source-trigger concern is static and unproved; no migration edit is authorized unless the actual negative reproduces and root independently reviews a finite trigger/migration amendment. Likewise cover the related audit/parent dependency cases without weakening prior negatives. Preserve original issued live exceptions, fair queues, Task 7/8 and standalone/local behavior. Required final two unfiltered commands, fresh disposable migration, exact source/test freeze and independent Spec/Quality/Delivery acceptance remain unchanged. Task 9 acceptance and final integration are pending.

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

## Task 9 finite source-isolation migration GO (2026-10-07)

Root independently read Astra finite report/audit (`9D6F76AEB64688562FFE1F6240FCDA4FEFCE2A21808DA906D68862FA7B288FDF` / `AD8585E3FDEEBAC5E483EE6F07A551FBCE51DAAF17689E0C175ACA5F6874D57C`). N1 real old-RR DELETE was accepted; its UPDATE was a no-op and is not content-tamper proof. N2 legal `size_bytes+1` UPDATE reproduced the same snapshot bypass (1 failed, actual pytest/child exit 1), with actual 72-input before/after stability. These are diagnostic RED runs, not formal Task 9 acceptance; root diagnostic check `8B953845DA22DBA0F86BC6EE28DE33D73C8F6871152BCB5C9F91432D7268B9D2` preserves that distinction.

Root grants Sol High new owned source `migrations/versions/20261007_16_publication_dependency_isolation.py`, revision `20261007_16`, down_revision `20261002_15`. Install only separate `publication_dependency_isolation_guard_16()` plus `aa_import_object_isolation_guard_16 BEFORE UPDATE OR DELETE ON import_objects FOR EACH ROW`. The function refuses current_setting(transaction_isolation) other than read committed before returning OLD for DELETE / NEW for UPDATE. Preserve existing _15 bytes and functions. No data rewrite, FK/index/version/retention changes, SECURITY DEFINER, trigger disabling, or runtime/native/prod wiring. The same-function audit trigger requires separate root grant after concrete audit negative review; add owned-test audit/parent RR negatives now without extra parent triggers.

The explicit write contract refuses raw source UPDATE/DELETE under RR, SERIALIZABLE and READ UNCOMMITTED even without a gate, for no-op updates, or after resolution. RC source semantics and INSERT remain unchanged; detached RR READ ONLY proof remains untouched. Required owned recovery tests cover real two-connection source mutation denial and fresh retained-state checks, no-gate unsupported isolation, RC compatibility, audit/parent negatives, populated _15-to-_16 byte preservation, fresh upgrade, upgrade-head no-op, and transactional DDL failure rollback. Downgrade fails closed with compatible recovery requirement. No final acceptance is inferred.

Approved baseline v4 `.runtime/distributed-cutover/restart-packet3-task9-baseline-v4-manifest.json`, SHA256 `698DBBBF4181A60C4E4FD65961D54DB455E8545CB24423BAE2B0788A7C7C2980`, preserves every v3 baseline value/relative input order and adds only this new absent owned path: 73 inputs, 70 BASE-present and three BASE-absent. It records prior baseline provenance, not current WIP equality. All old manifests, EOL originals, RED artifacts and basetemps remain. Helpers must bind all 73 inputs. Final two exact suites may run only after source freeze and independent static/helper review GO. Root docs stay uncommitted until Task 9 source commit with sole parent d9ddeb7.

Task 9 baseline correction: v4 strict inherited-order check actually exited 1 before output: sorting moved app/postgres.py among unchanged 72 entries. Preserve v4; helper pinning now uses `restart-packet3-task9-baseline-v5-manifest.json`, SHA256 `8C187F8BC02F4B7E31A8F7F16366FB8924334BE265F1C53D9BB402A63AC175BC`. V5 preserves all exact v3 entries/order and appends only the new _16 BASE-absent path. Initial v5 check exited 1 on a current-workspace absence assertion while Sol legitimately created the granted _16 WIP; no product or baseline corruption is inferred. Revised root check independently validates all 70 actual HEAD blobs and three HEAD absences, explicitly records current WIP existence, and makes no current-owned-source equality claim. Check `restart-packet3-task9-baseline-v5-root-check.json`, SHA256 `78842E34C862F6CEF7B3E2F1A69751BAAFB5BA0939AD74DB4B108D8055A5F091`, actual exit 0: baseline provenance only, not implementation acceptance. Source-only _16 contract unchanged.

Task 9 fixture dependency binding: the owned fresh/populated/rollback upgrade tests import shared_database from `tests/integration/test_postgres_auth_sessions.py`. Root adds this unchanged fixture provider as verification-only (no edit ownership), actual HEAD/work raw SHA256 `FA9BA54DE921C67150B73B31900EDF3D0836A1B1B71EDCDBAE64C780552E3BED`, mode100644 blob `93ea8aff9c569247e2d39fc3d12f18f57949c453`. Approved baseline v6 `restart-packet3-task9-baseline-v6-manifest.json`, SHA256 `474FF583237F2DAB4C7C045A4F76DAD9F252709D796CD6C699B5D25C23D25446`, preserves first73 v5 entries/order and appends only this dependency: 74 inputs, 71 BASE-present/three BASE-absent. Formal helpers/snapshots/binder must include all74. Prior targeted73 diagnostics remain exactly their observed scope, never relabeled74 or formal. Existing _15/_16 ownership contract and final command argv remain unchanged. All prior manifests retained.

## Task 9 finite audit-isolation amendment GO (2026-10-08)

Root read the complete new independent Astra N6 report/audit, SHA256 `60CBFADC0196B778AAC4821E56A4D466F27AA9A27C561043E2FC01CD722BF7F2` / `F86617E312E2646AC9EA1A79C489C9D1362D7031B3D750632185E9BC00FA9E84`. All nine artifact hashes match root; actual audit old-RR DELETE statement accepted after original intent commit, then rolled back by the failing test. This justifies a finite amendment, not delivery acceptance.

Root explicitly grants Sol addition of only `aa_import_attempt_isolation_guard_16 BEFORE UPDATE OR DELETE ON import_task_attempts FOR EACH ROW EXECUTE FUNCTION publication_dependency_isolation_guard_16()` within the already owned, uncommitted _16 migration. Reuse its one isolation guard function; generic dependency wording/docstring is permitted. Keep revision/down_revision and _15 bytes/functions unchanged. No parent-table trigger, FK/index/version/data rewrite, reverse user locks, retention/cleanup change, SECURITY DEFINER, native/runtime/production activation. Baseline v6 stays74 inputs with no new ownership path.

Matched audit and source UPDATE/DELETE now refuse non-RC isolation even without gate/no-op/after resolution; RC legacy heartbeat/terminal/expiry and source semantics, INSERT, detached RR READ ONLY proof stay unchanged. Owned tests must specifically assert direct audit P0001/READ COMMITTED message for DELETE and illegal end-state UPDATE, no-gate unsupported-isolation and RC compatibility, deterministic complete retained-row equality from fresh transactions. Parent negatives require actual allowed SQLSTATE P0001/23503/40001 as applicable, never timeout/deadlock/broad arbitrary failure. Fresh upgrade inspects both triggers/shared function; populated intent and terminal data preservation includes actual receipt/domain rows and upgrade-head no-op. Failure while installing second trigger must roll back first trigger/function/version atomically; downgrade remains fail-closed.

Sol may now implement and run unique targeted74-input diagnostics in the sole pytest lane, preserving all earlier evidence/basetemps and verifying dedicated services. Full exact two suites require frozen source plus independent helper/static review and root GO; Task9/whole Packet3 acceptance remains pending.

## Task 9 finite batch-bookkeeping amendment (2026-10-08)

Astra identified direct `_touch(cursor,row)` ungated batch mutation and ordinary `_progress`/`_finish` trusting a caller-supplied different task row before validating the live attempt. Sol fixes these within default-owned `app/postgres_import_leases.py`: direct ordinary `_touch` must reject non-active/non-RC transactions before SQL, lock the actual user and require no gate; ordinary progress/finish validate fresh exact live task/user/lease/batch consumed fields before any DML. No caller flag, reconstructed authority or new exception capability is permitted.

To preserve original exact authorized C/recovery bookkeeping without making direct `_touch` a gate bypass, root grants one additional narrow edit in `app/import_publication_recovery.py:close_expired_evidence_attempt_in_transaction`: replace its final `_touch(cursor,held)` call with the same local batch `updated_at` SQL after existing exact closure validation/CAS, using its already captured fresh database time and exact held batch/user. Remove the now-unused local import if needed. No closure predicates, lease/queue/gate/reservation semantics or lock order changes are authorized. Existing original issued live `_progress` and terminal-bound `_finish` may perform the same local batch UPDATE only after their existing exact validations; ordinary paths retain guarded `_touch`. No helper replay/issuer widening/C or proof file edit.

Required direct-helper tests show gated `_touch` and cross-user/task supplied rows cannot mutate batch/task/audit, including when the caller catches rejection and commits the surrounding transaction. Preserve ordinary compatibility and exact issued C/recovery checks. All74 final inputs/ownership paths unchanged; this amendment is not implementation acceptance.

Task 9 dedicated-service observation (2026-10-08): Sol restored the same three previously authorized isolated container IDs and original volumes. Root independently verified filtered identities/mounts/state, PostgreSQL pg_isready exit0, S3/Qdrant ready HTTP200, PG59497/S359498 and target Qdrant dynamically allocated `127.0.0.1:51469` (supersedes51038). Protected source Qdrant stays exited with empty ports. Record `restart-packet3-task9-services-20261008-root.json`, SHA256 `8787792BB6192E1F427121172A1E5FA69119928E1BE9C2956852116C1B968A95`. This is readiness only, not business-flow acceptance; fresh per-invocation checks remain required. Targeted N7 74-input matrix may run in Sol's sole lane; final suites still require frozen independent review GO.

## Task 9 N4/v7 formal-suite GO (2026-10-08)

Independent N4 source READY_STATIC_SOURCE (manifest `359AE5EBB38EFC49A254B88E82F4280FA7651D09770CFCE5A1B7AC7A439948E0`) and frozen v7 helper READY_STATIC_HELPER (`3D0AB382706DD47185C9873B1769BF6D28F13F68B044ECB6B361C2824ECE7961`, audit `B021106C80E47D077326C0EB237927467ABDEEF756AE451562D528BB7C5058C3`) now close the static prerequisites. The exact parameterized migration names were checked against actual XML without weakening identity matching. Approved v6 baseline remains `474FF583237F2DAB4C7C045A4F76DAD9F252709D796CD6C699B5D25C23D25446`.

Root actually captured `packet3-task9-static-359ae5eb-v7` with exit0, binding `5651186F9B01DF3158B91A11C5DDEF11C5431CBFBE0E397B85DC3B118BC13607`, index `4260B87CBF86CB6F560759323B9FB0727491AB6B7196BC8037FB2E1274FA82CF`. Independent raw root check `restart-packet3-task9-n4-v7-static-root-check.json` (`298B4964055DEC5E833652D985D93C4045241251947AC0233C523104E5986993`) actually exits0: 74 ordered current/source-copy inputs,71 actual BASE blobs/three absences,12 owned differences,235 payloads and8 helper bytes match; HEAD stays d9ddeb7.

Sol High receives sole-lane GO for the exact unfiltered final command through frozen v7 detached wrapper (`restart-packet3-task9-final`, `.pytest-tmp-p3-final`), then only after complete success/zero skips the exact offline-local command (`restart-packet3-task9-offline-local`, `.pytest-tmp-p3-offline-local`). Fresh service identity/readiness preflight matches isolated ports59497/59498/51469 and preserved volumes; protected sourceQ stays exited. Existing source/helper bytes stay frozen. Failed first phase stops the second for review. Actual results, independent Spec/Quality, raw committed Delivery and wholePacket3 integration remain pending; docs stay uncommitted until sole-parent-d9 source commit. No native/main/production acceptance is inferred.

## Task 9 finite actual-failure correction GO (2026-10-08)

Root read complete Sol proposal `60FF91A4249FD48A41E2F8F161D96DEBBB65D1256E9EEF198C4BF51B45E5D362`/audit `03F28D9B37EC8585148840DFF379774B5323AE1FF8BC7199657159F40B4FF9FC`, independent actual-failure CHANGES_REQUIRED report `72BA7095F71EF2CC486CA99BA45E844402382CD862327DB87E2667A588418D35`/audit `8A71B10CEEEB7563B8E5D8757811711CE1B18018936808A20947B8783A0D94AD`, and finite proposal READY_FOR_ROOT_FINITE_AMENDMENT_AND_IMPLEMENTATION report `6D1B34DD1EBF3FB0FA1DE6FCEC66A949BA99F6074663DF475C85DF6C8623DA3C`/audit `E906356DDAB203CE99ED6A515385C204442CDE7CDF0472EBCAB9118BA9C46067`. Source74 and original actual run hashes match. The first failure audit observed an interim Sol proposal hash; final Sol proposal is separately frozen at the hashes above, and the old audit is retained rather than retroactively rewritten. This distinction does not alter original run evidence.

Root grants Sol only one new finite source ownership: `app/import_memory_publication.py::ImportMemoryPublicationService.publish` initial existing self-owned transaction block before `_plan_intent`. Add explicit BEGIN, existing coordinator `_isolation(cursor)`, existing `_lock_user(cursor,attempt.task.user_id)`, then retain the strict no-gate check. Never change configured isolation, hold this preflight across external I/O, weaken public caller-owned IDLE/RC checks, mutate app/postgres.py, durable issuance/execution/terminal/proof, recovery, or native/API/runtime composition.

Within existing owned `_finish`, preserve original terminal receiver/coordinator/fixed arguments, expanded authoritative consumed-field comparison and original ImportMemoryPublicationError/message on valid terminal caller-row refusal before one-use consumption/DML; ordinary mismatch remains ImportLeaseLost. Keep full unchanged-row/unused-binding/same-binding-success tests. The two owned legacy migration test files may use version-correct local raw SQL fixture seeds instead of current guarded APIs on pre-gate schemas; keep original target revisions/idempotence/retained constraints/refusal assertions. No missing-table production fallback, fake gate table, early head upgrade, skipped tests or broad exception expectation.

New approved baseline-v7 `restart-packet3-task9-baseline-v7-manifest.json` SHA256 `171655548EC3D51242361C4333D2634661B7B1D531924EED8A5B9731F3701B1C` preserves all74 v6 input paths/order and every original BASE value, changing only the existing ordinary publication file's ownership flag and adding finite provenance. Root creation/check actually exits0, check `0A33975FAD8667992E805E6CC0498FA643C305A0AA24E6049D25AE7868EA9318`. HEAD remains d9ddeb7; prior baselines/source freezes/helpers/failed artifacts/basetemps remain preserved.

Sol High may now implement these finite corrections and focused diagnostics in its sole pytest lane, then create a new source freeze for independent Astra review. New preserved v8 helper candidates must bind baseline-v7 and the same74 inputs, exact unchanged module sets with new exclusive final-n2/offline-local-n2 prefixes and basetemps; source/helper/static review and root GO precede either full rerun. Task9/fullPacket3/Delivery acceptance remains pending; root docs stay uncommitted until source-only sole-parent-d9 commit.

## Task 9 N6 focused diagnostic GO and fresh service binding (2026-10-09)

Root read the complete independent N6 test-delta review v3 and parsed its audit: READY_STATIC_TEST_DELTA_TESTS_PENDING, problems[], all74 current/copies match,70 original named AST nodes unchanged. Report SHA256 `61EA0FD2C924B018FD39D8D3A9F29C657B30B68D824E995094D001C92EF17D04`; audit `36E62F8B22DE3AA1B8A7A6A31877890C7DF0D4CB4053E6F06543583F2293D468`. The orphan earlier audit remains preserved. N6 manifest remains `92E3C9EDB99A6FBC7DC44BA7F16BBBBC08DD4F3D906226FC2432E29833672A65`; HEAD remains d9ddeb7, with no source commit or full-suite acceptance.

Only the original three dedicated target containers were restored with their existing volumes. Root independently observed original IDs/mounts/loopback ports, PostgreSQL readiness exit0 and S3/Qdrant readiness HTTP200. PG/S3 remain59497/59498; target Qdrant dynamically changed to55369; protected source Qdrant remains exited/no ports. Fresh record `restart-packet3-task9-services-20261009-root.json`, SHA256 `3FB49A7F2478B59759722EFFCE9FF11022F1C83A5F4F7722A1A58F71BFB58DBF`. Readiness is not business acceptance. Preserved v9 helper candidates bind this observation; independent v8/v9 helper review is still pending.

Root grants Sol High sole-lane GO only for the frozen focused diagnostic wrapper `run-p3-task9-n6-focused-sol.py`, SHA256 `C90B3479B36F45AC4308B7FDA66B6408B44BEF5F5A5C35932ACBECB57219B1A0`. Its actual preview contains39 exact nodeids: all29 original formal failures,2 new N6 tests and8 related original negatives, no implicit selection/config/environment filter. Prefix `p3-task9-n6-focused-sol`, new basetemp `.pytest-tmp-p3-n6-focused`, project venv Python, before/after74, XML/passive ledger/meta/actual exit and original service checks are required. This GO is not a started/completed result; formal final-n2/offline-local-n2 require their separate frozen helper/static GO. Preserve all previous evidence and basetemps.
## Task 9 N6/v9 formal retry GO (2026-10-09)

The focused correction run actually completed with39 passed/0 failed/errors/skips,338.11s console, pytest/wrapper/meta/ledger exits0. Root actual XML/ledger/exact39 node identity/setup-call-teardown/full74/current/service check exits0: `restart-packet3-task9-n6-focused-root-check.json`, SHA `4DB2E8F96875599E3ADA07C89ECCE54FA9054025A89FC74DF77DB506BC266D04`. This is diagnostic correction evidence only; prior formal399/29 remains preserved and is not relabeled.

Root read full independent v8-v3/v9-v1 helper READY_STATIC_HELPER reports and parsed their audits. v8 report/audit `DB0BCC81D4924D0AF400372BCD77424C5A454D2E1F451C9BF004BABCCD3BE467` / `3309D13911E83B8186619E6000F9F22EDA1102777FD8F24066C2D8147A23839C`; v9 `38D64B14F273DB0DFAC9A27C29343D43585C51C0808DD7581DC121EFFBA908F2` / `835448D75D57D5E4A199D5C61E222B170A943D479B35D4F2219FF14CC4DA3CA6`. Exact finite normalized raw equality, actual migration AST/XML positives/21 negatives, binder four-case positives/negatives and native parsing preserve every existing guard. The original v8 freeze MD contains a N13 hash transcription error, and two stale audit heuristic false values; new independent reviews record actual raw/XML truth without rewriting those historical artifacts.

Root governed v9 static capture actually exits0: `packet3-task9-static-92e3c9ed-v9`, binding `01B4A59402068B0856C9F3CB075E01B4E49F41BD581F2F93DA4C3875BDD33390`, index `0F5905174F1A80F20CEFC1071A418F88C84DF281FF738BFDCCDA0DE02E7A5ACF`. Root independent235 payload/all74 raw current/copies/71 actual BASE blobs/3 actual absences/8 frozen helpers/exact15 owned changes check exits0: `restart-packet3-task9-n6-v9-static-root-check.json`, SHA `0F550F4DEE99077AE1E6DA621D80AFF531EDD74C5E80DCBC663BAB0EDF164DD5`. Its initial capture call exited1 before output because root mistyped an approved manifest SHA argument; corrected explicit approved parameter passed. No helper/source/evidence was overwritten. Expanded175 context actual BASE check also exits0, `81CE3C8ACD356D8FE481CB5525309F37E98780E5A624FA281FB028C77A632DA3`:59 raw-equal/106 unchanged EOL-only/10 approved frozen product deltas, only two finite product changes since N4 inventory.

Root grants Sol High sole-lane GO for the exact unfiltered12-module final command through frozen v9 detached wrapper, prefix `restart-packet3-task9-final-n2`, basetemp `.pytest-tmp-p3-final-n2`. Only its complete actual success with zero failures/errors/skips and stable frozen source/helpers enables the exact8-module offline-local command, prefix `restart-packet3-task9-offline-local-n2`, basetemp `.pytest-tmp-p3-offline-local-n2`, serially through the same frozen wrapper. Fresh service/credential/lane/filter preflight remains mandatory. This GO is not start/completion, actual Spec/Quality/Delivery, commit/push, wholePacket3, native/runtime or production acceptance. HEAD remains d9ddeb7 and root documents remain uncommitted.

Task 9 independent actual diagnostic review (2026-10-09): root read complete Astra report `restart-packet3-task9-n6-focused-actual-review-astra-v1.md` SHA `F2E07698103FA6C48A37385F52D656ADAB95AC13FE26E027C28988F66B373DE9`, parsed audit `AFDD09F0541BBE15F993308BB2B6A309EBABD429AA3F267E752C8AA1D4D980B1`: PASS_FOCUSED_DIAGNOSTIC_CORRECTION_ONLY, problems[]. Exact original29/new2/related8 identities, all39 setup/call/teardown, original exception/state/one-use and finite legacy assertions, actual exit0/full74/current/helper/service observation bindings agree. The later-publication proof case uses simulated acknowledgement and is not recovery-API acceptance. No complete formal Spec/Quality is inferred. The formal final-n2 launch is observed at `2026-10-09T02:06:37.6950594+00:00` (10:06:37 Asia/Shanghai), runner PID8376, sameN6/v9; result is still pending and offline-local is not started.
## Task 9 final-n2 incomplete process observation (2026-10-09)

At the next continuation root observed no original runner8376, no pytest or v9-wrapper process, and no final-n2 XML/meta/resource ledger/completion/wrapper sentinel. The preserved console stops near10:16, while this fresh process check was around10:41 Asia/Shanghai. Existing launch/log/stderr/wrapper logs and basetemp remain untouched. Exact termination cause and child exit are unavailable; no completed/pass/fail case count or exit is inferred from console dots. The run is incomplete, not accepted. Offline-local has not started.

All74 current raw inputs still match N6, HEAD is d9ddeb7, and the same dedicated target services still run on59497/59498/55369. Root grants only a finite preserved v10 helper namespace amendment: coherent v9→v10 script references and final/offline n2 prefixes/basetemps→n3, preserving all guards, modules, source/base/service bindings and the eighth passive plugin. Sol is preparing an independent hidden background launch, retaining the original frozen wrapper's actual child-exit evidence. Independent finite v10/launcher review and root static snapshot/GO precede retry. The unfinished Astra cross-packet advisory input exists, but no complete advisory report or verdict was produced before its usage-limit interruption; no review completion is inferred.

## Task 9 frozen v10 external final-n3 GO (2026-10-09)

Root read complete Astra finite READY_STATIC_HELPER_AND_EXTERNAL_LAUNCHER report `0C720D84B3DCBB9EACB614483B6D709E0D8715E644C1D7F4AAC1CE2D9D4BEA53` and parsed audit `D9A04409AD5F4397E46ECF96E108346E5AF7D7FC95E97E403CFA6476FC0D7A3C`. Seven v10 files preserve exact v9 normalized bytes except coherent version/n3 namespace substitutions. External launcher `launch-restart-packet3-task9-wmi-n3.ps1`, SHA `99535577076F10F8A35DE304DCEB70AC78DB482246E122D233EFE1D430F6A113`, starts the original frozen wrapper using Win32_Process.Create/ShowWindow0/workspace directory. It records creation only; actual child-exit evidence remains the frozen wrapper's responsibility. A separate two-second no-SQL/no-pytest probe completed after launcher return; it does not guarantee every interruption mode or count as product verification. Its WMI-created venv launcher PID6156 and real Python child19860 are distinguished.

Actual governed v10 static capture exits0: `packet3-task9-static-92e3c9ed-v10`, binding `0553E30091320D85D5BB1B56DAD504A7BA82FA1FADEC9AE0C7158BDD07A8C22A`, index `581451E2A35535410253E2567CD50B6C2B5FF747D7E9B15B55452CC3F77570A0`. Root independent235 payload/74 current/copied sources/71 actual BASE blobs/3 absences/8 frozen helpers/exact15 owned changes check actually exits0: `restart-packet3-task9-n6-v10-static-root-check.json`, SHA `B3ACF5B24F2992358AD5236F726C5218C0EA717E3D98B4464EC70151B1C96C18`. Source N6, BASEd9, baselinev7 and service observation55369 remain bound.

Root grants Sol sole-lane GO for the original unfiltered12-module final command, prefix `restart-packet3-task9-final-n3`, basetemp `.pytest-tmp-p3-final-n3`, via the reviewed independent hidden launcher and frozen v10 wrapper SHA `2D9D87DD5E44C6F3532E7DB9255FF7D6115CC8B2E5081970391C7C10B8B81981`. Only full clean actual child/wrapper/meta/ledger/XML and stable74/eight-script evidence enables original unfiltered8-module offline-local command serially, prefix `restart-packet3-task9-offline-local-n3`, basetemp `.pytest-tmp-p3-offline-local-n3`, with the same frozen launcher/wrapper. Fresh original service/lane/filter checks remain required. This records execution GO, not start/completion/acceptance. Incomplete n2 logs/basetemp are preserved; no exit was fabricated. No source commit/push/wholePacket3 or native/runtime/production acceptance yet.
Task 9 final-n3 actual launch: WMI-created wrapper PID32964 at `2026-10-09T02:50:46.6976787+00:00` (10:50:46 Asia/Shanghai), external launch raw hash `77EBCEBB7753E0B2765E0428998842CC1B42EC8426A0F7CB202142F76505D8A9`; runner9412 and actual venv pytest process tree independently observed after launcher returned. This demonstrates current independent-process liveness, not completed result. Early log contains a failure marker; offline-local will not start absent a complete clean final result. No traceback/exit/XML/phase acceptance yet, and no source mutation during the run.

Root read the complete independent cross-packet advisory v2 (`D1EB1ADBFADF270DFF0149B1D26DA1230C3781C273713C2A682928154088B68F` MD / `B33586FF0D2550C6D81B3D302A3B6076246328AB3BE422282C3F0ABD099FB2E9` audit): NO_NEW_BLOCKING_CROSS_PACKET_ISSUE_STATIC_ADVISORY,findings[]. It binds25 scoped frozen source files and interface contracts, not formal Spec/Quality or Delivery. The previous v1 preparation-only orphan remains preserved. A possible global pg_trigger absence assertion in the rollback migration test is being examined separately; its relationship to the early failure is unproved pending actual traceback. No retained schema or prior run artifact is cleaned to make a test pass.
Task 9 final-n3 was intentionally terminated early after its observed failure marker and independent static confirmation of a cross-schema test catalog query defect. Sol checked the exact process ancestry/argv and stopped only actual pytest23328; original wrapper32964/runner9412 completed their own postflight. Actual pytest and wrapper child exits are -1; source/helper stability is true and runner error is null. XML is absent, so no completed case count or complete suite result is inferred. Preserve all launch/external/meta/completion/sentinel/termination/log evidence and basetemp. Offline-local remains unstarted. Exact n3 F attribution is unavailable; a separate original single-test RED reproduction is being prepared to obtain a full actual traceback before a finite owned-test correction. No product/test was edited during n3.

Task 9 original rollback-test RED diagnostic GO (2026-10-09): fresh root observation `restart-packet3-task9-services-20261009-n2-root.json` SHA `3788D348CB0216C2953FE4DFFCB2B9F51D64889DAC21472085B6E72DDD36CA9D` actually exits0, preserving all original IDs/volumes; PG/S3 remain59497/59498, targetQ now63188, sourceQ stays exited. Old v10 formal55369 binding is historical and cannot be reused for a future formal run without a preserved finite helper amendment. Root read the complete RED runner delta and exact1-case preview; GO only for `run-p3-task9-n3-rollback-red-sol.py` SHA `801BC38C06F22E1E020784561A13A776A9634C8BA1A27F1AAF4631498B3AA817`, prefix `p3-task9-n3-rollback-red-sol`, new basetemp `.pytest-tmp-p3-n3-rollback-red`. All74 originalN6 source/HEAD/plugin/credential/lane/filter checks remain; no test or product source edit is authorized until actual RED evidence is examined. No formal acceptance is inferred.
## Task 9 actual rollback-test RED and finite test correction GO (2026-10-09)

Original N6 single test `test_15_to_16_failed_second_trigger_rolls_back_transactional_ddl[20261002_15]` actually failed at recovery test line160: the global pg_trigger name-only query returned a row. XML1 test/1 failure/0 errors/skips,36.390s; pytest/wrapper/meta/ledger exit1, setup/teardown passed and call failed, full74/HEAD/services before-after stable. Log SHA `9E1C7100C5A021FB424BD462C0AA032C125D355BF1821F70315666869A148E0C`; XML `AEE9594BA2F5C92DD6AE9202D30CA7F7DE17376A543C142A9C6BDD0997B68C48`. The target schema stayed at `_15` and shared function was absent before the failed trigger assertion. This independently reproduces the static defect; the incomplete n3 run still has no complete traceback and its early F is not retroactively attributed as an actual observation.

Root grants Sol only the already-owned rollback-test SQL change: constrain that existing pg_trigger absence assertion with `tgrelid='import_objects'::regclass` under the original fixture search_path. Keep all original parameter/exception/message/version/function/source-row/DDL rollback assertions, product and migration bytes. No schema cleanup or broader assertion is authorized. Freeze a new N7 full74 candidate and complete independent finite test review before its single-case GREEN diagnostic; formal helpers need a later preserved new prefix/service binding. Current runtime/API/main/prod remain unchanged and no commit/push is made.
## Task 9 N7 finite test correction and actual GREEN (2026-10-09)

N7 manifest `5977AAC2E549FB4E13C7BE300A0D92DD5A95F26B649B724FE45F84D8D6976BC9`, index `5A3EBC261D8982BB53F7DD9CACF18A99D20BCA814E615B39580652BADEC8A4DA`, handoff `A3EF47BAD9BD0866DBADB92E215384BFE0DAD2B241A16C21D9399485AD766450`: exactly one test SQL constant changes; the other73 inputs including every product/migration byte remain N6-identical. Root read full N7 independent READY_STATIC_TEST_DELTA_GREEN_PENDING report `6F8920EB8C7BE41B38209F6DE798D962EC3C793A0666802E9404F285B40499DE` and parsed audit `E4A568895604100B58E276198C59E43BB2F7CC72CB474EA51749C01CC5A17D91`, problems[]. All original rollback assertions/parameters remain, without schema cleanup. The RED establishes the actual failing catalog query, but did not itself capture a relation-scoped observation in its test schema; stronger returned-row-schema attribution in the handoff is not adopted as independent proof.

Root reviewed the exclusive GREEN runner `812F332F60785BCC71E2CFEAC5284A4FA1C3CF79D94B6491995442B913479506` and granted GO for the exact same original rollback node under prefix `p3-task9-n7-rollback-green-sol`, new basetemp `.pytest-tmp-p3-n7-rollback-green`. Actual GREEN exits0, XML1/0 failures/errors/skips, all setup/call/teardown passed,25.92s console; full74 source/HEAD/services stable. Root actual independent XML/ledger/exit/current/source/runner check exits0: `restart-packet3-task9-n7-rollback-green-root-check.json`, SHA `2C318931C19F3F8850017749D3A0AE585FFE46B17A313E4B0EBD2A226C737971`. All RED/n2/n3/N6 evidence remains. This is one-case correction evidence, never a complete formal result or sum with the prior39 diagnostic cases. Independent GREEN evidence review and frozen v11 service/n4-namespace helper review remain pending before root governed static snapshot and formal retry GO. HEAD remains d9ddeb7 with no commit/push.
## Task 9 N7/v11 actual static binding and final-n4 GO (2026-10-09)

Root read full independent GREEN actual report `A64C0CA3BE5F96C5893D040BEC62D4246E7C3C5C58E2A76C7D2DF3E36F914585` and parsed audit `3AA16C18E74BD68D9542F6F17DCC5952662240AC8305352D3DB12647D94965E5`: PASS_DIAGNOSTIC_SINGLE_ROLLBACK_CASE_ONLY, problems[], all original assertions including source full-row equality executed. Root also read full v11 finite helper READY_STATIC_HELPER_AND_EXTERNAL_LAUNCHER report `81B64DEC3AB7AE5D188087B370918C615EE809AA79B3439BE164A967F9707F5E` / audit `C32A9EFBDD13BDFC54ACA22A3272C34DFF35230BB0EB3D6F1715559D72534EC2`. No implicit acceptance of n2/n3 incomplete runs or combination of diagnostic counts.

Root governed static capture actually exits0: `packet3-task9-static-5977aac2-v11`, binding `8E53109AD0CF075BCF138582A35695AA195F0C9F621013C9FC4BC03840A20248`, index `D889328477342FE7DD8CDABB3AAA9E2B869A81881005B1FA421D89BF52EA1D41`. Independent actual235 payload/all74 current/copied sources/71 actual BASE blobs/3 absences/eight frozen helpers/exact15 owned changes check exits0: `restart-packet3-task9-n7-v11-static-root-check.json` SHA `405F1E52A664C1DC68BDEB722E212B8CC3D5741593E3C0F3E51679F2A1462529`. Expanded175 context actual BASE check exits0, `950E194823336BBAD6E2E07EED53C96C7DE1EF395ADD48258A81880EA403C63B`, counts59 raw-equal/106 unchanged EOL-only/10 approved frozen source deltas, only two approved finite product deltas since N4. No expanded edit scope.

Root grants Sol sole-lane GO for the original unfiltered12-module final command via reviewed hidden WMI n4 launcher `CCFC2F0CA26935CF9A99EA60CEA13A19BD861F8F089BD6AC21F3EE99320ED9CE` and frozen v11 wrapper `AD6B84AC4531408EDC344BCDFB11800397CA0E281DB3772A12A397CFA33864C8`, prefix `restart-packet3-task9-final-n4`, new basetemp `.pytest-tmp-p3-final-n4`, sourceN7/basev7/HEADd9. Only complete clean actual pytest/wrapper/meta/ledger/XML/74/eight-helper evidence enables exact8-module offline-local serially, prefix `restart-packet3-task9-offline-local-n4`, new basetemp `.pytest-tmp-p3-offline-local-n4`, same frozen launcher/wrapper. Fresh original IDs/volumes/loopback ports59497/59498/63188/sourceQ-exited/filter/lane/absence checks remain mandatory. This is GO, not observed start/completion. No formal Spec/Quality/Delivery, source commit/push, wholePacket3 or runtime/production acceptance yet.

Task 9 final-n4 actual external launch (2026-10-09): WMI wrapper PID28872, runner9612, approved venv launcher34588 and its base-interpreter child32376 were independently observed alive with the exact final12-module argv. Runner launch is `2026-10-09T08:30:56.5242420+00:00` (16:30:56 Asia/Shanghai); external creation record hash `8B1F9A230A28EDCA1AACE425935865C4064EA63600380C4B4038C83F5A19B7AE`, ReturnValue0. The project pyvenv.cfg records D:/Anaconda as its home/base interpreter and include-system-site-packages=false; invocation remains the approved D:/python_self_agent/venv/Scripts/python.exe, not a separately selected system interpreter/test lane. Current N7/v11 source/helper/base/service bindings match the granted GO. No final exit/XML/completion yet; offline-local remains unstarted. Creation success/liveness does not imply test success.
## Task 9 N7/v11 final-n4 complete and offline-local-n4 started (2026-10-09)

The original unfiltered final12 modules completed naturally: 430 passed in 4126.05s, zero failures/errors/skips; XML and passive ledger each bind430 exact collected cases. Actual pytest/meta/ledger/runner/detached-child exit codes are0; source74 and eight-helper hashes remain frozen, HEAD d9ddeb7 unchanged. This result does not include prior diagnostic or incomplete runs.

Root's governed final snapshot actually exits0: `packet3-task9-final-n7-v11`,247 payloads, binding SHA `A052EA6CF4C780F8DD797074C18F65D35A35F6ED9032517E931B6B8ECB594E9F`, index `60251195C81290E289C1794492052EF73DDB5344B483A6B8529456F51D651A2D`. The v11 phase verifier actually exits0 with status `complete_full_pass_evidence`, problems[]: `restart-packet3-task9-final-n7-v11-phase-check.json`, SHA `E42F295E1947B4D09D0FA63BC444FC04D13BA691756BB9692F52F8F03DD695DD`. This is completed first-phase evidence; formal overall acceptance remains pending.

Sol independently checked that gate and used the frozen WMI n4 launcher to start the exact8-module offline/local command serially, prefix `restart-packet3-task9-offline-local-n4`, new basetemp `.pytest-tmp-p3-offline-local-n4`. Actual external creation record SHA `A0F567373BCF76890074F64FED6BA53E74F9AC932922DEE617D7A0B9029D0BD0`, ReturnValue0, wrapperPID21928; offline tests are running and have no completed result yet. Independent Astra code/contract preparation found no new blocking and binds74/74, but explicitly waits for complete two-phase evidence and root full actual-check before formal Spec/Quality. No source commit, Delivery, wholePacket3, runtime/main or production acceptance is claimed.

Task 9 offline-local-n4 partial failure observation (2026-10-09): the running console produced two F markers after its first34% line. The run remains active, source/helper bytes are frozen, and no full traceback/XML/completion has yet been observed. The final-n4 complete430-pass evidence is preserved separately; two partial markers do not establish final case totals or a failure cause. Sol is waiting for natural completion and will provide credential-redacted failure details. Source commit/Delivery and overall formal acceptance remain withheld pending actual second-phase evidence and independent review.

## Task 9 N7/v11 offline-local-n4 complete failed evidence (2026-10-09)

The exact8-module run completed naturally with207 collected cases:204 passed,2 failed,1 setup error,0 skipped,1438.77s. Actual pytest/meta/ledger/runner/detached-child exits are1; source74 and eight-helper bytes stayed frozen and RunnerError is null. Original final-n4's430-pass result remains separate. Root governed failed snapshot actually exits0: `packet3-task9-offline-local-n7-v11-failed`,247 payloads, binding `42A047F0D4767E8D79F30782F10AB62FEAFAFDEB7993B61FE9A3B0F27CFE83EB`, index `4BFCEF444AA323C8DD440C94D94C08B3F3C8F72D49C11667BC2B62227DE560B9`.

Root's unchanged v11 phase verifier actually exits1, status `incomplete_or_incoherent`, sole problem `ledger_phase_count_incomplete`; output `restart-packet3-task9-offline-local-n7-v11-failed-phase-check.json` SHA `084D4EBE88A09F0B78FFBA711207878793CC6E2E25C5D8E543C05D1763C38759`. Its observed ledger207 contains setup206 passed/1 failed, call204 passed/2 failed/1 missing, teardown207 passed. Preserve the checker refusal rather than labeling it a coherent-failure PASS or changing helper rules to obtain success. Overall formal acceptance is withheld.

Root read the full credential-redacted Sol failure analysis `D36A6C341E82E7B8F6064F59BE386BC901DE8AB39B6E18C405C0D002F42ECAEA` and audit `B92CB08077754EA392C8F68004560C06E0F9775181FFE57D97988B8875E7FB99`. Both History collision parameters fail at preflight helperline108 -> witnessinsert225 before task/oracle because the cursor is IDLE. The rev12 migration test errors at document-publication fixtureline60 -> coordinator93 -> gate630 before test assertions because rev14's gate table is absent. These actual failures are fixture preparation; no zero-write or migration preservation PASS is inferred.

A finite proposal is under independent Astra review: activate the existing preflight fixture transaction before ordinary guarded witness insertion; move legacy rev12 lease preparation/release to real version-correct SQL while retaining the current API for latest schemas. All four collision parameters, complete zero-write oracle and upgrade/head/witness assertions must remain. Those two already-listed test paths are baseline-unowned and need explicit Task9 ownership amendment in a new baseline plus a new74-input freeze before editing/reruns. Source/migration/production guards remain unchanged. No commit/Delivery/wholePacket3 acceptance is made.

## Task 9 finite two-fixture ownership amendment (2026-10-09)

Root read the complete independent Astra v2 report `1ED05C45039FE08181354F46015610E7855D43C9C96E4B78271D3B65FD392479` and parsed audit `CA8C657BAF5B3F1550988270B331C8B861C6001ECA7E692418339481E762878B`: FAILURE_CAUSES_CONFIRMED / FINITE_TEST_REPAIR_READY_PENDING_ROOT_OWNERSHIP, no new blocking. Only the complete v2 report/audit is accepted review evidence; v1 audit is preserved as a reviewer-normalization diagnostic. Installed pytest confirms a failed setup has no call phase; the original v11 checker refusal remains unchanged.

Root authorizes a bounded ownership amendment for two already-listed74 inputs: `tests/integration/test_import_memory_preflight.py` may add only explicit BEGIN before the existing guarded witness insertion in `_publish_old_history`; `tests/integration/test_import_document_publication.py` may read revision before lease acquisition and use real rev12 SQL lease seed/release with exact user/owner/UUID token/positive version/database-clock expiry and user-first locking, preserving latest-schema coordinator API behavior. All original collision parameters, complete zero-write oracle, migration target/timing/head/witness/source assertions remain; no product/migration guard edits, early gate creation, skipping, cleanup or bypass is authorized.

Luna is preparing a new baseline-v8 with identical74 order and realBASE values, changing only these two task9_owned_test flags plus explicit provenance. Root must verify that candidate before serial Sol implementation GO. Sol must then freeze full74 N8 with only these two test-file deltas relative to N7,17 total actual changes against BASEd9, and obtain independent finite source review. New diagnostics and full unfiltered formal runs need fresh exclusive namespaces and appropriately reviewed helper bindings. Final-n4's430-pass N7 bytes remain historical successful evidence and cannot alone accept changed N8 fixture dependencies. No source commit/Delivery/integration/runtime/production acceptance occurs at this amendment.

Task 9 baseline-v8 actual verification and finite implementation GO (2026-10-09): Root's first actual check exits0 against initial candidate hash A35506CC1C268171AEF3B4C9B96C3D89362648C654D8EAD445B1CFC41DD1BA40 (root-check CDE9DFB402C594A7D3005F1C416CDF6D1C6C746AD52459132B3C0F67A70D5EB7). Luna subsequently amended only candidate created_utc to UTC serialization and rebound the audit, so that first check is retained as historical and is not current approval. Sol confirmed it had not edited any file and paused on the hash mismatch.

Root then independently reran the baseline comparison with an explicit expected manifest hash and a new exclusive result. Actual exit0: final baseline-v8 `1ED3DC4E86D2F051C9CC8CB4B2E1043BC85F79BA424E26AD122E26F61B9E3810`, final-root-check `6C483DA37B75FE513C7EF2509266A91834AC57B284CF2F31FE49B1640A66769E`. All74 ordered paths/71actualBASEblobs/3absences/other baseline row values and metadata remain; only the two approved ownership flags and allowed provenance/time change, and all74 current raw bytes still equal N7. Root restored serial Sol GO solely for the recorded two-fixture edits and full74 N8 freeze bound to this final baseline. No diagnostic or formal pytest retry, source/helper expansion or commit is authorized at this GO. Frozen outputs must not change after their hash handoff.

## Task 9 N8 two-fixture source freeze (2026-10-09)

Sol completed only the two authorized test-fixture repairs and froze74 inputs. N8 manifest `55E8FCB0C2F4FF6877D3A0DF3FFA1FEA8255228420C64DE957160308C47B3DF9`, index `62273BBE2CEA7C8477CDEEE4CB6C8E949DCE2CAEAAC80A1D3813D12E76C71119`, handoff `02C80841C39F037F3D390F76D8BE8F67A56D74079E5E833E1BC395D2D63D5058`, bound to final baseline-v8 `1ED3DC4E86D2F051C9CC8CB4B2E1043BC85F79BA424E26AD122E26F61B9E3810`. Original N7 and failed evidence remain preserved.

Root read the full handoff and both exact diffs, then independently ran `check_task9_n8_fixture_root.py`: actual exit0, result `restart-packet3-task9-n8-fixture-root-check.json` SHA `76D2ABD46A6FED7EA4C89E77004306A39B9C6BCAFA61E020B6DE5DA6878BF494`. All74 ordered current files and both74 raw copy sets match their manifests; only the approved two tests differ from N7, exactly17 owned paths differ from BASE. All product/migration bytes are N7-identical. This is source binding, not test PASS.

Independent Astra finite implementation review is in progress. Sol may prepare an exclusive six-case diagnostic candidate and preview but cannot run pytest until root's reviewed GO. Luna may prepare preserved v12/n5 formal helper copies with only coherent version/baseline/hash/phase namespace substitutions; original v11 and launcher remain frozen. All formal/helper/review acceptance and Task9/Packet3 Delivery remain pending.

Task 9 N8 independent finite source review and six-case diagnostic GO (2026-10-09): Root read the full independent `n8-fixture-review-astra-v1.md` SHA `40F07EF7B8F520C0EA6A6E8234549A6ECDB65E14903C160C9E15611D193B110B` and parsed its audit `96C70094E2DD719E8BA05CEB256D8B5BBA5308726D88B71C9FB587F97802F85F`, READY_STATIC_FIXTURE_DELTA_DIAGNOSTIC_PENDING, problems[]. All74 raw/order/copied/BASE bindings and the exact2 fixture delta/17owned changes independently passed; all other functions/decorators and original assertions remain.

Root read the complete new diagnostic runner delta, report and actual exit0 preview, and confirmed `run-p3-task9-n8-fixture-green-sol.py` SHA `65F9E6D3415B90FCEC3EFE21989C85B3F247AAD01023CEED6A54B1D444B93674`. GO only for its exact6 nodes: the actual3 failed/error nodes, both originally passing episode collision parameters, and latest-schema strict-paired witness test. Prefix `p3-task9-n8-fixture-green-sol`, new basetemp `.pytest-tmp-p3-n8-fixture-green`, fixedvenv/N8/finalbaseline-v8/HEADd9/plugin/originalservices/filter/lane/absence/privatecredentials guards apply. This is execution GO, not completed tests; formal unfiltered tests still require new reviewed helpers.

v12 mechanical helper candidate is not READY: independent Astra found its old finite test allow-lists still exclude the two newly owned fixtures and would reject baseline-v8. All v12 original candidate bytes remain preserved; no helper main or formal run has executed. A narrowly reviewed allow-list correction in new preserved helpers is required. Passive plugin remains the eighth bound input (seven helper scripts plus plugin); the external launcher remains independently bound and excluded from that eight-input set.

Task 9 N8 six-case actual review accepted (2026-10-09): exclusive diagnostic completed6 passed/60.42s,0 failures/errors/skips, actual outer/pytest/meta/completion/ledger exit0; all18 setup/call/teardown reports passed, full74 before/after/current/source/HEAD/services equal. Root actual check `4E85523178A79DCFBF87F26B944D6D8C2111A2081CDEB7D2ACEC9EDEA3B92E89` exits0. Root read full independent actual report `FD085F6476E9B1CDC62E25E90ECE982E56ECA8CA9A7A69D202CD5E83992FC73B` and parsed audit `20A0D69232A6F13FAA749B7E674AE519FB504F0937EECEB9B4EF53049078A1DB`, PASS_DIAGNOSTIC_SIX_FIXTURE_CASES_ONLY/problems[]. All original zero-write and rev12 upgrade assertions completed, alongside both episode and latest-schema controls. Preserve the report's separate hash erratum `0E935A686625782AE4192FF64C266ED420D32812041002B9CB47162F41F3B3BD`: its prose accidentally transcribed a65-character ledger hash; actual audit and original ledger use the verified64-character FAD5AA360F4B84F19B8E1EFC7DEAF168EE86EFDD33A34CAFAEE370711E553790. Frozen original report/audit are unchanged; no outcome change.

Root read complete v12 blocking report `71E21F4226BAD490412F7178859183BAFC1F73887C4BBB2271058ABE5A4303FF`/audit `847CABE184F1694AAE5D6CB5F79A814C69B6FBE583F4375685C703AF1A0E4DA1`, and authorized new preserved v13 copies with only coherentversionrefs and precise additions of the2 approved fixture paths in three finite test allow-list constants. Every exact ownership equality/BASE/rawhash/source/migration/ledger/actualexit guard remains. New v13 freeze `6F96FEFA8BB5A65BF310BC2F3F247700A6B4461570CDFD9CA9E925564CC7F261`/audit `D9975D82A5FDBB9B07D3514B35070E20D0C29C35D4CB6B9DE06142E8F76CDCF5` is under independent review; no formal/helper main has run. N8 formal unfiltered12/8 phases remain pending, and no Task9 Delivery/commit/wholePacket3/runtime/production acceptance is inferred.

## Task 9 N8/v13 actual static binding and final-n5 GO (2026-10-09)

Root read complete v13 independent helper READY report `488A158DC16A34C85CD59ABFB9859AB6189BCE0FB29244546A5207C3593E15C9` and parsed audit `0DECA5C534F44D99E0AE849F7EA34EE5847B6992F3C55E730B1EA8D8151FA213`, problems[]. The exact3 test allow-lists now equal baseline-v8's13 authorized paths; every equality/actualexit/ledger/source/service guard remains. Separate external launcher SHA `BBBB119803C341C32231C560C8B40FD064239935ED39EA1A1A66C13321862316`; v13 detached wrapper `36C6CB0AF8BAF5855C15796A3A3760A7EECB6D2AB2ED8AB513313900C5CC0DDE`.

Root governed static snapshot actually exits0: `packet3-task9-static-55e8fcb0-v13`,235 payloads, binding `827F01262AC9F1D3900FF0076A7BDEECBA3122BD6E25D37A6C0066DBBA8CE667`, index `B2B085261371712379F952476CEE3D3E6CE8EFBB84ADF4EB71A43C3382C3C4A8`. Root checker first exited1 before output because its exact string SHA comparison received uppercase rather than lowercase audit SHA; source/snapshot/helper bytes were not changed. Rerunning the same checker with the actual lowercase hash exits0: `restart-packet3-task9-n8-v13-static-root-check.json`, SHA `76ED52DA4A2E764EFE6CD28EDD1B5BFC428A36D52F4C6A7781D4267F1317F2C4`. All74 current/copied paths,71realBASEblobs/3absences,235payloads/eightfrozen script inputs and exact17owned changes verified.

Root grants serial Sol execution GO for original unfiltered12-module final command under prefix `restart-packet3-task9-final-n5`, new basetemp `.pytest-tmp-p3-final-n5`, via reviewed `launch-restart-packet3-task9-wmi-n5-v13.ps1` and v13 detached wrapper above, frozenN8 manifest `55E8FCB0C2F4FF6877D3A0DF3FFA1FEA8255228420C64DE957160308C47B3DF9`, finalbaseline-v8 `1ED3DC4E86D2F051C9CC8CB4B2E1043BC85F79BA424E26AD122E26F61B9E3810`, BASEd9. Only actual clean completed final evidence (XML/ledger/meta/runner/wrapperexit0/all74/eighthelpersstable/zeroerrorsfailskips) enables the exact8-module offline-local command serially under `restart-packet3-task9-offline-local-n5` and `.pytest-tmp-p3-offline-local-n5` with the same frozen inputs. Preflight must freshly confirm lane/filter/config/output absence, original IDs/volumes and loopback59497/59498/63188 readiness, protectedsourceQexited; no service alteration or source/helper edits. This is GO only, not observed launch/completion or formal/Delivery/commit/wholePacket3/runtime/production acceptance. All previous failed/incomplete/static/diagnostic evidence stays preserved.

Task 9 final-n5 actual external launch (2026-10-09): Win32_Process.Create ReturnValue0/ShowWindow0, wrapperPID19304 -> runner36992 -> approved venv launcher1576 -> its base-interpreter child35468 were independently observed alive with the final-n5 argv. External creation started `2026-10-09T14:31:43.7264004+00:00` (22:31:43 Asia/Shanghai); record SHA `8A5A6A049E6B05FA02ED74DEF0C3B42589BFBA6B51C015449C390DC6E18D0A3D`. N8/v13/finalbaseline-v8 frozenbindings match GO. A first pass marker is present; no full XML/meta/completion/final exit is available. Sol continues sole-lane supervision and may only advance exact8-module offline serially after complete clean final evidence. Creation/liveness is not acceptance. HEADd9 and no commit/push/wholePacket3/runtime/production acceptance.

## Task 9 Oct10 continuation: final-n5 completed, offline-n5 interrupted, isolated services restored

N8/v13 final-n5 naturally completed430 passed/3841.78s,0 failures/errors/skips, all recorded actual pytest/meta/ledger/runner/wrapper exits0, source74/eight-helper stabilitytrue. Full snapshot `packet3-task9-final-n8-v13` is present with247payloads, binding `01FBCB9E278FDE3EC7FEEF564BD9AAF7A95A515CA2E3E5B37B9323D9CEAAE40A`, index `BD30E7FAFC736C53C1362FD66D519D50018B3A2AAA5A8C269740AEA48F57C44E`. Its original outer snapshot exec session29280 is unavailable after continuation, so its final outer exit is not fabricated. Root freshly executed the unchanged v13 phase verifier against the preserved snapshot: actualexit0, status complete_full_pass_evidence/problems[], check SHA `4AB0E494704ABA172D8DE18D6D3F17D3B94E88FA0656029E5724A6AD0C2A4811`. Source remains frozenN8/HEADd9.

Offline-local-n5 was actually WMI-created at23:39:07 Asia/Shanghai Oct9, wrapper35616, externalrecord SHA `F8F950693970026345C61A8032EF02B9384E68561C6D7B00BEF5E6900A960C0D`. Oct10 observation finds no original wrapper or pytest process, partiallog34% followed byE markers, and no completeXML/meta/ledger/completion/sentinel. There is no actual final exit or full traceback; no exact completed count/error cause is inferred. Root exclusive interruption record SHA `1AC28BD5F3C0CAD1C445E0E8A37AB76870DB747B9E8D3E364A13BEDA5F6BE764` preserves all present hashes/absences and stopped dedicated-service identities. The first observer attempt matched its own PowerShell regex text and refused before output; filtering python process names correctly then actually exits0. All original n5 artifacts/basetemp remain; this run is incomplete, not acceptance.

Root restored only the same three existing dedicated containers/volumes. First start produced PG/S3 socket-binding refusal; Q started. Read-only excluded-port/listener checks did not establish a persistent conflict; subsequent serial starts of the same PG and S3 each actually exit0. No host network setting, port exclusion, source-Q, main service or data was changed by Root. Fresh observation `restart-packet3-task9-services-20261010-root.json` actually exits0, SHA `F073DCDB283610F176A02C84839E9CF2F081B576BF36CEB22200D1A7402893CC`: PG59497/S359498, targetQ now59539, original IDs/volumes, sourceQexited, PGready and S3/Q200, business_acceptancefalse.

Luna is authorized only preserved v14 mechanical helper copies with coherentversionrefs, exclusive final/offline n6 namespaces, fresh rootservice path/hash and Q63188->59539. All baseline-v8/all74/13ownedsets/actualexit/ledgerthreephase/argv/sourceguards unchanged. Independent Astra first reviews final-n5 actual evidence, then newhelperfreeze; no newrun until reviewed static binding and RootGO. Full formal acceptance/commit/Delivery/wholePacket3/runtime/main/production remain pending. N8 whole-feature preparation `9B6FA8F5A0677C4CC981830F243D6024CEC0323A2016C87EEFE05C351EDF738D`/audit `6A1288FDE183EEBC9C36FB1EECD1A564124A31B04BC3E4D860F0F9868E2D74E4` remains code/contract preparation only; wholefeature32raw differences vs oldN6's31 are exactly explained by the newly changed preflight test, not unexplained scope.

## Task 9 N8/v14 actual static binding and final-n6 GO (2026-10-10)

Root read complete final-n5 independent actual v2 report `51FFB5F77EC9C413514DD4595936F8272AA6AA9357ECC7021A71FA0535BCA642`/audit `68D32367B7830247DF29AD71CEB4196911F76076AF28DF9D9F0222ACACD338F0`, PASS_FINAL_PHASE_ACTUAL_ONLY_OFFLINE_UNACCEPTED/problems[]. Source74/71BASE+3absence/17owned/247payload/eighthelpers/430unique/1290passed/exits0 verified. The preserved v1 audit's XML properties heuristic was a reviewer-only diagnostic; v2 separately corrects it without changing original evidence. Unknown historical snapshot outer exit remains unknown.

Root read complete v14 independent helper READY report `173D3FDF87AC9924E19E1111D13A34CD0DAFC8F323D27881D094818D07C3FF93`/audit `5A6000A38AAFE09B9613E2770EB5AACF25DAB42635FDCFDA0E79DC196662024A`, problems[]. Seven helpers plus separately bound launcher have only reviewed version/n6 namespace/fresh service-observation/Q59539 substitutions; 13owned testpaths/allstrictguards remain. Luna's preparation AST used system Python, so Root repeated five AST parses using the fixed project venv and Astra's independent full checker used that same venv, both actualexit0. This is no substituted pytest lane. Frozen old diagnostic prose in two external-launcher errors remains nonblocking; actual guards enforce n6/v14.

Root governed static capture actually exits0: `packet3-task9-static-55e8fcb0-v14`,235payloads, binding `095CFF225F44A708DDF580DB1C7CF31729B8243A249D1A54831D93DB493955E2`, index `37FF6921E0FC9D287A633E540C887BE6F811BAEF7FC4DAEDAAAF320C8BFD7932`. Root actual full235/all74/current+copies/71realBASE+3absence/eightfrozeninputs/exact17owned check exits0: `restart-packet3-task9-n8-v14-static-root-check.json` SHA `82F6C3294D888E18159CA3A028B9FD392687FBB0D7DFB9428EE05958E214E5E4`.

Root grants serial sole Sol lane GO for original unfiltered12-module final command via `launch-restart-packet3-task9-wmi-n6-v14.ps1` SHA `00E3FE7DF9016E3E2AF334D3BFDC17FC5A01E16BEC997D15261C0AF8C56E0641` and detached v14 wrapper `59A1B9F568E672C82337D851DA6653987427B3DF327CD7FCAEBD97BB6101058A`, prefix `restart-packet3-task9-final-n6`, new basetemp `.pytest-tmp-p3-final-n6`. Bind frozenN8 `55E8FCB0C2F4FF6877D3A0DF3FFA1FEA8255228420C64DE957160308C47B3DF9`/finalbaseline-v8 `1ED3DC4E86D2F051C9CC8CB4B2E1043BC85F79BA424E26AD122E26F61B9E3810`/HEADd9. FreshoriginalIDs/volumes/loopback59497/59498/59539/sourceQexited/readiness/filter/lane/outputabsence checks apply. Only complete clean actualfinal XML/ledger/meta/runner/wrapper0/zeroerrorfailure skip/all74/eighthelpersstable permits serial exact8-module offline-local under `restart-packet3-task9-offline-local-n6`/`.pytest-tmp-p3-offline-local-n6`, samefrozenlauncherwrapper. This is GO not observedstart/PASS; original n5 incomplete artifacts remain. Formaloverall/commit/Delivery/wholePacket3/runtime/main/production acceptance remains pending.

Task 9 final-n6 actual external launch (2026-10-10): WMI creation ReturnValue0/ShowWindow0, externalrecord SHA `0F1E40143F766D97652F27093E3D4AC331533F89CE98F9EEB715F52E0EFC9F40`, started `2026-10-10T04:11:20.0085048Z` (12:11:20 Asia/Shanghai). Root independently observed wrapper3404 -> runner29272 -> approvedvenvlauncher10712 -> base-interpreterchild35616 and a first pass marker. PID35616 is now a new Python process in this n6 tree, not the previous vanished n5 wrapper; process identities must include name/creation/argv/ancestry, never bare PID reuse. N8/v14/finalbaseline-v8 binding stays frozen. No completeXML/meta/ledger/completion/sentinel/final exit yet; offline-n6 unstarted. WMI creation/liveness is not PASS; sourcecommit/Delivery/wholePacket3/runtime/main/production remain pending.

## Task 9 N8/v14 final-n6 complete phase evidence (2026-10-10)

Original unfiltered twelve-module final-n6 naturally completed at 13:10:58 Asia/Shanghai: 430 passed in 3556.91s, no failures/errors/skips. Actual pytest/meta/ledger/runner/detached-wrapper exits are zero. All 430 unique identities and 1290 setup/call/teardown outcomes passed; frozen N8 source74 and eight v14 helper inputs remained stable. HEAD remains `d9ddeb7f309df326c8d2c7d79e37e8969e880eed`; this is uncommitted source verification, not Delivery.

Root governed snapshot actually exits0: `packet3-task9-final-n8-v14`,247payloads, binding `97C03859A4D5C07BC7FFD34E5DE3127433B76C84B7953425BDA9017C8723BC87`, index `7EF58FD7A69C1C5CFF4B1621C5C4E2E48C743565DBB48EAEB44A2574A350E153`. Actual unchanged v14 phase verifier exits0, status `complete_full_pass_evidence`, problems[]; `restart-packet3-task9-final-n8-v14-phase-check.json` SHA `23B77A284F7189F1483AA9CEF3AF1EAAFB6721109374880B9958080B0029429B`.

Root reconfirms the already authorized serial Sol-only offline-local-n6 GO after fresh service/lane/input checks. Offline completion, full checker, independent formal Spec/Quality, source-only commit binding, actual Delivery and whole-feature final integration review remain pending. Original failed/interrupted and earlier successful runs stay separate; no counts are summed. Default native/API/bootstrap path remains OFF; no main or production acceptance is claimed.

Task 9 offline-local-n6 actual start (2026-10-10): after the complete final-n6 gate, Sol launched the frozen v14 detached wrapper through the reviewed external WMI launcher at 13:37:41 Asia/Shanghai, ReturnValue0/ShowWindow0, wrapper26988. External record SHA `7663269AEC9C9CC8581C77834DE6408FE61A3E0CFCB0298842D7DC2786E16DFA`. The actual runner launch binds the original eight modules without filters/selectors, `.pytest-tmp-p3-offline-local-n6`, frozen N8/finalbaseline-v8/HEADd9 and original dedicated PG59497/S359498/targetQ59539. Protected sourceQ remains exited. Root observed pass markers, not a complete result. Final-n6 remains complete430 PASS; offline completion, full checker, formal Spec/Quality, actual source commit/Delivery and whole-feature acceptance remain pending.

## Task 9 N8/v14 complete two-phase actual evidence (2026-10-10)

Original unfiltered offline-local-n6 eight-module run naturally completed at 13:58:26 Asia/Shanghai: 207 passed in 1231.45s, no failures/errors/skips. Its 207 unique XML/ledger cases and 621 setup/call/teardown outcomes all passed, and all actual pytest/meta/ledger/runner/wrapper exits are zero. Source74/eighthelpers/HEADd9/dedicated services remained stable; protected sourceQ is exited. Sol actual handoff `p3-task9-offline-local-n6-sol-actual-handoff.md` SHA `B2FB124E30FC48EE5F6A312AC4D30090A3D2C2F00DEAF1005F59416FB8206857`.

Root governed offline snapshot actually exits0: `packet3-task9-offline-local-n8-v14`,247payloads, binding `0ED78A39E92D4B07EA9EFD207A836AEB66D85D03E52C514AD5B1DDE09EA4EE20`, index `F540EEFEB82F15B9761C21983B4C9B0CB99C9B2DC697982CB59FCEE17F7C9A5E`. Actual unchanged v14 phase verifier exits0, complete_full_pass_evidence/problems[]; phase-check SHA `5BAB1FE826F55711177815497DA080E2B106C635CF2C7A3347A67945D04B1289`.

Actual unchanged v14 full verifier exits0, complete_two_phase_full_pass_evidence/problems[], 637 unique cases across the current final430 and offline207 runs, no historical or diagnostic cases added. `restart-packet3-task9-n8-v14-full-check.json` SHA `49E43A39B32D679B68526BED40D6897A81AF69A7329853393D2CD31BA4ED11EA`. The first root full-helper invocation passed uppercase baseline hex and refusedexit1 before output under its lowercase regex; corrected lowercase arguments then passed on unchanged helper/source/evidence. Its stale v7 error wording is not the actual baseline binding. No test was repeated or guard changed.

Independent formal Astra Spec/Quality and actual source commit/Delivery remain pending. Whole Packet3 and whole-feature final integration acceptance are not inferred from this evidence. Default native/API/bootstrap remains OFF; no main/runtime/production acceptance follows.

## Final Packet 3 implementation handoff (2026-10-10)

- Status: done; independent-branch opt-in library delivery only.
- Commit: Task7 `686ec5279c44bb01324c6154d819af5af41d4b9b`, Task8 `6622a1ed544b87a0f93df9161e007f2c3371025b`, Task9 `5d0e66a88d3f9d39231c757f3b2d07768fe51cb9`.
- Files changed: exact Task9 seventeen paths are recorded in `restart-packet3-task9-n8-v14-5d0e66a-commit-binding.json`; all74 tested/reviewed/committed raw inputs match, soleparentd9. Source-only commit contains no docs/runtime outputs.
- [x] Bounded queue/lease and immutable `_15` split: accepted Task7 source and303-case review.
- [x] Exact proof acknowledgement, paired permanent revocation and bounded hold/retry: accepted Task8 source and160-case review.
- [x] Remaining direct mutators, fresh liveness/bookkeeping, ordinary transaction admission, additive `_16` and default-OFF private Worker seam: Task9 formal Spec/Quality and actual-commit Delivery PASS.
- [x] Original current combined final12/offline8 commands:430 passed3556.91s /207 passed1231.45s, zero failures/errors/skips,637 unique identities, all1911 setup/call/teardown outcomes passed, actual exits0. Earlier packet/diagnostic/historical results are not added.
- [x] Fresh migration and preservation/rollback assertions completed in the original final command; scoped committed whitespace check exits0 using `core.whitespace=cr-at-eol`.
- [x] Expanded175-input accepted-tree context rebound:69raw equal/106unchanged checkout EOL-only; original inventory and all-mutator semantic review retained. No native distributed writer was enabled.
- Spec/Quality report SHA `9690323E2DB51CB72844F6D6BBBA5259E8677DD31E1A8A6E5CD7CF4448DE7386`; audit `1776C511A8DC9668600571EF25A6868CF2D7DF70EA562A8E41D9C55FD8A523A9`.
- Delivery report SHA `43F5B9773DFE97AA5824EB8FABAA3FD1B2E9BA88760FAFB7E35BFFBB7B104071`; audit `25758721D6534E591D8D0846E5EF2371D7444B9864C261770EDA6DB5432F43FF`.
- Actual commit binder SHA `FD1B768CF25C30B0ADBB450BA848787187C3D98668948D16CD7EAD0ABCEEA79F`; expanded175 check `B353CF772FF34D7D3744A661E67DFB70094FE912A455B7360CC99B207F26EF85`; full current two-phase check `49E43A39B32D679B68526BED40D6897A81AF69A7329853393D2CD31BA4ED11EA`.
- Deviations: finite Root-approved source/fixture ownership amendments and original failure/correction history remain above. Historical incomplete offline-n5 stays incomplete; no artifact was overwritten or count transferred.
- Residual risks: native composition/authenticated restart journeys, retained business migration, historical vector provenance, compatible-image rollback and production cutover require separate acceptance. Historical text/metadata are retained; vector recall remains pending proof. Protected sourceQ/retained data remain untouched.

## Whole-feature final integration accepted (2026-10-10)

All three packets are done. Root final integration review `FINAL_INTEGRATION_REVIEW.md` SHA `62F5A236FAA3237A2FD3D0C94EE9F3981ABEEAF4EFA0E7FFA54BA5F280856D51` accepts the approved private library feature at source `5d0e66a88d3f9d39231c757f3b2d07768fe51cb9`, whole-feature BASE `bc3176387ec4e41d452c5cac6e07bce1ba5fbc20`. Independent Astra final integration report SHA `C9215CBB6EA9E20873EB548237A48E62E1B286DDF9CE92B2E0551C98C84897BF`, audit `01B030AF22309949E544B19F38BF79BCA70DDA779B615308F7B18E463DA63489`, PASS_SCOPED_WHOLE_FEATURE_FINAL_INTEGRATION/no blocking corrections.

Independent review verifies actual74sourceblobs/32whole-featurechanges/exact17Task9changes/175context/all228+247+247+235payloads/637currentcases/1911passedphases/allactualexits0 and allacceptedancestors. Semantic review reuse is explicit and source-bound. Native/API/bootstrap remains OFF; authenticated business/restart, retained data/provenance, compatible-image rollback, main and production acceptance remain separate. No runtime activation or source-Q/retained-data change follows.

The next authorized action is a documentation-only descendant commit containing these four coordinator records and the exact reviewed final integration document, followed by branch push and fresh remote-head verification. This record does not yet assert a completed push; actual push output and fresh ls-remote will be preserved separately.
