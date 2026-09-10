# BGE-M3 Atomic JSON Candidate Publication Plan

**Goal:** Reuse the accepted candidate checkpoint to publish a deterministic managed JSON index atomically and recover across the file/state commit boundary.

**Architecture:** Generalize candidate embedding to either backend while keeping backend identity exact. Persist a UTC creation timestamp in the pre-release checkpoint. Stream deterministic JSON to a sibling temporary file, fsync, then replace only a missing target; if a prior crash left the exact target, compare bounded SHA-256 and finish state. Validate the finished file through JsonChunkSource.

**Constraints:** no whole-vector list, no overwrite of unknown targets, no link/reparse targets, no source/runtime/registry changes, no live data/API, no controller/cutover. Existing E3-A4 checkpoint format is pre-release and must be regenerated after the added created_at column.

**Files:** candidate_rebuild.py and test_candidate_rebuild.py; this plan/workflow/spec/output records only.

**Acceptance:** JSON and Qdrant builds remain identity-specific; deterministic timestamp/file bytes; interruption recovery; published target revalidation/repair; unknown or unsafe target refusal; exact count and managed identity validation; full focused and Memory/RAG regression.

**Verification:** run candidate/source/authority focused tests, all tests/memory, compileall, pip check and git diff --check. No execution-choice prompt: user authorized uninterrupted serial inline work.
