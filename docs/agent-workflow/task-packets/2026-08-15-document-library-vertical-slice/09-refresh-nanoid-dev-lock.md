---
id: "document-library-vertical-slice-09"
title: "Refresh the patched nanoid development lock"
status: "ready"
parallel-safe: true
depends-on: []
base-commit: "14a990ed6e5260760411b2d7fad0c2ead7dda342"
owner: "unassigned"
---

# Corrective Task Packet: Refresh the patched nanoid development lock

## Goal

Remove the current full npm audit finding by resolving Vite/PostCSS's transitive `nanoid` to a patched compatible version without changing top-level dependencies or production behavior.

## Non-goals

- Do not upgrade Vite, PostCSS, React or any declared package version.
- Do not modify frontend source, tests, configuration or production dependencies.

## Delivery context

`npm audit --omit=dev` is clean, but full audit reports one high advisory for dev-only `nanoid@3.3.17`; PostCSS accepts `^3.3.16` and npm reports a patch fix available.

## Relevant files and current interfaces

- `web/package-lock.json:3621` — locked `nanoid@3.3.17`.
- `web/package-lock.json:3861` — PostCSS accepts `nanoid ^3.3.16`.
- `web/package.json` — top-level dependency contract; read-only.
- Existing changes to preserve: completed feature and corrective review artifacts.

## Prerequisites

### Packet dependencies

- none.

### Repository/base state

- Base commit: `14a990ed6e5260760411b2d7fad0c2ead7dda342`.
- Current full audit reports exactly one nanoid finding and production-only audit reports zero.

### External prerequisites

- npm registry access for lock resolution.

## Explicit change boundary

### Allowed files

- Modify: `web/package-lock.json`
- Modify: this packet for handoff.

### Allowed behavior changes

- Refresh only the compatible transitive nanoid lock entry/integrity and mechanically required lock metadata.

### Forbidden changes

- No `package.json`, source, test, config, snapshot, other dependency-version or production behavior changes.

## Interface contract

### Consumes

- PostCSS's existing semver range `^3.3.16`.

### Produces

- A reproducible lock resolving nanoid to a non-vulnerable compatible patch.

### Invariants

- `npm ci` succeeds and top-level package versions are byte-for-byte unchanged.
- Frontend tests/typecheck/lint/build remain green.

## Required behavior

- Use npm's lock-aware audit/update flow; do not hand-invent integrity data.
- Confirm the full audit, not only production audit, reports zero vulnerabilities.
- Confirm the diff contains no unrelated lock churn.

## Implementation guidance

Prefer `npm audit fix --package-lock-only` or the narrow equivalent from `web/`, inspect the lock diff, then run `npm ci` and all frontend gates.

## Acceptance criteria

- [ ] Full `npm audit --json` reports total zero.
- [ ] `npm audit --omit=dev --json` remains zero.
- [ ] `npm ci`, unit, typecheck, lint and build pass.
- [ ] Only the lockfile and packet change; top-level versions stay unchanged.

## Test and verification commands

```powershell
Set-Location web
npm ci
npm audit --json
npm audit --omit=dev --json
npm test
npm run typecheck
npm run lint
npm run build
Set-Location ..
git diff --check
```

Expected: both audits have zero findings and all frontend gates pass.

## Stop conditions

Stop if the fix requires a top-level version change, unrelated lock churn, source/config edits, or npm reports no compatible patch.

## Implementation handoff

Replace with the workflow handoff template, including before/after audit counts, exact lock change, gates and commit.
