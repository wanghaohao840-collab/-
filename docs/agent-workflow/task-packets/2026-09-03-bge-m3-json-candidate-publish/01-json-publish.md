---
id: "bge-m3-json-candidate-01"
status: "done"
parallel-safe: false
depends-on: ["bge-m3-qdrant-candidate-01"]
base-commit: "a33b071"
owner: "Codex-inline"
---

# Task Packet

Deliver the reviewed atomic JSON publication plan. Owned files are candidate_rebuild.py, test_candidate_rebuild.py and same-name records/spec. No real data, secrets, network, runtime/registry, dependency, stable checkout or Git publication. Handoff requires recovery/immutability evidence and exact regression counts.

## Implementation handoff

Done, uncommitted. Candidate checkpoints now support exact JSON/Qdrant identities and persist deterministic UTC creation time. JSON publication streams rows into a sibling temporary file, fsyncs and atomically replaces only a missing target; exact preexisting bytes recover the replace/state window, unknown or unsafe targets reject. Completed targets are revalidated through JsonChunkSource. Focused 107 passed in 25.50s; Memory/RAG 511 passed in 30.35s; compile/pip/diff pass. Synthetic data only; no production effects.
