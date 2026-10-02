# Plan Review: Restart-safe publication evidence

- Source plan: `docs/superpowers/plans/2026-10-02-restart-publication-evidence.md`
- Reviewed source commit: `bc3176387ec4e41d452c5cac6e07bce1ba5fbc20`
- Review date: 2026-10-02
- Verdict: Packet 1 exact implementation accepted at `bc6aa90`; Packet 2 promoted from independently reviewed draft after dependency binding; Packet 3 remains draft.

## Repository evidence

Root read PROJECT_KNOWLEDGE.md, the approved structural design, repository workflow/templates and the complete implementation plan. Astra independently inspected C/B/document publication, user/task lease locking, snapshot/Memory-document mutators, capability issuance, the migration chain and integration fixture interfaces. Its local read-only report is `.runtime/distributed-cutover/restart-packet-1-plan-review.md`; no implementation or new test result is claimed.

Latest migration is `20260930_13`. The revision-12 setup in `test_import_document_publication.py` needs explicit test-only legacy seeding after adding a mandatory gate-table lookup. Production must not treat a missing authority table as an absent gate. Required Python is `D:/python_self_agent/venv/Scripts/python.exe`. Existing approval/plan/progress changes are coordinator-owned documents and remain preserved.

## Findings and incorporated revisions

- Existing ungated C/RAG library success remains compatible. The new durable publication entry cannot enable until Packet 2 proof/slots and Packet 3 all-mutator guards are reviewed.
- Exact live attempt identity alone cannot permit arbitrary snapshot/Memory writes. Packet 1 ordinary mutators deny every unresolved gate; a later private terminal integration must bind exact frozen values and issuance.
- Owned files and regression commands include the known legacy document-publication fixture. All test commands use the actual project venv.
- Private planning performs no authority or external write. `reserve_intent` is a separate user-first transaction.
- No remaining Packet 1 blocker. Numeric limits freeze after measured sizing; over-limit refusal remains legal, with no 100000-entry capacity guarantee.

The later fixed-source Astra inventory (`.runtime/distributed-cutover/restart-mutator-inventory.md`, source `fa1c25169b4547e7db7d2875595afafb5662a2d4`) extends the main plan's explicit serial ownership to PostgreSQL import-control/store factories, source/task admission, bare witness/index helpers, candidate I/O admission, and unresolved source/task/audit retention. Packet 2's ownership now includes its evidence-slot repository and exact terminal admission in the four lower-level domain writers. Astra's follow-up found no scope blocker: these complete the approved all-mutator and exact-value contracts, not a new architecture or implementation PASS. Downstream packets remain draft pending their accepted prerequisites.

## Accepted scope and packet graph

| Packet | Depends on | Parallel-safe | Outcome | Readiness |
| --- | --- | --- | --- | --- |
| `01-evidence-foundation.md` | Approved structure and completed sizing | false | Bounded codec, additive evidence/gate/reservations, guarded direct entries, read-only plan | Done; Sol implementation `bc6aa90`, Astra exact Spec/Quality/Delivery PASS |
| `02-live-publication-proof.md` | Accepted Packet 1 `bc6aa90` and exact eight hashes | false | Live write-once publication slots and detached strong proof | Ready; Astra final promoted-packet check PASS, Sol High owns implementation |
| Packet 3 | Packet 2 exact acceptance | false | Recovery-only lease/queue, permanent exact revocation and all remaining guards | Draft until prerequisite delivered; root prepares self-contained packet then |

Packet 1 has completed verification and exact-commit review. Packet 2 freezes its new private signatures, type homes, wire hash, UUID conversion, issued callback and transaction/value admission, candidate operation checks, and detached proof service in a self-contained packet. These new APIs are to be implemented in Packet 2, rather than available baseline APIs. Packets overlap C files and must be serial.

## Integration verification and final gate

Use Packet 1's seven-module command in its own in-repo basetemp; evidence must include complete exit/log/meta/XML, exact source hashes, no required service skips and disposable resource identities. An implementer owns the sole pytest invocation. Astra reviews the exact implementation commit before downstream work.

The completed foundation's final corrective six-module run passed 158 tests (exit 0; 0 failures/errors/skips), with eight raw committed blobs identical to the frozen tested/reviewed files. Three additional unchanged modules were covered by the historical nine-module 161-pass run; this is not summed or attributed wholesale to the corrective source. See Packet 1's completed handoff for exact boundaries. Its three review findings were corrected and independently accepted; no runtime enablement follows.

Packet 2's draft review required explicit candidate-writer ownership, narrow `try_begin_committing`/`complete` ownership, actual issuer-backed private invocation and exact terminal admission, a required detached result/service, and a legal test-only later-retirement acknowledgement fixture. Sol incorporated them. Astra's final draft delta verdict is READY-CONDITIONAL at draft SHA-256 `909FFDD030F1B2E32C9A0C479FC83780384B386E0BECF06045309C2AF4918C87`; foundation types, owner-token UUID comparison and database-owning proof service were corrected. The promoted packet fills actual `bc6aa90` hashes, limits/schema/types and clean product-state inputs; its promotion check is recorded separately before assignment. Ordinary durable runtime entry stays unconditionally off through Packet 2; Packet 3 owns remaining lease/control/all-mutator/recovery/retention paths.

Astra's final promotion verdict is READY at packet SHA-256 `970062FA10CA7C5CEAB1115B53417CED3715388A377BC02C10E1B11215DB69AD` (`restart-packet2-promoted-ready-review.md`). Actual foundation hashes match 8/8, graph/dependency/owner are synchronized, and normalized content preserves the reviewed private contracts. This closes the promotion conditions only; Packet 2 has no implementation or test acceptance yet.

After all implementation packets pass, root creates `FINAL_INTEGRATION_REVIEW.md` in this directory, assessing cross-packet interfaces, source/authority fences, immutability, compatibility, isolation, recovery schedules and combined regressions. No final acceptance is claimed yet. Runtime/API/bootstrap, authenticated restart journeys, historical vector provenance, compatible-image rollback and production cutover remain separate gates.
