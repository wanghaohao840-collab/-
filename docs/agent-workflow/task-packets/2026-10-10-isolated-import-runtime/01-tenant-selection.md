---
id: "2026-10-10-isolated-import-runtime-01"
title: "Filter PostgreSQL import authority by trusted tenant in SQL"
status: "done"
parallel-safe: false
depends-on: []
base-commit: "38bc8bcabd90e23a7518375d7e2087a399f0eec9"
owner: "Sol High"
---

# Task Packet: SQL tenant selection for import and recovery

## Accepted delivery — 2026-10-11

- Task01 is done. Sol High implemented the three selectors and their tests. Astra High independently returned Spec PASS / Quality PASS for the final five-file snapshot; root independently verified the complete evidence and file boundary.
- Final verification: expanded GREEN n3, 28 passed in 65.79 seconds; full three-file regression n3, 194 passed in 944.33 seconds. Both actual exits are 0 with zero failures, errors or skips. There are 222 unique test identities; the separate 15-pass environment-free unit run is already represented in GREEN and is not added again.
- Evidence: `.runtime/isolated-import-runtime/p1-green-n3-completion.json`, `p1-regression-n3-completion.json`, and `p1-delivery-root-n3.json`. Independent final report: `p1-final-accepted-astra-n4.md`, SHA256 `d911fcc66e1c920034cf8e57752fe9836e8049fb03dffe846b14286f81004cd5`.
- GREEN XML SHA256 `526df5564f8e84cd560a48b0551c50a3d6820fc8e1e0a608c014b083582b9b69`; regression XML SHA256 `b2f77f4b56095685bb5d81c7f2f4041f40897e5a1fff5e9d443c055cc618b419`.
- Only the two authorized repositories differ from the accepted 74-file baseline; its other 72 files and all 15 pre-existing modified paths retain their raw bytes. The helper and two test files are the authorized additions.
- Docker restoration retained all container and volume identities. Final target Qdrant port is 49742, PostgreSQL 59497 and S3 59498; protected source Qdrant remained exited. The original blocked/interrupted handoff below is historical and superseded by this completed verification.
- Scope: SQL tenant selection only. Runtime configuration, authentication facades, API, Worker, complete feature integration and production acceptance remain pending. The source delivery commit is recorded in the root commit-binding receipt after this document is committed.

## Goal

Add an optional, strictly validated tenant allowlist to the three existing PostgreSQL selectors. When a nonempty allowlist is supplied, ordinary claim, expired ordinary lease recovery, and publication recovery claim consider only those users in SQL before locking or mutating authority. Existing callers without a filter retain their current behavior.

## Non-goals

- No isolated runtime configuration, API, Worker, publication execution, migration, service lifecycle, or production cutover.
- No change to lease duration, retries, gate/proof semantics, task/user lease tuple, scheduling order, or business data format.
- No changes to local import Worker or other pre-existing dirty code.

## Delivery context

The approved isolated runtime will eventually call these three selectors with two pre-attested tenant IDs. A filter performed only after a database candidate is selected can lock or mutate a disallowed user's task and can starve permitted users. This packet supplies the database boundary independently of that future runtime. `None` is the repository-wide legacy selector; isolated callers will later be required to pass a nonempty `frozenset[str]`.

The ordinary selector first ranks eligible active users, then runs one short transaction per candidate. The expiry selector scans running tasks and handles each candidate under user-first locks. Publication recovery first captures a due queue row in a short transaction, commits it, and only then waits on user authority in another transaction. Preserve those structures.

## Relevant files and current interfaces

