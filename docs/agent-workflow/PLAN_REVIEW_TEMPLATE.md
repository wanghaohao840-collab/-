# Plan Review: <feature>

- Source plan: `docs/superpowers/plans/<plan>.md`
- Reviewed commit: `<git commit>`
- Review date: `YYYY-MM-DD`
- Verdict: `accepted | accepted-with-revisions | rejected`

## Repository evidence

- Relevant implementation:
  - `path`: what was verified
- Relevant tests:
  - `path`: what was verified
- Configuration/runtime facts:
  - fact and evidence
- Existing worktree changes to preserve:
  - `path` or `none`

## Findings

### Blocking

- None.

### Required revisions

- None.

### Non-blocking notes

- None.

## Accepted scope

- Goal:
- In scope:
- Out of scope:
- Compatibility requirements:
- Architecture/data-isolation constraints:

## Packet graph

| Packet | Depends on | Parallel-safe | Owned files | Outcome |
|---|---|---:|---|---|
| `01-name.md` | none | yes | `path` | outcome |

## Packet readiness audit

| Packet | Goal/non-goals | Context/interfaces | Prerequisites | Change boundary | Acceptance/tests | Forbidden changes | Handoff format | Ready |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `01-name.md` | yes | yes | yes | yes | yes | yes | yes | yes |

No packet may have `status: ready` while any readiness column is `no`.

## Integration verification

- `exact command`

## Final integration review requirement

- Output:
  `docs/agent-workflow/task-packets/<plan-name>/FINAL_INTEGRATION_REVIEW.md`
- Required after: every implementation packet is `done`
- Result must be: `accepted | changes-required | blocked`
- Required checks:
  - cross-packet interfaces
  - missing requirements
  - duplicate or overlapping implementation
  - central integration points
  - architecture, compatibility, persistence, and isolation
  - combined regression verification

## Open decisions

- None.
