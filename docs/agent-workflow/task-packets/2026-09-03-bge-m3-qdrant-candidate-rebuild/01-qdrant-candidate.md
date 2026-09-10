---
id: "bge-m3-qdrant-candidate-01"
title: "Recoverable Qdrant candidate rebuild"
status: "done"
parallel-safe: false
depends-on: ["bge-m3-app-source-authority-01"]
base-commit: "a33b071"
owner: "Codex-inline"
---

# Task Packet

Implement exactly the reviewed plan. Allowed: candidate_rebuild.py/test_candidate_rebuild.py; source_inventory.py/test_source_inventory.py; this workflow/spec/output only. Forbidden: real data/services/config, deletion, runtime registry/cutover/JSON publication, dependencies, stable checkout or Git publication. Stop on interface conflict. Handoff records exact tests, recovery evidence, scope and residual gates.

## Implementation handoff

- Status: done; uncommitted and unpushed.
- Delivered: authority-required ordered inventory chunks, transactional candidate vector checkpoint, resume without re-embedding completed batches, bounded idempotent Qdrant publication to the identity-derived new collection.
- Verification: focused 104 passed in 18.57s; Memory/RAG 508 passed in 21.03s; no failures/skips. Compile, pip check and diff check pass.
- Evidence: source/identity/migration mismatches reject; incomplete build resumes; target nonempty and wrong identity reject; metadata/isolation fields preserved in Qdrant layout; exact count checked after each publish batch.
- Scope: synthetic files and InMemoryVectorStore only. No real data/API/container/registry/config/deletion/activation; JSON publication, maintenance controller, quality and cutover remain pending.