- `app/postgres_import_leases.py:44` — `_DUE` is the ordinary task eligibility expression, including source existence and unresolved publication gate exclusion.
- `app/postgres_import_leases.py:90` — `PostgresImportLeaseRepository.claim_next(worker_id, lease_seconds=60)` returns `ImportAttempt | None`; initial user scan orders by `import_user_schedule.last_claimed_at NULLS FIRST, u.id`, then locks a user, acquires its lease, locks and rereads a task, and writes attempt audit and schedule. Existing positional callers must keep working.
- `app/postgres_import_leases.py:111` — the per-user running-task and due-task queries use the candidate user. At lines 121–122, the task `FOR UPDATE` and reread currently use only `id`; both must bind the exact scanned candidate user before acquisition can commit.
- `app/postgres_import_leases.py:468` — `recover_expired(limit=100)` returns an integer and scans all running tasks. At lines 482–488 it locks user, user lease and task, then rereads the task; its task lock/reread currently use only task `id`. It may update audit, task, user lease and batch only after exact user confirmation.
- `app/import_publication_recovery.py:265` — `PostgresImportPublicationRecoveryRepository.claim_next(worker_id: str, lease_seconds: int=60) -> RecoveryClaim | None` ranks per-user due queue rows, locks the final candidate, commits queue capture and schedule before a separate authority transaction.
- `app/import_publication_recovery.py:272` — the ranked CTE has pending/expired-claimed `OR`; any added `AND` must follow parentheses around the entire `OR`. The final locked candidate query at lines 280–300 needs the same bound allowlist.
- `app/import_publication_recovery.py:41` — `_report_unavailable` wraps recovery calls. Invalid allowlist remains `ValueError`, before any database transaction, and is not converted to an availability error.
- `tests/integration/test_postgres_import_artifacts.py:21` — existing `fixture` provides two active UUID users, one disabled user, two database pools and a versioned S3 store; `tests/integration/test_postgres_import_leases.py:19` provides `submit(fixture, user=None, count=1)`.
- `tests/test_import_tenant_selection.py` — new service-independent validation test. Construct repositories with a database whose `transaction()` raises immediately; verify malformed and empty filters without PG/S3/Qdrant fixtures. `None` is expected to reach the database and is verified by integration cases.
- `tests/integration/test_import_publication_recovery.py:1570` — existing evidence expiry and queue claim precedent; `:2912` tests short capture before user wait and `:2972` builds two independently attested publication users and tests fairness. Reuse their real setup pattern in the new test file, including `tests/integration/test_import_memory_publication.py:36` `memory_publication` and `tests/integration/test_import_publication_proof.py:75` `_issue`.
- `tests/integration/test_postgres_auth_sessions.py:28` — `shared_database` creates and drops a unique PostgreSQL schema; `tests/integration/test_s3_object_store.py:15` creates and deletes a versioned bucket; `tests/integration/test_import_document_publication.py:40` uses target Qdrant and unique collections.
- Existing changes to preserve: `app/coordination.py`, `app/document_library.py`, `app/history.py`, `app/import_models.py`, `app/import_worker.py`, `app/memory_repository.py`, `app/qa_deletion.py`, `app/qa_persistence.py`, `app/qa_repository.py`, `app/recovery.py`, `migrations/versions/20260926_01_business_schema.sql`, `migrations/versions/20260928_09_import_leases.py`, `tests/conftest.py`, `tests/test_import_worker.py`, and `tests/test_user_mutation_coordination.py`. None belongs to this packet; a normal Git diff may be empty even while status shows these 15 modified paths.

## Prerequisites

### Packet dependencies

- None. This is the first independently deliverable unit.

### Repository/base state

- Base commit: `38bc8bcabd90e23a7518375d7e2087a399f0eec9`. Preserve the 15 pre-existing dirty paths and the untracked source plan; do not clean/reset or normalize them.
- The named repository classes, `_DUE`, recovery queue tables, migrations, and fixtures above must still exist. Recheck their current signatures and Git state before editing.
- Astra High independently reviewed the complete packet and returned `READY` on 2026-10-10. Root resolved both findings and updated `REVIEW.md` to `Ready: yes`. The review covers this implementation contract; implementation and test acceptance remain pending.

