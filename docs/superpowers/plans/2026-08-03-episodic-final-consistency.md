# Episodic Final Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add compensating Episodic cleanup and user-scoped legacy timestamp normalization.

**Architecture:** Keep compensation inside EpisodicMemory using existing SQLite snapshots and vector exact-ID deletion. Normalize remote timestamps lazily before the first scoped time-range query and retain local post-filter fallback.

**Tech Stack:** Python 3.12, SQLite, VectorStore, qdrant-client 1.18.0, Qdrant 1.18.2, pytest

## Global Constraints

- Preserve all accepted uncommitted Qdrant work and unrelated concurrent work.
- Do not commit, push, add dependencies, or recreate collections.
- Execute task 1 before task 2; run real Qdrant and full regression after both.

### Task 1: Compensating exact-ID cleanup

**Files:** `hello_agents/memory/types/episodic.py`, `tests/memory/test_episodic_vector_cleanup.py`

- [ ] Add vector-failure and SQLite-mid-delete failure tests asserting rows are restored and local maps unchanged.
- [ ] Add `_delete_episode_ids` that snapshots SQLite rows, deletes SQLite then vectors, restores snapshots on failure, and surfaces rollback failure.
- [ ] Route forget/clear through the helper and run focused tests.

### Task 2: Normalize scoped legacy timestamps

**Files:** `hello_agents/memory/types/episodic.py`, `tests/memory/test_episodic_vector_store_protocol.py`, `tests/integration/test_qdrant_document_scope.py`

- [ ] Add failing protocol test for legacy timestamp normalization before a time-range search.
- [ ] Canonicalize new writes and implement one-time per-user remote normalization through filtered scroll with vectors.
- [ ] Extend the live test with a legacy timestamp point and prove range retrieval/count.
- [ ] Run focused, live, affected, and full regressions.

### Commit

Do not commit or push without separate explicit authorization.
