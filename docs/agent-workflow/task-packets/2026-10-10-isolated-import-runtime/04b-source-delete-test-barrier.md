---
id: "2026-10-10-isolated-import-runtime-04b"
title: "Make source-delete-before-intent regression deterministic"
status: "done"
parallel-safe: false
depends-on: ["2026-10-10-isolated-import-runtime-03"]
base-commit: "c2e7d18cdcc5e834be6174059ddf89b76d2d8bf3"
owner: "GPT-6 Sol High"
---

# Task Packet: deterministic source-delete rejection

## Goal and delivery context

Task04 final regression n3 actually exited1: 319 passes and one failure out of320, without errors/skips. A deleted source correctly causes ImportDocumentPublicationError before intent reservation, but the existing test accepts only its subclass ImportMemoryPublicationError or two other refusal types. Its fixed sleep also permits different legal rejection paths. Make the source parameter deterministically exercise deletion committed before document planning, assert the exact lower-layer refusal, and execute the existing no-evidence/no-gate checks. Task04 and04a remain unaccepted pending final evidence.

## Non-goals

No production behavior, exception hierarchy, SQL, leases, storage, Worker/provider, migrations, API, restart or production changes. No global broadening of accepted exceptions or edits to unrelated tests.

## Relevant files and current interfaces

- tests/integration/test_import_publication_recovery.py:2274: parametrized source/audit deletion-before-intent test; currently submits issuer during an uncommitted DELETE, sleeps0.2s and accepts PublicationEvidenceError, ImportMemoryPublicationError or ImportLeaseLost. Its last two assertions require evidence and gate absence.
- app/import_document_publication.py:31,107,136,222: ImportDocumentPublicationError(RuntimeError); _plan_document calls _task_and_source in a fresh transaction and refuses a missing pinned source with 'Pinned import source is missing'.
- app/import_memory_publication.py:40,642,988: ImportMemoryPublicationError subclasses the document exception; _issue_live_publication completes _plan_intent, including documents._plan_document, before reserving intent.
- Event and monkeypatch are already established test seams in the recovery file. Existing memory_publication fixture owns disposable PG/S3/Qdrant setup and cleanup.

## Prerequisites and base state

Task03 is done. HEAD c2e7d18 is the Task04 documentation descendant. Task04 four candidate files and04a evidence source are uncommitted but frozen; no concurrent writer or pytest lane is active. Read PROJECT_KNOWLEDGE and repository workflow. Original recovery test raw SHA256 is5b6b22e429d4a7943e7f5b1986bfce9c702d9063111b00b4d2cfbc006c7e9898. Failed n3 XML47fd0e899c1f8f1d69937677e5da16478a14b5e5d1f0ca55a454cbe5720a90a1 and logb2b094a87eb97740e7c0f943be0160004c90f7beb997b78878d8bea50a6a05cb remain immutable.

Use D:\python_self_agent\venv\Scripts\python.exe. Root-approved private runner freshly verifies exact dedicated PG/S3/target-Q container IDs/names/volumes/health; source-Q stays exited. Five fixture credentials enter child environment only. Root approves the sole serial pytest lane; no self-started services.

## Explicit change boundary

Sol High may modify only tests/integration/test_import_publication_recovery.py: import the existing ImportDocumentPublicationError, add monkeypatch to this single test, and change only its source branch synchronization/exception assertion. Audit branch mutation, fixed-delay behavior and exception tuple remain unchanged; both final no-write checks remain.

Root owns this packet, REVIEW/progress and ignored runners/baselines/checkers. All production files and four Task04 candidate files are forbidden to change. Preserve these15 pre-existing dirty paths exactly: app/coordination.py, app/document_library.py, app/history.py, app/import_models.py, app/import_worker.py, app/memory_repository.py, app/qa_deletion.py, app/qa_persistence.py, app/qa_repository.py, app/recovery.py, migrations/versions/20260926_01_business_schema.sql, migrations/versions/20260928_09_import_leases.py, tests/conftest.py, tests/test_import_worker.py, tests/test_user_mutation_coordination.py. No staging, commits, pushes or old-output overwrites by the implementer.

## Contract and required behavior

