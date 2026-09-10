---
id: "bge-m3-journaled-cutover-01"
status: "done"
parallel-safe: false
depends-on: ["bge-m3-migration-preparation-01"]
base-commit: "a33b071"
owner: "Codex-inline"
---

# Task Packet

Implement conditional registry replacement, durable activation journal,
pre-completion rollback and startup refusal during incomplete cutover. Do not
edit the real environment, start/stop services or claim production activation.

## Handoff

- Added conditional peer-preserving registry replacement, a self-digesting
  cutover journal, idempotent apply and pre-completion rollback.
- Both simple and managed RAG startup reject any incomplete cutover journal.
- Added the Windows controller that holds the shared operations lock, builds the
  app image, takes a cold backup, stops writers, prepares the candidate, applies
  the registry/config transition, runs health/deep smoke and never starts Neo4j.
- Scheduled operations now include `-WindowStyle Hidden`, preventing recurring
  PowerShell console windows while retaining status/log notifications.
- Release regression: 738 tests passed and one environment-conditioned test was
  skipped; compilation, dependency and diff checks passed.
