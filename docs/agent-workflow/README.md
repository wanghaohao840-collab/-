# Superpowers → Codex → Claude Code Workflow

This directory defines a file-based handoff protocol for three roles:

1. **Superpowers `writing-plans`** turns a requirement into an implementation
   plan under `docs/superpowers/plans/`.
2. **Codex** reviews that plan against the current repository and produces
   context-complete task packets.
3. **Claude Code** implements one ready task packet at a time and returns a
   verification-backed handoff report.

The plan is guidance. Current code, tests, configuration, and runtime behavior
remain authoritative.

## Directory contract

```text
docs/
├── superpowers/
│   └── plans/
│       └── YYYY-MM-DD-feature.md
└── agent-workflow/
    ├── README.md
    ├── PLAN_REVIEW_TEMPLATE.md
    ├── TASK_PACKET_TEMPLATE.md
    ├── FINAL_INTEGRATION_REVIEW_TEMPLATE.md
    └── task-packets/
        └── YYYY-MM-DD-feature/
            ├── REVIEW.md
            ├── 01-short-name.md
            ├── 02-short-name.md
            └── FINAL_INTEGRATION_REVIEW.md
```

## Phase 1: write the implementation plan

The plan author must describe the goal, architecture, constraints, affected
files, public interfaces, ordered implementation steps, tests, and explicit
non-goals. Save the result in `docs/superpowers/plans/`.

The plan must not claim that repository facts were verified unless they were
actually inspected. Uncertain facts should be labeled as assumptions.

## Phase 2: Codex review and packetization

Codex first reads `PROJECT_KNOWLEDGE.md`, then inspects the current code, tests,
configuration, and worktree. It creates `REVIEW.md` from
`PLAN_REVIEW_TEMPLATE.md`.

A plan passes review only when:

- its referenced paths and interfaces match the repository;
- architectural and data-isolation rules are preserved;
- acceptance criteria are observable;
- verification commands are runnable;
- migration, compatibility, and failure behavior are addressed where relevant;
- unrelated or already-implemented work is removed;
- each implementation unit can be given complete local context.

Codex then creates numbered packets from `TASK_PACKET_TEMPLATE.md`.

### Ready gate: a packet is a delivery unit

A task packet is not a shortened list of implementation steps. It is an
independently deliverable context bundle. Codex must not set `status: ready`
unless the packet contains all of the following:

- a measurable goal and explicit non-goals;
- verified relevant files, current interfaces, and repository facts;
- prerequisite packets, external prerequisites, and the required base state;
- an exhaustive owned-file and permitted-change boundary;
- observable acceptance criteria and exact test commands;
- files, behavior, data, and architectural boundaries that must not be touched;
- a complete handoff-summary format for the implementer.

The worker must be able to implement the packet without reading the original
planning conversation. Referring only to another plan section, an issue, or
“the existing design” is insufficient context.

### Packet independence rules

Packets marked `parallel-safe: true` must:

- have disjoint owned-file sets;
- not rely on uncommitted output from another packet;
- include their own necessary context and exact verification;
- avoid shared generated files, dependency manifests, registries, and central
  exports unless those are assigned to a dedicated integration packet.

If two tasks must edit the same file or one consumes another's new interface,
they are not independent. Merge them into one packet or declare a dependency
and set `parallel-safe: false`.

Every packet must state a base commit. If the worktree is dirty, it must also
identify relevant pre-existing changes that the worker must preserve.

Before marking a packet `ready`, Codex should be able to answer “yes” to:

- Can this packet be completed, tested, and handed off as a useful repository
  increment?
- Are all inputs and interfaces it consumes already present, or declared as
  completed prerequisites?
- Can a worker identify every allowed edit without guessing?
- Can acceptance be decided from the listed commands and observable evidence?
- Can the packet be reverted or reviewed without disentangling unrelated work?

## Phase 3: Claude Code implementation

The user assigns exactly one packet, for example:

```text
请实现 docs/agent-workflow/task-packets/2026-06-28-example/01-storage.md
```

Claude Code:

1. reads the repository context and assigned packet;
2. verifies the packet's repository facts, interfaces, prerequisites, and
   assumptions before editing;
3. changes only owned files, except after reporting and receiving approval for
   a scope amendment;
4. runs the packet's verification commands;
5. updates the packet status and appends a handoff report.

Completion means the packet's deliverable exists and is verified. Merely
finishing the listed coding steps is not completion.

### Reality-conflict pause protocol

Claude Code must not mechanically follow a stale or incorrect packet. It must
pause before further edits, set the packet to `blocked`, and report when:

- a referenced file, symbol, signature, caller, or test no longer matches;
- the requested behavior already exists or conflicts with current behavior;
- a prerequisite is absent, incomplete, or incompatible;
- the packet cannot meet acceptance criteria within its allowed files;
- another packet or existing worktree change overlaps the same responsibility;
- implementation would violate architecture, compatibility, persistence, or
  data-isolation constraints;
- the listed verification is invalid or cannot demonstrate acceptance.

The blocker report must contain:

```markdown
## Reality-conflict report

- Packet: `<packet-id>`
- Status: blocked
- Expected by packet:
  - precise expected fact
- Observed in repository:
  - precise actual fact with `path:line`, command output, or test evidence
- Impact:
  - why continuing would be unsafe, incorrect, or outside scope
- Work completed before pause:
  - files changed and verification run, or `none`
- Recommended resolution:
  - revise packet | add dependency | merge packets | change acceptance | cancel
- Decision required:
  - one concrete question for Codex/user
```

Codex reviews the evidence and revises the review, dependency graph, affected
packets, and acceptance criteria before the worker resumes. A worker must not
quietly reinterpret the plan.

### Allowed packet statuses

- `draft`: incomplete; must not be implemented.
- `ready`: reviewed and assignable.
- `in_progress`: claimed by one worker.
- `blocked`: cannot proceed without a decision or prerequisite.
- `done`: implementation and listed verification completed.

Only one worker may own an `in_progress` packet.

## Claude Code handoff report

Append this section to the assigned packet:

```markdown
## Implementation handoff

- Status: done | blocked
- Files changed:
  - `path`
- Acceptance criteria:
  - [x] Criterion and evidence
- Verification:
  - `exact command` — PASS/FAIL (summary)
- Deviations:
  - None, or a precise explanation
- Residual risks:
  - None, or a precise explanation
- Commit:
  - `<hash>` or `not committed`
```

## Mandatory Codex final integration review

After all implementation packets are `done`, the feature is still not complete.
Codex must review the combined repository state and create
`FINAL_INTEGRATION_REVIEW.md` in the plan's task-packet directory.

The review must inspect the actual combined diff and check:

- cross-packet interface producers and consumers agree on signatures, data
  shapes, error behavior, defaults, and lifecycle;
- dependencies were integrated in the intended order;
- no requirement or acceptance criterion was lost between packets;
- no behavior was implemented twice in competing layers or helpers;
- overlapping edits did not overwrite, duplicate, or bypass another packet;
- central exports, configuration, dependency manifests, migrations, and
  documentation were updated exactly once where required;
- architecture, backward compatibility, persistence, and data isolation remain
  intact;
- focused packet tests and the appropriate combined regression suite pass;
- residual deviations and risks are explicit.

Use this result:

- `accepted`: combined delivery is complete and verified.
- `changes-required`: create one or more corrective task packets; do not patch
  implementation opportunistically during review.
- `blocked`: integration cannot be judged without an external decision or
  unavailable prerequisite.

Commit, push, or pull request creation happens only when the user explicitly
requests it.