For source only, wrap the fixture service.documents._plan_document instance method. Signal an entered Event immediately before the original call and block on a release Event with an explicit bounded timeout. Start the issuer and require barrier arrival; execute and commit source DELETE after arrival; then release and call the original method. Require ImportDocumentPublicationError matching exactly '^Pinned import source is missing$'. Do not synthesize the production refusal in the wrapper. Ensure release is set in finally before executor context exit on all assertion/transaction failures. Keep future.result timeout and explicit pending-future assertion while barrier holds. Audit continues along its existing path and accepted tuple.

No publication intent or issuance may precede the source deletion commit in this deterministic branch. Both evidence/gate absence assertions must execute after rejection for both parameters. A barrier timeout must fail explicitly, without hanging or silently accepting another path.

## Acceptance criteria

- [x] Astra independently approves this contract and exact source-only test diff.
- [x] Only allowed test changes; five Task04 candidate/corrective source and test hashes and15protected paths unchanged (four Task04 files plus04a evidence source; no new production file).
- [x] Both parameter cases pass on real dedicated services, zero failure/error/skip, with post-error no-write checks executed.
- [x] All six regression files pass in a fresh run,320unique cases, actual pytest/wrapper0, zero failure/error/skip.
- [x] Before/after/current raw hashes, XML/log/runner exit and resource records are independently bound; historical failed evidence is retained.
- [x] Final review explicitly identifies this one test-only superseded input. Prior corrected Worker unit53/real19/full72 and facade41 source evidence remains historical with unchanged production hashes; their snapshots must not be rewritten or claimed byte-identical to the new regression test input. No unnecessary rerun of unaffected stages merely to replace hashes.

## Test and verification commands

After root-reviewed runner GO, from repository root with verified child-only fixture env:

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/integration/test_import_publication_recovery.py -k raw_dependency_delete_wins_before_intent_and_issuance_fails --basetemp=.runtime/pytest-isolated-runtime-p4-delete-n1 --junitxml=.runtime/isolated-import-runtime/p4-delete-n1.xml
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/test_import_document_preparation.py tests/app/test_import_publication_worker_adapter.py tests/integration/test_isolated_import_tenant_selection.py tests/integration/test_postgres_import_leases.py tests/integration/test_import_publication_proof.py tests/integration/test_import_publication_recovery.py --basetemp=.runtime/pytest-isolated-runtime-p4-regression-n4 --junitxml=.runtime/isolated-import-runtime/p4-regression-n4.xml
```

Expected two focused cases and320regression cases with zero failures/errors/skips and actual0. This is not PID-restart, whole-plan or production acceptance.

## Stop conditions and handoff

Report a reality conflict if current types/stage order differ, the barrier cannot be reached before deletion, scope expansion is needed or the assertions cannot prove the stated order. Do not improvise production changes.

Sol handoff must report exact allowed diff/rawSHA, bounded-thread behavior, commands and actual exits/unique IDs/XML/log hashes, unchanged production/protected bindings, explicit test-only baseline exception, retained failures, deviations and remaining later gates. Root/Astra finalize acceptance; implementer does not markdone before all verification.

## Independent ready review and assignment

Astra High p4-delete-plan-astra-n1.md SHA2569609807ee7ae2f01150047ab69a21f09ee8242db075c5a4c5194caa16dd0db13 reviewed the draft contract SPEC PASS / QUALITY PASS, with no blocking findings. Root records that ready gate passed and assigns Sol High only the specified test change. Statusin_progress is assignment bookkeeping; all pytest still requires root-approved runner GO and exact diff review.

## Accepted handoff

Done: only source-parameter deterministic Event control and precise document exception assertion plus import/monkeypatch fixture changed. Audit path and common absence assertions preserved. Final testSHA7e9741f34e2de29e932f8191de4d1d73de3e4fda0d2a378e866d81780fffec64. Focused delete n2 actualpytest/wrapper0,2pass0FES; full regression n4 actual0,320pass0FES, XMLf1f9ea0ac3c05e34754c25514635602ac3c0cf6d9dbff63d95be65b30d651473/logd8ec7064493792dcbfb479a4ecbc3c8f334c5274904e9f127c5b9acb15c38d28. Source/production/protected hashes unchanged except the explicit one-test supersession. Astra final SPEC/QUALITYPASS report SHA1c4e731684a14931cee88a18917b41b1b27273ead4202ea772146f7f69dd0b02; Sol reconstructed handoff/delivery details recorded in packet04. Failed n3 and preflight n1 retained. Root owns scoped Git chain; no later-stage acceptance.
