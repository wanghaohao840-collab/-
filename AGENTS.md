# Repository working context

Before changing this repository, read `PROJECT_KNOWLEDGE.md`. It is the maintained project context distilled from the project's historical ChatGPT/RAG discussion.

When historical notes and the current repository disagree, treat the current code, tests, configuration, and runtime behavior as authoritative. Preserve the architectural boundaries and data-isolation rules documented there unless the user explicitly requests a redesign.

## Multi-agent planning workflow

When reviewing a Superpowers implementation plan, follow
`docs/agent-workflow/README.md`.

Codex owns plan review and task-packet preparation. Do not implement the
reviewed feature unless the user separately asks Codex to implement it.
Write task packets under `docs/agent-workflow/task-packets/<plan-name>/` and
make every packet self-contained, independently verifiable, and free of file
ownership overlap with packets that may run in parallel.
