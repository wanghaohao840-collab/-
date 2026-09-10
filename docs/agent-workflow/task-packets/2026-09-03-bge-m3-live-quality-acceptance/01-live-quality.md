---
id: "bge-m3-live-quality-01"
status: "done"
parallel-safe: false
depends-on: ["bge-m3-retrieval-quality-01"]
base-commit: "a33b071"
owner: "Codex-inline"
---

# Task Packet

Implement a meaningful competing-chunk corpus, isolated old/new comparison and
tamper-evident evidence report. Run the configured candidate only after offline
tests pass. Do not activate production, read user content, print keys or mutate
the registry.

## Handoff

- The bilingual corpus now has ten competing chunks in each language-specific
  document instead of a trivial one-chunk search scope.
- Offline acceptance tests passed. The configured real BGE-M3 endpoint then
  achieved Recall@5 1.00, MRR@5 1.00 and zero leaks; the SimpleEmbedding
  baseline measured 0.50 and 0.228 respectively.
- The tamper-evident report is local deployment evidence and contains no key or
  user content. Production remained unchanged during this packet.
