# Search, source notes and learning integration

Status: complete. Deliver the verified integration as one local commit, following the user's 2026-09-10 instruction. No push or deployment.

## Scope and evidence

- Preserve the accepted search and BGE-M3 runtime already present at HEAD daeb24f plus local changes.
- Learning compatibility target is the SQLite plans/today implementation in `.worktrees/bge-m3-runtime-identity`, packets 01–11/16–17, including the browser acceptance in packet 07. The older `codex/learning-mvp` JSON implementation is not the product integration target.
- Integrate existing product code and regression tests; do not redesign learning or execute a production migration/release. Individual packet acceptance does not establish overall production readiness.

## Milestones

- [x] Compare shared interfaces and import only the learning delta, preserving search/notes/embedding.
- [x] Verify combined deletion, authentication, persistence and managed-index contracts, plus frontend checks.
- [x] Record exact results and prepare an explicit source-only staging boundary; exclude operations recovery, runtime data, backups and credentials.