### External prerequisites

- For implementation tests only: a disposable PG service on loopback 59497, versioned S3 on 59498, and target Qdrant on 49742. The earlier recorded observation `.runtime/distributed-cutover/isolated-runtime-services-root-20261010-n1.json` used target port 59539; after Docker restoration the same target container ID and volume use loopback 49742. Recheck exact service identities, volumes, current ports and health immediately before testing. Protected source Qdrant remains stopped and untouched.
- The test launcher/root must provide `POSTGRES_TEST_URL`, `S3_TEST_ENDPOINT`, `S3_TEST_ACCESS_KEY`, `S3_TEST_SECRET_KEY`, and `GENERATION_QDRANT_TEST_URL` through the existing private environment mechanism. Do not write secret values, `Config.Env`, or raw credential URLs to public documentation or logs.
- Use only `D:\python_self_agent\venv\Scripts\python.exe`. The root grants the sole pytest lane; no worker or reviewer runs pytest before that GO.

## Explicit change boundary

### Allowed files

- Modify: `app/postgres_import_leases.py`.
- Modify: `app/import_publication_recovery.py`.
- Create only if shared pure validation avoids divergence: `app/import_tenant_selection.py`.
- Create: `tests/integration/test_isolated_import_tenant_selection.py`.
- Create: `tests/test_import_tenant_selection.py`.
- During implementation handoff only: update this assigned packet's `status` and append its implementation handoff after all required verification.
- Ignored test outputs may be written only under unique `.runtime/pytest-isolated-runtime-p1-*` basetemps and `.runtime/isolated-import-runtime/p1-*.xml` by the exact verification commands. A root-owned private test launcher may set process-local environment; it is not an owned source change in this packet.

### Allowed behavior changes

- Add a final optional keyword-only parameter `allowed_user_ids: frozenset[str] | None = None` to each of the three selectors. Preserve all preceding positional arguments and return types.
- Validate the exact input type and each ID before opening a database transaction. `None` means legacy unfiltered behavior; empty `frozenset()` means no selection and no database use.
- Use parameter-bound PostgreSQL array membership, never interpolated user IDs, in the first candidate selection and every later selection or lock that could choose the authority row. Recheck exact candidate user on the locked path before any audit, queue, schedule, lease, batch, or task mutation.

### Forbidden changes

- Do not modify any other tracked source, test, migration, schema, dependency file, bootstrap, API, local Worker, or protected service. In particular, leave all 15 pre-existing dirty files untouched.
- Do not alter the original user-first lock order, fresh database clock after locks, fairness order, no candidate cap, short queue capture transaction, original lease and proof checks, or terminal authority.
- Do not add a local fallback, a filtered in-memory postprocessing step, or a predicate that selects/locks a disallowed user before rejection. Do not widen public selectors to accept mutable sets/lists or silently normalize malformed IDs.
- Do not commit, push, start/stop services, or run pytest outside the sole root-approved lane.

If implementation requires anything outside this boundary, report a reality conflict.

## Interface contract

### Consumes

- Existing `PostgresImportLeaseRepository` and `PostgresImportPublicationRecoveryRepository` instances and PostgreSQL transaction interfaces.
- Exact original eligibility/authority predicates, task/user lease tuple, gates, audit, recovery queue and schedule.

### Produces

- `PostgresImportLeaseRepository.claim_next(worker_id, lease_seconds=60, *, allowed_user_ids: frozenset[str] | None = None) -> ImportAttempt | None`.
- `PostgresImportLeaseRepository.recover_expired(limit=100, *, allowed_user_ids: frozenset[str] | None = None) -> int`.
- `PostgresImportPublicationRecoveryRepository.claim_next(worker_id: str, lease_seconds: int = 60, *, allowed_user_ids: frozenset[str] | None = None) -> RecoveryClaim | None`.
- A pure helper is optional. If created, its contract is `validate_allowed_user_ids(value: frozenset[str] | None) -> frozenset[str] | None`: exact `frozenset` type; each member exact `str`, nonempty and equal to its stripped form; invalid input raises `ValueError('allowed_user_ids must be a frozenset of nonempty user IDs')`. No DB access or coercion.

