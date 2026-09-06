# Import controls/history — final corrective verification

Corrections: 2026-09-05; verification continued 2026-09-06. Baseline: `ee2cc98`.
Scope: independent final-review findings I1–I6 and M1. No push or production-data changes.

## Approved lock correction

The user explicitly approved correcting Task 5's conflicting shared-lock requirement.
Authenticated pause/cancel now acquire a short, per-user runtime request gate, so durable
SQLite requests can commit while the real Assistant holds its data-write lock in RAG.
Clear/delete acquire **data-write lock → request gate** across guard and mutation;
cleanup-failed cancellation cannot slip between them. Request paths never acquire the
data-write lock while holding the gate. Submission/resume/retry, import data writes,
compensation, ownership/lifecycle checks, and single-process user serialization retain
their existing safety boundaries. The accepted plan and synchronization tests were updated.

## Corrective coverage

| Finding | Implementation and concrete regression evidence |
| --- | --- |
| I1 | Real Assistant + service + runner with offline RAG barrier; all four task/batch pause/cancel requests commit before release; queued siblings and both destructive-guard orderings covered. |
| I2 | Strict JSON compensation persistence; inject actual cache write and replace failures, retain staging/failure states, retry with target already absent in live memory, reopen and preserve unrelated chunks. |
| I3 | Always reconcile deterministic-event snapshot; fail actual snapshot atomic replacement twice, retry same-runtime cleanup, reconstruct Memory and preserve unrelated event. |
| I4 | Transactional ordinary lifecycle events; actual success/failure/retry sequences, stage deduplication, rejection/duplicate no-ops, persisted reopen, and rollback when event insertion fails. |
| I5 | Server-owned rendered-page ID/version/context snapshot; insertion, deletion, membership/version change, same filenames, page/filter/auth changes, timeline and confirmed deletion cannot silently retarget. |
| I6 | Durable least-attempted recovery selection; 23 deleting batches, first 20 permanently invalid, bounded passes, restart/reinitialization, wraparound, and frozen/backwards clock. Partial-index query plan verified. |
| M1 | Rendered checkbox explicitly promises imported documents are preserved. |

I6 adds only the private `cleanup_attempt_count` field (idempotent migration, default zero)
and a partial recovery index. Counters increment in the bounded selection transaction
before filesystem work, so a crash or failed error-record write still permits later rows
to advance. This avoids wall-clock ordering dependence without discarding failed rows or
introducing an unbounded cleanup pass. Worker limits remain 20 batches per recovery pass.

## Verification

- Initial backend RED: 11 failed, 2 passed; UI RED: 8 failed, 6 deselected.
- Affected integration gate: **186 passed, 1 skipped (32.94s)**.
- Broader focused: 535 passed, 2 obsolete long-lock test expectations failed (725.86s).
  Updated those expectations to preserve destructive safety through the new short gate.
- Final targeted gate: **175 passed, 1 skipped (109.36s)**.
- Compileall, side-effect-free UI import, and `git diff --check`: **exit 0**.
  UI probe: `Blocks`, `data_root_created=False import_workers_started=False`.
- **Single full suite: 1067 passed, 8 skipped in 921.13s (15:21), exit 0.**
  JUnit `.runtime/final-full-sept6.xml`: 1075 tests, 0 failures, 0 errors, 8 skipped.
  Command: `D:/python_self_agent/venv/Scripts/python.exe -m pytest -q --basetemp=.runtime/final-full-sept6 -o cache_dir=.runtime/final-cache-full-sept6 --junitxml=.runtime/final-full-sept6.xml`.
  Production/tests were unchanged during this fresh complete invocation.
- Skip limits: 1 Neo4j live-environment case, 5 Qdrant live-environment cases, and
  2 Windows symlink-privilege cases. No new skips, warning suppression, or elevation.
- Detailed exact command/output history: `.superpowers/sdd/final-fixes-report.md`
  in the implementation worktree (intentionally ignored local handoff evidence).

Self-review preserved existing architectural boundaries and public best-effort deletion
outside compensation. Large pre-existing modules were not restructured.

## Independent final re-review

September 6 independent review: **Task 8 Spec PASS / Quality PASS; whole-feature code-review
Overall PASS / Spec PASS / Quality PASS; 0 Critical, 0 Important, 0 Minor findings**.
All original I1–I6 and M1 are closed. The review inspected the unchanged production/test
tree and concrete regression assertions, and did not rerun passing suites. Its local
evidence is `.superpowers/sdd/final-rereview-sept6.md`. The single full offline suite
subsequently passed on the same reviewed tree. All required corrective implementation,
independent code-review, and verification gates are complete; no open findings remain.
