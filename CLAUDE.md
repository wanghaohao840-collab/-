# Claude Code repository instructions

Before changing this repository, read:

1. `PROJECT_KNOWLEDGE.md`
2. `docs/agent-workflow/README.md`
3. The single task packet assigned by the user

Treat current code, tests, configuration, and runtime behavior as
authoritative when they disagree with historical notes.

Claude Code is the implementation worker in this workflow. Implement only one
task packet at a time. Do not silently expand its scope, edit files owned by
another parallel packet, redesign accepted architecture, or mark acceptance
criteria as passed without running the listed verification.

If a packet is incomplete, contradictory, stale, or requires edits outside its
declared ownership, stop and report the blocker instead of guessing. At
handoff, use the report format defined in `docs/agent-workflow/README.md`.