### Invariants

- Existing `None`/omitted calls behave as before, including positional calls and unfiltered selector ordering.
- For nonempty filters, no excluded user's row is selected for `FOR UPDATE`, captured, claimed, expired, audited, scheduled, or given a token. An excluded earlier candidate cannot block an eligible later user.
- Bound arrays protect SQL structure. The same validated allowlist applies in recovery's ranked and final locked predicates and in ordinary/expiry candidate and locked-row paths.
- User lock precedes user lease/task authority lock; after locks, exact candidate identity and eligibility are rechecked with a fresh database clock. Recovery capture commits before any wait for user authority.

## Required behavior

- Validate after the existing worker/lease/limit argument checks but before any transaction. Reject a mutable `set`, list, tuple, `frozenset` subclass, `frozenset({None})`, empty/whitespace/trim-mismatched IDs, and non-string members. Do not treat a string as an iterable allowlist. Valid empty input immediately returns `None` for claims or `0` for expiry, with zero transaction openings.
- Put the empty/malformed checks in `tests/test_import_tenant_selection.py` using a transaction-bomb database. These cases must run without any PG/S3/Qdrant environment, so integration fixture skips cannot mask validation failures.
- Ordinary initial `users u` candidate SQL must include `u.id = ANY(%s::text[])` or equivalent for the non-`None` branch. Preserve schedule sort and absence of `LIMIT`. In the short candidate transaction, bind the scanned candidate user to the user lock, running-task check and due-task query. The task `FOR UPDATE` SQL itself must require both `id=%s AND user_id=%s` (and the allowlist, if an equivalent prelock condition is used). The reread must require the same exact pair; confirm `row['user_id'] == candidate['id']` and membership before attempt/audit/schedule writes. If the exact task row disappears or drifts after lease acquisition, follow the existing `ImportLeaseLost` rollback path; never accept a row moved to a different allowed user.
- Expiry initial running-task SQL must include bound `user_id = ANY(%s::text[])` for filtered calls. The later task `FOR UPDATE` and reread SQL must require the exact scanned `(id, user_id)`, even if both old and new user IDs are allowed. Confirm returned `row['user_id'] == candidate['user_id']` and membership before audit, header, queue, task or lease mutation. Missing/drifted row skips without side effects. Preserve the current user → user mutation lease → task lock order and fresh-clock expiry checks.
- Recovery ranked CTE must filter `q.user_id` and preserve `((pending AND due) OR (claimed AND expired))` as a single parenthesized expression before applying `AND` filters. The outer final `FOR UPDATE OF q SKIP LOCKED` selection must also filter bound `q.user_id` and retain existing gate and live-lease exclusions. Recheck captured `candidate['user_id']` before queue UPDATE, token issuance or schedule UPDATE. A disallowed due pending row and a disallowed expired claimed row must both remain byte-for-byte unchanged while an allowed due row is selected.
- `None` may retain the original SQL branch. For filtered calls, pass `sorted(validated_ids)` as a bound PostgreSQL text-array parameter. This must be a Python `list`; a tuple is adapted differently by psycopg and is not an array parameter. Empty filter does not begin a transaction, even in recovery's three-attempt loop.
- Fairness, busy-user skip, fresh-clock decisions, queue capture visibility before user wait, and expired capture losing authority must also hold with a non-`None` filter; old unfiltered suites alone cannot demonstrate that.

## Implementation guidance

