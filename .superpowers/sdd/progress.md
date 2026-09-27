# Document Library Vertical Slice SDD Progress

- Planning base: `7ae7314`
- Task 1: complete (commits `d21d79d..90eccf1`, review clean; Penpot revision `119`; focused contract `4 passed`)
- Minor for final review: reconcile superseded revision `115` wording in `01-penpot-design-source.md` with the authoritative revision `118` export / revision `119` semantic evidence.
- Task 2: complete (commits `90eccf1..7d810ec`, review clean; RED `11 failed`; GREEN `29 passed`; compatibility `2 passed`)
- Task 3: complete (commits `1b878b2..d8d748a`, review clean after two corrective rounds; final GREEN `164 passed`)
- Task 4: complete (commits `6d9ed6f..45959a5`, review clean after two corrective rounds; final GREEN `121 passed`)
- Task 5: complete (implementation `9571ee9`; corrections through `3b22198`; final independent re-review approved with no findings; focused GREEN `36 passed`; final frontend `101 passed`; typecheck/lint/build, component map `6/6`, token and diff checks PASS)
- Task 5a: complete (commits `67132d2`, `a291e79`; independent review approved; desktop card/halo pixel geometry matches Penpot; frontend `101/101`)
- Task 5b: complete (implementation `e9707ee`; terminal-only import batches render no DOM; frontend `102/102`; typecheck/lint/build, component map `6/6`, token and diff checks PASS)
- Task 6: complete (implementation `af469ba`; Python `227/227`, frontend `102/102`, E2E `46 passed` with 2 existing conditional skips; six reviewed no-update snapshots; process/runtime cleanup clean)
- Corrective packets 07–09: complete (`f909bc2`, `50d0ee6`, `7f385f4`; focused tests and full npm audit green)
- Final integration review: accepted (`948 passed`, frontend `102/102`, E2E `46 passed` + 2 existing conditional skips, npm audit 0, Docker Linux daemon and Penpot MCP green)

## Distributed cutover 2026-09-26
- PostgreSQL baseline schema: complete (commits 528e70e..e91a84d, task review clean; 7 real-PG tests passed). Full distributed cutover remains active.
- Distributed cutover foundation: complete (commit 83ebbf3, scoped review approved; 43 focused tests passed, disposable PostgreSQL pool smoke passed, paired stopped-write restore drill passed). Full distributed cutover remains active.
- Relational migration prerequisite: scoped review approved (commit 1e6e783; 5 real-PG migration tests passed). Paired-backup apply copied all 25 tables with zero discrepancies, repeat apply unchanged, verify equal. Journal siblings are now rechecked after reading; caller must keep source frozen.
- Immutable object store contract: scoped review approved (commit b59be19; unit+real S3 tests 9 passed). Explicit-version retention and delete-marker response are tested. Runtime publication and bootstrap wiring remain open.
- File migration: complete within document/report byte scope (09a5f1f, correction 4f8b5bc, final scoped review approved). Paired source 4 files verified; repeat apply created no new objects. Missing-users check split from symlink-capability skip. Combined minor follow-ups: 15 passed, 2 Windows symlink skips. Structured snapshots/runtime reference publication remain open.
- Shared PostgreSQL authentication/sessions: scoped review approved (a9e319d, correction a413244). Initial focused suite18 passed; final concurrency-fix suite7 passed. User status is freshly read after session row locking under READ COMMITTED. Shared product API runtime remains unimplemented.
- Learning persistence: scoped complete (39f10fa; independent review approved, 53 tests passed including 6 real-PG scenarios). Shared local/PG rules, user-first writer locking and repeatable-read paging; service legacy gate and composite deletion integration remain open.
- Versioned History/Memory snapshots: scoped complete (009c5be; independent review approved). Final focused 8 real-PG tests passed after covering both minor review requests (positive-version stale CAS and nested JSON/copy/rejection cases). Source migration, manager hydration, episodic persistence and Worker publication fencing remain open.
- Structured source authority inventory: scoped complete (a94304f, fixes de1eb82; independent re-review approved). 18 focused tests passed. Real paired CLI after fixes verified31tables/7files, History2documents, Memory4episodic+2semantic, episodeSQLite4rows and source tag order. No authority migration performed.
- Notes persistence: active; owned working changes and brief `.runtime/distributed-cutover/notes-persistence-brief.md`, resumed implementer notes_finish. Preserve completed work; final tests/report/review pending.
- Report publication: implemented dfb89e7, 6 real-PG/S3 tests passed; scoped review pending. Schema005 depends on pending Notes004. Do not push until dependency is committed/reviewed. Source refs/API downloads remain open.
