---
id: "bge-m3-retrieval-quality-01"
status: "done"
parallel-safe: false
depends-on: ["bge-m3-candidate-validation-01"]
base-commit: "a33b071"
owner: "Codex-inline"
---

# Task Packet

Implement fixed bilingual dataset and generic scoped quality gate only. Owned evals/test/records/spec files. No live API/index, user data, runtime/cutover/dependency or Git publication. Handoff must distinguish deterministic evaluator acceptance from pending real BGE/old-model comparison.

## Handoff

- Added a fixed public/synthetic dataset containing 10 Chinese and 10 English
  document-question pairs.
- Added deterministic Recall@5, MRR@5, isolation-leak and baseline-regression
  gates. Malformed datasets, invalid metrics, duplicate hits and callbacks that
  exceed the requested top-five contract fail closed.
- Strengthened candidate checkpoint reinspection so a corrupted vector with the
  wrong dimension or an all-zero vector cannot pass recovery validation.
- Verification: 13 focused tests passed; 517 `tests/evals` plus `tests/memory`
  tests passed; compile, dependency and diff checks passed.
- This packet does not claim real BGE-M3 quality. A live acceptance controller
  must still build isolated old/new indexes and persist the resulting comparison.
