# Plan Review: episodic-final-consistency

- Source plan: `docs/superpowers/plans/2026-08-03-episodic-final-consistency.md`
- Reviewed commit: `de321b2309b281623bc7f9c0d898810403319261`
- Review date: `2026-08-03`
- Verdict: `accepted`

## Repository evidence

- Episodic forget/clear currently delete vectors then SQLite; SQLite failure can
  leave a partial state and there is no compensation.
- SQLiteDocumentStore already provides get/add/delete operations sufficient to
  snapshot and restore rows without schema changes.
- Current range pushdown uses DATETIME against raw episode timestamps; common
  legacy non-ISO strings need canonicalization before remote matching.
- VectorStore scroll can return payloads and vectors under user/type equality
  filters, so migration needs no new storage interface.
- Current full baseline after Qdrant hardening: 610 passed, 6 skipped.

## Findings

### Blocking

- None.

### Required revisions

- None.

### Non-blocking notes

- Compensation cannot guarantee recovery if SQLite restoration itself fails;
  that condition must be surfaced explicitly.
- Unparseable timestamps are preserved rather than assigned fabricated dates.

## Accepted scope

- Two serial packets; no schema/collection migration, API change, or unrelated
  refactor.
- Preserve per-user migration scope, local fallback, and exact-ID deletion.

## Packet graph

| Packet | Depends on | Parallel-safe | Owned files | Outcome |
|---|---|---:|---|---|
| `01-compensating-cleanup.md` | none | no | episodic + cleanup tests | failure restores SQLite/local state |
| `02-legacy-timestamp-normalization.md` | 01 | no | episodic + protocol/live tests | legacy timestamps become range-queryable |

## Packet readiness audit

| Packet | Goal/non-goals | Context/interfaces | Prerequisites | Change boundary | Acceptance/tests | Forbidden changes | Handoff format | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `01-compensating-cleanup.md` | yes | yes | yes | yes | yes | yes | yes | yes |
| `02-legacy-timestamp-normalization.md` | yes | yes | yes | yes | yes | yes | yes | yes |

## Integration verification

- focused cleanup/protocol tests
- `scripts/run_qdrant_integration.ps1`
- affected memory regression
- full pytest suite

## Final integration review requirement

Create `FINAL_INTEGRATION_REVIEW.md` after both packets are done.

## Open decisions

- None.
