---
id: "bge-m3-migration-preparation-01"
status: "done"
parallel-safe: false
depends-on: ["bge-m3-live-quality-01"]
base-commit: "a33b071"
owner: "Codex-inline"
---

# Task Packet

Add a resumable Qdrant candidate preparation command and immutable evidence.
No registry/env activation, process control, backups, user-data logging, Git
publication or Neo4j startup belongs to this packet.

## Handoff

- Added an app-authorized Qdrant preparation command composing bounded source
  inventory, resumable embedding, new-collection publication, exact target
  rescan and quality-evidence binding.
- Resume revalidates source, candidate, current target and the unchanged quality
  file before reusing evidence. Paths are confined and prior artifacts are not
  overwritten.
- Empty and non-empty sources share this path. Real production execution remains
  owned by the Windows maintenance controller.
