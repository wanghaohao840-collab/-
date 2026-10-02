# Plan Review: Restart-safe publication evidence

- Source plan: `docs/superpowers/plans/2026-10-02-restart-publication-evidence.md`
- Reviewed source commit: `bc3176387ec4e41d452c5cac6e07bce1ba5fbc20`
- Review date: 2026-10-02
- Verdict: accepted-with-revisions; revisions incorporated, Packet 1 ready.

## Repository evidence

Root read PROJECT_KNOWLEDGE.md, the approved structural design, repository workflow/templates and the complete implementation plan. Astra independently inspected C/B/document publication, user/task lease locking, snapshot/Memory-document mutators, capability issuance, the migration chain and integration fixture interfaces. Its local read-only report is `.runtime/distributed-cutover/restart-packet-1-plan-review.md`; no implementation or new test result is claimed.

Latest migration is `20260930_13`. The revision-12 setup in `test_import_document_publication.py` needs explicit test-only legacy seeding after adding a mandatory gate-table lookup. Production must not treat a missing authority table as an absent gate. Required Python is `D:/python_self_agent/venv/Scripts/python.exe`. Existing approval/plan/progress changes are coordinator-owned documents and remain preserved.

## Findings and incorporated revisions

- Existing ungated C/RAG library success remains compatible. The new durable publication entry cannot enable until Packet 2 proof/slots and Packet 3 all-mutator guards are reviewed.
- Exact live attempt identity alone cannot permit arbitrary snapshot/Memory writes. Packet 1 ordinary mutators deny every unresolved gate; a later private terminal integration must bind exact frozen values and issuance.
- Owned files and regression commands include the known legacy document-publication fixture. All test commands use the actual project venv.
- Private planning performs no authority or external write. `reserve_intent` is a separate user-first transaction.
- No remaining Packet 1 blocker. Numeric limits freeze after measured sizing; over-limit refusal remains legal, with no 100000-entry capacity guarantee.

## Accepted scope and packet graph

| Packet | Depends on | Parallel-safe | Outcome | Readiness |
| --- | --- | --- | --- | --- |
| `01-evidence-foundation.md` | Approved structure and completed sizing | false | Bounded codec, additive evidence/gate/reservations, guarded direct entries, read-only plan | Ready; explicit owned paths and commands in packet |
| Packet 2 | Packet 1 exact implementation acceptance and stable interfaces | false | Live write-once publication slots and detached strong proof | Draft until prerequisite delivered; root prepares self-contained packet then |
| Packet 3 | Packet 2 exact acceptance | false | Recovery-only lease/queue, permanent exact revocation and all remaining guards | Draft until prerequisite delivered; root prepares self-contained packet then |

Packet 1 has measurable goals/non-goals, verified context/interfaces, explicit prerequisites/base and owned/forbidden paths, exact verification and complete handoff. Later packet signatures remain proposed; they are not available implementation prerequisites. Packets overlap C files and must be serial.

## Integration verification and final gate

Use Packet 1's seven-module command in its own in-repo basetemp; evidence must include complete exit/log/meta/XML, exact source hashes, no required service skips and disposable resource identities. An implementer owns the sole pytest invocation. Astra reviews the exact implementation commit before downstream work.

After all implementation packets pass, root creates `FINAL_INTEGRATION_REVIEW.md` in this directory, assessing cross-packet interfaces, source/authority fences, immutability, compatibility, isolation, recovery schedules and combined regressions. No final acceptance is claimed yet. Runtime/API/bootstrap, authenticated restart journeys, historical vector provenance, compatible-image rollback and production cutover remain separate gates.
