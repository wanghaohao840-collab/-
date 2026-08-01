# Final Integration Review: <feature>

- Source review: `REVIEW.md`
- Reviewed commit/worktree: `<commit plus dirty-state description>`
- Review date: `YYYY-MM-DD`
- Result: `accepted | changes-required | blocked`

## Delivered packet inventory

| Packet | Status | Commit | Owned files | Verification |
|---|---|---|---|---|
| `<packet-id>` | done | `<hash or uncommitted>` | `path` | PASS |

## Combined diff reviewed

- Files added:
- Files modified:
- Pre-existing changes excluded from this review:

## Cross-packet interface audit

| Producer | Consumer | Contract checked | Result | Evidence |
|---|---|---|---|---|
| `symbol` | `caller` | signature/data/errors/defaults | pass/fail | `path:line` |

## Requirement coverage

| Accepted requirement | Implementing packet(s) | Evidence | Result |
|---|---|---|---|
| requirement | packet | test/path | pass/fail |

## Overlap and duplication audit

- Conflicting edits: none, or precise findings.
- Duplicate responsibilities/helpers: none, or precise findings.
- Overwritten packet work: none, or precise findings.
- Missing central integration points: none, or precise findings.

## Architecture and invariant audit

- Dependency direction:
- Backward compatibility:
- Persistence/migration:
- Data isolation:
- Failure and concurrency behavior:

## Combined verification

- `exact focused command` — PASS/FAIL (summary)
- `exact regression command` — PASS/FAIL (summary)

## Findings

### Blocking

- None.

### Changes required

- None.

### Residual risks

- None.

## Decision

State why the result is `accepted`, `changes-required`, or `blocked`.

If changes are required, list corrective packet IDs. Do not implement fixes
inside this review.