1. Write service-independent RED tests in `tests/test_import_tenant_selection.py` with a database object whose `transaction()` raises if called. Then write RED integration tests in the new integration file. For ordinary/expiry, reuse the existing two-active-user artifact fixture and `submit` helper. Stage the disallowed user earlier in schedule/order, give an allowed user a task, and assert the allowed result, exact audit/token/schedule changes, and unchanged excluded rows. Use two pools for a lock/busy-user case.
2. For queue tests, reuse `memory_publication` and the two-user setup at `tests/integration/test_import_publication_recovery.py:2972`: create the other user's real History/RAG/episode empty baseline, issue one real publication per user, make queue rows due and both ordinary leases expired. Cover an excluded pending row and an excluded expired-claimed row; verify all queue fields, schedule, issuance token rows, gates and audit before/after. Keep Qdrant fixtures disposable.
3. Add tests for omitted/`None`, empty and malformed filters on all three methods. The service-independent transaction bomb proves invalid/empty filters never touch the DB; it does not apply to `None`, which must retain DB selection and is covered by integration tests. Add an instrumented cursor or controlled race that presents a stale task ID with a different `user_id`; assert task `FOR UPDATE` SQL binds the original candidate `user_id`, reread binds the same pair, ordinary claim rolls back its acquired lease, and expiry skips without writes. Merely checking allowlist membership is insufficient when a row drifts to another permitted user.
4. Add filtered recovery concurrency tests using the existing short-capture/user-lock seam near `test_import_publication_recovery.py:2912`, expired-capture takeover seam nearby, and two-user fairness seam near `:2972`. Assert capture is externally visible while user authority is blocked, stale capture cannot grant recovery, and an allowed second user advances when an allowed first user is busy. Include the excluded user in the database so SQL filtering is exercised.
5. Implement exact validation and bound SQL. Preserve original `None` SQL branch if that minimizes risk. Recheck task identity after locks, before writes. Review the final SQL text for `OR` precedence and every `FOR UPDATE` target.
6. Run RED, GREEN, and regression commands with three distinct basetemps and actual exit/XML evidence only after root GO. Report any skip as missing acceptance, not a pass.

## Acceptance criteria

- [ ] All three public signatures have the specified keyword-only default, and omitted/`None` calls retain the previous ordering, return types and positional-call behavior.
- [ ] Invalid filters raise `ValueError` and valid empty filters return `None`/`0` before any database transaction; strict type and ID edge cases are tested.
- [ ] The malformed/empty no-transaction test runs in a separate child process with the five fixture environment keys removed and no skip, using a transaction-bomb database for each repository method. The parent environment remains unchanged.
- [ ] SQL selects only allowed users in ordinary's initial candidate query, expiry's initial running-task query, and both recovery ranked and final locked queries. Values are parameter bound through a sorted Python list adapted as a PostgreSQL text array. Pending/expired-claimed recovery `OR` is fully parenthesized.
- [ ] Ordinary and expiry task-lock SQL binds the exact scanned `(task id, candidate user id)`; subsequent reread binds that same pair and checks row identity and membership before writes. A controlled drift/race case proves ordinary rollback and expiry no-op, including drift to another user inside the allowlist.
- [ ] A disallowed earlier ordinary task, a disallowed expired running task, a disallowed due pending recovery queue row, and a disallowed expired claimed queue row retain their original task/audit/lease/queue/schedule/token state while permitted work advances.
- [ ] Filtered contention and expiry tests preserve fairness, skip busy users, fresh-clock checks, short recovery capture before user wait, and stale capture rejection. The existing unfiltered selector suites still pass.
- [ ] Root records the actual unique test commands, exit codes and XML with zero skips on exact disposable dependencies; Astra independently reviews final SQL, lock order and excluded-tenant side effects before `done`.

## Test and verification commands

Run serially from the repository root, only after root owns and grants the sole pytest lane. The new private launcher must check exact disposable service IDs/volumes and set the five fixture environment keys for PG 59497, S3 59498 and **target** Qdrant 49742 without logging secret values. Create only the ignored XML output directory first: `New-Item -ItemType Directory -Force .runtime/isolated-import-runtime`. Do not use `-k` or a narrowed collection filter. Do not run an old fixture runner tied to a previous Qdrant port.

