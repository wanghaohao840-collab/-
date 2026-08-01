---
id: "<feature>-01"
title: "<short outcome>"
status: "draft"
parallel-safe: false
depends-on: []
base-commit: "<git commit>"
owner: "unassigned"
---

# Task Packet: <short outcome>

## Goal

State one independently useful, observable repository result. Describe the
condition that will be true when this packet is delivered.

## Non-goals

- Behavior this packet intentionally does not implement.
- Adjacent refactors, migrations, cleanup, or integrations that remain outside
  this delivery unit.

## Delivery context

Explain why this result is needed, its role in the accepted architecture, and
enough domain context for a worker who has not read the planning conversation.

## Relevant files and current interfaces

- `path:line` — symbol/signature/data shape and relevant current behavior.
- `path:line` — test seam or caller that constrains the change.
- Existing changes to preserve: `path` or `none`.

Do not list a file without explaining why it matters.

## Prerequisites

### Packet dependencies

- `<packet-id>` must be `done`, or `none`.

### Repository/base state

- Base commit: `<git commit>`.
- Required existing symbols, configuration, fixtures, or migrations.

### External prerequisites

- Services, environment variables, installed dependencies, or `none`.

## Explicit change boundary

### Allowed files

- Create: `path`
- Modify: `path`
- Test: `path`

### Allowed behavior changes

- Exact behavior and interfaces this packet may change.

### Forbidden changes

- Files and directories that must not be edited.
- Public interfaces and persisted formats that must remain compatible.
- Data, generated artifacts, unrelated cleanup, and redesigns that are banned.
- Architectural and data-isolation boundaries that must not be crossed.

If implementation requires anything outside the allowed boundary, stop instead
of broadening the packet.

## Interface contract

### Consumes

- Exact existing symbol, signature, data shape, or fixture.

### Produces

- Exact new or changed symbol, signature, return shape, error behavior, or
  persisted representation.

### Invariants

- Compatibility, failure, concurrency, persistence, and isolation rules that
  remain true before and after delivery.

## Required behavior

- Precise functional rule, including relevant edge cases.
- Observable failure behavior.
- Interaction with existing callers and data.

## Implementation guidance

Ordered, concrete guidance without requiring hidden chat context. Include edge
cases and likely traps, but leave local coding choices to the implementer.

## Acceptance criteria

- [ ] Deliverable criterion with observable evidence.
- [ ] Interface/behavior criterion with observable evidence.
- [ ] Failure or edge-case criterion with observable evidence.
- [ ] Regression and invariant criterion with observable evidence.

Each criterion must be decidable without relying on the implementer's claim.

## Test and verification commands

Run from repository root:

```powershell
<focused test command>
```

Expected: exact pass condition.

```powershell
<broader regression command when needed>
```

Expected: exact pass condition.

If a criterion requires manual verification, give exact actions and expected
output. “Test manually” is not sufficient.

## Stop conditions

Stop and report `blocked` if:

- a verified repository fact above is no longer true;
- a referenced interface, caller, fixture, or test differs from this packet;
- requested behavior already exists or conflicts with the current code;
- implementation requires a file outside **Owned files**;
- a dependency packet is not `done`;
- acceptance criteria conflict with current behavior or another packet.
- another packet or pre-existing worktree change overlaps this responsibility;
- the verification commands are invalid or cannot prove acceptance.

Do not improvise around these conflicts. Append the **Reality-conflict report**
from `docs/agent-workflow/README.md` and wait for packet revision.

## Implementation handoff

Claude Code must replace this placeholder with:

```markdown
## Implementation handoff

- Packet: `<packet-id>`
- Status: `done | blocked`
- Delivered:
  - concise description of the independently useful result
- Files changed:
  - `path` — purpose of change
- Interfaces added or changed:
  - exact symbol/signature/data shape, or `none`
- Acceptance evidence:
  - [x] criterion — evidence
- Verification:
  - `exact command` — PASS/FAIL (counts or concise output)
- Scope confirmation:
  - changed only allowed files: yes/no
  - forbidden areas untouched: yes/no
- Deviations:
  - `none` or precise approved deviation
- Residual risks/follow-ups:
  - `none` or precise item not included in this packet
- Commit:
  - `<hash>` or `not committed`
```
