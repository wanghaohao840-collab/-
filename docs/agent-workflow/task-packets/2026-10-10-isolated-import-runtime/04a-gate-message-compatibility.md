---
id: "2026-10-10-isolated-import-runtime-04a"
title: "Preserve legacy unresolved-gate exception text"
status: "done"
parallel-safe: false
depends-on: ["2026-10-10-isolated-import-runtime-03"]
base-commit: "c2e7d18cdcc5e834be6174059ddf89b76d2d8bf3"
owner: "GPT-6 Sol High"
---

# Corrective packet: unresolved-gate message compatibility

## Goal and reality conflict

Task04 regression n2 executed 320 tests, with 304 passes and 16 failures (pytest and wrapper exit1, no errors/skips). Existing recovery tests require the legacy gate message, while accepted Task03 introduced the typed UnresolvedPublicationGate with a different string. Preserve both the typed exception and legacy internal message so existing direct callers retain compatibility. Task04 remains in progress; its four CREATE files remain frozen.

## Current interfaces and cause

- app/import_publication_evidence.py:52 defines UnresolvedPublicationGate(PublicationEvidenceError), with no custom constructor.
- require_no_gate_in_transaction at line636 raises that subclass with 'unresolved_publication_gate'. The previous internal message was 'Unresolved import publication gates this user'. The query/transaction validation is unchanged.
- app/distributed_import_service.py:228 catches the typed subclass, then authoritatively rechecks the exact user's gate. Public output remains needs_reconciliation/409/false or dependency/proof503. It does not expose or match the exception string.
- tests/integration/test_import_publication_recovery.py has one exact message assertion and fifteen parameterized direct-mutator assertions matching 'Unresolved'. The actual n2 XML/log is retained; these failures are pre-fix RED, not a new test count.

## Exhaustive change boundary

Modify only app/import_publication_evidence.py: replace the single exception message argument at this gate raise with the exact legacy message. Keep UnresolvedPublicationGate, its inheritance, gate predicate, transaction isolation validation, locks, issuance/reservations/proof/recovery and all public facade mappings unchanged. No tests or other source edits. No migration, deployment, data changes or error-class redesign.

Root owns this packet, REVIEW/plan/progress and private runners. Required edits beyond this one-string correction are a reality conflict. Existing 15 protected dirty paths and four Task04 candidate files must not be rewritten or staged by the implementer.

## Prerequisite and evidence boundary

Read PROJECT_KNOWLEDGE and the repository workflow. Fixed interpreter D:\python_self_agent\venv\Scripts\python.exe. Dedicated PG/S3/target-Q only, freshly verify exact IDs/names/volumes/health; source Q remains exited. Credentials enter child fixture environment only. Root reviews the runner and gives the only serial pytest lane GO. No self-started services or production action.

The accepted original evidence source raw SHA is 0d5e8aff4670608612275ef130e682cd4616e78f380c78d59ebf5b91f693379f. This correction explicitly supersedes that one input, not Task03's historical tested bytes. Preserve old receipts; rebuild final baseline with this exception before repeating required Task04 selections. Do not overwrite old before/after snapshots or claim the previous Task04 unit/real/full counts validate changed authority bytes.

## Implementation and acceptance

1. Independently confirm all 16 n2 XML failures concern only legacy-message matching and the typed subclass still gates writes before planning/I/O.
2. After Astra contract review and root ready assignment, make the single-string correction and report exact raw SHA/diff. Compile only; root GO precedes tests.
3. Root-approved focused existing recovery gate cases must pass with exact original message, while Task03 facade cases prove typed catch, authoritative gate409 and sanitized non-gate503. No weakening of test expectations.
4. Re-run final Task04 unit, real, full and all six regression files under the corrected raw input baseline, unique paths and actual exit/XML/log bindings. Require zero failure/error/skip; verify unique IDs and unchanged four candidate files/15 protected paths. Task04b subsequently authorizes one existing test-only supersession; its fresh complete regression closes acceptance while corrected unit/real/full/facade retain original snapshots and unchanged production-source bindings. Do not rewrite those historical snapshots or claim all governance documents are identical.
5. Astra independently reviews the exact correction and final raw evidence. Root finalizes 04a/04 done and commits only authorized source/tests/docs after acceptance. No main merge or production claim.

Exact focused selections for the reviewed private runner:

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/integration/test_import_publication_recovery.py -k 'ordinary_publish_refuses_unresolved_gate_before_planning_or_io or task9_direct_import_mutators_refuse_unresolved_user_without_writes' --basetemp=.runtime/pytest-isolated-runtime-p4-compat-n1 --junitxml=.runtime/isolated-import-runtime/p4-compat-n1.xml
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/integration/test_distributed_import_facades.py --basetemp=.runtime/pytest-isolated-runtime-p4-facade-n1 --junitxml=.runtime/isolated-import-runtime/p4-facade-n1.xml
```

The four final Task04 selections retain packet04's exact filenames/filters, with new n4/n2/n3 run names fixed by the root-reviewed runner after input re-freeze. Current regression n2 XML SHA4804956481f757cc6a09419af5dfbd4541d4d473e6c2b403146d738cad8dff0e and log SHA46eed671d92e654fe5a005243b58b192a320990bb2630928671ad091ebcf73f0 remain failed evidence.

## Handoff

Report source SHA, exact one-line diff, focused/facade and final command/exit/IDs/XML/log hashes, final baseline exception, preserved candidate/protected hashes, deviations and limits. No test-console progress or started process constitutes acceptance. Non-daemon late operations, live remote providers, API wiring, actual PID restart and production remain subject to the existing later gates.


## Independent ready review and assignment

Astra contract review p4-compat-plan-astra-n1.md SHAfa4a6636b44e2a4550ddcbf90a4eee2e629f0d9497c08ff523e3b543a065a27b judged exact pre-bookkeeping packet c2895d7d1d26e0b3de0719dd715ab9c11d0cb307a238608685924ad36acc5aa3 SPEC/QUALITY PASS. Root records ready gate passed and assigns Sol High solely for the one-string source correction; status in_progress is assignment bookkeeping, not source/test acceptance. Pytest still requires root runner review and GO.

## Accepted handoff

Done after final Astra SPEC/QUALITY PASS p4-final-accepted-astra-n1.md SHA2561c4e731684a14931cee88a18917b41b1b27273ead4202ea772146f7f69dd0b02. The one-string evidence change is rawSHA4c70e8a8520695faebe28a12a1f0deaa333a4367611612030a471d9facf23d4b; exception type, query and locks are unchanged. Focused16 and facade41 passed, corrected Worker53/19/72 passed, and final regression n4 passed320 after the separately approved04btest-only correction. All actual pytest/wrapper0, zeroFES. Old failures/snapshots retained; final delivery/lineage and Sol handoff hashes are recorded in packet04. No API/PID/production acceptance follows.