```powershell
D:\python_self_agent\venv\Scripts\python.exe -c 'import os,subprocess,sys; e=os.environ.copy(); [e.pop(k,None) for k in ("POSTGRES_TEST_URL","S3_TEST_ENDPOINT","S3_TEST_ACCESS_KEY","S3_TEST_SECRET_KEY","GENERATION_QDRANT_TEST_URL")]; sys.exit(subprocess.call([sys.executable,"-m","pytest","-q","tests/test_import_tenant_selection.py","--basetemp=.runtime/pytest-isolated-runtime-p1-unit-noenv-n1","--junitxml=.runtime/isolated-import-runtime/p1-unit-noenv-n1.xml"],env=e))'
```

The commands are displayed with the independent unit command first. Execute the RED command before implementation; after implementation, execute this unit-only child process, then GREEN and regression. Expected for the unit command: exit 0, zero skipped, transaction-bomb validation succeeds with exactly the five fixture keys removed from the child environment; the parent environment is unchanged. Capture its XML and exit separately.

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/test_import_tenant_selection.py tests/integration/test_isolated_import_tenant_selection.py --basetemp=.runtime/pytest-isolated-runtime-p1-red-n1 --junitxml=.runtime/isolated-import-runtime/p1-red-n1.xml
```

Expected before implementation: nonzero exit from newly required selector assertions only, with no skip or fixture/dependency error. Record actual failures rather than presuming this result.

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/test_import_tenant_selection.py tests/integration/test_isolated_import_tenant_selection.py --basetemp=.runtime/pytest-isolated-runtime-p1-green-n1 --junitxml=.runtime/isolated-import-runtime/p1-green-n1.xml
```

Expected after implementation: exit 0; all new cases passed, zero skipped.

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/integration/test_postgres_import_leases.py tests/integration/test_import_publication_recovery.py tests/integration/test_postgres_import_artifacts.py --basetemp=.runtime/pytest-isolated-runtime-p1-regression-n1 --junitxml=.runtime/isolated-import-runtime/p1-regression-n1.xml
```

Expected: exit 0, zero skipped, no regression in original ordinary, expiry, recovery, and artifact tests. The two fixture prerequisites are real versioned S3 and target Qdrant; a skipped suite does not verify this packet. Preserve raw command, output, exit code, XML, source commit and target resource identity in root-controlled evidence.

## Stop conditions

Stop and report `blocked` if:

- this packet remains `draft` or Astra/REVIEW readiness is unresolved;
- a verified repository fact, interface, caller, fixture, service identity, or test above differs;
- another worker or the pre-existing dirty worktree overlaps an owned responsibility;
- the requested behavior already exists or conflicts with current code;
- implementation requires a file outside the allowed boundary;
- a dependency or root's sole pytest lane is unavailable;
- the exact commands cannot prove acceptance, skip because services are absent, or target protected resources;
- a task can move between users without the exact candidate-pair lock and reread safely preventing side effects;
- acceptance criteria conflict with current persistence, lock, or publication authority.

Do not improvise around a conflict. Append the **Reality-conflict report** from `docs/agent-workflow/README.md` and wait for packet revision.

## Resumption amendment, 2026-10-11

The user authorized restoration of the exact disposable services. Root observed the same four container identities and volumes, with PostgreSQL at 59497, S3 at 59498, target Qdrant now at 49742, and protected source Qdrant exited. This packet is back in progress. The new ignored private runner `run-p1-sol-n3.py` must recheck those identities, volumes, ports and health before each test run. Earlier blocked evidence remains historical; no prior XML or runner output is overwritten. The final expanded GREEN and full regression still require actual exit and zero-skip XML evidence.

## Implementation handoff

- Packet: `2026-10-10-isolated-import-runtime-01`
- Status: `blocked` — external Docker prerequisite unavailable; implementation and final integration acceptance remain pending.
- Delivered:
  - Strict optional tenant allowlists for ordinary claim, ordinary expiry recovery, and publication recovery claim. Nonempty filters are parameter-bound in SQL before candidate lock; exact task/user pairs are rechecked before writes. Unfiltered calls retain their original public positional prefix.
- Files changed:
  - `app/postgres_import_leases.py` — filter ordinary and expiry candidates, bind exact task/user locks and rereads.
  - `app/import_publication_recovery.py` — filter ranked and final locked recovery queue selection.
  - `app/import_tenant_selection.py` — pure strict allowlist validator.
  - `tests/test_import_tenant_selection.py` — environment-free transaction-bomb validation.
  - `tests/integration/test_isolated_import_tenant_selection.py` — disposable-service filter, drift, fairness, queue capture and fresh-clock cases.
  - This assigned packet — status and evidence handoff only.
- Interfaces added or changed:
  - `PostgresImportLeaseRepository.claim_next(worker_id, lease_seconds=60, *, allowed_user_ids: frozenset[str] | None = None)`.
  - `PostgresImportLeaseRepository.recover_expired(limit=100, *, allowed_user_ids: frozenset[str] | None = None)`.
  - `PostgresImportPublicationRecoveryRepository.claim_next(worker_id: str, lease_seconds: int = 60, *, allowed_user_ids: frozenset[str] | None = None) -> RecoveryClaim | None`.
  - `validate_allowed_user_ids(value: frozenset[str] | None) -> frozenset[str] | None`.
- Acceptance evidence:
  - [x] Strict malformed/empty input validation before any transaction — unit-noenv n2, 15 passed, zero skipped.
  - [x] Earlier integration set covering excluded ordinary/expiry and recovery rows, candidate-pair drift, capture and rotation — GREEN n1, 26 passed, zero skipped, against exact disposable resources before they became unavailable.
  - [ ] Final expanded integration set with filtered expiry lock-wait/fresh-clock and two-allowed blocked-user fairness — tests added, currently unrun.
  - [ ] Required unfiltered regression and final Astra review on frozen final tests — pending.
- Verification:
  - `D:\python_self_agent\venv\Scripts\python.exe .runtime\isolated-import-runtime\run-p1-sol-n1.py red n3` — expected RED, actual exit 1; 26 failures due missing selector contract, zero errors/skips; `.runtime/isolated-import-runtime/p1-red-n3.xml`.
  - `D:\python_self_agent\venv\Scripts\python.exe .runtime\isolated-import-runtime\run-p1-sol-n1.py unit-noenv n2` — PASS, actual exit 0; 15 passed, zero skipped; `.runtime/isolated-import-runtime/p1-unit-noenv-n2.xml`; five fixture environment keys removed only from child process.
  - `D:\python_self_agent\venv\Scripts\python.exe .runtime\isolated-import-runtime\run-p1-sol-n1.py green` — PASS, actual exit 0; 26 passed, zero skipped; `.runtime/isolated-import-runtime/p1-green-n1.xml`. This precedes the two final concurrency cases; its integration test SHA256 was `481E496E0FD4E5D0E5A235F07F4CA635FC9D72219C0AB0AC923CC4422C21A382`.
  - `D:\python_self_agent\venv\Scripts\python.exe .runtime\isolated-import-runtime\run-p1-sol-n1.py regression` — INTERRUPTED after start; no actual exit, XML or completion record, only `.runtime/isolated-import-runtime/p1-regression-n1-before.json` and three-byte log. It is not a passing regression.
  - `D:\python_self_agent\venv\Scripts\python.exe .runtime\isolated-import-runtime\run-p1-sol-n1.py green n2` — PRETEST BLOCKED; `docker inspect` could not connect to the Docker Desktop Linux pipe. No pytest child, XML or completion record.
  - `D:\python_self_agent\venv\Scripts\python.exe -m py_compile app/postgres_import_leases.py app/import_publication_recovery.py app/import_tenant_selection.py tests/test_import_tenant_selection.py tests/integration/test_isolated_import_tenant_selection.py` — actual exit 0.
  - `git -c core.whitespace=cr-at-eol diff --check -- app/postgres_import_leases.py app/import_publication_recovery.py docs/agent-workflow/task-packets/2026-10-10-isolated-import-runtime/01-tenant-selection.md` — actual exit 0.
- Scope confirmation:
  - changed only allowed tracked/untracked packet files: yes; ignored private runner and evidence are outside the source diff.
  - forbidden areas untouched: yes; the 15 pre-existing dirty paths remain intact by completed-run before/after raw SHA evidence.
- Deviations:
  - GREEN n1 predates two added cases, and regression n1 was interrupted at a turn boundary. Neither is counted as final acceptance.
- Residual risks/follow-ups:
  - Restore exact disposable Docker services, run expanded GREEN and full regression with unique outputs and zero skips, then obtain final independent Astra review.
  - Current expanded integration file SHA256: `A319F2D9A558E6C07012983A86F100777EDEE9EE5807CB13B34FE9A45129C81D`. Source SHA256: ordinary `C27ECF41D3551BE3632DD761335D2EC314E0B73DB186A1CCAA33F0B8C8994BE3`; recovery `C14DE04DB0A036EEF0000A288989F9C5CADEFB0FC30E3910C92D1EA4F182ED37`; helper `801A1FA27C1F5189EB990A734E1CCFAB3B6F8D12F95EC5FB9BFB8240A6F4CB11`.
- Commit:
  - `not committed`; no push. Product/runtime acceptance is not claimed.

## Reality-conflict report

- Packet: `2026-10-10-isolated-import-runtime-01`
- Status: blocked
- Expected by packet:
  - The four recorded disposable Docker identities remain inspectable; PostgreSQL, versioned S3 and target Qdrant are healthy while protected source Qdrant is exited. All new and regression integration tests use those exact resources.
- Observed in repository:
  - The approved runner `.runtime/isolated-import-runtime/run-p1-sol-n1.py` could not begin GREEN n2 because `docker inspect` could not connect to `npipe:////./pipe/dockerDesktopLinuxEngine`; `docker info --format '{{.ServerVersion}}'` failed with the same missing pipe. No n2 GREEN XML or completion record exists.
  - Earlier RED n3: exit 1, 26 selector-contract failures, zero fixture errors/skips; unit-noenv n2: exit 0, 15 passed, zero skips. GREEN n1: exit 0, 26 passed, zero skips, before the two added concurrency cases. Regression n1 has only its before snapshot and a three-character progress log, with no live runner process, XML or completion receipt; it is interrupted and cannot be counted.
- Impact:
  - The newly added filtered expiry lock-wait/fresh-clock and two-allowed recovery fairness cases, plus the required full regression, cannot be accepted without verifying exact disposable services. Starting or stopping services is outside this packet's change boundary.
- Work completed before pause:
  - Implemented strict validation and SQL-bound tenant selection in `app/postgres_import_leases.py`, `app/import_publication_recovery.py` and `app/import_tenant_selection.py`; added service-independent and integration tests in the two owned new test files.
  - All five owned Python files passed `py_compile`; `git -c core.whitespace=cr-at-eol diff --check` passed. The 15 pre-existing dirty files were preserved by test-runner before/after SHA checks on completed runs.
- Recommended resolution:
  - Restore Docker availability and revalidate the exact recorded identities, volumes, ports and health; then set this packet back to `in_progress` and run new unique GREEN and regression outputs under the sole pytest lane. No plan or interface change is needed.
- Decision required:
  - Will the exact disposable Docker services be restored for completion of this packet's acceptance tests?
